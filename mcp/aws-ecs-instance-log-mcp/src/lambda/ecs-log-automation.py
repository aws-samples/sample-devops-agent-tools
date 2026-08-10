"""
ECS Instance Log Collection MCP Server - Enhanced Lambda Handler
Provides comprehensive ECS container instance log collection and analysis via SSM.
Similar to EKS Node Log MCP but tailored for ECS EC2 instances.
"""

import json
import boto3
import os
import re
import time
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from botocore.exceptions import ClientError

# AWS Clients - default region (where Lambda runs)
# S3 client uses SigV4 explicitly — required for presigned URLs on KMS-encrypted buckets
from botocore.config import Config as BotoConfig
ssm_client = boto3.client('ssm')
s3_client = boto3.client('s3', config=BotoConfig(signature_version='s3v4'))
ec2_client = boto3.client('ec2')
ecs_client = boto3.client('ecs')

# Regional client cache
_regional_clients: Dict[str, Any] = {}

# Environment variables
LOGS_BUCKET = os.environ.get('LOGS_BUCKET_NAME', '')
SSM_AUTOMATION_ROLE_ARN = os.environ.get('SSM_AUTOMATION_ROLE_ARN', '')
KMS_KEY_ARN = os.environ.get('KMS_KEY_ARN', '')
DEFAULT_REGION = os.environ.get('AWS_REGION', 'us-east-1')

# Constants
MAX_CONCURRENT_READS = 10
DEFAULT_MAX_BYTES = 100000
DEFAULT_CHUNK_SIZE = 1048576  # 1MB
MAX_CHUNK_SIZE = 5242880  # 5MB
DEFAULT_LINE_COUNT = 1000
MAX_LINE_COUNT = 10000
FINDINGS_INDEX_FILE = 'findings_index.json'


# =============================================================================
# PRESIGNED URL EXPIRATION — configurable via env var (T5 mitigation)
# =============================================================================

def _parse_presigned_url_expiration() -> int:
    """Parse PRESIGNED_URL_EXPIRATION_SECONDS env var, default to 900."""
    raw = os.environ.get('PRESIGNED_URL_EXPIRATION_SECONDS', '')
    try:
        val = int(raw)
        if val > 0:
            return val
    except (ValueError, TypeError):
        pass
    return 900

PRESIGNED_URL_EXPIRATION = _parse_presigned_url_expiration()


# =============================================================================
# ALLOWED REGIONS — configurable via env var (T9, T11 mitigation)
# =============================================================================

ALLOWED_REGIONS = set(
    r.strip() for r in os.environ.get('ALLOWED_REGIONS', '').split(',')
    if r.strip()
) or {os.environ.get('AWS_REGION', DEFAULT_REGION)}


def validate_region(region: str) -> Optional[Dict]:
    """
    Validate that a region is in the allowed set.
    Returns None if valid, or an error response dict if invalid.
    """
    if region not in ALLOWED_REGIONS:
        return error_response(
            403,
            f"Region '{region}' is not permitted. Allowed regions: {', '.join(sorted(ALLOWED_REGIONS))}"
        )
    return None


def resolve_and_validate_region(arguments: Dict, instance_id: str = None) -> tuple:
    """
    Resolve and validate region. Returns (region, error_response).
    If error_response is not None, caller should return it immediately.
    """
    region = resolve_region(arguments, instance_id)
    error = validate_region(region)
    return region, error


# =============================================================================
# ECS INSTANCE VALIDATION — verify target is an ECS container instance (T4, T13 mitigation)
# =============================================================================

def validate_ecs_instance(instance_id: str, region: str) -> Optional[Dict]:
    """
    Validate that an instance belongs to an ECS cluster by checking for
    the ECS agent tag (aws:ecs:clusterName) or by querying ECS
    ListContainerInstances across clusters.
    Returns None if valid, or an error response dict if invalid.
    """
    try:
        regional_ec2 = get_regional_client('ec2', region)
        resp = regional_ec2.describe_instances(InstanceIds=[instance_id])
        for reservation in resp.get('Reservations', []):
            for instance in reservation.get('Instances', []):
                tags = instance.get('Tags', [])
                for tag in tags:
                    # ECS-managed instances have aws:ecs:clusterName tag
                    if tag['Key'] == 'aws:ecs:clusterName':
                        return None  # Valid ECS instance
                    # Also accept ecs:cluster tag (set by some ECS AMIs)
                    if tag['Key'].startswith('ecs:cluster'):
                        return None
                    # Accept instances with ECS-related names
                    if tag['Key'] == 'Name' and 'ecs' in tag.get('Value', '').lower():
                        return None

        # Fallback: try to find the instance via ECS API
        try:
            regional_ecs = get_regional_client('ecs', region)
            clusters_resp = regional_ecs.list_clusters()
            for cluster_arn in clusters_resp.get('clusterArns', [])[:10]:
                ci_resp = regional_ecs.list_container_instances(
                    cluster=cluster_arn,
                    filter=f'ec2InstanceId == {instance_id}',
                )
                if ci_resp.get('containerInstanceArns'):
                    return None  # Found in an ECS cluster
        except Exception as e:
            print(f"Warning: ECS API fallback check failed: {str(e)}")
            # Non-fatal: if ECS API fails, fall through to tag-based rejection

        return error_response(
            403,
            f"Instance {instance_id} does not appear to be part of an ECS cluster "
            f"(no aws:ecs:clusterName tag found and not found via ECS API). "
            f"This tool is designed for ECS container instances only."
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidInstanceID.NotFound':
            return error_response(404, f"Instance {instance_id} not found in region {region}")
        return error_response(500, f"Failed to validate instance {instance_id}: {str(e)}")


# =============================================================================
# TIME WINDOW RESOLVER — enforces time-bounded log analysis
# =============================================================================

class TimeWindowResolver:
    """
    Resolves an analysis time window from user-provided incident time parameters.

    Rules:
      1. If start_time AND end_time provided: use exactly.
      2. If a single incident_time provided: window = [incident_time - 5min, incident_time + 5min].
      3. If nothing provided: window = [now_utc - 10min, now_utc].

    All outputs are UTC datetime objects.
    """

    DEFAULT_WINDOW_MINUTES = 10
    INCIDENT_PADDING_MINUTES = 5
    MAX_WINDOW_HOURS = 24  # safety cap

    @staticmethod
    def resolve(arguments: Dict) -> Dict:
        now_utc = datetime.utcnow()
        incident_time_str = arguments.get('incident_time')
        start_time_str = arguments.get('start_time')
        end_time_str = arguments.get('end_time')

        window_start = None
        window_end = None
        reason = ''

        if start_time_str and end_time_str:
            window_start = TimeWindowResolver._parse_timestamp(start_time_str)
            window_end = TimeWindowResolver._parse_timestamp(end_time_str)
            if window_start and window_end:
                reason = 'explicit incident window provided'
            else:
                reason = 'failed to parse explicit window; default last 10 minutes'
                window_start = None
                window_end = None

        if window_start is None and incident_time_str:
            incident_dt = TimeWindowResolver._parse_timestamp(incident_time_str)
            if incident_dt:
                pad = timedelta(minutes=TimeWindowResolver.INCIDENT_PADDING_MINUTES)
                window_start = incident_dt - pad
                window_end = incident_dt + pad
                reason = f'incident time provided; applied +/- {TimeWindowResolver.INCIDENT_PADDING_MINUTES} minute padding'
            else:
                reason = 'failed to parse incident_time; default last 10 minutes'

        if window_start is None:
            window_end = now_utc
            window_start = now_utc - timedelta(minutes=TimeWindowResolver.DEFAULT_WINDOW_MINUTES)
            if not reason:
                reason = f'no incident time; default last {TimeWindowResolver.DEFAULT_WINDOW_MINUTES} minutes'

        # Safety cap
        max_delta = timedelta(hours=TimeWindowResolver.MAX_WINDOW_HOURS)
        if (window_end - window_start) > max_delta:
            window_start = window_end - max_delta
            reason += f' (clamped to max {TimeWindowResolver.MAX_WINDOW_HOURS}h window)'

        if window_end < window_start:
            window_start, window_end = window_end, window_start
            reason += ' (swapped start/end)'

        jctl_fmt = '%Y-%m-%d %H:%M:%S'
        return {
            'window_start_utc': window_start,
            'window_end_utc': window_end,
            'window_start_iso': window_start.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'window_end_iso': window_end.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'resolution_reason': reason,
            'journalctl_since': window_start.strftime(jctl_fmt),
            'journalctl_until': window_end.strftime(jctl_fmt),
        }

    @staticmethod
    def _parse_timestamp(ts_str: str) -> Optional[datetime]:
        if not ts_str or not isinstance(ts_str, str):
            return None
        ts_str = ts_str.strip()
        for fmt in [
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%fZ',
            '%Y-%m-%dT%H:%M:%S.%f',
            '%Y-%m-%dT%H:%M:%S%z',
            '%Y-%m-%d %H:%M:%S UTC',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
        ]:
            try:
                dt = datetime.strptime(ts_str, fmt)
                if dt.tzinfo:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt
            except ValueError:
                continue
        try:
            ts_float = float(ts_str)
            if 1_000_000_000 < ts_float < 2_000_000_000:
                return datetime.utcfromtimestamp(ts_float)
            if 1_000_000_000_000 < ts_float < 2_000_000_000_000:
                return datetime.utcfromtimestamp(ts_float / 1000)
        except (ValueError, OSError):
            pass
        return None

    @staticmethod
    def is_within_window(timestamp_str: str, window: Dict) -> bool:
        dt = TimeWindowResolver._parse_timestamp(timestamp_str)
        if dt is None:
            return True  # Conservative: include if unparseable
        return window['window_start_utc'] <= dt <= window['window_end_utc']

    @staticmethod
    def filter_findings_by_window(findings: List[Dict], window: Dict) -> Dict:
        included = []
        excluded_count = 0
        unparseable_count = 0
        for f in findings:
            sample = f.get('sample', '') or f.get('line', '')
            ts_str = extract_timestamp(sample) if sample else None
            if ts_str is None:
                unparseable_count += 1
                included.append(f)
                continue
            if TimeWindowResolver.is_within_window(ts_str, window):
                included.append(f)
            else:
                excluded_count += 1
        return {
            'findings': included,
            'excluded_outside_window': excluded_count,
            'unparseable_timestamps': unparseable_count,
            'total_before_filter': len(findings),
        }

    @staticmethod
    def window_metadata(window: Dict) -> Dict:
        return {
            'window_start_utc': window['window_start_iso'],
            'window_end_utc': window['window_end_iso'],
            'resolution_reason': window['resolution_reason'],
        }

# ============================================================================
# ECS ERROR PATTERNS
# ============================================================================

ECS_ERROR_PATTERNS = {
    'critical': [
        (r'(?i)agent.*connected.*false', 'ECS Agent disconnected'),
        (r'(?i)agent.*not.*connected', 'ECS Agent not connected'),
        (r'(?i)failed.*register.*container.*instance', 'Container instance registration failed'),
        (r'(?i)unable.*connect.*ecs', 'Unable to connect to ECS service'),
        (r'(?i)No container instances were found', 'No container instances available'),
        (r'(?i)AGENT_DISCONNECTED', 'ECS Agent disconnected from cluster'),
        (r'(?i)ECS Agent failed to start', 'ECS Agent startup failure'),
        (r'(?i)websocket.*unable to dial', 'Agent websocket connection failed'),
        (r'(?i)Error getting ECS instance credentials', 'Agent credential retrieval failed'),
        (r'(?i)client version.*is too old', 'Docker API version mismatch'),
        (r'(?i)CannotPullContainerError', 'Cannot pull container image'),
        (r'(?i)CannotPullECRContainerError', 'Cannot pull ECR container image'),
        (r'(?i)ResourceInitializationError', 'Resource initialization failed'),
        (r'(?i)TaskFailedToStart', 'Task failed to start'),
        (r'(?i)CannotStartContainerError', 'Cannot start container'),
        (r'(?i)CannotCreateContainerError', 'Cannot create container'),
        (r'(?i)ContainerRuntimeError', 'Container runtime error'),
        (r'(?i)ContainerRuntimeTimeoutError', 'Container runtime timeout'),
        (r'(?i)CannotCreateVolumeError', 'Cannot create volume mount'),
        (r'(?i)CannotInspectContainerError', 'Cannot inspect container'),
        (r'(?i)CannotStopContainerError', 'Cannot stop container'),
        (r'(?i)SpotInterruptionError', 'Spot capacity interruption'),
        (r'(?i)InternalError', 'AWS internal error'),
        (r'(?i)pull.*access.*denied', 'Image pull access denied'),
        (r'(?i)repository.*does.*not.*exist', 'Repository does not exist'),
        (r'(?i)manifest.*not.*found', 'Image manifest not found'),
        (r'(?i)image.*not.*found', 'Image not found'),
        (r'(?i)unauthorized.*authentication.*required', 'Authentication required for image pull'),
        (r'(?i)no.*basic.*auth.*credentials', 'Missing authentication credentials'),
        (r'(?i)denied.*requested.*access.*resource', 'Access denied to resource'),
        (r'(?i)toomanyrequests.*Too Many Requests', 'Docker Hub rate limit exceeded'),
        (r'(?i)failed to resolve ref.*not found', 'Image reference not found'),
        (r'(?i)net/http.*request canceled while waiting', 'Image pull network timeout'),
        (r'(?i)API error \(500\).*Get https://.*ecr', 'ECR API error 500'),
        (r'(?i)ecr:BatchGetImage.*not authorized', 'Cross-account ECR access denied'),
        (r'(?i)inspect image has been retried', 'Image inspection retry failure'),
        (r'(?i)unable to pull secrets or registry auth', 'Secrets/registry auth pull failed'),
        (r'(?i)unable to retrieve secret from asm', 'Secrets Manager retrieval failed'),
        (r'(?i)unable to retrieve ecr registry auth', 'ECR registry auth retrieval failed'),
        (r'(?i)failed to validate logger args', 'Logger args validation failed'),
        (r'(?i)execution resource retrieval failed', 'Execution resource retrieval failed'),
        (r'(?i)unable to get registry auth from asm', 'Private registry auth from ASM failed'),
        (r'(?i)failed to initialize logging driver', 'Logging driver initialization failed'),
        (r'(?i)service call has been retried.*times', 'Service call retry exhausted'),
        (r'(?i)insufficient.*cpu.*units', 'Insufficient CPU units'),
        (r'(?i)insufficient.*memory', 'Insufficient memory'),
        (r'(?i)insufficient.*GPU.*units', 'Insufficient GPU units'),
        (r'(?i)OutOfMemoryError', 'Out of memory error'),
        (r'(?i)oom.*kill', 'OOM kill detected'),
        (r'(?i)Memory cgroup out of memory', 'Memory cgroup OOM'),
        (r'(?i)invoked oom-killer', 'OOM killer invoked'),
        (r'(?i)Killed process.*total-vm', 'Process killed by OOM'),
        (r'(?i)exit code 137', 'Container killed (exit 137 - OOM)'),
        (r'(?i)exit code 139', 'Container segfault (exit 139 - SIGSEGV)'),
        (r'(?i)exit code 255', 'Container ENTRYPOINT/CMD failed (exit 255)'),
        (r'(?i)No valid providers in chain', 'IAM credential chain error'),
        (r'(?i)unable.*assume.*role', 'Unable to assume IAM role'),
        (r'(?i)AccessDeniedException', 'Access denied exception'),
        (r'(?i)UnauthorizedOperation', 'Unauthorized operation'),
        (r'(?i)is not authorized to perform', 'IAM permission denied'),
        (r'(?i)execution role.*does not have', 'Task execution role missing permissions'),
        (r'(?i)task role.*does not have', 'Task role missing permissions'),
        (r'(?i)AssumeRoleUnauthorizedAccess', 'Cannot assume IAM role'),
        (r'(?i)ecr:GetAuthorizationToken.*denied', 'ECR GetAuthorizationToken denied'),
        (r'(?i)failed.*retrieve.*secrets', 'Failed to retrieve secrets'),
        (r'(?i)SecretNotFound', 'Secret not found'),
        (r'(?i)ParameterNotFound', 'SSM parameter not found'),
        (r'(?i)AccessDenied.*secretsmanager', 'Secrets Manager access denied'),
        (r'(?i)AccessDenied.*ssm', 'SSM access denied'),
        (r'(?i)ResourceNotFoundException.*secret', 'Secret resource not found'),
        (r'(?i)InvalidRequestException.*secret', 'Invalid secret request'),
        (r'(?i)secretsmanager:GetSecretValue.*denied', 'GetSecretValue permission denied'),
        (r'(?i)ssm:GetParameters.*denied', 'SSM GetParameters permission denied'),
        (r'(?i)docker.*daemon.*not.*running', 'Docker daemon not running'),
        (r'(?i)cannot.*connect.*docker', 'Cannot connect to Docker'),
        (r'(?i)OCI runtime create failed', 'OCI runtime create failed'),
        (r'(?i)containerd.*not.*running', 'containerd not running'),
        (r'(?i)no.*space.*left.*device', 'No space left on device'),
        (r'(?i)disk.*full', 'Disk full'),
        (r'(?i)exec format error', 'Wrong image architecture'),
        (r'(?i)container_linux.go.*starting container process', 'Container process start failed'),
        (r'(?i)no such file or directory.*entrypoint', 'Entrypoint not found'),
        (r'(?i)permission denied.*entrypoint', 'Entrypoint permission denied'),
        (r'(?i)network.*unreachable', 'Network unreachable'),
        (r'(?i)ENI.*allocation.*failed', 'ENI allocation failed'),
        (r'(?i)failed.*create.*network.*interface', 'Failed to create network interface'),
        (r'(?i)InsufficientFreeAddressesInSubnet', 'Insufficient IP addresses in subnet'),
        (r'(?i)no.*available.*IP.*addresses', 'No available IP addresses'),
        (r'(?i)Timeout waiting for network interface', 'ENI provisioning timeout'),
        (r'(?i)deployment circuit breaker.*triggered', 'Deployment circuit breaker triggered'),
        (r'(?i)ECS Deployment Circuit Breaker was triggered', 'Circuit breaker deployment failed'),
        (r'(?i)kernel.*panic', 'Kernel panic'),
        (r'(?i)BUG:.*', 'Kernel bug detected'),
        (r'(?i)segfault', 'Segmentation fault'),
        (r'(?i)watchdog.*soft.*lockup', 'Soft lockup detected'),
    ],
    'warning': [
        (r'(?i)health.*check.*failed', 'Health check failed'),
        (r'(?i)UNHEALTHY', 'Container unhealthy'),
        (r'(?i)health.*status.*unhealthy', 'Health status unhealthy'),
        (r'(?i)target.*unhealthy', 'Target unhealthy'),
        (r'(?i)failed.*health.*check', 'Failed health check'),
        (r'(?i)failed container health checks', 'Container health check failure'),
        (r'(?i)failed ELB health checks', 'ELB health check failure'),
        (r'(?i)Instance.*port.*is unhealthy', 'Instance port unhealthy'),
        (r'(?i)task.*stopped', 'Task stopped'),
        (r'(?i)container.*stopped.*unexpectedly', 'Container stopped unexpectedly'),
        (r'(?i)container.*exited.*non-zero', 'Container exited with non-zero code'),
        (r'(?i)Essential container.*exited', 'Essential container exited'),
        (r'(?i)STOPPED', 'Task/container stopped'),
        (r'(?i)DEPROVISIONING', 'Task deprovisioning'),
        (r'(?i)exit code 143', 'Container graceful shutdown (SIGTERM)'),
        (r'(?i)exit code 1', 'Container general error'),
        (r'(?i)task.*stuck.*PROVISIONING', 'Task stuck in provisioning'),
        (r'(?i)task.*stuck.*PENDING', 'Task stuck in pending'),
        (r'(?i)service.*was unable to place a task', 'Service placement failure'),
        (r'(?i)service.*has stopped.*running tasks', 'Service stopped tasks'),
        (r'(?i)deployment circuit breaker.*rolling back', 'Deployment rolling back'),
        (r'(?i)service.*unable to place a task', 'Task placement failure'),
        (r'(?i)service.*discovery.*failed', 'Service discovery failed'),
        (r'(?i)failed.*register.*service', 'Failed to register service'),
        (r'(?i)DNS.*registration.*failed', 'DNS registration failed'),
        (r'(?i)target.*draining', 'Target draining'),
        (r'(?i)deregistering.*target', 'Deregistering target'),
        (r'(?i)failed.*register.*target', 'Failed to register target'),
        (r'(?i)target-group.*is unhealthy', 'Target group unhealthy'),
        (r'(?i)scaling.*activity.*failed', 'Scaling activity failed'),
        (r'(?i)unable.*scale', 'Unable to scale'),
        (r'(?i)capacity.*provider.*error', 'Capacity provider error'),
        (r'(?i)service.*began draining connections', 'Service draining connections'),
        (r'(?i)connection.*refused', 'Connection refused'),
        (r'(?i)connection.*timeout', 'Connection timeout'),
        (r'(?i)dial.*tcp.*timeout', 'TCP dial timeout'),
        (r'(?i)i/o timeout', 'I/O timeout'),
        (r'(?i)TLS.*handshake.*timeout', 'TLS handshake timeout'),
        (r'(?i)DNS.*failed', 'DNS resolution failed'),
        (r'(?i)no.*route.*host', 'No route to host'),
        (r'(?i)packet.*dropped', 'Packets dropped'),
        (r'(?i)conntrack.*table.*full', 'Conntrack table full'),
        (r'(?i)Post.*dial tcp.*timeout', 'HTTP POST timeout'),
        (r'(?i)memory.*pressure', 'Memory pressure'),
        (r'(?i)cpu.*throttl', 'CPU throttling'),
        (r'(?i)disk.*pressure', 'Disk pressure'),
        (r'(?i)inode.*exhausted', 'Inodes exhausted'),
        (r'(?i)image.*pull.*slow', 'Slow image pull'),
        (r'(?i)layer.*already.*exists', 'Layer already exists (potential issue)'),
        (r'(?i)docker.*restart', 'Docker restarted'),
        (r'(?i)docker.*timeout', 'Docker timeout'),
        (r'(?i)log.*driver.*error', 'Log driver error'),
        (r'(?i)failed.*send.*logs', 'Failed to send logs'),
        (r'(?i)CloudWatch.*error', 'CloudWatch logging error'),
        (r'(?i)awslogs.*error', 'awslogs driver error'),
        (r'(?i)logs:CreateLogStream.*denied', 'CreateLogStream permission denied'),
        (r'(?i)performing maintenance on.*infrastructure', 'AWS infrastructure maintenance'),
        (r'(?i)task retirement', 'Task retirement notice'),
        (r'(?i)error', 'Error detected'),
        (r'(?i)fail', 'Failure detected'),
        (r'(?i)denied', 'Access denied'),
        (r'(?i)refused', 'Connection refused'),
        (r'(?i)timeout', 'Timeout detected'),
        (r'(?i)unauthorized', 'Unauthorized'),
        (r'(?i)forbidden', 'Forbidden'),
        (r'(?i)backoff', 'Backoff detected'),
    ],
    'info': [
        (r'(?i)warn', 'Warning'),
        (r'(?i)warning', 'Warning'),
        (r'(?i)unable', 'Unable to perform operation'),
        (r'(?i)cannot', 'Cannot perform operation'),
        (r'(?i)invalid', 'Invalid configuration'),
        (r'(?i)deprecated', 'Deprecated feature'),
        (r'(?i)missing', 'Missing resource'),
        (r'(?i)not found', 'Resource not found'),
        (r'(?i)retrying', 'Retrying operation'),
        (r'(?i)slow', 'Slow operation'),
        (r'(?i)delayed', 'Delayed operation'),
        (r'(?i)waiting', 'Waiting for resource'),
        (r'(?i)pending', 'Pending operation'),
    ]
}

ECS_TRIAGE_CATEGORIES = {
    'A': {'name': 'Task Startup Failures', 'patterns': [(r'CannotPullContainerError', 'high'), (r'CannotPullECRContainerError', 'high'), (r'ResourceInitializationError', 'high'), (r'TaskFailedToStart', 'high'), (r'CannotStartContainerError', 'high'), (r'CannotCreateContainerError', 'high'), (r'ContainerRuntimeError', 'high'), (r'ContainerRuntimeTimeoutError', 'high'), (r'CannotCreateVolumeError', 'high'), (r'CannotInspectContainerError', 'high'), (r'CannotStopContainerError', 'medium'), (r'SpotInterruptionError', 'high'), (r'InternalError', 'high')], 'log_sources': ['ecs-agent', 'docker', 'containerd'], 'description': 'Task fails to start due to image pull, resource init, or container runtime issues', 'runbook': 'AWSSupport-TroubleshootECSTaskFailedToStart'},
    'B': {'name': 'Image Pull Issues', 'patterns': [(r'pull.*access.*denied', 'high'), (r'repository.*does.*not.*exist', 'high'), (r'manifest.*not.*found', 'high'), (r'unauthorized.*authentication', 'high'), (r'no.*basic.*auth.*credentials', 'high'), (r'ECR.*token.*expired', 'high'), (r'toomanyrequests.*Too Many Requests', 'high'), (r'failed to resolve ref.*not found', 'high'), (r'net/http.*request canceled', 'high'), (r'API error \(500\)', 'high'), (r'ecr:BatchGetImage.*not authorized', 'high'), (r'inspect image has been retried', 'high')], 'log_sources': ['ecs-agent', 'docker'], 'description': 'ECR/Docker Hub authentication, image not found, registry connectivity, rate limits', 'docs': 'https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_cannot_pull_image.html'},
    'C': {'name': 'IAM/Secrets Issues', 'patterns': [(r'No valid providers in chain', 'high'), (r'AccessDeniedException', 'high'), (r'is not authorized to perform', 'high'), (r'failed.*retrieve.*secrets', 'high'), (r'SecretNotFound', 'high'), (r'ParameterNotFound', 'high'), (r'AssumeRoleUnauthorizedAccess', 'high'), (r'ecr:GetAuthorizationToken.*denied', 'high'), (r'secretsmanager:GetSecretValue.*denied', 'high'), (r'ssm:GetParameters.*denied', 'high'), (r'unable to pull secrets or registry auth', 'high'), (r'unable to retrieve secret from asm', 'high'), (r'unable to retrieve ecr registry auth', 'high')], 'log_sources': ['ecs-agent', 'messages', 'secure'], 'description': 'Task execution role, task role, secrets manager, SSM parameter store', 'docs': 'https://repost.aws/knowledge-center/ecs-unable-to-pull-secrets'},
    'D': {'name': 'Resource Exhaustion', 'patterns': [(r'insufficient.*cpu', 'high'), (r'insufficient.*memory', 'high'), (r'OutOfMemoryError', 'high'), (r'oom.*kill', 'high'), (r'exit code 137', 'high'), (r'exit code 139', 'high'), (r'no.*space.*left', 'high'), (r'Memory cgroup out of memory', 'high'), (r'invoked oom-killer', 'high'), (r'Killed process.*total-vm', 'high')], 'log_sources': ['dmesg', 'messages', 'cgroups', 'docker'], 'description': 'CPU/memory limits, OOM kills, disk space', 'docs': 'https://docs.aws.amazon.com/AmazonECS/latest/developerguide/out-of-memory.html'},
    'E': {'name': 'Networking Issues', 'patterns': [(r'ENI.*allocation.*failed', 'high'), (r'InsufficientFreeAddressesInSubnet', 'high'), (r'network.*unreachable', 'high'), (r'connection.*refused', 'medium'), (r'connection.*timeout', 'medium'), (r'DNS.*failed', 'medium'), (r'Timeout waiting for network interface', 'high'), (r'failed.*create.*network.*interface', 'high'), (r'i/o timeout', 'medium'), (r'dial.*tcp.*timeout', 'medium')], 'log_sources': ['networking', 'ecs-agent', 'docker'], 'description': 'ENI allocation, subnet IP exhaustion, security groups, DNS, VPC endpoints'},
    'F': {'name': 'Health Check Failures', 'patterns': [(r'health.*check.*failed', 'high'), (r'UNHEALTHY', 'high'), (r'target.*unhealthy', 'high'), (r'Essential container.*exited', 'high'), (r'failed container health checks', 'high'), (r'failed ELB health checks', 'high'), (r'Instance.*port.*is unhealthy', 'high')], 'log_sources': ['ecs-agent', 'docker', 'containers'], 'description': 'Container health checks, ALB/NLB target health'},
    'G': {'name': 'ECS Agent Issues', 'patterns': [(r'agent.*connected.*false', 'high'), (r'AGENT_DISCONNECTED', 'high'), (r'failed.*register.*container.*instance', 'high'), (r'No container instances were found', 'high'), (r'ECS Agent failed to start', 'high'), (r'websocket.*unable to dial', 'high'), (r'Error getting ECS instance credentials', 'high'), (r'client version.*is too old', 'high')], 'log_sources': ['ecs-agent', 'messages'], 'description': 'Agent connectivity, instance registration, cluster communication'},
    'H': {'name': 'Logging/Monitoring Issues', 'patterns': [(r'log.*driver.*error', 'medium'), (r'failed.*send.*logs', 'medium'), (r'CloudWatch.*error', 'medium'), (r'awslogs.*error', 'medium'), (r'failed to validate logger args', 'high'), (r'failed to initialize logging driver', 'high'), (r'logs:CreateLogStream.*denied', 'high')], 'log_sources': ['docker', 'ecs-agent'], 'description': 'CloudWatch logs, FireLens, log driver configuration'},
    'I': {'name': 'Deployment/Circuit Breaker', 'patterns': [(r'deployment circuit breaker.*triggered', 'high'), (r'ECS Deployment Circuit Breaker was triggered', 'high'), (r'deployment circuit breaker.*rolling back', 'high'), (r'service.*was unable to place a task', 'high'), (r'service.*has stopped.*running tasks', 'medium')], 'log_sources': ['ecs-agent'], 'description': 'Deployment failures, circuit breaker triggers, rollbacks', 'docs': 'https://repost.aws/knowledge-center/ecs-troubleshoot-deployment-failures'},
    'J': {'name': 'Container Runtime Issues', 'patterns': [(r'OCI runtime create failed', 'high'), (r'exec format error', 'high'), (r'container_linux.go.*starting container process', 'high'), (r'no such file or directory.*entrypoint', 'high'), (r'permission denied.*entrypoint', 'high'), (r'docker.*daemon.*not.*running', 'high'), (r'containerd.*not.*running', 'high')], 'log_sources': ['docker', 'containerd'], 'description': 'Docker/containerd runtime errors, entrypoint issues, architecture mismatch'},
}

ECS_LOG_TYPE_PATTERNS = {
    'ecs-agent': ['ecs-agent', 'ecs/', 'ecs_agent', 'ecs-init', 'amazon-ecs-agent',
                  'ecs.config', 'ecs_agent_data', 'agent-running-info'],
    'docker': ['docker', 'daemon.json', 'containerd', 'docker-info', 'docker-ps',
               'docker-images', 'docker-version', 'docker-stats', 'docker-not-running',
               'sysconfig-docker', 'docker-storage', 'docker.service', 'containerd.service'],
    'containers': ['containers/', 'container-logs', 'container-'],
    'system': ['messages', 'syslog', 'secure', 'audit', 'journal', 'system.log',
               'services.txt', 'top.txt', 'ps.txt', 'pkglist', 'os-release',
               'uname', 'dmidecode', 'lsmod', 'open-file', 'mounts',
               'lvdisplay', 'vgdisplay', 'pvdisplay', 'selinux'],
    'dmesg': ['dmesg'],
    'networking': ['networking', 'iptables', 'ip-', 'netstat', 'ss-',
                   'brctlshow', 'ipaddrshow', 'veth'],
    'cgroups': ['cgroup', 'memory-events', 'memory-stat', 'cgroupv2',
                'system.slice', 'ecstasks.slice'],
    'metadata': ['metadata', 'instance-'],
    'gpu': ['gpu', 'nvidia', 'gpu-list', 'gpu-info', 'gpu-open-module', 'gpu-installed-kmod'],
}


# Pre-compile all ECS_ERROR_PATTERNS at module level
COMPILED_ERROR_PATTERNS = {}
for _sev_key, _patterns in ECS_ERROR_PATTERNS.items():
    COMPILED_ERROR_PATTERNS[_sev_key] = []
    for _pat, _desc in _patterns:
        try:
            COMPILED_ERROR_PATTERNS[_sev_key].append((re.compile(_pat, re.IGNORECASE), _desc))
        except re.error:
            pass

# False positive suppression patterns
FALSE_POSITIVE_PATTERNS = [
    re.compile(r'(?i)error_count["\s]*[:=]\s*0'),
    re.compile(r'(?i)no\s+errors?\s+found'),
    re.compile(r'(?i)errors?["\s]*[:=]\s*null'),
    re.compile(r'(?i)error_rate["\s]*[:=]\s*0'),
    re.compile(r'(?i)--error-'),
    re.compile(r'(?i)error\.log'),
    re.compile(r'(?i)if.*error'),
    re.compile(r'(?i)catch.*error'),
    re.compile(r'(?i)handle.*error'),
]

# ============================================================================
# SEVERITY
# ============================================================================

class Severity(Enum):
    CRITICAL = 'critical'
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'
    INFO = 'info'

SEVERITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}

# ============================================================================
# SOP KEYWORD MAP — maps issue keywords to runbook files
# ============================================================================

SOP_KEYWORD_MAP = {
    # ── Task Startup Failures ──
    'task_startup': [
        {'sop': 'runbooks/A1-task-startup-resource-init.md', 'keywords': ['ResourceInitializationError', 'TaskFailedToStart', 'CannotStartContainerError', 'CannotCreateContainerError', 'resource.*init'], 'relevance': 'primary'},
        {'sop': 'runbooks/A2-task-startup-container-runtime.md', 'keywords': ['ContainerRuntimeError', 'ContainerRuntimeTimeoutError', 'docker.*daemon.*not.*running', 'containerd.*not.*running'], 'relevance': 'primary'},
    ],
    # ── Image Pull Issues ──
    'image_pull': [
        {'sop': 'runbooks/B1-ecr-image-pull-auth.md', 'keywords': ['CannotPullECRContainerError', 'ecr:GetAuthorizationToken.*denied', 'ecr:BatchGetImage.*not authorized', 'pull.*access.*denied', 'CannotPullContainerError'], 'relevance': 'primary'},
        {'sop': 'runbooks/B2-image-not-found.md', 'keywords': ['manifest.*not.*found', 'repository.*does.*not.*exist', 'image.*not.*found', 'failed to resolve ref.*not found'], 'relevance': 'primary'},
        {'sop': 'runbooks/B3-docker-hub-rate-limit.md', 'keywords': ['toomanyrequests', 'Too Many Requests', 'rate limit', 'docker.io.*rate'], 'relevance': 'primary'},
    ],
    # ── IAM / Secrets Issues ──
    'iam_secrets': [
        {'sop': 'runbooks/C1-task-execution-role-permissions.md', 'keywords': ['AccessDeniedException', 'is not authorized to perform', 'No valid providers in chain', 'AssumeRoleUnauthorizedAccess', 'execution role.*does not have', 'UnauthorizedOperation'], 'relevance': 'primary'},
        {'sop': 'runbooks/C2-secrets-manager-retrieval.md', 'keywords': ['unable to pull secrets', 'unable to retrieve secret', 'SecretNotFound', 'ParameterNotFound', 'secretsmanager:GetSecretValue.*denied', 'ssm:GetParameters.*denied', 'execution resource retrieval failed'], 'relevance': 'primary'},
    ],
    # ── Resource Exhaustion ──
    'resource_exhaustion': [
        {'sop': 'runbooks/D1-oom-kill-memory.md', 'keywords': ['OutOfMemoryError', 'oom.*kill', 'Memory cgroup out of memory', 'invoked oom-killer', 'exit code 137', 'Killed process.*total-vm', 'insufficient.*memory'], 'relevance': 'primary'},
        {'sop': 'runbooks/D2-disk-space-exhaustion.md', 'keywords': ['no.*space.*left.*device', 'disk.*full', 'disk.*pressure', 'inode.*exhausted'], 'relevance': 'primary'},
        {'sop': 'runbooks/D3-cpu-throttling.md', 'keywords': ['cpu.*throttl', 'insufficient.*cpu', 'cpu.*limit'], 'relevance': 'primary'},
    ],
    # ── Networking Issues ──
    'networking': [
        {'sop': 'runbooks/E1-eni-allocation-subnet-ip.md', 'keywords': ['ENI.*allocation.*failed', 'InsufficientFreeAddressesInSubnet', 'Timeout waiting for network interface', 'failed.*create.*network.*interface', 'no.*available.*IP'], 'relevance': 'primary'},
        {'sop': 'runbooks/E2-dns-resolution-failures.md', 'keywords': ['DNS.*failed', 'resolve.*fail', 'SERVFAIL', 'NXDOMAIN', 'name.*resolution', 'no.*route.*host'], 'relevance': 'primary'},
        {'sop': 'runbooks/E3-connection-timeout.md', 'keywords': ['connection.*timeout', 'network.*unreachable', 'dial.*tcp.*timeout', 'i/o timeout', 'TLS.*handshake.*timeout', 'connection.*refused'], 'relevance': 'primary'},
    ],
    # ── Health Check Failures ──
    'health_checks': [
        {'sop': 'runbooks/F1-container-health-check.md', 'keywords': ['health.*check.*failed', 'UNHEALTHY', 'failed container health checks', 'health.*status.*unhealthy'], 'relevance': 'primary'},
        {'sop': 'runbooks/F2-elb-target-health.md', 'keywords': ['target.*unhealthy', 'failed ELB health checks', 'Instance.*port.*is unhealthy', 'deregistering.*target'], 'relevance': 'primary'},
    ],
    # ── ECS Agent Issues ──
    'ecs_agent': [
        {'sop': 'runbooks/G1-agent-disconnected.md', 'keywords': ['agent.*connected.*false', 'AGENT_DISCONNECTED', 'websocket.*unable to dial', 'Error getting ECS instance credentials', 'agent.*not.*connected'], 'relevance': 'primary'},
        {'sop': 'runbooks/G2-instance-registration-failure.md', 'keywords': ['failed.*register.*container.*instance', 'No container instances were found', 'ECS Agent failed to start', 'client version.*is too old'], 'relevance': 'primary'},
    ],
    # ── Logging/Monitoring Issues ──
    'logging': [
        {'sop': 'runbooks/H1-cloudwatch-log-driver.md', 'keywords': ['log.*driver.*error', 'failed.*send.*logs', 'awslogs.*error', 'failed to initialize logging driver', 'logs:CreateLogStream.*denied', 'CloudWatch.*error', 'failed to validate logger args'], 'relevance': 'primary'},
    ],
    # ── Deployment / Circuit Breaker ──
    'deployment': [
        {'sop': 'runbooks/I1-deployment-circuit-breaker.md', 'keywords': ['deployment circuit breaker.*triggered', 'ECS Deployment Circuit Breaker', 'circuit breaker.*rolling back', 'service.*was unable to place a task', 'service.*has stopped.*running tasks'], 'relevance': 'primary'},
    ],
    # ── Container Runtime Issues ──
    'container_runtime': [
        {'sop': 'runbooks/J1-oci-runtime-entrypoint.md', 'keywords': ['OCI runtime create failed', 'exec format error', 'container_linux.go.*starting container process', 'no such file or directory.*entrypoint', 'permission denied.*entrypoint'], 'relevance': 'primary'},
    ],
    # ── Spot Interruption / Instance Draining ──
    'spot_interruption': [
        {'sop': 'runbooks/K1-spot-interruption-instance-draining.md', 'keywords': ['spot.*interrupt', 'instance.*drain', 'DRAINING', 'Spot Instance interruption', 'rebalance.*recommendation', 'capacity.*rebalance'], 'relevance': 'primary'},
    ],
    # ── Task Placement Failures ──
    'task_placement': [
        {'sop': 'runbooks/K2-task-placement-failures.md', 'keywords': ['no container instance.*met.*requirements', 'placement.*constraint', 'placement.*strategy', 'unable to place', 'distinctInstance', 'memberOf', 'attribute:ecs'], 'relevance': 'primary'},
    ],
    # ── Service Steady State Failures ──
    'service_stability': [
        {'sop': 'runbooks/K3-service-steady-state-failures.md', 'keywords': ['unable to reach steady state', 'steady state', 'has stopped.*running tasks', 'rolling back', 'service.*unstable', 'deployment.*failed'], 'relevance': 'primary'},
    ],
    # ── Auto Scaling / Capacity Provider ──
    'auto_scaling': [
        {'sop': 'runbooks/K4-service-auto-scaling-issues.md', 'keywords': ['auto.*scal', 'capacity.*provider', 'managed.*scaling', 'target.*tracking', 'scaling.*policy', 'desired.*count', 'CapacityProviderReservation'], 'relevance': 'primary'},
    ],
    # ── ECS Exec / SSM Failures ──
    'ecs_exec': [
        {'sop': 'runbooks/K5-ecs-exec-failures.md', 'keywords': ['ECS Exec', 'execute-command', 'ExecuteCommandAgent', 'SSM.*session', 'ssmmessages', 'session.*manager', 'exec.*failed'], 'relevance': 'primary'},
    ],
    # ── Service Connect / Cloud Map ──
    'service_connect': [
        {'sop': 'runbooks/K6-service-connect-discovery-failures.md', 'keywords': ['Service Connect', 'Cloud Map', 'service.*discovery', 'namespace.*not.*found', 'servicediscovery', 'cloudmap', 'DNS.*SRV'], 'relevance': 'primary'},
    ],
    # ── EBS/EFS Volume Mount Failures ──
    'volume_mount': [
        {'sop': 'runbooks/K7-ebs-efs-volume-mount-failures.md', 'keywords': ['volume.*mount.*fail', 'EBS.*attach', 'EFS.*mount', 'nfs.*timeout', 'mount.*target', 'volume.*not.*found', 'bind.*mount'], 'relevance': 'primary'},
    ],
    # ── Task Stuck in PENDING ──
    'task_pending': [
        {'sop': 'runbooks/K8-task-stuck-pending.md', 'keywords': ['PENDING', 'PROVISIONING', 'stuck', 'task not starting', 'waiting for capacity', 'timed out waiting'], 'relevance': 'primary'},
    ],
    # ── Fargate Platform / Ephemeral Storage ──
    'fargate_platform': [
        {'sop': 'runbooks/K9-fargate-platform-ephemeral-storage.md', 'keywords': ['platform version', 'ephemeral storage', 'no space left.*fargate', 'platform 1\\.3', 'platform 1\\.4', 'storage exceeded', 'ephemeralStorage'], 'relevance': 'primary'},
    ],
    # ── API Throttling / Service Quotas ──
    'api_throttling': [
        {'sop': 'runbooks/K10-api-throttling-service-quotas.md', 'keywords': ['throttl', 'rate.*limit', 'Rate exceeded', 'TooManyRequestsException', 'Limit exceeded', 'service.*quota', 'RequestLimitExceeded', 'Operations are being throttled'], 'relevance': 'primary'},
    ],
    # ── Fargate Metadata / Credential Errors ──
    'metadata_credentials': [
        {'sop': 'runbooks/K11-fargate-metadata-credential-errors.md', 'keywords': ['Missing credentials', 'could not load credentials', 'metadata.*error', 'credential.*provider', '169\\.254\\.170', 'ECS_CONTAINER_METADATA', 'IMDS'], 'relevance': 'primary'},
    ],
    # ── Essential Container Exited Non-Zero ──
    'container_exit': [
        {'sop': 'runbooks/K12-essential-container-exited-nonzero.md', 'keywords': ['EssentialContainerExited', 'exit code', 'non-zero', 'exit.*137', 'exit.*139', 'exit.*143', 'SIGKILL', 'SIGTERM', 'segfault', 'essential container.*exit'], 'relevance': 'primary'},
    ],
    # ── Windows Container Issues ──
    'windows': [
        {'sop': 'runbooks/K13-windows-container-issues.md', 'keywords': ['Windows', 'OS mismatch', 'operating system does not match', 'EnableTaskIAMRole', 'ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE', 'No valid providers in chain.*windows', 'Unable to assume the role.*windows', 'Windows Server'], 'relevance': 'primary'},
    ],
    # ── Task Latency / Performance ──
    'performance': [
        {'sop': 'runbooks/K14-task-latency-performance.md', 'keywords': ['latency', 'slow', 'performance', 'response time', 'TargetResponseTime', 'TTFB', 'EBS.*throttl', 'network.*throughput', 'DNS.*slow'], 'relevance': 'primary'},
    ],
    # ── Docker Daemon / Agent Errors ──
    'docker_agent': [
        {'sop': 'runbooks/K15-docker-daemon-agent-errors.md', 'keywords': ['Docker.*API.*500', 'devmapper', 'thin pool', 'docker.*daemon', 'containerd.*error', 'docker\\.sock', 'storage.*driver', 'container.*runtime.*error'], 'relevance': 'primary'},
    ],
}

# Map ECS triage categories (A-K) to SOP keyword groups
TRIAGE_CATEGORY_TO_SOP_GROUP = {
    'A': ['task_startup', 'image_pull', 'container_exit', 'fargate_platform'],
    'B': ['image_pull'],
    'C': ['iam_secrets', 'metadata_credentials'],
    'D': ['resource_exhaustion', 'performance'],
    'E': ['networking', 'service_connect'],
    'F': ['health_checks'],
    'G': ['ecs_agent', 'docker_agent'],
    'H': ['logging'],
    'I': ['deployment', 'service_stability', 'auto_scaling'],
    'J': ['container_runtime', 'docker_agent', 'windows'],
    'K': ['spot_interruption', 'task_placement', 'task_pending', 'ecs_exec', 'volume_mount', 'api_throttling'],
}


def match_sops_for_issues(issues: List[Dict], findings: List[Dict] = None,
                          triage_category: str = None, max_sops: int = 5) -> List[Dict]:
    """
    Match detected issues/findings against SOP runbooks.
    Returns a list of recommended SOPs with relevance and reason.

    Args:
        issues: List of issue dicts from diagnostics (each has 'message' and 'section')
        findings: Optional list of error findings (each has 'pattern', 'sample')
        triage_category: Optional triage category ID (A-J) from ECS triage root cause
        max_sops: Maximum SOPs to return
    """
    scored_sops = {}  # sop_name -> {score, reasons, keywords}

    # Build a combined text corpus from issues and findings for keyword matching
    issue_texts = []
    for issue in (issues or []):
        issue_texts.append(issue.get('message', ''))
    for finding in (findings or []):
        issue_texts.append(finding.get('pattern', ''))
        issue_texts.append(finding.get('sample', '')[:200])
    corpus = ' '.join(issue_texts).lower()

    # If triage category is known, prioritize SOPs from that category's groups
    priority_groups = set()
    if triage_category and triage_category in TRIAGE_CATEGORY_TO_SOP_GROUP:
        priority_groups = set(TRIAGE_CATEGORY_TO_SOP_GROUP[triage_category])

    for group_name, sop_entries in SOP_KEYWORD_MAP.items():
        is_priority = group_name in priority_groups
        for entry in sop_entries:
            sop_name = entry['sop']
            matched_keywords = []
            for kw in entry['keywords']:
                try:
                    if re.search(kw, corpus, re.IGNORECASE):
                        matched_keywords.append(kw)
                except re.error:
                    if kw.lower() in corpus:
                        matched_keywords.append(kw)

            if matched_keywords:
                if sop_name not in scored_sops:
                    scored_sops[sop_name] = {'score': 0, 'reasons': [], 'keywords': []}
                scored_sops[sop_name]['score'] += len(matched_keywords) * 3
                if is_priority:
                    scored_sops[sop_name]['score'] += 5
                scored_sops[sop_name]['keywords'].extend(matched_keywords[:3])
                scored_sops[sop_name]['reasons'].append(
                    f"Matched {len(matched_keywords)} keyword(s) from {group_name}"
                )

    # Always include Z1 general troubleshooting if any issues exist but no specific SOPs matched
    if not scored_sops and (issues or findings):
        scored_sops['runbooks/Z1-general-troubleshooting.md'] = {
            'score': 1,
            'reasons': ['General troubleshooting guide for unmatched issues'],
            'keywords': []
        }

    # Sort by score descending, take top N
    sorted_sops = sorted(scored_sops.items(), key=lambda x: x[1]['score'], reverse=True)
    result = []
    for sop_name, info in sorted_sops[:max_sops]:
        result.append({
            'sopName': sop_name,
            'relevanceScore': info['score'],
            'matchedKeywords': list(set(info['keywords']))[:5],
            'reason': '; '.join(info['reasons'][:2]),
        })
    return result


def normalize_severity_filter(severity_filter: str) -> list:
    if severity_filter == 'all':
        return ['critical', 'warning', 'info']
    if severity_filter in ('critical', 'warning', 'info'):
        return [severity_filter]
    return ['critical', 'warning', 'info']


def assign_finding_id(index: int) -> str:
    return f"F-{index:03d}"


# ============================================================================
# RESPONSE HELPERS
# ============================================================================

def success_response(data: Dict) -> Dict:
    MAX_PAYLOAD_BYTES = 5_500_000
    body = json.dumps({'success': True, **data}, default=str)
    if len(body.encode('utf-8')) > MAX_PAYLOAD_BYTES:
        truncated_data = {k: v for k, v in data.items() if not isinstance(v, list)}
        for k, v in data.items():
            if isinstance(v, list):
                trimmed = v
                while trimmed:
                    candidate = json.dumps({
                        'success': True, **truncated_data, k: trimmed,
                        '_payloadTruncated': True, '_originalCount': len(v), '_returnedCount': len(trimmed),
                    }, default=str)
                    if len(candidate.encode('utf-8')) <= MAX_PAYLOAD_BYTES:
                        return {'statusCode': 200, 'body': candidate}
                    trimmed = trimmed[:len(trimmed) // 2]
                truncated_data[k] = []
        body = json.dumps({'success': True, **truncated_data, '_payloadTruncated': True, '_error': 'Response too large'}, default=str)
    return {'statusCode': 200, 'body': body}


def error_response(code: int, message: str, details: Dict = None) -> Dict:
    body = {'success': False, 'error': message}
    if details:
        body['details'] = details
    return {'statusCode': code, 'body': json.dumps(body, default=str)}


# ============================================================================
# S3 SAFE HELPERS
# ============================================================================

def safe_s3_read_raw(bucket: str, key: str, byte_range: str = None, s3c=None) -> Optional[bytes]:
    """Legacy S3 read — returns raw bytes or None."""
    try:
        c = s3c or s3_client
        kwargs = {'Bucket': bucket, 'Key': key}
        if byte_range:
            kwargs['Range'] = byte_range
        return c.get_object(**kwargs)['Body'].read()
    except Exception:
        return None


def safe_s3_head_raw(bucket: str, key: str, s3c=None) -> Optional[Dict]:
    """Legacy S3 head — returns raw boto3 response or None."""
    try:
        c = s3c or s3_client
        return c.head_object(Bucket=bucket, Key=key)
    except Exception:
        return None


def safe_s3_read(key: str, range_bytes: str = None, max_size: int = 1048576) -> Dict:
    """
    EKS-compatible S3 read — returns dict with 'success', 'content' or 'error'.
    NEVER raises exceptions.
    """
    try:
        params = {'Bucket': LOGS_BUCKET, 'Key': key}
        if range_bytes:
            params['Range'] = range_bytes
        elif max_size:
            params['Range'] = f'bytes=0-{max_size - 1}'
        response = s3_client.get_object(**params)
        content = response['Body'].read()
        try:
            content_str = content.decode('utf-8')
        except UnicodeDecodeError:
            content_str = content.decode('latin-1', errors='replace')
        return {'success': True, 'content': content_str, 'size': len(content),
                'content_type': response.get('ContentType', 'unknown')}
    except Exception as e:
        return {'success': False, 'error': f'Failed to read {key}: {str(e)}',
                'error_type': 'read_error', 'content': ''}


def safe_s3_head(key: str) -> Dict:
    """
    EKS-compatible S3 head — returns dict with 'success', 'size' or 'error'.
    NEVER raises exceptions.
    """
    try:
        response = s3_client.head_object(Bucket=LOGS_BUCKET, Key=key)
        return {'success': True, 'size': response['ContentLength'],
                'content_type': response.get('ContentType', 'unknown'),
                'last_modified': response.get('LastModified')}
    except Exception as e:
        return {'success': False, 'error': f'Failed to get metadata for {key}: {str(e)}',
                'error_type': 'metadata_error'}


def safe_s3_list(prefix: str, max_keys: int = 1000) -> Dict:
    """
    Safely list S3 objects with graceful error handling.
    NEVER raises exceptions - always returns a result dict.
    """
    try:
        all_objects = []
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=LOGS_BUCKET, Prefix=prefix, PaginationConfig={'MaxItems': max_keys}):
            for obj in page.get('Contents', []):
                all_objects.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj.get('LastModified')
                })
        return {
            'success': True,
            'objects': all_objects,
            'count': len(all_objects)
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'Failed to list objects with prefix {prefix}: {str(e)}',
            'error_type': 'list_error',
            'objects': [],
            'count': 0
        }


def safe_s3_list_raw(bucket: str, prefix: str, max_keys: int = 1000, s3c=None) -> List[Dict]:
    """Legacy list helper that returns raw S3 Contents list."""
    try:
        c = s3c or s3_client
        resp = c.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=max_keys)
        return resp.get('Contents', [])
    except Exception:
        return []


def find_latest_bundle_files(instance_id: str, prefix_scheme: str = 'ecs') -> Dict:
    """
    Shared helper: discover the LATEST extracted bundle for an instance.
    Returns only files from the most recent bundle (by last_modified timestamp).
    """
    search_result = safe_s3_list(f"{prefix_scheme}_{instance_id}", max_keys=5000)
    if not search_result.get('success'):
        return {'success': False, 'files': [], 'all_objects': [], 'error': search_result.get('error', 'S3 list failed')}

    all_objects = search_result.get('objects', [])
    bundle_files = []
    bundle_timestamps = {}
    for obj in all_objects:
        key = obj.get('key', '')
        if '/extracted/' in key:
            bundle_files.append(key)
            if obj.get('last_modified'):
                bundle_timestamps[key] = obj['last_modified']

    if not bundle_files:
        return {'success': False, 'files': [], 'all_objects': all_objects, 'error': f'No extracted log bundle found for {instance_id}. Run collect first.'}

    from collections import defaultdict
    bundles_by_prefix = defaultdict(list)
    for f in bundle_files:
        prefix_part = f.split('/extracted/')[0] if '/extracted/' in f else f
        bundles_by_prefix[prefix_part].append(f)

    latest_prefix = max(
        bundles_by_prefix.keys(),
        key=lambda p: max(
            (bundle_timestamps.get(f, datetime.min.replace(tzinfo=None)) for f in bundles_by_prefix[p]),
            default=datetime.min
        )
    )
    latest_files = bundles_by_prefix[latest_prefix]

    bundle_age_minutes = None
    bundle_collected_at = None
    if bundle_timestamps:
        ts_values = [ts for f in latest_files for ts in [bundle_timestamps.get(f)] if ts is not None]
        if ts_values:
            newest_ts = max(ts_values)
            now_utc = datetime.now(timezone.utc)
            if newest_ts.tzinfo is None:
                newest_ts = newest_ts.replace(tzinfo=timezone.utc)
            bundle_age_minutes = int((now_utc - newest_ts).total_seconds() / 60)
            bundle_collected_at = newest_ts.isoformat()

    return {
        'success': True,
        'files': latest_files,
        'bundle_prefix': latest_prefix,
        'bundle_age_minutes': bundle_age_minutes,
        'bundle_collected_at': bundle_collected_at,
        'all_objects': all_objects,
    }


# ============================================================================
# REGIONAL CLIENT HELPERS
# ============================================================================

def get_regional_client(service: str, region: str) -> Any:
    if region == DEFAULT_REGION:
        if service == 'ssm': return ssm_client
        if service == 's3': return s3_client
        if service == 'ec2': return ec2_client
        if service == 'ecs': return ecs_client
    cache_key = f'{service}:{region}'
    if cache_key not in _regional_clients:
        _regional_clients[cache_key] = boto3.client(service, region_name=region)
    return _regional_clients[cache_key]


def detect_instance_region(instance_id: str) -> Optional[str]:
    start = time.time()
    try:
        resp = ec2_client.describe_instances(InstanceIds=[instance_id])
        if resp['Reservations']:
            return DEFAULT_REGION
    except Exception:
        pass
    common_regions = ['us-west-2', 'us-east-2', 'eu-west-1', 'eu-central-1', 'ap-southeast-1', 'ap-northeast-1', 'ap-south-1', 'us-west-1', 'eu-west-2', 'eu-north-1', 'ap-southeast-2', 'ap-northeast-2', 'sa-east-1', 'ca-central-1']
    common_regions = [r for r in common_regions if r != DEFAULT_REGION]
    for region in common_regions:
        if time.time() - start > 20:
            return None
        try:
            regional_ec2 = get_regional_client('ec2', region)
            resp = regional_ec2.describe_instances(InstanceIds=[instance_id])
            if resp['Reservations']:
                return region
        except Exception:
            continue
    return None


def resolve_region(arguments: Dict, instance_id: str = None) -> str:
    explicit = arguments.get('region')
    if explicit and re.match(r'^[a-z]{2}(-[a-z]+-\d+)$', explicit):
        return explicit
    if instance_id:
        detected = detect_instance_region(instance_id)
        if detected:
            return detected
    return DEFAULT_REGION


# ============================================================================
# UTILITY HELPERS
# ============================================================================

def format_bytes(size: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def parse_failure_reason(invocation: Dict) -> str:
    """Parse failure reason from SSM RunCommand invocation."""
    status = invocation.get('Status', invocation.get('StatusDetails', ''))
    stderr = invocation.get('StandardErrorContent', '')
    if stderr:
        lines = [l.strip() for l in stderr.strip().split('\n') if l.strip()]
        if lines:
            return f"{status}: {lines[-1][:200]}"
    return status


def estimate_progress(invocation: Dict) -> Dict:
    """Estimate progress from RunCommand invocation."""
    status = invocation.get('Status', '')
    status_map = {
        'Pending': {'percent': 5, 'step': 'Queued'},
        'InProgress': {'percent': 50, 'step': 'Collecting logs'},
        'Delayed': {'percent': 10, 'step': 'Delayed'},
        'Success': {'percent': 100, 'step': 'Complete'},
        'Cancelled': {'percent': 0, 'step': 'Cancelled'},
        'TimedOut': {'percent': 0, 'step': 'Timed out'},
        'Failed': {'percent': 0, 'step': 'Failed'},
    }
    progress = status_map.get(status, {'percent': 0, 'step': status})
    # Refine InProgress estimate from stdout
    if status == 'InProgress':
        stdout = invocation.get('StandardOutputContent', '')
        if 'Creating Archive' in stdout:
            progress = {'percent': 85, 'step': 'Creating archive'}
        elif 'Uploading to S3' in stdout:
            progress = {'percent': 95, 'step': 'Uploading to S3'}
        elif 'Collecting Network' in stdout:
            progress = {'percent': 70, 'step': 'Collecting network diagnostics'}
        elif 'Collecting ECS Agent' in stdout:
            progress = {'percent': 40, 'step': 'Collecting ECS agent info'}
        elif 'Collecting Docker' in stdout:
            progress = {'percent': 30, 'step': 'Collecting Docker info'}
        elif 'Collecting System' in stdout:
            progress = {'percent': 20, 'step': 'Collecting system info'}
    return progress


# ============================================================================
# IDEMPOTENCY (S3-based)
# ============================================================================

def store_execution_region(execution_id: str, region: str):
    try:
        s3_client.put_object(Bucket=LOGS_BUCKET, Key=f'execution-regions/{execution_id}', Body=region.encode(), ServerSideEncryption='AES256')
    except Exception:
        pass


def get_execution_region(execution_id: str) -> Optional[str]:
    data = safe_s3_read_raw(LOGS_BUCKET, f'execution-regions/{execution_id}')
    return data.decode('utf-8').strip() if data else None


def find_execution_by_idempotency_token(instance_id: str, token: str) -> Optional[Dict]:
    """Find existing execution by idempotency token, scoped to instance."""
    key = f'idempotency/{instance_id}/{token}.json'
    data = safe_s3_read_raw(LOGS_BUCKET, key)
    if data:
        try:
            return json.loads(data)
        except Exception:
            pass
    return None


def store_idempotency_mapping(instance_id: str, token: str, execution_id: str):
    """Store idempotency mapping scoped to instance."""
    mapping = {
        'executionId': execution_id,
        'instanceId': instance_id,
        'token': token,
        'status': 'InProgress',
        'createdAt': datetime.utcnow().isoformat(),
    }
    try:
        s3_client.put_object(
            Bucket=LOGS_BUCKET,
            Key=f'idempotency/{instance_id}/{token}.json',
            Body=json.dumps(mapping, default=str).encode(),
            ContentType='application/json',
        )
    except Exception as e:
        print(f"Warning: Failed to store idempotency mapping: {str(e)}")


# ============================================================================
# BASELINE HELPERS
# ============================================================================

def load_baselines(instance_id: str) -> Dict:
    data = safe_s3_read_raw(LOGS_BUCKET, f'baselines/{instance_id}/baseline.json')
    if data:
        try:
            return json.loads(data)
        except Exception:
            pass
    return {}


def update_baselines(instance_id: str, findings: List[Dict]):
    baseline = load_baselines(instance_id)
    for f in findings:
        sig = f'{f.get("description", "")}__{f.get("file", "")}'
        key = hashlib.md5(sig.encode()).hexdigest()
        if key not in baseline:
            baseline[key] = {'firstSeen': datetime.now(timezone.utc).isoformat(), 'count': 0, 'description': f.get('description', '')}
        baseline[key]['count'] = baseline[key].get('count', 0) + 1
        baseline[key]['lastSeen'] = datetime.now(timezone.utc).isoformat()
    try:
        s3_client.put_object(Bucket=LOGS_BUCKET, Key=f'baselines/{instance_id}/baseline.json', Body=json.dumps(baseline, default=str).encode(), ServerSideEncryption='AES256')
    except Exception:
        pass


def annotate_findings_with_baselines(findings: List[Dict], baselines: Dict) -> List[Dict]:
    for f in findings:
        sig = f'{f.get("description", "")}__{f.get("file", "")}'
        key = hashlib.md5(sig.encode()).hexdigest()
        if key in baselines:
            f['isBaseline'] = True
            f['baselineFirstSeen'] = baselines[key].get('firstSeen')
            f['baselineCount'] = baselines[key].get('count', 0)
        else:
            f['isBaseline'] = False
    return findings


# ============================================================================
# SCAN HELPERS
# ============================================================================

def find_findings_index(prefix: str, s3c=None) -> Optional[str]:
    """
    Find the findings index file for a log collection.
    Returns the S3 key string of the findings_index.json in the LATEST bundle, or None.
    """
    parts = prefix.split('_', 1)
    instance_id = parts[1] if len(parts) > 1 else prefix
    scheme = parts[0] if len(parts) > 1 else 'ecs'

    bundle_info = find_latest_bundle_files(instance_id, prefix_scheme=scheme)
    if not bundle_info['success']:
        return None

    index_files = [f for f in bundle_info['files'] if FINDINGS_INDEX_FILE in f]
    return index_files[0] if index_files else None


def scan_file_for_errors(content: str, filename: str) -> List[Dict]:
    findings = []
    lines = content.split('\n')
    for line_num, line in enumerate(lines, 1):
        if not line.strip():
            continue
        # False positive suppression
        if any(fp.search(line) for fp in FALSE_POSITIVE_PATTERNS):
            continue
        for sev_key, compiled_patterns in COMPILED_ERROR_PATTERNS.items():
            for regex, description in compiled_patterns:
                if regex.search(line):
                    findings.append({
                        'severity': sev_key,
                        'description': description,
                        'file': filename,
                        'lineNumber': line_num,
                        'line': line[:500],
                        'pattern': regex.pattern,
                    })
                    break  # One match per line per severity
    return findings


def scan_and_index_errors(prefix: str, s3c=None) -> Dict:
    """Scan all files in a bundle and build findings index."""
    c = s3c or s3_client
    files = safe_s3_list_raw(LOGS_BUCKET, prefix, s3c=c)
    all_findings = []
    files_scanned = 0
    for obj in files:
        key = obj['Key']
        size = obj.get('Size', 0)
        if any(key.endswith(ext) for ext in ['.tar.gz', '.zip', '.gz', '.tar', '.bin', '.so', '.png', '.jpg']):
            continue
        if key.endswith(FINDINGS_INDEX_FILE) or key.endswith('manifest.json'):
            continue
        if size > MAX_CHUNK_SIZE or size == 0:
            continue
        data = safe_s3_read_raw(LOGS_BUCKET, key, s3c=c)
        if not data:
            continue
        try:
            content = data.decode('utf-8', errors='ignore')
        except Exception:
            continue
        files_scanned += 1
        filename = key[len(prefix):] if key.startswith(prefix) else key.split('/')[-1]
        file_findings = scan_file_for_errors(content, filename)
        all_findings.extend(file_findings)
    # Assign finding IDs and sort by severity
    all_findings.sort(key=lambda f: SEVERITY_ORDER.get(f.get('severity', 'info'), 4))
    for i, f in enumerate(all_findings):
        f['finding_id'] = assign_finding_id(i + 1)
    index = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'filesScanned': files_scanned,
        'totalFindings': len(all_findings),
        'findings': all_findings,
        'summary': {},
    }
    for sev in ['critical', 'warning', 'info']:
        index['summary'][sev] = len([f for f in all_findings if f['severity'] == sev])
    # Store index
    try:
        c.put_object(Bucket=LOGS_BUCKET, Key=f'{prefix}{FINDINGS_INDEX_FILE}', Body=json.dumps(index, default=str).encode(), ServerSideEncryption='AES256')
    except Exception:
        pass
    return index


# ============================================================================
# READ / SEARCH HELPERS
# ============================================================================

def read_by_lines(bucket: str, key: str, start_line: int = 1, max_lines: int = DEFAULT_LINE_COUNT, s3c=None) -> Dict:
    head = safe_s3_head_raw(bucket, key, s3c=s3c)
    if not head:
        return {'error': f'File not found: {key}'}
    total_size = head['ContentLength']
    data = safe_s3_read_raw(bucket, key, s3c=s3c)
    if not data:
        return {'error': f'Cannot read: {key}'}
    try:
        content = data.decode('utf-8', errors='ignore')
    except Exception:
        content = data.decode('latin-1', errors='ignore')
    lines = content.split('\n')
    total_lines = len(lines)
    end_line = min(start_line + max_lines - 1, total_lines)
    selected = lines[start_line - 1:end_line]
    return {
        'lines': selected, 'startLine': start_line, 'endLine': end_line,
        'totalLines': total_lines, 'totalSize': total_size, 'hasMore': end_line < total_lines,
    }


def search_file_for_pattern(bucket: str, key: str, pattern: str, max_matches: int = 50, s3c=None) -> List[Dict]:
    """Search a file for regex pattern with chunked reading for large files."""
    head = safe_s3_head_raw(bucket, key, s3c=s3c)
    if not head:
        return []
    file_size = head['ContentLength']
    if file_size > 10 * 1024 * 1024:  # Skip files > 10MB
        return []
    data = safe_s3_read_raw(bucket, key, s3c=s3c)
    if not data:
        return []
    try:
        content = data.decode('utf-8', errors='ignore')
    except Exception:
        return []
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return []
    matches = []
    filename = key.split('/')[-1]
    for i, line in enumerate(content.split('\n'), 1):
        if regex.search(line):
            matches.append({
                'file': filename, 'fullKey': key, 'lineNumber': i,
                'line': line[:500], 'pattern': pattern,
            })
            if len(matches) >= max_matches:
                break
    return matches


def get_line_context(content: str, line_num: int, context: int = 3) -> List[str]:
    lines = content.split('\n')
    start = max(0, line_num - context - 1)
    end = min(len(lines), line_num + context)
    return lines[start:end]


def extract_timestamp(line: str) -> Optional[str]:
    patterns = [
        r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})',
        r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})',
        r'(\[\d+\.\d+\])',
    ]
    for p in patterns:
        m = re.search(p, line)
        if m:
            return m.group(1)
    return None


def categorize_log_source(filename: str) -> str:
    """Categorize a log file into ECS-specific component."""
    fl = filename.lower()
    for category, patterns in ECS_LOG_TYPE_PATTERNS.items():
        if any(p in fl for p in patterns):
            return category
    if 'kernel' in fl or 'dmesg' in fl:
        return 'kernel'
    return 'system'


def find_correlations(findings: List[Dict]) -> List[Dict]:
    """Find ECS-specific cross-component correlations."""
    correlations = []
    # Group findings by component
    by_component = {}
    for f in findings:
        comp = categorize_log_source(f.get('file', ''))
        by_component.setdefault(comp, []).append(f)
    # Kernel <-> ECS Agent correlation
    kernel_findings = by_component.get('kernel', []) + by_component.get('dmesg', [])
    ecs_findings = by_component.get('ecs-agent', [])
    if kernel_findings and ecs_findings:
        correlations.append({
            'type': 'kernel-ecs-agent',
            'description': 'Kernel issues detected alongside ECS agent problems - kernel instability may cause agent disconnection',
            'components': ['kernel', 'ecs-agent'],
            'findingIds': [f.get('finding_id') for f in (kernel_findings[:3] + ecs_findings[:3]) if f.get('finding_id')],
            'confidence': 'high',
        })
    # Network <-> Docker correlation
    net_findings = by_component.get('networking', [])
    docker_findings = by_component.get('docker', [])
    if net_findings and docker_findings:
        correlations.append({
            'type': 'network-docker',
            'description': 'Network issues detected alongside Docker problems - network connectivity may affect container operations',
            'components': ['networking', 'docker'],
            'findingIds': [f.get('finding_id') for f in (net_findings[:3] + docker_findings[:3]) if f.get('finding_id')],
            'confidence': 'medium',
        })
    # OOM <-> Task failures
    oom_findings = [f for f in findings if any(k in f.get('description', '').lower() for k in ['oom', 'memory', 'exit code 137'])]
    task_failures = [f for f in findings if any(k in f.get('description', '').lower() for k in ['task', 'container', 'stopped'])]
    if oom_findings and task_failures:
        correlations.append({
            'type': 'oom-task-failure',
            'description': 'OOM kills detected alongside task failures - memory exhaustion likely causing task stops',
            'components': ['cgroups', 'ecs-agent'],
            'findingIds': [f.get('finding_id') for f in (oom_findings[:3] + task_failures[:3]) if f.get('finding_id')],
            'confidence': 'high',
        })
    return correlations


def generate_recommendations(findings: List[Dict]) -> List[Dict]:
    """Generate ECS-specific recommendations based on findings."""
    recs = []
    descs = ' '.join(f.get('description', '') for f in findings).lower()
    if 'oom' in descs or 'memory' in descs or 'exit code 137' in descs:
        recs.append({'priority': 'high', 'category': 'Resource', 'action': 'Increase task memory limits or investigate memory leaks', 'docs': 'https://docs.aws.amazon.com/AmazonECS/latest/developerguide/out-of-memory.html'})
    if 'pull' in descs or 'ecr' in descs or 'image' in descs:
        recs.append({'priority': 'high', 'category': 'Image', 'action': 'Verify ECR permissions, image existence, and VPC endpoint connectivity', 'docs': 'https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_cannot_pull_image.html'})
    if 'agent' in descs and ('disconnect' in descs or 'not connected' in descs):
        recs.append({'priority': 'high', 'category': 'Agent', 'action': 'Check ECS agent logs, verify IAM instance profile, and network connectivity to ECS endpoints'})
    if 'secret' in descs or 'parameter' in descs:
        recs.append({'priority': 'high', 'category': 'Secrets', 'action': 'Verify task execution role has secretsmanager:GetSecretValue and ssm:GetParameters permissions', 'docs': 'https://repost.aws/knowledge-center/ecs-unable-to-pull-secrets'})
    if 'network' in descs or 'eni' in descs or 'subnet' in descs:
        recs.append({'priority': 'high', 'category': 'Network', 'action': 'Check subnet IP availability, security group rules, and VPC endpoint configuration'})
    if 'circuit breaker' in descs or 'deployment' in descs:
        recs.append({'priority': 'medium', 'category': 'Deployment', 'action': 'Review deployment configuration, health check settings, and task definition', 'docs': 'https://repost.aws/knowledge-center/ecs-troubleshoot-deployment-failures'})
    if 'health check' in descs or 'unhealthy' in descs:
        recs.append({'priority': 'medium', 'category': 'Health', 'action': 'Review health check configuration, grace period, and container startup time'})
    if 'disk' in descs or 'space' in descs or 'inode' in descs:
        recs.append({'priority': 'medium', 'category': 'Storage', 'action': 'Clean up unused images/containers, increase EBS volume size'})
    return recs


# ============================================================================
# TRIAGE
# ============================================================================

def perform_ecs_triage(findings: List[Dict]) -> Dict:
    """Perform ECS-specific triage using ECS_TRIAGE_CATEGORIES."""
    matched_categories = {}
    for cat_id, cat_info in ECS_TRIAGE_CATEGORIES.items():
        cat_findings = []
        for f in findings:
            line = f.get('line', '') + ' ' + f.get('description', '')
            for pattern, priority in cat_info['patterns']:
                if re.search(pattern, line, re.IGNORECASE):
                    cat_findings.append({**f, 'triagePriority': priority})
                    break
        if cat_findings:
            matched_categories[cat_id] = {
                'name': cat_info['name'],
                'description': cat_info['description'],
                'logSources': cat_info['log_sources'],
                'findingCount': len(cat_findings),
                'findings': cat_findings[:10],
                'docs': cat_info.get('docs'),
                'runbook': cat_info.get('runbook'),
            }
    # Detect ECS task states
    task_states = {}
    for f in findings:
        line = f.get('line', '')
        for state in ['PENDING', 'PROVISIONING', 'RUNNING', 'STOPPED', 'DEPROVISIONING']:
            if state in line:
                task_states[state] = task_states.get(state, 0) + 1
    # Detect instance conditions
    instance_conditions = {}
    for f in findings:
        line = f.get('line', '')
        for cond in ['ACTIVE', 'DRAINING', 'DISCONNECTED', 'AGENT_DISCONNECTED']:
            if cond in line:
                instance_conditions[cond] = instance_conditions.get(cond, 0) + 1
    return {
        'categories': matched_categories,
        'taskStates': task_states,
        'instanceConditions': instance_conditions,
        'topCategory': max(matched_categories.keys(), key=lambda k: matched_categories[k]['findingCount']) if matched_categories else None,
    }


# ============================================================================
# TEMPORAL CORRELATION
# ============================================================================

def _build_temporal_clusters(findings: List[Dict], window_seconds: int = 60) -> List[Dict]:
    """Group findings into temporal clusters."""
    timestamped = []
    for f in findings:
        ts = extract_timestamp(f.get('line', ''))
        if ts:
            timestamped.append({**f, '_ts': ts})
    if not timestamped:
        return []
    clusters = []
    current_cluster = [timestamped[0]]
    for f in timestamped[1:]:
        current_cluster.append(f)
        if len(current_cluster) >= 20:
            clusters.append({'events': current_cluster, 'count': len(current_cluster)})
            current_cluster = []
    if current_cluster:
        clusters.append({'events': current_cluster, 'count': len(current_cluster)})
    return clusters


def _build_root_cause_chain(findings: List[Dict]) -> List[Dict]:
    """Build ECS-specific causal chains."""
    chains = []
    # ECS causal patterns: kernel issue -> agent disconnect -> task failures
    kernel_issues = [f for f in findings if categorize_log_source(f.get('file', '')) in ('kernel', 'dmesg') and f.get('severity') == 'critical']
    agent_issues = [f for f in findings if 'agent' in f.get('description', '').lower() or 'disconnect' in f.get('description', '').lower()]
    task_issues = [f for f in findings if any(k in f.get('description', '').lower() for k in ['task', 'container', 'stopped', 'failed'])]
    if kernel_issues and agent_issues:
        chains.append({
            'type': 'kernel-cascade',
            'description': 'Kernel instability → ECS Agent disconnect → Task failures',
            'rootCause': kernel_issues[0].get('finding_id'),
            'effects': [f.get('finding_id') for f in agent_issues[:3] + task_issues[:3] if f.get('finding_id')],
            'confidence': 'high',
        })
    # OOM cascade: memory pressure -> OOM kill -> container exit -> task stop
    oom_issues = [f for f in findings if any(k in f.get('description', '').lower() for k in ['oom', 'memory cgroup', 'invoked oom-killer'])]
    exit_137 = [f for f in findings if 'exit code 137' in f.get('description', '').lower()]
    if oom_issues:
        chains.append({
            'type': 'oom-cascade',
            'description': 'Memory pressure → OOM kill → Container exit (137) → Task stop',
            'rootCause': oom_issues[0].get('finding_id'),
            'effects': [f.get('finding_id') for f in exit_137[:3] + task_issues[:3] if f.get('finding_id')],
            'confidence': 'high',
        })
    # Network cascade: ENI/subnet issue -> connection failures -> health check failures
    net_root = [f for f in findings if any(k in f.get('description', '').lower() for k in ['eni', 'subnet', 'network unreachable'])]
    conn_issues = [f for f in findings if any(k in f.get('description', '').lower() for k in ['connection refused', 'timeout', 'dns'])]
    health_issues = [f for f in findings if 'health' in f.get('description', '').lower()]
    if net_root and (conn_issues or health_issues):
        chains.append({
            'type': 'network-cascade',
            'description': 'Network/ENI issue → Connection failures → Health check failures',
            'rootCause': net_root[0].get('finding_id'),
            'effects': [f.get('finding_id') for f in conn_issues[:3] + health_issues[:3] if f.get('finding_id')],
            'confidence': 'medium',
        })
    return chains

# =============================================================================
# LAMBDA HANDLER + TOOL ROUTING
# =============================================================================

def list_sops(arguments: Dict) -> Dict:
    """List all SOPs in the SOP S3 bucket."""
    sop_bucket = os.environ.get('SOP_BUCKET_NAME', '')
    if not sop_bucket:
        return error_response(400, 'SOP_BUCKET_NAME not configured')
    try:
        s3 = boto3.client('s3')
        response = s3.list_objects_v2(Bucket=sop_bucket)
        if 'Contents' not in response:
            return success_response({'sops': [], 'count': 0, 'bucket': sop_bucket})
        sops = [{'name': obj['Key'], 'size': obj['Size'], 'lastModified': obj['LastModified'].isoformat()} for obj in response['Contents']]
        return success_response({'sops': sops, 'count': len(sops), 'bucket': sop_bucket})
    except Exception as e:
        return error_response(500, f'Failed to list SOPs: {str(e)}')


def get_sop(arguments: Dict) -> Dict:
    """Get a specific SOP by name from the SOP S3 bucket."""
    sop_bucket = os.environ.get('SOP_BUCKET_NAME', '')
    if not sop_bucket:
        return error_response(400, 'SOP_BUCKET_NAME not configured')
    sop_name = arguments.get('sopName')
    if not sop_name:
        return error_response(400, 'sopName is required')
    try:
        s3 = boto3.client('s3')
        response = s3.get_object(Bucket=sop_bucket, Key=sop_name)
        content = response['Body'].read().decode('utf-8')
        return success_response({
            'sop': {'name': sop_name, 'content': content, 'size': response['ContentLength'],
                    'lastModified': response['LastModified'].isoformat(),
                    'contentType': response.get('ContentType', 'text/plain')}
        })
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            return error_response(404, f'SOP "{sop_name}" not found. Use list_sops to see available SOPs.')
        return error_response(500, f'Failed to get SOP: {str(e)}')
    except Exception as e:
        return error_response(500, f'Failed to get SOP: {str(e)}')


def lambda_handler(event, context):
    """Main Lambda handler - routes to appropriate tool function."""
    print(f"Received event: {json.dumps(event)}")

    delimiter = "___"
    original_tool_name = context.client_context.custom.get('bedrockAgentCoreToolName', '')

    if delimiter in original_tool_name:
        tool_name = original_tool_name[original_tool_name.index(delimiter) + len(delimiter):]
    else:
        tool_name = original_tool_name

    print(f"Executing tool: {tool_name}")

    tools = {
        # Tier 1: Core Operations
        'collect': start_log_collection,
        'status': get_collection_status,
        'validate': validate_bundle_completeness,
        'errors': get_error_summary,
        'read': read_log_chunk,
        # Tier 2: Advanced Analysis
        'search': search_logs_deep,
        'correlate': correlate_events,
        'artifact': get_artifact_reference,
        'summarize': generate_incident_summary,
        'history': list_collection_history,
        # Tier 3: Cluster-Level Intelligence
        'cluster_health': cluster_health_check,
        'compare_instances': compare_instances,
        'batch_collect': batch_collect,
        'batch_status': batch_status,
        'network_diagnostics': network_diagnostics,
        'tcpdump_capture': tcpdump_capture,
        'tcpdump_analyze': tcpdump_analyze,
        'list_sops': list_sops,
        'get_sop': get_sop,
    }

    if tool_name not in tools:
        return error_response(400, f'Unknown tool: {tool_name}', {
            'available_tools': list(tools.keys())
        })

    try:
        return tools[tool_name](event)
    except Exception as e:
        print(f"Error executing {tool_name}: {str(e)}")
        import traceback
        traceback.print_exc()
        return error_response(500, f'Internal error: {str(e)}')


# =============================================================================
# TIER 1: CORE OPERATIONS
# =============================================================================

def start_log_collection(arguments: Dict) -> Dict:
    """
    Start ECS log collection via AWSSupport-CollectECSInstanceLogs SSM Automation.

    Inputs:
        instanceId: EC2 instance ID (required)
        idempotencyToken: Optional token to prevent duplicate executions
        region: AWS region where the instance runs (optional, auto-detected)

    Returns:
        executionId, estimatedCompletionTime, status, region
    """
    instance_id = arguments.get('instanceId')
    idempotency_token = arguments.get('idempotencyToken')

    if not instance_id:
        return error_response(400, 'instanceId is required')

    if not re.match(r'^i-[0-9a-f]{8,17}$', instance_id):
        return error_response(400, f'Invalid instanceId format: {instance_id}. Expected: i-xxxxxxxxxxxxxxxxx')

    # Resolve and validate region
    target_region, region_error = resolve_and_validate_region(arguments, instance_id)
    if region_error:
        return region_error

    # Validate instance belongs to an ECS cluster
    instance_error = validate_ecs_instance(instance_id, target_region)
    if instance_error:
        return instance_error

    try:
        regional_ssm = get_regional_client('ssm', target_region)
    except Exception as e:
        return error_response(500, f'Failed to create SSM client for region {target_region}: {str(e)}')

    print(f"Starting ECS log collection for {instance_id} in region {target_region}")

    # Verify instance state
    try:
        regional_ec2 = get_regional_client('ec2', target_region)
        desc_resp = regional_ec2.describe_instances(InstanceIds=[instance_id])
        reservations = desc_resp.get('Reservations', [])
        if reservations and reservations[0].get('Instances'):
            state = reservations[0]['Instances'][0].get('State', {}).get('Name', 'unknown')
            if state in ('terminated', 'shutting-down'):
                return error_response(400, f'Instance {instance_id} is {state}. Cannot collect logs from terminated instances.')
            if state == 'stopped':
                return error_response(400, f'Instance {instance_id} is stopped. Start the instance first, then retry.')
    except Exception as e:
        print(f"Warning: Could not verify instance state: {str(e)}")

    # Idempotency check
    if idempotency_token:
        existing = find_execution_by_idempotency_token(instance_id, idempotency_token)
        if existing:
            return success_response({
                'message': 'Returning existing execution (idempotent)',
                'executionId': existing['executionId'],
                'status': existing['status'],
                'instanceId': instance_id,
                'region': target_region,
                'idempotent': True,
            })

    try:
        params = {
            'ECSInstanceId': [instance_id],
            'LogDestination': [LOGS_BUCKET],
            'AutomationAssumeRole': [SSM_AUTOMATION_ROLE_ARN],
        }

        response = regional_ssm.start_automation_execution(
            DocumentName='AWSSupport-CollectECSInstanceLogs',
            Parameters=params,
        )

        execution_id = response['AutomationExecutionId']

        if idempotency_token:
            store_idempotency_mapping(instance_id, idempotency_token, execution_id)

        region_stored = store_execution_region(execution_id, target_region)

        response_data = {
            'message': 'ECS log collection started',
            'executionId': execution_id,
            'instanceId': instance_id,
            'region': target_region,
            's3Bucket': LOGS_BUCKET,
            'estimatedCompletionTime': '3-5 minutes',
            'suggestedPollIntervalSeconds': 15,
            'nextStep': f'Poll status with status(executionId="{execution_id}") every 15 seconds',
            'task': {
                'taskId': execution_id,
                'state': 'running',
                'message': 'Log collection started via SSM Automation',
                'progress': 0,
            },
        }

        if not region_stored and target_region != DEFAULT_REGION:
            response_data['warning'] = (
                f'Region mapping could not be persisted. Pass region="{target_region}" '
                f'explicitly in subsequent status/validate calls.'
            )

        return success_response(response_data)

    except regional_ssm.exceptions.AutomationDefinitionNotFoundException:
        return error_response(404, 'AWSSupport-CollectECSInstanceLogs document not found', {
            'suggestion': f'This SSM document may not be available in region {target_region}. '
                          f'Try us-east-1 or us-west-2.',
            'region': target_region,
        })
    except Exception as e:
        return error_response(500, f'Failed to start log collection in {target_region}: {str(e)}')


def get_collection_status(arguments: Dict) -> Dict:
    """
    Get detailed status of ECS log collection with progress tracking.

    Inputs:
        executionId: SSM Automation execution ID (required)
        includeStepDetails: Include individual step status (default: true)

    Returns:
        status, progress, stepDetails, failureReason (if failed)
    """
    execution_id = arguments.get('executionId')
    include_steps = arguments.get('includeStepDetails', True)

    if not execution_id:
        return error_response(400, 'executionId is required')

    target_region = get_execution_region(execution_id) or arguments.get('region', DEFAULT_REGION)
    try:
        regional_ssm = get_regional_client('ssm', target_region)
    except Exception as e:
        return error_response(500, f'Failed to create SSM client for region {target_region}: {str(e)}')

    try:
        response = regional_ssm.get_automation_execution(AutomationExecutionId=execution_id)
        execution = response['AutomationExecution']
        status = execution['AutomationExecutionStatus']

        result = {
            'executionId': execution_id,
            'status': status,
            'documentName': execution.get('DocumentName', ''),
            'startTime': execution.get('ExecutionStartTime'),
            'endTime': execution.get('ExecutionEndTime'),
        }

        if status == 'Success':
            result['progress'] = 100
        elif status == 'Failed':
            result['progress'] = 0
            result['failureReason'] = parse_failure_reason(execution)
        elif status == 'InProgress':
            result['progress'] = estimate_progress(execution)
        else:
            result['progress'] = 0

        if include_steps and 'StepExecutions' in execution:
            result['stepDetails'] = [
                {
                    'stepName': step.get('StepName'),
                    'status': step.get('StepStatus'),
                    'startTime': step.get('ExecutionStartTime'),
                    'endTime': step.get('ExecutionEndTime'),
                }
                for step in execution.get('StepExecutions', [])
            ]

        if 'Outputs' in execution:
            result['outputs'] = execution['Outputs']

        if status == 'Success':
            result['nextStep'] = f'Validate bundle with validate(executionId="{execution_id}")'
        elif status == 'InProgress':
            result['suggestedPollIntervalSeconds'] = 15
            result['nextStep'] = 'Wait 15 seconds then poll again until status is Success or Failed'
        elif status == 'Failed':
            result['nextStep'] = 'Review failureReason and retry if appropriate'

        SSM_TO_TASK_STATE = {
            'Pending': 'running', 'InProgress': 'running', 'Waiting': 'running',
            'Success': 'completed', 'TimedOut': 'failed', 'Cancelling': 'cancelling',
            'Cancelled': 'cancelled', 'Failed': 'failed',
        }
        result['task'] = {
            'taskId': execution_id,
            'state': SSM_TO_TASK_STATE.get(status, 'running'),
            'message': result.get('failureReason', f'SSM status: {status}'),
            'progress': result.get('progress', 0),
        }

        return success_response({'automation': result})

    except regional_ssm.exceptions.AutomationExecutionNotFoundException:
        return error_response(404, f'Execution {execution_id} not found')
    except Exception as e:
        return error_response(500, f'Failed to get status: {str(e)}')


def validate_bundle_completeness(arguments: Dict) -> Dict:
    """
    Verify all expected files were extracted from ECS log bundle.

    Inputs:
        executionId: SSM execution ID OR
        instanceId: Instance ID to locate bundle

    Returns:
        complete, fileCount, totalSize, missingPatterns, manifest
    """
    execution_id = arguments.get('executionId')
    instance_id = arguments.get('instanceId')

    if not execution_id and not instance_id:
        return error_response(400, 'Either executionId or instanceId is required')

    try:
        if instance_id:
            prefix = f'ecs_{instance_id}'
        else:
            try:
                target_region = get_execution_region(execution_id) or arguments.get('region', DEFAULT_REGION)
                regional_ssm = get_regional_client('ssm', target_region)
            except Exception as e:
                return error_response(500, f'Failed to create SSM client for region: {str(e)}')
            try:
                exec_response = regional_ssm.get_automation_execution(AutomationExecutionId=execution_id)
                params = exec_response['AutomationExecution'].get('Parameters', {})
                instance_id = params.get('ECSInstanceId', [''])[0]
                prefix = f'ecs_{instance_id}'
            except regional_ssm.exceptions.AutomationExecutionNotFoundException:
                return error_response(404, f'Execution {execution_id} not found')
            except Exception as e:
                return error_response(500, f'Failed to get execution details: {str(e)}')

        # Use shared latest-bundle discovery
        bundle_info = find_latest_bundle_files(instance_id)

        if not bundle_info['success']:
            return success_response({
                'complete': False, 'fileCount': 0, 'totalSize': 0, 'totalSizeHuman': '0 B',
                'missingPatterns': ['all'], 'foundPatterns': [], 'hasFindingsIndex': False,
                'instanceId': instance_id, 'manifest': [],
                'warning': bundle_info.get('error', 'Failed to list files'),
                'nextStep': 'Check if log collection completed successfully',
            })

        size_map = {obj['key']: obj for obj in bundle_info['all_objects']}
        all_files = [size_map[k] for k in bundle_info['files'] if k in size_map]

        # Check for manifest.json in latest bundle
        manifest_data = None
        manifest_files = [obj for obj in bundle_info['all_objects']
                         if obj['key'].endswith('manifest.json') and obj['key'].startswith(bundle_info['bundle_prefix'])]
        if manifest_files:
            manifest_files.sort(key=lambda x: x.get('last_modified', ''), reverse=True)
            manifest_read = safe_s3_read(manifest_files[0]['key'])
            if manifest_read['success']:
                try:
                    manifest_data = json.loads(manifest_read['content'])
                except json.JSONDecodeError:
                    manifest_data = None

        if not all_files:
            return success_response({
                'complete': False, 'fileCount': 0, 'totalSize': 0, 'totalSizeHuman': '0 B',
                'missingPatterns': ['all - no extracted logs found'], 'foundPatterns': [],
                'hasFindingsIndex': False, 'instanceId': instance_id, 'manifest': [],
                'info': 'No extracted log files found. Log collection may still be in progress.',
                'nextStep': 'Check log collection status with status',
            })

        total_size = sum(f['size'] for f in all_files)

        # ECS-specific expected patterns (aligned with amazon-ecs-logs-collector.sh output)
        expected_patterns = [
            'ecs', 'docker', 'messages', 'dmesg', 'networking', 'containers',
            'metadata', 'iptables', 'mounts', 'services', 'os-release', 'uname',
            'pkglist', 'ps.txt', 'top.txt', 'open-file', 'cgroup',
        ]
        found_patterns = set()
        for f in all_files:
            key_lower = f['key'].lower()
            for pattern in expected_patterns:
                if pattern in key_lower:
                    found_patterns.add(pattern)

        missing_patterns = list(set(expected_patterns) - found_patterns)
        has_findings_index = any(FINDINGS_INDEX_FILE in f['key'] for f in all_files)
        is_complete = len(all_files) >= 5 and len(found_patterns) >= 3

        result = {
            'complete': is_complete,
            'fileCount': len(all_files),
            'totalSize': total_size,
            'totalSizeHuman': format_bytes(total_size),
            'missingPatterns': missing_patterns,
            'foundPatterns': list(found_patterns),
            'hasFindingsIndex': has_findings_index,
            'instanceId': instance_id,
        }

        if manifest_data and manifest_data.get('version', 1) >= 2:
            result['manifestVersion'] = manifest_data.get('version')
            result['archiveSize'] = manifest_data.get('archiveSize', 0)
            result['archiveSizeHuman'] = format_bytes(manifest_data.get('archiveSize', 0))
            manifest_file_count = manifest_data.get('totalFiles', 0)
            if manifest_file_count > 0 and len(all_files) < manifest_file_count:
                result['warning'] = (
                    f'Manifest reports {manifest_file_count} files but only {len(all_files)} found in S3.'
                )
                result['complete'] = False

        if missing_patterns:
            result['info'] = f'Some log types not found: {", ".join(missing_patterns)}. May be normal depending on instance config.'

        result['manifest'] = [
            {
                'key': f['key'].split('/extracted/')[-1] if '/extracted/' in f['key'] else f['key'],
                'fullKey': f['key'],
                'size': f['size'],
                'sizeHuman': format_bytes(f['size']),
            }
            for f in sorted(all_files, key=lambda x: x['size'], reverse=True)[:50]
        ]

        if is_complete:
            result['nextStep'] = f'Get error summary with errors(instanceId="{instance_id}")'
        else:
            result['nextStep'] = 'Bundle may be incomplete. Check SSM Automation status or proceed with available logs.'

        return success_response(result)

    except Exception as e:
        return success_response({
            'complete': False, 'fileCount': 0, 'totalSize': 0, 'totalSizeHuman': '0 B',
            'missingPatterns': ['unknown'], 'foundPatterns': [], 'hasFindingsIndex': False,
            'instanceId': instance_id or 'unknown', 'manifest': [],
            'error': f'Unexpected error during validation: {str(e)}',
            'nextStep': 'Retry or check AWS console for log collection status',
        })


def get_error_summary(arguments: Dict) -> Dict:
    """
    Get pre-indexed error findings with pagination and baseline support.

    Inputs:
        instanceId: EC2 instance ID (required)
        severity: Filter (critical|high|medium|low|info|all)
        response_format: 'concise' (default) or 'detailed'
        pageSize: Findings per page (default: 50, max: 200)
        pageToken: Opaque token for next page

    Returns:
        findings[], summary counts, coverage_report
    """
    instance_id = arguments.get('instanceId')
    severity_filter = arguments.get('severity', 'all')
    response_format = arguments.get('response_format', 'concise')
    page_size = min(arguments.get('pageSize', 50), 200)
    page_token = arguments.get('pageToken')
    cluster_context = arguments.get('clusterContext')

    if not instance_id:
        return error_response(400, 'instanceId is required')

    page_offset = 0
    if page_token:
        try:
            import base64
            page_offset = int(base64.b64decode(page_token).decode('utf-8'))
        except Exception:
            page_offset = 0

    try:
        prefix = f'ecs_{instance_id}'
        index_key = find_findings_index(prefix)

        if index_key:
            read_result = safe_s3_read(index_key)
            if read_result['success']:
                try:
                    index_data = json.loads(read_result['content'])
                    findings = index_data.get('findings', [])

                    for idx, f in enumerate(findings):
                        if 'finding_id' not in f:
                            f['finding_id'] = assign_finding_id(idx + 1)

                    if cluster_context:
                        findings = annotate_findings_with_baselines(findings, cluster_context)

                    allowed_severities = normalize_severity_filter(severity_filter)
                    if severity_filter != 'all':
                        findings = [f for f in findings if f.get('severity') in allowed_severities]

                    total_findings = len(findings)
                    page_findings = findings[page_offset:page_offset + page_size]
                    has_more = (page_offset + page_size) < total_findings

                    next_token = None
                    if has_more:
                        import base64
                        next_token = base64.b64encode(str(page_offset + page_size).encode('utf-8')).decode('utf-8')

                    summary = index_data.get('summary', {})

                    if response_format == 'concise':
                        page_findings = [
                            {
                                'finding_id': f.get('finding_id'),
                                'severity': f.get('severity'),
                                'pattern': f.get('pattern'),
                                'file': f.get('file'),
                                'count': f.get('count'),
                                **(
                                    {'is_baseline': f.get('is_baseline', False), 'baseline_note': f.get('baseline_note')}
                                    if f.get('is_baseline') else {}
                                ),
                            }
                            for f in page_findings
                        ]

                    coverage_report = {
                        'files_scanned': index_data.get('filesScanned', 0),
                        'files_skipped': index_data.get('filesSkipped', 0),
                        'scan_complete': True,
                        'index_version': index_data.get('index_version', 'v1'),
                    }

                    if cluster_context:
                        update_baselines(cluster_context, findings)

                    return success_response({
                        'instanceId': instance_id,
                        'indexedAt': index_data.get('indexedAt'),
                        'findings': page_findings,
                        'totalFindings': total_findings,
                        'pageSize': page_size,
                        'pageOffset': page_offset,
                        'hasMore': has_more,
                        'nextPageToken': next_token,
                        'summary': summary,
                        'cached': True,
                        'coverage_report': coverage_report,
                        'interpretationGuide': {
                            'CannotPullContainerError': 'ECR auth or image not found. Check IAM role and image URI.',
                            'OOMKilled': 'Container exceeded memory limit. Check task definition memory settings.',
                            'STOPPED (Essential container exited)': 'Essential container crashed. Check container logs.',
                            'AGENT_DISCONNECTED': 'ECS agent lost connection. Check instance networking and agent logs.',
                            'ResourceNotFoundException': 'ECS resource not found. Service/task may have been deleted.',
                        },
                        'nextStep': 'Use search for detailed investigation, or summarize with finding_ids',
                        'recommendedSOPs': match_sops_for_issues([], findings=page_findings),
                    })
                except json.JSONDecodeError:
                    print("Warning: Findings index corrupted, will scan on-demand")

        # Slow path: scan and index on-demand
        result = scan_and_index_errors(instance_id, severity_filter)
        return result

    except Exception as e:
        return success_response({
            'instanceId': instance_id,
            'findings': [],
            'totalFindings': 0,
            'summary': {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0},
            'cached': False,
            'warning': f'Could not retrieve error summary: {str(e)}',
            'nextStep': 'Check if logs exist with validate',
        })


def read_log_chunk(arguments: Dict) -> Dict:
    """
    Byte-range streaming for large log files. NO TRUNCATION. Line-aligned.

    Inputs:
        logKey: S3 key of log file (required)
        startByte: Starting byte offset (default: 0)
        endByte: Ending byte offset (optional, defaults to startByte + 1MB)
        startLine: Starting line number (alternative to byte range)
        lineCount: Number of lines to return (default: 1000)

    Returns:
        content, startByte, endByte, totalSize, hasMore, nextChunkToken
    """
    log_key = arguments.get('logKey')
    start_byte = arguments.get('startByte', 0)
    end_byte = arguments.get('endByte')
    start_line = arguments.get('startLine')
    line_count = arguments.get('lineCount', DEFAULT_LINE_COUNT)

    if not log_key:
        return error_response(400, 'logKey is required')

    try:
        head_result = safe_s3_head(log_key)
        if not head_result['success']:
            return success_response({
                'logKey': log_key, 'content': '', 'startByte': 0, 'endByte': 0,
                'chunkSize': 0, 'totalSize': 0, 'totalSizeHuman': '0 B',
                'hasMore': False, 'nextChunkToken': None, 'truncated': False,
                'fileNotFound': True,
                'warning': head_result.get('error', 'File not found'),
                'suggestion': 'Try listing available logs first.',
            })

        total_size = head_result['size']

        if total_size > MAX_CHUNK_SIZE * 10:
            return get_artifact_reference({'logKey': log_key, 'reason': 'File too large for direct read'})

        # Line-based reading
        if start_line is not None:
            return read_by_lines(LOGS_BUCKET, log_key, start_line, min(line_count, MAX_LINE_COUNT))

        # Byte-range reading
        if end_byte is None:
            end_byte = min(start_byte + DEFAULT_CHUNK_SIZE, total_size)

        start_byte = max(0, start_byte)
        end_byte = min(end_byte, total_size)
        chunk_size = end_byte - start_byte

        if chunk_size > MAX_CHUNK_SIZE:
            end_byte = start_byte + MAX_CHUNK_SIZE
            chunk_size = MAX_CHUNK_SIZE

        if total_size == 0 or chunk_size <= 0:
            return success_response({
                'logKey': log_key, 'content': '', 'startByte': 0, 'endByte': 0,
                'chunkSize': 0, 'totalSize': total_size, 'totalSizeHuman': format_bytes(total_size),
                'hasMore': False, 'nextChunkToken': None, 'truncated': False,
                'info': 'File is empty or requested range is invalid',
            })

        # Line-aligned byte-range reads
        BOUNDARY_SCAN = 4096
        actual_start = max(0, start_byte - 1) if start_byte > 0 else 0
        actual_end = min(end_byte + BOUNDARY_SCAN, total_size)

        range_header = f'bytes={actual_start}-{actual_end - 1}'
        read_result = safe_s3_read(log_key, range_bytes=range_header)

        if not read_result['success']:
            return success_response({
                'logKey': log_key, 'content': '', 'startByte': start_byte, 'endByte': end_byte,
                'chunkSize': 0, 'totalSize': total_size, 'totalSizeHuman': format_bytes(total_size),
                'hasMore': False, 'nextChunkToken': None, 'truncated': False,
                'warning': read_result.get('error', 'Failed to read file content'),
            })

        raw = read_result['content']

        aligned_start = start_byte
        if start_byte > 0:
            first_nl = raw.find('\n')
            if first_nl >= 0:
                aligned_start = actual_start + first_nl + 1
                raw = raw[first_nl + 1:]

        content_end_offset = end_byte - aligned_start
        if content_end_offset < len(raw) and end_byte < total_size:
            nl_pos = raw.find('\n', content_end_offset)
            if nl_pos >= 0:
                raw = raw[:nl_pos + 1]
                aligned_end = aligned_start + nl_pos + 1
            else:
                raw = raw[:content_end_offset]
                aligned_end = end_byte
        else:
            aligned_end = aligned_start + len(raw)

        has_more = aligned_end < total_size

        return success_response({
            'logKey': log_key,
            'content': raw,
            'startByte': aligned_start,
            'endByte': aligned_end,
            'chunkSize': len(raw),
            'totalSize': total_size,
            'totalSizeHuman': format_bytes(total_size),
            'hasMore': has_more,
            'nextChunkToken': str(aligned_end) if has_more else None,
            'truncated': False,
            'lineAligned': True,
        })

    except Exception as e:
        return success_response({
            'logKey': log_key, 'content': '', 'startByte': 0, 'endByte': 0,
            'chunkSize': 0, 'totalSize': 0, 'totalSizeHuman': '0 B',
            'hasMore': False, 'nextChunkToken': None, 'truncated': False,
            'error': f'Unexpected error reading log: {str(e)}',
        })


# =============================================================================
# TIER 2: ADVANCED ANALYSIS
# =============================================================================

def search_logs_deep(arguments: Dict) -> Dict:
    """
    Full-text regex search across all collected logs.

    Inputs:
        instanceId: EC2 instance ID (required)
        query: Regex pattern to search (required)
        logTypes: Comma-separated log types to search (optional)
        maxResults: Max results per file (default: 100)

    Returns:
        matches[], pagination info, coverage_report
    """
    instance_id = arguments.get('instanceId')
    query = arguments.get('query')
    log_types_str = arguments.get('logTypes', '')
    max_results = min(arguments.get('maxResults', 100), 500)

    if not instance_id:
        return error_response(400, 'instanceId is required')
    if not query:
        return error_response(400, 'query is required')
    if len(query) > 500:
        return error_response(400, 'query too long (max 500 characters)')

    try:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return error_response(400, f'Invalid regex pattern: {str(e)}')

        file_patterns = None
        if log_types_str:
            file_patterns = []
            for log_type in log_types_str.split(','):
                log_type = log_type.strip().lower()
                if log_type in ECS_LOG_TYPE_PATTERNS:
                    file_patterns.extend(ECS_LOG_TYPE_PATTERNS[log_type])

        # Use shared latest-bundle discovery
        bundle_info = find_latest_bundle_files(instance_id)

        if not bundle_info['success']:
            return success_response({
                'instanceId': instance_id, 'query': query,
                'filesSearched': 0, 'filesWithMatches': 0, 'totalMatches': 0,
                'results': [], 'truncated': False,
                'warning': bundle_info.get('error', 'Failed to list log files'),
                'nextStep': 'Check if logs exist with validate',
            })

        files_to_search = []
        large_file_count = 0
        size_map = {obj['key']: obj['size'] for obj in bundle_info['all_objects']}
        for key in bundle_info['files']:
            if any(key.endswith(ext) for ext in ['.tar.gz', '.zip', '.gz', '.bin', '.so']):
                continue
            fsize = size_map.get(key, 0)
            if fsize > 52428800:
                large_file_count += 1
                continue
            if file_patterns:
                if not any(p in key.lower() for p in file_patterns):
                    continue
            files_to_search.append({'key': key, 'size': fsize})

        if not files_to_search:
            return success_response({
                'instanceId': instance_id, 'query': query,
                'filesSearched': 0, 'filesWithMatches': 0, 'totalMatches': 0,
                'results': [], 'truncated': False,
                'info': 'No log files found matching criteria.',
                'nextStep': 'Check log collection status or try different log types',
            })

        all_matches = []
        files_searched = 0
        files_with_errors = 0

        for file_info in files_to_search[:50]:
            files_searched += 1
            matches = search_file_for_pattern(LOGS_BUCKET, file_info['key'], pattern, max_results)
            if matches is None:
                files_with_errors += 1
                continue
            if matches:
                filename = file_info['key'].split('/extracted/')[-1]
                all_matches.append({
                    'file': filename, 'fullKey': file_info['key'],
                    'matchCount': len(matches), 'matches': matches,
                })
            if sum(len(m['matches']) for m in all_matches) >= max_results * 3:
                break

        all_matches.sort(key=lambda x: x['matchCount'], reverse=True)

        finding_counter = 0
        for match_group in all_matches:
            finding_counter += 1
            match_group['finding_id'] = f"S-{finding_counter:03d}"

        total_matches_kept = 0
        for match_group in all_matches:
            remaining_budget = max(10, max_results * 3 - total_matches_kept)
            if len(match_group['matches']) > remaining_budget:
                match_group['matches'] = match_group['matches'][:remaining_budget]
                match_group['matchCount'] = len(match_group['matches'])
                match_group['matchesTruncated'] = True
            total_matches_kept += len(match_group['matches'])

        return success_response({
            'instanceId': instance_id,
            'query': query,
            'filesSearched': files_searched,
            'filesWithMatches': len(all_matches),
            'totalMatches': sum(m['matchCount'] for m in all_matches),
            'results': all_matches,
            'truncated': files_searched < len(files_to_search),
            'coverage_report': {
                'files_searched': files_searched,
                'files_available': len(files_to_search),
                'files_skipped_size': large_file_count,
                'files_with_errors': files_with_errors,
                'scan_complete': files_searched >= len(files_to_search),
            },
            'interpretationGuide': {
                'CannotPullContainerError': 'ECR auth failure or image not found. Check task execution role.',
                'OOMKilled': 'Container exceeded memory limit. Check task definition memory.',
                'AGENT_DISCONNECTED': 'ECS agent lost connection. Check instance networking.',
                'connection timed out': 'Network connectivity issue. Check security groups and NACLs.',
            },
            'nextStep': 'Use read to get full context around specific matches',
        })

    except Exception as e:
        return success_response({
            'instanceId': instance_id, 'query': query,
            'filesSearched': 0, 'filesWithMatches': 0, 'totalMatches': 0,
            'results': [], 'truncated': False,
            'error': f'Search encountered an error: {str(e)}',
            'nextStep': 'Check if logs exist with validate',
        })


def correlate_events(arguments: Dict) -> Dict:
    """
    Cross-file timeline correlation for ECS incident analysis.

    Inputs:
        instanceId: EC2 instance ID (required)
        timeWindow: Seconds around pivot event (default: 60)

    Returns:
        timeline[], correlations, temporal_clusters, potential_root_cause_chain
    """
    instance_id = arguments.get('instanceId')
    time_window = arguments.get('timeWindow', 60)

    if not instance_id:
        return error_response(400, 'instanceId is required')

    try:
        prefix = f'ecs_{instance_id}'
        index_key = find_findings_index(prefix)
        findings = []
        files_scanned = 0

        if index_key:
            read_result = safe_s3_read(index_key)
            if read_result['success']:
                try:
                    index_data = json.loads(read_result['content'])
                    findings = index_data.get('findings', [])
                    files_scanned = index_data.get('filesScanned', 0)
                except json.JSONDecodeError:
                    pass

        if not findings:
            error_summary = scan_and_index_errors(instance_id, 'all')
            if error_summary['statusCode'] != 200:
                return success_response({
                    'instanceId': instance_id, 'timeWindow': time_window,
                    'timeline': [], 'byComponent': {}, 'correlations': [],
                    'temporal_clusters': [], 'potential_root_cause_chain': [],
                    'coverage_report': {'files_scanned': 0, 'scan_complete': False},
                    'confidence': 'none', 'gaps': ['Could not retrieve error data'],
                    'nextStep': 'Check if logs exist with validate',
                })
            summary_data = json.loads(error_summary['body'])
            findings = summary_data.get('findings', [])
            files_scanned = summary_data.get('coverage_report', {}).get('files_scanned', 0)

        if not findings:
            return success_response({
                'instanceId': instance_id, 'timeWindow': time_window,
                'timeline': [], 'byComponent': {}, 'correlations': [],
                'temporal_clusters': [], 'potential_root_cause_chain': [],
                'coverage_report': {'files_scanned': files_scanned, 'scan_complete': True},
                'confidence': 'none', 'gaps': [],
                'info': 'No error findings to correlate. Instance may be healthy.',
                'nextStep': 'Use search to search for specific patterns',
            })

        # Build timeline
        timeline = []
        for idx, finding in enumerate(findings):
            timestamp = extract_timestamp(finding.get('sample', ''))
            timeline.append({
                'finding_id': finding.get('finding_id', assign_finding_id(idx + 1)),
                'timestamp': timestamp,
                'source': finding.get('file', 'unknown'),
                'severity': finding.get('severity', 'info'),
                'event': finding.get('pattern', ''),
                'sample': finding.get('sample', '')[:200],
                'count': finding.get('count', 1),
            })

        timeline.sort(key=lambda x: (SEVERITY_ORDER.get(x['severity'], 4), -x['count']))

        by_component = {}
        for event in timeline:
            component = categorize_log_source(event['source'])
            if component not in by_component:
                by_component[component] = []
            by_component[component].append(event)

        temporal_clusters = _build_temporal_clusters(timeline, time_window)
        root_cause_chain = _build_root_cause_chain(findings)

        critical_count = len([e for e in timeline if e['severity'] == 'critical'])
        if critical_count > 0 and len(timeline) >= 3:
            confidence = 'high'
        elif len(timeline) >= 2:
            confidence = 'medium'
        else:
            confidence = 'low'

        gaps = []
        if files_scanned < 10:
            gaps.append('Few files scanned — some log sources may be missing')
        timestamps_present = sum(1 for e in timeline if e.get('timestamp'))
        if timestamps_present < len(timeline) * 0.5:
            gaps.append('Many events lack timestamps — temporal ordering may be unreliable')

        return success_response({
            'instanceId': instance_id,
            'timeWindow': time_window,
            'timeline': timeline[:50],
            'byComponent': by_component,
            'correlations': find_correlations(timeline),
            'temporal_clusters': temporal_clusters,
            'potential_root_cause_chain': root_cause_chain,
            'confidence': confidence,
            'gaps': gaps,
            'coverage_report': {
                'files_scanned': files_scanned,
                'components_found': list(by_component.keys()),
                'events_with_timestamps': timestamps_present,
                'events_total': len(timeline),
                'scan_complete': True,
            },
            'caveat': (
                'Timeline correlation is based on pattern matching across log files. '
                'Timestamps may not be perfectly synchronized across components. '
                'Correlation does not imply causation.'
            ),
            'nextStep': 'Use search to investigate specific events',
            'recommendedSOPs': match_sops_for_issues([], findings=findings),
        })

    except Exception as e:
        return success_response({
            'instanceId': instance_id, 'timeWindow': time_window,
            'timeline': [], 'byComponent': {}, 'correlations': [],
            'temporal_clusters': [], 'potential_root_cause_chain': [],
            'confidence': 'none', 'gaps': [f'Correlation error: {str(e)}'],
            'coverage_report': {'files_scanned': 0, 'scan_complete': False},
            'error': f'Correlation encountered an error: {str(e)}',
            'nextStep': 'Check if logs exist with validate',
        })


def get_artifact_reference(arguments: Dict) -> Dict:
    """
    Get secure presigned URL for large artifacts.

    Inputs:
        logKey: S3 key of artifact (required)
        expirationMinutes: URL expiration (default: 15, max: 60)

    Returns:
        presignedUrl, s3Uri, size
    """
    log_key = arguments.get('logKey')
    expiration_minutes = min(arguments.get('expirationMinutes', 15), 60)

    if not log_key:
        return error_response(400, 'logKey is required')

    try:
        head_result = safe_s3_head(log_key)
        if not head_result['success']:
            return success_response({
                'logKey': log_key, 'presignedUrl': None,
                's3Uri': f's3://{LOGS_BUCKET}/{log_key}',
                'size': 0, 'sizeHuman': '0 B', 'fileNotFound': True,
                'warning': head_result.get('error', 'File not found'),
            })

        try:
            presigned_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': LOGS_BUCKET, 'Key': log_key},
                ExpiresIn=expiration_minutes * 60,
            )
        except Exception as e:
            return success_response({
                'logKey': log_key, 'presignedUrl': None,
                's3Uri': f's3://{LOGS_BUCKET}/{log_key}',
                'size': head_result['size'], 'sizeHuman': format_bytes(head_result['size']),
                'warning': f'Could not generate presigned URL: {str(e)}',
            })

        return success_response({
            'logKey': log_key,
            'presignedUrl': presigned_url,
            's3Uri': f's3://{LOGS_BUCKET}/{log_key}',
            'size': head_result['size'],
            'sizeHuman': format_bytes(head_result['size']),
            'contentType': head_result.get('content_type', 'application/octet-stream'),
            'lastModified': head_result.get('last_modified'),
            'expiresIn': f'{expiration_minutes} minutes',
        })

    except Exception as e:
        return success_response({
            'logKey': log_key, 'presignedUrl': None,
            's3Uri': f's3://{LOGS_BUCKET}/{log_key}',
            'size': 0, 'sizeHuman': '0 B',
            'error': f'Unexpected error: {str(e)}',
        })


def generate_incident_summary(arguments: Dict) -> Dict:
    """
    Generate structured incident summary grounded in finding_ids.

    Inputs:
        instanceId: EC2 instance ID (required)
        finding_ids: List of finding IDs from errors/search (required)
        includeRecommendations: Include remediation suggestions (default: true)
        includeTriage: Include ECS triage analysis (default: true)

    Returns:
        summary with criticalFindings, timeline, recommendations, ecs_triage
    """
    import time as _time
    start_time = _time.time()
    MAX_EXECUTION_TIME = 25

    def check_timeout():
        if _time.time() - start_time > MAX_EXECUTION_TIME:
            raise TimeoutError(f"Execution time exceeded {MAX_EXECUTION_TIME}s")

    instance_id = arguments.get('instanceId')
    finding_ids = arguments.get('finding_ids', [])
    include_recommendations = arguments.get('includeRecommendations', True)
    include_triage = arguments.get('includeTriage', True)

    if not instance_id:
        return error_response(400, 'instanceId is required')
    if not finding_ids:
        return error_response(400,
            'finding_ids is required. Call errors tool first to get finding_ids (F-001 format), '
            'then pass them here to ground the summary in verified evidence.')

    try:
        bundle_data = {}
        try:
            check_timeout()
            bundle_result = validate_bundle_completeness({'instanceId': instance_id})
            if bundle_result['statusCode'] == 200:
                bundle_data = json.loads(bundle_result['body'])
        except TimeoutError:
            raise
        except Exception as e:
            print(f"Warning: Could not get bundle completeness: {str(e)}")

        error_data = {}
        try:
            check_timeout()
            error_result = get_error_summary({'instanceId': instance_id, 'severity': 'all', 'pageSize': 200})
            if error_result['statusCode'] == 200:
                error_data = json.loads(error_result['body'])
        except TimeoutError:
            raise
        except Exception as e:
            print(f"Warning: Could not get error summary: {str(e)}")

        check_timeout()

        all_findings = error_data.get('findings', [])
        summary_counts = error_data.get('summary', {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0})

        finding_id_set = set(finding_ids)
        findings = [f for f in all_findings if f.get('finding_id') in finding_id_set]
        unresolved_ids = finding_id_set - {f.get('finding_id') for f in findings}

        critical_findings = [f for f in findings if f.get('severity') == 'critical'][:10]
        high_findings = [f for f in findings if f.get('severity') == 'high'][:10]
        medium_findings = [f for f in findings if f.get('severity') == 'medium'][:5]

        affected_components = set()
        for finding in findings:
            affected_components.add(categorize_log_source(finding.get('file', '')))

        if len(findings) >= 3 and critical_findings:
            confidence = 'high'
        elif len(findings) >= 1:
            confidence = 'medium'
        else:
            confidence = 'low'

        gaps = []
        if unresolved_ids:
            gaps.append(f'{len(unresolved_ids)} finding_ids could not be resolved: {list(unresolved_ids)[:5]}')

        summary = {
            'instanceId': instance_id,
            'generatedAt': datetime.utcnow().isoformat(),
            'executionTimeMs': int((_time.time() - start_time) * 1000),
            'grounded': True,
            'confidence': confidence,
            'gaps': gaps,
            'bundleStatus': {
                'complete': bundle_data.get('complete', False),
                'fileCount': bundle_data.get('fileCount', 0),
                'totalSize': bundle_data.get('totalSizeHuman', 'unknown'),
            },
            'errorSummary': {
                'critical': summary_counts.get('critical', 0),
                'high': summary_counts.get('high', 0),
                'medium': summary_counts.get('medium', 0),
                'low': summary_counts.get('low', 0),
                'info': summary_counts.get('info', 0),
                'total': len(all_findings),
            },
            'criticalFindings': [
                {'finding_id': f.get('finding_id'), 'file': f.get('file'), 'fullKey': f.get('fullKey'),
                 'pattern': f.get('pattern'), 'count': f.get('count'), 'sample': f.get('sample', '')[:200]}
                for f in critical_findings
            ],
            'highFindings': [
                {'finding_id': f.get('finding_id'), 'file': f.get('file'), 'fullKey': f.get('fullKey'),
                 'pattern': f.get('pattern'), 'count': f.get('count')}
                for f in high_findings
            ],
            'affectedComponents': list(affected_components),
        }

        if not findings:
            summary['info'] = 'No error findings detected. Instance may be healthy.'

        if include_recommendations:
            summary['recommendations'] = generate_recommendations(critical_findings, high_findings, medium_findings)

        summary['artifactLinks'] = []
        for finding in critical_findings[:5]:
            if finding.get('fullKey'):
                summary['artifactLinks'].append({
                    'finding_id': finding.get('finding_id'),
                    'file': finding.get('file'),
                    'key': finding.get('fullKey'),
                    'action': f'read(logKey="{finding.get("fullKey")}")',
                })

        if include_triage and findings:
            try:
                check_timeout()
                triage_result = perform_ecs_triage(instance_id, findings, bundle_data)
                summary['ecs_triage'] = triage_result
            except TimeoutError:
                summary['ecs_triage'] = {'triageVersion': '1.0', 'warning': 'Triage skipped due to time constraints.'}
            except Exception as e:
                summary['ecs_triage'] = {'triageVersion': '1.0', 'error': f'Triage failed: {str(e)}'}
        elif include_triage:
            summary['ecs_triage'] = {
                'triageVersion': '1.0', 'info': 'No findings to triage.',
                'task_states_detected': [], 'instance_conditions_detected': [],
                'most_likely_root_cause': None, 'evidence': [],
            }

        summary['caveat'] = (
            'Root cause analysis is based on log pattern matching only. '
            'Verify findings by checking ECS agent logs, Docker daemon config, '
            'task definitions, and instance networking. '
            'Log patterns indicate symptoms, not always root causes.'
        )

        if summary.get('ecs_triage', {}).get('most_likely_root_cause'):
            root_cause = summary['ecs_triage']['most_likely_root_cause']
            summary['nextStep'] = f"Root cause: {root_cause['category_name']} ({root_cause['confidence']}). Follow remediation steps."
        else:
            summary['nextStep'] = 'Use search for detailed investigation of specific patterns'

        # Auto-match SOPs based on findings and triage category
        triage_cat = None
        if summary.get('ecs_triage', {}).get('most_likely_root_cause'):
            triage_cat = summary['ecs_triage']['most_likely_root_cause'].get('category')
        summary['recommendedSOPs'] = match_sops_for_issues(
            [], findings=findings, triage_category=triage_cat
        )

        summary['executionTimeMs'] = int((_time.time() - start_time) * 1000)
        return success_response(summary)

    except TimeoutError:
        return success_response({
            'instanceId': instance_id, 'generatedAt': datetime.utcnow().isoformat(),
            'grounded': True, 'confidence': 'low',
            'gaps': ['Execution timed out — partial results only'],
            'warning': 'Execution timed out',
            'nextStep': 'Call errors first, then summarize with includeTriage=false',
        })
    except Exception as e:
        return success_response({
            'instanceId': instance_id, 'generatedAt': datetime.utcnow().isoformat(),
            'grounded': True, 'confidence': 'none',
            'gaps': [f'Summary generation failed: {str(e)}'],
            'error': f'Could not generate complete summary: {str(e)}',
            'nextStep': 'Check if logs exist with validate',
        })


def list_collection_history(arguments: Dict) -> Dict:
    """
    List historical ECS log collections for audit and comparison.

    Inputs:
        instanceId: Filter by instance (optional)
        maxResults: Max results (default: 20)
        status: Filter by status (optional)

    Returns:
        collections[], count
    """
    instance_id = arguments.get('instanceId')
    max_results = min(arguments.get('maxResults', 20), 50)
    status_filter = arguments.get('status')
    document_name = 'AWSSupport-CollectECSInstanceLogs'

    try:
        filters = [{'Key': 'DocumentNamePrefix', 'Values': [document_name]}]
        if status_filter:
            filters.append({'Key': 'ExecutionStatus', 'Values': [status_filter]})

        target_region = arguments.get('region', DEFAULT_REGION)
        regions_to_try = [target_region]
        common_regions = ['us-west-2', 'us-east-1', 'eu-west-1', 'ap-southeast-1']
        for r in common_regions:
            if r not in regions_to_try:
                regions_to_try.append(r)

        collections = []
        searched_regions = []

        for region in regions_to_try:
            try:
                regional_ssm = get_regional_client('ssm', region)
                response = regional_ssm.describe_automation_executions(
                    Filters=filters, MaxResults=max_results,
                )
                for exec_meta in response.get('AutomationExecutionMetadataList', []):
                    if instance_id:
                        params = exec_meta.get('Parameters', {})
                        exec_instance = params.get('ECSInstanceId', [''])[0]
                        if instance_id not in exec_instance:
                            continue

                    exec_id = exec_meta['AutomationExecutionId']
                    params = exec_meta.get('Parameters', {})
                    exec_instance = params.get('ECSInstanceId', [''])[0]
                    bundle_exists = False
                    if exec_instance:
                        s3_check = safe_s3_list(f"ecs_{exec_instance}_{exec_id}/", max_keys=1)
                        bundle_exists = bool(s3_check.get('success') and s3_check.get('objects'))

                    collections.append({
                        'executionId': exec_id,
                        'documentName': exec_meta.get('DocumentName', ''),
                        'status': exec_meta['AutomationExecutionStatus'],
                        'startTime': exec_meta.get('ExecutionStartTime'),
                        'endTime': exec_meta.get('ExecutionEndTime'),
                        'instanceId': exec_instance or None,
                        'region': region,
                        'bundleExists': bundle_exists,
                    })

                searched_regions.append(region)
                if collections:
                    break
            except Exception:
                searched_regions.append(f"{region} (error)")
                continue

        return success_response({
            'collections': collections,
            'count': len(collections),
            'searchedRegions': searched_regions,
            'filters': {'instanceId': instance_id, 'status': status_filter, 'documentName': document_name},
        })

    except Exception as e:
        return error_response(500, f'Failed to list history: {str(e)}')


# =============================================================================
# TIER 3: CLUSTER-LEVEL INTELLIGENCE
# =============================================================================

def cluster_health_check(arguments: Dict) -> Dict:
    """
    Comprehensive ECS cluster health overview.
    Enumerates container instances, checks SSM status, flags unhealthy instances.

    Inputs:
        clusterName: ECS cluster name (required)
        region: AWS region (optional)
        includeSSMStatus: Check SSM agent per instance (default: true)

    Returns:
        clusterInfo, instances[], healthSummary
    """
    cluster_name = arguments.get('clusterName')
    if not cluster_name:
        return error_response(400, 'clusterName is required')

    include_ssm = arguments.get('includeSSMStatus', True)
    target_region = resolve_region(arguments)

    try:
        regional_ecs = get_regional_client('ecs', target_region)
        regional_ec2 = get_regional_client('ec2', target_region)
        regional_ssm = get_regional_client('ssm', target_region)

        # 1. Describe the cluster
        try:
            cluster_resp = regional_ecs.describe_clusters(clusters=[cluster_name], include=['STATISTICS', 'SETTINGS'])
            clusters = cluster_resp.get('clusters', [])
            if not clusters:
                return error_response(404, f'Cluster {cluster_name} not found in {target_region}')
            cluster_info = clusters[0]
            cluster_meta = {
                'name': cluster_info.get('clusterName'),
                'status': cluster_info.get('status'),
                'registeredContainerInstancesCount': cluster_info.get('registeredContainerInstancesCount', 0),
                'runningTasksCount': cluster_info.get('runningTasksCount', 0),
                'pendingTasksCount': cluster_info.get('pendingTasksCount', 0),
                'activeServicesCount': cluster_info.get('activeServicesCount', 0),
                'region': target_region,
            }
        except Exception as e:
            return error_response(404, f'Cluster {cluster_name} not found in {target_region}: {str(e)}')

        # 2. List container instances
        container_instance_arns = []
        try:
            paginator = regional_ecs.get_paginator('list_container_instances')
            for page in paginator.paginate(cluster=cluster_name):
                container_instance_arns.extend(page.get('containerInstanceArns', []))
        except Exception as e:
            return error_response(500, f'Failed to list container instances: {str(e)}')

        instances = []
        instance_ids = []

        if container_instance_arns:
            # Describe in batches of 100
            for i in range(0, len(container_instance_arns), 100):
                batch = container_instance_arns[i:i + 100]
                try:
                    desc_resp = regional_ecs.describe_container_instances(
                        cluster=cluster_name, containerInstances=batch,
                    )
                    for ci in desc_resp.get('containerInstances', []):
                        ec2_id = ci.get('ec2InstanceId', '')
                        if ec2_id:
                            instance_ids.append(ec2_id)
                        instances.append({
                            'containerInstanceArn': ci.get('containerInstanceArn'),
                            'ec2InstanceId': ec2_id,
                            'status': ci.get('status'),
                            'agentConnected': ci.get('agentConnected'),
                            'runningTasksCount': ci.get('runningTasksCount', 0),
                            'pendingTasksCount': ci.get('pendingTasksCount', 0),
                            'registeredAt': ci.get('registeredAt'),
                            'agentVersion': ci.get('versionInfo', {}).get('agentVersion'),
                            'dockerVersion': ci.get('versionInfo', {}).get('dockerVersion'),
                            'ssmStatus': None,
                        })
                except Exception:
                    pass

        # 3. Enrich with EC2 metadata
        if instance_ids:
            try:
                ec2_paginator = regional_ec2.get_paginator('describe_instances')
                ec2_map = {}
                for page in ec2_paginator.paginate(InstanceIds=instance_ids):
                    for res in page.get('Reservations', []):
                        for inst in res.get('Instances', []):
                            ec2_map[inst['InstanceId']] = {
                                'instanceType': inst.get('InstanceType'),
                                'availabilityZone': inst.get('Placement', {}).get('AvailabilityZone'),
                                'state': inst.get('State', {}).get('Name'),
                                'privateIp': inst.get('PrivateIpAddress'),
                                'imageId': inst.get('ImageId'),
                                'launchTime': inst.get('LaunchTime'),
                            }
                for inst in instances:
                    ec2_data = ec2_map.get(inst['ec2InstanceId'], {})
                    inst.update(ec2_data)
            except Exception:
                pass

        # 4. Check SSM status
        if include_ssm and instance_ids:
            ssm_status_map = {}
            try:
                for i in range(0, len(instance_ids), 50):
                    chunk = instance_ids[i:i + 50]
                    ssm_paginator = regional_ssm.get_paginator('describe_instance_information')
                    for page in ssm_paginator.paginate(Filters=[{'Key': 'InstanceIds', 'Values': chunk}]):
                        for info in page.get('InstanceInformationList', []):
                            ssm_status_map[info['InstanceId']] = {
                                'pingStatus': info.get('PingStatus'),
                                'agentVersion': info.get('AgentVersion'),
                                'lastPingTime': info.get('LastPingDateTime'),
                            }
            except Exception as e:
                print(f"SSM status check failed: {e}")

            for inst in instances:
                ssm_info = ssm_status_map.get(inst.get('ec2InstanceId'))
                inst['ssmStatus'] = ssm_info or {'pingStatus': 'NotRegistered', 'agentVersion': None}

        # 5. Build health summary
        total = len(instances)
        active = sum(1 for i in instances if i.get('status') == 'ACTIVE')
        agent_connected = sum(1 for i in instances if i.get('agentConnected'))
        ssm_online = sum(1 for i in instances if i.get('ssmStatus', {}).get('pingStatus') == 'Online')

        az_distribution = {}
        for i in instances:
            az = i.get('availabilityZone', 'unknown')
            az_distribution[az] = az_distribution.get(az, 0) + 1

        unhealthy = []
        for i in instances:
            issues = []
            if i.get('status') != 'ACTIVE':
                issues.append(f"ecsStatus={i.get('status')}")
            if not i.get('agentConnected'):
                issues.append('agentDisconnected')
            if include_ssm and i.get('ssmStatus', {}).get('pingStatus') != 'Online':
                issues.append(f"ssm={i.get('ssmStatus', {}).get('pingStatus', 'unknown')}")
            if issues:
                unhealthy.append({'instanceId': i.get('ec2InstanceId'), 'issues': issues})

        health_summary = {
            'totalInstances': total,
            'active': active,
            'agentConnected': agent_connected,
            'ssmOnline': ssm_online,
            'unhealthyCount': len(unhealthy),
            'azDistribution': az_distribution,
        }

        gaps = []
        if not include_ssm:
            gaps.append('SSM status not checked')
        if total == 0:
            gaps.append('No container instances found — cluster may be empty or Fargate-only')

        if total > 0 and include_ssm and ssm_online == total:
            confidence = 'high'
        elif total > 0:
            confidence = 'medium'
        else:
            confidence = 'none'

        return success_response({
            'cluster': cluster_meta,
            'instances': instances,
            'unhealthyInstances': unhealthy,
            'healthSummary': health_summary,
            'region': target_region,
            'confidence': confidence,
            'gaps': gaps,
            'nextStep': 'Use compare_instances to diff specific instances, or batch_collect to sample unhealthy ones' if unhealthy else 'Cluster looks healthy.',
        })

    except Exception as e:
        return error_response(500, f'cluster_health failed: {str(e)}')


def compare_instances(arguments: Dict) -> Dict:
    """
    Diff error findings between two or more ECS container instances.

    Inputs:
        instanceIds: list of 2+ instance IDs (required)
        compareFields: "errors", "config", "all" (default: "all")

    Returns:
        commonFindings[], uniqueFindings{}, comparisonMatrix
    """
    instance_ids = arguments.get('instanceIds', [])
    if not instance_ids or len(instance_ids) < 2:
        return error_response(400, 'instanceIds must contain at least 2 instance IDs')

    seen = set()
    deduped = []
    for iid in instance_ids:
        if iid not in seen:
            seen.add(iid)
            deduped.append(iid)
    instance_ids = deduped
    if len(instance_ids) < 2:
        return error_response(400, 'instanceIds must contain at least 2 distinct instance IDs')
    if len(instance_ids) > 10:
        return error_response(400, 'Maximum 10 instances for comparison')

    compare_fields = arguments.get('compareFields', 'all')

    try:
        node_findings = {}
        node_configs = {}

        def _gather_instance_data(iid):
            nf = []
            nc = {}
            if compare_fields in ('errors', 'all'):
                prefix = f"ecs_{iid}"
                try:
                    idx = find_findings_index(prefix)
                    if idx:
                        resp = s3_client.get_object(Bucket=LOGS_BUCKET, Key=idx)
                        findings_data = json.loads(resp['Body'].read().decode('utf-8'))
                        nf = findings_data.get('findings', [])
                    else:
                        nf = [{'error': f'No findings index for {iid}. Run collect first.', 'needsCollection': True}]
                except Exception as e:
                    nf = [{'error': f'Could not load findings: {str(e)}'}]

            if compare_fields in ('config', 'all'):
                bundle_info = find_latest_bundle_files(iid)
                extracted_prefix = None
                if bundle_info['success']:
                    extracted_prefix = bundle_info['bundle_prefix'] + '/extracted/'
                if extracted_prefix:
                    config_files = [
                        ('ecs_config', f"{extracted_prefix}ecs.config"),
                        ('docker_daemon', f"{extracted_prefix}docker-daemon.json"),
                    ]
                else:
                    config_files = []
                for config_name, config_key in config_files:
                    result = safe_s3_read(config_key, max_size=65536)
                    nc[config_name] = result['content'][:2000] if result.get('success') else None
            return iid, nf, nc

        with ThreadPoolExecutor(max_workers=min(len(instance_ids), 10)) as executor:
            futures = {executor.submit(_gather_instance_data, iid): iid for iid in instance_ids}
            for future in as_completed(futures):
                iid_key = futures[future]
                try:
                    iid, nf, nc = future.result()
                    node_findings[iid] = nf
                    node_configs[iid] = nc
                except Exception as e:
                    node_findings[iid_key] = [{'error': f'Failed: {str(e)}'}]
                    node_configs[iid_key] = {}

        # Build comparison
        common_findings = []
        unique_findings = {}

        if compare_fields in ('errors', 'all') and node_findings:
            def finding_signature(f):
                if 'error' in f and 'severity' not in f:
                    return f"__error__{f.get('error', 'unknown')[:80]}"
                return f"{f.get('severity', '')}__{f.get('category', '')}__{f.get('pattern', f.get('message', ''))[:80]}"

            sig_to_nodes = {}
            for iid, findings in node_findings.items():
                unique_findings[iid] = []
                for f in findings:
                    sig = finding_signature(f)
                    if sig not in sig_to_nodes:
                        sig_to_nodes[sig] = {'finding': f, 'nodes': []}
                    sig_to_nodes[sig]['nodes'].append(iid)

            for sig, data in sig_to_nodes.items():
                if len(data['nodes']) == len(instance_ids):
                    common_findings.append({**data['finding'], 'presentOnAllInstances': True})
                else:
                    for iid in data['nodes']:
                        unique_findings[iid].append({**data['finding'], 'uniqueTo': iid})

        # Config diff
        config_diffs = {}
        if compare_fields in ('config', 'all') and node_configs:
            ref_id = instance_ids[0]
            ref_config = node_configs.get(ref_id, {})
            for iid in instance_ids[1:]:
                other_config = node_configs.get(iid, {})
                diffs = []
                all_keys = set(list(ref_config.keys()) + list(other_config.keys()))
                for key in all_keys:
                    if ref_config.get(key) != other_config.get(key):
                        diffs.append({
                            'configFile': key, 'referenceInstance': ref_id,
                            'comparedInstance': iid, 'match': False,
                            'note': 'Content differs' if (ref_config.get(key) and other_config.get(key)) else 'Missing on one instance',
                        })
                config_diffs[f"{ref_id}_vs_{iid}"] = diffs if diffs else [{'match': True, 'note': 'Configs identical'}]

        matrix = []
        for iid in instance_ids:
            total_f = len(node_findings.get(iid, []))
            unique_count = len(unique_findings.get(iid, []))
            critical_count = sum(1 for f in node_findings.get(iid, []) if f.get('severity') == 'critical')
            matrix.append({
                'instanceId': iid, 'totalFindings': total_f,
                'criticalFindings': critical_count, 'uniqueFindings': unique_count,
                'commonFindings': total_f - unique_count,
            })

        common_count = len(common_findings)
        total_unique = sum(len(v) for v in unique_findings.values())
        if common_count > 0 and total_unique == 0:
            insight = f"All {len(instance_ids)} instances share the same {common_count} findings. Likely a cluster-wide issue."
        elif common_count == 0 and total_unique > 0:
            insight = "No common findings. Each instance has unique issues — investigate individually."
        elif common_count > total_unique:
            insight = f"{common_count} common vs {total_unique} unique. Mostly a shared problem."
        else:
            insight = f"{common_count} common, {total_unique} unique. Mixed picture."

        return success_response({
            'comparedInstances': instance_ids,
            'commonFindings': common_findings,
            'commonFindingsCount': common_count,
            'uniqueFindings': unique_findings,
            'configDiffs': config_diffs,
            'comparisonMatrix': matrix,
            'insight': insight,
            'caveat': 'Comparison is based on pre-indexed error findings. Differences may reflect different workloads.',
            'nextStep': 'Common findings suggest cluster-wide issue. Unique findings point to instance-specific problems.',
        })

    except Exception as e:
        return error_response(500, f'compare_instances failed: {str(e)}')


def batch_collect(arguments: Dict) -> Dict:
    """
    Smart batch log collection with statistical sampling for ECS clusters.

    Inputs:
        clusterName: ECS cluster name (required)
        region: AWS region (optional)
        filter: "all", "unhealthy", "disconnected" (default: "unhealthy")
        strategy: "sample" or "all" (default: "sample")
        samplesPerBucket: instances per bucket (default: 3, max: 5)
        maxTotalCollections: hard cap (default: 15, max: 15)
        dryRun: preview only (default: false)

    Returns:
        buckets[], plannedCollections, executions[] (if not dryRun)
    """
    cluster_name = arguments.get('clusterName')
    if not cluster_name:
        return error_response(400, 'clusterName is required')

    target_region = resolve_region(arguments)
    node_filter = arguments.get('filter', 'unhealthy')
    valid_filters = ('all', 'unhealthy', 'disconnected')
    if node_filter not in valid_filters:
        return error_response(400, f"Invalid filter '{node_filter}'. Must be one of: {', '.join(valid_filters)}")
    strategy = arguments.get('strategy', 'sample')
    samples_per_bucket = min(arguments.get('samplesPerBucket', 3), 5)
    max_total = min(arguments.get('maxTotalCollections', 15), 15)
    dry_run = arguments.get('dryRun', False)

    try:
        regional_ecs = get_regional_client('ecs', target_region)
        regional_ec2 = get_regional_client('ec2', target_region)
        regional_ssm = get_regional_client('ssm', target_region)

        # 1. List container instances
        ci_arns = []
        paginator = regional_ecs.get_paginator('list_container_instances')
        for page in paginator.paginate(cluster=cluster_name):
            ci_arns.extend(page.get('containerInstanceArns', []))

        if not ci_arns:
            return error_response(404, f'No container instances found for cluster {cluster_name}')

        # 2. Describe container instances
        all_instances = []
        for i in range(0, len(ci_arns), 100):
            batch = ci_arns[i:i + 100]
            desc_resp = regional_ecs.describe_container_instances(cluster=cluster_name, containerInstances=batch)
            for ci in desc_resp.get('containerInstances', []):
                ec2_id = ci.get('ec2InstanceId', '')
                all_instances.append({
                    'instanceId': ec2_id,
                    'status': ci.get('status'),
                    'agentConnected': ci.get('agentConnected'),
                    'runningTasks': ci.get('runningTasksCount', 0),
                })

        # 3. Check SSM status
        instance_ids = [i['instanceId'] for i in all_instances if i['instanceId']]
        ssm_status = {}
        if instance_ids:
            try:
                for i in range(0, len(instance_ids), 50):
                    chunk = instance_ids[i:i + 50]
                    ssm_paginator = regional_ssm.get_paginator('describe_instance_information')
                    for page in ssm_paginator.paginate(Filters=[{'Key': 'InstanceIds', 'Values': chunk}]):
                        for info in page.get('InstanceInformationList', []):
                            ssm_status[info['InstanceId']] = info.get('PingStatus', 'Unknown')
            except Exception:
                pass

        # 4. Apply filter
        filtered = []
        for inst in all_instances:
            ssm_ping = ssm_status.get(inst['instanceId'], 'NotRegistered')
            inst['ssmPingStatus'] = ssm_ping
            is_unhealthy = inst['status'] != 'ACTIVE' or not inst['agentConnected'] or ssm_ping != 'Online'
            is_disconnected = not inst['agentConnected'] or ssm_ping != 'Online'

            if node_filter == 'all':
                filtered.append(inst)
            elif node_filter == 'unhealthy' and is_unhealthy:
                filtered.append(inst)
            elif node_filter == 'disconnected' and is_disconnected:
                filtered.append(inst)

        if not filtered and node_filter in ('unhealthy', 'disconnected'):
            return success_response({
                'message': f'No {node_filter} instances found — cluster looks healthy',
                'totalInstances': len(all_instances), 'filteredInstances': 0,
                'filter': node_filter, 'buckets': [], 'plannedCollections': 0,
            })

        # 5. Group into buckets by status + SSM
        buckets = {}
        for inst in filtered:
            key = f"{inst['status']}|{inst['ssmPingStatus']}"
            if key not in buckets:
                buckets[key] = {'signature': key, 'nodes': [], 'count': 0}
            buckets[key]['nodes'].append(inst)
            buckets[key]['count'] += 1

        # 6. Select samples
        bucket_list = []
        total_planned = 0
        for sig, bucket in buckets.items():
            sample_count = min(samples_per_bucket, bucket['count']) if strategy == 'sample' else bucket['count']
            if total_planned + sample_count > max_total:
                sample_count = max(0, max_total - total_planned)
            sample_nodes = bucket['nodes'][:sample_count]
            total_planned += len(sample_nodes)
            bucket_list.append({
                'signature': sig, 'totalInstances': bucket['count'],
                'sampleCount': len(sample_nodes),
                'sampleInstances': [n['instanceId'] for n in sample_nodes],
            })

        if dry_run:
            return success_response({
                'dryRun': True, 'clusterName': cluster_name, 'region': target_region,
                'totalInstances': len(all_instances), 'filteredInstances': len(filtered),
                'filter': node_filter, 'strategy': strategy,
                'bucketCount': len(bucket_list), 'buckets': bucket_list,
                'plannedCollections': total_planned,
                'message': f'{len(filtered)} instances grouped into {len(bucket_list)} buckets. Will collect from {total_planned}. Re-run with dryRun=false to proceed.',
            })

        # 7. Execute collections
        batch_id = hashlib.md5(f"{cluster_name}-{datetime.utcnow().isoformat()}".encode()).hexdigest()[:12]
        executions = []

        for bucket in bucket_list:
            for iid in bucket['sampleInstances']:
                try:
                    collect_args = {
                        'instanceId': iid, 'region': target_region,
                        'idempotencyToken': f"batch-{batch_id}-{iid}",
                    }
                    result = start_log_collection(collect_args)
                    result_body = json.loads(result.get('body', '{}'))
                    executions.append({
                        'instanceId': iid, 'bucket': bucket['signature'],
                        'executionId': result_body.get('executionId'),
                        'status': 'Started' if result_body.get('success') else 'Failed',
                        'error': result_body.get('error'),
                    })
                except Exception as e:
                    executions.append({'instanceId': iid, 'bucket': bucket['signature'], 'status': 'Failed', 'error': str(e)})

        # Store batch metadata
        try:
            s3_client.put_object(
                Bucket=LOGS_BUCKET, Key=f"batches/{batch_id}/metadata.json",
                Body=json.dumps({'batchId': batch_id, 'clusterName': cluster_name, 'region': target_region,
                                  'createdAt': datetime.utcnow().isoformat(), 'executions': executions, 'buckets': bucket_list}, default=str),
                ContentType='application/json',
            )
        except Exception:
            pass

        started = sum(1 for e in executions if e['status'] == 'Started')
        failed = sum(1 for e in executions if e['status'] == 'Failed')

        return success_response({
            'batchId': batch_id, 'clusterName': cluster_name, 'region': target_region,
            'totalInstances': len(all_instances), 'filteredInstances': len(filtered),
            'bucketCount': len(bucket_list), 'buckets': bucket_list,
            'executions': executions, 'collectionsStarted': started, 'collectionsFailed': failed,
            'task': {
                'taskId': batch_id, 'state': 'running' if started > 0 else 'failed',
                'message': f'{started} collections started, {failed} failed', 'progress': 0,
            },
            'nextStep': f'Use batch_status(batchId="{batch_id}") to poll all collections.',
        })

    except Exception as e:
        return error_response(500, f'batch_collect failed: {str(e)}')


def batch_status(arguments: Dict) -> Dict:
    """
    Poll status of multiple log collections at once.
    Returns consolidated view with allComplete flag.

    Inputs:
        executionIds: list of SSM execution IDs (required if no batchId)
        batchId: batch ID from batch_collect (alternative to executionIds)

    Returns:
        allComplete, summary counts, per-execution status
    """
    execution_ids = arguments.get('executionIds', [])
    batch_id = arguments.get('batchId')

    # If batchId provided, load execution IDs from stored metadata
    if batch_id and not execution_ids:
        try:
            meta_result = safe_s3_read(f"batches/{batch_id}/metadata.json")
            if meta_result.get('success'):
                meta = json.loads(meta_result['content'])
                execution_ids = [
                    e['executionId'] for e in meta.get('executions', [])
                    if e.get('executionId')
                ]
        except Exception:
            pass

    if not execution_ids:
        return error_response(400, 'executionIds list or batchId is required')

    # Deduplicate
    execution_ids = list(dict.fromkeys(execution_ids))

    # Poll all executions in parallel
    results = []

    def _poll(eid):
        try:
            target_region = get_execution_region(eid) or DEFAULT_REGION
            regional_ssm = get_regional_client('ssm', target_region)
            resp = regional_ssm.get_automation_execution(AutomationExecutionId=eid)
            execution = resp['AutomationExecution']
            status = execution['AutomationExecutionStatus']
            params = execution.get('Parameters', {})
            instance_id = params.get('ECSInstanceId', [None])[0] if params.get('ECSInstanceId') else None
            return {
                'executionId': eid,
                'instanceId': instance_id,
                'status': status,
                'progress': 100 if status == 'Success' else (0 if status == 'Failed' else estimate_progress(execution)),
                'failureReason': parse_failure_reason(execution) if status == 'Failed' else None,
            }
        except Exception as e:
            return {
                'executionId': eid,
                'instanceId': None,
                'status': 'Unknown',
                'progress': 0,
                'error': str(e),
            }

    with ThreadPoolExecutor(max_workers=min(len(execution_ids), 15)) as executor:
        results = list(executor.map(_poll, execution_ids))

    # Compute summary
    succeeded = [r for r in results if r['status'] == 'Success']
    failed = [r for r in results if r['status'] == 'Failed']
    in_progress = [r for r in results if r['status'] in ('InProgress', 'Pending', 'Waiting')]
    unknown = [r for r in results if r['status'] not in ('Success', 'Failed', 'InProgress', 'Pending', 'Waiting')]

    all_complete = len(in_progress) == 0 and len(unknown) == 0

    response_data = {
        'allComplete': all_complete,
        'summary': {
            'total': len(results),
            'succeeded': len(succeeded),
            'failed': len(failed),
            'inProgress': len(in_progress),
            'unknown': len(unknown),
        },
        'executions': results,
    }

    if all_complete:
        ready_instances = [r['instanceId'] for r in succeeded if r['instanceId']]
        failed_instances = [r['instanceId'] for r in failed if r['instanceId']]
        response_data['nextStep'] = (
            f"All collections complete. {len(succeeded)} succeeded, {len(failed)} failed. "
            f"Use errors/search/network_diagnostics on succeeded instances: {ready_instances[:5]}."
        )
        if failed_instances:
            response_data['failedInstances'] = failed_instances
    else:
        response_data['nextStep'] = f'{len(in_progress)} still running. Poll again in 15 seconds.'
        response_data['suggestedPollIntervalSeconds'] = 15

    return success_response(response_data)


def network_diagnostics(arguments: Dict) -> Dict:
    """
    Extract and structure networking info from collected ECS log bundles.
    Parses iptables, docker networking, routes, DNS, ENI, and security groups.

    Inputs:
        instanceId: EC2 instance ID (required)
        sections: comma-separated: "iptables,docker,routes,dns,eni,security-groups" or "all" (default: "all")

    Returns:
        Structured networking diagnostics per section
    """
    instance_id = arguments.get('instanceId')
    if not instance_id:
        return error_response(400, 'instanceId is required')

    sections_str = arguments.get('sections', 'all')
    valid_sections = {'iptables', 'docker', 'routes', 'dns', 'eni', 'security-groups'}
    if sections_str == 'all':
        sections = ['iptables', 'docker', 'routes', 'dns', 'eni', 'security-groups']
    else:
        sections = [s.strip() for s in sections_str.split(',')]
        invalid = [s for s in sections if s not in valid_sections]
        if invalid:
            return error_response(400, f"Invalid section(s): {', '.join(invalid)}. Valid: {', '.join(sorted(valid_sections))}")
        if not sections:
            return error_response(400, 'At least one section is required')

    prefix = f"logs/{instance_id}/extracted/"
    results = {}
    issues_found = []

    try:
        # Use shared latest-bundle discovery
        bundle_info = find_latest_bundle_files(instance_id)
        if not bundle_info['success']:
            return error_response(404, bundle_info.get('error', f'No extracted log bundle found for {instance_id}. Run collect first.'))

        bundle_files = bundle_info['files']
        bundle_age_minutes = bundle_info['bundle_age_minutes']
        bundle_collected_at = bundle_info['bundle_collected_at']

        # HARD BLOCK: Do NOT analyze stale bundles — force fresh collection
        STALE_THRESHOLD_MINUTES = 15
        if bundle_age_minutes is not None and bundle_age_minutes > STALE_THRESHOLD_MINUTES:
            return error_response(409, (
                f'STALE BUNDLE: The log bundle for {instance_id} is {bundle_age_minutes} minutes old '
                f'(collected at {bundle_collected_at}). The instance state has likely changed since then. '
                f'You MUST run the collect tool first to gather fresh logs, wait for it to complete '
                f'(poll status until success), then call network_diagnostics again. '
                f'Do NOT draw conclusions from stale data.'
            ), {
                'bundleInfo': {
                    'collectedAt': bundle_collected_at,
                    'ageMinutes': bundle_age_minutes,
                    'isStale': True,
                    'staleThresholdMinutes': STALE_THRESHOLD_MINUTES,
                },
                'action': 'Run collect tool, poll status until complete, then retry network_diagnostics',
            })

        def find_files(patterns):
            """Find bundle files matching any of the given patterns."""
            matched = []
            for f in bundle_files:
                fname = f.lower()
                for p in patterns:
                    if p in fname:
                        matched.append(f)
                        break
            return matched

        # Pre-fetch all needed files in parallel
        files_to_fetch = set()
        section_file_map = {}
        fetch_sizes = {}

        if 'iptables' in sections:
            keys = find_files(['iptables', 'ip-tables', 'iptable'])[:6]
            section_file_map['iptables'] = keys
            for k in keys: files_to_fetch.add(k); fetch_sizes[k] = 262144
        if 'docker' in sections:
            keys = find_files(['docker', 'containerd', 'bridge', 'docker-network', 'brctlshow', 'veth'])[:5]
            section_file_map['docker'] = keys
            for k in keys: files_to_fetch.add(k); fetch_sizes[k] = 262144
        if 'routes' in sections:
            r_keys = find_files(['ip-route', 'ip_route', 'route-table', 'routes'])[:3]
            i_keys = find_files(['ifconfig', 'ip-addr', 'ip_addr', 'interfaces', 'ipaddrshow'])[:2]
            section_file_map['routes'] = r_keys
            section_file_map['routes_iface'] = i_keys
            for k in r_keys + i_keys: files_to_fetch.add(k); fetch_sizes[k] = 262144
        if 'dns' in sections:
            keys = find_files(['resolv', 'dns'])[:5]
            section_file_map['dns'] = keys
            for k in keys: files_to_fetch.add(k); fetch_sizes[k] = 262144
        if 'eni' in sections:
            keys = find_files(['eni', 'network-interface', 'eth'])[:3]
            section_file_map['eni'] = keys
            for k in keys: files_to_fetch.add(k); fetch_sizes[k] = 32768
        if 'security-groups' in sections:
            section_file_map['security-groups'] = []  # Fetched via API

        # Parallel S3 reads
        file_contents = {}
        def _fetch(key):
            r = safe_s3_read(key, max_size=fetch_sizes.get(key, 262144))
            return key, r.get('content', '') if r.get('success') else None

        with ThreadPoolExecutor(max_workers=10) as executor:
            fetch_list = list(files_to_fetch)
            for key, content in executor.map(_fetch, fetch_list):
                file_contents[key] = content

        def read_file_content(key, max_size=262144):
            cached = file_contents.get(key)
            if cached is not None:
                return cached
            r = safe_s3_read(key, max_size=max_size)
            return r.get('content', '') if r.get('success') else None

        # =================================================================
        # IPTABLES
        # =================================================================
        if 'iptables' in sections:
            ipt_data = {'chainCount': 0, 'ruleCount': 0, 'natRules': [], 'dockerRules': [], 'issues': []}

            # BUG FIX: Parse ALL iptables files and merge results.
            # Docker DNAT/MASQUERADE rules live in the NAT table, not the filter table.
            # Previously we broke after the first file (usually iptables-filter.txt),
            # missing NAT table rules entirely.
            ipt_files = find_files(['iptables', 'ip-tables', 'iptable'])

            all_lines_merged = []
            all_nat_rules = []
            all_docker_rules = []
            total_rule_count = 0
            total_chain_count = 0
            source_files = []

            # Prefer iptables-save.txt if available
            save_file = None
            for f in ipt_files:
                fl = f.lower()
                if 'iptables-save' in fl or 'iptables_save' in fl:
                    save_file = f
                    break

            files_to_parse = [save_file] if save_file else ipt_files[:6]

            for f in files_to_parse:
                content = read_file_content(f)
                if not content:
                    continue
                lines = content.splitlines()
                source_files.append(f)
                all_lines_merged.extend(lines)

                total_rule_count += sum(1 for l in lines if l.strip() and not l.startswith('#') and not l.startswith('*') and not l.startswith(':'))
                total_chain_count += sum(1 for l in lines if l.startswith(':'))
                all_nat_rules.extend(l.strip() for l in lines if 'DNAT' in l or 'SNAT' in l or 'MASQUERADE' in l)
                all_docker_rules.extend(l.strip() for l in lines if 'DOCKER' in l)

            ipt_data['ruleCount'] = total_rule_count
            ipt_data['chainCount'] = total_chain_count
            ipt_data['natRules'] = all_nat_rules[:20]
            ipt_data['dockerRules'] = all_docker_rules[:20]
            ipt_data['sourceFiles'] = source_files
            ipt_data['sourceFile'] = source_files[0] if source_files else None

            if all_lines_merged:
                if total_rule_count == 0:
                    ipt_data['issues'].append('No iptables rules found — Docker networking may be broken')
                    issues_found.append({'section': 'iptables', 'severity': 'critical', 'message': 'No iptables rules found'})
                if not any('DOCKER' in l for l in all_lines_merged):
                    ipt_data['issues'].append('DOCKER chain missing — Docker bridge networking not configured')
                    issues_found.append({'section': 'iptables', 'severity': 'warning', 'message': 'DOCKER chain missing'})

            results['iptables'] = ipt_data

        # =================================================================
        # DOCKER NETWORKING
        # =================================================================
        if 'docker' in sections:
            docker_data = {'networkMode': None, 'bridgeConfig': {}, 'errors': [], 'issues': []}
            docker_files = find_files(['docker', 'containerd', 'bridge', 'docker-network'])
            for f in docker_files[:5]:
                content = read_file_content(f)
                if content:
                    # Parse docker daemon config
                    if f.endswith('.json') or 'daemon' in f.lower():
                        try:
                            cfg = json.loads(content)
                            docker_data['bridgeConfig'] = cfg
                            if 'bridge' in cfg:
                                docker_data['networkMode'] = 'bridge'
                            if cfg.get('iptables') is False:
                                docker_data['issues'].append('Docker iptables disabled — container networking may fail')
                                issues_found.append({'section': 'docker', 'severity': 'critical', 'message': 'Docker iptables disabled'})
                        except json.JSONDecodeError:
                            pass
                    # Parse docker logs for network errors
                    for line in content.split('\n'):
                        ll = line.lower()
                        if ('error' in ll or 'failed' in ll) and ('network' in ll or 'bridge' in ll or 'eni' in ll):
                            docker_data['errors'].append(line.strip()[:200])
            if docker_data['errors']:
                docker_data['issues'].append(f"{len(docker_data['errors'])} Docker networking errors found")
                issues_found.append({'section': 'docker', 'severity': 'warning', 'message': f"{len(docker_data['errors'])} Docker network errors"})
            docker_data['errors'] = docker_data['errors'][:20]
            results['docker'] = docker_data

        # =================================================================
        # ROUTE TABLES
        # =================================================================
        if 'routes' in sections:
            route_data = {'routes': [], 'defaultGateway': None, 'interfaces': [], 'issues': []}
            route_files = find_files(['ip-route', 'ip_route', 'route-table', 'routes'])
            for f in route_files[:3]:
                content = read_file_content(f)
                if content:
                    for line in content.split('\n'):
                        line = line.strip()
                        if not line:
                            continue
                        route_data['routes'].append(line)
                        if line.startswith('default') or 'default' in line:
                            route_data['defaultGateway'] = line
            # Parse interfaces
            iface_files = find_files(['ifconfig', 'ip-addr', 'ip_addr', 'interfaces'])
            for f in iface_files[:2]:
                content = read_file_content(f)
                if content:
                    current_iface = None
                    for line in content.split('\n'):
                        if re.match(r'^\d+:\s+\S+', line) or re.match(r'^\S+:', line):
                            iface_match = re.search(r'(\S+?)[@:]', line)
                            if iface_match:
                                current_iface = iface_match.group(1)
                        if 'inet ' in line and current_iface:
                            ip_match = re.search(r'inet\s+(\S+)', line)
                            if ip_match:
                                route_data['interfaces'].append({'name': current_iface, 'ip': ip_match.group(1)})
            if not route_data['defaultGateway']:
                route_data['issues'].append('No default gateway found')
                issues_found.append({'section': 'routes', 'severity': 'critical', 'message': 'No default gateway'})
            route_data['routeCount'] = len(route_data['routes'])
            route_data['routes'] = route_data['routes'][:50]
            results['routes'] = route_data

        # =================================================================
        # DNS
        # =================================================================
        if 'dns' in sections:
            dns_data = {'resolv_conf': {}, 'nameservers': [], 'searchDomains': [], 'issues': []}
            dns_files = find_files(['resolv', 'dns'])
            for f in dns_files[:5]:
                content = read_file_content(f)
                if content:
                    if 'resolv' in f.lower():
                        for line in content.split('\n'):
                            line = line.strip()
                            if line.startswith('nameserver'):
                                ns = line.split(None, 1)[1] if len(line.split()) > 1 else ''
                                dns_data['nameservers'].append(ns)
                            elif line.startswith('search'):
                                dns_data['searchDomains'] = line.split()[1:]
                            elif line.startswith('options'):
                                dns_data['resolv_conf']['options'] = line
                        dns_data['resolv_conf']['raw'] = content[:500]
            if not dns_data['nameservers']:
                dns_data['issues'].append('No nameservers in resolv.conf')
                issues_found.append({'section': 'dns', 'severity': 'critical', 'message': 'No nameservers configured'})
            dns_data['_note'] = (
                "This is the NODE-LEVEL /etc/resolv.conf. It is expected to show VPC DNS "
                "(e.g., 172.31.0.2 = VPC CIDR+2). Container DNS is configured separately "
                "by Docker/ECS agent via --dns flags or task-level dnsServers config."
            )
            results['dns'] = dns_data

        # =================================================================
        # ENI (Elastic Network Interfaces)
        # =================================================================
        if 'eni' in sections:
            eni_data = {'attachedENIs': [], 'eniCount': 0, 'issues': []}
            try:
                target_region = resolve_region(arguments, instance_id)
                regional_ec2 = get_regional_client('ec2', target_region)
                eni_resp = regional_ec2.describe_network_interfaces(
                    Filters=[{'Name': 'attachment.instance-id', 'Values': [instance_id]}]
                )
                for eni in eni_resp.get('NetworkInterfaces', []):
                    eni_data['attachedENIs'].append({
                        'eniId': eni['NetworkInterfaceId'],
                        'subnetId': eni.get('SubnetId'),
                        'privateIp': eni.get('PrivateIpAddress'),
                        'secondaryIps': [addr['PrivateIpAddress'] for addr in eni.get('PrivateIpAddresses', []) if not addr.get('Primary')],
                        'status': eni.get('Status'),
                        'description': eni.get('Description', '')[:100],
                        'securityGroups': [sg['GroupId'] for sg in eni.get('Groups', [])],
                    })
                eni_data['eniCount'] = len(eni_data['attachedENIs'])
                eni_data['totalSecondaryIPs'] = sum(len(e['secondaryIps']) for e in eni_data['attachedENIs'])
                if eni_data['eniCount'] == 0:
                    eni_data['issues'].append('No ENIs attached — instance may be detached from VPC')
                    issues_found.append({'section': 'eni', 'severity': 'critical', 'message': 'No ENIs attached'})
            except Exception as e:
                eni_data['issues'].append(f'Could not query ENI info: {str(e)}')
            # Also check from bundle files
            eni_files = find_files(['eni', 'network-interface', 'eth'])
            for f in eni_files[:3]:
                content = read_file_content(f, max_size=32768)
                if content:
                    eni_data['bundleNetworkInfo'] = content[:2000]
                    break
            results['eni'] = eni_data

        # =================================================================
        # SECURITY GROUPS
        # =================================================================
        if 'security-groups' in sections:
            sg_data = {'securityGroups': [], 'issues': []}
            try:
                target_region = resolve_region(arguments, instance_id)
                regional_ec2 = get_regional_client('ec2', target_region)
                inst_resp = regional_ec2.describe_instances(InstanceIds=[instance_id])
                sgs = []
                for res in inst_resp.get('Reservations', []):
                    for inst in res.get('Instances', []):
                        sgs = inst.get('SecurityGroups', [])
                sg_ids = [sg['GroupId'] for sg in sgs]
                if sg_ids:
                    sg_resp = regional_ec2.describe_security_groups(GroupIds=sg_ids)
                    for sg in sg_resp.get('SecurityGroups', []):
                        ingress_rules = []
                        for rule in sg.get('IpPermissions', []):
                            for cidr in rule.get('IpRanges', []):
                                ingress_rules.append({
                                    'protocol': rule.get('IpProtocol', 'all'),
                                    'fromPort': rule.get('FromPort'),
                                    'toPort': rule.get('ToPort'),
                                    'cidr': cidr.get('CidrIp'),
                                })
                            for sg_ref in rule.get('UserIdGroupPairs', []):
                                ingress_rules.append({
                                    'protocol': rule.get('IpProtocol', 'all'),
                                    'fromPort': rule.get('FromPort'),
                                    'toPort': rule.get('ToPort'),
                                    'sourceGroup': sg_ref.get('GroupId'),
                                })
                        sg_data['securityGroups'].append({
                            'groupId': sg['GroupId'],
                            'groupName': sg.get('GroupName', ''),
                            'description': sg.get('Description', '')[:100],
                            'ingressRuleCount': len(ingress_rules),
                            'ingressRules': ingress_rules[:30],
                        })
                    # Check for ECS-specific port requirements
                    all_ingress_ports = set()
                    for sg_info in sg_data['securityGroups']:
                        for rule in sg_info['ingressRules']:
                            fp = rule.get('fromPort')
                            tp = rule.get('toPort')
                            if fp and tp:
                                all_ingress_ports.update(range(fp, tp + 1))
                    # ECS agent needs outbound (usually allowed by default), but check for common task ports
                    if not sg_ids:
                        sg_data['issues'].append('No security groups attached to instance')
                        issues_found.append({'section': 'security-groups', 'severity': 'critical', 'message': 'No security groups'})
            except Exception as e:
                sg_data['issues'].append(f'Could not query security groups: {str(e)}')
            results['security-groups'] = sg_data

        # =================================================================
        # ECS NETWORKING GUARDRAILS — prevent misinterpretation of findings
        # =================================================================
        ecs_context = {
            '_purpose': 'ECS-specific networking context to prevent misinterpretation of findings. '
                        'ECS container instances use Docker bridge/awsvpc networking which differs from '
                        'bare-metal Linux hosts. DO NOT diagnose ECS instances like standalone servers.',
            'guardrails': [],
        }

        # Docker bridge networking context
        docker_cfg = results.get('docker', {}).get('bridgeConfig', {})
        if docker_cfg:
            ecs_context['guardrails'].append(
                'ECS EC2 launch type uses Docker bridge networking by default for tasks without '
                'awsvpc network mode. In bridge mode, containers share the host ENI via port mappings '
                '(DNAT rules). Missing DOCKER iptables chains means Docker networking is broken, '
                'but missing SNAT/MASQUERADE rules may be normal if tasks use awsvpc mode.'
            )

        # awsvpc mode context
        eni_count = results.get('eni', {}).get('eniCount', 0)
        if eni_count > 1:
            ecs_context['guardrails'].append(
                f'Multiple ENIs detected ({eni_count}): ECS tasks using awsvpc network mode get their own ENI. '
                'Each task ENI has its own security group and private IP. This is NORMAL for awsvpc tasks. '
                'Do NOT flag extra ENIs as anomalous on ECS instances running awsvpc tasks.'
            )

        # ANTI-HALLUCINATION: Docker iptables=false
        if docker_cfg.get('iptables') is False:
            ecs_context['guardrails'].append(
                'CRITICAL ANTI-HALLUCINATION: Docker daemon has "iptables": false in daemon.json. '
                'This means Docker will NOT manage iptables rules for container networking. '
                'This breaks bridge-mode container networking (no DNAT for port mappings, no MASQUERADE for egress). '
                'However, awsvpc-mode tasks are UNAFFECTED because they use their own ENI directly. '
                'Do NOT claim all container networking is broken — check the task network mode first.'
            )

        # ANTI-HALLUCINATION: Missing default gateway on awsvpc instances
        if not results.get('routes', {}).get('defaultGateway') and eni_count > 1:
            # Downgrade the "no default gateway" issue if awsvpc ENIs are present
            for issue in issues_found:
                if issue.get('section') == 'routes' and 'default gateway' in issue.get('message', '').lower():
                    issue['severity'] = 'info'
                    issue['message'] += ' [May be EXPECTED: awsvpc tasks use per-task ENI routing]'
            ecs_context['guardrails'].append(
                'ANTI-HALLUCINATION: On ECS instances with awsvpc tasks, the host route table may appear '
                'minimal. awsvpc tasks have their own network namespace with separate routing. '
                'Check per-task ENI configuration before flagging host routing as broken.'
            )

        # ANTI-HALLUCINATION: ECS Agent connectivity vs container networking
        ecs_context['guardrails'].append(
            'ANTI-HALLUCINATION: ECS Agent connectivity issues (AGENT_DISCONNECTED) and container '
            'networking issues are SEPARATE problems. The ECS Agent communicates with the ECS service '
            'endpoint via HTTPS (port 443). Container networking depends on the task network mode '
            '(bridge/awsvpc/host). An agent disconnect does NOT mean container networking is broken, '
            'and vice versa. Diagnose them independently.'
        )

        # ANTI-HALLUCINATION: Security group requirements differ by network mode
        ecs_context['guardrails'].append(
            'ANTI-HALLUCINATION: Security group requirements differ by ECS network mode. '
            'Bridge mode: Only the instance security group matters — containers use host ports via DNAT. '
            'awsvpc mode: Each task has its own security group — the instance SG does NOT apply to task traffic. '
            'Host mode: Tasks share the instance security group directly. '
            'Do NOT apply bridge-mode SG analysis to awsvpc tasks or vice versa.'
        )

        # ANTI-HALLUCINATION: DNS configuration
        if results.get('dns', {}).get('nameservers'):
            ecs_context['guardrails'].append(
                'ANTI-HALLUCINATION: The node-level /etc/resolv.conf shows VPC DNS resolver '
                '(typically VPC CIDR + 2, e.g., 172.31.0.2). Container DNS is configured separately: '
                'bridge mode uses Docker --dns flags (default: host DNS). '
                'awsvpc mode uses the VPC DNS directly in the task network namespace. '
                'Custom DNS can be set via task definition dnsServers/dnsSearchDomains. '
                'Do NOT assume container DNS matches host DNS without checking the task network mode.'
            )

        # ANTI-HALLUCINATION: Conntrack exhaustion is kernel-level
        ecs_context['guardrails'].append(
            'ANTI-HALLUCINATION: "nf_conntrack: table full" is a kernel resource exhaustion issue. '
            'Do NOT blame Docker or the ECS Agent. Fix: increase nf_conntrack_max via sysctl. '
            'High-traffic instances (running many tasks with bridge networking) are most susceptible '
            'because all containers share the host conntrack table in bridge mode.'
        )

        # ANTI-HALLUCINATION: ENI limits and task density
        ecs_context['guardrails'].append(
            'ANTI-HALLUCINATION: Each EC2 instance type has a maximum number of ENIs and IPs per ENI. '
            'awsvpc tasks each consume one ENI (or one IP with ENI trunking enabled). '
            'If ENI allocation fails, it may be an instance limit, NOT a subnet IP exhaustion issue. '
            'Check the instance type ENI limit before blaming the subnet. '
            'ENI trunking (account-level opt-in) allows more tasks per instance by sharing trunk ENIs.'
        )

        # ANTI-HALLUCINATION: ECS service connect vs service discovery
        ecs_context['guardrails'].append(
            'ANTI-HALLUCINATION: ECS Service Connect and ECS Service Discovery (Cloud Map) are '
            'DIFFERENT features. Service Connect uses an Envoy sidecar proxy for service mesh. '
            'Service Discovery uses Route 53 DNS (A/SRV records). They can coexist but have '
            'different failure modes. DNS failures may be Service Discovery issues, while '
            'connection proxy errors point to Service Connect. Do NOT conflate them.'
        )

        # ANTI-HALLUCINATION: Container health checks vs ELB health checks
        ecs_context['guardrails'].append(
            'ANTI-HALLUCINATION: ECS container health checks (HEALTHCHECK in Dockerfile or '
            'healthCheck in task definition) and ELB target health checks are INDEPENDENT. '
            'A container can be healthy per its own health check but unhealthy per the ALB/NLB. '
            'Common cause: security group not allowing health check traffic from the load balancer. '
            'Diagnose each health check type separately.'
        )

        results['ecsContext'] = ecs_context

        # =================================================================
        # OVERALL SUMMARY
        # =================================================================
        total_issues = len(issues_found)
        critical_issues = sum(1 for i in issues_found if i.get('severity') == 'critical')
        warning_issues = sum(1 for i in issues_found if i.get('severity') == 'warning')

        sections_with_data = sum(1 for s in sections if s in results and results[s])
        if sections_with_data >= 4 and critical_issues > 0:
            confidence = 'high'
        elif sections_with_data >= 2 and total_issues > 0:
            confidence = 'medium'
        elif sections_with_data >= 1:
            confidence = 'low'
        else:
            confidence = 'none'

        gaps = []
        if not bundle_files:
            gaps.append('No extracted bundle found — collect and wait for completion first')
        sections_without_files = [s for s in sections if s not in section_file_map or not section_file_map.get(s)]
        if sections_without_files:
            gaps.append(f'No files found for sections: {", ".join(sections_without_files)}')

        # Build response (stale bundles are already rejected above, so this is always fresh)
        response = {
            'instanceId': instance_id,
        }

        # Bundle freshness info (always fresh since stale bundles are hard-blocked)
        if bundle_age_minutes is not None:
            response['bundleInfo'] = {
                'collectedAt': bundle_collected_at,
                'ageMinutes': bundle_age_minutes,
                'isStale': False,
            }

        response['sections'] = sections
        response['diagnostics'] = results
        response['issuesSummary'] = {
            'total': total_issues,
            'critical': critical_issues,
            'warning': warning_issues,
            'issues': issues_found,
        }
        response['confidence'] = confidence
        response['gaps'] = gaps
        response['overallAssessment'] = _network_assessment(issues_found)
        response['nextStep'] = 'Use search tool to dig deeper into specific networking errors, or correlate to build a timeline.' if issues_found else 'No networking issues detected in the bundle.'
        response['recommendedSOPs'] = match_sops_for_issues(issues_found)

        return success_response(response)

    except Exception as e:
        return error_response(500, f'network_diagnostics failed: {str(e)}')


def _network_assessment(issues: List[Dict]) -> str:
    """Generate overall network health assessment."""
    if not issues:
        return "HEALTHY — No networking issues detected in the log bundle."
    critical = [i for i in issues if i.get('severity') == 'critical']
    if critical:
        sections = set(i['section'] for i in critical)
        return f"CRITICAL — {len(critical)} critical networking issues in: {', '.join(sections)}. Immediate investigation needed."
    return f"WARNING — {len(issues)} non-critical networking issues found. Review recommended."

def tcpdump_capture(arguments: Dict) -> Dict:
    """
    Run tcpdump on an ECS container instance via SSM Run Command for a specified duration,
    then upload the pcap file to S3.

    Inputs:
        instanceId: EC2 instance ID (required)
        durationSeconds: Capture duration in seconds (default: 120, max: 300)
        interface: Network interface to capture on (default: "any")
        filter: BPF filter expression (e.g., "port 443", "host 10.0.0.1") (optional)
        taskId: ECS task ID to capture traffic for a specific container's network namespace (optional)
        containerName: Container name within the task (optional, uses first container if omitted)
        region: AWS region where the instance runs (optional, auto-detected)
        commandId: If provided, polls status of an existing capture instead of starting a new one

    Returns:
        commandId for async polling, or capture results if already complete
    """
    instance_id = arguments.get('instanceId')
    if not instance_id:
        return error_response(400, 'instanceId is required')

    if not re.match(r'^i-[0-9a-f]{8,17}$', instance_id):
        return error_response(400, f'Invalid instanceId format: {instance_id}')

    duration = int(arguments.get('durationSeconds', 120))
    if duration < 10 or duration > 300:
        return error_response(400, 'durationSeconds must be between 10 and 300')

    interface = arguments.get('interface', 'any')
    if not re.match(r'^[a-zA-Z0-9\-\.]+$', interface):
        return error_response(400, f'Invalid interface name: {interface}')

    bpf_filter = arguments.get('filter', '')
    if bpf_filter and re.search(r'[;&|`$(){}]', bpf_filter):
        return error_response(400, 'filter contains invalid characters')

    ecs_task_id = arguments.get('taskId', '').strip()
    container_name = arguments.get('containerName', '').strip()

    # Check if this is a status poll for an existing command
    command_id = arguments.get('commandId')
    if command_id:
        return _poll_tcpdump_status(command_id, instance_id, arguments)

    target_region = resolve_region(arguments, instance_id)

    try:
        regional_ssm = get_regional_client('ssm', target_region)
    except Exception as e:
        return error_response(500, f'Failed to create SSM client for region {target_region}: {str(e)}')

    # Build the shell script that runs tcpdump and uploads to S3
    timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    s3_prefix = f"tcpdump/{instance_id}/{timestamp}"
    s3_key = f"{s3_prefix}/capture.pcap"
    s3_key_txt = f"{s3_prefix}/capture_summary.txt"
    s3_key_stats = f"{s3_prefix}/capture_stats.json"
    s3_uri = f"s3://{LOGS_BUCKET}/{s3_key}"
    s3_uri_txt = f"s3://{LOGS_BUCKET}/{s3_key_txt}"
    s3_uri_stats = f"s3://{LOGS_BUCKET}/{s3_key_stats}"

    filter_clause = f' {bpf_filter}' if bpf_filter else ''

    use_nsenter = bool(ecs_task_id)
    ns_label = ''
    if ecs_task_id:
        ns_label = f' (ECS task {ecs_task_id}' + (f' container {container_name}' if container_name else '') + ' namespace)'

    script = f"""#!/bin/bash
set -euo pipefail

PCAP_FILE="/tmp/tcpdump_capture_{timestamp}.pcap"
TXT_FILE="/tmp/tcpdump_summary_{timestamp}.txt"
STATS_FILE="/tmp/tcpdump_stats_{timestamp}.json"

# Check if tcpdump is available
if ! command -v tcpdump &>/dev/null; then
    echo "ERROR: tcpdump not found. Installing..."
    if command -v yum &>/dev/null; then
        yum install -y tcpdump 2>/dev/null || {{ echo "FATAL: Failed to install tcpdump"; exit 1; }}
    elif command -v apt-get &>/dev/null; then
        apt-get update -qq && apt-get install -y tcpdump 2>/dev/null || {{ echo "FATAL: Failed to install tcpdump"; exit 1; }}
    else
        echo "FATAL: No package manager found to install tcpdump"
        exit 1
    fi
fi

NSENTER_PREFIX=""
"""

    # Add ECS task container PID resolution when taskId is provided
    if ecs_task_id:
        container_filter = f'--filter "name={container_name}"' if container_name else ''
        script += f"""
# === Resolve ECS task "{ecs_task_id}" to container PID ===
echo "Resolving ECS task to container PID..."
TARGET_PID=""

# Method 1: ECS agent introspection endpoint (works on all ECS-optimized AMIs)
echo "Trying ECS agent introspection endpoint..."
TASK_META=$(curl -s http://localhost:51678/v1/tasks 2>/dev/null || true)
if [ -n "$TASK_META" ] && command -v python3 &>/dev/null; then
    TARGET_DOCKER_ID=$(python3 -c "
import sys, json
try:
    data = json.loads('''$TASK_META''')
    tasks = data if isinstance(data, list) else data.get('Tasks', [])
    for task in tasks:
        task_arn = task.get('Arn', '')
        # Match by full ARN or just the task ID suffix
        if '{ecs_task_id}' in task_arn:
            containers = task.get('Containers', [])
            for c in containers:
                container_name_filter = '{container_name}'
                if container_name_filter and c.get('Name') != container_name_filter:
                    continue
                docker_id = c.get('DockerId', '')
                if docker_id:
                    print(docker_id)
                    sys.exit(0)
            # If no container_name filter matched, take the first container
            if containers:
                docker_id = containers[0].get('DockerId', '')
                if docker_id:
                    print(docker_id)
                    sys.exit(0)
except Exception as e:
    print('', file=sys.stderr)
" 2>/dev/null || true)
    if [ -n "$TARGET_DOCKER_ID" ]; then
        echo "Found Docker container ID from ECS introspection: $TARGET_DOCKER_ID"
    fi
fi

# Method 2: docker ps with ECS task label
if [ -z "$TARGET_DOCKER_ID" ] && command -v docker &>/dev/null; then
    echo "Trying docker ps with task label..."
    TARGET_DOCKER_ID=$(docker ps --filter "label=com.amazonaws.ecs.task-arn" {container_filter} --format '{{{{.ID}}}} {{{{.Labels}}}}' 2>/dev/null | grep '{ecs_task_id}' | head -1 | awk '{{print $1}}')
    if [ -z "$TARGET_DOCKER_ID" ]; then
        # Broader search
        TARGET_DOCKER_ID=$(docker ps --format '{{{{.ID}}}} {{{{.Labels}}}}' 2>/dev/null | grep '{ecs_task_id}' | head -1 | awk '{{print $1}}')
    fi
    if [ -n "$TARGET_DOCKER_ID" ]; then
        echo "Found Docker container ID from docker ps: $TARGET_DOCKER_ID"
    fi
fi

# Get PID from Docker container ID
if [ -n "$TARGET_DOCKER_ID" ] && command -v docker &>/dev/null; then
    TARGET_PID=$(docker inspect --format '{{{{.State.Pid}}}}' "$TARGET_DOCKER_ID" 2>/dev/null || true)
    echo "Docker container $TARGET_DOCKER_ID -> PID $TARGET_PID"
fi

# Method 3: ctr (containerd native CLI — for newer ECS AMIs using containerd without docker)
if [ -z "$TARGET_PID" ] || [ "$TARGET_PID" = "0" ]; then
    CTR=""
    for p in /usr/local/bin/ctr /usr/bin/ctr $(which ctr 2>/dev/null); do
        if [ -x "$p" ]; then CTR="$p"; break; fi
    done
    if [ -n "$CTR" ]; then
        echo "Trying ctr ($CTR) to find ECS task container..."
        # ECS uses the 'moby' namespace in containerd (or 'default')
        # ctr containers ls does NOT show task IDs — must inspect each container's labels
        for NS in moby default; do
            for cid in $($CTR -n $NS containers ls -q 2>/dev/null); do
                INFO=$($CTR -n $NS containers info "$cid" 2>/dev/null || true)
                if echo "$INFO" | grep -q "{ecs_task_id}"; then
                    echo "ctr: found container $cid in namespace $NS"
                    CTR_PID=$($CTR -n $NS task ls 2>/dev/null | grep "$cid" | awk '{{print $2}}')
                    if [ -n "$CTR_PID" ] && [ "$CTR_PID" != "0" ] && [ -e "/proc/$CTR_PID/ns/net" ]; then
                        TARGET_PID="$CTR_PID"
                        echo "ctr: resolved pid=$TARGET_PID"
                        break 2
                    fi
                fi
            done
        done
        [ -z "$TARGET_PID" ] && echo "ctr: could not resolve ECS task '{ecs_task_id}'"
    fi
fi

if [ -z "$TARGET_PID" ] || [ "$TARGET_PID" = "0" ]; then
    echo "FATAL: Could not resolve ECS task {ecs_task_id} to a container PID on this instance."
    echo "Ensure the task is running on instance {instance_id}."
    echo ""
    echo "Debug info:"
    echo "  ECS agent introspection:"
    curl -s http://localhost:51678/v1/tasks 2>/dev/null | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    tasks = d if isinstance(d, list) else d.get('Tasks', [])
    for t in tasks[:10]:
        print(f'  Task: {{t.get(\"Arn\",\"?\")}}  Status: {{t.get(\"DesiredStatus\",\"?\")}}')
except: print('  (could not parse)')
" 2>/dev/null || echo "  (endpoint not available)"
    echo "  Running docker containers:"
    docker ps --format 'table {{{{.ID}}}}\\t{{{{.Names}}}}\\t{{{{.Status}}}}' 2>/dev/null | head -10 || echo "  (docker not available)"
    echo "  ctr binary: ${{CTR:-not found}}"
    echo "  containerd socket: $(ls -la /run/containerd/containerd.sock 2>/dev/null || echo 'not found')"
    exit 1
fi

echo "Resolved ECS task {ecs_task_id} -> PID $TARGET_PID"
if [ ! -e "/proc/$TARGET_PID/ns/net" ]; then
    if [ ! -d "/proc/$TARGET_PID" ]; then
        echo "FATAL: PID $TARGET_PID does not exist in /proc (container may have exited)"
    else
        echo "FATAL: PID $TARGET_PID exists but /proc/$TARGET_PID/ns/net is missing"
    fi
    exit 1
fi
NSENTER_PREFIX="nsenter -n -t $TARGET_PID "
"""

    script += f"""
echo "Starting tcpdump{ns_label} on interface '{interface}' for {duration}s..."
echo "Filter: '{bpf_filter or 'none'}'"
echo "Output: $PCAP_FILE"

# Run tcpdump with timeout (with optional nsenter)
timeout {duration} ${{NSENTER_PREFIX}}tcpdump -i {interface} -w "$PCAP_FILE" -c 100000{filter_clause} 2>&1 || true

# Verify capture file exists and has data
if [ ! -f "$PCAP_FILE" ]; then
    echo "FATAL: Capture file not created"
    exit 1
fi

FILE_SIZE=$(stat -c%s "$PCAP_FILE" 2>/dev/null || stat -f%z "$PCAP_FILE" 2>/dev/null || echo "0")
echo "Capture complete. File size: $FILE_SIZE bytes"

if [ "$FILE_SIZE" -eq 0 ]; then
    echo "WARNING: Capture file is empty — no packets matched the filter"
fi

# Decode pcap to human-readable text summary (first 5000 packets max)
echo "Decoding pcap to text summary..."
# Use -c 5000 instead of piping through head to avoid SIGPIPE under set -euo pipefail
tcpdump -nn -r "$PCAP_FILE" -c 5000 > "$TXT_FILE" 2>/dev/null || true
TXT_SIZE=$(stat -c%s "$TXT_FILE" 2>/dev/null || stat -f%z "$TXT_FILE" 2>/dev/null || echo "0")
PACKET_COUNT=$(wc -l < "$TXT_FILE" 2>/dev/null || echo "0")
echo "Decoded $PACKET_COUNT packets to text (txt_size=$TXT_SIZE)"

# If decode produced empty output, log diagnostics and retry
if [ "$TXT_SIZE" -eq 0 ] || [ "$PACKET_COUNT" -eq 0 ]; then
    echo "WARNING: Text decode produced empty output."
    echo "Pcap file details:"
    ls -la "$PCAP_FILE" 2>/dev/null || true
    file "$PCAP_FILE" 2>/dev/null || true
    # Retry with verbose stderr to diagnose
    echo "Retry with stderr:"
    tcpdump -nn -r "$PCAP_FILE" -c 10 2>&1 || true
fi

# Generate stats JSON with protocol breakdown and top talkers (using Python for valid JSON)
echo "Generating capture statistics..."
if command -v python3 &>/dev/null; then
    python3 - "$PCAP_FILE" "$STATS_FILE" << 'PYSTATS'
import subprocess, json, sys, re
from collections import Counter

pcap, out = sys.argv[1], sys.argv[2]

def tcpdump_count(extra_args=None):
    cmd = ['tcpdump', '-nn', '-r', pcap] + (extra_args or [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return len([l for l in r.stdout.strip().splitlines() if l])
    except Exception:
        return 0

def tcpdump_lines():
    try:
        r = subprocess.run(['tcpdump', '-nn', '-r', pcap], capture_output=True, text=True, timeout=60)
        return [l for l in r.stdout.strip().splitlines() if l]
    except Exception:
        return []

lines = tcpdump_lines()
total = len(lines)

ip_port_re = re.compile(r'^(\\d+\\.\\d+\\.\\d+\\.\\d+)\\.\\d+$')
src_counter, dst_counter = Counter(), Counter()
for line in lines:
    parts = line.split()
    if len(parts) >= 5:
        m = ip_port_re.match(parts[2])
        if m:
            src_counter[m.group(1)] += 1
        dst_raw = parts[4].rstrip(':')
        m = ip_port_re.match(dst_raw)
        if m:
            dst_counter[m.group(1)] += 1

retrans = sum(1 for l in lines if 'retransmit' in l.lower() or 'retrans' in l.lower())

stats = {{
    "totalPackets": total,
    "protocols": {{
        "tcp": tcpdump_count(['tcp']),
        "udp": tcpdump_count(['udp']),
        "icmp": tcpdump_count(['icmp']),
        "arp": tcpdump_count(['arp']),
    }},
    "ports": {{
        "dns_53": tcpdump_count(['port', '53']),
        "http_80": tcpdump_count(['port', '80']),
        "https_443": tcpdump_count(['port', '443']),
    }},
    "tcpFlags": {{
        "syn": tcpdump_count(['tcp[tcpflags] & (tcp-syn) != 0']),
        "rst": tcpdump_count(['tcp[tcpflags] & (tcp-rst) != 0']),
    }},
    "possibleRetransmits": retrans,
    "topSourceIPs": dict(src_counter.most_common(10)),
    "topDestinationIPs": dict(dst_counter.most_common(10)),
}}

with open(out, 'w') as f:
    json.dump(stats, f, indent=2)
print(f"Stats generated: {{total}} packets")
PYSTATS
else
    TOTAL=$(tcpdump -nn -r "$PCAP_FILE" 2>/dev/null | wc -l)
    TCP_COUNT=$(tcpdump -nn -r "$PCAP_FILE" tcp 2>/dev/null | wc -l)
    UDP_COUNT=$(tcpdump -nn -r "$PCAP_FILE" udp 2>/dev/null | wc -l)
    ICMP_COUNT=$(tcpdump -nn -r "$PCAP_FILE" icmp 2>/dev/null | wc -l)
    echo '{{"totalPackets":'$TOTAL',"protocols":{{"tcp":'$TCP_COUNT',"udp":'$UDP_COUNT',"icmp":'$ICMP_COUNT'}},"topSourceIPs":{{}},"topDestinationIPs":{{}}}}' > "$STATS_FILE"
fi
if [ ! -f "$STATS_FILE" ] || [ ! -s "$STATS_FILE" ]; then
    echo '{{"error":"stats generation failed"}}' > "$STATS_FILE"
fi

# Upload all artifacts to S3
set +e
UPLOAD_FAILURES=0

echo "Uploading pcap to {s3_uri}..."
aws s3 cp "$PCAP_FILE" "{s3_uri}" --no-progress 2>&1
if [ $? -eq 0 ]; then echo "UPLOAD_PCAP=ok"; else echo "UPLOAD_PCAP=failed"; UPLOAD_FAILURES=$((UPLOAD_FAILURES + 1)); fi

echo "Uploading text summary to {s3_uri_txt}..."
aws s3 cp "$TXT_FILE" "{s3_uri_txt}" --quiet 2>&1
if [ $? -eq 0 ]; then echo "UPLOAD_TXT=ok"; else echo "UPLOAD_TXT=failed"; UPLOAD_FAILURES=$((UPLOAD_FAILURES + 1)); fi

echo "Uploading stats to {s3_uri_stats}..."
aws s3 cp "$STATS_FILE" "{s3_uri_stats}" --quiet 2>&1
if [ $? -eq 0 ]; then echo "UPLOAD_STATS=ok"; else echo "UPLOAD_STATS=failed"; UPLOAD_FAILURES=$((UPLOAD_FAILURES + 1)); fi

if [ "$UPLOAD_FAILURES" -gt 0 ]; then
    echo "WARNING: $UPLOAD_FAILURES of 3 uploads failed. Ensure the instance IAM role has s3:PutObject permission to {LOGS_BUCKET}."
fi

echo "S3_KEY={s3_key}"
echo "S3_KEY_TXT={s3_key_txt}"
echo "S3_KEY_STATS={s3_key_stats}"
echo "FILE_SIZE=$FILE_SIZE"
echo "PACKET_COUNT=$PACKET_COUNT"
echo "UPLOAD_FAILURES=$UPLOAD_FAILURES"

# Inline the decoded text and stats in stdout so Lambda can parse them even if S3 upload failed
echo "===INLINE_STATS_BEGIN==="
cat "$STATS_FILE" 2>/dev/null || echo '{{"error":"stats file missing"}}'
echo ""
echo "===INLINE_STATS_END==="
echo "===INLINE_TXT_BEGIN==="
head -500 "$TXT_FILE" 2>/dev/null || echo "(no decoded text)"
echo ""
echo "===INLINE_TXT_END==="

# Cleanup
rm -f "$PCAP_FILE" "$TXT_FILE" "$STATS_FILE" 2>/dev/null || true
echo "DONE"
exit 0
"""

    try:
        response = regional_ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName='AWS-RunShellScript',
            Parameters={
                'commands': [script],
                'executionTimeout': [str(duration + 120)],
            },
            TimeoutSeconds=duration + 180,
            Comment=f'tcpdump capture for ECS instance {instance_id} ({duration}s)',
        )

        cmd_id = response['Command']['CommandId']

        # Store metadata for status polling
        try:
            s3_client.put_object(
                Bucket=LOGS_BUCKET,
                Key=f"tcpdump-commands/{cmd_id}.json",
                Body=json.dumps({
                    'commandId': cmd_id,
                    'instanceId': instance_id,
                    'region': target_region,
                    's3Key': s3_key,
                    's3KeyTxt': s3_key_txt,
                    's3KeyStats': s3_key_stats,
                    's3Prefix': s3_prefix,
                    'durationSeconds': duration,
                    'interface': interface,
                    'filter': bpf_filter,
                    'taskId': ecs_task_id or None,
                    'containerName': container_name or None,
                    'startedAt': timestamp,
                }),
            )
        except Exception:
            pass  # Non-fatal

        return success_response({
            'message': f'tcpdump capture started ({duration}s){ns_label}',
            'commandId': cmd_id,
            'instanceId': instance_id,
            'region': target_region,
            'durationSeconds': duration,
            'interface': interface,
            'filter': bpf_filter or 'none',
            'taskId': ecs_task_id or None,
            'containerName': container_name or None,
            's3Key': s3_key,
            's3KeyTxt': s3_key_txt,
            's3KeyStats': s3_key_stats,
            's3Bucket': LOGS_BUCKET,
            'estimatedCompletionSeconds': duration + 30,
            'nextStep': f'Poll with tcpdump_capture(commandId="{cmd_id}", instanceId="{instance_id}") after ~{duration + 30}s. Once complete, use tcpdump_analyze(instanceId="{instance_id}", commandId="{cmd_id}") to read the decoded packet summary.',
            'task': {
                'taskId': cmd_id,
                'state': 'running',
                'message': f'tcpdump running for {duration}s on {interface}',
                'progress': 0,
            },
        })

    except Exception as e:
        return error_response(500, f'Failed to start tcpdump: {str(e)}')


def _poll_tcpdump_status(command_id: str, instance_id: str, arguments: Dict) -> Dict:
    """Poll the status of a tcpdump SSM Run Command."""

    # Try to load stored metadata
    metadata = {}
    try:
        meta_resp = s3_client.get_object(
            Bucket=LOGS_BUCKET,
            Key=f"tcpdump-commands/{command_id}.json",
        )
        metadata = json.loads(meta_resp['Body'].read().decode('utf-8'))
    except Exception:
        pass

    target_region = metadata.get('region') or resolve_region(arguments, instance_id)

    try:
        regional_ssm = get_regional_client('ssm', target_region)
        result = regional_ssm.get_command_invocation(
            CommandId=command_id,
            InstanceId=instance_id,
        )

        status = result.get('Status', 'Unknown')
        stdout = result.get('StandardOutputContent', '')
        stderr = result.get('StandardErrorContent', '')

        # Parse output for S3 key and file size
        s3_key = metadata.get('s3Key', '')
        s3_key_txt = metadata.get('s3KeyTxt', '')
        s3_key_stats = metadata.get('s3KeyStats', '')
        file_size = 0
        packet_count = 0
        for line in stdout.split('\n'):
            if line.startswith('S3_KEY='):
                s3_key = line.split('=', 1)[1].strip()
            elif line.startswith('S3_KEY_TXT='):
                s3_key_txt = line.split('=', 1)[1].strip()
            elif line.startswith('S3_KEY_STATS='):
                s3_key_stats = line.split('=', 1)[1].strip()
            elif line.startswith('FILE_SIZE='):
                try: file_size = int(line.split('=', 1)[1].strip())
                except ValueError: pass
            elif line.startswith('PACKET_COUNT='):
                try: packet_count = int(line.split('=', 1)[1].strip())
                except ValueError: pass

        capture_completed = (
            'Capture complete.' in stdout
            or 'DONE' in stdout
            or 'UPLOAD_PCAP=ok' in stdout
            or 'UPLOAD_PCAP=failed' in stdout
            or ('FILE_SIZE=' in stdout and 'S3_KEY=' in stdout)
        )
        if 'FATAL:' in stdout:
            capture_completed = False

        upload_failures = 0
        for line in stdout.split('\n'):
            if line.startswith('UPLOAD_FAILURES='):
                try: upload_failures = int(line.split('=', 1)[1].strip())
                except ValueError: pass

        # Extract inline stats and text from stdout
        inline_stats = {}
        inline_txt_lines = []
        if '===INLINE_STATS_BEGIN===' in stdout:
            try:
                stats_block = stdout.split('===INLINE_STATS_BEGIN===')[1].split('===INLINE_STATS_END===')[0].strip()
                if stats_block:
                    inline_stats = json.loads(stats_block)
            except (IndexError, json.JSONDecodeError):
                pass
        if '===INLINE_TXT_BEGIN===' in stdout:
            try:
                txt_block = stdout.split('===INLINE_TXT_BEGIN===')[1].split('===INLINE_TXT_END===')[0].strip()
                if txt_block:
                    inline_txt_lines = txt_block.split('\n')
            except IndexError:
                pass

        if status in ('Success',) or (capture_completed and status == 'Failed'):
            presigned_url = ''
            try:
                presigned_url = s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': LOGS_BUCKET, 'Key': s3_key},
                    ExpiresIn=3600,
                )
            except Exception:
                pass

            # If S3 uploads failed from node, store inline data from Lambda
            if (upload_failures > 0 or (status == 'Failed' and capture_completed)) and inline_stats:
                try:
                    s3_client.put_object(Bucket=LOGS_BUCKET, Key=s3_key_stats,
                                         Body=json.dumps(inline_stats, indent=2), ContentType='application/json')
                except Exception:
                    pass
            if (upload_failures > 0 or (status == 'Failed' and capture_completed)) and inline_txt_lines:
                try:
                    s3_client.put_object(Bucket=LOGS_BUCKET, Key=s3_key_txt,
                                         Body='\n'.join(inline_txt_lines), ContentType='text/plain')
                except Exception:
                    pass

            warnings = []
            actual_failures = 0
            if upload_failures > 0 or (status == 'Failed' and capture_completed):
                actual_failures = upload_failures if upload_failures > 0 else 3
                warnings.append(f'{actual_failures} of 3 S3 uploads failed from the node. Stats and text recovered via Lambda.')
                if actual_failures == 3:
                    warnings.append('pcap file was NOT uploaded — add S3 PutObject permission to the instance IAM role.')

            response_data = {
                'commandId': command_id,
                'instanceId': instance_id,
                'status': 'completed' if not warnings else 'completed_with_warnings',
                's3Key': s3_key, 's3KeyTxt': s3_key_txt, 's3KeyStats': s3_key_stats,
                's3Bucket': LOGS_BUCKET,
                'fileSizeBytes': file_size, 'fileSizeHuman': format_bytes(file_size),
                'packetCount': packet_count,
                'presignedUrl': presigned_url, 'presignedUrlExpiresIn': '1 hour',
                'output': stdout[-2000:] if len(stdout) > 2000 else stdout,
                'nextStep': f'Use tcpdump_analyze(instanceId="{instance_id}", commandId="{command_id}") to read decoded packet data and statistics.',
                'task': {'taskId': command_id, 'state': 'completed',
                         'message': f'tcpdump capture completed' + (f' ({actual_failures} S3 uploads failed — recovered via Lambda)' if warnings else f' — uploaded to s3://{LOGS_BUCKET}/{s3_key}'),
                         'progress': 100},
            }
            if warnings:
                response_data['warnings'] = warnings
            if inline_stats:
                response_data['inlineStats'] = inline_stats
            return success_response(response_data)

        elif status in ('InProgress', 'Pending', 'Delayed'):
            elapsed = 0
            duration = metadata.get('durationSeconds', 120)
            if metadata.get('startedAt'):
                try:
                    start_dt = datetime.strptime(metadata['startedAt'], '%Y%m%dT%H%M%SZ')
                    elapsed = (datetime.utcnow() - start_dt).total_seconds()
                except Exception:
                    pass
            progress = min(95, int((elapsed / (duration + 30)) * 100)) if duration else 0

            return success_response({
                'commandId': command_id,
                'instanceId': instance_id,
                'status': 'in_progress',
                'elapsedSeconds': int(elapsed),
                'durationSeconds': duration,
                'nextStep': 'Poll again in 15-30 seconds',
                'task': {'taskId': command_id, 'state': 'running',
                         'message': f'tcpdump capture in progress ({int(elapsed)}s / {duration}s)',
                         'progress': progress},
            })

        else:
            return error_response(500, f'tcpdump command {status}', {
                'commandId': command_id, 'status': status,
                'stdout': stdout[-2000:] if stdout else '',
                'stderr': stderr[-2000:] if stderr else '',
                'statusDetails': result.get('StatusDetails', ''),
                'task': {'taskId': command_id, 'state': 'failed',
                         'message': f'tcpdump command {status}: {stderr[:200] if stderr else "unknown error"}',
                         'progress': 0},
            })

    except Exception as e:
        return error_response(500, f'Failed to poll tcpdump status: {str(e)}')


def tcpdump_analyze(arguments: Dict) -> Dict:
    """
    Read and analyze a completed tcpdump capture from S3.
    Returns decoded packet text, protocol statistics, and top talkers.

    Inputs:
        instanceId: EC2 instance ID (required)
        commandId: SSM Command ID from tcpdump_capture (optional — finds latest if omitted)
        section: "summary" (decoded packets), "stats" (protocol breakdown), "all" (default)
        maxPackets: Max decoded packet lines to return (default: 500, max: 3000)
        filter: Text filter to apply on decoded lines (e.g., "SYN", "RST", "10.0.0.5")

    Returns:
        Decoded packet text, protocol stats, top talkers, and anomaly indicators
    """
    instance_id = arguments.get('instanceId')
    if not instance_id:
        return error_response(400, 'instanceId is required')

    command_id = arguments.get('commandId')
    section = arguments.get('section', 'all')
    max_packets = min(int(arguments.get('maxPackets', 500)), 3000)
    text_filter = arguments.get('filter', '')

    # Find the capture metadata
    metadata = {}
    if command_id:
        try:
            meta_resp = s3_client.get_object(
                Bucket=LOGS_BUCKET,
                Key=f"tcpdump-commands/{command_id}.json",
            )
            metadata = json.loads(meta_resp['Body'].read().decode('utf-8'))
        except Exception:
            pass

    # If no commandId, find the latest capture for this instance
    if not metadata:
        try:
            list_resp = safe_s3_list_raw(LOGS_BUCKET, "tcpdump-commands/", max_keys=200)
            candidates = []
            for obj in list_resp:
                try:
                    r = s3_client.get_object(Bucket=LOGS_BUCKET, Key=obj['Key'])
                    m = json.loads(r['Body'].read().decode('utf-8'))
                    if m.get('instanceId') == instance_id:
                        candidates.append(m)
                except Exception:
                    continue
            if candidates:
                candidates.sort(key=lambda x: x.get('startedAt', ''), reverse=True)
                metadata = candidates[0]
        except Exception:
            pass

    if not metadata:
        return error_response(404, f'No tcpdump capture found for {instance_id}. Run tcpdump_capture first.')

    s3_key_txt = metadata.get('s3KeyTxt', '')
    s3_key_stats = metadata.get('s3KeyStats', '')
    s3_key_pcap = metadata.get('s3Key', '')

    results = {
        'instanceId': instance_id,
        'commandId': metadata.get('commandId', command_id or 'unknown'),
        'captureInfo': {
            'interface': metadata.get('interface', 'unknown'),
            'filter': metadata.get('filter', 'none'),
            'durationSeconds': metadata.get('durationSeconds', 0),
            'startedAt': metadata.get('startedAt', 'unknown'),
            'taskId': metadata.get('taskId'),
            'containerName': metadata.get('containerName'),
        },
    }

    # Read stats
    if section in ('stats', 'all'):
        stats = {}
        if s3_key_stats:
            try:
                raw = safe_s3_read_raw(LOGS_BUCKET, s3_key_stats)
                if raw:
                    stats = json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
            except (json.JSONDecodeError, Exception):
                stats = {'error': 'Could not parse stats JSON'}
        else:
            stats = {'error': 'No stats file found — capture may still be in progress'}

        results['statistics'] = stats

        # Anomaly detection
        anomalies = []
        if isinstance(stats, dict) and 'totalPackets' in stats:
            total = stats.get('totalPackets', 0)
            rst_count = stats.get('tcpFlags', {}).get('rst', 0)
            syn_count = stats.get('tcpFlags', {}).get('syn', 0)
            retrans = stats.get('possibleRetransmits', 0)

            if total > 0:
                rst_pct = (rst_count / total) * 100
                if rst_pct > 5:
                    anomalies.append({
                        'type': 'high_rst_rate',
                        'severity': 'warning' if rst_pct < 15 else 'critical',
                        'message': f'{rst_pct:.1f}% of packets are TCP RST ({rst_count}/{total}) — possible connection rejection or firewall drops',
                    })
                if retrans > 0:
                    retrans_pct = (retrans / total) * 100
                    anomalies.append({
                        'type': 'retransmissions',
                        'severity': 'warning' if retrans_pct < 5 else 'critical',
                        'message': f'{retrans} possible retransmissions ({retrans_pct:.1f}%) — network congestion or packet loss',
                    })
                if syn_count > 0 and rst_count > syn_count * 0.5:
                    anomalies.append({
                        'type': 'syn_rst_ratio',
                        'severity': 'warning',
                        'message': f'High RST-to-SYN ratio ({rst_count} RST vs {syn_count} SYN) — many connections being refused',
                    })
                icmp_count = stats.get('protocols', {}).get('icmp', 0)
                if icmp_count > total * 0.1:
                    anomalies.append({
                        'type': 'high_icmp',
                        'severity': 'info',
                        'message': f'{icmp_count} ICMP packets ({(icmp_count/total)*100:.1f}%) — possible ping flood or unreachable destinations',
                    })
        results['anomalies'] = anomalies

    # Read decoded text summary
    if section in ('summary', 'all'):
        decoded_lines = []
        if s3_key_txt:
            try:
                raw = safe_s3_read_raw(LOGS_BUCKET, s3_key_txt)
                if raw:
                    content = raw.decode('utf-8') if isinstance(raw, bytes) else raw
                    all_lines = content.split('\n')

                    if text_filter:
                        pattern = re.compile(re.escape(text_filter), re.IGNORECASE)
                        all_lines = [l for l in all_lines if pattern.search(l)]

                    total_lines = len(all_lines)
                    decoded_lines = all_lines[:max_packets]

                    results['decodedPackets'] = {
                        'lines': decoded_lines,
                        'totalPackets': total_lines,
                        'returnedPackets': len(decoded_lines),
                        'truncated': total_lines > max_packets,
                        'filter': text_filter or 'none',
                    }
                else:
                    results['decodedPackets'] = {'error': 'Text summary file is empty or unreadable'}
            except Exception as e:
                results['decodedPackets'] = {'error': f'Failed to read text summary: {str(e)}'}
        else:
            results['decodedPackets'] = {'error': 'No text summary file found — capture may still be in progress'}

    # Presigned URL for pcap download
    if s3_key_pcap:
        try:
            results['pcapDownloadUrl'] = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': LOGS_BUCKET, 'Key': s3_key_pcap},
                ExpiresIn=3600,
            )
            results['pcapDownloadUrlExpiresIn'] = '1 hour'
        except Exception:
            pass

    results['s3Bucket'] = LOGS_BUCKET
    results['s3KeyPcap'] = s3_key_pcap
    results['s3KeyTxt'] = s3_key_txt
    results['s3KeyStats'] = s3_key_stats

    return success_response(results)
