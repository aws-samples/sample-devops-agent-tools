"""
EKS Node Log MCP Server - Enhanced Lambda Handler

MCP toolset for incident response:
- Async task pattern with idempotency
- Byte-range streaming for multi-GB files
- Manifest validation and completeness verification
- Pre-indexed error findings
- Cross-file correlation
- Secure artifact references


"""

import json
import boto3
import logging
import os
import re
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum
from botocore.exceptions import ClientError
from botocore.config import Config


# Structured JSON logging — enables CloudWatch Logs Insights queries
logger = logging.getLogger()
logger.setLevel(logging.INFO)


# AWS Clients - default region (where Lambda runs)
# S3 client uses SigV4 explicitly — required for presigned URLs on KMS-encrypted buckets
ssm_client = boto3.client('ssm')
s3_client = boto3.client('s3', config=Config(signature_version='s3v4'))
ec2_client = boto3.client('ec2')
cloudwatch_client = boto3.client('cloudwatch')

# Regional client cache to avoid re-creating clients per invocation
_regional_clients: Dict[str, Dict[str, Any]] = {}

# Environment
LOGS_BUCKET = os.environ['LOGS_BUCKET_NAME']
SSM_AUTOMATION_ROLE_ARN = os.environ.get('SSM_AUTOMATION_ROLE_ARN', '')
DEFAULT_REGION = os.environ.get('AWS_REGION', 'us-east-1')
STACK_NAME = os.environ.get('STACK_NAME', 'EksNodeLogMcp')


def emit_metric(metric_name: str, value: float = 1.0, unit: str = 'Count',
                dimensions: Optional[List[Dict[str, str]]] = None) -> None:
    """Emit a CloudWatch custom metric for operational visibility."""
    try:
        cloudwatch_client.put_metric_data(
            Namespace='EksNodeLogMcp',
            MetricData=[{
                'MetricName': metric_name,
                'Value': value,
                'Unit': unit,
                'Dimensions': dimensions or [
                    {'Name': 'StackName', 'Value': STACK_NAME},
                ],
            }],
        )
    except Exception:
        # Metrics are best-effort — never fail the request over a metric
        pass


def get_regional_client(service: str, region: str) -> Any:
    """
    Get or create a boto3 client for a specific region.
    Caches clients to avoid repeated creation within the same Lambda invocation.
    """
    if region == DEFAULT_REGION:
        # Use the pre-initialized default clients
        if service == 'ssm':
            return ssm_client
        elif service == 's3':
            return s3_client
        elif service == 'ec2':
            return ec2_client

    cache_key = f'{service}:{region}'
    if cache_key not in _regional_clients:
        _regional_clients[cache_key] = boto3.client(service, region_name=region)
    return _regional_clients[cache_key]


def detect_instance_region(instance_id: str) -> Optional[str]:
    """
    Auto-detect the region of an EC2 instance by querying EC2 DescribeInstances
    across ALLOWED_REGIONS only. Tries the default region first, then remaining
    allowed regions.
    
    Security: Only scans regions in ALLOWED_REGIONS to prevent account-wide
    metadata exposure (T9/T11 mitigation).
    
    Returns the region string or None if not found.
    Times out after 20 seconds to avoid Lambda timeout issues.
    """
    import time
    start = time.time()
    DETECTION_TIMEOUT = 20  # seconds - leave headroom for Lambda timeout
    
    # Try default region first (fast path)
    if DEFAULT_REGION in ALLOWED_REGIONS:
        try:
            resp = ec2_client.describe_instances(InstanceIds=[instance_id])
            if resp['Reservations']:
                return DEFAULT_REGION
        except ec2_client.exceptions.ClientError:
            pass
        except Exception:
            pass

    # Only scan ALLOWED_REGIONS — never scan all 16+ regions (T9 mitigation)
    remaining_regions = [r for r in sorted(ALLOWED_REGIONS) if r != DEFAULT_REGION]

    for region in remaining_regions:
        # Check timeout to avoid Lambda execution limit
        if time.time() - start > DETECTION_TIMEOUT:
            logger.warning(f"Region auto-detection timed out after {DETECTION_TIMEOUT}s")
            return None
        try:
            regional_ec2 = get_regional_client('ec2', region)
            resp = regional_ec2.describe_instances(InstanceIds=[instance_id])
            if resp['Reservations']:
                logger.info(f"Auto-detected instance {instance_id} in region {region}")
                return region
        except Exception:
            continue

    return None


def resolve_region(arguments: Dict, instance_id: str = None) -> str:
    """
    Resolve the target region from arguments or auto-detection.
    Priority: explicit region param > auto-detect from instance > default region.
    """
    explicit_region = arguments.get('region')
    if explicit_region:
        # Basic validation: AWS region format is like us-east-1, eu-west-2, etc.
        if not re.match(r'^[a-z]{2}(-[a-z]+-\d+)$', explicit_region):
            print(f"Warning: Invalid region format '{explicit_region}', falling back to auto-detection")
        else:
            return explicit_region

    if instance_id:
        detected = detect_instance_region(instance_id)
        if detected:
            return detected

    return DEFAULT_REGION

# Constants
DEFAULT_CHUNK_SIZE = 1048576  # 1MB
MAX_CHUNK_SIZE = 5242880  # 5MB
DEFAULT_LINE_COUNT = 1000
MAX_LINE_COUNT = 10000
FINDINGS_INDEX_FILE = 'findings_index.json'


# =============================================================================
# PRESIGNED URL EXPIRATION — configurable via env var (T5 mitigation)
# =============================================================================

def _parse_presigned_url_expiration() -> int:
    """Parse PRESIGNED_URL_EXPIRATION_SECONDS env var, default to 300, max 900."""
    raw = os.environ.get('PRESIGNED_URL_EXPIRATION_SECONDS', '')
    try:
        val = int(raw)
        if val > 0:
            return min(val, 900)  # Cap at 15 minutes max (T5 mitigation)
    except (ValueError, TypeError):
        pass
    return 300

PRESIGNED_URL_EXPIRATION = _parse_presigned_url_expiration()


def _parse_pcap_presigned_url_expiration() -> int:
    """
    Parse PCAP_PRESIGNED_URL_EXPIRATION_SECONDS env var. Network captures may
    contain credentials in transit and other sensitive payloads — they get a
    much shorter window than ordinary log artifacts. Default 60s, max 300s.
    """
    raw = os.environ.get('PCAP_PRESIGNED_URL_EXPIRATION_SECONDS', '')
    try:
        val = int(raw)
        if val > 0:
            return min(val, 300)
    except (ValueError, TypeError):
        pass
    return 60


PCAP_PRESIGNED_URL_EXPIRATION = _parse_pcap_presigned_url_expiration()


def _parse_max_pcap_bytes() -> int:
    """Cap at which a pcap upload is flagged as oversized."""
    raw = os.environ.get('MAX_PCAP_BYTES', '')
    try:
        val = int(raw)
        if val > 0:
            return val
    except (ValueError, TypeError):
        pass
    return 200 * 1024 * 1024  # 200 MiB


MAX_PCAP_BYTES = _parse_max_pcap_bytes()


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
# TOOL AUTHORIZATION TIERS — per-tool access control (T6 mitigation)
# =============================================================================

# Tools that require explicit opt-in via ENABLED_RESTRICTED_TOOLS env var.
# These tools perform invasive operations (network captures, namespace entry)
# and are completely removed from the routing table by default.
# They do not appear in available_tools and cannot be invoked unless enabled.
RESTRICTED_TOOLS = {
    'tcpdump_capture',
    'tcpdump_analyze',
}

# Parse enabled restricted tools from env var
ENABLED_RESTRICTED_TOOLS = set(
    t.strip() for t in os.environ.get('ENABLED_RESTRICTED_TOOLS', '').split(',')
    if t.strip()
)


def _parse_tool_authorization() -> Dict[str, set]:
    """
    Parse the per-tool ACL from the TOOL_AUTHORIZATION env var.

    Format: tool1:client_a,client_b;tool2:client_c
    - Tools listed get a non-empty allow-set: only those clients may invoke.
    - Tools not listed are open to all authenticated callers.
    - Tools listed with no clients are deny-all.
    """
    raw = os.environ.get('TOOL_AUTHORIZATION', '')
    acl: Dict[str, set] = {}
    if not raw:
        return acl
    for entry in raw.split(';'):
        entry = entry.strip()
        if not entry or ':' not in entry:
            continue
        tool, clients = entry.split(':', 1)
        tool = tool.strip()
        if not tool:
            continue
        client_set = {c.strip() for c in clients.split(',') if c.strip()}
        acl[tool] = client_set
    return acl


TOOL_AUTHORIZATION_ACL = _parse_tool_authorization()


def _parse_per_caller_rate_limit() -> int:
    """Per-caller invocations-per-minute limit. 0 disables rate limiting."""
    raw = os.environ.get('PER_CALLER_RATE_LIMIT_PER_MINUTE', '')
    try:
        val = int(raw)
        if val >= 0:
            return val
    except (ValueError, TypeError):
        pass
    return 60


PER_CALLER_RATE_LIMIT = _parse_per_caller_rate_limit()


# In-process token bucket. Lambda containers can be reused across invocations
# in the same warm execution environment; this bucket gives best-effort rate
# limiting on a single warm container. For strict cross-container limits a
# DynamoDB-backed counter would be required — flagged below in code.
_RATE_LIMIT_STATE: Dict[str, list] = {}


def _extract_caller_identity(event: Dict, context: Any) -> Dict[str, Optional[str]]:
    """
    Extract caller identity from the AgentCore Gateway invocation envelope.
    AgentCore forwards JWT claims via context.client_context.custom; we read
    the Cognito client_id (and sub if available) for ACL + rate-limiter keys.
    Returns a dict with 'client_id', 'sub', and 'principal' (best-available
    stable identifier).
    """
    info: Dict[str, Optional[str]] = {'client_id': None, 'sub': None, 'principal': None}
    try:
        custom = getattr(context.client_context, 'custom', None) or {}
    except Exception:
        custom = {}
    # Common AgentCore claim shapes
    for key in ('bedrockAgentCoreClientId', 'clientId', 'client_id'):
        if custom.get(key):
            info['client_id'] = str(custom[key])
            break
    for key in ('bedrockAgentCoreSub', 'sub', 'principalId', 'cognito:username'):
        if custom.get(key):
            info['sub'] = str(custom[key])
            break
    # Allow event-level fallbacks (e.g. when invoked outside AgentCore)
    if not info['client_id']:
        ev_id = (event or {}).get('clientId') or (event or {}).get('client_id')
        if ev_id:
            info['client_id'] = str(ev_id)
    if not info['sub']:
        ev_sub = (event or {}).get('sub') or (event or {}).get('principalId')
        if ev_sub:
            info['sub'] = str(ev_sub)
    info['principal'] = info['sub'] or info['client_id'] or 'anonymous'
    return info


def _enforce_rate_limit(caller_key: str) -> Optional[Dict]:
    """
    Best-effort per-caller rate limit. Returns None if under the limit, or an
    error_response dict if the limit is exceeded.
    """
    if PER_CALLER_RATE_LIMIT <= 0:
        return None
    now = time.time()
    window = 60.0
    history = _RATE_LIMIT_STATE.setdefault(caller_key, [])
    # Drop entries older than the window
    cutoff = now - window
    while history and history[0] < cutoff:
        history.pop(0)
    if len(history) >= PER_CALLER_RATE_LIMIT:
        retry_after = max(1, int(history[0] + window - now))
        return error_response(
            429,
            f'Rate limit exceeded ({PER_CALLER_RATE_LIMIT} invocations/min per caller). '
            f'Retry in ~{retry_after}s.',
            {'retryAfterSeconds': retry_after},
        )
    history.append(now)
    return None


def validate_tool_authorization(tool_name: str, caller: Optional[Dict] = None) -> Optional[Dict]:
    """
    Authorization gate. Combines:
      (a) Restricted-tool opt-in (ENABLED_RESTRICTED_TOOLS).
      (b) Per-tool ACL keyed on Cognito client_id (TOOL_AUTHORIZATION).
    Returns None if authorized, or an error_response dict if denied.
    """
    if tool_name in RESTRICTED_TOOLS and tool_name not in ENABLED_RESTRICTED_TOOLS:
        return error_response(
            403,
            f"Tool '{tool_name}' is restricted and not enabled. "
            f"Set ENABLED_RESTRICTED_TOOLS environment variable to include '{tool_name}' to enable it.",
            {'restrictedTools': sorted(RESTRICTED_TOOLS)},
        )
    if tool_name in TOOL_AUTHORIZATION_ACL:
        allowed = TOOL_AUTHORIZATION_ACL[tool_name]
        client_id = (caller or {}).get('client_id') or ''
        if not allowed:
            return error_response(
                403,
                f"Tool '{tool_name}' is configured deny-all (no clients in its ACL).",
            )
        if client_id not in allowed:
            return error_response(
                403,
                f"Caller is not permitted to invoke '{tool_name}'.",
                {'requiredClients': sorted(allowed)},
            )
    return None


# =============================================================================
# BPF FILTER VALIDATION — allowlist-based (T1 mitigation)
# =============================================================================

# Allowlist of safe BPF filter tokens. This is intentionally restrictive.
# BPF filters are a mini-language; we only allow known-safe primitives.
_BPF_ALLOWED_KEYWORDS = frozenset({
    # Protocols
    'tcp', 'udp', 'icmp', 'arp', 'ip', 'ip6', 'ether', 'vlan', 'stp',
    # Directions
    'src', 'dst',
    # Qualifiers
    'host', 'net', 'port', 'portrange', 'proto',
    # Logical operators
    'and', 'or', 'not',
    # TCP flags (used in bracket expressions)
    'tcp-syn', 'tcp-ack', 'tcp-fin', 'tcp-rst', 'tcp-push', 'tcp-urg',
    # Misc
    'greater', 'less', 'len',
})

# Pattern for valid BPF tokens: keywords, IPs, CIDRs, numbers, and bracket expressions
_BPF_TOKEN_PATTERN = re.compile(
    r'^('
    r'\d{1,5}'                     # port numbers
    r'|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(/\d{1,2})?'  # IPv4 addresses and CIDRs
    r'|[0-9a-f:]+(/\d{1,3})?'      # IPv6 addresses and CIDRs
    r'|\d+-\d+'                     # port ranges (e.g., 80-443)
    r')$',
    re.IGNORECASE
)

# Bracket expressions like tcp[tcpflags], tcp[13], udp[0:2]
_BPF_BRACKET_PATTERN = re.compile(
    r'^(tcp|udp|icmp|ip|ip6|ether)\['
    r'[a-z0-9:]+\]'
    r'(\s*[&|!=<>]+\s*'
    r'(\(?(tcp-syn|tcp-ack|tcp-fin|tcp-rst|tcp-push|tcp-urg|0x[0-9a-f]+|\d+)\)?)'
    r')?$',
    re.IGNORECASE
)


def validate_bpf_filter(bpf_filter: str) -> Optional[str]:
    """
    Validate a BPF filter expression using allowlist-based validation.
    
    Returns None if valid, or an error message string if invalid.
    
    Security: This replaces the previous denylist approach which missed
    backticks, newlines, and other injection vectors. The allowlist approach
    only permits known-safe BPF primitives.
    """
    if not bpf_filter:
        return None
    
    # Hard reject: any control characters, backticks, or shell metacharacters
    # This catches \n, \r, \t, backticks, $, etc.
    if re.search(r'[\x00-\x1f\x7f`$\\;{}<>!~^]', bpf_filter):
        return 'BPF filter contains forbidden characters (control chars, backticks, shell metacharacters)'
    
    # Reject excessively long filters
    if len(bpf_filter) > 256:
        return 'BPF filter too long (max 256 characters)'
    
    # Reject parentheses used for subshells — BPF uses them for grouping but
    # we handle them carefully
    # Allow balanced parentheses only
    depth = 0
    for ch in bpf_filter:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if depth < 0:
            return 'BPF filter has unbalanced parentheses'
    if depth != 0:
        return 'BPF filter has unbalanced parentheses'
    
    # Strip parentheses for token validation (BPF uses them for grouping)
    stripped = bpf_filter.replace('(', ' ').replace(')', ' ')
    
    # Tokenize and validate each token
    tokens = stripped.split()
    if not tokens:
        return 'BPF filter is empty after parsing'
    
    for token in tokens:
        token_lower = token.lower().strip()
        if not token_lower:
            continue
        
        # Check against known keywords
        if token_lower in _BPF_ALLOWED_KEYWORDS:
            continue
        
        # Check against token pattern (IPs, ports, numbers)
        if _BPF_TOKEN_PATTERN.match(token_lower):
            continue
        
        # Check bracket expressions (e.g., tcp[tcpflags])
        if _BPF_BRACKET_PATTERN.match(token_lower):
            continue
        
        # Comparison operators
        if token_lower in ('!=', '==', '>=', '<=', '>', '<', '=', '&'):
            continue
        
        # Hex values (used in flag comparisons)
        if re.match(r'^0x[0-9a-f]+$', token_lower):
            continue
        
        return f"BPF filter contains disallowed token: '{token}'. Only standard BPF primitives are permitted."
    
    return None


# =============================================================================
# EKS INSTANCE VALIDATION — verify target is an EKS node (T4, T13 mitigation)
# =============================================================================

def validate_eks_instance(instance_id: str, region: str) -> Optional[Dict]:
    """
    Validate that an instance belongs to an EKS cluster using both tag-based
    AND EKS API-based verification.
    
    Security: Tag-based validation alone is bypassable by anyone who can tag
    EC2 instances. We now cross-reference with EKS DescribeNodegroup or
    the eks:cluster-name tag (set by EKS service, not user-editable).
    
    Returns None if valid, or an error response dict if invalid.
    """
    try:
        regional_ec2 = get_regional_client('ec2', region)
        resp = regional_ec2.describe_instances(InstanceIds=[instance_id])
        
        eks_cluster_name = None
        has_k8s_tag = False
        has_eks_managed_tag = False
        
        for reservation in resp.get('Reservations', []):
            for instance in reservation.get('Instances', []):
                tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
                
                # Check for kubernetes.io/cluster/* tag (user-settable)
                for key in tags:
                    if key.startswith('kubernetes.io/cluster/'):
                        has_k8s_tag = True
                        break
                
                # Check for eks:cluster-name tag (set by EKS managed node groups,
                # not user-editable via standard EC2 tag APIs)
                eks_cluster_name = tags.get('eks:cluster-name')
                if eks_cluster_name:
                    has_eks_managed_tag = True
                
                # Also check eks:nodegroup-name as secondary signal
                if tags.get('eks:nodegroup-name'):
                    has_eks_managed_tag = True
        
        if not has_k8s_tag and not has_eks_managed_tag:
            return error_response(
                403,
                f"Instance {instance_id} is not part of an EKS cluster "
                f"(no kubernetes.io/cluster/* or eks:cluster-name tag found)"
            )
        
        # If we have an EKS cluster name, verify it exists via EKS API
        if eks_cluster_name:
            try:
                regional_eks = get_regional_client('eks', region)
                regional_eks.describe_cluster(name=eks_cluster_name)
            except ClientError as e:
                code = e.response['Error']['Code']
                if code == 'ResourceNotFoundException':
                    return error_response(
                        403,
                        f"Instance {instance_id} references EKS cluster '{eks_cluster_name}' "
                        f"which does not exist in region {region}. Tag may be spoofed."
                    )
                # Other errors (AccessDenied, etc.) — don't block, but log
                logger.warning(f"Could not verify EKS cluster '{eks_cluster_name}': {e}")
        
        return None  # Valid EKS instance
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidInstanceID.NotFound':
            return error_response(404, f"Instance {instance_id} not found in region {region}")
        return error_response(500, f"Failed to validate instance {instance_id}: {str(e)}")


# =============================================================================
# RESPONSE REDACTION — strip sensitive infra details from tool responses
# =============================================================================

# Resource ID prefixes we treat as sensitive infrastructure metadata.
_REDACT_ID_PATTERNS: List = [
    (re.compile(r'\bsg-[0-9a-f]{8,17}\b'),               'sg-***'),
    (re.compile(r'\beni-[0-9a-f]{8,17}\b'),              'eni-***'),
    (re.compile(r'\bsubnet-[0-9a-f]{8,17}\b'),           'subnet-***'),
    (re.compile(r'\bvpc-[0-9a-f]{8,17}\b'),              'vpc-***'),
    (re.compile(r'\bvol-[0-9a-f]{8,17}\b'),              'vol-***'),
    (re.compile(r'\bfs-[0-9a-f]{8,17}\b'),               'fs-***'),
    (re.compile(r'\bfsap-[0-9a-f]{8,17}\b'),             'fsap-***'),
    # Account IDs in ARNs (12 digits between colons)
    (re.compile(r'(arn:[^:]*:[^:]*:[^:]*:)\d{12}(:)'),   r'\1***\2'),
    # AWS access key prefixes
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'),                'AKIA****************'),
    (re.compile(r'\bASIA[0-9A-Z]{16}\b'),                'ASIA****************'),
    # bearer tokens / JWT-ish strings
    (re.compile(r'eyJ[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}'),
     '<redacted-jwt>'),
]

# Substrings that indicate IAM / credential failure messages we want to
# collapse to a tag rather than echo verbatim.
_IAM_ERROR_MARKERS = (
    'accessdenied', 'access denied', 'unauthorized', 'forbidden',
    'not authorized to perform', 'expiredtoken', 'invalidclienttokenid',
    'signature does not match', 'tokenrefreshrequired',
)

# Private IP redaction. RFC1918 + carrier-grade NAT (100.64.0.0/10) are common
# in EKS pod networks; we keep a coarse signal (octet count) but obscure the
# host portion.
_PRIVATE_IP_RE = re.compile(
    r'\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}'
    r'|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}'
    r'|192\.168\.\d{1,3}\.\d{1,3}'
    r'|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b'
)


def _redact_string(value: str, mask_private_ips: bool = False) -> str:
    """Apply redaction patterns to a single string."""
    if not value or not isinstance(value, str):
        return value
    out = value
    for pattern, replacement in _REDACT_ID_PATTERNS:
        out = pattern.sub(replacement, out)
    if mask_private_ips:
        out = _PRIVATE_IP_RE.sub('<private-ip>', out)
    # Collapse IAM/credential error message bodies after the marker
    lowered = out.lower()
    for marker in _IAM_ERROR_MARKERS:
        idx = lowered.find(marker)
        if idx >= 0:
            # Keep the marker context but drop the rest of the line which often
            # echoes principal ARNs, service names, and request IDs.
            head = out[:idx + len(marker)]
            out = head + ' <iam-error-details-redacted>'
            break
    return out


def _redact_value(value: Any, mask_private_ips: bool = False, _depth: int = 0) -> Any:
    """Recursively redact a JSON-serialisable structure."""
    if _depth > 12:
        return value  # safety: bail on deeply nested structures
    if isinstance(value, str):
        return _redact_string(value, mask_private_ips=mask_private_ips)
    if isinstance(value, list):
        return [_redact_value(v, mask_private_ips=mask_private_ips, _depth=_depth + 1) for v in value]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            kl = k.lower() if isinstance(k, str) else ''
            # Drop wholesale: any field that names a credential, secret, token,
            # or password — never useful in a diagnostic response.
            if any(s in kl for s in ('password', 'secret', 'apikey', 'api_key', 'token', 'credential')):
                # Allow short identifiers where the *name* is "tokenName" etc;
                # we still drop the value to be safe.
                out[k] = '<redacted>'
                continue
            # Volume handles often embed full EBS/EFS IDs — truncate hard.
            if kl in ('volumehandle', 'volume_handle'):
                out[k] = (str(v)[:24] + '…') if v else v
                continue
            out[k] = _redact_value(v, mask_private_ips=mask_private_ips, _depth=_depth + 1)
        return out
    return value


def redact_response(response: Dict, tool_name: str = '') -> Dict:
    """
    Redact a Lambda response envelope ({statusCode, body}). Returns a new dict
    with the body re-serialised after sensitive values have been masked.
    Diagnostic tools that traffic in network metadata get aggressive private-IP
    masking; other tools keep IPs (often needed to interpret a finding) but
    still get IAM/secret/account-ID redaction.
    """
    if not isinstance(response, dict) or 'body' not in response:
        return response
    body_raw = response.get('body')
    if not isinstance(body_raw, str):
        return response
    try:
        parsed = json.loads(body_raw)
    except Exception:
        return response
    aggressive_ip_tools = {
        'network_diagnostics', 'tcpdump_analyze', 'tcpdump_capture',
        'cluster_health', 'storage_diagnostics',
    }
    mask_ips = tool_name in aggressive_ip_tools
    redacted = _redact_value(parsed, mask_private_ips=mask_ips)
    return {**response, 'body': json.dumps(redacted, default=str)}


class Severity(Enum):
    CRITICAL = 'critical'
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'
    INFO = 'info'


# Backward-compat mapping: v1 (3-level) -> v2 (5-level)
SEVERITY_V1_TO_V2 = {
    'critical': 'critical',
    'warning': 'high',     # old "warning" maps to new "high"
    'info': 'info',
}

# Reverse mapping for queries using old severity names
SEVERITY_V2_TO_V1 = {
    'critical': 'critical',
    'high': 'warning',
    'medium': 'warning',
    'low': 'info',
    'info': 'info',
}

# Severity ordering for sorting (lower = more severe)
SEVERITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}


def normalize_severity_filter(severity_filter: str) -> list:
    """Normalize a severity filter to a list of v2 severity values."""
    if severity_filter == 'all':
        return ['critical', 'high', 'medium', 'low', 'info']
    # Support old v1 names
    if severity_filter == 'warning':
        return ['high', 'medium']
    if severity_filter in SEVERITY_ORDER:
        return [severity_filter]
    return ['critical', 'high', 'medium', 'low', 'info']


def assign_finding_id(index: int) -> str:
    """Generate a stable finding ID in F-001 format."""
    return f"F-{index:03d}"


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
        """
        Resolve time window from tool arguments.

        Accepts:
            incident_time: ISO8601 string or human-readable UTC timestamp
            start_time: ISO8601 string (window start)
            end_time: ISO8601 string (window end)

        Returns dict with:
            window_start_utc: datetime
            window_end_utc: datetime
            window_start_iso: str (ISO8601)
            window_end_iso: str (ISO8601)
            resolution_reason: str
            journalctl_since: str (formatted for --since)
            journalctl_until: str (formatted for --until)
        """
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

        # Safety cap: clamp window to MAX_WINDOW_HOURS
        max_delta = timedelta(hours=TimeWindowResolver.MAX_WINDOW_HOURS)
        if (window_end - window_start) > max_delta:
            window_start = window_end - max_delta
            reason += f' (clamped to max {TimeWindowResolver.MAX_WINDOW_HOURS}h window)'

        # Ensure end >= start
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
        """Parse various timestamp formats into a UTC datetime."""
        if not ts_str or not isinstance(ts_str, str):
            return None
        ts_str = ts_str.strip()
        # Try ISO8601 variants
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
                    # Convert to UTC, then strip tzinfo for uniform comparison
                    from datetime import timezone
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt
            except ValueError:
                continue
        # Try unix timestamp (seconds)
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
        """Check if a log line timestamp falls within the resolved window."""
        dt = TimeWindowResolver._parse_timestamp(timestamp_str)
        if dt is None:
            return True  # If we can't parse, include it (conservative)
        return window['window_start_utc'] <= dt <= window['window_end_utc']

    @staticmethod
    def filter_findings_by_window(findings: List[Dict], window: Dict) -> Dict:
        """
        Filter findings list to only those within the time window.
        Returns dict with filtered findings and exclusion stats.
        """
        included = []
        excluded_count = 0
        unparseable_count = 0

        for f in findings:
            sample = f.get('sample', '')
            ts_str = extract_timestamp(sample) if sample else None
            if ts_str is None:
                unparseable_count += 1
                included.append(f)  # Conservative: include if no timestamp
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
        """Return a serializable metadata block for inclusion in tool responses."""
        return {
            'window_start_utc': window['window_start_iso'],
            'window_end_utc': window['window_end_iso'],
            'resolution_reason': window['resolution_reason'],
        }


ERROR_PATTERNS = {
    Severity.CRITICAL: [  # Unrecoverable / node-down / data-loss risk
        r'BUG:.*',  # Kernel bug detected
        r'kernel panic',
        r'watchdog: BUG: soft lockup',  # Soft lockup detection
        r'Memory cgroup out of memory.*process \d+ \(.*?\)',  # OOM kill with process
        r'\S+ invoked oom-killer',  # OOM killer invoked
        r'traps:\s*.*?\[',  # Application crash/trap
        r'\s.*?\[\d+\]: segfault at',  # Segfault
        r'task .*?:\d+ blocked for more than',  # Process blocked (I/O)
        r'(ip|nf)_conntrack: table full, dropping packet',  # Conntrack exhaustion
        r'dropping packet',  # Conntrack or iptables dropping packets
        r'iptables.*error',  # iptables rule error
        r'iptables-restore.*failed',  # iptables restore failed
        r'kube-proxy.*error',  # kube-proxy issue
        r'IPVS.*error',  # IPVS mode error
        r'conntrack.*exhausted',  # Connection tracking full
        
        r'failed to (list|ensure lease exists).*Unauthorized',  # AWS auth issue
        r'(Server rejected|Unable to register).*Unauthorized',  # Node registration failed
        r'UnauthorizedOperation:',  # IAM permission issue
        r'the server has asked for the client to provide credentials',  # Cluster role issue
        r'unknown node for user "system:node:"',  # Bad certificate
        r'no networks found in /etc/cni/net\.d',  # CNI failure
        r'fork/exec.*resource temporarily unavailable',  # PID exhaustion
        r'failed to create new OS thread.*errno=11',  # Go runtime PID exhaustion
        r'Node became not ready.* Message:',  # Node NotReady
        r'Unit kubelet.* entered failed state',  # Kubelet failed
        r'failed to run Kubelet:',  # Kubelet launch failure
        r'Unable to register node with API server.* node=',  # Invalid node naming
        r'OCI runtime create failed:',  # Container runtime failure
        r'Standalone mode',  # Kubelet standalone mode (misconfiguration)
        r'PLEG is not healthy',  # PLEG health issue
        r'failed to validate kubelet flags:',  # Kubelet flag validation
        r'(failed to list|ensure lease exists|Server rejected|Unable to register).*dial.*i/o timeout',  # Network failure
        
        r'Starting L-IPAMD',  # IPAMD restart (critical if repeated)
        r'InsufficientFreeAddressesInSubnet',  # IP exhaustion
        r'Failed to check API server connectivity.*no configuration has been provided',  # Missing token
        r'Unable to reach API Server',  # API server unreachable
        r'Failed to check API server connectivity',  # API connectivity failure
        r'Unauthorized operation: failed to call .* due to missing permissions',  # IAM missing permissions
        
        r'Instances failed to join',
        r'failed to join the kubernetes cluster',
        r'unable to register node',
        r'failed to register node',
        r'certificate has expired',
        r'x509: certificate',
        
        r'WebIdentityErr: failed to retrieve credentials',  # IRSA credential retrieval failed
        r'InvalidIdentityToken.*No OpenIDConnect provider found',  # OIDC provider not found
        r'InvalidIdentityToken.*Incorrect token audience',  # Wrong OIDC audience
        r"InvalidIdentityToken.*HTTPS certificate doesn't match",  # OIDC thumbprint mismatch
        r'AccessDenied.*Not authorized to perform sts:AssumeRoleWithWebIdentity',  # IRSA assume role denied
        r'InvalidClientTokenId.*security token.*invalid',  # Invalid security token
        r'ValidationError.*Request ARN is invalid',  # Invalid IAM ARN format
        
        r'SUBNET_NOT_FOUND',  # Subnet not found
        r'SECURITY_GROUP_NOT_FOUND',  # Security group not found
        r'IP_NOT_AVAILABLE',  # IP not available in subnet
        r'VPC_NOT_FOUND',  # VPC not found - UNRECOVERABLE
        r'ASSUME_ROLE_ACCESS_DENIED',  # Cannot assume cluster role
        r'PERMISSION_ACCESS_DENIED',  # Insufficient role permissions
        r'ASSUME_ROLE_ACCESS_DENIED_USING_SLR',  # Cannot assume EKS service-linked-role
        r'PERMISSION_ACCESS_DENIED_USING_SLR',  # SLR insufficient permissions
        r'KMS_KEY_DISABLED',  # KMS key disabled
        r'KMS_KEY_NOT_FOUND',  # KMS key not found - UNRECOVERABLE
        r'KMS_GRANT_REVOKED',  # KMS grants revoked - UNRECOVERABLE
        r'STS_REGIONAL_ENDPOINT_DISABLED',  # STS endpoint disabled
        r'OPT_IN_REQUIRED',  # EC2 subscription missing
        
        r'AccessDenied',
        r'AmiIdNotFound',
        r'AsgInstanceLaunchFailures',
        r'AutoScalingGroupNotFound',
        r'ClusterUnreachable',
        r'Ec2LaunchTemplateNotFound',
        r'Ec2SecurityGroupNotFound',
        r'Ec2SubnetInvalidConfiguration',
        r'IamInstanceProfileNotFound',
        r'IamNodeRoleNotFound',
        r'InstanceLimitExceeded',
        r'InsufficientFreeAddresses',
        r'NodeCreationFailure',
        r'AutoScalingGroupInvalidConfiguration',  # ASG config modified externally
        r'Ec2LaunchTemplateVersionMismatch',  # Launch template version mismatch
        r'Ec2SecurityGroupDeletionFailure',  # Cannot delete remote access security group
        r'InternalFailure',  # Amazon EKS server-side issue
        
        r'nodeadm.*failed',
        r'nodeadm.*error',
        r'failed to initialize node',
        r'SSM activation failed',
        
        r'error mounting.*etc-hosts.*to rootfs.*/etc/hosts',  # /etc/hosts mount failed
        r'volume.*failed',
        r'mount.*failed',
        r'PersistentVolume.*failed',
        r'Unable to attach or mount volumes',  # General mount failure
        r'MountVolume\.SetUp failed',  # Mount setup failed
        r'timed out waiting for the condition.*volume',  # Mount timeout
        r'ebs-csi.*error',  # EBS CSI driver error
        r'efs-csi.*error',  # EFS CSI driver error
        r'mount\.nfs.*timed out',  # NFS mount timeout
        r'mount: wrong fs type',  # Filesystem type mismatch
        r'fsck.*error',  # Filesystem check error
        
        r'5\.4\.214-120\.368',  # Known PLEG issue kernel
        r'5\.4\.217-126\.408',  # Known PLEG issue kernel
        r'5\.4\.238-155\.346',  # Known SMB mount issue kernel
        
        r'Container runtime network not ready: NetworkReady=false reason:NetworkPluginNotReady',
        r'network plugin is not ready: cni config uninitialized',  # CNI not initialized
        r'container_linux\.go.*starting container process',  # Container start failure
        r'exec format error',  # Wrong architecture (amd64/arm64 mismatch)
        r'no such file or directory',  # Missing entrypoint/binary
        r'permission denied',  # File permissions issue
        
        r'Failed to assign an IP address to pod',  # IP assignment failure
        r'no free IP addresses',  # IP exhaustion
        r'ENI allocation failed',  # ENI limit or subnet issue
        r'failed to set up sandbox container.*network',  # Network setup failure
        r'NetworkNotReady',  # Network not ready condition
        r'networkPlugin cni failed',  # CNI plugin failure
        
        r'dial udp.*:53.*i/o timeout',  # DNS port unreachable (High)
        r'dial udp.*:53.*timeout',  # DNS timeout (High)
        r'upstream.*unreachable',  # CoreDNS upstream unreachable (High)
        r'coredns.*unhealthy',  # CoreDNS unhealthy (High)
        r'CoreDNS.*error',  # CoreDNS error (High)
        r'DNS.*timeout',  # DNS query timeout (High)
        
        r'node "" not found',  # Missing private DNS entry
        r'Failed to list \*v1\.Service: Unauthorized',
        r'Unable to register node.*with API server: Unauthorized',
        
        r'Killed process.*total-vm',  # OOM killer with memory info
        r'exit code 137',  # SIGKILL (OOM or manual kill)
        
        r'Failed to pull image',  # Image pull failure
        r'unauthorized.*authentication required',  # Registry auth missing
        r'manifest.*not found',  # Image/tag doesn't exist
        r'repository does not exist',  # Wrong repository
        r'ECR.*token.*expired',  # ECR auth expired
        r'pull access denied',  # No pull permission
        
        r'failed to get secret',  # Secret retrieval failed
        r'secrets.*not found',  # Secret doesn't exist
        r'secrets.*forbidden',  # No permission to access secret
        r'KMS.*error',  # KMS error (High severity)
        r'decrypt.*failed',  # Decryption failed (High severity)
        r'webhook.*timeout',  # Webhook timeout
        r'webhook.*denied',  # Webhook denied request
        r'admission.*rejected',  # Admission controller rejected
        r'CreateContainerConfigError',  # Container config error (often secrets-related)
        
        # Bandwidth/Network Limits
        r'Rx packets queued/dropped',  # IDBandwidthInExceeded - Rx bandwidth exceeded
        r'Tx packets queued/dropped',  # IDPPSExceeded - Tx packets per second exceeded
        r'Bandwidth.*exceeded',  # IDBandwidthOutExceeded - Bandwidth out exceeded
        r'LinkLocal.*dropped',  # IDLinkLocalExceeded - LinkLocal packets dropped
        
        # Conntrack (kernel level)
        r'nf_conntrack.*table full',  # IDConntrackExceededKernel - Conntrack exceeded at kernel level
        r'Maximum connections exceeded',  # IDConntrackExceeded - Instance level conntrack exceeded
        
        r'REJECT.*rule',  # IDUnexpectedRejectRule - Unexpected REJECT rule in iptables
        r'Missing.*IPAMD.*iptables',  # IDMissingIPAMdIptablesRules - Missing IPAMD iptables rules
        r'port.*conflict',  # Port conflict detected
        
        r'interface.*down',  # IDInterfaceDown - Network interface down
        r'Missing.*IPv6.*address',  # IDMissingIPv6Address - Missing IPv6 address
        r'Missing.*loopback',  # IDMissingLoopbackInterface - Missing loopback interface
        
        r'Missing.*pod.*IP.*route',  # IDMissingIPRouteRules - Missing pod IP route rules
        r'Missing.*default.*route',  # IDMissingDefaultRoutes - Missing default route rules
        
        r'Excessive.*threads',  # IDExcessiveThreads - Too many threads
        r'zombie.*process',  # IdExcessiveZombieProcesses - Zombie processes
        r'Approaching.*kernel.*pid.*max',  # IDApproachingKernelPidMax - Near PID limit
        r'runc.*init.*hung',  # IDRuncInitPossiblyHung - runc init possibly hung
        
        r'nodeadm.*run.*restart',  # IDNodeadmRunRestart - Nodeadm run restart
        
        # Bootstrap/Boot Issues
        r'Repeated.*bootstrap.*execution',  # IDRepeatedBootstrapExecution - Repeated bootstrap
        r'Multiple.*boots',  # IDMultipleBoots - Multiple boots detected
        r'Unexpected.*filesystem.*mount.*operation',  # IDUnexpectedFilesystemMountOperation - Unexpected mount after bootstrap
        
        # Auto Mode Issues
        r'VPC.*CNI.*pod.*Auto.*Mode.*node',  # IDAutoModeNodeWithAwsNode - VPC CNI pod on Auto Mode node
        
        r'ec2-net-utils',  # IDHasEC2NetUtilsPackage - ec2-net-utils package installed (causes issues)
        
        # Security Agent Issues
        r'Trend.*Micro.*Security.*Agent',  # IDHasTrendMicroSecurityAgent - Trend Micro agent running (known issues)
    ],
    Severity.HIGH: [  # Service-impacting but recoverable
        r'Readiness probe for ".*?:(.*)" failed',  # Readiness probe failure
        r'Liveness probe for ".*?:(.*)" failed',  # Liveness probe failure
        r'due to client-side throttling',  # Client-side throttling
        r'\(PLEG\): ".*?".*Type:"ContainerDied"',  # Container died
        r'Pod still has one or more containers in the non-exited state',  # Pod stuck terminating
        r'(Starting|Stopping).* Kubernetes Kubelet',  # Kubelet restart
        
        r'\S+: Found a Tx that wasn\'t completed on time',  # TX not completed
        r'nfs: server .*? not responding',  # NFS not responding
        r'mce: .*: Core temperature is above threshold',  # CPU overheating
        
        r'is not authorized to perform: .*? ',  # Missing AWS permission
        r'systemd.*Failed to start .*?\.',  # Service failed to start
        r'cloud-init: \+ /etc/eks/bootstrap\.sh',  # Repeated bootstrap (if multiple)
        r'cloud-init: \+ mount /.*? /.*?',  # Unexpected mount operation
        r'kernel: Command line:',  # Multiple boots
        
        r'getsockopt: no route to host',
        r'network is unreachable',
        r'dial tcp.*connection refused',
        r'dial tcp.*i/o timeout',
        r'TLS handshake timeout',
        r'context deadline exceeded',
        r'DNS.*failed',
        r'resolve.*failed',
        r'lookup.*failed',
        
        r'ImagePullBackOff',
        r'ErrImagePull',
        r'CrashLoopBackOff',
        r'RunContainerError',
        r'CreateContainerError',
        r'CreateContainerConfigError',
        r'FailedScheduling',
        r'FailedMount',
        r'FailedAttachVolume',
        
        r'Insufficient cpu',
        r'Insufficient memory',
        r'Insufficient pods',
        r'resource quota exceeded',
        r'PodEvicted',
        r'Evicted',
        r'OOMKilled',
        
        r'node\(s\) didn\'t match.*selector',  # Node selector mismatch
        r'node\(s\) had.*taint',  # Taint/toleration mismatch
        r'node\(s\) didn\'t have free ports',  # Host port conflict
        r'0/\d+ nodes are available',  # No schedulable nodes
        r'Unschedulable',  # Pod can't be scheduled
        r'PodToleratesNodeTaints',  # Toleration issue
        r'NodeAffinity',  # Affinity rule not satisfied
        r'PodAffinity',  # Pod affinity not satisfied
        
        r'VolumeResizeFailed',
        r'WaitForFirstConsumer',
        r'Pending.*PersistentVolumeClaim',
        r'xfs_repair',  # XFS filesystem repair needed
        r'PVC.*pending',  # PVC in pending state
        
        r'VPC CNI v1\.20\.4',  # Known buggy version
        
        r'runc init',  # runc init possibly hung
        r'zombie',  # Zombie processes
        
        r'DataDog.*7\.38\.[01]',  # DataDog zombie process bug
        
        r'Back-off restarting failed container',  # Container restart backoff
        r'denied.*access',  # Access denied (generic)
        r'dial tcp.*connection refused.*registry',  # Registry connection refused
        r'no such host.*ecr',  # ECR DNS failure
        r'no such host',  # Host not resolvable (generic)
        r'i/o timeout.*registry',  # Registry timeout
        r'NXDOMAIN',  # DNS domain not found
        r'SERVFAIL',  # DNS server failure
        r'CoreDNS.*error',  # CoreDNS error
    ],
    Severity.MEDIUM: [  # Degraded performance / potential escalation
        r'fs: disk usage and inodes count on following dirs took',  # Slow disk usage
        r'--node-labels=""',  # Empty node labels
        
        r'net_ratelimit:.*\d+ callbacks suppressed',  # Kernel log rate limiting
        r'martian source .* from .*, on dev',  # Martian packet
        
        r'rsyslogd:.* \d+ messages lost due to rate-limiting',  # Syslog rate limiting
        
        r'MutatingWebhook.*error',  # Mutating webhook error
        r'ValidatingWebhook.*error',  # Validating webhook error
        
        r'cpu.*throttl',  # IDCPUThrottling - CPU throttling detected
        r'io.*delay',  # IDIODelays - I/O delays detected
        
        r'High.*Disk.*Usage',  # IDHighDiskUsage - High disk usage
        r'XFS.*Small.*Average.*Cluster.*Size',  # IDXFSSmallAverageClusterSize
        
        r'UNREPLIED.*conntrack',  # IDConntrackUnrepliedEntries
        
        r'kube-proxy.*slow',  # IDKubeProxySlow
        
        # Pod Issues
        r'Pod.*stuck.*terminating',  # IDPodStuckTerminating
        
        # Environment Issues
        r'Large.*environment.*variables',  # IDLargeEnvironment
        
        # Cron Issues
        r'Rapid.*cron',  # IDRapidCron
        
        # Container Issues
        r'Many.*dead.*containers',  # IDManyDeadContainers
        
        # Network Configuration
        r'Missing.*MACAddressPolicy',  # IDMissingMACAddressPolicy
        r'Non.*default.*VPC.*CNI.*settings',  # IDNonDefaultVPCCNISettings
        
        # Well-known Application Bugs
        r'Well.*known.*application.*bug',  # IDWellKnownApplicationBug
    ],
    Severity.LOW: [  # Informational warnings that may need attention
        r'readiness probe failed',
        r'liveness probe failed',
        r'startup probe failed',
        
        r'CPU Throttling',
        r'I/O Delay',
        
        r'High Disk Usage',
        r'Small XFS Average Cluster Size',
        
        r'Many Network Connections',
        r'Interface Down',
    ],
}

# Pre-compile all ERROR_PATTERNS at module level to avoid recompilation per-file
COMPILED_ERROR_PATTERNS = {}
for _severity, _patterns in ERROR_PATTERNS.items():
    COMPILED_ERROR_PATTERNS[_severity] = []
    for _pattern in _patterns:
        try:
            COMPILED_ERROR_PATTERNS[_severity].append(re.compile(_pattern, re.IGNORECASE))
        except re.error:
            pass  # Skip invalid patterns

# =============================================================================
# POD/NODE FAILURE TRIAGE PATTERNS
# =============================================================================

# Category A: Volume/CSI Mount Issues
TRIAGE_VOLUME_CSI_PATTERNS = [
    (r'FailedMount', 'high'),
    (r'FailedAttachVolume', 'high'),
    (r'Unable to attach or mount volumes', 'high'),
    (r'MountVolume\.SetUp failed', 'high'),
    (r'volume.*not found', 'medium'),
    (r'VolumeResizeFailed', 'medium'),
    (r'WaitForFirstConsumer', 'medium'),
    (r'timed out waiting for the condition.*volume', 'high'),
    (r'ebs-csi.*error', 'high'),
    (r'efs-csi.*error', 'high'),
    (r'mount\.nfs.*timed out', 'high'),
    (r'mount: wrong fs type', 'high'),
    (r'fsck.*error', 'medium'),
    (r'xfs_repair', 'medium'),
    (r'PersistentVolume.*failed', 'high'),
    (r'PVC.*pending', 'medium'),
]

# Category B: Worker Node Issues
TRIAGE_NODE_ISSUES_PATTERNS = [
    (r'Node became not ready', 'high'),
    (r'NodeNotReady', 'high'),
    (r'PLEG is not healthy', 'high'),
    (r'Unit kubelet.*entered failed state', 'high'),
    (r'failed to run Kubelet', 'high'),
    (r'OCI runtime create failed', 'high'),
    (r'containerd.*error', 'medium'),
    (r'docker.*error', 'medium'),
    (r'DiskPressure', 'high'),
    (r'MemoryPressure', 'high'),
    (r'PIDPressure', 'high'),
    (r'eviction.*threshold', 'medium'),
    (r'Taint.*NoSchedule', 'medium'),
    (r'Taint.*NoExecute', 'high'),
    (r'OOMKilled', 'high'),
    (r'Memory cgroup out of memory', 'high'),
    (r'invoked oom-killer', 'high'),
    (r'Killed process.*total-vm', 'high'),
    (r'exit code 137', 'high'),
    (r'CrashLoopBackOff', 'high'),
    (r'Back-off restarting failed container', 'medium'),
    (r'Evicted', 'high'),
    (r'PodEvicted', 'high'),
]

# Category C: CNI/Networking Issues
TRIAGE_CNI_NETWORK_PATTERNS = [
    (r'InsufficientFreeAddressesInSubnet', 'high'),
    (r'failed to assign an IP address', 'high'),
    (r'no free IP addresses', 'high'),
    (r'ENI.*allocation.*failed', 'high'),
    (r'ipamd.*error', 'high'),
    (r'aws-node.*error', 'medium'),
    (r'no networks found in /etc/cni/net\.d', 'high'),
    (r'CNI.*failed', 'high'),
    (r'plugin.*returned.*error', 'medium'),
    (r'failed to set up sandbox container.*network', 'high'),
    (r'NetworkNotReady', 'high'),
    (r'networkPlugin cni failed', 'high'),
    (r'SNAT.*error', 'medium'),
    (r'egress.*failed', 'medium'),
]

# Category D: iptables/conntrack/kube-proxy
TRIAGE_IPTABLES_CONNTRACK_PATTERNS = [
    (r'ip_conntrack: table full', 'high'),
    (r'nf_conntrack: table full', 'high'),
    (r'dropping packet', 'high'),
    (r'iptables.*error', 'medium'),
    (r'iptables-restore.*failed', 'high'),
    (r'kube-proxy.*error', 'medium'),
    (r'IPVS.*error', 'medium'),
    (r'conntrack.*exhausted', 'high'),
    (r'nf_conntrack_max', 'medium'),
]

# Category E: Scheduling Constraints
TRIAGE_SCHEDULING_PATTERNS = [
    (r'FailedScheduling', 'high'),
    (r'Insufficient cpu', 'high'),
    (r'Insufficient memory', 'high'),
    (r'Insufficient pods', 'medium'),
    (r'node\(s\) didn\'t match.*selector', 'high'),
    (r'node\(s\) had.*taint', 'high'),
    (r'node\(s\) didn\'t have free ports', 'medium'),
    (r'PodToleratesNodeTaints', 'medium'),
    (r'NodeAffinity', 'medium'),
    (r'PodAffinity', 'medium'),
    (r'0/\d+ nodes are available', 'high'),
    (r'Unschedulable', 'high'),
]

# Category F: Image Pull/Auth Issues
TRIAGE_IMAGE_PULL_PATTERNS = [
    (r'ImagePullBackOff', 'high'),
    (r'ErrImagePull', 'high'),
    (r'Failed to pull image', 'high'),
    (r'unauthorized.*authentication required', 'high'),
    (r'manifest.*not found', 'high'),
    (r'repository does not exist', 'high'),
    (r'denied.*access', 'high'),
    (r'ECR.*token.*expired', 'high'),
    (r'dial tcp.*connection refused.*registry', 'medium'),
    (r'no such host.*ecr', 'high'),
    (r'i/o timeout.*registry', 'medium'),
    (r'pull access denied', 'high'),
]

# Category G: DNS/CoreDNS Issues
TRIAGE_DNS_PATTERNS = [
    (r'CoreDNS.*error', 'high'),
    (r'SERVFAIL', 'medium'),
    (r'NXDOMAIN', 'medium'),
    (r'lookup.*failed', 'medium'),
    (r'no such host', 'medium'),
    (r'DNS.*timeout', 'high'),
    (r'resolve.*failed', 'medium'),
    (r'dial udp.*53.*timeout', 'high'),
    (r'upstream.*unreachable', 'high'),
    (r'coredns.*unhealthy', 'high'),
]

# Category H: Secrets/KMS/Webhook/Admission
TRIAGE_SECRETS_WEBHOOK_PATTERNS = [
    (r'failed to get secret', 'high'),
    (r'secrets.*not found', 'high'),
    (r'secrets.*forbidden', 'high'),
    (r'KMS.*error', 'high'),
    (r'decrypt.*failed', 'high'),
    (r'webhook.*timeout', 'high'),
    (r'webhook.*denied', 'high'),
    (r'admission.*rejected', 'high'),
    (r'MutatingWebhook.*error', 'medium'),
    (r'ValidatingWebhook.*error', 'medium'),
    (r'CreateContainerConfigError', 'high'),
]

# All triage categories with metadata
TRIAGE_CATEGORIES = {
    'A': {
        'name': 'Volume/CSI Mount Issues',
        'patterns': TRIAGE_VOLUME_CSI_PATTERNS,
        'log_sources': ['storage', 'kubelet', 'dmesg', 'messages', 'ebs-csi', 'efs-csi'],
        'description': 'EBS/EFS CSI driver failures, mount timeouts, permission denied, PVC/PV mismatch'
    },
    'B': {
        'name': 'Worker Node Issues',
        'patterns': TRIAGE_NODE_ISSUES_PATTERNS,
        'log_sources': ['kubelet', 'dmesg', 'messages', 'containerd', 'docker'],
        'description': 'kubelet issues, containerd/runtime issues, disk full, memory pressure, node not ready, OOMKilled'
    },
    'C': {
        'name': 'CNI/Networking Issues',
        'patterns': TRIAGE_CNI_NETWORK_PATTERNS,
        'log_sources': ['ipamd', 'aws-node', 'networking', 'cni', 'plugin.log'],
        'description': 'VPC CNI IP exhaustion, ENI allocation failures, aws-node errors, SNAT/egress issues'
    },
    'D': {
        'name': 'iptables/conntrack/kube-proxy',
        'patterns': TRIAGE_IPTABLES_CONNTRACK_PATTERNS,
        'log_sources': ['networking', 'iptables', 'conntrack', 'dmesg', 'messages'],
        'description': 'conntrack exhaustion, kube-proxy rule failures, iptables restore errors'
    },
    'E': {
        'name': 'Scheduling Constraints',
        'patterns': TRIAGE_SCHEDULING_PATTERNS,
        'log_sources': ['kubelet', 'pods'],
        'description': 'insufficient CPU/memory, affinity/nodeSelector mismatch, taints/tolerations mismatch'
    },
    'F': {
        'name': 'Image Pull/Auth Issues',
        'patterns': TRIAGE_IMAGE_PULL_PATTERNS,
        'log_sources': ['kubelet', 'containerd', 'docker'],
        'description': 'registry auth, ECR token, DNS resolution, throttling'
    },
    'G': {
        'name': 'DNS/CoreDNS Issues',
        'patterns': TRIAGE_DNS_PATTERNS,
        'log_sources': ['coredns', 'networking', 'kubelet', 'pods'],
        'description': 'CoreDNS failures, upstream timeouts, NXDOMAIN storms'
    },
    'H': {
        'name': 'Secrets/KMS/Webhook/Admission',
        'patterns': TRIAGE_SECRETS_WEBHOOK_PATTERNS,
        'log_sources': ['kubelet', 'messages', 'secure'],
        'description': 'secrets retrieval errors, webhook timeout/deny'
    },
}

# Pod state patterns for detection
POD_STATE_PATTERNS = {
    'Pending': [r'Pod.*Pending', r'status.*Pending', r'phase.*Pending'],
    'ContainerCreating': [r'ContainerCreating', r'creating container'],
    'CrashLoopBackOff': [r'CrashLoopBackOff', r'Back-off restarting failed container'],
    'ImagePullBackOff': [r'ImagePullBackOff', r'ErrImagePull'],
    'OOMKilled': [r'OOMKilled', r'exit code 137', r'Memory cgroup out of memory'],
    'Evicted': [r'Evicted', r'PodEvicted', r'eviction'],
    'Error': [r'RunContainerError', r'CreateContainerError', r'CreateContainerConfigError'],
}

# Node condition patterns
NODE_CONDITION_PATTERNS = {
    'NotReady': [r'NodeNotReady', r'Node became not ready', r'condition.*Ready.*False'],
    'DiskPressure': [r'DiskPressure', r'disk pressure'],
    'MemoryPressure': [r'MemoryPressure', r'memory pressure'],
    'PIDPressure': [r'PIDPressure', r'pid pressure'],
    'NetworkUnavailable': [r'NetworkUnavailable', r'NetworkNotReady'],
}


# Log type to file pattern mapping
# Aligned with official EKS log collector: https://github.com/awslabs/amazon-eks-ami/blob/main/log-collector-script/
LOG_TYPE_PATTERNS = {
    # Core Kubernetes
    'kubelet': ['kubelet', 'kube-proxy', 'kubelet-config', 'kubeconfig'],
    'containerd': ['containerd', 'containerd-config', 'containerd-log', 'containerd-version', 
                   'containerd-namespaces', 'containerd-images', 'containerd-containers', 
                   'containerd-tasks', 'containerd-plugins'],
    'docker': ['docker', 'daemon.json', 'docker-info', 'docker-ps', 'docker-images', 
               'docker-version', 'docker-trace'],
    
    # System logs
    'dmesg': ['dmesg'],
    'kernel': ['kernel', 'dmesg', 'uname'],
    'messages': ['messages', 'syslog'],
    'system': ['messages', 'syslog', 'secure', 'audit', 'cron', 'cloud-init', 
               'cloud-init-output', 'user-data', 'pkglist', 'services', 'top', 
               'ps', 'netstat', 'procstat', 'instance-id', 'region', 'selinux',
               'cpu_throttling', 'io_throttling', 'last_reboot', 'large_environments'],
    'security': ['secure', 'audit', 'selinux'],
    
    # Networking
    'networking': ['networking', 'iptables', 'ip6tables', 'conntrack', 'conntrack6',
                   'iproute', 'ip6route', 'iprule', 'ip6rule', 'resolv', 'ifconfig',
                   'ipvsadm', 'ipset', 'ethtool', 'systemd-network', 'curl_api_server',
                   'configure-multicard-interfaces', 'ebpf-data', 'ebpf-maps-data'],
    
    # Storage
    'storage': ['storage', 'mount', 'lsblk', 'xfs', 'fstab', 'inodes', 'lvs', 'pvs', 
                'vgs', 'pod_local_storage', 'ebs-csi', 'efs-csi', 'fsx-csi', 
                'fsx-openzfs-csi', 'file-cache-csi', 's3-csi', 'mount-s3'],
    
    # AWS VPC CNI / IPAMD
    'ipamd': ['ipamd', 'aws-routed-eni', 'cni', 'plugin.log', 'network-policy',
              'enis.json', 'pods.json', 'networkutils-env-settings', 'ipamd-env-settings',
              'eni-configs', 'metrics.json', 'cni-configuration-variables'],
    
    # Pod/Container logs
    'pods': ['pods/', 'containers/'],
    'aws-node': ['aws-node', 'cni-metrics-helper'],
    'coredns': ['coredns'],
    
    # EKS-specific
    'nodeadm': ['nodeadm', 'nodeadm-config', 'nodeadm-run', 'nodeadm-boot-hook', 
                'udev-net-manager'],
    'sandbox-image': ['sandbox-image'],
    'eks-agents': ['eks-pod-identity-agent', 'eks-node-monitoring-agent'],
    
    # Configuration
    'config': ['kubelet-config', 'config.json', 'config.toml', 'kubeconfig', 
               'kubelet_service', 'kubelet-eks_service'],
    
    # Module/Kernel info
    'modinfo': ['modinfo', 'lustre', 'ip_vs', 'nf_conntrack'],
    'sysctls': ['sysctls', 'sysctl_all'],
    
    # GPU
    'gpu': ['gpu', 'nvidia-bug-report'],
    
    # Advanced networking
    'multus': ['multus', 'kube-multus'],
    'soci-snapshotter': ['soci-snapshotter', 'soci-snapshotter-status', 'soci-snapshotter-log'],
    
    # Throttling analysis
    'throttling': ['cpu_throttling', 'io_throttling'],
}


# =============================================================================
# SOP (Standard Operating Procedure) KEYWORD MAPPING
# Maps issue patterns, triage categories, and diagnostic findings to SOP runbook filenames.
# Used by quick_triage, network_diagnostics, and storage_diagnostics to automatically
# recommend relevant SOPs without requiring the user to mention "SOP" in their prompt.
# =============================================================================

SOP_KEYWORD_MAP = {
    # ── Node readiness / bootstrap ──
    'node_not_ready': [
        {'sop': 'runbooks/A1-node-not-ready-kubelet-oom.md', 'keywords': ['NotReady', 'node not ready', 'OOMKilled', 'kubelet.*oom', 'memory cgroup out of memory'], 'relevance': 'primary'},
        {'sop': 'runbooks/A2-node-not-ready-certificate-expired.md', 'keywords': ['certificate.*expir', 'x509.*certificate', 'tls.*handshake'], 'relevance': 'primary'},
        {'sop': 'runbooks/A2-node-bootstrap-registration-failure.md', 'keywords': ['bootstrap', 'registration.*fail', 'node.*register', 'nodeadm'], 'relevance': 'primary'},
        {'sop': 'runbooks/A3-clock-skew.md', 'keywords': ['clock.*skew', 'time.*sync', 'ntp', 'chrony', 'certificate.*not yet valid'], 'relevance': 'primary'},
        {'sop': 'runbooks/A4-worker-node-join-failure.md', 'keywords': ['join.*fail', 'worker.*join', 'aws-auth', 'configmap.*aws-auth', 'unauthorized'], 'relevance': 'primary'},
    ],
    # ── Kubelet / runtime ──
    'kubelet_runtime': [
        {'sop': 'runbooks/B1-kubelet-configuration-errors.md', 'keywords': ['kubelet.*config', 'kubelet.*error', 'kubelet.*fail', 'flag.*not recognized'], 'relevance': 'primary'},
        {'sop': 'runbooks/B2-eviction-manager-issues.md', 'keywords': ['evict', 'eviction', 'DiskPressure', 'MemoryPressure', 'ephemeral.*storage'], 'relevance': 'primary'},
        {'sop': 'runbooks/B3-pleg-issues.md', 'keywords': ['PLEG', 'pod lifecycle', 'GenericPLEG', 'relist.*slow'], 'relevance': 'primary'},
    ],
    # ── Container / image ──
    'container_image': [
        {'sop': 'runbooks/C1-image-pull-failures.md', 'keywords': ['ImagePullBackOff', 'ErrImagePull', 'pull.*access.*denied', 'manifest.*not found', 'ecr.*token'], 'relevance': 'primary'},
        {'sop': 'runbooks/C2-sandbox-creation-failures.md', 'keywords': ['sandbox.*creat', 'RunPodSandbox', 'sandbox.*fail', 'network.*sandbox'], 'relevance': 'primary'},
        {'sop': 'runbooks/C3-overlayfs-inode-exhaustion.md', 'keywords': ['inode', 'no space left', 'overlayfs', 'overlay.*error', 'disk.*full'], 'relevance': 'primary'},
        {'sop': 'runbooks/K4-containerd-runtime-failures.md', 'keywords': ['containerd', 'runtime.*error', 'container.*runtime', 'runc', 'shim.*error'], 'relevance': 'primary'},
    ],
    # ── Networking / CNI ──
    'networking_cni': [
        {'sop': 'runbooks/D1-vpc-cni-ip-allocation-failures.md', 'keywords': ['ip.*alloc', 'no available ip', 'ipamd', 'ip exhaustion', 'subnet.*full', 'ENI.*fail', 'warm.*ip'], 'relevance': 'primary'},
        {'sop': 'runbooks/D2-kube-proxy-iptables-sync.md', 'keywords': ['kube-proxy', 'iptables.*sync', 'iptables.*restore', 'KUBE-SVC', 'sync.*rules.*fail'], 'relevance': 'primary'},
        {'sop': 'runbooks/D3-conntrack-exhaustion.md', 'keywords': ['conntrack', 'nf_conntrack', 'table full', 'dropping packet'], 'relevance': 'primary'},
        {'sop': 'runbooks/D4-mtu-fragmentation.md', 'keywords': ['mtu', 'fragmentation', 'packet.*too.*large', 'pmtu', 'jumbo'], 'relevance': 'primary'},
        {'sop': 'runbooks/D5-dns-failures.md', 'keywords': ['dns', 'coredns', 'SERVFAIL', 'NXDOMAIN', 'resolve.*fail', 'name.*resolution'], 'relevance': 'primary'},
        {'sop': 'runbooks/D6-ena-throttling.md', 'keywords': ['ena.*throttl', 'linklocal.*throttl', 'bw_in_allowance_exceeded', 'conntrack_allowance_exceeded'], 'relevance': 'primary'},
        {'sop': 'runbooks/D7-network-performance-degradation.md', 'keywords': ['network.*degrad', 'latency', 'packet.*loss', 'retransmit', 'tcp.*timeout'], 'relevance': 'primary'},
        {'sop': 'runbooks/D8-kube-proxy-service-connectivity.md', 'keywords': ['service.*connect', 'ClusterIP.*unreachable', 'service.*timeout', 'endpoint.*not.*found'], 'relevance': 'primary'},
        {'sop': 'runbooks/D9-pod-to-pod-connectivity.md', 'keywords': ['pod.*connect', 'pod.*unreachable', 'pod.*timeout', 'network.*policy.*deny'], 'relevance': 'primary'},
    ],
    # ── Storage / volumes ──
    'storage_volumes': [
        {'sop': 'runbooks/E1-ebs-csi-attach-mount-timeout.md', 'keywords': ['ebs.*csi', 'FailedAttachVolume', 'FailedMount', 'volume.*attach.*timeout', 'Multi-Attach'], 'relevance': 'primary'},
        {'sop': 'runbooks/E2-efs-mount-failures.md', 'keywords': ['efs', 'nfs.*mount', 'mount.*2049', 'efs.*timeout', 'access.*point'], 'relevance': 'primary'},
        {'sop': 'runbooks/K5-csi-node-plugin-failures.md', 'keywords': ['csi.*node', 'csi.*plugin', 'csi.*driver', 'agent-not-ready', 'NodeStageVolume', 'NodePublishVolume'], 'relevance': 'primary'},
        {'sop': 'runbooks/J2-ebs-transient-attach.md', 'keywords': ['ebs.*transient', 'volume.*detach', 'volume.*stuck', 'VolumeInUse'], 'relevance': 'primary'},
    ],
    # ── Scheduling / capacity ──
    'scheduling_capacity': [
        {'sop': 'runbooks/F1-insufficient-cpu-memory.md', 'keywords': ['Insufficient.*cpu', 'Insufficient.*memory', 'Unschedulable', 'FailedScheduling', 'resource.*quota'], 'relevance': 'primary'},
        {'sop': 'runbooks/F2-max-pods-limit.md', 'keywords': ['max.*pods', 'Too many pods', 'pod.*limit', 'max-pods'], 'relevance': 'primary'},
        {'sop': 'runbooks/F3-taints-tolerations-node-selectors.md', 'keywords': ['taint', 'toleration', 'nodeSelector', 'node.*affinity', 'NoSchedule', 'NoExecute'], 'relevance': 'primary'},
    ],
    # ── Resource pressure ──
    'resource_pressure': [
        {'sop': 'runbooks/G1-disk-pressure-eviction-storms.md', 'keywords': ['DiskPressure', 'disk.*pressure', 'eviction.*storm', 'imagefs', 'nodefs'], 'relevance': 'primary'},
        {'sop': 'runbooks/G2-oomkill-memory-pressure.md', 'keywords': ['OOMKill', 'oom_kill', 'MemoryPressure', 'memory.*pressure', 'cgroup.*oom', 'exit code 137'], 'relevance': 'primary'},
        {'sop': 'runbooks/G3-pid-pressure.md', 'keywords': ['PIDPressure', 'pid.*pressure', 'fork.*fail', 'cannot allocate memory', 'too many process'], 'relevance': 'primary'},
    ],
    # ── IAM / permissions ──
    'iam_permissions': [
        {'sop': 'runbooks/H1-node-role-missing-permissions.md', 'keywords': ['AccessDenied', 'not authorized', 'iam.*role', 'instance.*profile', 'sts.*assume'], 'relevance': 'primary'},
        {'sop': 'runbooks/H2-irsa-pod-identity-confusion.md', 'keywords': ['irsa', 'pod.*identity', 'service.*account.*token', 'oidc', 'web.*identity'], 'relevance': 'primary'},
        {'sop': 'runbooks/H3-imds-issues.md', 'keywords': ['imds', 'metadata.*service', '169.254.169.254', 'hop.*limit', 'IMDSv2'], 'relevance': 'primary'},
    ],
    # ── Version / compatibility ──
    'version_compat': [
        {'sop': 'runbooks/I1-version-skew.md', 'keywords': ['version.*skew', 'version.*mismatch', 'incompatible.*version', 'api.*version.*not.*supported'], 'relevance': 'primary'},
    ],
    # ── Hardware / instance ──
    'hardware_instance': [
        {'sop': 'runbooks/J1-ena-throttling-instance-limits.md', 'keywords': ['ena.*throttl', 'instance.*limit', 'bandwidth.*exceed', 'pps.*limit'], 'relevance': 'primary'},
        {'sop': 'runbooks/J2-ebs-transient-attach.md', 'keywords': ['ebs.*attach', 'volume.*attach.*limit', 'maximum.*volumes'], 'relevance': 'primary'},
        {'sop': 'runbooks/J3-az-outage-impact.md', 'keywords': ['az.*outage', 'availability.*zone', 'zone.*fail', 'regional.*issue'], 'relevance': 'primary'},
    ],
    # ── Pod lifecycle ──
    'pod_lifecycle': [
        {'sop': 'runbooks/K1-stuck-terminating-pods.md', 'keywords': ['Terminating', 'stuck.*terminat', 'finalizer', 'force.*delete', 'gracePeriod'], 'relevance': 'primary'},
        {'sop': 'runbooks/K2-probe-failures.md', 'keywords': ['probe.*fail', 'liveness.*fail', 'readiness.*fail', 'startup.*fail', 'Unhealthy'], 'relevance': 'primary'},
        {'sop': 'runbooks/K3-crashloopbackoff.md', 'keywords': ['CrashLoopBackOff', 'crash.*loop', 'Back-off restarting', 'exit code'], 'relevance': 'primary'},
    ],
}

# Map triage categories (A-H) to SOP keyword groups for quick_triage integration
TRIAGE_CATEGORY_TO_SOP_GROUP = {
    'A': ['storage_volumes'],
    'B': ['kubelet_runtime', 'node_not_ready', 'resource_pressure'],
    'C': ['networking_cni'],
    'D': ['networking_cni'],
    'E': ['scheduling_capacity'],
    'F': ['container_image'],
    'G': ['networking_cni'],  # DNS is under networking
    'H': ['iam_permissions'],
}


def match_sops_for_issues(issues: List[Dict], findings: List[Dict] = None,
                          triage_category: str = None, max_sops: int = 5) -> List[Dict]:
    """
    Match detected issues/findings against SOP runbooks.
    Returns a list of recommended SOPs with relevance and reason.

    Args:
        issues: List of issue dicts from diagnostics (each has 'message' and 'section')
        findings: Optional list of error findings (each has 'pattern', 'sample')
        triage_category: Optional triage category ID (A-H) from quick_triage root cause
        max_sops: Maximum SOPs to return
    """
    scored_sops = {}  # sop_name -> {score, reasons}

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
                # Score: 3 per keyword match, +5 bonus if from priority triage group
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


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def format_bytes(size: int) -> str:
    """Format bytes to human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def parse_failure_reason(execution: Dict) -> str:
    """Extract failure reason from SSM execution."""
    # Check for failure message in outputs
    outputs = execution.get('Outputs', {})
    if 'FailureMessage' in outputs:
        return outputs['FailureMessage']
    
    # Check step executions for failure
    for step in execution.get('StepExecutions', []):
        if step.get('StepStatus') == 'Failed':
            failure_msg = step.get('FailureMessage', '')
            if failure_msg:
                return f"Step '{step.get('StepName')}' failed: {failure_msg}"
    
    return execution.get('FailureMessage', 'Unknown failure reason')


def estimate_progress(execution: Dict) -> int:
    """Estimate progress percentage from step executions."""
    steps = execution.get('StepExecutions', [])
    if not steps:
        return 10  # Started but no steps yet
    
    total_steps = len(steps)
    completed_steps = sum(1 for s in steps if s.get('StepStatus') in ['Success', 'Failed', 'Cancelled'])
    
    if total_steps == 0:
        return 10
    
    return min(95, int((completed_steps / total_steps) * 100))


def store_execution_region(execution_id: str, region: str) -> bool:
    """Store the region where an SSM execution was started, so subsequent calls can find it.
    Returns True if stored successfully, False otherwise."""
    key = f'execution-regions/{execution_id}.json'
    mapping = {
        'executionId': execution_id,
        'region': region,
        'createdAt': datetime.utcnow().isoformat()
    }
    for attempt in range(2):
        try:
            s3_client.put_object(
                Bucket=LOGS_BUCKET,
                Key=key,
                Body=json.dumps(mapping),
                ContentType='application/json'
            )
            return True
        except Exception as e:
            print(f"Warning: Failed to store execution region mapping (attempt {attempt + 1}): {str(e)}")
    return False


def get_execution_region(execution_id: str) -> Optional[str]:
    """Retrieve the region where an SSM execution was started."""
    key = f'execution-regions/{execution_id}.json'
    result = safe_s3_read(key)
    if result['success']:
        try:
            mapping = json.loads(result['content'])
            return mapping.get('region')
        except Exception:
            pass
    return None


def find_execution_by_idempotency_token(instance_id: str, token: str) -> Optional[Dict]:
    """Find existing execution by idempotency token, using the correct regional SSM client."""
    key = f'idempotency/{instance_id}/{token}.json'
    result = safe_s3_read(key)
    
    if result['success']:
        try:
            mapping = json.loads(result['content'])
            execution_id = mapping.get('executionId')
            
            # Look up which region this execution lives in
            exec_region = get_execution_region(execution_id) or DEFAULT_REGION
            regional_ssm = get_regional_client('ssm', exec_region)
            
            try:
                response = regional_ssm.get_automation_execution(
                    AutomationExecutionId=execution_id
                )
                return {
                    'executionId': execution_id,
                    'status': response['AutomationExecution']['AutomationExecutionStatus']
                }
            except Exception:
                return None
        except Exception:
            return None
    
    return None


def store_idempotency_mapping(instance_id: str, token: str, execution_id: str):
    """Store idempotency token to execution mapping with conditional write to prevent races."""
    key = f'idempotency/{instance_id}/{token}.json'
    mapping = {
        'executionId': execution_id,
        'instanceId': instance_id,
        'token': token,
        'createdAt': datetime.utcnow().isoformat()
    }
    
    try:
        # Use IfNoneMatch='*' to prevent overwriting existing mappings (S3 conditional write).
        # This prevents race conditions where concurrent invocations could corrupt state.
        s3_client.put_object(
            Bucket=LOGS_BUCKET,
            Key=key,
            Body=json.dumps(mapping),
            ContentType='application/json',
            IfNoneMatch='*',
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'PreconditionFailed':
            # Another invocation already wrote this mapping — that's fine
            logger.info(f"Idempotency mapping already exists for {token}, skipping write")
        else:
            logger.warning(f"Failed to store idempotency mapping: {str(e)}")
    except Exception as e:
        logger.warning(f"Failed to store idempotency mapping: {str(e)}")


# ========================================================================
# Baseline Subtraction (S3-based)
# ========================================================================

BASELINE_PREFIX = 'baselines/'
BASELINE_THRESHOLD = 10  # pattern must appear 10+ times to be considered baseline


def load_baselines(cluster_name: str) -> Dict[str, Dict]:
    """
    Load baseline patterns for a cluster from S3.
    Baselines are stored as: baselines/{cluster_name}/patterns.json
    Returns {pattern_string: {count, first_seen, last_seen, is_baseline}}.
    """
    if not cluster_name:
        return {}
    key = f'{BASELINE_PREFIX}{cluster_name}/patterns.json'
    try:
        result = safe_s3_read(key)
        if result['success']:
            return json.loads(result['content'])
    except Exception:
        pass
    return {}


def _load_baselines_with_version(cluster_name: str) -> tuple:
    """
    Load baselines along with the S3 object's VersionId so update_baselines can
    perform an optimistic-concurrency write (If-Match on the version). Returns
    (baselines, version_id_or_None). Missing object returns ({}, None).
    """
    if not cluster_name:
        return {}, None
    key = f'{BASELINE_PREFIX}{cluster_name}/patterns.json'
    try:
        resp = s3_client.get_object(Bucket=LOGS_BUCKET, Key=key)
        body = resp['Body'].read()
        try:
            content = body.decode('utf-8')
        except UnicodeDecodeError:
            content = body.decode('latin-1', errors='replace')
        version_id = resp.get('VersionId')
        try:
            return json.loads(content), version_id
        except Exception:
            return {}, version_id
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        if code in ('NoSuchKey', '404'):
            return {}, None
        raise
    except Exception:
        return {}, None


def update_baselines(cluster_name: str, findings: List[Dict]):
    """
    Increment baseline counters for observed patterns and persist to S3.

    Race-safe: uses optimistic concurrency control. Reads the current
    VersionId, applies the increments locally, then PUTs with `IfMatch` set to
    that VersionId. On `PreconditionFailed` we re-read and retry up to a small
    number of attempts. Bucket already has versioning enabled in the construct,
    so this gives correct cross-invocation counter updates without an external
    lock.
    """
    if not cluster_name or not findings:
        return

    key = f'{BASELINE_PREFIX}{cluster_name}/patterns.json'
    max_attempts = 5

    for attempt in range(max_attempts):
        baselines, version_id = _load_baselines_with_version(cluster_name)
        now_iso = datetime.utcnow().isoformat()

        for f in findings:
            pattern = f.get('pattern', '')
            if not pattern:
                continue
            if pattern not in baselines:
                baselines[pattern] = {
                    'count': 0,
                    'first_seen': now_iso,
                    'last_seen': now_iso,
                    'is_baseline': False,
                }
            entry = baselines[pattern]
            entry['count'] = entry.get('count', 0) + 1
            entry['last_seen'] = now_iso
            if entry['count'] >= BASELINE_THRESHOLD:
                entry['is_baseline'] = True

        body = json.dumps(baselines, default=str)
        put_kwargs = {
            'Bucket': LOGS_BUCKET,
            'Key': key,
            'Body': body,
            'ContentType': 'application/json',
        }
        # If the object exists, require its current VersionId to match. If it
        # didn't exist on read, require IfNoneMatch='*' to ensure we don't
        # clobber a concurrent first-create.
        if version_id:
            put_kwargs['IfMatch'] = version_id
        else:
            put_kwargs['IfNoneMatch'] = '*'

        try:
            s3_client.put_object(**put_kwargs)
            return
        except ClientError as e:
            code = e.response.get('Error', {}).get('Code', '')
            if code in ('PreconditionFailed', 'ConditionalRequestConflict'):
                # Lost the race — re-read and retry.
                logger.info(
                    f"Baseline update conflict for {cluster_name} (attempt {attempt + 1}/{max_attempts}); retrying."
                )
                # Tiny backoff to avoid lockstep retries.
                time.sleep(0.05 * (attempt + 1))
                continue
            logger.warning(f"Failed to update baselines for {cluster_name}: {e}")
            return
        except Exception as e:
            logger.warning(f"Failed to update baselines for {cluster_name}: {e}")
            return

    logger.warning(
        f"Baseline update for {cluster_name} gave up after {max_attempts} attempts due to concurrent writes."
    )


def annotate_findings_with_baselines(findings: List[Dict], cluster_name: str) -> List[Dict]:
    """
    Annotate each finding with is_baseline and baseline_note if the pattern
    is a known baseline for this cluster.
    """
    if not cluster_name:
        return findings
    baselines = load_baselines(cluster_name)
    if not baselines:
        return findings

    for f in findings:
        pattern = f.get('pattern', '')
        baseline = baselines.get(pattern)
        if baseline and baseline.get('is_baseline'):
            f['is_baseline'] = True
            f['baseline_note'] = (
                f"This pattern has been seen {baseline['count']} times "
                f"across cluster {cluster_name} since {baseline.get('first_seen', 'unknown')}. "
                f"Likely normal operation."
            )
        else:
            f['is_baseline'] = False

    return findings


def find_findings_index(prefix: str) -> Optional[str]:
    """
    Find the findings index file for a log collection.
    Searches for the most recent findings_index.json file in the LATEST bundle only.
    
    Prefix format: eks_{instance_id} (without trailing slash or execution_id)
    Actual S3 structure: eks_{instance_id}_{execution_id}/extracted/findings_index.json
    """
    # Extract instance_id from prefix (e.g., "eks_i-0014be4d4a0ea0543" -> "i-0014be4d4a0ea0543")
    parts = prefix.split('_', 1)
    instance_id = parts[1] if len(parts) > 1 else prefix
    scheme = parts[0] if len(parts) > 1 else 'eks'
    
    bundle_info = find_latest_bundle_files(instance_id, prefix_scheme=scheme)
    if not bundle_info['success']:
        return None
    
    # Find findings index in the latest bundle only
    index_files = [f for f in bundle_info['files'] if FINDINGS_INDEX_FILE in f]
    return index_files[0] if index_files else None


def scan_and_index_errors(instance_id: str, severity_filter: str, time_window: Dict = None) -> Dict:
    """Scan logs and build error index on-demand, filtered by time window."""
    prefix = f'eks_{instance_id}'
    
    # Use shared latest-bundle discovery
    bundle_info = find_latest_bundle_files(instance_id)
    
    if not bundle_info['success']:
        return success_response({
            'instanceId': instance_id,
            'findings': [],
            'totalFindings': 0,
            'summary': {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0},
            'cached': False,
            'warning': bundle_info.get('error', 'Failed to list log files'),
            **(TimeWindowResolver.window_metadata(time_window) if time_window else {}),
        })
    
    # Build obj-like dicts for size filtering (need size info from all_objects)
    latest_keys = set(bundle_info['files'])
    size_map = {obj['key']: obj['size'] for obj in bundle_info['all_objects']}
    
    # Filter for text files in latest extracted bundle only
    files_to_scan = [
        {'key': k, 'size': size_map.get(k, 0)}
        for k in latest_keys
        if not any(k.endswith(ext) for ext in ['.tar.gz', '.zip', '.gz', '.bin', '.so'])
        and size_map.get(k, 0) < 10485760  # Skip files >10MB
    ]
    
    findings = []
    summary = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0}
    
    # Parallel file scanning — up to 10 concurrent S3 reads
    files_batch = files_to_scan[:100]
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_key = {executor.submit(scan_file_for_errors, fi['key']): fi['key'] for fi in files_batch}
        for future in as_completed(future_to_key):
            try:
                file_findings = future.result()
                for finding in file_findings:
                    severity = finding.get('severity', 'info')
                    summary[severity] = summary.get(severity, 0) + 1
                    if severity_filter == 'all' or severity in normalize_severity_filter(severity_filter):
                        findings.append(finding)
            except Exception:
                pass  # Skip files that fail to scan
    
    # Sort by severity
    findings.sort(key=lambda x: SEVERITY_ORDER.get(x.get('severity', 'info'), 4))
    

    # A CRITICAL finding is confirmed if the same pattern appears in 2+ files
    # or if a corroborating pattern exists (e.g., OOM kill + exit code 137)
    critical_patterns = {}
    for f in findings:
        if f.get('severity') == 'critical':
            pat = f.get('pattern', '')
            if pat not in critical_patterns:
                critical_patterns[pat] = []
            critical_patterns[pat].append(f.get('file', ''))
    
    for f in findings:
        if f.get('severity') == 'critical':
            pat = f.get('pattern', '')
            sources = set(critical_patterns.get(pat, []))
            f['confirmed'] = len(sources) >= 2
            f['signal_sources'] = len(sources)
    
    
    now_iso = datetime.utcnow().isoformat()
    for f in findings:
        # Try to extract timestamps from sample match lines
        sample_line = f.get('sample', '')
        ts = extract_timestamp(sample_line) if sample_line else None
        f['first_seen'] = ts or now_iso
        f['last_seen'] = ts or now_iso
    
    # Assign finding_ids
    for idx, finding in enumerate(findings):
        finding['finding_id'] = assign_finding_id(idx + 1)
    
    # ── Time-window filtering ──
    tw_meta = {}
    excluded_outside_window = 0
    unparseable_timestamps = 0
    if time_window:
        tw_result = TimeWindowResolver.filter_findings_by_window(findings, time_window)
        findings = tw_result['findings']
        excluded_outside_window = tw_result['excluded_outside_window']
        unparseable_timestamps = tw_result['unparseable_timestamps']
        tw_meta = TimeWindowResolver.window_metadata(time_window)
    
    return success_response({
        'instanceId': instance_id,
        'findings': findings[:100],
        'totalFindings': len(findings),
        'summary': summary,
        'cached': False,
        'indexedAt': datetime.utcnow().isoformat(),
        'coverage_report': {
            'files_scanned': len(files_batch),
            'files_available': len(files_to_scan),
            'files_skipped_size': len([f for f in list_result['objects'] if '/extracted/' in f['key'] and f['size'] >= 10485760]),
            'scan_complete': len(files_batch) >= len(files_to_scan),
        },
        **tw_meta,
        **(
            {
                'time_window_filter': {
                    'excluded_outside_window': excluded_outside_window,
                    'unparseable_timestamps': unparseable_timestamps,
                    'total_before_filter': excluded_outside_window + len(findings),
                }
            }
            if time_window else {}
        ),
    })


def scan_file_for_errors(key: str) -> List[Dict]:
    """Scan a single file for error patterns."""
    findings = []
    
    # Read file content
    read_result = safe_s3_read(key, max_size=5242880)  # 5MB max
    
    if not read_result['success']:
        return findings
    
    content = read_result['content']
    lines = content.split('\n')
    filename = key.split('/extracted/')[-1] if '/extracted/' in key else key
    
 
    # These patterns indicate the match is informational, not an actual error
    FALSE_POSITIVE_CONTEXTS = [
        re.compile(r'(resolv\.conf|/etc/resolv)', re.IGNORECASE),  # Node resolv.conf is normal
        re.compile(r'(--help|usage:|man\s+page)', re.IGNORECASE),  # Help text
        re.compile(r'(example|sample|template|default)', re.IGNORECASE),  # Example/template text
        re.compile(r'(test|mock|fake|stub)', re.IGNORECASE),  # Test artifacts
        re.compile(r'Successfully\s+', re.IGNORECASE),  # Success messages containing error keywords
    ]
    
    # Track patterns found to avoid duplicates
    found_patterns = {}
    MAX_FINDINGS_PER_FILE = 20
    
    for severity, compiled_patterns in COMPILED_ERROR_PATTERNS.items():
        if len(findings) >= MAX_FINDINGS_PER_FILE:
            break
        for regex in compiled_patterns:
            if len(findings) >= MAX_FINDINGS_PER_FILE:
                break
            matches = []
            
            for i, line in enumerate(lines):
                if regex.search(line):
                  
                    is_false_positive = False
                    for fp_re in FALSE_POSITIVE_CONTEXTS:
                        if fp_re.search(line):
                            is_false_positive = True
                            break
                    if is_false_positive:
                        continue
                    
                    matches.append({
                        'lineNumber': i + 1,
                        'line': line[:500]  # Limit line length
                    })
                    if len(matches) >= 5:  # Cap match samples per pattern
                        break
            
            if matches:
                pattern_key = f"{filename}:{regex.pattern}"
                if pattern_key not in found_patterns:
                    found_patterns[pattern_key] = True
                    findings.append({
                        'file': filename,
                        'fullKey': key,
                        'pattern': regex.pattern,
                        'severity': severity.value,
                        'count': len(matches),
                        'sample': matches[0]['line'] if matches else ''
                    })
    
    return findings


def read_by_lines(key: str, start_line: int, line_count: int, total_size: int) -> Dict:
    """Read file by line numbers instead of byte range."""
    # For line-based reading, we need to read the whole file (up to a limit)
    max_read = min(total_size, 10485760)  # 10MB max for line reading
    
    read_result = safe_s3_read(key, max_size=max_read)
    
    if not read_result['success']:
        return success_response({
            'logKey': key,
            'content': '',
            'startLine': start_line,
            'lineCount': 0,
            'totalLines': 0,
            'hasMore': False,
            'warning': read_result.get('error', 'Failed to read file')
        })
    
    lines = read_result['content'].split('\n')
    total_lines = len(lines)
    
    # Handle negative start_line (from end)
    if start_line < 0:
        start_line = max(0, total_lines + start_line)
    else:
        start_line = max(0, start_line - 1)  # Convert to 0-indexed
    
    end_line = min(start_line + line_count, total_lines)
    selected_lines = lines[start_line:end_line]
    
    return success_response({
        'logKey': key,
        'content': '\n'.join(selected_lines),
        'startLine': start_line + 1,  # Convert back to 1-indexed
        'endLine': end_line,
        'lineCount': len(selected_lines),
        'totalLines': total_lines,
        'hasMore': end_line < total_lines,
        'nextLineToken': str(end_line + 1) if end_line < total_lines else None,
        'truncated': False
    })


def search_file_for_pattern(key: str, pattern: re.Pattern, max_results: int, file_size: int = 0) -> Optional[List[Dict]]:
    """
    Search a single file for a regex pattern.
    For files >5MB, reads in chunks with overlap to avoid missing matches at boundaries.
    """
    CHUNK_READ_SIZE = 5242880  # 5MB per chunk
    OVERLAP = 4096  # 4KB overlap between chunks to catch boundary matches

    # Small file: read all at once (original fast path)
    if file_size <= CHUNK_READ_SIZE:
        read_result = safe_s3_read(key, max_size=CHUNK_READ_SIZE)
        if not read_result['success']:
            return None
        matches = []
        lines = read_result['content'].split('\n')
        for i, line in enumerate(lines):
            if pattern.search(line):
                matches.append({
                    'lineNumber': i + 1,
                    'line': line[:500],
                    'context': get_line_context(lines, i, 2)
                })
                if len(matches) >= max_results:
                    break
        return matches

    # Large file: chunked reading
    matches = []
    offset = 0
    global_line_offset = 0
    seen_lines = set()  # Deduplicate matches in overlap regions

    while offset < file_size and len(matches) < max_results:
        end = min(offset + CHUNK_READ_SIZE, file_size)
        range_header = f'bytes={offset}-{end - 1}'
        read_result = safe_s3_read(key, range_bytes=range_header)

        if not read_result['success']:
            break

        chunk = read_result['content']
        lines = chunk.split('\n')

        # If not the first chunk, skip the first (potentially partial) line
        start_idx = 1 if offset > 0 else 0
        # If not the last chunk, skip the last (potentially partial) line
        end_idx = len(lines) - 1 if end < file_size else len(lines)

        for i in range(start_idx, end_idx):
            line = lines[i]
            line_num = global_line_offset + i + 1
            if pattern.search(line):
                # Deduplicate across overlap regions
                line_key = f"{line_num}:{line[:100]}"
                if line_key not in seen_lines:
                    seen_lines.add(line_key)
                    # Build context from available lines in this chunk
                    ctx_before = lines[max(0, i - 2):i]
                    ctx_after = lines[i + 1:min(len(lines), i + 3)]
                    matches.append({
                        'lineNumber': line_num,
                        'line': line[:500],
                        'context': {'before': ctx_before, 'after': ctx_after}
                    })
                    if len(matches) >= max_results:
                        break

        # Advance: subtract overlap so we re-read boundary region
        global_line_offset += end_idx - start_idx
        next_offset = end - OVERLAP
        if next_offset <= offset:
            break
        offset = next_offset

    return matches


def get_line_context(lines: List[str], index: int, context_lines: int) -> Dict:
    """Get surrounding context lines."""
    start = max(0, index - context_lines)
    end = min(len(lines), index + context_lines + 1)
    
    return {
        'before': lines[start:index],
        'after': lines[index + 1:end]
    }


def extract_timestamp(line: str) -> Optional[str]:
    """Extract timestamp from a log line."""
    # Common timestamp patterns
    patterns = [
        r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})',  # ISO format
        r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})',  # Syslog format
        r'(\d{10,13})',  # Unix timestamp
    ]
    
    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            return match.group(1)
    
    return None


def categorize_log_source(filename: str) -> str:
    """Categorize a log file into a component."""
    filename_lower = filename.lower()
    
    if 'kubelet' in filename_lower:
        return 'kubelet'
    elif 'containerd' in filename_lower or 'docker' in filename_lower:
        return 'container-runtime'
    elif 'dmesg' in filename_lower or 'kernel' in filename_lower:
        return 'kernel'
    elif 'messages' in filename_lower or 'syslog' in filename_lower:
        return 'system'
    elif 'ipamd' in filename_lower or 'cni' in filename_lower or 'aws-node' in filename_lower:
        return 'networking'
    elif 'storage' in filename_lower or 'mount' in filename_lower:
        return 'storage'
    elif 'pods' in filename_lower or 'containers' in filename_lower:
        return 'pods'
    else:
        return 'other'


def find_correlations(timeline: List[Dict]) -> List[Dict]:
    """Find correlations between events in the timeline."""
    correlations = []
    
    # Group by component
    by_component = {}
    for event in timeline:
        component = categorize_log_source(event.get('source', ''))
        if component not in by_component:
            by_component[component] = []
        by_component[component].append(event)
    
    # Look for common patterns
    if 'kernel' in by_component and 'kubelet' in by_component:
        correlations.append({
            'type': 'kernel-kubelet',
            'description': 'Kernel issues may be affecting kubelet',
            'components': ['kernel', 'kubelet']
        })
    
    if 'networking' in by_component and 'kubelet' in by_component:
        correlations.append({
            'type': 'network-kubelet',
            'description': 'Network issues may be affecting kubelet communication',
            'components': ['networking', 'kubelet']
        })
    
    # Check for OOM patterns
    oom_events = [e for e in timeline if 'oom' in e.get('event', '').lower() or 'killed' in e.get('event', '').lower()]
    if oom_events:
        correlations.append({
            'type': 'memory-pressure',
            'description': f'Memory pressure detected ({len(oom_events)} OOM events)',
            'components': list(set(categorize_log_source(e.get('source', '')) for e in oom_events))
        })
    
    return correlations


def generate_recommendations(critical_findings: List[Dict], high_findings: List[Dict], warning_findings: List[Dict] = None) -> List[Dict]:
    """Generate remediation recommendations based on findings.
    
    Args:
        critical_findings: Findings with severity=critical
        high_findings: Findings with severity=high
        warning_findings: Deprecated, kept for backward compat. Merged into high_findings.
    """
    recommendations = []
    
    # Merge warning_findings into high_findings for backward compat
    all_high = list(high_findings or [])
    if warning_findings:
        all_high.extend(warning_findings)
    
    # Analyze critical findings
    for finding in critical_findings:
        pattern = finding.get('pattern', '').lower()
        
        if 'oom' in pattern or 'memory' in pattern:
            recommendations.append({
                'priority': 'high',
                'category': 'memory',
                'issue': 'Memory pressure detected',
                'action': 'Review pod resource limits and node capacity. Consider scaling up or adding nodes.',
                'evidence_finding_ids': [finding.get('finding_id')],
            })
        elif 'unauthorized' in pattern or 'denied' in pattern:
            recommendations.append({
                'priority': 'high',
                'category': 'auth',
                'issue': 'Authentication/authorization failures',
                'action': 'Check IAM roles, RBAC policies, and aws-auth ConfigMap.',
                'evidence_finding_ids': [finding.get('finding_id')],
            })
        elif 'cni' in pattern or 'ipamd' in pattern or 'network' in pattern:
            recommendations.append({
                'priority': 'high',
                'category': 'networking',
                'issue': 'CNI/networking issues detected',
                'action': 'Check VPC CNI plugin logs, subnet IP availability, and security groups.',
                'evidence_finding_ids': [finding.get('finding_id')],
            })
        elif 'pleg' in pattern:
            recommendations.append({
                'priority': 'high',
                'category': 'kubelet',
                'issue': 'PLEG (Pod Lifecycle Event Generator) issues',
                'action': 'Check for container runtime issues, disk I/O problems, or too many pods on node.',
                'evidence_finding_ids': [finding.get('finding_id')],
            })
    
    # Analyze high findings
    for finding in all_high:
        pattern = finding.get('pattern', '').lower()
        
        if 'crashloop' in pattern or 'restart' in pattern:
            recommendations.append({
                'priority': 'medium',
                'category': 'stability',
                'issue': 'Container restart loops detected',
                'action': 'Check container exit codes and previous logs (kubectl logs <pod> --previous).',
                'evidence_finding_ids': [finding.get('finding_id')],
            })
        elif 'scheduling' in pattern or 'insufficient' in pattern:
            recommendations.append({
                'priority': 'medium',
                'category': 'capacity',
                'issue': 'Scheduling constraints detected',
                'action': 'Review node capacity, resource requests/limits, and scheduling constraints.',
                'evidence_finding_ids': [finding.get('finding_id')],
            })
    
    # Remove duplicates by category
    seen = set()
    unique_recommendations = []
    for rec in recommendations:
        key = rec['category']
        if key not in seen:
            seen.add(key)
            unique_recommendations.append(rec)
    
    return unique_recommendations


# =============================================================================
# POD/NODE TRIAGE FUNCTIONS
# =============================================================================

def perform_pod_node_triage(instance_id: str, findings: List[Dict], bundle_data: Dict) -> Dict:
    """
    Perform pod/node failure triage analysis.
    
    Returns structured triage result with root cause, evidence, and remediation.
    """
    import time
    start_time = time.time()
    
    triage_result = {
        'triageVersion': '1.0',
        'analyzedAt': datetime.utcnow().isoformat(),
        'pod_states_detected': [],
        'node_conditions_detected': [],
        'most_likely_root_cause': None,
        'evidence': [],
        'secondary_hypotheses': [],
        'immediate_remediation_steps': [],
        'preventive_recommendations': [],
        'followup_validation_commands': [],
        'coverage_report': {
            'files_scanned': 0,
            'files_total': 0,
            'files_skipped': [],
            'categories_checked': list(TRIAGE_CATEGORIES.keys()),
            'categories_with_findings': [],
            'missing_log_sources': [],
            'scan_limitations': [],
            'scan_duration_ms': 0
        }
    }
    
    # PASS 1: Analyze pre-indexed findings by category (with temporal weighting)
    category_scores = {}
    category_evidence = {}

    # Collect all timestamps from findings to compute recency weights
    all_timestamps = []
    for finding in findings:
        ts = extract_timestamp(finding.get('sample', ''))
        if ts:
            try:
                all_timestamps.append(datetime.fromisoformat(ts.replace('Z', '+00:00').replace('+00:00', '')))
            except (ValueError, TypeError):
                pass

    reference_time = max(all_timestamps) if all_timestamps else datetime.utcnow()

    def _recency_weight(timestamp_str):
        """Return 1.0-2.0 multiplier: recent findings score higher."""
        if not timestamp_str:
            return 1.0
        try:
            ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00').replace('+00:00', ''))
            age_minutes = (reference_time - ts).total_seconds() / 60.0
            if age_minutes < 0:
                age_minutes = 0
            if age_minutes <= 5:
                return 2.0
            elif age_minutes <= 30:
                return 1.5
            elif age_minutes <= 120:
                return 1.2
            return 1.0
        except (ValueError, TypeError):
            return 1.0

    for cat_id, cat_info in TRIAGE_CATEGORIES.items():
        category_scores[cat_id] = {'score': 0, 'high_matches': 0, 'medium_matches': 0, 'patterns_matched': []}
        category_evidence[cat_id] = []
        
        for pattern, confidence in cat_info['patterns']:
            try:
                regex = re.compile(pattern, re.IGNORECASE)
                for finding in findings:
                    sample = finding.get('sample', '')
                    file_path = finding.get('file', '')
                    
                    if regex.search(sample) or regex.search(finding.get('pattern', '')):
                        ts_str = extract_timestamp(sample)
                        weight = _recency_weight(ts_str)

                        if confidence == 'high':
                            category_scores[cat_id]['score'] += int(3 * weight)
                            category_scores[cat_id]['high_matches'] += 1
                        else:
                            category_scores[cat_id]['score'] += int(1 * weight)
                            category_scores[cat_id]['medium_matches'] += 1
                        
                        category_scores[cat_id]['patterns_matched'].append(pattern)
                        
                        # Collect evidence
                        category_evidence[cat_id].append({
                            'file_path': file_path,
                            'full_key': finding.get('fullKey', ''),
                            'timestamp': ts_str,
                            'recency_weight': weight,
                            'line_number': None,  # Would need deep scan for this
                            'byte_range': None,
                            'excerpt': sample[:300] if sample else '',
                            'pattern_matched': pattern,
                            'relevance': 'primary' if confidence == 'high' else 'corroborating'
                        })
            except re.error:
                continue
    
    # PASS 2: Detect pod states
    pod_states = detect_pod_states(findings)
    triage_result['pod_states_detected'] = pod_states
    
    # PASS 3: Detect node conditions
    node_conditions = detect_node_conditions(findings)
    triage_result['node_conditions_detected'] = node_conditions
    
    # PASS 4: Determine root cause
    # Sort categories by score
    sorted_categories = sorted(
        category_scores.items(),
        key=lambda x: (x[1]['score'], x[1]['high_matches']),
        reverse=True
    )
    
    # Find categories with findings
    categories_with_findings = [
        cat_id for cat_id, scores in sorted_categories
        if scores['score'] > 0
    ]
    triage_result['coverage_report']['categories_with_findings'] = categories_with_findings
    
    if sorted_categories and sorted_categories[0][1]['score'] > 0:
        top_cat_id = sorted_categories[0][0]
        top_cat_info = TRIAGE_CATEGORIES[top_cat_id]
        top_scores = sorted_categories[0][1]
        
        # Calculate confidence
        confidence_score = min(0.99, top_scores['score'] / 10.0)
        if top_scores['high_matches'] >= 2:
            confidence = 'high'
            confidence_score = max(confidence_score, 0.85)
        elif top_scores['high_matches'] >= 1:
            confidence = 'medium'
            confidence_score = max(confidence_score, 0.60)
        else:
            confidence = 'low'
        
        # Build root cause summary
        evidence_list = category_evidence[top_cat_id][:5]  # Top 5 evidence items
        primary_evidence = evidence_list[0] if evidence_list else {}
        
        triage_result['most_likely_root_cause'] = {
            'category': top_cat_id,
            'category_name': top_cat_info['name'],
            'confidence': confidence,
            'confidence_score': round(confidence_score, 2),
            'summary': f"{top_cat_info['name']} detected",
            'technical_detail': primary_evidence.get('excerpt', 'See evidence for details')[:200]
        }
        
        triage_result['evidence'] = evidence_list
        
        # Add secondary hypotheses
        for cat_id, scores in sorted_categories[1:4]:  # Next 3 categories
            if scores['score'] > 0:
                cat_info = TRIAGE_CATEGORIES[cat_id]
                sec_confidence_score = min(0.50, scores['score'] / 15.0)
                triage_result['secondary_hypotheses'].append({
                    'category': cat_id,
                    'category_name': cat_info['name'],
                    'confidence': 'low' if sec_confidence_score < 0.3 else 'medium',
                    'confidence_score': round(sec_confidence_score, 2),
                    'summary': f"Possible {cat_info['name'].lower()}",
                    'evidence_count': len(category_evidence[cat_id])
                })
        
        # Generate remediation steps
        triage_result['immediate_remediation_steps'] = generate_triage_remediation(
            top_cat_id, evidence_list, pod_states, node_conditions
        )
        
        # Generate preventive recommendations
        triage_result['preventive_recommendations'] = generate_preventive_recommendations(top_cat_id)
        
        # Generate followup commands
        triage_result['followup_validation_commands'] = generate_followup_commands(
            top_cat_id, pod_states, node_conditions
        )
    
    # Update coverage report
    triage_result['coverage_report']['files_scanned'] = bundle_data.get('fileCount', 0)
    triage_result['coverage_report']['files_total'] = bundle_data.get('fileCount', 0)
    triage_result['coverage_report']['scan_duration_ms'] = int((time.time() - start_time) * 1000)
    
    # Check for missing log sources
    found_patterns = bundle_data.get('foundPatterns', [])
    if found_patterns:
        expected_sources = ['kubelet', 'containerd', 'dmesg', 'messages', 'networking']
        missing = [s for s in expected_sources if s not in found_patterns]
        if missing:
            triage_result['coverage_report']['missing_log_sources'] = missing
    else:
        # foundPatterns not available (e.g. quick_triage passes minimal bundle_data)
        triage_result['coverage_report']['missing_log_sources'] = []
    
    return triage_result


def detect_pod_states(findings: List[Dict]) -> List[Dict]:
    """Detect pod states from findings."""
    detected_states = {}
    
    for state, patterns in POD_STATE_PATTERNS.items():
        count = 0
        sample_pods = []
        
        for finding in findings:
            sample = finding.get('sample', '')
            for pattern in patterns:
                try:
                    if re.search(pattern, sample, re.IGNORECASE):
                        count += finding.get('count', 1)
                        # Try to extract pod name
                        pod_match = re.search(r'pod[/\s]+([a-z0-9-]+)', sample, re.IGNORECASE)
                        if pod_match and pod_match.group(1) not in sample_pods:
                            sample_pods.append(pod_match.group(1))
                        break
                except re.error:
                    continue
        
        if count > 0:
            detected_states[state] = {
                'state': state,
                'count': count,
                'sample_pods': sample_pods[:5]
            }
    
    return list(detected_states.values())


def detect_node_conditions(findings: List[Dict]) -> List[Dict]:
    """Detect node conditions from findings."""
    detected_conditions = []
    
    for condition, patterns in NODE_CONDITION_PATTERNS.items():
        for finding in findings:
            sample = finding.get('sample', '')
            for pattern in patterns:
                try:
                    if re.search(pattern, sample, re.IGNORECASE):
                        severity = 'critical' if condition in ['NotReady', 'MemoryPressure'] else 'warning'
                        detected_conditions.append({
                            'condition': condition,
                            'severity': severity,
                            'message': sample[:150]
                        })
                        break
                except re.error:
                    continue
    
    # Deduplicate
    seen = set()
    unique_conditions = []
    for cond in detected_conditions:
        if cond['condition'] not in seen:
            seen.add(cond['condition'])
            unique_conditions.append(cond)
    
    return unique_conditions


def generate_triage_remediation(category: str, evidence: List[Dict], 
                                 pod_states: List[Dict], node_conditions: List[Dict]) -> List[Dict]:
    """Generate immediate remediation steps based on triage category."""
    steps = []
    priority = 1
    
    if category == 'A':  # Volume/CSI
        steps = [
            {
                'priority': priority,
                'action': 'Check CSI driver pods',
                'command': 'kubectl get pods -n kube-system -l app=ebs-csi-controller',
                'expected_outcome': 'Verify CSI controller is running — if not running, volume operations will fail'
            },
            {
                'priority': priority + 1,
                'action': 'Check PV/PVC status',
                'command': "kubectl get pv,pvc -A | grep -E 'Pending|Failed'",
                'expected_outcome': 'Identify stuck volumes'
            },
            {
                'priority': priority + 2,
                'action': 'Check EBS volume attachment',
                'command': "aws ec2 describe-volumes --filters Name=status,Values=attaching,error --query 'Volumes[].{ID:VolumeId,State:State}'",
                'expected_outcome': 'Find volumes stuck in attaching state'
            },
        ]
    elif category == 'B':  # Node Issues
        steps = [
            {
                'priority': priority,
                'action': 'Check node status',
                'command': 'kubectl get nodes -o wide',
                'expected_outcome': 'Identify NotReady nodes'
            },
            {
                'priority': priority + 1,
                'action': 'Check node conditions',
                'command': "kubectl describe nodes | grep -A5 'Conditions:'",
                'expected_outcome': 'Identify pressure conditions'
            },
            {
                'priority': priority + 2,
                'action': 'Check pod resource usage',
                'command': 'kubectl top pods -A --sort-by=memory | head -20',
                'expected_outcome': 'Find memory-hungry pods'
            },
        ]
        # Add OOM-specific steps if detected
        if any(s.get('state') == 'OOMKilled' for s in pod_states):
            steps.append({
                'priority': priority + 3,
                'action': 'Increase memory limits for affected pods',
                'command': 'kubectl set resources deployment/<name> --limits=memory=1Gi',
                'expected_outcome': 'Prevent future OOM kills'
            })
    elif category == 'C':  # CNI/Networking
        steps = [
            {
                'priority': priority,
                'action': 'Check subnet IP availability',
                'command': "aws ec2 describe-subnets --query 'Subnets[].{ID:SubnetId,AvailableIPs:AvailableIpAddressCount}'",
                'expected_outcome': 'Identify IP-exhausted subnets'
            },
            {
                'priority': priority + 1,
                'action': 'Check aws-node daemonset',
                'command': 'kubectl get pods -n kube-system -l k8s-app=aws-node',
                'expected_outcome': 'Verify CNI pods are running'
            },
            {
                'priority': priority + 2,
                'action': 'Check ENI allocation',
                'command': "aws ec2 describe-network-interfaces --filters Name=description,Values='*amazon-vpc-cni*' --query 'NetworkInterfaces | length(@)'",
                'expected_outcome': 'Count ENIs used by VPC CNI'
            },
        ]
    elif category == 'D':  # iptables/conntrack
        steps = [
            {
                'priority': priority,
                'action': 'Check conntrack table usage',
                'command': 'cat /proc/sys/net/netfilter/nf_conntrack_count && cat /proc/sys/net/netfilter/nf_conntrack_max',
                'expected_outcome': 'Compare current vs max conntrack entries'
            },
            {
                'priority': priority + 1,
                'action': 'Increase conntrack max if needed',
                'command': 'sudo sysctl -w net.netfilter.nf_conntrack_max=262144',
                'expected_outcome': 'Increase conntrack table size'
            },
        ]
    elif category == 'E':  # Scheduling
        steps = [
            {
                'priority': priority,
                'action': 'Check pending pods',
                'command': "kubectl get pods -A --field-selector=status.phase=Pending",
                'expected_outcome': 'List all pending pods'
            },
            {
                'priority': priority + 1,
                'action': 'Check node resources',
                'command': 'kubectl describe nodes | grep -A10 "Allocated resources"',
                'expected_outcome': 'See resource allocation per node'
            },
            {
                'priority': priority + 2,
                'action': 'Check for taints',
                'command': "kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{\"\\t\"}{.spec.taints[*].key}{\"\\n\"}{end}'",
                'expected_outcome': 'Identify node taints blocking scheduling'
            },
        ]
    elif category == 'F':  # Image Pull
        steps = [
            {
                'priority': priority,
                'action': 'Check image pull errors',
                'command': "kubectl get events -A --field-selector=reason=Failed | grep -i image",
                'expected_outcome': 'Find image pull failures'
            },
            {
                'priority': priority + 1,
                'action': 'Verify ECR authentication',
                'command': 'aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com',
                'expected_outcome': 'Test ECR authentication'
            },
        ]
    elif category == 'G':  # DNS
        steps = [
            {
                'priority': priority,
                'action': 'Check CoreDNS pods',
                'command': 'kubectl get pods -n kube-system -l k8s-app=kube-dns',
                'expected_outcome': 'Verify CoreDNS is running'
            },
            {
                'priority': priority + 1,
                'action': 'Test DNS resolution',
                'command': 'kubectl run -it --rm debug --image=busybox --restart=Never -- nslookup kubernetes.default',
                'expected_outcome': 'Verify DNS works from pod'
            },
        ]
    elif category == 'H':  # Secrets/Webhook
        steps = [
            {
                'priority': priority,
                'action': 'Check secrets access',
                'command': 'kubectl get secrets -A | head -20',
                'expected_outcome': 'List accessible secrets'
            },
            {
                'priority': priority + 1,
                'action': 'Check webhook configurations',
                'command': 'kubectl get mutatingwebhookconfigurations,validatingwebhookconfigurations',
                'expected_outcome': 'List active webhooks'
            },
        ]
    
    return steps


def generate_preventive_recommendations(category: str) -> List[Dict]:
    """Generate preventive recommendations based on category."""
    recommendations = {
        'A': [
            {
                'category': 'monitoring',
                'recommendation': 'Set up CloudWatch alarms for EBS volume attachment failures',
            },
            {
                'category': 'configuration',
                'recommendation': 'Use volumeBindingMode: WaitForFirstConsumer in StorageClass',
                'reference': 'https://kubernetes.io/docs/concepts/storage/storage-classes/#volume-binding-mode'
            },
        ],
        'B': [
            {
                'category': 'capacity_planning',
                'recommendation': 'Implement pod resource requests and limits',
                'reference': 'https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/'
            },
            {
                'category': 'monitoring',
                'recommendation': 'Set up Container Insights for memory/CPU monitoring',
            },
        ],
        'C': [
            {
                'category': 'capacity_planning',
                'recommendation': 'Enable VPC CNI prefix delegation for higher IP density',
            },
            {
                'category': 'monitoring',
                'recommendation': 'Monitor subnet IP availability with CloudWatch',
            },
        ],
        'D': [
            {
                'category': 'configuration',
                'recommendation': 'Increase nf_conntrack_max via node configuration',
            },
        ],
        'E': [
            {
                'category': 'capacity_planning',
                'recommendation': 'Implement Cluster Autoscaler or Karpenter',
            },
        ],
        'F': [
            {
                'category': 'security',
                'recommendation': 'Use ECR pull-through cache for external images',
            },
        ],
        'G': [
            {
                'category': 'reliability',
                'recommendation': 'Scale CoreDNS based on cluster size',
            },
        ],
        'H': [
            {
                'category': 'security',
                'recommendation': 'Use EKS Pod Identity for secrets access',
            },
        ],
    }
    
    return recommendations.get(category, [])


def generate_followup_commands(category: str, pod_states: List[Dict], 
                                node_conditions: List[Dict]) -> List[Dict]:
    """Generate followup validation commands."""
    commands = []
    
    # Common commands
    commands.append({
        'tool': 'kubectl',
        'command': "kubectl get pods -A | grep -E 'Pending|ContainerCreating|CrashLoopBackOff|Error' | wc -l",
        'purpose': 'Count pods in problematic states'
    })
    
    if category == 'C':  # CNI
        commands.append({
            'tool': 'kubectl',
            'command': 'kubectl logs -n kube-system -l k8s-app=aws-node --tail=50',
            'purpose': 'Check aws-node for IP allocation success'
        })
    elif category == 'B':  # Node
        commands.append({
            'tool': 'kubectl',
            'command': 'kubectl get nodes',
            'purpose': 'Verify all nodes are Ready'
        })
    elif category == 'A':  # Volume
        commands.append({
            'tool': 'kubectl',
            'command': "kubectl get pv,pvc -A | grep -v Bound",
            'purpose': 'Check for unbound volumes'
        })
    
    return commands


# =============================================================================
# S3 SAFE HELPERS
# =============================================================================

def safe_s3_read(key: str, range_bytes: str = None, max_size: int = 1048576) -> Dict:
    """
    Safely read from S3 with graceful error handling.
    Returns dict with 'success', 'content' or 'error', and 'error_type'.
    NEVER raises exceptions - always returns a result dict.
    """
    try:
        params = {'Bucket': LOGS_BUCKET, 'Key': key}
        if range_bytes:
            params['Range'] = range_bytes
        elif max_size:
            # Enforce max_size via byte range if no explicit range given
            params['Range'] = f'bytes=0-{max_size - 1}'
        
        response = s3_client.get_object(**params)
        content = response['Body'].read()
        
        # Decode with fallback
        try:
            content_str = content.decode('utf-8')
        except UnicodeDecodeError:
            content_str = content.decode('latin-1', errors='replace')
        
        return {
            'success': True,
            'content': content_str,
            'size': len(content),
            'content_type': response.get('ContentType', 'unknown')
        }
        
    except s3_client.exceptions.NoSuchKey:
        return {
            'success': False,
            'error': f'File not found: {key}',
            'error_type': 'not_found',
            'content': ''
        }
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code == 'InvalidRange':
            return {
                'success': False,
                'error': f'Invalid byte range for: {key}',
                'error_type': 'invalid_range',
                'content': ''
            }
        if error_code == 'NoSuchKey' or error_code == '404':
            return {
                'success': False,
                'error': f'File not found: {key}',
                'error_type': 'not_found',
                'content': ''
            }
        return {
            'success': False,
            'error': f'S3 error reading {key}: {error_code} - {str(e)}',
            'error_type': 'client_error',
            'content': ''
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'Failed to read {key}: {str(e)}',
            'error_type': 'read_error',
            'content': ''
        }


def safe_s3_head(key: str) -> Dict:
    """
    Safely get S3 object metadata with graceful error handling.
    NEVER raises exceptions - always returns a result dict.
    """
    try:
        response = s3_client.head_object(Bucket=LOGS_BUCKET, Key=key)
        return {
            'success': True,
            'size': response['ContentLength'],
            'content_type': response.get('ContentType', 'unknown'),
            'last_modified': response.get('LastModified')
        }
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        if error_code == '404':
            return {
                'success': False,
                'error': f'File not found: {key}',
                'error_type': 'not_found'
            }
        return {
            'success': False,
            'error': f'Failed to get metadata for {key}: {str(e)}',
            'error_type': 'metadata_error'
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'Failed to get metadata for {key}: {str(e)}',
            'error_type': 'unknown_error'
        }


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


def find_latest_bundle_files(instance_id: str, prefix_scheme: str = 'eks') -> Dict:
    """
    Shared helper: discover the LATEST extracted bundle for an instance.
    Returns only files from the most recent bundle (by last_modified timestamp).
    
    Returns:
        {
            'success': True/False,
            'files': [list of S3 keys in the latest bundle],
            'bundle_prefix': 'eks_{id}_{exec_id}',
            'bundle_age_minutes': int or None,
            'bundle_collected_at': ISO string or None,
            'all_objects': [raw S3 objects for non-extracted use cases],
            'error': str (only if success=False),
        }
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

    # Group by bundle prefix (everything before /extracted/)
    from collections import defaultdict
    bundles_by_prefix = defaultdict(list)
    for f in bundle_files:
        prefix_part = f.split('/extracted/')[0] if '/extracted/' in f else f
        bundles_by_prefix[prefix_part].append(f)

    # Pick the bundle with the newest file
    latest_prefix = max(
        bundles_by_prefix.keys(),
        key=lambda p: max(
            (bundle_timestamps.get(f, datetime.min.replace(tzinfo=None)) for f in bundles_by_prefix[p]),
            default=datetime.min
        )
    )
    latest_files = bundles_by_prefix[latest_prefix]

    # Calculate bundle age
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

        sops = []
        for obj in response['Contents']:
            sops.append({
                'name': obj['Key'],
                'size': obj['Size'],
                'lastModified': obj['LastModified'].isoformat(),
            })
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
            'sop': {
                'name': sop_name,
                'content': content,
                'size': response['ContentLength'],
                'lastModified': response['LastModified'].isoformat(),
                'contentType': response.get('ContentType', 'text/plain'),
            }
        })
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            return error_response(404, f'SOP "{sop_name}" not found. Use list_sops to see available SOPs.')
        return error_response(500, f'Failed to get SOP: {str(e)}')
    except Exception as e:
        return error_response(500, f'Failed to get SOP: {str(e)}')


def lambda_handler(event: Dict, context: Any) -> Dict:
    """Main Lambda handler - routes to appropriate tool function."""
    start_time = time.time()

    logger.info(json.dumps({'event': 'invocation_start', 'payload': event}, default=str))

    # Extract caller identity from AgentCore JWT claims (T6 mitigation).
    caller = _extract_caller_identity(event, context)

    # Extract tool name from AgentCore context
    delimiter = "___"
    original_tool_name = context.client_context.custom.get('bedrockAgentCoreToolName', '')

    if delimiter in original_tool_name:
        tool_name = original_tool_name[original_tool_name.index(delimiter) + len(delimiter):]
    else:
        tool_name = original_tool_name

    logger.info(json.dumps({
        'event': 'tool_dispatch', 'tool': tool_name,
        'caller': {'client_id': caller.get('client_id'), 'sub': caller.get('sub')},
    }))

    # Tool routing
    tools = {
        # Core Operations (Tier 1)
        'collect': start_log_collection,
        'status': get_collection_status,
        'validate': validate_bundle_completeness,
        'errors': get_error_summary,
        'read': read_log_chunk,

        # Advanced Analysis (Tier 2)
        'search': search_logs_deep,
        'correlate': correlate_events,
        'artifact': get_artifact_reference,
        'summarize': generate_incident_summary,
        'quick_triage': quick_triage,
        'history': list_collection_history,

        # Cluster-Level Intelligence (Tier 3)
        'cluster_health': cluster_health,
        'compare_nodes': compare_nodes,
        'batch_collect': batch_collect,
        'batch_status': batch_status,
        'network_diagnostics': network_diagnostics,
        'storage_diagnostics': storage_diagnostics,
        'list_sops': list_sops,
        'get_sop': get_sop,
    }

    # Restricted tools are only registered when explicitly enabled.
    # By default these are completely absent from the routing table —
    # they don't appear in available_tools and cannot be invoked.
    _restricted_tool_map = {
        'tcpdump_capture': tcpdump_capture,
        'tcpdump_analyze': tcpdump_analyze,
    }
    for rt_name, rt_func in _restricted_tool_map.items():
        if rt_name in ENABLED_RESTRICTED_TOOLS:
            tools[rt_name] = rt_func

    if tool_name not in tools:
        emit_metric('ToolInvocationError', dimensions=[
            {'Name': 'StackName', 'Value': STACK_NAME},
            {'Name': 'ErrorType', 'Value': 'UnknownTool'},
        ])
        return error_response(400, f'Unknown tool: {tool_name}', {
            'available_tools': list(tools.keys())
        })

    # Authorization gate: restricted-tool opt-in + per-tool ACL on caller client_id
    auth_error = validate_tool_authorization(tool_name, caller=caller)
    if auth_error:
        emit_metric('ToolAuthorizationDenied', dimensions=[
            {'Name': 'StackName', 'Value': STACK_NAME},
            {'Name': 'ToolName', 'Value': tool_name},
        ])
        logger.warning(json.dumps({
            'event': 'tool_authorization_denied', 'tool': tool_name,
            'caller': {'client_id': caller.get('client_id'), 'sub': caller.get('sub')},
        }))
        return auth_error

    # Per-caller rate limit (best-effort, in-process token bucket)
    rate_limit_error = _enforce_rate_limit(caller.get('principal') or 'anonymous')
    if rate_limit_error:
        emit_metric('ToolRateLimited', dimensions=[
            {'Name': 'StackName', 'Value': STACK_NAME},
            {'Name': 'ToolName', 'Value': tool_name},
        ])
        logger.warning(json.dumps({
            'event': 'tool_rate_limited', 'tool': tool_name,
            'principal': caller.get('principal'),
        }))
        return rate_limit_error

    try:
        result = tools[tool_name](event)
        # Redact sensitive infrastructure metadata before returning to caller
        result = redact_response(result, tool_name=tool_name)
        duration_ms = (time.time() - start_time) * 1000
        emit_metric('ToolInvocation', dimensions=[
            {'Name': 'StackName', 'Value': STACK_NAME},
            {'Name': 'ToolName', 'Value': tool_name},
        ])
        emit_metric('ToolLatency', value=duration_ms, unit='Milliseconds', dimensions=[
            {'Name': 'StackName', 'Value': STACK_NAME},
            {'Name': 'ToolName', 'Value': tool_name},
        ])
        logger.info(json.dumps({
            'event': 'tool_complete', 'tool': tool_name,
            'duration_ms': round(duration_ms, 1),
            'status_code': result.get('statusCode', 0),
        }))
        return result
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(json.dumps({
            'event': 'tool_error', 'tool': tool_name,
            'error': str(e), 'duration_ms': round(duration_ms, 1),
        }))
        import traceback
        logger.error(traceback.format_exc())
        emit_metric('ToolInvocationError', dimensions=[
            {'Name': 'StackName', 'Value': STACK_NAME},
            {'Name': 'ToolName', 'Value': tool_name},
        ])
        return error_response(500, f'Internal error: {str(e)}')


def success_response(data: Dict) -> Dict:
    """Standard success response format with payload size guard."""
    MAX_PAYLOAD_BYTES = 5_500_000  # ~5.5MB safety margin under Lambda's 6MB limit

    body = json.dumps({
        'success': True,
        **data
    }, default=str)

    if len(body.encode('utf-8')) > MAX_PAYLOAD_BYTES:
        # Truncate large result arrays to fit within Lambda response limits
        truncated_data = {k: v for k, v in data.items() if not isinstance(v, list)}
        for k, v in data.items():
            if isinstance(v, list):
                # Progressively trim lists until we fit
                trimmed = v
                while trimmed:
                    candidate = json.dumps({
                        'success': True,
                        **truncated_data,
                        k: trimmed,
                        '_payloadTruncated': True,
                        '_originalCount': len(v),
                        '_returnedCount': len(trimmed),
                    }, default=str)
                    if len(candidate.encode('utf-8')) <= MAX_PAYLOAD_BYTES:
                        return {'statusCode': 200, 'body': candidate}
                    trimmed = trimmed[:len(trimmed) // 2]
                truncated_data[k] = []
        # Fallback: return metadata only
        body = json.dumps({
            'success': True,
            **truncated_data,
            '_payloadTruncated': True,
            '_error': 'Response too large, all result arrays removed',
        }, default=str)

    return {'statusCode': 200, 'body': body}


def error_response(status_code: int, message: str, details: Dict = None) -> Dict:
    """Standard error response format."""
    body = {'success': False, 'error': message}
    if details:
        body['details'] = details
    return {
        'statusCode': status_code,
        'body': json.dumps(body, default=str)
    }


# =============================================================================
# TIER 1: CORE OPERATIONS
# =============================================================================

def start_log_collection(arguments: Dict) -> Dict:
    """
    Start EKS log collection with idempotency and cross-region support.
    
    Inputs:
        instanceId: EC2 instance ID (required)
        idempotencyToken: Optional token to prevent duplicate executions
        region: AWS region where the instance runs (optional, auto-detected if omitted)
    
    Returns:
        executionId, estimatedCompletionTime, status, region
    """
    instance_id = arguments.get('instanceId')
    idempotency_token = arguments.get('idempotencyToken')
    
    if not instance_id:
        return error_response(400, 'instanceId is required')
    
    # Validate instance ID format (i-xxxxxxxxxxxxxxxxx)
    if not re.match(r'^i-[0-9a-f]{8,17}$', instance_id):
        return error_response(400, f'Invalid instanceId format: {instance_id}. Expected format: i-xxxxxxxxxxxxxxxxx')
    
    # Resolve and validate region
    target_region, region_error = resolve_and_validate_region(arguments, instance_id)
    if region_error:
        return region_error

    # Validate instance belongs to an EKS cluster
    instance_error = validate_eks_instance(instance_id, target_region)
    if instance_error:
        return instance_error

    try:
        regional_ssm = get_regional_client('ssm', target_region)
    except Exception as e:
        return error_response(500, f'Failed to create SSM client for region {target_region}: {str(e)}')
    
    print(f"Starting log collection for {instance_id} in region {target_region}")
    
    # Verify instance is running and SSM-reachable before starting automation
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
        # Non-fatal: proceed anyway, SSM will fail with a clearer error if instance is unreachable
        print(f"Warning: Could not verify instance state: {str(e)}")
    
    # Check for existing execution with same idempotency token
    if idempotency_token:
        existing = find_execution_by_idempotency_token(instance_id, idempotency_token)
        if existing:
            return success_response({
                'message': 'Returning existing execution (idempotent)',
                'executionId': existing['executionId'],
                'status': existing['status'],
                'instanceId': instance_id,
                'region': target_region,
                'idempotent': True
            })
    
    try:
        # Start SSM Automation in the target region
        params = {
            'EKSInstanceId': [instance_id],
            'LogDestination': [LOGS_BUCKET],
            'AutomationAssumeRole': [SSM_AUTOMATION_ROLE_ARN]
        }
        
        response = regional_ssm.start_automation_execution(
            DocumentName='AWSSupport-CollectEKSInstanceLogs',
            Parameters=params
        )
        
        execution_id = response['AutomationExecutionId']
        
        # Store idempotency mapping with region info
        if idempotency_token:
            store_idempotency_mapping(instance_id, idempotency_token, execution_id)
        
        # Also store region mapping so subsequent calls know which region to query
        region_stored = store_execution_region(execution_id, target_region)
        
        response_data = {
            'message': 'EKS log collection started',
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
        return error_response(404, 'AWSSupport-CollectEKSInstanceLogs document not found', {
            'suggestion': f'This SSM document may not be available in region {target_region}. '
                          f'Check https://docs.aws.amazon.com/systems-manager-automation-runbooks/latest/userguide/ '
                          f'for regional availability, or try running from a supported region like us-east-1 or us-west-2.',
            'region': target_region
        })
    except Exception as e:
        return error_response(500, f'Failed to start log collection in {target_region}: {str(e)}')


def get_collection_status(arguments: Dict) -> Dict:
    """
    Get detailed status of log collection with progress tracking.
    
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
    
    # Resolve region for this execution
    target_region = get_execution_region(execution_id) or arguments.get('region', DEFAULT_REGION)
    try:
        regional_ssm = get_regional_client('ssm', target_region)
    except Exception as e:
        return error_response(500, f'Failed to create SSM client for region {target_region}: {str(e)}')
    
    try:
        response = regional_ssm.get_automation_execution(
            AutomationExecutionId=execution_id
        )
        execution = response['AutomationExecution']
        
        status = execution['AutomationExecutionStatus']
        
        result = {
            'executionId': execution_id,
            'status': status,
            'documentName': execution.get('DocumentName', ''),
            'startTime': execution.get('ExecutionStartTime'),
            'endTime': execution.get('ExecutionEndTime'),
        }
        
        # Calculate progress
        if status == 'Success':
            result['progress'] = 100
        elif status == 'Failed':
            result['progress'] = 0
            result['failureReason'] = parse_failure_reason(execution)
        elif status == 'InProgress':
            result['progress'] = estimate_progress(execution)
        else:
            result['progress'] = 0
        
        # Include step details
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
        
        # Add outputs if available
        if 'Outputs' in execution:
            result['outputs'] = execution['Outputs']
        
        # Provide next step guidance
        if status == 'Success':
            result['nextStep'] = f'Validate bundle with validate(executionId="{execution_id}")'
        elif status == 'InProgress':
            result['suggestedPollIntervalSeconds'] = 15
            result['nextStep'] = 'Wait 15 seconds then poll again until status is Success or Failed'
        elif status == 'Failed':
            result['nextStep'] = 'Review failureReason and retry if appropriate'
        
        
        SSM_TO_TASK_STATE = {
            'Pending': 'running',
            'InProgress': 'running',
            'Waiting': 'running',
            'Success': 'completed',
            'TimedOut': 'failed',
            'Cancelling': 'cancelling',
            'Cancelled': 'cancelled',
            'Failed': 'failed',
        }
        task_state = SSM_TO_TASK_STATE.get(status, 'running')
        result['task'] = {
            'taskId': execution_id,
            'state': task_state,
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
    Verify all expected files were extracted from log bundle.
    Gracefully handles missing logs - reports what's available without failing.
    
    Inputs:
        executionId: SSM execution ID OR
        instanceId: Instance ID + timestamp to locate bundle
    
    Returns:
        complete (bool), fileCount, totalSize, missingPatterns, manifest
    """
    execution_id = arguments.get('executionId')
    instance_id = arguments.get('instanceId')
    
    if not execution_id and not instance_id:
        return error_response(400, 'Either executionId or instanceId is required')
    
    try:
        # Determine prefix from execution or instance
        if instance_id:
            prefix = f'eks_{instance_id}'
        else:
            # Get instance ID from execution
            try:
                target_region = get_execution_region(execution_id) or arguments.get('region', DEFAULT_REGION)
                regional_ssm = get_regional_client('ssm', target_region)
            except Exception as e:
                return error_response(500, f'Failed to create SSM client for region: {str(e)}')
            try:
                exec_response = regional_ssm.get_automation_execution(
                    AutomationExecutionId=execution_id
                )
                params = exec_response['AutomationExecution'].get('Parameters', {})
                instance_id = params.get('EKSInstanceId', [''])[0]
                prefix = f'eks_{instance_id}'
            except regional_ssm.exceptions.AutomationExecutionNotFoundException:
                return error_response(404, f'Execution {execution_id} not found')
            except Exception as e:
                return error_response(500, f'Failed to get execution details: {str(e)}')
        
        # Use shared latest-bundle discovery
        bundle_info = find_latest_bundle_files(instance_id)
        
        if not bundle_info['success']:
            # Return partial result even if listing fails
            return success_response({
                'complete': False,
                'fileCount': 0,
                'totalSize': 0,
                'totalSizeHuman': '0 B',
                'missingPatterns': ['all'],
                'foundPatterns': [],
                'hasFindingsIndex': False,
                'instanceId': instance_id,
                'manifest': [],
                'warning': bundle_info.get('error', 'Failed to list files'),
                'nextStep': 'Check if log collection completed successfully'
            })
        
        # Filter for extracted files — already filtered to latest bundle
        size_map = {obj['key']: obj for obj in bundle_info['all_objects']}
        all_files = [
            size_map[k] for k in bundle_info['files'] if k in size_map
        ]
        
        # Manifest: look in the latest bundle prefix
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
        
        # Handle case where no logs found
        if not all_files:
            return success_response({
                'complete': False,
                'fileCount': 0,
                'totalSize': 0,
                'totalSizeHuman': '0 B',
                'missingPatterns': ['all - no extracted logs found'],
                'foundPatterns': [],
                'hasFindingsIndex': False,
                'instanceId': instance_id,
                'manifest': [],
                'info': 'No extracted log files found. Log collection may still be in progress or may have failed.',
                'nextStep': 'Check log collection status with status'
            })
        
        total_size = sum(f['size'] for f in all_files)
        
        # Check for expected log patterns - these are optional, not required
        expected_patterns = [
            'kubelet', 'containerd', 'dmesg', 'messages',
            'networking', 'storage', 'pods'
        ]
        
        found_patterns = set()
        for f in all_files:
            key_lower = f['key'].lower()
            for pattern in expected_patterns:
                if pattern in key_lower:
                    found_patterns.add(pattern)
        
        missing_patterns = list(set(expected_patterns) - found_patterns)
        
        # Check for findings index
        has_findings_index = any(
            FINDINGS_INDEX_FILE in f['key'] for f in all_files
        )
        
        # Consider complete if we have at least some files (not all patterns required)
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
            # Cross-check: manifest says N files, S3 listing shows M
            if manifest_file_count > 0 and len(all_files) < manifest_file_count:
                result['warning'] = (
                    f'Manifest reports {manifest_file_count} files but only {len(all_files)} found in S3. '
                    f'Some files may have been deleted or extraction was incomplete.'
                )
                result['complete'] = False
        
        # Add info about missing patterns (not an error, just informational)
        if missing_patterns:
            result['info'] = f'Some log types not found: {", ".join(missing_patterns)}. This may be normal depending on node configuration.'
        
        # Include manifest (first 50 files)
        result['manifest'] = [
            {
                'key': f['key'].split('/extracted/')[-1] if '/extracted/' in f['key'] else f['key'],
                'fullKey': f['key'],
                'size': f['size'],
                'sizeHuman': format_bytes(f['size'])
            }
            for f in sorted(all_files, key=lambda x: x['size'], reverse=True)[:50]
        ]
        
        if is_complete:
            result['nextStep'] = f'Get error summary with errors(instanceId="{instance_id}")'
        else:
            result['nextStep'] = 'Bundle may be incomplete. Check SSM Automation status or proceed with available logs.'
        
        return success_response(result)
        
    except Exception as e:
        # Even on unexpected error, return a graceful response
        return success_response({
            'complete': False,
            'fileCount': 0,
            'totalSize': 0,
            'totalSizeHuman': '0 B',
            'missingPatterns': ['unknown'],
            'foundPatterns': [],
            'hasFindingsIndex': False,
            'instanceId': instance_id or 'unknown',
            'manifest': [],
            'error': f'Unexpected error during validation: {str(e)}',
            'nextStep': 'Retry or check AWS console for log collection status'
        })


def get_error_summary(arguments: Dict) -> Dict:
    """
    Get pre-indexed error findings (fast path, no scanning).
    Gracefully handles missing logs - returns empty findings without failing.
    
    Inputs:
        instanceId: EC2 instance ID (required)
        severity: Filter by severity (critical|high|medium|low|info|warning|all)
        response_format: 'concise' (default) or 'detailed'
        pageSize: Number of findings per page (default: 50, max: 200)
        pageToken: Opaque token for next page (base64-encoded offset)
        incident_time: ISO8601 timestamp of the incident (optional)
        start_time: Start of analysis window ISO8601 (optional)
        end_time: End of analysis window ISO8601 (optional)
    
    Returns:
        findings[], summary counts, indexed timestamp, coverage_report, time window metadata
    """
    instance_id = arguments.get('instanceId')
    severity_filter = arguments.get('severity', 'all')
    response_format = arguments.get('response_format', 'concise')
    page_size = min(arguments.get('pageSize', 50), 200)
    page_token = arguments.get('pageToken')
    cluster_context = arguments.get('clusterContext')
    
    # ── Resolve time window ──
    time_window = TimeWindowResolver.resolve(arguments)
    tw_meta = TimeWindowResolver.window_metadata(time_window)
    
    if not instance_id:
        return error_response(400, 'instanceId is required')
    
    # Decode page offset
    page_offset = 0
    if page_token:
        try:
            import base64
            page_offset = int(base64.b64decode(page_token).decode('utf-8'))
        except Exception:
            page_offset = 0
    
    try:
        # Try to read pre-computed findings index
        prefix = f'eks_{instance_id}'
        index_key = find_findings_index(prefix)
        
        if index_key:
            # Fast path: return cached findings
            read_result = safe_s3_read(index_key)
            
            if read_result['success']:
                try:
                    index_data = json.loads(read_result['content'])
                    findings = index_data.get('findings', [])
                    
                    # Assign finding_ids if not present
                    for idx, f in enumerate(findings):
                        if 'finding_id' not in f:
                            f['finding_id'] = assign_finding_id(idx + 1)
                    
                    # Backward-compat: remap old severity names
                    for f in findings:
                        old_sev = f.get('severity', 'info')
                        if old_sev == 'warning':
                            f['severity'] = 'high'
                    
                    
                    if cluster_context:
                        findings = annotate_findings_with_baselines(findings, cluster_context)
                    
                    # ── Time-window filtering on cached findings ──
                    tw_filter_stats = {}
                    tw_result = TimeWindowResolver.filter_findings_by_window(findings, time_window)
                    findings = tw_result['findings']
                    tw_filter_stats = {
                        'excluded_outside_window': tw_result['excluded_outside_window'],
                        'unparseable_timestamps': tw_result['unparseable_timestamps'],
                        'total_before_filter': tw_result['total_before_filter'],
                    }
                    
                    # Filter by severity if requested
                    allowed_severities = normalize_severity_filter(severity_filter)
                    if severity_filter != 'all':
                        findings = [f for f in findings if f.get('severity') in allowed_severities]
                    
                    # Pagination
                    total_findings = len(findings)
                    page_findings = findings[page_offset:page_offset + page_size]
                    has_more = (page_offset + page_size) < total_findings
                    
                    next_token = None
                    if has_more:
                        import base64
                        next_token = base64.b64encode(str(page_offset + page_size).encode('utf-8')).decode('utf-8')
                    
                    # Build summary with 5-level counts
                    summary = index_data.get('summary', {})
                    # Migrate old summary format
                    if 'warning' in summary and 'high' not in summary:
                        summary = {
                            'critical': summary.get('critical', 0),
                            'high': summary.get('warning', 0),
                            'medium': 0,
                            'low': 0,
                            'info': summary.get('info', 0),
                        }
                    
                    # Format findings based on response_format
                    if response_format == 'concise':
                        page_findings = [
                            {
                                'finding_id': f.get('finding_id'),
                                'severity': f.get('severity'),
                                'pattern': f.get('pattern'),
                                'file': f.get('file'),
                                'count': f.get('count'),
                                **(
                                    {'is_baseline': f.get('is_baseline', False),
                                     'baseline_note': f.get('baseline_note')}
                                    if f.get('is_baseline') else {}
                                ),
                            }
                            for f in page_findings
                        ]
                    
                    # Coverage report
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
                        **tw_meta,
                        'time_window_filter': tw_filter_stats,
                        'interpretationGuide': {
                            'NXDOMAIN': 'Domain does not exist. Check if pods are querying wrong service names or non-existent external domains. This is NOT necessarily a DNS server misconfiguration.',
                            'OOMKilled': 'Container exceeded its memory limit and was killed by the kernel. Check container memory requests/limits.',
                            'CrashLoopBackOff': 'Container keeps crashing and restarting. Check the exit code and container logs (kubectl logs <pod> --previous).',
                            'ImagePullBackOff': 'Failed to pull container image. Check image name, tag, registry auth, and ECR permissions.',
                            'FailedScheduling': 'Pod could not be scheduled. Check node resources, taints/tolerations, and node selectors.',
                            'Evicted': 'Pod was evicted due to node resource pressure (disk, memory, or PID). Check node conditions.',
                            'FailedMount': 'Volume mount failed. Check PV/PVC status, EBS volume availability, and IAM permissions.',
                            'NetworkNotReady': 'Node network plugin (CNI) is not ready. Check aws-node (VPC CNI) pod status.',
                            'resolv.conf (node)': 'Node /etc/resolv.conf showing VPC DNS (e.g., 172.31.0.2) is NORMAL. Pod DNS is set separately by kubelet --cluster-dns.',
                        },
                        'nextStep': 'Use search for detailed investigation, or summarize with finding_ids'
                    })
                except json.JSONDecodeError:
                    # Index file corrupted, fall through to scan
                    print(f"Warning: Findings index corrupted, will scan on-demand")
        
        # Slow path: scan and index on-demand
        result = scan_and_index_errors(instance_id, severity_filter, time_window=time_window)
        
        if cluster_context and result.get('success') and result.get('data', {}).get('findings'):
            result['data']['findings'] = annotate_findings_with_baselines(
                result['data']['findings'], cluster_context
            )
            update_baselines(cluster_context, result['data']['findings'])
        return result
        
    except Exception as e:
        # Return empty findings on error, don't fail
        return success_response({
            'instanceId': instance_id,
            'findings': [],
            'totalFindings': 0,
            'summary': {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0},
            'cached': False,
            **tw_meta,
            'warning': f'Could not retrieve error summary: {str(e)}',
            'nextStep': 'Check if logs exist with validate'
        })


def read_log_chunk(arguments: Dict) -> Dict:
    """
    Byte-range streaming for large log files. NO TRUNCATION.
    Gracefully handles missing files - returns informative error without failing.
    
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
    
    # Redirect agent away from networking files — network_diagnostics parses them better
    networking_files = ['iproute.txt', 'iptables.txt', 'ip-addr.txt', 'ip-rule.txt',
                        'ip-link.txt', 'ss.txt', 'netstat.txt', 'resolv.conf',
                        'conntrack.txt', 'nftables.txt']
    basename = log_key.rsplit('/', 1)[-1] if '/' in log_key else log_key
    if basename in networking_files:
        # Extract instance ID from key pattern: eks_{instanceId}_{executionId}/...
        parts = log_key.split('_')
        instance_hint = parts[1] if len(parts) >= 2 else 'unknown'
        return success_response({
            'logKey': log_key,
            'content': '',
            'redirect': True,
            'warning': f'Do NOT read {basename} directly. Use network_diagnostics(instanceId="{instance_hint}") instead — it parses all networking files into structured data in one call.',
            'nextStep': f'Call network_diagnostics with instanceId="{instance_hint}" to get structured routing, iptables, DNS, and CNI data.',
        })
    
    try:
        # Get file metadata using safe helper
        head_result = safe_s3_head(log_key)
        
        if not head_result['success']:
            # File not found or inaccessible - return graceful response
            return success_response({
                'logKey': log_key,
                'content': '',
                'startByte': 0,
                'endByte': 0,
                'chunkSize': 0,
                'totalSize': 0,
                'totalSizeHuman': '0 B',
                'hasMore': False,
                'nextChunkToken': None,
                'truncated': False,
                'fileNotFound': True,
                'warning': head_result.get('error', 'File not found or inaccessible'),
                'suggestion': 'The log file may not exist or may have been cleaned up. Try listing available logs first.'
            })
        
        total_size = head_result['size']
        
        # For very large files, return presigned URL instead
        if total_size > MAX_CHUNK_SIZE * 10:  # >50MB
            return get_artifact_reference({'logKey': log_key, 'reason': 'File too large for direct read'})
        
        # Line-based reading
        if start_line is not None:
            return read_by_lines(log_key, start_line, min(line_count, MAX_LINE_COUNT), total_size)
        
        # Byte-range reading
        if end_byte is None:
            end_byte = min(start_byte + DEFAULT_CHUNK_SIZE, total_size)
        
        # Clamp to valid range
        start_byte = max(0, start_byte)
        end_byte = min(end_byte, total_size)
        chunk_size = end_byte - start_byte
        
        if chunk_size > MAX_CHUNK_SIZE:
            end_byte = start_byte + MAX_CHUNK_SIZE
            chunk_size = MAX_CHUNK_SIZE
        
        # Handle empty file
        if total_size == 0 or chunk_size <= 0:
            return success_response({
                'logKey': log_key,
                'content': '',
                'startByte': 0,
                'endByte': 0,
                'chunkSize': 0,
                'totalSize': total_size,
                'totalSizeHuman': format_bytes(total_size),
                'hasMore': False,
                'nextChunkToken': None,
                'truncated': False,
                'info': 'File is empty or requested range is invalid'
            })
        
        # Read slightly more than requested to find newline boundaries
        BOUNDARY_SCAN = 4096  # Extra bytes to scan for newline alignment

        # Expand read range for boundary alignment
        actual_start = max(0, start_byte - 1) if start_byte > 0 else 0
        actual_end = min(end_byte + BOUNDARY_SCAN, total_size)

        range_header = f'bytes={actual_start}-{actual_end - 1}'
        read_result = safe_s3_read(log_key, range_bytes=range_header)
        
        if not read_result['success']:
            return success_response({
                'logKey': log_key,
                'content': '',
                'startByte': start_byte,
                'endByte': end_byte,
                'chunkSize': 0,
                'totalSize': total_size,
                'totalSizeHuman': format_bytes(total_size),
                'hasMore': False,
                'nextChunkToken': None,
                'truncated': False,
                'warning': read_result.get('error', 'Failed to read file content'),
                'suggestion': 'Try a different byte range or check file permissions'
            })
        
        raw = read_result['content']

        # Snap start: if not at file start, skip forward to first \n
        aligned_start = start_byte
        if start_byte > 0:
            first_nl = raw.find('\n')
            if first_nl >= 0:
                aligned_start = actual_start + first_nl + 1
                raw = raw[first_nl + 1:]

        # Snap end: find the last complete line
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

        content_str = raw
        has_more = aligned_end < total_size
        
        return success_response({
            'logKey': log_key,
            'content': content_str,
            'startByte': aligned_start,
            'endByte': aligned_end,
            'chunkSize': len(content_str),
            'totalSize': total_size,
            'totalSizeHuman': format_bytes(total_size),
            'hasMore': has_more,
            'nextChunkToken': str(aligned_end) if has_more else None,
            'truncated': False,  # NEVER truncate
            'lineAligned': True,
        })
        
    except Exception as e:
        # Even on unexpected error, return graceful response
        return success_response({
            'logKey': log_key,
            'content': '',
            'startByte': 0,
            'endByte': 0,
            'chunkSize': 0,
            'totalSize': 0,
            'totalSizeHuman': '0 B',
            'hasMore': False,
            'nextChunkToken': None,
            'truncated': False,
            'error': f'Unexpected error reading log: {str(e)}',
            'suggestion': 'Check if the log key is correct and the file exists'
        })


# =============================================================================
# TIER 2: ADVANCED ANALYSIS
# =============================================================================

def search_logs_deep(arguments: Dict) -> Dict:
    """
    Full-text search across all logs without truncation.
    Gracefully handles missing logs - returns empty results without failing.
    
    Inputs:
        instanceId: EC2 instance ID (required)
        query: Regex pattern to search (required)
        logTypes: Comma-separated log types to search (optional)
        timeRange: ISO timestamp range (optional)
        maxResults: Max results per file (default: 100)
        response_format: 'concise' (default) or 'detailed'
        incident_time: ISO8601 timestamp of the incident (optional)
        start_time: Start of analysis window ISO8601 (optional)
        end_time: End of analysis window ISO8601 (optional)
    
    Returns:
        matches[], pagination info, coverage_report, time window metadata
    """
    instance_id = arguments.get('instanceId')
    query = arguments.get('query')
    log_types_str = arguments.get('logTypes', '')
    max_results = min(arguments.get('maxResults', 100), 500)
    response_format = arguments.get('response_format', 'concise')
    
    # ── Resolve time window ──
    time_window = TimeWindowResolver.resolve(arguments)
    tw_meta = TimeWindowResolver.window_metadata(time_window)
    
    if not instance_id:
        return error_response(400, 'instanceId is required')
    if not query:
        return error_response(400, 'query is required')
    if len(query) > 500:
        return error_response(400, 'query too long (max 500 characters)')
    
    try:
        # Compile regex
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return error_response(400, f'Invalid regex pattern: {str(e)}')
        
        # Get file patterns to search
        file_patterns = None
        if log_types_str:
            file_patterns = []
            for log_type in log_types_str.split(','):
                log_type = log_type.strip().lower()
                if log_type in LOG_TYPE_PATTERNS:
                    file_patterns.extend(LOG_TYPE_PATTERNS[log_type])
        
        # Use shared latest-bundle discovery
        bundle_info = find_latest_bundle_files(instance_id)
        
        if not bundle_info['success']:
            return success_response({
                'instanceId': instance_id,
                'query': query,
                'filesSearched': 0,
                'filesWithMatches': 0,
                'totalMatches': 0,
                'results': [],
                'truncated': False,
                'warning': bundle_info.get('error', 'Failed to list log files'),
                'nextStep': 'Check if logs exist with validate'
            })
        
        # Filter files to search — already scoped to latest bundle
        files_to_search = []
        large_file_count = 0
        size_map = {obj['key']: obj['size'] for obj in bundle_info['all_objects']}
        for key in bundle_info['files']:
            if any(key.endswith(ext) for ext in ['.tar.gz', '.zip', '.gz', '.bin', '.so']):
                continue
            
            fsize = size_map.get(key, 0)
            if fsize > 20971520:  # Skip files >20MB to bound resource consumption
                large_file_count += 1
                continue
            
            # Filter by log type
            if file_patterns:
                if not any(p in key.lower() for p in file_patterns):
                    continue
            
            files_to_search.append({
                'key': key,
                'size': fsize
            })
        
        # Handle no files found
        if not files_to_search:
            return success_response({
                'instanceId': instance_id,
                'query': query,
                'filesSearched': 0,
                'filesWithMatches': 0,
                'totalMatches': 0,
                'results': [],
                'truncated': False,
                'info': 'No log files found matching criteria. Log collection may still be in progress.',
                'nextStep': 'Check log collection status or try different log types'
            })
        
        # Search files
        all_matches = []
        files_searched = 0
        files_with_errors = 0
        search_start = time.time() if 'time' in dir() else __import__('time').time()
        early_terminated = False
        
        for file_info in files_to_search[:50]:  # Limit files to prevent timeout
            files_searched += 1
            matches = search_file_for_pattern(file_info['key'], pattern, max_results, file_size=file_info['size'])
            
            if matches is None:
                # File read error - count but don't fail
                files_with_errors += 1
                continue
            
            if matches:
                filename = file_info['key'].split('/extracted/')[-1]
                all_matches.append({
                    'file': filename,
                    'fullKey': file_info['key'],
                    'matchCount': len(matches),
                    'matches': matches
                })
            
            if sum(len(m['matches']) for m in all_matches) >= max_results * 3:
                break

            # Early termination: if running >30s and have sufficient results
            elapsed = (__import__('time').time() - search_start)
            total_match_count = sum(len(m['matches']) for m in all_matches)
            if elapsed > 30 and total_match_count >= 10:
                early_terminated = True
                break
        
        # Sort by match count
        all_matches.sort(key=lambda x: x['matchCount'], reverse=True)
        
        # ── Time-window filtering on search matches ──
        excluded_outside_window = 0
        unparseable_timestamps = 0
        for match_group in all_matches:
            filtered_matches = []
            for m in match_group['matches']:
                line_text = m.get('line', m.get('text', ''))
                ts_str = extract_timestamp(line_text) if line_text else None
                if ts_str is None:
                    unparseable_timestamps += 1
                    filtered_matches.append(m)  # Conservative: include if no timestamp
                elif TimeWindowResolver.is_within_window(ts_str, time_window):
                    filtered_matches.append(m)
                else:
                    excluded_outside_window += 1
            match_group['matches'] = filtered_matches
            match_group['matchCount'] = len(filtered_matches)
        # Remove groups with zero matches after filtering
        all_matches = [mg for mg in all_matches if mg['matchCount'] > 0]
        
        # Assign finding_ids to search results
        finding_counter = 0
        for match_group in all_matches:
            finding_counter += 1
            match_group['finding_id'] = f"S-{finding_counter:03d}"
        
        # Trim individual file matches to keep response manageable
        total_matches_kept = 0
        for match_group in all_matches:
            remaining_budget = max(10, max_results * 3 - total_matches_kept)
            if len(match_group['matches']) > remaining_budget:
                match_group['matches'] = match_group['matches'][:remaining_budget]
                match_group['matchCount'] = len(match_group['matches'])
                match_group['matchesTruncated'] = True
            total_matches_kept += len(match_group['matches'])
        
        # Coverage report
        coverage_report = {
            'files_searched': files_searched,
            'files_available': len(files_to_search),
            'files_skipped_size': large_file_count,
            'files_with_errors': files_with_errors,
            'scan_complete': files_searched >= len(files_to_search) and not early_terminated,
            'early_terminated': early_terminated,
        }
        if early_terminated:
            coverage_report['early_termination_reason'] = (
                f'Search stopped after {files_searched} files ({elapsed:.1f}s elapsed) with '
                f'{sum(m["matchCount"] for m in all_matches)} matches found. '
                'Sufficient results available — remaining files skipped to prevent timeout.'
            )
        
        result = {
            'instanceId': instance_id,
            'query': query,
            'filesSearched': files_searched,
            'filesWithMatches': len(all_matches),
            'totalMatches': sum(m['matchCount'] for m in all_matches),
            'results': all_matches,
            'truncated': files_searched < len(files_to_search),
            'coverage_report': coverage_report,
            **tw_meta,
            'time_window_filter': {
                'excluded_outside_window': excluded_outside_window,
                'unparseable_timestamps': unparseable_timestamps,
            },
            'interpretationGuide': {
                'NXDOMAIN': 'Domain does not exist. Likely pods querying wrong service names or non-existent domains — not a DNS server misconfiguration.',
                'OOMKilled': 'Container exceeded memory limit. Check requests/limits in pod spec.',
                'CrashLoopBackOff': 'Container keeps crashing. Check exit code and previous container logs.',
                'SERVFAIL': 'DNS server failed to resolve. Could be CoreDNS overload or upstream DNS issue.',
                'connection timed out': 'Network connectivity issue. Check security groups, NACLs, and route tables.',
                'failed to allocate': 'Resource allocation failure. For IPs: check subnet capacity and ENI limits.',
                'resolv.conf': 'If from node /etc/resolv.conf: VPC DNS (e.g., 172.31.0.2) is NORMAL for nodes. Pod DNS is separate.',
            },
            'nextStep': 'Use read to get full context around specific matches'
        }
        
        if files_with_errors > 0:
            result['info'] = f'{files_with_errors} files could not be read (may be binary or inaccessible)'
        
        return success_response(result)
        
    except Exception as e:
        # Return empty results on error, don't fail
        return success_response({
            'instanceId': instance_id,
            'query': query,
            'filesSearched': 0,
            'filesWithMatches': 0,
            'totalMatches': 0,
            'results': [],
            'truncated': False,
            **tw_meta,
            'error': f'Search encountered an error: {str(e)}',
            'nextStep': 'Check if logs exist with validate'
        })


def correlate_events(arguments: Dict) -> Dict:
    """
    Cross-file timeline correlation for incident analysis.
    Gracefully handles missing data - returns empty correlations without failing.
    
    Inputs:
        instanceId: EC2 instance ID (required)
        timeWindow: Seconds around pivot event (default: 60)
        pivotEvent: Event to correlate around (optional)
        components: Components to include (optional)
        response_format: 'concise' (default) or 'detailed'
        incident_time: ISO8601 timestamp of the incident (optional)
        start_time: Start of analysis window ISO8601 (optional)
        end_time: End of analysis window ISO8601 (optional)
    
    Returns:
        timeline[], correlations, temporal_clusters, potential_root_cause_chain, coverage_report, time window metadata
    """
    instance_id = arguments.get('instanceId')
    time_window = arguments.get('timeWindow', 60)
    pivot_event = arguments.get('pivotEvent')
    components = arguments.get('components', [])
    response_format = arguments.get('response_format', 'concise')
    
    # ── Resolve analysis time window ──
    analysis_window = TimeWindowResolver.resolve(arguments)
    tw_meta = TimeWindowResolver.window_metadata(analysis_window)
    
    if not instance_id:
        return error_response(400, 'instanceId is required')
    
    try:
        # Try cached findings index first (fast path)
        prefix = f'eks_{instance_id}'
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
        
        # Fall back to on-demand scan only if no cached findings
        if not findings:
            error_summary = scan_and_index_errors(instance_id, 'all')
            
            if error_summary['statusCode'] != 200:
                return success_response({
                    'instanceId': instance_id,
                    'timeWindow': time_window,
                    'timeline': [],
                    'byComponent': {},
                    'correlations': [],
                    'temporal_clusters': [],
                    'potential_root_cause_chain': [],
                    'coverage_report': {'files_scanned': 0, 'scan_complete': False},
                    'confidence': 'none',
                    'gaps': ['Could not retrieve error data for correlation'],
                    'warning': 'Could not retrieve error data for correlation',
                    'nextStep': 'Check if logs exist with validate'
                })
            
            summary_data = json.loads(error_summary['body'])
            findings = summary_data.get('findings', [])
            files_scanned = summary_data.get('coverage_report', {}).get('files_scanned', 0)
        
        # Handle no findings
        if not findings:
            return success_response({
                'instanceId': instance_id,
                'timeWindow': time_window,
                'timeline': [],
                'byComponent': {},
                'correlations': [],
                'temporal_clusters': [],
                'potential_root_cause_chain': [],
                'coverage_report': {'files_scanned': files_scanned, 'scan_complete': True},
                'confidence': 'none',
                'gaps': [],
                'info': 'No error findings to correlate. This may indicate a healthy node or logs not yet collected.',
                'nextStep': 'Use search to search for specific patterns'
            })
        
        # Backward-compat: remap old severity names
        for f in findings:
            old_sev = f.get('severity', 'info')
            if old_sev == 'warning':
                f['severity'] = 'high'
        
        # ── Time-window filtering on findings ──
        tw_result = TimeWindowResolver.filter_findings_by_window(findings, analysis_window)
        findings = tw_result['findings']
        tw_filter_stats = {
            'excluded_outside_window': tw_result['excluded_outside_window'],
            'unparseable_timestamps': tw_result['unparseable_timestamps'],
            'total_before_filter': tw_result['total_before_filter'],
        }
        
        # Build timeline from findings with finding_ids
        timeline = []
        for idx, finding in enumerate(findings):
            # Parse timestamp if available
            timestamp = extract_timestamp(finding.get('sample', ''))
            
            timeline.append({
                'finding_id': finding.get('finding_id', assign_finding_id(idx + 1)),
                'timestamp': timestamp,
                'source': finding.get('file', 'unknown'),
                'severity': finding.get('severity', 'info'),
                'event': finding.get('pattern', ''),
                'sample': finding.get('sample', '')[:200],
                'count': finding.get('count', 1)
            })
        
        # Sort by severity (critical first) then by count
        timeline.sort(key=lambda x: (SEVERITY_ORDER.get(x['severity'], 4), -x['count']))
        
        # Group by component
        by_component = {}
        for event in timeline:
            source = event['source']
            component = categorize_log_source(source)
            if component not in by_component:
                by_component[component] = []
            by_component[component].append(event)
        
        
        temporal_clusters = _build_temporal_clusters(timeline, time_window)
        
        
        root_cause_chain = _build_root_cause_chain(timeline, by_component, temporal_clusters)
        
        # Confidence assessment
        critical_count = len([e for e in timeline if e['severity'] == 'critical'])
        total_count = len(timeline)
        if critical_count > 0 and total_count >= 3:
            confidence = 'high'
        elif total_count >= 2:
            confidence = 'medium'
        else:
            confidence = 'low'
        
        # Identify gaps
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
            **tw_meta,
            'time_window_filter': tw_filter_stats,
            'caveat': (
                'Timeline correlation is based on pattern matching across log files. '
                'Timestamps may not be perfectly synchronized across components. '
                'Correlation does not imply causation — verify findings by checking '
                'pod-level config and component-specific logs.'
            ),
            'nextStep': 'Use search to investigate specific events'
        })
        
    except Exception as e:
        # Return empty correlation on error, don't fail
        return success_response({
            'instanceId': instance_id,
            'timeWindow': time_window,
            'timeline': [],
            'byComponent': {},
            'correlations': [],
            'temporal_clusters': [],
            'potential_root_cause_chain': [],
            'confidence': 'none',
            'gaps': [f'Correlation error: {str(e)}'],
            'coverage_report': {'files_scanned': 0, 'scan_complete': False},
            **tw_meta,
            'error': f'Correlation encountered an error: {str(e)}',
            'nextStep': 'Check if logs exist with validate'
        })


def _build_temporal_clusters(timeline: List[Dict], time_window: int) -> List[Dict]:
    """Group events into temporal clusters based on timestamps."""
    # Separate events with and without timestamps
    timed_events = [e for e in timeline if e.get('timestamp')]
    untimed_events = [e for e in timeline if not e.get('timestamp')]
    
    if not timed_events:
        # No timestamps available — return single cluster with all events
        if timeline:
            return [{
                'cluster_id': 'C-001',
                'label': 'all-events (no timestamps)',
                'event_count': len(timeline),
                'finding_ids': [e.get('finding_id', '') for e in timeline[:20]],
                'dominant_severity': timeline[0].get('severity', 'info') if timeline else 'info',
            }]
        return []
    
    # Sort by timestamp
    timed_events.sort(key=lambda x: x['timestamp'])
    
    clusters = []
    current_cluster = [timed_events[0]]
    
    for event in timed_events[1:]:
        # Simple heuristic: events within time_window seconds are in same cluster
        # Since timestamps are strings, we do string comparison (ISO format sorts correctly)
        if len(current_cluster) < 20:  # Cap cluster size
            current_cluster.append(event)
        else:
            clusters.append(current_cluster)
            current_cluster = [event]
    
    if current_cluster:
        clusters.append(current_cluster)
    
    result = []
    for idx, cluster in enumerate(clusters):
        severities = [e['severity'] for e in cluster]
        dominant = min(severities, key=lambda s: SEVERITY_ORDER.get(s, 4))
        result.append({
            'cluster_id': f'C-{idx + 1:03d}',
            'time_range': {
                'start': cluster[0].get('timestamp'),
                'end': cluster[-1].get('timestamp'),
            },
            'event_count': len(cluster),
            'finding_ids': [e.get('finding_id', '') for e in cluster],
            'dominant_severity': dominant,
            'components': list(set(categorize_log_source(e.get('source', '')) for e in cluster)),
        })
    
    return result


def _build_root_cause_chain(timeline: List[Dict], by_component: Dict, clusters: List[Dict]) -> List[Dict]:
    """Build potential root cause chain from correlated events."""
    chain = []
    
    # Heuristic: look for known causal patterns
    critical_events = [e for e in timeline if e['severity'] == 'critical']
    
    # Pattern 1: Kernel -> Kubelet -> Pod failures
    kernel_issues = by_component.get('kernel', [])
    kubelet_issues = by_component.get('kubelet', [])
    
    if kernel_issues and kubelet_issues:
        chain.append({
            'sequence': 'kernel → kubelet → pod',
            'confidence': 'medium',
            'description': 'Kernel-level issues may have cascaded to kubelet and pod failures',
            'evidence_finding_ids': (
                [e.get('finding_id') for e in kernel_issues[:3]] +
                [e.get('finding_id') for e in kubelet_issues[:3]]
            ),
        })
    
    # Pattern 2: Network -> CNI -> Pod connectivity
    network_issues = by_component.get('networking', [])
    cni_issues = by_component.get('ipamd', []) + by_component.get('cni', [])
    
    if network_issues or cni_issues:
        chain.append({
            'sequence': 'network/CNI → pod connectivity',
            'confidence': 'medium' if (network_issues and cni_issues) else 'low',
            'description': 'Network or CNI issues may be causing pod connectivity failures',
            'evidence_finding_ids': (
                [e.get('finding_id') for e in network_issues[:3]] +
                [e.get('finding_id') for e in cni_issues[:3]]
            ),
        })
    
    # Pattern 3: OOM -> Container restarts
    oom_events = [e for e in timeline if 'oom' in e.get('event', '').lower() or 'memory' in e.get('event', '').lower()]
    restart_events = [e for e in timeline if 'restart' in e.get('event', '').lower() or 'crashloop' in e.get('event', '').lower()]
    
    if oom_events and restart_events:
        chain.append({
            'sequence': 'memory pressure → OOM kill → container restart',
            'confidence': 'high',
            'description': 'Memory pressure caused OOM kills leading to container restarts',
            'evidence_finding_ids': (
                [e.get('finding_id') for e in oom_events[:3]] +
                [e.get('finding_id') for e in restart_events[:3]]
            ),
        })
    
    # Pattern 4: Auth failures -> Node registration
    auth_events = [e for e in timeline if any(kw in e.get('event', '').lower() for kw in ['unauthorized', 'denied', 'credential'])]
    reg_events = [e for e in timeline if 'register' in e.get('event', '').lower() or 'join' in e.get('event', '').lower()]
    
    if auth_events and reg_events:
        chain.append({
            'sequence': 'auth failure → node registration failure',
            'confidence': 'high',
            'description': 'Authentication/authorization failures prevented node from joining the cluster',
            'evidence_finding_ids': (
                [e.get('finding_id') for e in auth_events[:3]] +
                [e.get('finding_id') for e in reg_events[:3]]
            ),
        })
    
    return chain


def get_artifact_reference(arguments: Dict) -> Dict:
    """
    Get secure presigned URL for large artifacts.
    Gracefully handles missing files - returns informative error without failing.
    
    Inputs:
        logKey: S3 key of artifact (required)
        expirationMinutes: URL expiration (default: 15, max: 60)
    
    Returns:
        presignedUrl, s3Uri, sha256, size
    """
    log_key = arguments.get('logKey')
    expiration_seconds = min(arguments.get('expirationMinutes', 0) * 60 or PRESIGNED_URL_EXPIRATION, PRESIGNED_URL_EXPIRATION)
    
    if not log_key:
        return error_response(400, 'logKey is required')
    
    try:
        # Get file metadata using safe helper
        head_result = safe_s3_head(log_key)
        
        if not head_result['success']:
            return success_response({
                'logKey': log_key,
                'presignedUrl': None,
                's3Uri': f's3://{LOGS_BUCKET}/{log_key}',
                'size': 0,
                'sizeHuman': '0 B',
                'fileNotFound': True,
                'warning': head_result.get('error', 'File not found or inaccessible'),
                'suggestion': 'The artifact may not exist or may have been cleaned up. Try listing available logs first.'
            })
        
        # Generate presigned URL
        try:
            presigned_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': LOGS_BUCKET, 'Key': log_key},
                ExpiresIn=expiration_seconds
            )
        except Exception as e:
            return success_response({
                'logKey': log_key,
                'presignedUrl': None,
                's3Uri': f's3://{LOGS_BUCKET}/{log_key}',
                'size': head_result['size'],
                'sizeHuman': format_bytes(head_result['size']),
                'warning': f'Could not generate presigned URL: {str(e)}',
                'suggestion': 'Use AWS CLI or console to download the file directly'
            })
        
        return success_response({
            'logKey': log_key,
            'presignedUrl': presigned_url,
            's3Uri': f's3://{LOGS_BUCKET}/{log_key}',
            'size': head_result['size'],
            'sizeHuman': format_bytes(head_result['size']),
            'contentType': head_result.get('content_type', 'application/octet-stream'),
            'lastModified': head_result.get('last_modified'),
            'expiresIn': f'{expiration_seconds} seconds',
            'note': 'Use this URL to download the full artifact. URL expires after the specified time.'
        })
        
    except Exception as e:
        return success_response({
            'logKey': log_key,
            'presignedUrl': None,
            's3Uri': f's3://{LOGS_BUCKET}/{log_key}',
            'size': 0,
            'sizeHuman': '0 B',
            'error': f'Unexpected error: {str(e)}',
            'suggestion': 'Check if the log key is correct and try again'
        })


def generate_incident_summary(arguments: Dict) -> Dict:
    """
    Generate AI-ready structured incident summary with Pod/Node failure triage.
    Requires finding_ids to ground summary in verified evidence.
    Falls back to full retrieval if finding_ids not provided (backward compat).
    
    Inputs:
        instanceId: EC2 instance ID (required)
        finding_ids: List of finding IDs from errors/search to include (recommended)
        includeRecommendations: Include remediation suggestions (default: true)
        includeTriage: Include pod/node failure triage analysis (default: true)
        incident_time: ISO8601 timestamp of the incident (optional)
        start_time: Start of analysis window ISO8601 (optional)
        end_time: End of analysis window ISO8601 (optional)
    
    Returns:
        summary with criticalFindings, timeline, recommendations, artifactLinks,
        pod_node_triage, confidence, gaps, time window metadata
    """
    import time
    start_time_perf = time.time()
    MAX_EXECUTION_TIME = 25  # Leave buffer for API Gateway 29s timeout
    
    def check_timeout():
        elapsed = time.time() - start_time_perf
        if elapsed > MAX_EXECUTION_TIME:
            raise TimeoutError(f"Execution time exceeded {MAX_EXECUTION_TIME}s")
        return elapsed
    
    instance_id = arguments.get('instanceId')
    finding_ids = arguments.get('finding_ids', [])
    include_recommendations = arguments.get('includeRecommendations', True)
    include_triage = arguments.get('includeTriage', True)
    
    # ── Resolve time window ──
    time_window_resolved = TimeWindowResolver.resolve(arguments)
    tw_meta = TimeWindowResolver.window_metadata(time_window_resolved)
    
    if not instance_id:
        return error_response(400, 'instanceId is required')
    
    if not finding_ids:
        return error_response(400,
            'finding_ids is required. Call errors tool first to get finding_ids (F-001 format), '
            'then pass them here to ground the summary in verified evidence.')
    
    try:
        # Get bundle completeness - don't fail if this errors
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
        
        # Get error summary - don't fail if this errors
        error_data = {}
        try:
            check_timeout()
            error_result = get_error_summary({
                'instanceId': instance_id,
                'severity': 'all',
                'pageSize': 200,
                'incident_time': arguments.get('incident_time'),
                'start_time': arguments.get('start_time'),
                'end_time': arguments.get('end_time'),
            })
            if error_result['statusCode'] == 200:
                error_data = json.loads(error_result['body'])
        except TimeoutError:
            raise
        except Exception as e:
            print(f"Warning: Could not get error summary: {str(e)}")
        
        check_timeout()
        
        # Build summary with available data
        all_findings = error_data.get('findings', [])
        summary_counts = error_data.get('summary', {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0})
        
        # If finding_ids provided, filter to only those findings
        grounded = bool(finding_ids)
        if finding_ids:
            finding_id_set = set(finding_ids)
            findings = [f for f in all_findings if f.get('finding_id') in finding_id_set]
            # Warn about unresolved IDs
            resolved_ids = {f.get('finding_id') for f in findings}
            unresolved_ids = finding_id_set - resolved_ids
        else:
            findings = all_findings
            unresolved_ids = set()
        
        critical_findings = [f for f in findings if f.get('severity') == 'critical'][:10]
        high_findings = [f for f in findings if f.get('severity') == 'high'][:10]
        medium_findings = [f for f in findings if f.get('severity') == 'medium'][:5]
        
        # Identify affected components
        affected_components = set()
        for finding in findings:
            component = categorize_log_source(finding.get('file', ''))
            affected_components.add(component)
        
        # Confidence assessment
        if grounded and len(findings) >= 3 and critical_findings:
            confidence = 'high'
        elif grounded and len(findings) >= 1:
            confidence = 'medium'
        elif not grounded and critical_findings:
            confidence = 'medium'
        else:
            confidence = 'low'
        
        # Identify gaps
        gaps = []
        if not grounded:
            gaps.append('Summary not grounded in specific finding_ids — may include unverified patterns')
        if unresolved_ids:
            gaps.append(f'{len(unresolved_ids)} finding_ids could not be resolved: {list(unresolved_ids)[:5]}')
        coverage = error_data.get('coverage_report', {})
        if coverage and not coverage.get('scan_complete', True):
            gaps.append('Not all log files were scanned — some findings may be missing')
        
        summary = {
            'instanceId': instance_id,
            'generatedAt': datetime.utcnow().isoformat(),
            'executionTimeMs': int((time.time() - start_time_perf) * 1000),
            'grounded': grounded,
            'confidence': confidence,
            'gaps': gaps,
            **tw_meta,
            'bundleStatus': {
                'complete': bundle_data.get('complete', False),
                'fileCount': bundle_data.get('fileCount', 0),
                'totalSize': bundle_data.get('totalSizeHuman', 'unknown')
            },
            'errorSummary': {
                'critical': summary_counts.get('critical', 0),
                'high': summary_counts.get('high', 0),
                'medium': summary_counts.get('medium', 0),
                'low': summary_counts.get('low', 0),
                'info': summary_counts.get('info', 0),
                'total': len(all_findings)
            },
            'criticalFindings': [
                {
                    'finding_id': f.get('finding_id'),
                    'file': f.get('file'),
                    'fullKey': f.get('fullKey'),
                    'pattern': f.get('pattern'),
                    'count': f.get('count'),
                    'sample': f.get('sample', '')[:200]
                }
                for f in critical_findings
            ],
            'highFindings': [
                {
                    'finding_id': f.get('finding_id'),
                    'file': f.get('file'),
                    'fullKey': f.get('fullKey'),
                    'pattern': f.get('pattern'),
                    'count': f.get('count')
                }
                for f in high_findings
            ],
            'affectedComponents': list(affected_components),
        }
        
        # Add info if no findings
        if not findings:
            summary['info'] = 'No error findings detected. Node may be healthy or logs not yet collected.'
        
        # Add recommendations if requested
        if include_recommendations:
            summary['recommendations'] = generate_recommendations(critical_findings, high_findings, medium_findings)
        
        # Add artifact links for key files
        summary['artifactLinks'] = []
        for finding in critical_findings[:5]:
            if finding.get('fullKey'):
                summary['artifactLinks'].append({
                    'finding_id': finding.get('finding_id'),
                    'file': finding.get('file'),
                    'key': finding.get('fullKey'),
                    'action': f'read(logKey="{finding.get("fullKey")}")'
                })
        
        if include_triage and findings:
            try:
                check_timeout()
                triage_result = perform_pod_node_triage(instance_id, findings, bundle_data)
                summary['pod_node_triage'] = triage_result
            except TimeoutError:
                summary['pod_node_triage'] = {
                    'triageVersion': '1.0',
                    'warning': 'Triage skipped due to time constraints. Call with includeTriage=true separately.',
                    'analyzedAt': datetime.utcnow().isoformat()
                }
            except Exception as e:
                print(f"Warning: Triage analysis failed: {str(e)}")
                summary['pod_node_triage'] = {
                    'triageVersion': '1.0',
                    'error': f'Triage analysis failed: {str(e)}',
                    'analyzedAt': datetime.utcnow().isoformat()
                }
        elif include_triage:
            summary['pod_node_triage'] = {
                'triageVersion': '1.0',
                'info': 'No findings to triage. Node may be healthy.',
                'analyzedAt': datetime.utcnow().isoformat(),
                'pod_states_detected': [],
                'node_conditions_detected': [],
                'most_likely_root_cause': None,
                'evidence': [],
                'coverage_report': {
                    'files_scanned': bundle_data.get('fileCount', 0),
                    'categories_checked': list(TRIAGE_CATEGORIES.keys()),
                    'categories_with_findings': []
                }
            }
        
        # Add caveat about analysis methodology
        summary['caveat'] = (
            'Root cause analysis is based on log pattern matching only. '
            'Verify findings by: (1) checking pod-level config (kubectl exec <pod> -- cat /etc/resolv.conf), '
            '(2) reviewing CoreDNS/kube-proxy/CNI pod logs, (3) checking kubelet --cluster-dns flag, '
            '(4) confirming node conditions (kubectl describe node). '
            'Log patterns indicate symptoms, not always root causes. '
            'Node-level /etc/resolv.conf showing VPC DNS is NORMAL — pod DNS is configured separately by kubelet.'
        )

        # Add next step guidance
        if summary.get('pod_node_triage', {}).get('most_likely_root_cause'):
            root_cause = summary['pod_node_triage']['most_likely_root_cause']
            summary['nextStep'] = f"Root cause identified: {root_cause['category_name']} ({root_cause['confidence']} confidence). Follow immediate_remediation_steps in pod_node_triage."
        else:
            summary['nextStep'] = 'Use search for detailed investigation of specific patterns'
        
        # Update execution time
        summary['executionTimeMs'] = int((time.time() - start_time_perf) * 1000)
        
        return success_response(summary)
    
    except TimeoutError as e:
        # Return partial summary on timeout
        return success_response({
            'instanceId': instance_id,
            'generatedAt': datetime.utcnow().isoformat(),
            'executionTimeMs': int((time.time() - start_time_perf) * 1000),
            'grounded': bool(finding_ids),
            'confidence': 'low',
            'gaps': ['Execution timed out — partial results only'],
            **tw_meta,
            'bundleStatus': bundle_data if bundle_data else {'complete': False, 'fileCount': 0, 'totalSize': 'unknown'},
            'errorSummary': error_data.get('summary', {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0, 'total': 0}) if error_data else {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0, 'total': 0},
            'criticalFindings': [],
            'highFindings': [],
            'affectedComponents': [],
            'recommendations': [],
            'artifactLinks': [],
            'pod_node_triage': {
                'triageVersion': '1.0',
                'warning': 'Analysis timed out. Try calling errors and summarize separately.'
            },
            'warning': f'Execution timed out: {str(e)}',
            'nextStep': 'Call errors first, then summarize with includeTriage=false'
        })
        
    except Exception as e:
        # Return partial summary on error, don't fail
        return success_response({
            'instanceId': instance_id,
            'generatedAt': datetime.utcnow().isoformat(),
            'grounded': bool(finding_ids),
            'confidence': 'none',
            'gaps': [f'Summary generation failed: {str(e)}'],
            'bundleStatus': {'complete': False, 'fileCount': 0, 'totalSize': 'unknown'},
            'errorSummary': {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0, 'total': 0},
            'criticalFindings': [],
            'highFindings': [],
            'affectedComponents': [],
            'recommendations': [],
            'artifactLinks': [],
            'pod_node_triage': {
                'triageVersion': '1.0',
                'error': f'Summary generation failed: {str(e)}'
            },
            'error': f'Could not generate complete summary: {str(e)}',
            'nextStep': 'Check if logs exist with validate'
        })


def quick_triage(arguments: Dict) -> Dict:
    """
    One-shot triage: validate + errors + summarize in a single call.
    Designed to minimize agent round-trips and avoid session timeouts.
    
    Inputs:
        instanceId: EC2 instance ID (required)
        severity: Filter findings by severity (default: all)
        includeTriage: Include pod/node failure triage (default: true)
        incident_time: ISO8601 timestamp of the incident (optional)
        start_time: Start of analysis window ISO8601 (optional)
        end_time: End of analysis window ISO8601 (optional)
    
    Returns:
        Combined validate + errors + summarize output in one response, with time window metadata.
    """
    import time
    start_time_perf = time.time()
    
    instance_id = arguments.get('instanceId')
    if not instance_id:
        return error_response(400, 'instanceId is required')
    
    severity_filter = arguments.get('severity', 'all')
    include_triage = arguments.get('includeTriage', True)
    
    # ── Resolve time window ──
    time_window_resolved = TimeWindowResolver.resolve(arguments)
    tw_meta = TimeWindowResolver.window_metadata(time_window_resolved)
    
    result = {
        'instanceId': instance_id,
        'generatedAt': datetime.utcnow().isoformat(),
        **tw_meta,
    }
    
    # Step 1: Validate bundle
    try:
        val_resp = validate_bundle_completeness({'instanceId': instance_id})
        if val_resp['statusCode'] == 200:
            val_data = json.loads(val_resp['body']) if isinstance(val_resp['body'], str) else val_resp['body']
            result['bundle'] = {
                'complete': val_data.get('complete', False),
                'fileCount': val_data.get('fileCount', 0),
                'totalSize': val_data.get('totalSizeHuman', 'unknown'),
                'missingPatterns': val_data.get('missingPatterns', []),
            }
        else:
            result['bundle'] = {'complete': False, 'warning': 'Could not validate bundle'}
    except Exception as e:
        result['bundle'] = {'complete': False, 'warning': str(e)}
    
    # Step 2: Get error findings
    findings = []
    summary_counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0}
    try:
        err_resp = get_error_summary({
            'instanceId': instance_id,
            'severity': severity_filter,
            'response_format': 'detailed',
            'pageSize': 200,
            'incident_time': arguments.get('incident_time'),
            'start_time': arguments.get('start_time'),
            'end_time': arguments.get('end_time'),
        })
        if err_resp['statusCode'] == 200:
            err_data = json.loads(err_resp['body']) if isinstance(err_resp['body'], str) else err_resp['body']
            findings = err_data.get('findings', [])
            summary_counts = err_data.get('summary', summary_counts)
            result['errorSummary'] = summary_counts
            result['totalFindings'] = err_data.get('totalFindings', len(findings))
            result['findings'] = findings[:30]  # Top 30 for response size
        else:
            result['errorSummary'] = summary_counts
            result['totalFindings'] = 0
            result['findings'] = []
    except Exception as e:
        result['errorSummary'] = summary_counts
        result['totalFindings'] = 0
        result['findings'] = []
        result['errorWarning'] = str(e)
    
    # Step 3: Prerequisite component checks
    prerequisite_checks = {}
    try:
        bundle_files_list = []
        bundle_info_qt = find_latest_bundle_files(instance_id)
        if bundle_info_qt.get('success'):
            bundle_files_list = bundle_info_qt.get('files', [])
        if bundle_files_list:
            bundle_lower = [f.lower() for f in bundle_files_list]
            # CSI driver presence
            has_ebs_csi = any('ebs-csi' in f or 'ebs_csi' in f for f in bundle_lower)
            has_efs_csi = any('efs-csi' in f or 'efs_csi' in f for f in bundle_lower)
            # CNI presence
            has_cni = any('aws-node' in f or 'ipamd' in f or '10-aws' in f for f in bundle_lower)
            # CoreDNS presence
            has_coredns = any('coredns' in f or 'kube-dns' in f for f in bundle_lower)
            # kube-proxy presence
            has_kube_proxy = any('kube-proxy' in f or 'kube_proxy' in f for f in bundle_lower)

            prerequisite_checks = {
                'ebsCsiDriver': 'present' if has_ebs_csi else 'not_found',
                'efsCsiDriver': 'present' if has_efs_csi else 'not_found',
                'vpcCni': 'present' if has_cni else 'not_found',
                'coreDns': 'present' if has_coredns else 'not_found',
                'kubeProxy': 'present' if has_kube_proxy else 'not_found',
            }
            missing_components = [k for k, v in prerequisite_checks.items() if v == 'not_found']
            if missing_components:
                prerequisite_checks['warning'] = (
                    f"No log files found for: {', '.join(missing_components)}. "
                    "These components may not be installed or their logs were not collected. "
                    "Verify component installation before investigating related errors."
                )
    except Exception:
        pass
    if prerequisite_checks:
        result['prerequisiteChecks'] = prerequisite_checks

    # Step 4: Triage analysis (inline, skip the summarize overhead)
    if include_triage and findings:
        try:
            bundle_data = result.get('bundle', {})
            triage = perform_pod_node_triage(instance_id, findings, bundle_data)
            result['triage'] = triage
            
            # Extract key fields for easy consumption
            root_cause = triage.get('most_likely_root_cause')
            if root_cause:
                result['rootCause'] = {
                    'category': root_cause.get('category_name'),
                    'confidence': root_cause.get('confidence'),
                    'summary': root_cause.get('summary'),
                    'detail': root_cause.get('technical_detail', '')[:300],
                }
                result['remediation'] = triage.get('immediate_remediation_steps', [])[:5]
                result['followupCommands'] = triage.get('followup_validation_commands', [])[:5]
        except Exception as e:
            result['triage'] = {'error': str(e)}
    elif include_triage:
        result['triage'] = {'info': 'No findings to triage — node may be healthy'}
    
    # Step 5: Recommendations
    critical = [f for f in findings if f.get('severity') == 'critical'][:10]
    high = [f for f in findings if f.get('severity') == 'high'][:10]
    medium = [f for f in findings if f.get('severity') == 'medium'][:5]
    result['recommendations'] = generate_recommendations(critical, high, medium)
    
    # Step 6: Top evidence excerpts — gives agent enough context to avoid follow-up searches
    top_evidence = []
    seen_samples = set()
    for f in (critical + high + medium):
        sample = f.get('sample', '')
        if sample and sample[:80] not in seen_samples:
            seen_samples.add(sample[:80])
            top_evidence.append({
                'finding_id': f.get('finding_id'),
                'severity': f.get('severity'),
                'file': f.get('file'),
                'pattern': f.get('pattern'),
                'count': f.get('count'),
                'excerpt': sample[:300],
            })
        if len(top_evidence) >= 15:
            break
    result['topEvidence'] = top_evidence
    
    # Step 7: Match relevant SOPs based on findings and triage results
    try:
        triage_cat = result.get('rootCause', {}).get('category') if result.get('rootCause') else None
        # Build issues list from findings for SOP matching
        sop_issues = []
        for f in findings[:50]:
            sop_issues.append({'message': f.get('pattern', '') + ' ' + f.get('sample', '')[:100], 'section': 'triage'})
        recommended_sops = match_sops_for_issues(
            issues=sop_issues,
            findings=findings[:50],
            triage_category=triage_cat,
            max_sops=5
        )
        if recommended_sops:
            result['recommendedSOPs'] = recommended_sops
    except Exception:
        pass  # SOP matching is best-effort, never block triage
    
    # Step 8: Investigation hints — guide agent to avoid rabbit-holing
    investigation_hints = []
    root_cause_cat = result.get('rootCause', {}).get('category', '') if result.get('rootCause') else ''
    root_cause_conf = result.get('rootCause', {}).get('confidence', '') if result.get('rootCause') else ''

    if root_cause_cat:
        hint_map = {
            'Volume/CSI Issues': 'Check CSI driver pod status and PV/PVC phase FIRST. If CSI driver is not running, all volume errors are secondary.',
            'Node Health Issues': 'Check node conditions (MemoryPressure, DiskPressure, PIDPressure) FIRST. OOMKill errors are symptoms, not root cause.',
            'CNI/Networking Issues': 'Check aws-node pod status and subnet IP availability FIRST. IP allocation errors cascade into pod scheduling failures.',
            'iptables/conntrack Issues': 'Check kube-proxy pod status FIRST. If kube-proxy is down, all service routing errors are expected.',
            'Scheduling Issues': 'Check node resource allocations and taints FIRST. Pending pods may be waiting for resources, not experiencing errors.',
            'Image Pull Issues': 'Check ECR authentication and network egress FIRST. Image pull failures often indicate IAM or network issues.',
            'DNS Issues': 'Check CoreDNS pod status FIRST. If CoreDNS is not running, all DNS errors are expected.',
            'Secrets/Webhook Issues': 'Check webhook endpoint availability FIRST. Webhook timeouts block pod creation.',
        }
        hint = hint_map.get(root_cause_cat, '')
        if hint:
            investigation_hints.append(hint)

    if root_cause_conf == 'low':
        investigation_hints.append(
            'Confidence is LOW. Consider pivoting: the root cause may not be in the collected logs. '
            'Check cluster-level events (kubectl get events -A) and control plane logs.'
        )

    if len(findings) > 50:
        investigation_hints.append(
            f'{len(findings)} findings detected. Focus on critical/high severity only. '
            'Do NOT chase medium/low findings until critical issues are resolved.'
        )

    investigation_hints.append(
        'TIME BUDGET: Spend no more than 2 minutes on any single hypothesis. '
        'If log evidence is inconclusive, pivot to live cluster checks (kubectl) instead of deeper log searches.'
    )

    result['investigationHints'] = investigation_hints

    # Confidence
    if critical and result.get('rootCause'):
        result['confidence'] = 'high'
    elif findings:
        result['confidence'] = 'medium'
    else:
        result['confidence'] = 'low'
    
    result['executionTimeMs'] = int((time.time() - start_time_perf) * 1000)
    sop_hint = ' Use get_sop to review the recommended SOPs for detailed remediation steps.' if result.get('recommendedSOPs') else ''
    result['nextStep'] = (
        f"Root cause: {result['rootCause']['category']} ({result['rootCause']['confidence']} confidence). "
        f"Review topEvidence excerpts and follow remediation steps. "
        f"Use read(logKey=...) only if you need full file content for a specific finding.{sop_hint}"
        if result.get('rootCause')
        else f'Review topEvidence excerpts. Use search only if a specific pattern needs deeper investigation.{sop_hint}'
    )
    
    return success_response(result)


def list_collection_history(arguments: Dict) -> Dict:
    """
    List historical log collections for audit and comparison.
    
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
    document_name = arguments.get('documentName', 'AWSSupport-CollectEKSInstanceLogs')
    
    try:
        filters = []
        if document_name:
            filters.append({'Key': 'DocumentNamePrefix', 'Values': [document_name]})
        if status_filter:
            filters.append({'Key': 'ExecutionStatus', 'Values': [status_filter]})
        
        # Support cross-region listing — try explicit region, then default, then common EKS regions
        target_region = arguments.get('region', DEFAULT_REGION)
        regions_to_try = [target_region]
        # If default region returned nothing, also try common EKS regions
        common_eks_regions = ['us-west-2', 'us-east-1', 'eu-west-1', 'ap-southeast-1']
        for r in common_eks_regions:
            if r not in regions_to_try:
                regions_to_try.append(r)

        collections = []
        searched_regions = []

        for region in regions_to_try:
            try:
                regional_ssm = get_regional_client('ssm', region)
                response = regional_ssm.describe_automation_executions(
                    Filters=filters,
                    MaxResults=max_results
                )
                
                for exec_meta in response.get('AutomationExecutionMetadataList', []):
                    # Filter by instance if specified
                    if instance_id:
                        params = exec_meta.get('Parameters', {})
                        exec_instance = params.get('EKSInstanceId', [''])[0]
                        if instance_id not in exec_instance:
                            continue
                    
                    # Check if S3 bundle still exists
                    exec_id = exec_meta['AutomationExecutionId']
                    params = exec_meta.get('Parameters', {})
                    exec_instance = params.get('EKSInstanceId', [''])[0]
                    bundle_exists = False
                    if exec_instance:
                        s3_check = safe_s3_list(f"eks_{exec_instance}_{exec_id}/", max_keys=1)
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
                # If we found results, stop searching more regions
                if collections:
                    break
            except Exception:
                searched_regions.append(f"{region} (error)")
                continue
        
        return success_response({
            'collections': collections,
            'count': len(collections),
            'searchedRegions': searched_regions,
            'filters': {
                'instanceId': instance_id,
                'status': status_filter,
                'documentName': document_name
            }
        })
        
    except Exception as e:
        return error_response(500, f'Failed to list history: {str(e)}')


# =============================================================================
# TIER 3: CLUSTER-LEVEL INTELLIGENCE
# =============================================================================

def cluster_health(arguments: Dict) -> Dict:
    """
    EKS cluster health overview.
    Enumerates all nodes, checks SSM status, instance metadata, and flags unhealthy nodes.

    Inputs:
        clusterName: EKS cluster name (required)
        region: AWS region (optional, auto-detected)
        includeSSMStatus: Check SSM agent per node (default: true)

    Returns:
        clusterInfo, nodes[], healthSummary
    """
    cluster_name = arguments.get('clusterName')
    if not cluster_name:
        return error_response(400, 'clusterName is required')

    include_ssm = arguments.get('includeSSMStatus', True)
    target_region, region_error = resolve_and_validate_region(arguments)
    if region_error:
        return region_error

    try:
        regional_eks = get_regional_client('eks', target_region)
        regional_ec2 = get_regional_client('ec2', target_region)
        regional_ssm = get_regional_client('ssm', target_region)

        # 1. Describe the cluster
        try:
            cluster_resp = regional_eks.describe_cluster(name=cluster_name)
            cluster_info = cluster_resp.get('cluster', {})
            cluster_meta = {
                'name': cluster_info.get('name'),
                'version': cluster_info.get('version'),
                'status': cluster_info.get('status'),
                'platformVersion': cluster_info.get('platformVersion'),
                'endpoint': cluster_info.get('endpoint', '')[:80] + '...',
                'region': target_region,
            }
        except Exception as e:
            return error_response(404, f'Cluster {cluster_name} not found in {target_region}: {str(e)}')

        # 2. List nodegroups
        nodegroups = []
        try:
            ng_resp = regional_eks.list_nodegroups(clusterName=cluster_name)
            for ng_name in ng_resp.get('nodegroups', []):
                try:
                    ng_detail = regional_eks.describe_nodegroup(
                        clusterName=cluster_name, nodegroupName=ng_name
                    )['nodegroup']
                    nodegroups.append({
                        'name': ng_name,
                        'status': ng_detail.get('status'),
                        'instanceTypes': ng_detail.get('instanceTypes', []),
                        'amiType': ng_detail.get('amiType'),
                        'desiredSize': ng_detail.get('scalingConfig', {}).get('desiredSize'),
                        'minSize': ng_detail.get('scalingConfig', {}).get('minSize'),
                        'maxSize': ng_detail.get('scalingConfig', {}).get('maxSize'),
                        'releaseVersion': ng_detail.get('releaseVersion'),
                    })
                except Exception:
                    nodegroups.append({'name': ng_name, 'status': 'DESCRIBE_FAILED'})
        except Exception:
            pass

        # 3. Find all EC2 instances tagged with this cluster
        paginator = regional_ec2.get_paginator('describe_instances')
        page_iter = paginator.paginate(
            Filters=[
                {'Name': 'tag:eks:cluster-name', 'Values': [cluster_name]},
                {'Name': 'instance-state-name', 'Values': ['running', 'stopped', 'pending', 'stopping', 'shutting-down']},
            ]
        )

        nodes = []
        instance_ids = []
        for page in page_iter:
            for res in page.get('Reservations', []):
                for inst in res.get('Instances', []):
                    iid = inst['InstanceId']
                    instance_ids.append(iid)
                    tags = {t['Key']: t['Value'] for t in inst.get('Tags', [])}
                    nodes.append({
                        'instanceId': iid,
                        'instanceType': inst.get('InstanceType'),
                        'availabilityZone': inst.get('Placement', {}).get('AvailabilityZone'),
                        'state': inst.get('State', {}).get('Name'),
                        'launchTime': inst.get('LaunchTime'),
                        'privateIp': inst.get('PrivateIpAddress'),
                        'imageId': inst.get('ImageId'),
                        'nodegroup': tags.get('eks:nodegroup-name', 'unknown'),
                        'name': tags.get('Name', ''),
                        'ssmStatus': None,
                    })

        # 4. Check SSM agent status in batches
        if include_ssm and instance_ids:
            ssm_status_map = {}
            # SSM DescribeInstanceInformation supports InstanceInformationFilterList
            # to filter by instance IDs, avoiding scanning the entire account.
            # Process in chunks of 50 (API limit per filter).
            try:
                cluster_ids_set = set(instance_ids)
                for i in range(0, len(instance_ids), 50):
                    chunk = instance_ids[i:i+50]
                    ssm_paginator = regional_ssm.get_paginator('describe_instance_information')
                    for page in ssm_paginator.paginate(
                        Filters=[{'Key': 'InstanceIds', 'Values': chunk}]
                    ):
                        for info in page.get('InstanceInformationList', []):
                            if info['InstanceId'] in cluster_ids_set:
                                ssm_status_map[info['InstanceId']] = {
                                    'pingStatus': info.get('PingStatus'),
                                    'agentVersion': info.get('AgentVersion'),
                                    'platformName': info.get('PlatformName'),
                                    'lastPingTime': info.get('LastPingDateTime'),
                                }
            except Exception as e:
                print(f"SSM status check failed: {e}")

            for node in nodes:
                ssm_info = ssm_status_map.get(node['instanceId'])
                if ssm_info:
                    node['ssmStatus'] = ssm_info
                else:
                    node['ssmStatus'] = {'pingStatus': 'NotRegistered', 'agentVersion': None}

        # 5. Build health summary
        total = len(nodes)
        running = sum(1 for n in nodes if n['state'] == 'running')
        ssm_online = sum(1 for n in nodes if n.get('ssmStatus', {}).get('pingStatus') == 'Online')
        ssm_offline = total - ssm_online if include_ssm else None

        # Group by AZ
        az_distribution = {}
        for n in nodes:
            az = n.get('availabilityZone', 'unknown')
            az_distribution[az] = az_distribution.get(az, 0) + 1

        # Group by nodegroup
        ng_distribution = {}
        for n in nodes:
            ng = n.get('nodegroup', 'unknown')
            ng_distribution[ng] = ng_distribution.get(ng, 0) + 1

        # Flag unhealthy nodes
        unhealthy = []
        for n in nodes:
            issues = []
            if n['state'] != 'running':
                issues.append(f"ec2State={n['state']}")
            if include_ssm and n.get('ssmStatus', {}).get('pingStatus') != 'Online':
                issues.append(f"ssm={n.get('ssmStatus', {}).get('pingStatus', 'unknown')}")
            if issues:
                unhealthy.append({'instanceId': n['instanceId'], 'issues': issues})

        health_summary = {
            'totalNodes': total,
            'running': running,
            'ssmOnline': ssm_online,
            'ssmOffline': ssm_offline,
            'unhealthyCount': len(unhealthy),
            'azDistribution': az_distribution,
            'nodegroupDistribution': ng_distribution,
        }

        # Confidence assessment 
        gaps = []
        if not include_ssm:
            gaps.append('SSM status not checked — some unhealthy nodes may be missed')
        if total == 0:
            gaps.append('No nodes found — cluster may be empty or tag filter mismatch')
        if include_ssm and ssm_offline and ssm_offline > 0:
            gaps.append(f'{ssm_offline} nodes not reachable via SSM — cannot collect logs from these')

        if total > 0 and include_ssm and ssm_online == total:
            confidence = 'high'
        elif total > 0 and include_ssm:
            confidence = 'medium'
        elif total > 0:
            confidence = 'low'
        else:
            confidence = 'none'

        return success_response({
            'cluster': cluster_meta,
            'nodegroups': nodegroups,
            'nodes': nodes,
            'unhealthyNodes': unhealthy,
            'healthSummary': health_summary,
            'region': target_region,
            'confidence': confidence,
            'gaps': gaps,
            'nextStep': 'Use compare_nodes to diff specific nodes, or batch_collect to sample unhealthy nodes' if unhealthy else 'Cluster looks healthy. Use collect on a specific node if needed.',
        })

    except Exception as e:
        return error_response(500, f'cluster_health failed: {str(e)}')


def compare_nodes(arguments: Dict) -> Dict:
    """
    Diff error findings and health between two or more nodes.
    Returns structured diff: common issues vs. unique-to-each-node.

    Inputs:
        instanceIds: list of 2+ instance IDs (required)
        compareFields: "errors", "config", "all" (default: "all")

    Returns:
        commonFindings[], uniqueFindings{}, comparisonMatrix
    """
    instance_ids = arguments.get('instanceIds', [])
    if not instance_ids or len(instance_ids) < 2:
        return error_response(400, 'instanceIds must contain at least 2 instance IDs')
    # Deduplicate while preserving order
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
        return error_response(400, 'Maximum 10 nodes for comparison')

    # Validate region
    target_region, region_error = resolve_and_validate_region(arguments)
    if region_error:
        return region_error

    compare_fields = arguments.get('compareFields', 'all')

    try:
        node_findings = {}
        node_configs = {}

        def _gather_node_data(iid):
            """Gather findings + config for a single node (runs in thread)."""
            nf = []
            nc = {}
            if compare_fields in ('errors', 'all'):
                prefix = f"eks_{iid}"
                try:
                    idx = find_findings_index(prefix)
                    if idx:
                        resp = s3_client.get_object(Bucket=LOGS_BUCKET, Key=idx)
                        findings_data = json.loads(resp['Body'].read().decode('utf-8'))
                        nf = findings_data.get('findings', [])
                    else:
                        # No pre-built index — don't do inline scan (too slow for gateway timeout).
                        # Return a marker so caller knows this node needs collection first.
                        nf = [{'error': f'No findings index for {iid}. Run collect first and wait for completion.', 'needsCollection': True}]
                except Exception as e:
                    nf = [{'error': f'Could not load findings: {str(e)}'}]

            if compare_fields in ('config', 'all'):
                # Use shared latest-bundle discovery
                bundle_info = find_latest_bundle_files(iid)
                extracted_prefix = None
                if bundle_info['success']:
                    extracted_prefix = bundle_info['bundle_prefix'] + '/extracted/'

                if extracted_prefix:
                    config_files = [
                        ('kubelet_config', f"{extracted_prefix}kubelet-config.json"),
                        ('kubelet_flags', f"{extracted_prefix}kubelet-flags"),
                        ('containerd_config', f"{extracted_prefix}containerd-config.toml"),
                    ]
                else:
                    config_files = []

                for config_name, config_key in config_files:
                    result = safe_s3_read(config_key, max_size=65536)
                    if result.get('success'):
                        nc[config_name] = result['content'][:2000]
                    else:
                        nc[config_name] = None
            return iid, nf, nc

        # Parallel per-node gathering
        with ThreadPoolExecutor(max_workers=min(len(instance_ids), 10)) as executor:
            futures = {executor.submit(_gather_node_data, iid): iid for iid in instance_ids}
            for future in as_completed(futures):
                iid_key = futures[future]
                try:
                    iid, nf, nc = future.result()
                    node_findings[iid] = nf
                    node_configs[iid] = nc
                except Exception as e:
                    node_findings[iid_key] = [{'error': f'Failed to gather data: {str(e)}'}]
                    node_configs[iid_key] = {}

        # Build comparison: find common vs unique error patterns
        common_findings = []
        unique_findings = {}

        if compare_fields in ('errors', 'all') and node_findings:
            # Normalize findings to comparable signatures
            def finding_signature(f):
                # Skip error entries from failed loads — they have no pattern/severity
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
                    common_findings.append({
                        **data['finding'],
                        'presentOnAllNodes': True,
                    })
                else:
                    for iid in data['nodes']:
                        unique_findings[iid].append({
                            **data['finding'],
                            'uniqueTo': iid,
                        })

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
                    ref_val = ref_config.get(key)
                    other_val = other_config.get(key)
                    if ref_val != other_val:
                        diffs.append({
                            'configFile': key,
                            'referenceNode': ref_id,
                            'comparedNode': iid,
                            'match': False,
                            'note': 'Content differs' if (ref_val and other_val) else 'Missing on one node',
                        })
                config_diffs[f"{ref_id}_vs_{iid}"] = diffs if diffs else [{'match': True, 'note': 'Configs identical'}]

        # Summary matrix
        matrix = []
        for iid in instance_ids:
            total_findings = len(node_findings.get(iid, []))
            unique_count = len(unique_findings.get(iid, []))
            critical_count = sum(1 for f in node_findings.get(iid, [])
                                 if f.get('severity') == 'critical')
            matrix.append({
                'instanceId': iid,
                'totalFindings': total_findings,
                'criticalFindings': critical_count,
                'uniqueFindings': unique_count,
                'commonFindings': total_findings - unique_count,
            })

        # Confidence assessment 
        gaps = []
        nodes_without_index = [iid for iid, findings in node_findings.items()
                               if findings and isinstance(findings[0], dict) and findings[0].get('needsCollection')]
        if nodes_without_index:
            gaps.append(f'No findings index for: {nodes_without_index}. Run collect first.')
        nodes_with_errors = [iid for iid, findings in node_findings.items()
                             if findings and isinstance(findings[0], dict) and 'error' in findings[0] and 'severity' not in findings[0]]
        if nodes_with_errors:
            gaps.append(f'Failed to load findings for: {nodes_with_errors}')
        if compare_fields != 'all':
            gaps.append(f'Only compared {compare_fields} — use compareFields=all for full comparison')

        if not gaps and len(common_findings) + sum(len(v) for v in unique_findings.values()) > 0:
            confidence = 'high'
        elif not nodes_without_index and not nodes_with_errors:
            confidence = 'medium'
        else:
            confidence = 'low'

        return success_response({
            'comparedNodes': instance_ids,
            'commonFindings': common_findings,
            'commonFindingsCount': len(common_findings),
            'uniqueFindings': unique_findings,
            'configDiffs': config_diffs,
            'comparisonMatrix': matrix,
            'insight': _generate_comparison_insight(common_findings, unique_findings, instance_ids),
            'confidence': confidence,
            'gaps': gaps,
            'caveat': (
                'Comparison is based on pre-indexed error findings from log bundles. '
                'Differences may reflect different workloads rather than configuration issues. '
                'Config diffs show file-level differences — verify significance by checking '
                'kubelet flags and node group settings.'
            ),
            'nextStep': 'Common findings suggest a cluster-wide issue. Unique findings point to node-specific problems.',
        })

    except Exception as e:
        return error_response(500, f'compare_nodes failed: {str(e)}')


def _generate_comparison_insight(common: List, unique: Dict, instance_ids: List[str]) -> str:
    """Generate a human-readable insight from the comparison."""
    common_count = len(common)
    total_unique = sum(len(v) for v in unique.values())

    if common_count > 0 and total_unique == 0:
        return f"All {len(instance_ids)} nodes share the same {common_count} findings. This is likely a cluster-wide issue (bad AMI, misconfigured nodegroup, or control plane problem)."
    elif common_count == 0 and total_unique > 0:
        return f"No common findings across nodes. Each node has unique issues — investigate individually."
    elif common_count > total_unique:
        return f"{common_count} common findings vs {total_unique} unique. Mostly a shared problem with some node-specific noise."
    else:
        return f"{common_count} common, {total_unique} unique findings. Mixed picture — check unique findings for the root cause on specific nodes."


def batch_collect(arguments: Dict) -> Dict:
    """
    Batch log collection with statistical sampling.
    Triages nodes, groups by failure signature, samples representatives.

    Inputs:
        clusterName: EKS cluster name (required)
        region: AWS region (optional)
        filter: "all", "unhealthy", "notready" (default: "unhealthy")
            - unhealthy: EC2 state != running OR SSM != Online
            - notready: SSM not Online (regardless of EC2 state) — targets nodes that can't run SSM commands
        strategy: "sample" or "all" (default: "sample")
        samplesPerBucket: nodes per bucket (default: 3, max: 5)
        maxTotalCollections: hard cap (default: 15, max: 15)
        groupBy: "auto", "az", "nodegroup", "instance-type", "ami" (default: "auto")
        dryRun: preview only (default: false)

    Returns:
        buckets[], plannedCollections, executions[] (if not dryRun)
    """
    cluster_name = arguments.get('clusterName')
    if not cluster_name:
        return error_response(400, 'clusterName is required')

    target_region, region_error = resolve_and_validate_region(arguments)
    if region_error:
        return region_error
    node_filter = arguments.get('filter', 'unhealthy')
    # Validate filter parameter
    valid_filters = ('all', 'unhealthy', 'notready')
    if node_filter not in valid_filters:
        return error_response(400, f"Invalid filter '{node_filter}'. Must be one of: {', '.join(valid_filters)}")
    strategy = arguments.get('strategy', 'sample')
    samples_per_bucket = min(arguments.get('samplesPerBucket', 3), 5)
    max_total = min(arguments.get('maxTotalCollections', 15), 15)
    group_by = arguments.get('groupBy', 'auto')
    dry_run = arguments.get('dryRun', False)

    try:
        regional_eks = get_regional_client('eks', target_region)
        regional_ec2 = get_regional_client('ec2', target_region)
        regional_ssm = get_regional_client('ssm', target_region)

        # 1. Get all cluster nodes
        paginator = regional_ec2.get_paginator('describe_instances')
        page_iter = paginator.paginate(
            Filters=[
                {'Name': 'tag:eks:cluster-name', 'Values': [cluster_name]},
                {'Name': 'instance-state-name', 'Values': ['running', 'stopped', 'pending', 'stopping', 'shutting-down']},
            ]
        )

        all_nodes = []
        for page in page_iter:
            for res in page.get('Reservations', []):
                for inst in res.get('Instances', []):
                    tags = {t['Key']: t['Value'] for t in inst.get('Tags', [])}
                    all_nodes.append({
                        'instanceId': inst['InstanceId'],
                        'state': inst.get('State', {}).get('Name'),
                        'instanceType': inst.get('InstanceType'),
                        'az': inst.get('Placement', {}).get('AvailabilityZone'),
                        'imageId': inst.get('ImageId'),
                        'nodegroup': tags.get('eks:nodegroup-name', 'unknown'),
                        'launchTime': inst.get('LaunchTime'),
                    })

        if not all_nodes:
            return error_response(404, f'No nodes found for cluster {cluster_name} in {target_region}')

        # 2. Check SSM status to identify unhealthy nodes (filtered to cluster nodes only)
        ssm_status = {}
        node_ids = [n['instanceId'] for n in all_nodes]
        try:
            for i in range(0, len(node_ids), 50):
                chunk = node_ids[i:i+50]
                ssm_paginator = regional_ssm.get_paginator('describe_instance_information')
                for page in ssm_paginator.paginate(
                    Filters=[{'Key': 'InstanceIds', 'Values': chunk}]
                ):
                    for info in page.get('InstanceInformationList', []):
                        ssm_status[info['InstanceId']] = info.get('PingStatus', 'Unknown')
        except Exception:
            pass

        # 3. Apply filter
        filtered_nodes = []
        for node in all_nodes:
            ssm_ping = ssm_status.get(node['instanceId'], 'NotRegistered')
            node['ssmPingStatus'] = ssm_ping
            # unhealthy: EC2 not running OR SSM not Online
            is_unhealthy = (node['state'] != 'running') or (ssm_ping != 'Online')
            # notready: SSM not Online — these nodes can't execute SSM commands
            # (a subset of unhealthy focused on SSM reachability regardless of EC2 state)
            is_not_ready = ssm_ping != 'Online'

            if node_filter == 'all':
                filtered_nodes.append(node)
            elif node_filter == 'unhealthy' and is_unhealthy:
                filtered_nodes.append(node)
            elif node_filter == 'notready' and is_not_ready:
                filtered_nodes.append(node)

        # If filter returned no results, return a clear message
        if not filtered_nodes and node_filter in ('unhealthy', 'notready'):
            return success_response({
                'message': f'No {node_filter} nodes found — cluster looks healthy',
                'totalNodes': len(all_nodes),
                'filteredNodes': 0,
                'filter': node_filter,
                'buckets': [],
                'plannedCollections': 0,
            })

        # 4. Group into buckets
        buckets = {}
        for node in filtered_nodes:
            if group_by == 'az':
                key = node['az']
            elif group_by == 'nodegroup':
                key = node['nodegroup']
            elif group_by == 'instance-type':
                key = node['instanceType']
            elif group_by == 'ami':
                key = node['imageId']
            else:
                # Auto: combine nodegroup + AZ + state + SSM status
                key = f"{node['nodegroup']}|{node['az']}|{node['state']}|{node['ssmPingStatus']}"

            if key not in buckets:
                buckets[key] = {
                    'signature': key,
                    'nodes': [],
                    'count': 0,
                }
            buckets[key]['nodes'].append(node)
            buckets[key]['count'] += 1

        # 5. Select samples from each bucket
        bucket_list = []
        total_planned = 0
        for sig, bucket in buckets.items():
            if strategy == 'sample':
                sample_count = min(samples_per_bucket, bucket['count'])
            else:
                sample_count = bucket['count']

            # Respect hard cap
            if total_planned + sample_count > max_total:
                sample_count = max(0, max_total - total_planned)

            sample_nodes = bucket['nodes'][:sample_count]
            total_planned += len(sample_nodes)

            bucket_list.append({
                'signature': sig,
                'totalNodes': bucket['count'],
                'sampleCount': len(sample_nodes),
                'sampleNodes': [n['instanceId'] for n in sample_nodes],
                'representativeInfo': {
                    'instanceType': sample_nodes[0]['instanceType'] if sample_nodes else None,
                    'az': sample_nodes[0]['az'] if sample_nodes else None,
                    'nodegroup': sample_nodes[0]['nodegroup'] if sample_nodes else None,
                    'imageId': sample_nodes[0]['imageId'] if sample_nodes else None,
                },
            })

        # 6. Dry run — just return the plan
        if dry_run:
            return success_response({
                'dryRun': True,
                'clusterName': cluster_name,
                'region': target_region,
                'totalNodes': len(all_nodes),
                'filteredNodes': len(filtered_nodes),
                'filter': node_filter,
                'strategy': strategy,
                'bucketCount': len(bucket_list),
                'buckets': bucket_list,
                'plannedCollections': total_planned,
                'message': f'{len(filtered_nodes)} nodes grouped into {len(bucket_list)} buckets. Will collect from {total_planned} representative nodes. Re-run with dryRun=false to proceed.',
            })

        # 7. Execute collections
        batch_id = hashlib.sha256(f"{cluster_name}-{datetime.utcnow().isoformat()}".encode(), usedforsecurity=False).hexdigest()[:12]
        executions = []

        for bucket in bucket_list:
            for iid in bucket['sampleNodes']:
                try:
                    # Reuse existing collect logic
                    collect_args = {
                        'instanceId': iid,
                        'region': target_region,
                        'idempotencyToken': f"batch-{batch_id}-{iid}",
                    }
                    result = start_log_collection(collect_args)
                    result_body = json.loads(result.get('body', '{}'))
                    executions.append({
                        'instanceId': iid,
                        'bucket': bucket['signature'],
                        'executionId': result_body.get('executionId'),
                        'status': 'Started' if result_body.get('success') else 'Failed',
                        'error': result_body.get('error'),
                    })
                except Exception as e:
                    executions.append({
                        'instanceId': iid,
                        'bucket': bucket['signature'],
                        'status': 'Failed',
                        'error': str(e),
                    })

        # Store batch metadata
        try:
            s3_client.put_object(
                Bucket=LOGS_BUCKET,
                Key=f"batches/{batch_id}/metadata.json",
                Body=json.dumps({
                    'batchId': batch_id,
                    'clusterName': cluster_name,
                    'region': target_region,
                    'createdAt': datetime.utcnow().isoformat(),
                    'executions': executions,
                    'buckets': bucket_list,
                }, default=str),
                ContentType='application/json',
            )
        except Exception:
            pass

        started = sum(1 for e in executions if e['status'] == 'Started')
        failed = sum(1 for e in executions if e['status'] == 'Failed')

        # Determine task state
        if failed == len(executions):
            task_state = 'failed'
        elif started > 0:
            task_state = 'running'
        else:
            task_state = 'failed'

        return success_response({
            'batchId': batch_id,
            'clusterName': cluster_name,
            'region': target_region,
            'totalNodes': len(all_nodes),
            'filteredNodes': len(filtered_nodes),
            'bucketCount': len(bucket_list),
            'buckets': bucket_list,
            'executions': executions,
            'collectionsStarted': started,
            'collectionsFailed': failed,
            'task': {
                'taskId': batch_id,
                'state': task_state,
                'message': f'{started} collections started, {failed} failed',
                'progress': 0 if task_state == 'running' else 100,
            },
            'nextStep': f'Use batch_status(batchId="{batch_id}") to poll all collections at once. Wait until allComplete=true before running analysis tools.',
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
            # Extract instanceId from parameters
            params = execution.get('Parameters', {})
            instance_id = params.get('InstanceId', [None])[0] if params.get('InstanceId') else None
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
    Extract and structure networking info from collected log bundles.
    Parses iptables, CNI config, routes, DNS, ENI, ipamd logs, and kube-proxy.

    Inputs:
        instanceId: EC2 instance ID (required)
        sections: comma-separated: "iptables,cni,routes,dns,eni,ipamd,kube_proxy" or "all" (default: "all")

    Returns:
        Structured networking diagnostics per section
    """
    instance_id = arguments.get('instanceId')
    if not instance_id:
        return error_response(400, 'instanceId is required')

    # Validate region and EKS instance
    target_region, region_error = resolve_and_validate_region(arguments, instance_id)
    if region_error:
        return region_error
    instance_error = validate_eks_instance(instance_id, target_region)
    if instance_error:
        return instance_error

    sections_str = arguments.get('sections', 'all')
    valid_sections = {'iptables', 'cni', 'routes', 'dns', 'eni', 'ipamd', 'kube_proxy'}
    if sections_str == 'all':
        sections = ['iptables', 'cni', 'routes', 'dns', 'eni', 'ipamd', 'kube_proxy']
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
                f'(collected at {bundle_collected_at}). The node state has likely changed since then. '
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
        fetch_sizes = {}  # key -> max_size

        if 'iptables' in sections:
            keys = find_files(['iptables', 'ip-tables', 'iptable'])[:6]
            section_file_map['iptables'] = keys
            for k in keys: files_to_fetch.add(k); fetch_sizes[k] = 262144
        if 'cni' in sections:
            keys = find_files(['aws-node', 'cni', 'ipamd-config', '10-aws'])[:5]
            section_file_map['cni'] = keys
            for k in keys: files_to_fetch.add(k); fetch_sizes[k] = 262144
        if 'routes' in sections:
            r_keys = find_files(['iproute', 'ip-route', 'ip_route', 'route-table', 'routes'])[:3]
            i_keys = find_files(['ifconfig', 'ip-addr', 'ip_addr', 'ipaddr', 'interfaces'])[:2]
            section_file_map['routes'] = r_keys
            section_file_map['routes_iface'] = i_keys
            for k in r_keys + i_keys: files_to_fetch.add(k); fetch_sizes[k] = 262144
        if 'dns' in sections:
            keys = find_files(['resolv', 'dns', 'coredns'])[:5]
            section_file_map['dns'] = keys
            for k in keys: files_to_fetch.add(k); fetch_sizes[k] = 262144
        if 'eni' in sections:
            keys = find_files(['eni', 'network-interface', 'eth'])[:3]
            section_file_map['eni'] = keys
            for k in keys: files_to_fetch.add(k); fetch_sizes[k] = 32768
        if 'ipamd' in sections:
            keys = find_files(['ipamd', 'aws_node', 'ip-address-management'])[:5]
            # If no ipamd-specific files found, fall back to aws-node logs
            if not keys:
                keys = find_files(['aws-node'])[:3]
            section_file_map['ipamd'] = keys
            for k in keys: files_to_fetch.add(k); fetch_sizes[k] = 524288
        if 'kube_proxy' in sections:
            keys = find_files(['kube-proxy', 'kube_proxy', 'kubeproxy'])[:5]
            section_file_map['kube_proxy'] = keys
            for k in keys: files_to_fetch.add(k); fetch_sizes[k] = 524288
            # Also grab conntrack/sysctl files for cross-reference
            ct_keys = find_files(['conntrack', 'nf_conntrack', 'sysctl'])[:3]
            section_file_map['kube_proxy_conntrack'] = ct_keys
            for k in ct_keys: files_to_fetch.add(k); fetch_sizes[k] = 65536
            # Grab modinfo/modules for IPVS kernel module detection
            mod_keys = find_files(['modinfo', 'modules', 'lsmod'])[:3]
            section_file_map['kube_proxy_modules'] = mod_keys
            for k in mod_keys: files_to_fetch.add(k); fetch_sizes[k] = 65536

        # Parallel S3 reads
        file_contents = {}
        def _fetch(key):
            r = safe_s3_read(key, max_size=fetch_sizes.get(key, 262144))
            return key, r.get('content', '') if r.get('success') else None

        with ThreadPoolExecutor(max_workers=10) as executor:
            fetch_list = list(files_to_fetch)  # Convert set to list for deterministic ordering
            for key, content in executor.map(_fetch, fetch_list):
                file_contents[key] = content

        def read_file_content(key, max_size=262144):
            """Read from pre-fetched cache. Falls back to direct S3 read if not cached."""
            cached = file_contents.get(key)
            if cached is not None:
                return cached
            # Fallback for files not in the pre-fetch set
            r = safe_s3_read(key, max_size=max_size)
            return r.get('content', '') if r.get('success') else None

        # =====================================================================
        # IPTABLES
        # =====================================================================
        if 'iptables' in sections:
            ipt_data = {'raw': None, 'chainCount': 0, 'ruleCount': 0, 'natRules': [], 'kubeProxyRules': [], 'snatRules': [], 'dnatRules': [], 'issues': []}

            # BUG FIX: Parse ALL iptables files and merge results.
            # KUBE-SVC/DNAT/SNAT/MASQUERADE rules live in the NAT table (iptables-nat.txt),
            # while FORWARD policy and filter chains live in iptables-filter.txt.
            # Previously we broke after the first file (usually iptables-filter.txt sorted first),
            # which meant kubeSvcRuleCount was always 0 even when kube-proxy was healthy.
            ipt_files = find_files(['iptables', 'ip-tables', 'iptable'])

            # Accumulate across ALL iptables files
            all_lines_merged = []       # all lines from all files
            all_nat_rules = []
            all_snat_rules = []
            all_dnat_rules = []
            all_kube_rules = []
            all_aws_cni_chains = []
            all_forward_policy = []
            total_rule_count = 0
            total_chain_count = 0
            source_files = []

            # Prefer iptables-save.txt (contains ALL tables) if available, otherwise merge individual files
            save_file = None
            nat_file = None
            filter_file = None
            for f in ipt_files:
                fl = f.lower()
                if 'iptables-save' in fl or 'iptables_save' in fl:
                    save_file = f
                elif 'iptables-nat' in fl or 'iptables_nat' in fl:
                    nat_file = f
                elif 'iptables-filter' in fl or 'iptables_filter' in fl:
                    filter_file = f

            # Determine which files to parse: prefer save file, else merge all unique files
            if save_file:
                files_to_parse = [save_file]
            else:
                # Parse all available iptables files (filter, nat, mangle, etc.)
                files_to_parse = ipt_files[:6]  # up to 6 files

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
                all_snat_rules.extend(l.strip() for l in lines if 'SNAT' in l or 'MASQUERADE' in l)
                all_dnat_rules.extend(l.strip() for l in lines if 'DNAT' in l)
                all_kube_rules.extend(l.strip() for l in lines if 'KUBE-' in l)
                all_aws_cni_chains.extend(l.strip() for l in lines if 'AWS-SNAT' in l or 'AWS-CONNMARK' in l or 'PREROUTING' in l)
                all_forward_policy.extend(l.strip() for l in lines if ':FORWARD' in l)

            ipt_data['ruleCount'] = total_rule_count
            ipt_data['chainCount'] = total_chain_count
            ipt_data['natRules'] = all_nat_rules[:30]
            ipt_data['snatRules'] = all_snat_rules[:20]
            ipt_data['dnatRules'] = all_dnat_rules[:20]
            ipt_data['kubeProxyRules'] = all_kube_rules[:30]
            ipt_data['awsCniChains'] = all_aws_cni_chains[:20]
            ipt_data['sourceFiles'] = source_files
            # Keep legacy sourceFile for backward compat
            ipt_data['sourceFile'] = source_files[0] if source_files else None

            if all_lines_merged:
                # --- FORWARD policy check (EKS guardrail) ---
                ipt_data['forwardPolicy'] = all_forward_policy[:5] if all_forward_policy else []
                has_forward_drop = any('DROP' in l for l in all_forward_policy)
                if has_forward_drop:
                    ipt_data['issues'].append(
                        'iptables FORWARD policy is DROP — this breaks pod networking on EKS. '
                        'Custom AMIs must set FORWARD policy to ACCEPT under kubelet.service. '
                        'Fix: add "ExecStartPre=/sbin/iptables -P FORWARD ACCEPT" to kubelet.service.'
                    )
                    issues_found.append({'section': 'iptables', 'severity': 'critical',
                                         'message': 'iptables FORWARD policy is DROP (breaks pod networking on custom AMIs)'})

                # Check for issues
                if total_rule_count == 0:
                    ipt_data['issues'].append('No iptables rules found — kube-proxy may not be running')
                    issues_found.append({'section': 'iptables', 'severity': 'critical', 'message': 'No iptables rules found'})
                if not any('KUBE-SERVICES' in l for l in all_lines_merged):
                    ipt_data['issues'].append('KUBE-SERVICES chain missing — kube-proxy not configured')
                    issues_found.append({'section': 'iptables', 'severity': 'warning', 'message': 'KUBE-SERVICES chain missing'})

                # CRITICAL CHECK: KUBE-SERVICES chain exists but has NO KUBE-SVC rules
                # Now checks across ALL tables (filter + nat + save) so nat table KUBE-SVC rules are counted
                has_kube_services_chain = any('KUBE-SERVICES' in l for l in all_lines_merged)
                kube_svc_rules = [l for l in all_lines_merged if 'KUBE-SVC-' in l]
                ipt_data['kubeSvcRuleCount'] = len(kube_svc_rules)
                if has_kube_services_chain and len(kube_svc_rules) == 0:
                    ipt_data['issues'].append(
                        'CRITICAL: KUBE-SERVICES chain EXISTS but contains ZERO KUBE-SVC rules. '
                        'This means kube-proxy is NOT syncing service rules on this node. '
                        'ALL ClusterIP/NodePort service traffic will TIME OUT because there are no '
                        'DNAT rules to translate Service IPs to pod IPs. '
                        'Root cause: kube-proxy is either not running, was recently restarted and has not '
                        'synced yet, or cannot reach the API server. '
                        'CHECK IMMEDIATELY: Is kube-proxy pod running on this node? '
                        '(kubectl get pods -n kube-system -l k8s-app=kube-proxy --field-selector spec.nodeName=<node>)'
                    )
                    issues_found.append({'section': 'iptables', 'severity': 'critical',
                                         'message': 'KUBE-SERVICES chain is EMPTY (0 KUBE-SVC rules) — kube-proxy not syncing, all ClusterIP traffic will fail'})

                # Check VPC CNI SNAT — cross-reference with CNI config for external SNAT
                has_snat = any('SNAT' in l or 'MASQUERADE' in l for l in all_lines_merged)
                has_aws_snat_chain = any('AWS-SNAT-CHAIN' in l for l in all_lines_merged)
                ipt_data['_snat_present'] = has_snat
                if not has_snat:
                    ipt_data['issues'].append('No SNAT/MASQUERADE rules found in iptables (see eksNetworkingContext for interpretation)')
                    issues_found.append({'section': 'iptables', 'severity': 'info',
                                         'message': 'No SNAT rules — may be expected if AWS_VPC_K8S_CNI_EXTERNALSNAT=true (NAT gateway handles SNAT)'})
                if has_aws_snat_chain:
                    ipt_data['vpcCniSnat'] = 'AWS-SNAT-CHAIN present (VPC CNI managing SNAT)'

                # --- Port-specific DROP/REJECT rule detection ---
                # Scan for custom rules that block critical K8s ports
                critical_ports = {'53': 'DNS', '443': 'HTTPS/API', '6443': 'kube-apiserver', '10250': 'kubelet'}
                port_block_findings = []
                for line in all_lines_merged:
                    line_upper = line.upper()
                    if 'DROP' not in line_upper and 'REJECT' not in line_upper:
                        continue
                    # Skip chain definitions (lines starting with ':')
                    if line.strip().startswith(':'):
                        continue
                    for port, port_name in critical_ports.items():
                        if f'--dport {port}' in line or f'--dport {port} ' in line or f'dpt:{port}' in line:
                            port_block_findings.append({
                                'port': port,
                                'service': port_name,
                                'rule': line.strip()[:200],
                                'action': 'DROP' if 'DROP' in line_upper else 'REJECT',
                            })
                if port_block_findings:
                    blocked_services = list(set(f"{pf['service']} (port {pf['port']})" for pf in port_block_findings))
                    ipt_data['portBlockRules'] = port_block_findings[:20]
                    ipt_data['issues'].append(
                        f"Custom DROP/REJECT rules found targeting critical K8s ports: {', '.join(blocked_services)}. "
                        "These rules may block DNS resolution (53), API server communication (443/6443), "
                        "or kubelet health checks (10250). Review and remove if unintended."
                    )
                    issues_found.append({'section': 'iptables', 'severity': 'critical',
                                         'message': f'Custom DROP/REJECT rules blocking critical ports: {", ".join(blocked_services)}'})

            results['iptables'] = ipt_data

        # =====================================================================
        # CNI CONFIG (aws-node / VPC CNI)
        # =====================================================================
        if 'cni' in sections:
            cni_data = {'config': {}, 'envVars': {}, 'issues': []}
            cni_files = find_files(['aws-node', 'cni', 'ipamd-config', '10-aws'])
            for f in cni_files[:5]:
                content = read_file_content(f)
                if content:
                    # Parse CNI config JSON
                    if f.endswith('.json') or f.endswith('.conflist'):
                        try:
                            cni_data['config'] = json.loads(content)
                        except json.JSONDecodeError:
                            cni_data['config'] = {'raw': content[:1000]}
                    # Parse env vars
                    elif 'env' in f.lower() or 'aws-node' in f.lower():
                        for line in content.split('\n'):
                            if '=' in line and not line.startswith('#'):
                                parts = line.strip().split('=', 1)
                                if len(parts) == 2:
                                    cni_data['envVars'][parts[0]] = parts[1]

            # Check for common CNI issues
            env = cni_data.get('envVars', {})
            if env.get('WARM_IP_TARGET', '') == '0' and env.get('MINIMUM_IP_TARGET', '') == '0':
                cni_data['issues'].append('Both WARM_IP_TARGET and MINIMUM_IP_TARGET are 0 — pod IP allocation may fail')
                issues_found.append({'section': 'cni', 'severity': 'critical', 'message': 'IP target settings are 0'})
            if env.get('AWS_VPC_K8S_CNI_EXTERNALSNAT', '').lower() == 'true':
                cni_data['issues'].append('External SNAT enabled — ensure NAT gateway is configured')

            # --- Prefix delegation + zero warm targets check ---
            if env.get('ENABLE_PREFIX_DELEGATION', '').lower() == 'true':
                warm_prefix = env.get('WARM_PREFIX_TARGET', '')
                warm_ip = env.get('WARM_IP_TARGET', '')
                min_ip = env.get('MINIMUM_IP_TARGET', '')
                if warm_prefix == '0' or (warm_ip == '0' and min_ip == '0'):
                    cni_data['issues'].append(
                        'ENABLE_PREFIX_DELEGATION=true but warm targets are 0 — this is NOT supported. '
                        'Pod IP assignment will be extremely slow as IPAMD maintains no prefixes in warm pool. '
                        'Set WARM_PREFIX_TARGET>=1 or WARM_IP_TARGET/MINIMUM_IP_TARGET > 0.'
                    )
                    issues_found.append({'section': 'cni', 'severity': 'critical',
                                         'message': 'Prefix delegation enabled with zero warm targets (unsupported config)'})

            # --- ENABLE_POD_ENI (trunk ENI / security groups per pod) ---
            if env.get('ENABLE_POD_ENI', '').lower() == 'true':
                sgp_mode = env.get('POD_SECURITY_GROUP_ENFORCING_MODE', 'strict')
                cni_data['securityGroupsPerPod'] = {
                    'enabled': True,
                    'enforcingMode': sgp_mode,
                    'note': (
                        'Trunk ENI enabled for security groups per pod. '
                        f'Enforcing mode: {sgp_mode}. '
                        'In "strict" mode, SGP pods bypass VPC CNI SNAT — traffic uses branch ENI directly. '
                        'In "standard" mode, SGP pods use VPC CNI SNAT like regular pods.'
                    )
                }

            # --- Network policy enforcing mode ---
            np_mode = env.get('NETWORK_POLICY_ENFORCING_MODE', '')
            if np_mode:
                cni_data['networkPolicyMode'] = np_mode
                if np_mode.lower() == 'strict':
                    cni_data['issues'].append(
                        'NETWORK_POLICY_ENFORCING_MODE=strict: New pods will have DEFAULT DENY until a '
                        'NetworkPolicy explicitly allows traffic. This can cause connectivity issues for '
                        'pods without matching NetworkPolicy rules.'
                    )
                    issues_found.append({'section': 'cni', 'severity': 'warning',
                                         'message': 'Network policy strict mode — new pods default deny'})

            # --- ENABLE_NFTABLES detection ---
            nftables_env = env.get('ENABLE_NFTABLES', '')
            if nftables_env:
                cni_data['nftablesMode'] = nftables_env
                # Note: In v1.13.1+ ENABLE_NFTABLES is deprecated (auto-detected from kubelet)

            # --- IP_COOLDOWN_PERIOD ---
            cooldown = env.get('IP_COOLDOWN_PERIOD', '')
            if cooldown:
                cni_data['ipCooldownPeriod'] = cooldown

            # --- AWS_VPC_K8S_CNI_EXCLUDE_SNAT_CIDRS ---
            exclude_snat = env.get('AWS_VPC_K8S_CNI_EXCLUDE_SNAT_CIDRS', '')
            if exclude_snat:
                cni_data['excludeSnatCidrs'] = exclude_snat

            # --- AWS_VPC_K8S_CNI_RANDOMIZESNAT ---
            randomize_snat = env.get('AWS_VPC_K8S_CNI_RANDOMIZESNAT', '')
            if randomize_snat:
                cni_data['randomizeSnat'] = randomize_snat

            # --- DISABLE_NETWORK_RESOURCE_PROVISIONING ---
            if env.get('DISABLE_NETWORK_RESOURCE_PROVISIONING', '').lower() == 'true':
                cni_data['issues'].append(
                    'DISABLE_NETWORK_RESOURCE_PROVISIONING=true: VPC CNI uses IMDS-only mode. '
                    'ENI/IP management is handled externally. This is an advanced config.'
                )

            # --- AWS_MANAGE_ENIS_NON_SCHEDULABLE ---
            if env.get('AWS_MANAGE_ENIS_NON_SCHEDULABLE', '').lower() == 'true':
                cni_data['manageEnisNonSchedulable'] = True

            # --- Missing ENABLE_IPv4/ENABLE_IPv6 (known crash bug in v1.10.x) ---
            has_enable_ipv4 = 'ENABLE_IPv4' in env or 'ENABLE_IPV4' in env
            has_enable_ipv6 = 'ENABLE_IPv6' in env or 'ENABLE_IPV6' in env
            if not has_enable_ipv4 and not has_enable_ipv6 and env:
                cni_data['issues'].append(
                    'Neither ENABLE_IPv4 nor ENABLE_IPv6 env vars found. '
                    'VPC CNI v1.10.x+ requires these — missing them can cause aws-node crash (SIGSEGV). '
                    'Ensure the full CNI manifest is applied, not just the image tag update.'
                )

            # Capture key env vars for guardrails cross-reference
            cni_data['_parsedFlags'] = {
                'externalSnat': env.get('AWS_VPC_K8S_CNI_EXTERNALSNAT', '').lower() == 'true',
                'customNetworking': env.get('AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG', '').lower() == 'true',
                'prefixDelegation': env.get('ENABLE_PREFIX_DELEGATION', '').lower() == 'true',
                'podEni': env.get('ENABLE_POD_ENI', '').lower() == 'true',
                'sgpMode': env.get('POD_SECURITY_GROUP_ENFORCING_MODE', 'strict'),
                'networkPolicyMode': env.get('NETWORK_POLICY_ENFORCING_MODE', ''),
                'nftables': env.get('ENABLE_NFTABLES', ''),
                'ipCooldown': env.get('IP_COOLDOWN_PERIOD', '30'),
                'excludeSnatCidrs': env.get('AWS_VPC_K8S_CNI_EXCLUDE_SNAT_CIDRS', ''),
                'warmEniTarget': env.get('WARM_ENI_TARGET', '1'),
                'warmIpTarget': env.get('WARM_IP_TARGET', ''),
                'minimumIpTarget': env.get('MINIMUM_IP_TARGET', ''),
            }

            cni_data['sourceFiles'] = cni_files[:5]
            results['cni'] = cni_data

        # =====================================================================
        # ROUTE TABLES
        # =====================================================================
        if 'routes' in sections:
            route_data = {'routes': [], 'defaultGateway': None, 'interfaces': [], 'issues': []}
            route_files = find_files(['iproute', 'ip-route', 'ip_route', 'route-table', 'routes'])
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
            iface_files = find_files(['ifconfig', 'ip-addr', 'ip_addr', 'ipaddr', 'interfaces'])
            for f in iface_files[:2]:
                content = read_file_content(f)
                if content:
                    # Extract interface names and IPs
                    current_iface = None
                    for line in content.split('\n'):
                        if re.match(r'^\d+:\s+\S+', line) or re.match(r'^\S+:', line):
                            iface_match = re.search(r'(\S+?)[@:]', line)
                            if iface_match:
                                current_iface = iface_match.group(1)
                        if 'inet ' in line and current_iface:
                            ip_match = re.search(r'inet\s+(\S+)', line)
                            if ip_match:
                                route_data['interfaces'].append({
                                    'name': current_iface,
                                    'ip': ip_match.group(1),
                                })

            # On EKS nodes with VPC CNI, pod traffic uses secondary ENIs with SNAT.
            # A missing default gateway on the host route table is NOT necessarily
            # a critical issue — downgrade to info if multiple ENIs are present.
            has_multiple_enis = len(route_data['interfaces']) >= 2
            if not route_data['defaultGateway']:
                if has_multiple_enis:
                    route_data['issues'].append('No default gateway on host route table (expected on EKS nodes with VPC CNI — secondary ENIs handle pod traffic via SNAT)')
                    issues_found.append({'section': 'routes', 'severity': 'info', 'message': 'No host default gateway (VPC CNI uses secondary ENI SNAT)'})
                else:
                    route_data['issues'].append('No default gateway found')
                    issues_found.append({'section': 'routes', 'severity': 'critical', 'message': 'No default gateway'})

            # --- nm-cloud-setup detection (breaks VPC CNI ip rules) ---
            # NetworkManager-cloud-setup overwrites ip rules installed for pods.
            # Symptom: routing table 30200 or 30400 present.
            has_nm_cloud_setup = any('30200' in r or '30400' in r for r in route_data['routes'])
            if has_nm_cloud_setup:
                route_data['nmCloudSetup'] = True
                route_data['issues'].append(
                    'Routing table 30200 or 30400 detected — nm-cloud-setup (NetworkManager-cloud-setup) '
                    'is likely active. This service is INCOMPATIBLE with VPC CNI and overwrites pod ip rules, '
                    'breaking pod networking. Fix: disable/remove nm-cloud-setup service. '
                    'See: https://github.com/aws/amazon-vpc-cni-k8s/blob/master/docs/troubleshooting.md'
                )
                issues_found.append({'section': 'routes', 'severity': 'critical',
                                     'message': 'nm-cloud-setup detected (routing table 30200/30400) — breaks VPC CNI pod networking'})

            # --- VPC CNI policy routing detection ---
            # VPC CNI creates per-ENI route tables and ip rules like:
            #   "from <pod-ip> lookup eni-X" and "to <pod-ip> lookup main"
            # These are NORMAL and expected.
            policy_routes = [r for r in route_data['routes'] if 'lookup' in r and ('eni-' in r or 'from' in r)]
            if policy_routes:
                route_data['vpcCniPolicyRoutes'] = len(policy_routes)
                route_data['_note'] = (
                    'VPC CNI uses policy routing: each secondary ENI has its own route table. '
                    'ip rules like "from <pod-ip> lookup eni-X" are NORMAL — they route pod egress '
                    'traffic through the correct ENI. Do NOT flag these as suspicious.'
                )

            route_data['routeCount'] = len(route_data['routes'])
            route_data['routes'] = route_data['routes'][:50]  # Cap output
            results['routes'] = route_data

        # =====================================================================
        # DNS
        # =====================================================================
        if 'dns' in sections:
            dns_data = {'resolv_conf': {}, 'nameservers': [], 'searchDomains': [], 'corednsStatus': None, 'issues': []}
            dns_files = find_files(['resolv', 'dns', 'coredns'])
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
                    elif 'coredns' in f.lower():
                        # Check for coredns errors
                        error_lines = [l for l in content.split('\n') if 'error' in l.lower() or 'SERVFAIL' in l]
                        if error_lines:
                            dns_data['corednsStatus'] = 'errors_found'
                            dns_data['corednsErrors'] = error_lines[:10]
                            issues_found.append({'section': 'dns', 'severity': 'warning', 'message': f'{len(error_lines)} CoreDNS errors found'})
                        else:
                            dns_data['corednsStatus'] = 'ok'

            # Validate DNS config
            if not dns_data['nameservers']:
                dns_data['issues'].append('No nameservers in resolv.conf')
                issues_found.append({'section': 'dns', 'severity': 'critical', 'message': 'No nameservers configured'})

            # Add interpretation note for node-level resolv.conf
            dns_data['_note'] = (
                "IMPORTANT CONTEXT: This is the NODE-LEVEL /etc/resolv.conf. "
                "It is EXPECTED to show VPC DNS (e.g., 172.31.0.2 which is VPC CIDR+2). "
                "This is NOT a misconfiguration. Pod DNS is configured SEPARATELY by kubelet "
                "via the --cluster-dns flag (typically 10.100.0.10 or 172.20.0.10) and injected "
                "into each pod's /etc/resolv.conf at runtime. Do NOT diagnose node resolv.conf "
                "pointing to VPC DNS as a pod DNS misconfiguration. To check pod DNS, use: "
                "kubectl exec <pod> -- cat /etc/resolv.conf"
            )
            results['dns'] = dns_data

        # =====================================================================
        # ENI (Elastic Network Interfaces)
        # =====================================================================
        if 'eni' in sections:
            eni_data = {'attachedENIs': [], 'eniCount': 0, 'issues': []}
            # Try to get ENI info from EC2 API
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

                # Check for IP exhaustion signals
                total_secondary_ips = sum(len(e['secondaryIps']) for e in eni_data['attachedENIs'])
                eni_data['totalSecondaryIPs'] = total_secondary_ips
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

        # =====================================================================
        # IPAMD (IP Address Management Daemon / aws-node)
        # =====================================================================
        if 'ipamd' in sections:
            ipamd_data = {'logSummary': {}, 'errors': [], 'ipAllocationIssues': [], 'issues': []}
            ipamd_files = find_files(['ipamd', 'aws_node', 'ip-address-management'])
            if not ipamd_files:
                ipamd_files = find_files(['aws-node'])
            for f in ipamd_files[:5]:
                content = read_file_content(f, max_size=524288)
                if content:
                    lines = content.split('\n')
                    total_lines = len(lines)
                    error_lines = []
                    ip_issues = []
                    for line in lines:
                        ll = line.lower()
                        stripped = line.strip()
                        # Skip Prometheus metric lines (contain {labels} with error="false" etc.)
                        if '{' in stripped and '}' in stripped and ('error="' in ll or 'status="' in ll):
                            continue
                        # Skip Prometheus HELP/TYPE comment lines
                        if stripped.startswith('# HELP') or stripped.startswith('# TYPE'):
                            continue
                        if 'error' in ll or 'failed' in ll:
                            error_lines.append(stripped[:200])
                        if 'ip address' in ll and ('exhaust' in ll or 'insufficient' in ll or 'no available' in ll):
                            ip_issues.append(stripped[:200])
                        if 'failed to allocate' in ll or 'no ips available' in ll:
                            ip_issues.append(stripped[:200])

                    ipamd_data['logSummary'][f] = {
                        'totalLines': total_lines,
                        'errorCount': len(error_lines),
                        'ipIssueCount': len(ip_issues),
                    }
                    ipamd_data['errors'].extend(error_lines[:20])
                    ipamd_data['ipAllocationIssues'].extend(ip_issues[:20])

            if ipamd_data['ipAllocationIssues']:
                # EKS guardrail: After pod deletion, VPC CNI has a configurable IP cooldown cache.
                # Transient "no available IP" messages during this window are NORMAL.
                cooldown_period = results.get('cni', {}).get('_parsedFlags', {}).get('ipCooldown', '30')
                ipamd_data['issues'].append(f"{len(ipamd_data['ipAllocationIssues'])} IP allocation issues found — possible subnet IP exhaustion")
                ipamd_data['issues'].append(
                    f'NOTE: Transient "no available IP" after pod deletion is normal — VPC CNI has a '
                    f'{cooldown_period}-second IP cooldown cache (IP_COOLDOWN_PERIOD={cooldown_period}) '
                    f'to let kube-proxy finish updating iptables rules before recycling the IP.'
                )
                issues_found.append({'section': 'ipamd', 'severity': 'critical', 'message': 'IP allocation failures detected'})
            if ipamd_data['errors']:
                ipamd_data['issues'].append(f"{len(ipamd_data['errors'])} errors in IPAMD logs")
                issues_found.append({'section': 'ipamd', 'severity': 'warning', 'message': f"{len(ipamd_data['errors'])} IPAMD errors"})
            results['ipamd'] = ipamd_data

        # =====================================================================
        # KUBE-PROXY (proxy mode, conntrack, version, IPVS, errors)
        # =====================================================================
        if 'kube_proxy' in sections:
            kp_data = {'proxyMode': 'unknown', 'config': {}, 'conntrack': {}, 'ipvs': {},
                       'errors': [], 'syncErrors': [], 'versionInfo': None, 'issues': []}

            kp_files = find_files(['kube-proxy', 'kube_proxy', 'kubeproxy'])
            for f in kp_files[:5]:
                content = read_file_content(f, max_size=524288)
                if content:
                    lines = content.split('\n')

                    # --- Detect proxy mode ---
                    for line in lines:
                        ll = line.lower()
                        if 'using ipvs proxier' in ll or 'ipvs proxier' in ll:
                            kp_data['proxyMode'] = 'ipvs'
                            break
                        elif 'using nftables proxier' in ll or 'nftables proxier' in ll:
                            kp_data['proxyMode'] = 'nftables'
                            break
                        elif 'using iptables proxier' in ll or 'iptables proxier' in ll:
                            kp_data['proxyMode'] = 'iptables'
                            break

                    # --- Parse kube-proxy config (from ConfigMap dump or args) ---
                    for line in lines:
                        stripped = line.strip()
                        # ConfigMap-style key: value
                        if 'mode:' in stripped and ('iptables' in stripped or 'ipvs' in stripped or 'nftables' in stripped):
                            mode_match = re.search(r'mode:\s*["\']?(\w+)', stripped)
                            if mode_match:
                                kp_data['config']['mode'] = mode_match.group(1)
                                if kp_data['proxyMode'] == 'unknown':
                                    kp_data['proxyMode'] = mode_match.group(1)
                        if 'scheduler:' in stripped:
                            sched_match = re.search(r'scheduler:\s*["\']?(\w+)', stripped)
                            if sched_match:
                                kp_data['config']['ipvsScheduler'] = sched_match.group(1)
                        if 'syncPeriod:' in stripped:
                            sync_match = re.search(r'syncPeriod:\s*["\']?(\S+)', stripped)
                            if sync_match:
                                kp_data['config']['syncPeriod'] = sync_match.group(1)
                        if 'minSyncPeriod:' in stripped:
                            msync_match = re.search(r'minSyncPeriod:\s*["\']?(\S+)', stripped)
                            if msync_match:
                                kp_data['config']['minSyncPeriod'] = msync_match.group(1)
                        if 'maxPerCore:' in stripped:
                            mpc_match = re.search(r'maxPerCore:\s*(\d+)', stripped)
                            if mpc_match:
                                kp_data['conntrack']['maxPerCore'] = int(mpc_match.group(1))
                        if 'conntrack' in stripped and 'min:' in stripped:
                            cmin_match = re.search(r'min:\s*(\d+)', stripped)
                            if cmin_match:
                                kp_data['conntrack']['min'] = int(cmin_match.group(1))

                    # --- Detect kube-proxy version ---
                    for line in lines:
                        ver_match = re.search(r'kube-proxy\s+v?(\d+\.\d+\.\d+)', line)
                        if ver_match:
                            kp_data['versionInfo'] = ver_match.group(1)
                            break
                    if not kp_data['versionInfo']:
                        for line in lines:
                            ver_match = re.search(r'v(\d+\.\d+\.\d+)-eksbuild', line)
                            if ver_match:
                                kp_data['versionInfo'] = ver_match.group(1)
                                break

                    # --- Parse errors and sync failures ---
                    error_lines = []
                    sync_errors = []
                    conntrack_errors = []
                    for line in lines:
                        ll = line.lower()
                        stripped = line.strip()
                        if not stripped:
                            continue
                        # Conntrack table full
                        if 'nf_conntrack' in ll and ('table full' in ll or 'dropping packet' in ll):
                            conntrack_errors.append(stripped[:200])
                        # Sync errors
                        if 'failed to sync' in ll or 'sync rules failed' in ll or 'syncrules' in ll.replace(' ', ''):
                            sync_errors.append(stripped[:200])
                        # General errors (skip Prometheus metric lines)
                        if ('error' in ll or 'failed' in ll) and '{' not in stripped:
                            error_lines.append(stripped[:200])
                        # Conntrack cleanup failures
                        if 'conntrack' in ll and ('failed' in ll or 'error' in ll):
                            conntrack_errors.append(stripped[:200])
                        # Watch/list failures (API server connectivity)
                        if 'failed to list' in ll or 'failed to watch' in ll:
                            sync_errors.append(stripped[:200])

                    kp_data['errors'] = error_lines[:30]
                    kp_data['syncErrors'] = sync_errors[:20]
                    if conntrack_errors:
                        kp_data['conntrack']['errors'] = conntrack_errors[:20]
                    kp_data['errorCount'] = len(error_lines)
                    kp_data['syncErrorCount'] = len(sync_errors)
                    kp_data['sourceFile'] = f
                    break  # Use first file with content

            # --- Parse conntrack/sysctl files for nf_conntrack_max ---
            ct_files = find_files(['conntrack', 'nf_conntrack', 'sysctl'])
            for f in ct_files[:3]:
                content = read_file_content(f, max_size=65536)
                if content:
                    # Look for nf_conntrack_max value
                    for line in content.split('\n'):
                        if 'nf_conntrack_max' in line:
                            val_match = re.search(r'(\d+)', line)
                            if val_match:
                                kp_data['conntrack']['nfConntrackMax'] = int(val_match.group(1))
                        if 'nf_conntrack_count' in line:
                            val_match = re.search(r'(\d+)', line)
                            if val_match:
                                kp_data['conntrack']['nfConntrackCount'] = int(val_match.group(1))

            # --- Parse modules files for IPVS kernel module detection ---
            mod_files = find_files(['modinfo', 'modules', 'lsmod'])
            for f in mod_files[:3]:
                content = read_file_content(f, max_size=65536)
                if content:
                    ipvs_modules = ['ip_vs', 'ip_vs_rr', 'ip_vs_wrr', 'ip_vs_sh', 'ip_vs_lc',
                                    'ip_vs_wlc', 'ip_vs_lblc', 'ip_vs_sed', 'ip_vs_nq', 'nf_conntrack']
                    loaded = []
                    missing = []
                    for mod in ipvs_modules:
                        if mod in content:
                            loaded.append(mod)
                        else:
                            missing.append(mod)
                    if loaded:
                        kp_data['ipvs']['loadedModules'] = loaded
                    if missing:
                        kp_data['ipvs']['missingModules'] = missing

            # --- Issue detection ---
            # Conntrack table full
            ct_max = kp_data['conntrack'].get('nfConntrackMax', 0)
            ct_count = kp_data['conntrack'].get('nfConntrackCount', 0)
            ct_errors = kp_data['conntrack'].get('errors', [])
            if ct_errors:
                kp_data['issues'].append(
                    f'{len(ct_errors)} conntrack errors found (table full / dropping packets). '
                    f'Current nf_conntrack_max={ct_max}. Each entry uses ~300 bytes of memory. '
                    'Fix: increase conntrack.min in kube-proxy-config ConfigMap, then restart kube-proxy DaemonSet.'
                )
                issues_found.append({'section': 'kube_proxy', 'severity': 'critical',
                                     'message': f'Conntrack table full errors ({len(ct_errors)} occurrences)'})
            elif ct_max > 0 and ct_count > 0 and ct_count > ct_max * 0.8:
                kp_data['issues'].append(
                    f'Conntrack table is {int(ct_count/ct_max*100)}% full ({ct_count}/{ct_max}). '
                    'Risk of packet drops. Consider increasing conntrack.min in kube-proxy-config.'
                )
                issues_found.append({'section': 'kube_proxy', 'severity': 'warning',
                                     'message': f'Conntrack table {int(ct_count/ct_max*100)}% full'})

            # IPVS mode without required kernel modules
            if kp_data['proxyMode'] == 'ipvs':
                missing_mods = kp_data.get('ipvs', {}).get('missingModules', [])
                critical_mods = [m for m in missing_mods if m in ('ip_vs', 'ip_vs_rr', 'nf_conntrack')]
                if critical_mods:
                    kp_data['issues'].append(
                        f'kube-proxy is in IPVS mode but critical kernel modules are missing: {", ".join(critical_mods)}. '
                        'IPVS will not function correctly. Fix: modprobe the missing modules and add them to /etc/modules-load.d/ipvs.conf.'
                    )
                    issues_found.append({'section': 'kube_proxy', 'severity': 'critical',
                                         'message': f'IPVS mode missing kernel modules: {", ".join(critical_mods)}'})

            # Sync errors (API server connectivity)
            if kp_data['syncErrorCount'] > 10:
                kp_data['issues'].append(
                    f'{kp_data["syncErrorCount"]} sync/watch errors in kube-proxy logs. '
                    'kube-proxy cannot sync iptables/ipvs rules from API server. '
                    'Check: API server connectivity, node network, kube-proxy service account permissions.'
                )
                issues_found.append({'section': 'kube_proxy', 'severity': 'warning',
                                     'message': f'{kp_data["syncErrorCount"]} kube-proxy sync errors'})

            # No kube-proxy logs found at all
            if not kp_files:
                kp_data['issues'].append(
                    'No kube-proxy log files found in the bundle. kube-proxy may not be running, '
                    'or logs are not captured by the EKS log collector. Check: kubectl get ds kube-proxy -n kube-system.'
                )
                issues_found.append({'section': 'kube_proxy', 'severity': 'warning',
                                     'message': 'No kube-proxy logs found in bundle'})

            # Cross-reference: iptables mode on nftables-based OS (RHEL 8.6+)
            nftables_env = results.get('cni', {}).get('_parsedFlags', {}).get('nftables', '')
            if kp_data['proxyMode'] == 'iptables' and nftables_env.lower() == 'true':
                kp_data['issues'].append(
                    'kube-proxy is in iptables mode but the host OS uses nftables (ENABLE_NFTABLES=true). '
                    'iptables rules may not be visible to nftables and vice versa. '
                    'Consider switching kube-proxy to IPVS mode for RHEL 8.6+ / nftables-based systems.'
                )
                issues_found.append({'section': 'kube_proxy', 'severity': 'warning',
                                     'message': 'kube-proxy iptables mode on nftables-based OS'})

            # Store internal flags for cross-reference
            kp_data['_parsedFlags'] = {
                'proxyMode': kp_data['proxyMode'],
                'hasConntrackErrors': len(ct_errors) > 0,
                'conntrackMax': ct_max,
                'conntrackCount': ct_count,
                'ipvsMode': kp_data['proxyMode'] == 'ipvs',
            }

            results['kube_proxy'] = kp_data

        # =====================================================================
        # OVERALL SUMMARY
        # =====================================================================

        # --- EKS Networking Guardrails: Cross-reference sections to prevent false positives ---
        eks_context = {
            '_purpose': 'EKS-specific networking context to prevent misinterpretation of findings. '
                        'EKS worker nodes use VPC CNI which fundamentally changes how networking works '
                        'compared to traditional Linux hosts. DO NOT diagnose EKS nodes like bare-metal servers.',
            'guardrails': [],
            'logPaths': {
                'ipamd': '/var/log/aws-routed-eni/ipamd.log',
                'cniPlugin': '/var/log/aws-routed-eni/plugin.log',
                'ipamState': '/var/run/aws-node/ipam.json',
                'ipamdDebugEndpoint': 'curl http://localhost:61679/v1/enis',
                'ipamdPodsEndpoint': 'curl http://localhost:61679/v1/pods',
                'ipamdMetrics': 'curl http://localhost:61678/metrics',
            }
        }

        # Cross-reference: external SNAT + missing iptables SNAT rules
        cni_flags = results.get('cni', {}).get('_parsedFlags', {})
        cni_env = results.get('cni', {}).get('envVars', {})
        external_snat = cni_flags.get('externalSnat', False)
        ipt_snat_present = results.get('iptables', {}).get('_snat_present', True)

        if external_snat:
            eks_context['externalSnat'] = True
            exclude_cidrs = cni_flags.get('excludeSnatCidrs', '')
            snat_note = (
                'AWS_VPC_K8S_CNI_EXTERNALSNAT=true: VPC CNI does NOT add SNAT/MASQUERADE rules to iptables. '
                'The off-VPC IP rule is also NOT applied. '
                'Pod egress traffic is NATed by the VPC NAT Gateway instead. Missing SNAT rules in iptables is EXPECTED.'
            )
            if exclude_cidrs:
                snat_note += f' Additionally, SNAT is excluded for CIDRs: {exclude_cidrs}.'
            eks_context['guardrails'].append(snat_note)
            # Downgrade any SNAT-related iptables issues to info
            for issue in issues_found:
                if issue.get('section') == 'iptables' and 'SNAT' in issue.get('message', ''):
                    issue['severity'] = 'info'
                    issue['message'] += ' [EXPECTED: external SNAT enabled, NAT gateway handles egress]'
        elif not ipt_snat_present and not external_snat and 'iptables' in results:
            eks_context['guardrails'].append(
                'No SNAT rules found and AWS_VPC_K8S_CNI_EXTERNALSNAT is not true. '
                'Pod egress traffic may fail. Check if NAT gateway is configured or if VPC CNI SNAT is expected.'
            )

        # Cross-reference: custom networking
        custom_networking = cni_flags.get('customNetworking', False)
        if custom_networking:
            eks_context['customNetworking'] = True
            eks_context['guardrails'].append(
                'AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG=true: Custom networking is enabled. '
                'Pods do NOT get IPs from the primary ENI subnet. They use ENIConfig CRDs to specify '
                'which subnet/security group to use. Empty primary ENI secondary IPs is EXPECTED.'
            )

        # Cross-reference: prefix delegation
        enable_prefix = cni_flags.get('prefixDelegation', False)
        if enable_prefix:
            eks_context['prefixDelegation'] = True
            eks_context['guardrails'].append(
                'ENABLE_PREFIX_DELEGATION=true: VPC CNI assigns /28 IPv4 prefixes (16 IPs each) instead of individual IPs. '
                'ENI slot usage looks different — each slot holds 16 IPs. Fewer ENIs with more IPs per ENI is normal. '
                'WARM_PREFIX_TARGET controls pre-allocation. Setting WARM_PREFIX_TARGET=0 or both '
                'WARM_IP_TARGET=0 and MINIMUM_IP_TARGET=0 is NOT supported with prefix delegation.'
            )

        # Cross-reference: security groups per pod (trunk ENI)
        pod_eni = cni_flags.get('podEni', False)
        if pod_eni:
            sgp_mode = cni_flags.get('sgpMode', 'strict')
            eks_context['securityGroupsPerPod'] = True
            eks_context['guardrails'].append(
                f'ENABLE_POD_ENI=true: Trunk ENI enabled for security groups per pod. '
                f'Enforcing mode: {sgp_mode}. '
                f'In "strict" mode, SGP pods bypass VPC CNI SNAT — traffic uses branch ENI directly. '
                f'In "standard" mode, SGP pods use VPC CNI SNAT like regular pods. '
                f'Trunk ENI changes ENI attachment behavior — do NOT flag extra ENIs as anomalous.'
            )

        # ANTI-HALLUCINATION GUARDRAIL: podSGEnforcingMode does NOT block unannotated pods
        eks_context['guardrails'].append(
            'CRITICAL ANTI-HALLUCINATION: POD_SECURITY_GROUP_ENFORCING_MODE (podSGEnforcingMode) '
            'does NOT create a default-deny for unannotated pods. It ONLY affects pods that have '
            'the vpc.amazonaws.com/pod-eni annotation (security groups per pod). '
            '"strict" mode means SGP-annotated pods use their branch ENI for ALL traffic instead of '
            'falling back to the primary ENI. Pods WITHOUT SGP annotations are COMPLETELY UNAFFECTED — '
            'they use the primary ENI and normal VPC routing as usual. '
            'Do NOT blame podSGEnforcingMode for connectivity failures on pods without SGP annotations. '
            'This is a COMMON MISDIAGNOSIS.'
        )

        # Cross-reference: network policy strict mode
        np_mode = cni_flags.get('networkPolicyMode', '')
        if np_mode.lower() == 'strict':
            eks_context['networkPolicyStrict'] = True
            eks_context['guardrails'].append(
                'NETWORK_POLICY_ENFORCING_MODE=strict: New pods have DEFAULT DENY until a NetworkPolicy '
                'explicitly allows traffic. If pods cannot communicate, check for missing NetworkPolicy rules '
                'before investigating CNI/routing issues. Network policies are NOT supported on GPU instances, '
                'Fargate, Windows nodes, or pods with hostNetwork=true.'
            )

        # Cross-reference: nm-cloud-setup
        if results.get('routes', {}).get('nmCloudSetup'):
            eks_context['guardrails'].append(
                'nm-cloud-setup (NetworkManager-cloud-setup) DETECTED via routing table 30200/30400. '
                'This service is INCOMPATIBLE with VPC CNI — it overwrites ip rules installed for pods, '
                'breaking pod networking. This is a KNOWN ISSUE on RHEL8 AMIs. '
                'Fix: disable nm-cloud-setup.service and nm-cloud-setup.timer.'
            )

        # Cross-reference: nftables vs iptables-legacy
        nftables_env = cni_flags.get('nftables', '')
        if nftables_env:
            eks_context['guardrails'].append(
                f'ENABLE_NFTABLES={nftables_env}: VPC CNI iptables mode is explicitly set. '
                'VPC CNI uses iptables-legacy by default. If the host OS uses nftables (RHEL 8.x+, Ubuntu 21.x+) '
                'but VPC CNI uses iptables-legacy, rules may not be visible to each other. '
                'In v1.13.1+, ENABLE_NFTABLES is deprecated — iptables mode is auto-detected from kubelet.'
            )

        # Route table context (already handled in routes section, reinforce here)
        has_multiple_enis = len(results.get('routes', {}).get('interfaces', [])) >= 2
        if has_multiple_enis:
            eks_context['guardrails'].append(
                'Multiple ENIs detected: On EKS nodes with VPC CNI, the host route table may appear empty or '
                'have no default gateway. This is NORMAL — secondary ENIs handle pod traffic via SNAT. '
                'VPC CNI creates per-ENI route tables with policy routing rules (ip rule from <pod-ip> lookup eni-X). '
                'Do NOT flag missing default gateway as a critical issue on multi-ENI EKS nodes.'
            )

        # VPC CNI pod networking architecture context
        eks_context['guardrails'].append(
            'VPC CNI pod networking architecture: Each pod gets a secondary IP from an ENI. '
            'Inside the pod, default gateway is 169.254.1.1 (link-local) with a static ARP entry pointing to host veth. '
            'On the host, per-pod /32 routes point to veth interfaces. Each ENI has its own route table. '
            'Policy routing rules (ip rule) direct pod traffic to the correct ENI route table. '
            'This is fundamentally different from traditional Linux routing.'
        )

        # hostNetwork context
        eks_context['guardrails'].append(
            'Pods with hostNetwork=true use the node primary IP directly (no SNAT/DNAT translation). '
            'kube-proxy, aws-node (VPC CNI), and CoreDNS typically run with hostNetwork=true. '
            'Their traffic will NOT appear in VPC CNI SNAT chains.'
        )

        # IP cooldown context (use configured value)
        cooldown_val = cni_flags.get('ipCooldown', '30')
        eks_context['guardrails'].append(
            f'After pod deletion, VPC CNI holds the IP in a {cooldown_val}-second cooldown cache '
            f'(IP_COOLDOWN_PERIOD={cooldown_val}) before returning it to the warm pool. '
            'This allows kube-proxy to finish updating iptables rules. Transient "no available IP" or "IP not in datastore" '
            'messages during this window are NORMAL and self-resolving.'
        )

        # WARM_ENI_TARGET / WARM_IP_TARGET context
        warm_eni = cni_flags.get('warmEniTarget', '1')
        warm_ip = cni_flags.get('warmIpTarget', '')
        min_ip = cni_flags.get('minimumIpTarget', '')
        if warm_ip or min_ip:
            eks_context['guardrails'].append(
                f'WARM_IP_TARGET={warm_ip}, MINIMUM_IP_TARGET={min_ip}: Fine-grained IP warm pool control. '
                'Use only for small clusters or low pod churn. High values increase EC2 API calls which can get '
                'throttled, preventing new ENIs/IPs from being attached to ANY instance in the cluster. '
                'Default WARM_ENI_TARGET=1 is recommended for most clusters.'
            )

        # iptables FORWARD policy context
        eks_context['guardrails'].append(
            'iptables FORWARD policy must be ACCEPT for pod networking to work. Custom AMIs often set it to DROP. '
            'Fix: add "ExecStartPre=/sbin/iptables -P FORWARD ACCEPT" to kubelet.service. '
            'The EKS-optimized AMI sets this correctly by default.'
        )

        # systemd-udev MACAddressPolicy context
        eks_context['guardrails'].append(
            'systemd-udev: Linux distributions with systemd-udev may set MACAddressPolicy=persistent '
            'in /usr/lib/systemd/network/99-default.link. This can change the MAC address of host veth interfaces '
            'after they are moved to the host namespace, breaking the static ARP entry in pods. '
            'Known to affect Ubuntu 22.04+. Fix: set MACAddressPolicy=none.'
        )

        # --- kube-proxy guardrails ---
        kp_flags = results.get('kube_proxy', {}).get('_parsedFlags', {})
        kp_mode = kp_flags.get('proxyMode', 'unknown')

        # Proxy mode context
        if kp_mode == 'ipvs':
            eks_context['kubeProxyMode'] = 'ipvs'
            eks_context['guardrails'].append(
                'kube-proxy is in IPVS mode: Uses hash tables instead of linear iptables rules for service routing. '
                'Recommended for clusters with 1000+ services. Requires ip_vs, ip_vs_rr, ip_vs_wrr, ip_vs_sh, '
                'nf_conntrack kernel modules. Validate with "sudo ipvsadm -L". In IPVS mode, KUBE-SVC iptables '
                'chains will NOT exist — this is EXPECTED, not a sign of broken kube-proxy.'
            )
        elif kp_mode == 'nftables':
            eks_context['kubeProxyMode'] = 'nftables'
            eks_context['guardrails'].append(
                'kube-proxy is in nftables mode (alpha/beta): Uses nftables instead of iptables for service routing. '
                'Rules are NOT visible via iptables-save — use "nft list ruleset" instead. '
                'Missing KUBE-SERVICES chains in iptables output is EXPECTED in nftables mode.'
            )
        elif kp_mode == 'iptables':
            eks_context['kubeProxyMode'] = 'iptables'
            eks_context['guardrails'].append(
                'kube-proxy is in iptables mode (default): Creates KUBE-SERVICES, KUBE-SVC-*, KUBE-SEP-* chains. '
                'For clusters with 1000+ services, iptables mode causes latency due to sequential rule processing. '
                'Consider IPVS mode for large clusters. On RHEL 8.6+ / nftables-based OS, iptables mode may not '
                'work correctly — switch to IPVS mode.'
            )

        # Conntrack context
        ct_max = kp_flags.get('conntrackMax', 0)
        if ct_max > 0:
            eks_context['guardrails'].append(
                f'Conntrack table: nf_conntrack_max={ct_max}. Formula: max(conntrack.min, conntrack.maxPerCore * num_cores). '
                'Each entry uses ~300 bytes of memory. "nf_conntrack: table full, dropping packet" in dmesg means '
                'the table is exhausted — increase conntrack.min in kube-proxy-config ConfigMap and restart kube-proxy. '
                'High-traffic nodes (load balancers, ingress controllers) are most susceptible.'
            )

        # kube-proxy version skew context
        kp_version = results.get('kube_proxy', {}).get('versionInfo')
        if kp_version:
            eks_context['guardrails'].append(
                f'kube-proxy version: {kp_version}. kube-proxy must be within 1 minor version of the cluster '
                'control plane version. Version skew beyond this can cause service routing failures. '
                'After cluster upgrade, update kube-proxy add-on to match.'
            )

        # kube-proxy static stability context
        eks_context['guardrails'].append(
            'kube-proxy static stability: During API server disconnections, existing kube-proxy rules '
            'continue to function. In-cluster service routing remains available. kube-proxy pods continue '
            'running. New services/endpoints will NOT be reflected until API server connectivity is restored.'
        )

        # ANTI-HALLUCINATION: NETWORK_POLICY_ENFORCING_MODE vs POD_SECURITY_GROUP_ENFORCING_MODE
        eks_context['guardrails'].append(
            'CRITICAL ANTI-HALLUCINATION: NETWORK_POLICY_ENFORCING_MODE and POD_SECURITY_GROUP_ENFORCING_MODE '
            'are COMPLETELY DIFFERENT settings. NETWORK_POLICY_ENFORCING_MODE controls Kubernetes NetworkPolicy '
            'enforcement via aws-network-policy-agent (eBPF). "strict" = default-deny until NetworkPolicy allows. '
            'POD_SECURITY_GROUP_ENFORCING_MODE controls veth naming for SGP-annotated pods ONLY. '
            'They share the word "enforcing" but have ZERO overlap in code paths. '
            'Do NOT confuse them. Do NOT claim one affects the other.'
        )

        # ANTI-HALLUCINATION: DISABLE_NETWORK_RESOURCE_PROVISIONING
        disable_nrp = cni_env.get('DISABLE_NETWORK_RESOURCE_PROVISIONING', 'false')
        if disable_nrp.lower() == 'true':
            eks_context['guardrails'].append(
                'DISABLE_NETWORK_RESOURCE_PROVISIONING=true: This ONLY disables ENI provisioning during pod init. '
                'It does NOT disable networking. Used when an external controller manages ENIs. '
                'Existing pod networking is UNAFFECTED. The name is misleading — do NOT claim it breaks networking.'
            )

        # ANTI-HALLUCINATION: WARM_IP_TARGET is NOT max pods
        eks_context['guardrails'].append(
            'ANTI-HALLUCINATION: WARM_IP_TARGET controls pre-allocation of FREE IPs in the warm pool. '
            'It does NOT limit max pods. Max pods is kubelet --max-pods (set by EKS bootstrap based on '
            'instance ENI/IP limits). Do NOT claim WARM_IP_TARGET caps the number of running pods.'
        )

        # ANTI-HALLUCINATION: AWS_VPC_ENI_MTU vs POD_MTU
        eks_context['guardrails'].append(
            'ANTI-HALLUCINATION: AWS_VPC_ENI_MTU (default 9001) sets host ENI MTU (eth0, eth1). '
            'POD_MTU (default 0 = use ENI MTU) sets pod veth MTU. They are different settings. '
            'Do NOT claim AWS_VPC_ENI_MTU directly controls pod MTU — POD_MTU does that.'
        )

        # ANTI-HALLUCINATION: Empty main route table on multi-ENI nodes
        eks_context['guardrails'].append(
            'ANTI-HALLUCINATION: On multi-ENI EKS nodes, an empty or minimal main route table is NORMAL. '
            'VPC CNI creates per-ENI route tables (table 2, 3, 4...) with policy routing rules. '
            'Check "ip rule list" and "ip route show table 2" before flagging routing as broken.'
        )

        # ANTI-HALLUCINATION: Conntrack exhaustion is kernel-level, not CNI/kube-proxy
        eks_context['guardrails'].append(
            'ANTI-HALLUCINATION: "nf_conntrack: table full" is a kernel resource exhaustion issue. '
            'Do NOT blame VPC CNI or kube-proxy. Fix: increase nf_conntrack_max via kube-proxy '
            'conntrack.min/conntrack.maxPerCore settings in kube-proxy-config ConfigMap.'
        )

        # ANTI-HALLUCINATION: ENABLE_IMDS_ONLY_MODE does NOT disable networking (G27)
        imds_only = cni_env.get('ENABLE_IMDS_ONLY_MODE', 'false')
        if imds_only.lower() == 'true':
            eks_context['guardrails'].append(
                'ENABLE_IMDS_ONLY_MODE=true: This ONLY skips EC2 API calls (DescribeNetworkInterfaces) '
                'and uses IMDS metadata for ENI discovery. It implicitly disables ENI provisioning and '
                'leaked ENI cleanup. Existing pod networking is UNAFFECTED. '
                'Do NOT claim IMDS-only mode breaks networking — it only changes ENI discovery source.'
            )

        # ANTI-HALLUCINATION: ENABLE_V4_EGRESS vs ENABLE_V6_EGRESS confusion (G28)
        v4_egress = cni_env.get('ENABLE_V4_EGRESS', '')
        v6_egress = cni_env.get('ENABLE_V6_EGRESS', '')
        ipv6_mode = cni_env.get('ENABLE_IPv6', 'false')
        if v4_egress or v6_egress:
            eks_context['guardrails'].append(
                'ANTI-HALLUCINATION: ENABLE_V4_EGRESS and ENABLE_V6_EGRESS are for OPPOSITE cluster modes. '
                'ENABLE_V6_EGRESS=true → IPv4 cluster gets IPv6 egress (egress-v6 plugin, ip6tables CNI-E6-* chains). '
                'ENABLE_V4_EGRESS=true → IPv6 cluster gets IPv4 egress (egress-v4 plugin, iptables CNI-E4-* chains). '
                f'Current cluster: IPv6={ipv6_mode}. '
                'Do NOT confuse which egress var applies to which cluster mode.'
            )

        # ANTI-HALLUCINATION: ENABLE_NFTABLES has been REMOVED from VPC CNI codebase
        eks_context['guardrails'].append(
            'ANTI-HALLUCINATION: ENABLE_NFTABLES env var has been REMOVED from the VPC CNI codebase. '
            'Auto-detection from kubelet replaced it in v1.13.1+. Do NOT reference ENABLE_NFTABLES '
            'as a current configuration option. If iptables mode issues arise, check the CNI version.'
        )

        # Clean up internal flags from results
        if 'iptables' in results and '_snat_present' in results['iptables']:
            del results['iptables']['_snat_present']
        if 'cni' in results and '_parsedFlags' in results['cni']:
            # Keep _parsedFlags in results for agent reference but rename to parsedFlags
            results['cni']['parsedFlags'] = results['cni'].pop('_parsedFlags')
        if 'kube_proxy' in results and '_parsedFlags' in results['kube_proxy']:
            results['kube_proxy']['parsedFlags'] = results['kube_proxy'].pop('_parsedFlags')

        total_issues = len(issues_found)
        critical_issues = sum(1 for i in issues_found if i.get('severity') == 'critical')
        warning_issues = sum(1 for i in issues_found if i.get('severity') == 'warning')

        # Confidence assessment
        sections_with_data = sum(1 for s in sections if s in results and results[s])
        if sections_with_data >= 4 and critical_issues > 0:
            confidence = 'high'
        elif sections_with_data >= 2 and total_issues > 0:
            confidence = 'medium'
        elif sections_with_data >= 1:
            confidence = 'low'
        else:
            confidence = 'none'

        # Identify gaps
        gaps = []
        if not bundle_files:
            gaps.append('No extracted bundle found — collect and wait for completion first')
        sections_without_files = [s for s in sections if s not in section_file_map or not section_file_map.get(s)]
        if sections_without_files:
            gaps.append(f'No files found for sections: {", ".join(sections_without_files)}')
        empty_reads = [s for s in sections if s in results and not any(
            v for k, v in results[s].items() if k not in ('issues', 'sourceFile', 'sourceFiles', '_note')
        )]
        if empty_reads:
            gaps.append(f'Sections returned empty data: {", ".join(empty_reads)}')

        # Match relevant SOPs based on detected networking issues
        recommended_sops = []
        try:
            if issues_found:
                recommended_sops = match_sops_for_issues(issues=issues_found, max_sops=5)
        except Exception:
            pass  # SOP matching is best-effort

        sop_hint = ' Use get_sop to review the recommended SOPs for detailed remediation steps.' if recommended_sops else ''

        # =================================================================
        # CRITICAL WARNINGS & ROOT CAUSE RANKING
        # These appear FIRST in the response to prevent the agent from
        # forming incorrect hypotheses before seeing the real issues.
        # =================================================================
        critical_warnings = []
        root_cause_ranking = []

        # Detect T1 scenario: empty KUBE-SERVICES + SGP config present
        kube_svc_empty = any(
            'KUBE-SERVICES chain is EMPTY' in i.get('message', '') or
            'KUBE-SERVICES chain missing' in i.get('message', '')
            for i in issues_found
        )
        sgp_config_present = cni_flags.get('podEni', False) or cni_flags.get('sgpMode', '') == 'strict'

        if kube_svc_empty:
            critical_warnings.append(
                '>>> STOP: KUBE-SERVICES CHAIN IS EMPTY OR MISSING. '
                'This means kube-proxy is NOT running or NOT syncing on this node. '
                'ALL service ClusterIP/NodePort routing is broken. '
                'This is the ROOT CAUSE of any service connectivity failure. '
                'Do NOT blame VPC CNI config, SGP, podSGEnforcingMode, or any other CNI setting. '
                'The VPC CNI NEVER touches KUBE-SERVICES — that chain is 100% owned by kube-proxy. '
                'FIX: Check if kube-proxy pod is running on this node. <<<'
            )
            root_cause_ranking.append({
                'rank': 1,
                'cause': 'kube-proxy not running/syncing on this node',
                'confidence': 'VERY HIGH',
                'evidence': 'KUBE-SERVICES chain is empty — zero KUBE-SVC rules',
                'owner': 'kube-proxy (NOT VPC CNI)',
                'action': 'Check kube-proxy DaemonSet scheduling and pod status on this node',
            })
            if sgp_config_present:
                critical_warnings.append(
                    '>>> WARNING: podSGEnforcingMode=strict IS PRESENT but is NOT the cause. '
                    'SGP enforcing mode ONLY affects pods with vpc.amazonaws.com/pod-eni annotation. '
                    'It CANNOT empty the KUBE-SERVICES chain. It CANNOT block unannotated pods. '
                    'The real problem is kube-proxy — see root cause ranking above. <<<'
                )

        # Detect kube-proxy not running (from kube_proxy section)
        kp_data = results.get('kube_proxy', {})
        kp_not_found = 'not found' in str(kp_data.get('issues', [])).lower() or not kp_data.get('versionInfo')
        if kp_not_found and not kube_svc_empty:
            root_cause_ranking.append({
                'rank': 2,
                'cause': 'kube-proxy process/config not found on node',
                'confidence': 'HIGH',
                'evidence': 'kube-proxy section returned no version or config data',
                'owner': 'kube-proxy DaemonSet',
                'action': 'Verify kube-proxy DaemonSet is scheduled to this node',
            })

        # Add CNI-related root causes only if they have real evidence
        for issue in issues_found:
            if issue.get('severity') == 'critical' and issue.get('section') != 'iptables':
                root_cause_ranking.append({
                    'rank': len(root_cause_ranking) + 1,
                    'cause': issue['message'],
                    'confidence': 'MEDIUM',
                    'evidence': f"Detected in {issue['section']} section",
                    'owner': issue['section'],
                    'action': 'Review section details',
                })

        # Build response with CRITICAL_WARNINGS FIRST
        response_data = {}

        # These go FIRST so the agent sees them before any config data
        if critical_warnings:
            response_data['CRITICAL_WARNINGS'] = critical_warnings
        if root_cause_ranking:
            response_data['rootCauseRanking'] = root_cause_ranking

        response_data['instanceId'] = instance_id

        # Bundle freshness info (stale bundles are already rejected above, so this is always fresh)
        if bundle_age_minutes is not None:
            response_data['bundleInfo'] = {
                'collectedAt': bundle_collected_at,
                'ageMinutes': bundle_age_minutes,
                'isStale': False,
            }

        response_data['issuesSummary'] = {
            'total': total_issues,
            'critical': critical_issues,
            'warning': warning_issues,
            'issues': issues_found,
        }
        response_data['overallAssessment'] = _network_assessment(issues_found)
        response_data['eksNetworkingContext'] = eks_context
        response_data['sections'] = sections
        response_data['diagnostics'] = results
        response_data['confidence'] = confidence
        response_data['gaps'] = gaps
        response_data['nextStep'] = (
            f'FIRST: Read CRITICAL_WARNINGS and rootCauseRanking above — they identify the real issue. '
            f'THEN: Review eksNetworkingContext guardrails before concluding on any networking issue. '
            f'Do NOT blame VPC CNI config for issues owned by kube-proxy.{sop_hint}'
            if issues_found else 'No networking issues detected in the bundle.'
        )
        if recommended_sops:
            response_data['recommendedSOPs'] = recommended_sops

        return success_response(response_data)

    except Exception as e:
        return error_response(500, f'network_diagnostics failed: {str(e)}')


def _network_assessment(issues: List[Dict]) -> str:
    """Generate overall network health assessment."""
    if not issues:
        return "HEALTHY — No networking issues detected in the log bundle."
    critical = [i for i in issues if i.get('severity') == 'critical']

    # Special case: kube-proxy is the root cause — call it out explicitly
    kube_proxy_down = any('KUBE-SERVICES' in i.get('message', '') for i in critical)
    if kube_proxy_down:
        return (
            "CRITICAL — kube-proxy is NOT running or NOT syncing on this node. "
            "KUBE-SERVICES chain is empty. ALL service routing (ClusterIP, NodePort) is broken. "
            "This is the PRIMARY root cause. Do NOT investigate VPC CNI config, SGP, or "
            "podSGEnforcingMode — they are NOT involved. Fix kube-proxy first."
        )

    if critical:
        sections = set(i['section'] for i in critical)
        # Proactive tcpdump escalation for non-kube-proxy critical issues
        if not kube_proxy_down:
            return (
                f"CRITICAL — {len(critical)} critical networking issues in: {', '.join(sections)}. Immediate investigation needed. "
                "ESCALATION: If log analysis is inconclusive, use tcpdump_capture to capture live traffic "
                "on the affected node. Target port 53 for DNS issues, port 443/6443 for API server issues, "
                "or the pod IP for pod-to-pod connectivity problems."
            )
        return f"CRITICAL — {len(critical)} critical networking issues in: {', '.join(sections)}. Immediate investigation needed."
    return f"WARNING — {len(issues)} non-critical networking issues found. Review recommended."



# =============================================================================
# STORAGE / VOLUME MOUNT / CSI DIAGNOSTICS
# =============================================================================

def storage_diagnostics(arguments: Dict) -> Dict:
    """
    Extract and structure storage/volume/CSI info from collected log bundles.
    Parses kubelet volume mount errors, EBS/EFS CSI driver logs, PV/PVC status,
    and cross-references with instance type and ENI config.

    Inputs:
        instanceId: EC2 instance ID (required)
        sections: comma-separated: "kubelet,ebs_csi,efs_csi,pv_pvc,instance" or "all" (default: "all")

    Returns:
        Structured storage diagnostics per section with eksStorageContext guardrails
    """
    instance_id = arguments.get('instanceId')
    if not instance_id:
        return error_response(400, 'instanceId is required')

    # Validate region and EKS instance
    target_region, region_error = resolve_and_validate_region(arguments, instance_id)
    if region_error:
        return region_error
    instance_error = validate_eks_instance(instance_id, target_region)
    if instance_error:
        return instance_error

    sections_str = arguments.get('sections', 'all')
    valid_sections = {'kubelet', 'ebs_csi', 'efs_csi', 'pv_pvc', 'instance'}
    if sections_str == 'all':
        sections = ['kubelet', 'ebs_csi', 'efs_csi', 'pv_pvc', 'instance']
    else:
        sections = [s.strip() for s in sections_str.split(',')]
        invalid = [s for s in sections if s not in valid_sections]
        if invalid:
            return error_response(400, f"Invalid section(s): {', '.join(invalid)}. Valid: {', '.join(sorted(valid_sections))}")

    results = {}
    issues_found = []

    try:
        # Find extracted bundle files
        bundle_files = []
        # Use shared latest-bundle discovery
        bundle_info = find_latest_bundle_files(instance_id)
        if bundle_info['success']:
            bundle_files = bundle_info['files']
        else:
            bundle_files = []

        if not bundle_files:
            return error_response(404, f'No extracted log bundle found for {instance_id}. Run collect first.')

        def find_files(patterns):
            matched = []
            for f in bundle_files:
                fname = f.lower()
                for p in patterns:
                    if p in fname:
                        matched.append(f)
                        break
            return matched

        # Pre-fetch files
        files_to_fetch = set()
        fetch_sizes = {}

        if 'kubelet' in sections:
            keys = find_files(['kubelet'])[:5]
            for k in keys:
                files_to_fetch.add(k)
                fetch_sizes[k] = 524288
        if 'ebs_csi' in sections:
            keys = find_files(['ebs-csi', 'ebs_csi', 'aws-ebs-csi'])[:5]
            for k in keys:
                files_to_fetch.add(k)
                fetch_sizes[k] = 524288
        if 'efs_csi' in sections:
            keys = find_files(['efs-csi', 'efs_csi', 'aws-efs-csi'])[:5]
            for k in keys:
                files_to_fetch.add(k)
                fetch_sizes[k] = 524288
        if 'pv_pvc' in sections:
            keys = find_files(['persistentvolume', 'pv', 'pvc', 'storageclass', 'csinode', 'volumeattachment'])[:8]
            for k in keys:
                files_to_fetch.add(k)
                fetch_sizes[k] = 262144

        file_contents = {}
        def _fetch(key):
            r = safe_s3_read(key, max_size=fetch_sizes.get(key, 262144))
            return key, r.get('content', '') if r.get('success') else None

        with ThreadPoolExecutor(max_workers=10) as executor:
            for key, content in executor.map(_fetch, list(files_to_fetch)):
                file_contents[key] = content

        def read_content(key, max_size=262144):
            cached = file_contents.get(key)
            if cached is not None:
                return cached
            r = safe_s3_read(key, max_size=max_size)
            return r.get('content', '') if r.get('success') else None


        # =================================================================
        # KUBELET VOLUME MOUNT ERRORS
        # =================================================================
        if 'kubelet' in sections:
            kubelet_data = {
                'volumeErrors': [], 'mountErrors': [], 'attachErrors': [],
                'multiAttachErrors': [], 'fsErrors': [], 'csiErrors': [],
                'issues': []
            }
            kubelet_files = find_files(['kubelet'])
            for f in kubelet_files[:5]:
                content = read_content(f, max_size=524288)
                if not content:
                    continue
                for line in content.split('\n'):
                    ll = line.lower()
                    stripped = line.strip()[:300]

                    # FailedMount / FailedAttachVolume
                    if 'failedmount' in ll or 'failed to mount' in ll:
                        kubelet_data['mountErrors'].append(stripped)
                    if 'failedattachvolume' in ll or 'failed to attach' in ll:
                        kubelet_data['attachErrors'].append(stripped)

                    # Multi-Attach error (6-min delay is NORMAL K8s behavior)
                    if 'multi-attach' in ll:
                        kubelet_data['multiAttachErrors'].append(stripped)

                    # Filesystem errors
                    if ('wrong fs type' in ll or 'bad superblock' in ll or
                            'mount: ' in ll and 'failed' in ll):
                        kubelet_data['fsErrors'].append(stripped)

                    # CSI-related kubelet errors
                    if 'csi' in ll and ('error' in ll or 'failed' in ll):
                        kubelet_data['csiErrors'].append(stripped)

                    # Generic volume errors
                    if 'volume' in ll and ('error' in ll or 'failed' in ll or 'timeout' in ll):
                        kubelet_data['volumeErrors'].append(stripped)

            # Cap arrays
            for key in ['volumeErrors', 'mountErrors', 'attachErrors', 'multiAttachErrors', 'fsErrors', 'csiErrors']:
                kubelet_data[key] = kubelet_data[key][:25]

            # Guardrail: Multi-Attach 6-minute delay
            if kubelet_data['multiAttachErrors']:
                kubelet_data['issues'].append(
                    f"{len(kubelet_data['multiAttachErrors'])} Multi-Attach errors found. "
                    "IMPORTANT: After unclean pod termination, Kubernetes waits ~6 minutes before "
                    "force-detaching an EBS volume (maxWaitForUnmountDuration). This is NORMAL K8s "
                    "behavior, NOT a CSI driver bug. Check Node.Status.VolumesInUse to see if the "
                    "old node still reports the volume."
                )
                issues_found.append({'section': 'kubelet', 'severity': 'warning',
                                     'message': 'Multi-Attach errors (6-min delay is normal K8s behavior after unclean termination)'})

            # Guardrail: XFS superblock error on older kernels
            xfs_errors = [e for e in kubelet_data['fsErrors'] if 'wrong fs type' in e.lower() or 'bad superblock' in e.lower()]
            if xfs_errors:
                kubelet_data['issues'].append(
                    "Filesystem mount errors detected (wrong fs type / bad superblock). "
                    "If using XFS on Amazon Linux 2 with newer xfsprogs, this is a KNOWN ISSUE. "
                    "Fix: set --legacy-xfs=true on the EBS CSI node DaemonSet. "
                    "Also check: mkfs options, volume was formatted with a compatible filesystem."
                )
                issues_found.append({'section': 'kubelet', 'severity': 'critical',
                                     'message': 'Filesystem mount errors (check XFS compatibility / --legacy-xfs)'})

            if kubelet_data['mountErrors'] and not kubelet_data['multiAttachErrors']:
                issues_found.append({'section': 'kubelet', 'severity': 'critical',
                                     'message': f"{len(kubelet_data['mountErrors'])} FailedMount errors in kubelet logs"})
            if kubelet_data['attachErrors']:
                issues_found.append({'section': 'kubelet', 'severity': 'critical',
                                     'message': f"{len(kubelet_data['attachErrors'])} FailedAttachVolume errors in kubelet logs"})

            kubelet_data['sourceFiles'] = kubelet_files[:5]
            results['kubelet'] = kubelet_data


        # =================================================================
        # EBS CSI DRIVER
        # =================================================================
        if 'ebs_csi' in sections:
            ebs_data = {
                'controllerErrors': [], 'nodeErrors': [], 'attachLimitIssues': [],
                'throttlingErrors': [], 'iamErrors': [], 'issues': []
            }
            ebs_files = find_files(['ebs-csi', 'ebs_csi', 'aws-ebs-csi'])
            # Also check container logs for ebs-csi pods
            if not ebs_files:
                ebs_files = find_files(['ebs-csi'])

            # Prerequisite check: warn if no CSI driver logs found at all
            if not ebs_files:
                ebs_data['issues'].append(
                    'No EBS CSI driver log files found in the bundle. '
                    'The EBS CSI driver may not be installed on this cluster. '
                    'Verify installation: kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver. '
                    'If not installed, volume mount errors are expected — install the EBS CSI driver add-on first.'
                )
                issues_found.append({'section': 'ebs_csi', 'severity': 'critical',
                                     'message': 'EBS CSI driver logs not found — driver may not be installed'})

            for f in ebs_files[:5]:
                content = read_content(f, max_size=524288)
                if not content:
                    continue
                is_controller = 'controller' in f.lower()
                for line in content.split('\n'):
                    ll = line.lower()
                    stripped = line.strip()[:300]

                    # EC2 API throttling
                    if 'throttl' in ll or 'requestlimitexceeded' in ll or 'rate exceeded' in ll:
                        ebs_data['throttlingErrors'].append(stripped)

                    # Volume attach limit
                    if 'attach' in ll and ('limit' in ll or 'maximum' in ll or 'capacity' in ll):
                        ebs_data['attachLimitIssues'].append(stripped)

                    # IAM / permission errors
                    if ('accessdenied' in ll or 'unauthorized' in ll or
                            'not authorized' in ll or 'forbidden' in ll):
                        ebs_data['iamErrors'].append(stripped)

                    # General errors
                    if 'error' in ll or 'failed' in ll:
                        if is_controller:
                            ebs_data['controllerErrors'].append(stripped)
                        else:
                            ebs_data['nodeErrors'].append(stripped)

            # Cap arrays
            for key in ebs_data:
                if isinstance(ebs_data[key], list) and key != 'issues':
                    ebs_data[key] = ebs_data[key][:20]

            # Guardrail: EC2 API throttling
            if ebs_data['throttlingErrors']:
                ebs_data['issues'].append(
                    f"{len(ebs_data['throttlingErrors'])} EC2 API throttling errors. "
                    "High worker-threads in CSI sidecars (external-provisioner, external-attacher) "
                    "can cause EC2 API throttling that affects ALL instances in the account/region, "
                    "not just this node. Reduce --worker-threads or --kube-api-qps in sidecar containers."
                )
                issues_found.append({'section': 'ebs_csi', 'severity': 'critical',
                                     'message': 'EC2 API throttling in EBS CSI (affects entire account/region)'})

            # Guardrail: IAM permissions
            if ebs_data['iamErrors']:
                ebs_data['issues'].append(
                    f"{len(ebs_data['iamErrors'])} IAM/permission errors. "
                    "EBS CSI driver needs AmazonEBSCSIDriverPolicy. Use EKS Pod Identity or IRSA "
                    "(NOT instance profile). For encrypted volumes, add KMS permissions: "
                    "kms:CreateGrant, kms:Decrypt, kms:GenerateDataKeyWithoutPlaintext."
                )
                issues_found.append({'section': 'ebs_csi', 'severity': 'critical',
                                     'message': 'IAM permission errors in EBS CSI driver'})

            # Guardrail: Attach limit
            if ebs_data['attachLimitIssues']:
                ebs_data['issues'].append(
                    f"{len(ebs_data['attachLimitIssues'])} volume attachment limit issues. "
                    "ENIs consume EBS attachment slots on pre-Gen7 instances (shared limit). "
                    "With VPC CNI prefix delegation, fewer ENIs are used, freeing EBS slots. "
                    "Gen7+ instances (m7i, c7g, etc.) have DEDICATED EBS attachment limits separate from ENIs. "
                    "Use --reserved-volume-attachments or --volume-attach-limit on CSI node to adjust. "
                    "K8s 1.34+ supports MutableCSINodeAllocatableCount for dynamic limit updates."
                )
                issues_found.append({'section': 'ebs_csi', 'severity': 'warning',
                                     'message': 'EBS volume attachment limit issues (check ENI vs EBS slot sharing)'})

            if ebs_data['controllerErrors'] or ebs_data['nodeErrors']:
                total = len(ebs_data['controllerErrors']) + len(ebs_data['nodeErrors'])
                issues_found.append({'section': 'ebs_csi', 'severity': 'warning',
                                     'message': f'{total} errors in EBS CSI driver logs'})

            ebs_data['sourceFiles'] = ebs_files[:5]
            results['ebs_csi'] = ebs_data


        # =================================================================
        # EFS CSI DRIVER
        # =================================================================
        if 'efs_csi' in sections:
            efs_data = {
                'controllerErrors': [], 'nodeErrors': [], 'mountErrors': [],
                'accessPointIssues': [], 'dnsErrors': [], 'issues': []
            }
            efs_files = find_files(['efs-csi', 'efs_csi', 'aws-efs-csi'])
            if not efs_files:
                efs_files = find_files(['efs-csi'])
            for f in efs_files[:5]:
                content = read_content(f, max_size=524288)
                if not content:
                    continue
                is_controller = 'controller' in f.lower()
                for line in content.split('\n'):
                    ll = line.lower()
                    stripped = line.strip()[:300]

                    # Mount failures
                    if 'mount' in ll and ('failed' in ll or 'error' in ll or 'timeout' in ll):
                        efs_data['mountErrors'].append(stripped)

                    # Access point issues
                    if 'accesspoint' in ll or 'access_point' in ll or 'access point' in ll:
                        if 'error' in ll or 'failed' in ll or 'not found' in ll:
                            efs_data['accessPointIssues'].append(stripped)

                    # DNS resolution failures (botocore fallback)
                    if 'dns' in ll or 'resolve' in ll or 'nslookup' in ll:
                        if 'error' in ll or 'failed' in ll or 'timeout' in ll:
                            efs_data['dnsErrors'].append(stripped)

                    # General errors
                    if 'error' in ll or 'failed' in ll:
                        if is_controller:
                            efs_data['controllerErrors'].append(stripped)
                        else:
                            efs_data['nodeErrors'].append(stripped)

            for key in efs_data:
                if isinstance(efs_data[key], list) and key != 'issues':
                    efs_data[key] = efs_data[key][:20]

            # Guardrail: EFS mount timeout
            if efs_data['mountErrors']:
                efs_data['issues'].append(
                    f"{len(efs_data['mountErrors'])} EFS mount errors. "
                    "Common causes: (1) Security group missing NFS port 2049 inbound rule, "
                    "(2) No mount target in the node's AZ/subnet, "
                    "(3) Network policy blocking NFS traffic (port 2049), "
                    "(4) For cross-VPC mounts, botocore must be installed for DNS resolution fallback."
                )
                issues_found.append({'section': 'efs_csi', 'severity': 'critical',
                                     'message': 'EFS mount errors (check SG port 2049, mount target AZ, network policy)'})

            # Guardrail: Access point issues
            if efs_data['accessPointIssues']:
                efs_data['issues'].append(
                    f"{len(efs_data['accessPointIssues'])} access point issues. "
                    "EFS dynamic provisioning creates access points automatically. "
                    "Each EFS file system supports up to 1000 access points. "
                    "The EFS file system itself must be pre-created — CSI driver only creates access points."
                )
                issues_found.append({'section': 'efs_csi', 'severity': 'warning',
                                     'message': 'EFS access point issues'})

            # Guardrail: DNS errors (cross-VPC)
            if efs_data['dnsErrors']:
                efs_data['issues'].append(
                    f"{len(efs_data['dnsErrors'])} DNS resolution errors. "
                    "For cross-VPC EFS mounts, install botocore in the CSI driver container "
                    "to enable mount target IP resolution fallback when DNS fails."
                )
                issues_found.append({'section': 'efs_csi', 'severity': 'warning',
                                     'message': 'EFS DNS resolution errors (check cross-VPC botocore fallback)'})

            efs_data['sourceFiles'] = efs_files[:5]
            results['efs_csi'] = efs_data


        # =================================================================
        # PV / PVC / STORAGECLASS STATUS
        # =================================================================
        if 'pv_pvc' in sections:
            pv_data = {
                'persistentVolumes': [], 'persistentVolumeClaims': [],
                'storageClasses': [], 'csiNodes': [], 'volumeAttachments': [],
                'issues': []
            }
            pv_files = find_files(['persistentvolume', 'pv', 'pvc', 'storageclass', 'csinode', 'volumeattachment'])
            for f in pv_files[:8]:
                content = read_content(f, max_size=262144)
                if not content:
                    continue
                fname = f.lower()

                # Try JSON parse for kubectl output
                try:
                    data = json.loads(content)
                    items = data.get('items', [data]) if isinstance(data, dict) else []
                    for item in items[:50]:
                        kind = item.get('kind', '')
                        meta = item.get('metadata', {})
                        spec = item.get('spec', {})
                        status = item.get('status', {})
                        name = meta.get('name', 'unknown')

                        if kind == 'PersistentVolume' or 'persistentvolume' in fname:
                            pv_entry = {
                                'name': name,
                                'capacity': spec.get('capacity', {}).get('storage', ''),
                                'accessModes': spec.get('accessModes', []),
                                'reclaimPolicy': spec.get('persistentVolumeReclaimPolicy', ''),
                                'storageClass': spec.get('storageClassName', ''),
                                'phase': status.get('phase', ''),
                                'csiDriver': spec.get('csi', {}).get('driver', ''),
                                'volumeHandle': spec.get('csi', {}).get('volumeHandle', '')[:50],
                            }
                            pv_data['persistentVolumes'].append(pv_entry)
                            # Check for stuck PVs
                            if status.get('phase') == 'Released':
                                pv_data['issues'].append(f"PV {name} is Released but not reclaimed (reclaimPolicy={spec.get('persistentVolumeReclaimPolicy', '')})")
                                issues_found.append({'section': 'pv_pvc', 'severity': 'warning', 'message': f'PV {name} stuck in Released phase'})

                        elif kind == 'PersistentVolumeClaim' or 'pvc' in fname:
                            pvc_entry = {
                                'name': name,
                                'namespace': meta.get('namespace', ''),
                                'storageClass': spec.get('storageClassName', ''),
                                'accessModes': spec.get('accessModes', []),
                                'requestedStorage': spec.get('resources', {}).get('requests', {}).get('storage', ''),
                                'phase': status.get('phase', ''),
                                'volumeName': spec.get('volumeName', ''),
                            }
                            pv_data['persistentVolumeClaims'].append(pvc_entry)
                            if status.get('phase') == 'Pending':
                                pv_data['issues'].append(f"PVC {meta.get('namespace', '')}/{name} is Pending — no PV bound")
                                issues_found.append({'section': 'pv_pvc', 'severity': 'critical', 'message': f'PVC {name} stuck in Pending phase'})

                        elif kind == 'StorageClass' or 'storageclass' in fname:
                            pv_data['storageClasses'].append({
                                'name': name,
                                'provisioner': spec.get('provisioner', item.get('provisioner', '')),
                                'reclaimPolicy': spec.get('reclaimPolicy', item.get('reclaimPolicy', '')),
                                'volumeBindingMode': spec.get('volumeBindingMode', item.get('volumeBindingMode', '')),
                                'allowVolumeExpansion': item.get('allowVolumeExpansion', False),
                            })
                            # Guardrail: in-tree provisioner migration
                            provisioner = spec.get('provisioner', item.get('provisioner', ''))
                            if provisioner == 'kubernetes.io/aws-ebs':
                                pv_data['issues'].append(
                                    f"StorageClass {name} uses in-tree provisioner kubernetes.io/aws-ebs. "
                                    "CSI migration translates this to ebs.csi.aws.com at runtime. "
                                    "If CSI migration feature gates are disabled, volumes will use the deprecated in-tree driver."
                                )
                                issues_found.append({'section': 'pv_pvc', 'severity': 'info',
                                                     'message': f'StorageClass {name} uses in-tree provisioner (CSI migration active)'})

                        elif kind == 'CSINode' or 'csinode' in fname:
                            drivers = spec.get('drivers', [])
                            for drv in drivers:
                                pv_data['csiNodes'].append({
                                    'name': name,
                                    'driver': drv.get('name', ''),
                                    'allocatable': drv.get('allocatable', {}).get('count'),
                                    'topologyKeys': drv.get('topologyKeys', []),
                                })

                        elif kind == 'VolumeAttachment' or 'volumeattachment' in fname:
                            pv_data['volumeAttachments'].append({
                                'name': name,
                                'attacher': spec.get('attacher', ''),
                                'nodeName': spec.get('nodeName', ''),
                                'pvName': spec.get('source', {}).get('persistentVolumeName', ''),
                                'attached': status.get('attached', False),
                            })
                            if not status.get('attached', False):
                                pv_data['issues'].append(f"VolumeAttachment {name} not attached to {spec.get('nodeName', '')}")
                                issues_found.append({'section': 'pv_pvc', 'severity': 'warning',
                                                     'message': f'VolumeAttachment {name} not attached'})
                except (json.JSONDecodeError, TypeError):
                    # Not JSON — try line-based parsing for kubectl text output
                    pass

            # Cap arrays
            for key in ['persistentVolumes', 'persistentVolumeClaims', 'storageClasses', 'csiNodes', 'volumeAttachments']:
                pv_data[key] = pv_data[key][:30]

            pv_data['sourceFiles'] = pv_files[:8]
            results['pv_pvc'] = pv_data


        # =================================================================
        # INSTANCE TYPE / EBS ATTACHMENT CAPACITY
        # =================================================================
        if 'instance' in sections:
            inst_data = {'instanceType': None, 'ebsLimits': {}, 'eniCount': 0, 'issues': []}
            try:
                target_region = resolve_region(arguments, instance_id)
                regional_ec2 = get_regional_client('ec2', target_region)
                desc = regional_ec2.describe_instances(InstanceIds=[instance_id])
                reservations = desc.get('Reservations', [])
                if reservations and reservations[0].get('Instances'):
                    inst = reservations[0]['Instances'][0]
                    inst_type = inst.get('InstanceType', '')
                    inst_data['instanceType'] = inst_type

                    # Count ENIs
                    eni_resp = regional_ec2.describe_network_interfaces(
                        Filters=[{'Name': 'attachment.instance-id', 'Values': [instance_id]}]
                    )
                    eni_count = len(eni_resp.get('NetworkInterfaces', []))
                    inst_data['eniCount'] = eni_count

                    # Check if Gen7+ (dedicated EBS limits)
                    is_gen7_plus = False
                    gen_match = re.match(r'^[a-z]+(\d+)', inst_type)
                    if gen_match:
                        gen_num = int(gen_match.group(1))
                        is_gen7_plus = gen_num >= 7
                    inst_data['isGen7Plus'] = is_gen7_plus

                    if is_gen7_plus:
                        inst_data['ebsLimits']['note'] = (
                            f'{inst_type} is Gen7+ — has DEDICATED EBS volume attachment limits '
                            'separate from ENI limits. ENIs do NOT consume EBS attachment slots.'
                        )
                    else:
                        inst_data['ebsLimits']['note'] = (
                            f'{inst_type} is pre-Gen7 — EBS and ENI share attachment slots. '
                            f'Currently {eni_count} ENIs attached, each consuming an EBS slot. '
                            'With VPC CNI prefix delegation, fewer ENIs are needed, freeing EBS slots.'
                        )
                        if eni_count >= 3:
                            inst_data['issues'].append(
                                f'{eni_count} ENIs attached on pre-Gen7 instance {inst_type}. '
                                'Each ENI consumes an EBS attachment slot. If volume attach fails, '
                                'consider: (1) Enable prefix delegation to reduce ENI count, '
                                '(2) Use --reserved-volume-attachments on CSI node, '
                                '(3) Upgrade to Gen7+ instance type with dedicated EBS limits.'
                            )
                            issues_found.append({'section': 'instance', 'severity': 'info',
                                                 'message': f'{eni_count} ENIs on pre-Gen7 {inst_type} (shared EBS/ENI slots)'})

                    # Check IMDS hop limit for CSI driver
                    metadata_options = inst.get('MetadataOptions', {})
                    hop_limit = metadata_options.get('HttpPutResponseHopLimit', 1)
                    inst_data['imdsHopLimit'] = hop_limit
                    if hop_limit < 2:
                        inst_data['issues'].append(
                            f'IMDSv2 hop limit is {hop_limit} (must be >=2 for containerized CSI drivers). '
                            'EBS CSI node DaemonSet runs in a container and needs 2 hops to reach IMDS. '
                            'Fix: aws ec2 modify-instance-metadata-options --instance-id {instance_id} '
                            '--http-put-response-hop-limit 2'
                        )
                        issues_found.append({'section': 'instance', 'severity': 'critical',
                                             'message': f'IMDSv2 hop limit={hop_limit} (needs >=2 for CSI drivers in containers)'})

            except Exception as e:
                inst_data['issues'].append(f'Could not query instance info: {str(e)}')

            results['instance'] = inst_data

        # =================================================================
        # EKS STORAGE CONTEXT (guardrails)
        # =================================================================
        eks_context = {
            '_purpose': 'EKS-specific storage context to prevent misinterpretation of volume/CSI findings. '
                        'Read guardrails array before concluding on any storage issue.',
            'guardrails': [],
            'logPaths': {
                'kubeletVolumeManager': 'journalctl -u kubelet | grep -i volume',
                'ebsCsiController': '/var/log/containers/*ebs-csi-controller*',
                'ebsCsiNode': '/var/log/containers/*ebs-csi-node*',
                'efsCsiController': '/var/log/containers/*efs-csi-controller*',
                'efsCsiNode': '/var/log/containers/*efs-csi-node*',
                'csiNodeInfo': 'kubectl get csinodes -o yaml',
                'volumeAttachments': 'kubectl get volumeattachments -o yaml',
            }
        }

        # Always-present guardrails
        eks_context['guardrails'].extend([
            'Multi-Attach error with ~6 minute delay after pod termination is NORMAL Kubernetes behavior. '
            'K8s waits maxWaitForUnmountDuration (default 6min) before force-detaching EBS volumes. '
            'This is NOT a CSI driver bug. Check Node.Status.VolumesInUse on the old node.',

            'EBS volume attachment slots are SHARED with ENIs on pre-Gen7 instances (m5, c5, r5, etc.). '
            'VPC CNI attaches secondary ENIs for pod IPs — each ENI consumes an EBS slot. '
            'If volume attach fails with "maximum number of volumes already attached", check ENI count. '
            'Fix: enable prefix delegation (fewer ENIs), use --reserved-volume-attachments, or upgrade to Gen7+.',

            'IMDSv2 hop limit must be >=2 for EBS/EFS CSI drivers running in containers. '
            'With hop limit=1, CSI node DaemonSet cannot reach IMDS for instance metadata. '
            'Fix: modify-instance-metadata-options --http-put-response-hop-limit 2.',

            'ebs.csi.aws.com/agent-not-ready:NoExecute taint prevents pods from scheduling before '
            'the EBS CSI node DaemonSet is ready. If pods are stuck Pending with this taint, '
            'check that the EBS CSI node DaemonSet is running and healthy on the node.',

            'XFS "wrong fs type, bad superblock" on Amazon Linux 2 with newer xfsprogs is a KNOWN ISSUE. '
            'Fix: set --legacy-xfs=true on the EBS CSI node DaemonSet args.',

            'EFS storage capacity in PV/PVC is MEANINGLESS — EFS is elastic and ignores the capacity value. '
            'The capacity field is required by Kubernetes but NOT enforced by EFS. '
            'Do NOT flag EFS PV/PVC capacity mismatches as issues.',

            'EFS dynamic provisioning creates access points (up to 1000 per file system). '
            'The EFS file system itself must be pre-created — CSI driver only manages access points. '
            'Each PV maps to one access point.',

            'StorageClass with provisioner kubernetes.io/aws-ebs uses the deprecated in-tree driver. '
            'CSI migration feature gates translate this to ebs.csi.aws.com at runtime. '
            'If CSI migration is disabled, volumes use the old in-tree path. '
            'Kubelet MUST be drained before changing CSI migration feature gates.',

            'EC2 API throttling from CSI sidecars (external-provisioner, external-attacher) with high '
            '--worker-threads can prevent volume operations across the ENTIRE account/region, not just one node. '
            'Symptoms: CreateVolume/AttachVolume/DetachVolume timeouts across multiple nodes simultaneously.',

            'Network policies in strict mode can block CSI driver communication. '
            'EBS CSI controller needs to reach EC2 API (HTTPS 443). '
            'EFS CSI node needs NFS port 2049 to mount targets. '
            'Ensure NetworkPolicy allows egress for CSI driver pods.',

            'For cross-VPC EFS mounts, install botocore in the EFS CSI driver container. '
            'Without botocore, DNS resolution for mount targets in other VPCs will fail. '
            'The driver falls back to botocore-based mount target IP resolution.',
        ])

        # Cross-reference: instance type context
        inst_type = results.get('instance', {}).get('instanceType', '')
        is_gen7 = results.get('instance', {}).get('isGen7Plus', False)
        eni_count = results.get('instance', {}).get('eniCount', 0)
        if inst_type and not is_gen7 and eni_count >= 2:
            eks_context['guardrails'].append(
                f'Instance {inst_type} (pre-Gen7) has {eni_count} ENIs attached. '
                f'Each ENI consumes an EBS attachment slot. Available EBS slots are reduced. '
                'This is the #1 cause of "maximum volumes attached" errors on EKS nodes with VPC CNI.'
            )

        # Cross-reference: CSINode allocatable count
        csi_nodes = results.get('pv_pvc', {}).get('csiNodes', [])
        for cn in csi_nodes:
            if cn.get('driver') == 'ebs.csi.aws.com' and cn.get('allocatable') is not None:
                eks_context['ebsCsiAllocatable'] = cn['allocatable']
                eks_context['guardrails'].append(
                    f'CSINode reports ebs.csi.aws.com allocatable count = {cn["allocatable"]}. '
                    'This is the max EBS volumes this node can attach as reported by the CSI driver. '
                    'If this seems low, check --volume-attach-limit and --reserved-volume-attachments flags.'
                )
                break

        # Clean up and return
        total_issues = len(issues_found)
        critical_issues = sum(1 for i in issues_found if i.get('severity') == 'critical')
        warning_issues = sum(1 for i in issues_found if i.get('severity') == 'warning')

        sections_with_data = sum(1 for s in sections if s in results and results[s])
        if sections_with_data >= 3 and critical_issues > 0:
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
        for s in sections:
            if s not in results:
                gaps.append(f'No data found for section: {s}')
            elif s in results and not any(v for k, v in results[s].items() if k not in ('issues', 'sourceFiles')):
                gaps.append(f'Section {s} returned empty data')

        # Match relevant SOPs based on detected storage issues
        recommended_sops = []
        try:
            if issues_found:
                recommended_sops = match_sops_for_issues(issues=issues_found, max_sops=5)
        except Exception:
            pass  # SOP matching is best-effort

        sop_hint = ' Use get_sop to review the recommended SOPs for detailed remediation steps.' if recommended_sops else ''
        response_data = {
            'instanceId': instance_id,
            'sections': sections,
            'diagnostics': results,
            'eksStorageContext': eks_context,
            'issuesSummary': {
                'total': total_issues,
                'critical': critical_issues,
                'warning': warning_issues,
                'issues': issues_found,
            },
            'confidence': confidence,
            'gaps': gaps,
            'overallAssessment': _storage_assessment(issues_found),
            'nextStep': f'Review eksStorageContext guardrails before concluding on any storage issue.{sop_hint}' if issues_found else 'No storage issues detected in the bundle.',
        }
        if recommended_sops:
            response_data['recommendedSOPs'] = recommended_sops

        return success_response(response_data)

    except Exception as e:
        return error_response(500, f'storage_diagnostics failed: {str(e)}')


def _storage_assessment(issues: List[Dict]) -> str:
    """Generate overall storage health assessment."""
    if not issues:
        return "HEALTHY — No storage/volume/CSI issues detected in the log bundle."
    critical = [i for i in issues if i.get('severity') == 'critical']
    if critical:
        sections = set(i['section'] for i in critical)
        return f"CRITICAL — {len(critical)} critical storage issues in: {', '.join(sections)}. Immediate investigation needed."
    return f"WARNING — {len(issues)} non-critical storage issues found. Review recommended."

# =============================================================================
# TCPDUMP CAPTURE VIA SSM RUN COMMAND
# =============================================================================

def tcpdump_capture(arguments: Dict) -> Dict:
    """
    Run tcpdump on an EKS worker node via SSM Run Command for a specified duration,
    then upload the pcap file to S3.

    Inputs:
        instanceId: EC2 instance ID (required)
        durationSeconds: Capture duration in seconds (default: 120, max: 300)
        interface: Network interface to capture on (default: "any")
        filter: BPF filter expression (e.g., "port 443", "host 10.0.0.1") (optional)
        region: AWS region where the instance runs (optional, auto-detected)

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
    # Sanitize interface name to prevent injection — strict allowlist
    if not re.match(r'^[a-zA-Z0-9\-\.]+$', interface):
        return error_response(400, f'Invalid interface name: {interface}')

    bpf_filter = arguments.get('filter', '')
    # Allowlist-based BPF filter validation (replaces denylist approach)
    bpf_error = validate_bpf_filter(bpf_filter)
    if bpf_error:
        return error_response(400, f'Invalid BPF filter: {bpf_error}')

    # Container/pod namespace support
    container_pid = arguments.get('containerPid', '')
    if container_pid:
        container_pid = str(container_pid).strip()
        if not re.match(r'^\d+$', container_pid):
            return error_response(400, f'Invalid containerPid — must be a numeric PID: {container_pid}')

    pod_name = arguments.get('podName', '').strip()
    pod_namespace = arguments.get('podNamespace', 'default').strip()
    if pod_name and not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9\-\.]{0,252}$', pod_name):
        return error_response(400, f'Invalid podName: {pod_name}')
    if pod_namespace and not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9\-]{0,62}$', pod_namespace):
        return error_response(400, f'Invalid podNamespace: {pod_namespace}')

    # Can't specify both podName and containerPid
    if pod_name and container_pid:
        return error_response(400, 'Specify either podName or containerPid, not both')

    # ── Confirmation gate (T2 mitigation) ──
    # tcpdump installs packages, enters container namespaces, and captures raw
    # network traffic as root. Require explicit confirmation to proceed.
    confirm = arguments.get('confirmCapture', False)
    if confirm is not True and str(confirm).lower() != 'true':
        scope_desc = (
            f'pod {pod_namespace}/{pod_name}' if pod_name
            else f'container PID {container_pid}' if container_pid
            else 'host network namespace'
        )
        return error_response(400,
            f'tcpdump_capture requires explicit confirmation. This tool will: '
            f'(1) run tcpdump as root on instance {instance_id} targeting {scope_desc}, '
            f'(2) capture raw network packets for {duration}s on interface {interface}, '
            f'(3) upload pcap to S3. '
            f'Set confirmCapture=true to proceed.',
            {
                'requiresConfirmation': True,
                'instanceId': instance_id,
                'scope': scope_desc,
                'durationSeconds': duration,
                'interface': interface,
                'filter': bpf_filter or 'none',
            }
        )

    # Check if this is a status poll for an existing command
    command_id = arguments.get('commandId')
    if command_id:
        return _poll_tcpdump_status(command_id, instance_id, arguments)

    # Resolve and validate region
    target_region, region_error = resolve_and_validate_region(arguments, instance_id)
    if region_error:
        return region_error

    # Validate instance belongs to an EKS cluster
    instance_error = validate_eks_instance(instance_id, target_region)
    if instance_error:
        return instance_error

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

    # Determine nsenter prefix based on pod or PID
    use_nsenter = bool(container_pid or pod_name)
    ns_label = ''
    if container_pid:
        ns_label = f' (container PID {container_pid} namespace)'
    elif pod_name:
        ns_label = f' (pod {pod_namespace}/{pod_name} namespace)'

    script = f"""#!/bin/bash
set -euo pipefail

PCAP_FILE="/tmp/tcpdump_capture_{timestamp}.pcap"
TXT_FILE="/tmp/tcpdump_summary_{timestamp}.txt"
STATS_FILE="/tmp/tcpdump_stats_{timestamp}.json"

# Check if tcpdump is available — do NOT auto-install packages (T2 mitigation)
if ! command -v tcpdump &>/dev/null; then
    echo "FATAL: tcpdump is not installed on this node. Install it manually or use an AMI that includes tcpdump."
    echo "For Amazon Linux 2: yum install -y tcpdump"
    echo "For Ubuntu: apt-get install -y tcpdump"
    exit 1
fi

NSENTER_PREFIX=""
"""

    # Add pod PID discovery when podName is provided
    if pod_name:
        script += f"""
# === Resolve pod "{pod_namespace}/{pod_name}" to container PID ===
echo "Resolving pod {pod_namespace}/{pod_name} to container PID..."
TARGET_PID=""

# Ensure PATH includes common binary locations (SSM may have minimal PATH)
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

# Set containerd endpoint for crictl (EKS standard)
export CONTAINER_RUNTIME_ENDPOINT="unix:///run/containerd/containerd.sock"
export CONTAINERD_ADDRESS="/run/containerd/containerd.sock"

# Find crictl binary (may not be in default SSM PATH)
CRICTL=""
for p in /usr/local/bin/crictl /usr/bin/crictl /opt/bin/crictl $(which crictl 2>/dev/null); do
    if [ -x "$p" ]; then CRICTL="$p"; break; fi
done

# Method 1: crictl (containerd/CRI-O — standard on EKS AL2023 / 1.24+)
if [ -n "$CRICTL" ]; then
    echo "Using crictl ($CRICTL) to find pod..."
    # crictl pods --name does substring match, so filter precisely
    POD_ID=$($CRICTL pods --namespace '{pod_namespace}' -q 2>/dev/null | while read pid; do
        PNAME=$($CRICTL inspectp --output json "$pid" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',{{}}).get('metadata',{{}}).get('name',''))" 2>/dev/null || true)
        if [ "$PNAME" = "{pod_name}" ]; then echo "$pid"; break; fi
    done)
    if [ -z "$POD_ID" ]; then
        # Fallback: simple name match (works when pod name is unique enough)
        POD_ID=$($CRICTL pods --name '{pod_name}' --namespace '{pod_namespace}' -q 2>/dev/null | head -1)
    fi
    if [ -n "$POD_ID" ]; then
        echo "Found pod ID: $POD_ID"
        CONTAINER_ID=$($CRICTL ps --pod "$POD_ID" -q 2>/dev/null | head -1)
        if [ -n "$CONTAINER_ID" ]; then
            echo "Found container ID: $CONTAINER_ID"
            # Extract PID using JSON parsing — try multiple paths (containerd versions differ)
            TARGET_PID=$($CRICTL inspect --output json "$CONTAINER_ID" 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
# Try info.pid (containerd 1.x), then status.pid, then info.runtimeSpec.linux.namespaces
pid = d.get('info',{{}}).get('pid',0)
if not pid:
    pid = d.get('status',{{}}).get('pid',0)
if not pid:
    # Last resort: look for any 'pid' key recursively
    def find_pid(obj):
        if isinstance(obj, dict):
            if 'pid' in obj and isinstance(obj['pid'], int) and obj['pid'] > 0:
                return obj['pid']
            for v in obj.values():
                r = find_pid(v)
                if r: return r
        return 0
    pid = find_pid(d)
print(pid)
" 2>/dev/null || true)
            echo "crictl: pod=$POD_ID container=$CONTAINER_ID pid=$TARGET_PID"
            # Validate PID immediately; if invalid, try the pause (sandbox) container instead
            if [ -n "$TARGET_PID" ] && [ "$TARGET_PID" != "0" ] && [ ! -e "/proc/$TARGET_PID/ns/net" ]; then
                echo "WARNING: container PID $TARGET_PID has no /proc entry or ns/net — trying sandbox (pause) container..."
                SANDBOX_PID=$($CRICTL inspectp --output json "$POD_ID" 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
pid = d.get('info',{{}}).get('pid',0)
if not pid:
    pid = d.get('status',{{}}).get('pid',0)
if not pid:
    def find_pid(obj):
        if isinstance(obj, dict):
            if 'pid' in obj and isinstance(obj['pid'], int) and obj['pid'] > 0:
                return obj['pid']
            for v in obj.values():
                r = find_pid(v)
                if r: return r
        return 0
    pid = find_pid(d)
print(pid)
" 2>/dev/null || true)
                if [ -n "$SANDBOX_PID" ] && [ "$SANDBOX_PID" != "0" ] && [ -e "/proc/$SANDBOX_PID/ns/net" ]; then
                    echo "Using sandbox (pause) container PID $SANDBOX_PID instead"
                    TARGET_PID="$SANDBOX_PID"
                else
                    echo "Sandbox PID $SANDBOX_PID also invalid"
                    TARGET_PID=""
                fi
            fi
        else
            echo "crictl: pod found but no running containers in pod $POD_ID"
        fi
    else
        echo "crictl: no pod matching name='{pod_name}' namespace='{pod_namespace}'"
        echo "Available pods on this node:"
        $CRICTL pods 2>/dev/null | head -10 || true
    fi
else
    echo "crictl not found on this node"
fi

# Method 2: ctr (containerd native CLI — usually present even when crictl is not)
if [ -z "$TARGET_PID" ] || [ "$TARGET_PID" = "0" ]; then
    CTR=""
    for p in /usr/local/bin/ctr /usr/bin/ctr $(which ctr 2>/dev/null); do
        if [ -x "$p" ]; then CTR="$p"; break; fi
    done
    # Find containerd socket
    CTR_ADDR=""
    for sock in /run/containerd/containerd.sock /var/run/containerd/containerd.sock; do
        if [ -S "$sock" ]; then CTR_ADDR="$sock"; break; fi
    done
    if [ -n "$CTR" ] && [ -n "$CTR_ADDR" ]; then
        echo "Trying ctr ($CTR) with socket $CTR_ADDR to find pod container..."
        CTR_CMD="$CTR --address $CTR_ADDR"
        # containerd uses k8s.io namespace for Kubernetes containers
        # ctr containers ls does NOT show pod names — must inspect each container's labels
        # First find non-pause container, then fall back to pause (sandbox) container
        SANDBOX_CID=""
        for cid in $($CTR_CMD -n k8s.io containers ls -q 2>/dev/null); do
            INFO=$($CTR_CMD -n k8s.io containers info "$cid" 2>/dev/null || true)
            if echo "$INFO" | grep -q '"io.kubernetes.pod.name": "{pod_name}"'; then
                if echo "$INFO" | grep -q '"io.kubernetes.pod.namespace": "{pod_namespace}"'; then
                    # Check if this is a pause/sandbox container
                    if echo "$INFO" | grep -q '"io.kubernetes.cri.container-type": "sandbox"'; then
                        SANDBOX_CID="$cid"
                        echo "ctr: found sandbox container $cid (saving as fallback)"
                    else
                        echo "ctr: found app container $cid"
                        CTR_PID=$($CTR_CMD -n k8s.io task ls 2>/dev/null | grep "$cid" | awk '{{print $2}}')
                        if [ -n "$CTR_PID" ] && [ "$CTR_PID" != "0" ] && [ -e "/proc/$CTR_PID/ns/net" ]; then
                            TARGET_PID="$CTR_PID"
                            echo "ctr: resolved pid=$TARGET_PID"
                            break
                        fi
                    fi
                fi
            fi
        done
        # Fall back to sandbox (pause) container — shares the same network namespace
        if ([ -z "$TARGET_PID" ] || [ "$TARGET_PID" = "0" ]) && [ -n "$SANDBOX_CID" ]; then
            echo "ctr: using sandbox container $SANDBOX_CID"
            CTR_PID=$($CTR_CMD -n k8s.io task ls 2>/dev/null | grep "$SANDBOX_CID" | awk '{{print $2}}')
            if [ -n "$CTR_PID" ] && [ "$CTR_PID" != "0" ] && [ -e "/proc/$CTR_PID/ns/net" ]; then
                TARGET_PID="$CTR_PID"
                echo "ctr: resolved pid=$TARGET_PID via sandbox"
            fi
        fi
        [ -z "$TARGET_PID" ] && echo "ctr: could not resolve pod '{pod_name}' in namespace '{pod_namespace}'"
    elif [ -n "$CTR" ]; then
        echo "ctr found ($CTR) but no containerd socket found"
    fi
fi

# Method 3: docker (older EKS AMIs with dockershim)
if [ -z "$TARGET_PID" ] || [ "$TARGET_PID" = "0" ]; then
    if command -v docker &>/dev/null; then
        echo "Trying docker..."
        DOCKER_ID=$(docker ps --filter "label=io.kubernetes.pod.name={pod_name}" --filter "label=io.kubernetes.pod.namespace={pod_namespace}" -q 2>/dev/null | head -1)
        if [ -n "$DOCKER_ID" ]; then
            TARGET_PID=$(docker inspect --format '{{{{.State.Pid}}}}' "$DOCKER_ID" 2>/dev/null || true)
            echo "docker: container=$DOCKER_ID pid=$TARGET_PID"
        fi
    fi
fi

# Method 4: search /proc cgroups for the pod name (works with containerd/CRI-O)
if [ -z "$TARGET_PID" ] || [ "$TARGET_PID" = "0" ]; then
    echo "Trying /proc cgroup scan for pod name..."
    # Container PIDs have cgroup entries containing the pod UID or pod name
    for pid_dir in /proc/[0-9]*/cgroup; do
        pid=$(echo "$pid_dir" | cut -d/ -f3)
        if grep -q "{pod_name}" "$pid_dir" 2>/dev/null; then
            # Verify it's a container process (not a host process)
            if [ -e "/proc/$pid/ns/net" ] && [ "$(readlink /proc/$pid/ns/net)" != "$(readlink /proc/1/ns/net)" ]; then
                TARGET_PID="$pid"
                echo "cgroup scan: found pid=$TARGET_PID (cgroup matches pod name)"
                break
            fi
        fi
    done
fi

# Method 5: fallback — search /proc for pause or main container process
if [ -z "$TARGET_PID" ] || [ "$TARGET_PID" = "0" ]; then
    echo "Trying /proc process scan..."
    # Look for any process whose network namespace differs from host and whose cgroup contains pod-related strings
    for pid in $(ps -eo pid --no-headers 2>/dev/null | tr -d ' '); do
        if [ -e "/proc/$pid/ns/net" ] && [ "$(readlink /proc/$pid/ns/net 2>/dev/null)" != "$(readlink /proc/1/ns/net 2>/dev/null)" ]; then
            # Check if this PID's cmdline or environ references the pod
            if grep -q "{pod_name}" /proc/$pid/cmdline 2>/dev/null || grep -q "{pod_name}" /proc/$pid/environ 2>/dev/null; then
                TARGET_PID="$pid"
                echo "proc scan: found pid=$TARGET_PID (cmdline/environ matches)"
                break
            fi
        fi
    done
fi

if [ -z "$TARGET_PID" ] || [ "$TARGET_PID" = "0" ]; then
    echo "FATAL: Could not resolve pod {pod_namespace}/{pod_name} to a container PID on this node."
    echo "Ensure the pod is running on this specific worker node (instance {instance_id})."
    echo "Use 'kubectl get pod -n {pod_namespace} {pod_name} -o wide' to verify the node."
    echo ""
    echo "Debug info:"
    echo "  crictl binary: ${{CRICTL:-not found}}"
    echo "  ctr binary: ${{CTR:-not found}}"
    echo "  containerd socket: ${{CTR_ADDR:-$(ls -la /run/containerd/containerd.sock 2>/dev/null || ls -la /var/run/containerd/containerd.sock 2>/dev/null || echo 'not found')}}"
    echo "  docker: $(which docker 2>/dev/null || echo 'not found')"
    echo "  Running containers:"
    ${{CRICTL:-true}} ps 2>/dev/null | head -10 || ${{CTR:-true}} --address ${{CTR_ADDR:-/run/containerd/containerd.sock}} -n k8s.io task ls 2>/dev/null | head -10 || docker ps 2>/dev/null | head -10 || echo "  (no container runtime accessible)"
    exit 1
fi

echo "Resolved pod {pod_namespace}/{pod_name} -> PID $TARGET_PID"
# Validate PID: /proc/<PID>/ns/net is a SYMLINK, not a directory — use -e (exists) not -d
if [ ! -e "/proc/$TARGET_PID/ns/net" ]; then
    # Retry: PID might be a thread group leader; check if /proc/<PID> exists at all
    if [ ! -d "/proc/$TARGET_PID" ]; then
        echo "FATAL: PID $TARGET_PID does not exist in /proc (process may have exited)"
    else
        echo "FATAL: PID $TARGET_PID exists but /proc/$TARGET_PID/ns/net is missing"
        echo "  /proc/$TARGET_PID/ns contents: $(ls -la /proc/$TARGET_PID/ns/ 2>/dev/null || echo 'cannot list')"
    fi
    exit 1
fi
NSENTER_PREFIX="nsenter -n -t $TARGET_PID "
"""
    elif container_pid:
        script += f"""
# === Validate container PID {container_pid} ===
if [ ! -e "/proc/{container_pid}/ns/net" ]; then
    if [ ! -d "/proc/{container_pid}" ]; then
        echo "FATAL: PID {container_pid} does not exist in /proc (process may have exited)"
    else
        echo "FATAL: PID {container_pid} exists but /proc/{container_pid}/ns/net is missing"
        echo "  /proc/{container_pid}/ns contents: $(ls -la /proc/{container_pid}/ns/ 2>/dev/null || echo 'cannot list')"
    fi
    exit 1
fi
CONTAINER_COMM=$(cat /proc/{container_pid}/comm 2>/dev/null || echo "unknown")
echo "Targeting container process: PID {container_pid} ($CONTAINER_COMM)"
NSENTER_PREFIX="nsenter -n -t {container_pid} "
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

# Extract IPs from tcpdump output: field 3 = src (IP.port), field 5 = dst (IP.port:)
ip_port_re = re.compile(r'^(\\d+\\.\\d+\\.\\d+\\.\\d+)\\.\\d+$')
src_counter, dst_counter = Counter(), Counter()
for line in lines:
    parts = line.split()
    if len(parts) >= 5:
        # Source: field index 2 (0-based), format "IP.port"
        m = ip_port_re.match(parts[2])
        if m:
            src_counter[m.group(1)] += 1
        # Destination: field index 4, format "IP.port:" (trailing colon)
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
    # Fallback: minimal stats without top talkers if python3 not available
    TOTAL=$(tcpdump -nn -r "$PCAP_FILE" 2>/dev/null | wc -l)
    TCP_COUNT=$(tcpdump -nn -r "$PCAP_FILE" tcp 2>/dev/null | wc -l)
    UDP_COUNT=$(tcpdump -nn -r "$PCAP_FILE" udp 2>/dev/null | wc -l)
    ICMP_COUNT=$(tcpdump -nn -r "$PCAP_FILE" icmp 2>/dev/null | wc -l)
    echo '{{"totalPackets":'$TOTAL',"protocols":{{"tcp":'$TCP_COUNT',"udp":'$UDP_COUNT',"icmp":'$ICMP_COUNT'}},"topSourceIPs":{{}},"topDestinationIPs":{{}}}}' > "$STATS_FILE"
fi
if [ ! -f "$STATS_FILE" ] || [ ! -s "$STATS_FILE" ]; then
    echo '{{"error":"stats generation failed"}}' > "$STATS_FILE"
fi

# Upload all artifacts to S3 (non-fatal — node may lack S3 permissions)
# IMPORTANT: disable set -e for uploads — these are best-effort and must not kill the script
set +e
UPLOAD_FAILURES=0

echo "Uploading pcap to {s3_uri}..."
aws s3 cp "$PCAP_FILE" "{s3_uri}" --no-progress 2>&1
if [ $? -eq 0 ]; then
    echo "UPLOAD_PCAP=ok"
else
    echo "WARNING: Failed to upload pcap to S3 (node IAM role may lack s3:PutObject permission)"
    echo "UPLOAD_PCAP=failed"
    UPLOAD_FAILURES=$((UPLOAD_FAILURES + 1))
fi

echo "Uploading text summary to {s3_uri_txt}..."
aws s3 cp "$TXT_FILE" "{s3_uri_txt}" --quiet 2>&1
if [ $? -eq 0 ]; then
    echo "UPLOAD_TXT=ok"
else
    echo "WARNING: Failed to upload text summary to S3"
    echo "UPLOAD_TXT=failed"
    UPLOAD_FAILURES=$((UPLOAD_FAILURES + 1))
fi

echo "Uploading stats to {s3_uri_stats}..."
aws s3 cp "$STATS_FILE" "{s3_uri_stats}" --quiet 2>&1
if [ $? -eq 0 ]; then
    echo "UPLOAD_STATS=ok"
else
    echo "WARNING: Failed to upload stats to S3"
    echo "UPLOAD_STATS=failed"
    UPLOAD_FAILURES=$((UPLOAD_FAILURES + 1))
fi

# Do NOT re-enable set -e — the inline output section and cleanup must not kill the script
# set -e is intentionally left off for the remainder

if [ "$UPLOAD_FAILURES" -gt 0 ]; then
    echo "WARNING: $UPLOAD_FAILURES of 3 uploads failed. Ensure the node IAM role has s3:PutObject permission to {LOGS_BUCKET}."
    echo "See README — 'S3 Upload Permissions for Worker Nodes' section."
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
                'executionTimeout': [str(duration + 120)],  # extra buffer for install + upload
            },
            TimeoutSeconds=duration + 180,
            Comment=f'tcpdump capture for {instance_id} ({duration}s)',
        )

        cmd_id = response['Command']['CommandId']

        # Store region mapping for status polling
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
                    'containerPid': container_pid or None,
                    'podName': pod_name or None,
                    'podNamespace': pod_namespace if pod_name else None,
                    'startedAt': timestamp,
                    'captureScope': 'podNamespace' if (pod_name or container_pid) else 'hostNamespace',
                    'nsenterUsed': bool(pod_name or container_pid),
                    'networkNamespace': (
                        f'pod/{pod_namespace}/{pod_name}' if pod_name
                        else f'container/PID-{container_pid}' if container_pid
                        else 'host'
                    ),
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
            'containerPid': container_pid or None,
            'podName': pod_name or None,
            'podNamespace': pod_namespace if pod_name else None,
            'captureScope': 'podNamespace' if (pod_name or container_pid) else 'hostNamespace',
            'nsenterUsed': bool(pod_name or container_pid),
            'networkNamespace': (
                f'pod/{pod_namespace}/{pod_name}' if pod_name
                else f'container/PID-{container_pid}' if container_pid
                else 'host'
            ),
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
            if line.startswith('S3_KEY_TXT='):
                s3_key_txt = line.split('=', 1)[1].strip()
            if line.startswith('S3_KEY_STATS='):
                s3_key_stats = line.split('=', 1)[1].strip()
            if line.startswith('FILE_SIZE='):
                try:
                    file_size = int(line.split('=', 1)[1].strip())
                except ValueError:
                    pass
            if line.startswith('PACKET_COUNT='):
                try:
                    packet_count = int(line.split('=', 1)[1].strip())
                except ValueError:
                    pass

        # Check if capture completed but S3 upload failed (script has inline data)
        # Use multiple markers for robustness — SSM truncates StandardOutputContent at 24KB
        # so early markers like "Capture complete." may be cut if inline stats/text are large.
        #
        # IMPORTANT: These markers are ONLY printed AFTER the capture succeeds.
        # Genuine failures (tcpdump not found, pod PID not resolved, no capture file)
        # exit with "FATAL:" before any of these markers are emitted, so
        # capture_completed will correctly be False for real failures.
        capture_completed = (
            'Capture complete.' in stdout
            or 'DONE' in stdout
            or 'UPLOAD_PCAP=ok' in stdout
            or 'UPLOAD_PCAP=failed' in stdout
            or ('FILE_SIZE=' in stdout and 'S3_KEY=' in stdout)
        )
        # Double-check: if stdout contains FATAL, the capture itself failed — never treat as success
        if 'FATAL:' in stdout:
            capture_completed = False
        upload_failures = 0
        for line in stdout.split('\n'):
            if line.startswith('UPLOAD_FAILURES='):
                try:
                    upload_failures = int(line.split('=', 1)[1].strip())
                except ValueError:
                    pass

        # Extract inline stats and text from stdout (available even when S3 upload fails)
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

        # If capture completed (even if S3 upload failed), treat as success with warnings
        if status in ('Success',) or (capture_completed and status == 'Failed'):
            # Generate presigned URL for download (may fail if pcap wasn't uploaded).
            # pcap captures use a tighter expiration than ordinary log artifacts
            # because they may contain credentials in transit.
            presigned_url = ''
            try:
                presigned_url = s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': LOGS_BUCKET, 'Key': s3_key},
                    ExpiresIn=PCAP_PRESIGNED_URL_EXPIRATION,
                )
            except Exception:
                pass

            # If S3 uploads failed, store inline data to S3 from Lambda (Lambda has S3 permissions)
            if (upload_failures > 0 or (status == 'Failed' and capture_completed)) and inline_stats:
                try:
                    s3_client.put_object(
                        Bucket=LOGS_BUCKET,
                        Key=s3_key_stats,
                        Body=json.dumps(inline_stats, indent=2),
                        ContentType='application/json',
                    )
                except Exception:
                    pass
            if (upload_failures > 0 or (status == 'Failed' and capture_completed)) and inline_txt_lines:
                try:
                    s3_client.put_object(
                        Bucket=LOGS_BUCKET,
                        Key=s3_key_txt,
                        Body='\n'.join(inline_txt_lines),
                        ContentType='text/plain',
                    )
                except Exception:
                    pass

            warnings = []
            actual_failures = 0
            if upload_failures > 0 or (status == 'Failed' and capture_completed):
                actual_failures = upload_failures if upload_failures > 0 else 3  # assume all failed if script died during upload
                warnings.append(f'{actual_failures} of 3 S3 uploads failed from the node (node IAM role may lack s3:PutObject). Stats and text summary were recovered from stdout and uploaded by Lambda.')
                if actual_failures == 3:
                    warnings.append('pcap file was NOT uploaded — it was too large to inline in stdout. Add S3 PutObject permission to the node IAM role to capture pcap files.')

            # Surface a warning when the pcap exceeds the configured upload cap.
            pcap_oversized = False
            if file_size and MAX_PCAP_BYTES and file_size > MAX_PCAP_BYTES:
                pcap_oversized = True
                warnings.append(
                    f'pcap exceeds MAX_PCAP_BYTES ({format_bytes(MAX_PCAP_BYTES)}); '
                    f'capture is {format_bytes(file_size)}. Consider shorter durationSeconds '
                    f'or a tighter BPF filter on future captures.'
                )

            response_data = {
                'commandId': command_id,
                'instanceId': instance_id,
                'status': 'completed' if not warnings else 'completed_with_warnings',
                's3Key': s3_key,
                's3KeyTxt': s3_key_txt,
                's3KeyStats': s3_key_stats,
                's3Bucket': LOGS_BUCKET,
                'fileSizeBytes': file_size,
                'fileSizeHuman': format_bytes(file_size),
                'packetCount': packet_count,
                'pcapOversized': pcap_oversized,
                'pcapMaxBytes': MAX_PCAP_BYTES,
                'presignedUrl': presigned_url,
                'presignedUrlExpiresIn': f'{PCAP_PRESIGNED_URL_EXPIRATION} seconds',
                'output': stdout[-2000:] if len(stdout) > 2000 else stdout,
                'nextStep': f'Use tcpdump_analyze(instanceId="{instance_id}", commandId="{command_id}") to read decoded packet data and statistics.',
                'task': {
                    'taskId': command_id,
                    'state': 'completed',
                    'message': f'tcpdump capture completed' + (f' ({actual_failures} S3 uploads failed — recovered via Lambda)' if warnings else f' — uploaded to s3://{LOGS_BUCKET}/{s3_key}'),
                    'progress': 100,
                },
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
                'nextStep': f'Poll again in 15-30 seconds',
                'task': {
                    'taskId': command_id,
                    'state': 'running',
                    'message': f'tcpdump capture in progress ({int(elapsed)}s / {duration}s)',
                    'progress': progress,
                },
            })

        else:
            # Failed / TimedOut / Cancelled
            return error_response(500, f'tcpdump command {status}', {
                'commandId': command_id,
                'status': status,
                'stdout': stdout[-2000:] if stdout else '',
                'stderr': stderr[-2000:] if stderr else '',
                'statusDetails': result.get('StatusDetails', ''),
                'task': {
                    'taskId': command_id,
                    'state': 'failed',
                    'message': f'tcpdump command {status}: {stderr[:200] if stderr else "unknown error"}',
                    'progress': 0,
                },
            })

    except Exception as e:
        return error_response(500, f'Failed to poll tcpdump status: {str(e)}')


def _analyze_dns_packets(lines: list) -> Dict:
    """Analyze DNS queries in decoded tcpdump lines.
    Detects ndots search-domain expansion (normal), NXDomain storms, and real failures."""
    dns_re = re.compile(
        r'>\s+\S+\.53:\s+\d+\+?\s+(A|AAAA|CNAME|MX|SRV|PTR|TXT|SOA|NS)\?\s+(\S+)',
        re.IGNORECASE,
    )
    nxdomain_re = re.compile(r'NXDomain', re.IGNORECASE)
    k8s_svc_suffix = re.compile(
        r'\.svc\.cluster\.local\..*\.svc\.cluster\.local',
        re.IGNORECASE,
    )

    queries = []
    nxdomain_count = 0
    ndots_expansion_queries = []
    total_dns = 0

    for line in lines:
        m = dns_re.search(line)
        if m:
            total_dns += 1
            qtype = m.group(1).upper()
            qname = m.group(2).rstrip('.')
            queries.append({'type': qtype, 'name': qname})
            if k8s_svc_suffix.search(qname):
                ndots_expansion_queries.append(qname)
        if nxdomain_re.search(line):
            nxdomain_count += 1

    if total_dns == 0:
        return {}

    result: Dict = {
        'totalDnsQueries': total_dns,
        'nxdomainResponses': nxdomain_count,
        'anomalies': [],
    }

    if ndots_expansion_queries:
        unique = list(set(ndots_expansion_queries))[:10]
        result['ndotsSearchDomainExpansion'] = {
            'count': len(ndots_expansion_queries),
            'isNormalBehavior': True,
            'explanation': (
                'Queries with doubled Kubernetes suffixes (e.g., '
                'name.ns.svc.cluster.local.ns.svc.cluster.local) are NORMAL. '
                'With the default ndots:5, the glibc resolver appends each '
                '/etc/resolv.conf search domain to names with fewer than 5 dots '
                'before trying the name as-is. These NXDomain responses are '
                'expected and harmless. To reduce them: use a trailing dot on '
                'FQDNs, lower ndots to 2, or use short service names.'
            ),
            'exampleQueries': unique,
        }
        result['anomalies'].append({
            'type': 'ndots_search_expansion',
            'severity': 'info',
            'message': (
                f'{len(ndots_expansion_queries)} DNS queries show ndots:5 search-domain '
                f'expansion (doubled .svc.cluster.local suffixes). This is NORMAL '
                f'Kubernetes DNS behavior, not a misconfiguration. The queries get '
                f'NXDomain but the correct resolution succeeds afterward.'
            ),
        })
    elif nxdomain_count > total_dns * 0.5 and total_dns > 10:
        result['anomalies'].append({
            'type': 'high_nxdomain_rate',
            'severity': 'warning',
            'message': (
                f'{nxdomain_count} NXDomain responses out of {total_dns} DNS queries '
                f'({(nxdomain_count/total_dns)*100:.0f}%) — possible DNS misconfiguration '
                f'or queries for non-existent services.'
            ),
        })

    return result


def _analyze_tcp_rst_patterns(lines: list) -> Dict:
    """Detect TCP RST patterns that are normal in Kubernetes.
    Health probes (liveness/readiness) open a TCP connection and immediately close it,
    producing RST packets. kube-proxy DNAT race during pod termination also causes RSTs.
    These are expected and should not be flagged as connection failures.
    Ref: https://docs.aws.amazon.com/prescriptive-guidance/latest/ha-resiliency-amazon-eks-apps/probes-checks.html"""
    rst_re = re.compile(r'Flags\s+\[R\.?\]|Flags\s+\[R\]', re.IGNORECASE)
    syn_re = re.compile(r'Flags\s+\[S\]', re.IGNORECASE)
    fin_re = re.compile(r'Flags\s+\[F\.?\]', re.IGNORECASE)
    # Health probe pattern: SYN then RST within a few packets to same port, short-lived
    # Detect port 10250 (kubelet), 10256 (kube-proxy health), 15021 (istio), common probe ports
    probe_port_re = re.compile(r'\.\s*(10250|10256|10257|10259|15021|8080|8443|80|443)\s*[>:]')

    total_rst = 0
    probe_rst = 0
    short_lived_rst = 0
    rst_lines_sample = []

    # Track connections: (src, dst, port) -> packet count
    connections: Dict[str, int] = {}

    for line in lines:
        if rst_re.search(line):
            total_rst += 1
            if probe_port_re.search(line):
                probe_rst += 1
            if len(rst_lines_sample) < 5:
                rst_lines_sample.append(line.strip()[:200])

    if total_rst == 0:
        return {}

    result: Dict = {'totalRstPackets': total_rst, 'anomalies': []}

    if probe_rst > 0:
        result['healthProbeRsts'] = {
            'count': probe_rst,
            'isNormalBehavior': True,
            'explanation': (
                'TCP RST packets to kubelet (10250), kube-proxy health (10256), '
                'or common HTTP ports after very short connections are NORMAL. '
                'Kubernetes liveness/readiness probes open a TCP connection to verify '
                'the port is listening, then close it immediately — producing a RST. '
                'This is expected probe behavior, not a connection failure.'
            ),
        }
        result['anomalies'].append({
            'type': 'health_probe_rst',
            'severity': 'info',
            'isNormalBehavior': True,
            'message': (
                f'{probe_rst} TCP RST packets detected on health-check ports '
                f'(10250/10256/8080/etc). This is NORMAL Kubernetes health probe '
                f'behavior — probes open and immediately close TCP connections.'
            ),
        })

    return result


def _analyze_kube_proxy_dnat(lines: list) -> Dict:
    """Detect kube-proxy DNAT patterns where both ClusterIP and PodIP appear for the same flow.
    When a pod connects to a ClusterIP Service, kube-proxy/iptables performs DNAT to rewrite
    the destination to a backend PodIP. In tcpdump on the node, you see BOTH the original
    ClusterIP destination AND the DNATed PodIP — this looks like duplicate or phantom traffic
    but is completely normal kube-proxy behavior.
    Ref: https://docs.aws.amazon.com/eks/latest/best-practices/hybrid-nodes-app-network-traffic.html"""
    # Detect 10.x.x.x (typical ClusterIP range) and pod IPs in same capture
    cluster_ip_re = re.compile(r'\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3})\.\d+\b')
    # Look for the same source talking to two different IPs on the same port
    flow_re = re.compile(
        r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s+>\s+(\d+\.\d+\.\d+\.\d+)\.(\d+)'
    )

    src_dst_pairs: Dict[str, set] = {}  # src.port -> set of dst IPs
    for line in lines:
        m = flow_re.search(line)
        if m:
            src_ip, src_port, dst_ip, dst_port = m.groups()
            key = f"{src_ip}:{src_port}->{dst_port}"
            if key not in src_dst_pairs:
                src_dst_pairs[key] = set()
            src_dst_pairs[key].add(dst_ip)

    # Find flows where same src:port->dstPort talks to multiple dst IPs (DNAT indicator)
    dnat_flows = {k: v for k, v in src_dst_pairs.items() if len(v) > 1}

    if not dnat_flows:
        return {}

    examples = []
    for key, dsts in list(dnat_flows.items())[:5]:
        examples.append(f"{key} -> {', '.join(sorted(dsts))}")

    return {
        'dnatFlowCount': len(dnat_flows),
        'isNormalBehavior': True,
        'explanation': (
            'Flows where the same source connects to multiple destination IPs on the '
            'same port are typically kube-proxy DNAT in action. When a pod connects to '
            'a ClusterIP Service, iptables rewrites the destination to a backend pod IP. '
            'tcpdump on the node captures BOTH the pre-DNAT (ClusterIP) and post-DNAT '
            '(PodIP) packets, making it look like duplicate traffic. This is normal.'
        ),
        'exampleFlows': examples,
        'anomalies': [{
            'type': 'kube_proxy_dnat',
            'severity': 'info',
            'isNormalBehavior': True,
            'message': (
                f'{len(dnat_flows)} flows show the same source connecting to multiple '
                f'destination IPs on the same port. This is likely kube-proxy DNAT — '
                f'ClusterIP being rewritten to backend PodIP. NORMAL behavior.'
            ),
        }],
    }


def _analyze_vpc_cni_snat(lines: list) -> Dict:
    """Detect VPC CNI SNAT where pod IP is translated to node IP for external traffic.
    By default, VPC CNI SNATs pod traffic destined outside the VPC — the source IP changes
    from the pod's IP to the node's primary ENI IP. In tcpdump you see outbound packets
    with the node IP as source instead of the pod IP. This is expected.
    Ref: https://docs.aws.amazon.com/eks/latest/userguide/external-snat.html"""
    # Detect traffic to external IPs (non-RFC1918, non-cluster)
    flow_re = re.compile(
        r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s+>\s+(\d+\.\d+\.\d+\.\d+)\.(\d+)'
    )
    private_re = re.compile(r'^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)')

    external_flows = 0
    for line in lines:
        m = flow_re.search(line)
        if m:
            dst_ip = m.group(3)
            if not private_re.match(dst_ip) and not dst_ip.startswith('127.'):
                external_flows += 1

    if external_flows < 5:
        return {}

    return {
        'externalTrafficFlows': external_flows,
        'isNormalBehavior': True,
        'explanation': (
            'Traffic to external (non-RFC1918) IPs undergoes SNAT by the VPC CNI plugin. '
            'The pod source IP is translated to the node primary ENI IP before leaving '
            'the VPC. In tcpdump on the node, outbound external packets show the node IP '
            'as source, not the pod IP. This is default VPC CNI behavior '
            '(AWS_VPC_K8S_CNI_EXTERNALSNAT=false). Inbound responses are reverse-NATed '
            'back to the pod IP.'
        ),
        'anomalies': [{
            'type': 'vpc_cni_snat',
            'severity': 'info',
            'isNormalBehavior': True,
            'message': (
                f'{external_flows} packets to external IPs detected. Source IP translation '
                f'(SNAT) from pod IP to node IP is NORMAL VPC CNI behavior for traffic '
                f'leaving the VPC.'
            ),
        }],
    }


def _analyze_tcp_keepalives(lines: list) -> Dict:
    """Detect TCP keepalive packets on idle connections.
    Long-lived connections (e.g., gRPC, database pools, websockets) send periodic TCP
    keepalive probes to prevent idle timeout by NAT gateways (350s), NLBs, or conntrack.
    These appear as small packets with ack flag on established connections. Normal behavior.
    Ref: https://aws.amazon.com/blogs/networking-and-content-delivery/implementing-long-running-tcp-connections-within-vpc-networking/"""
    # Keepalives are typically: small ack-only packets, often with length 0
    keepalive_re = re.compile(r'Flags\s+\[\.?\].*length\s+0', re.IGNORECASE)
    total_keepalive_candidates = 0

    for line in lines:
        if keepalive_re.search(line) and 'ack' in line.lower():
            total_keepalive_candidates += 1

    if total_keepalive_candidates < 10:
        return {}

    return {
        'keepaliveCandidates': total_keepalive_candidates,
        'isNormalBehavior': True,
        'explanation': (
            'Zero-length ACK packets on established connections are typically TCP keepalive '
            'probes. Applications and kernels send these to prevent idle connection timeout '
            'by NAT Gateway (350s idle timeout), NLB, or conntrack table eviction. This is '
            'expected for long-lived connections like gRPC streams, database connection pools, '
            'and websockets.'
        ),
        'anomalies': [{
            'type': 'tcp_keepalive',
            'severity': 'info',
            'isNormalBehavior': True,
            'message': (
                f'{total_keepalive_candidates} zero-length ACK packets detected (likely TCP '
                f'keepalives). This is NORMAL for long-lived connections preventing idle '
                f'timeout by NAT Gateway/NLB/conntrack.'
            ),
        }],
    }


def _analyze_icmp_expected(lines: list) -> Dict:
    """Detect expected ICMP patterns in Kubernetes.
    ICMP port-unreachable during rolling updates (old pod IP, new pod not yet ready),
    ICMP fragmentation-needed for PMTUD, and ICMP redirect from VPC routing are all normal.
    Ref: https://docs.aws.amazon.com/eks/latest/best-practices/vpc-cni.html"""
    icmp_unreach_re = re.compile(r'ICMP.*unreachable', re.IGNORECASE)
    icmp_frag_re = re.compile(r'ICMP.*frag.*needed|ICMP.*too\s+big', re.IGNORECASE)
    icmp_redirect_re = re.compile(r'ICMP.*redirect', re.IGNORECASE)

    unreachable = 0
    frag_needed = 0
    redirect = 0

    for line in lines:
        if icmp_unreach_re.search(line):
            unreachable += 1
        if icmp_frag_re.search(line):
            frag_needed += 1
        if icmp_redirect_re.search(line):
            redirect += 1

    total = unreachable + frag_needed + redirect
    if total == 0:
        return {}

    result: Dict = {'anomalies': []}

    if frag_needed > 0:
        result['pmtudFragNeeded'] = {
            'count': frag_needed,
            'isNormalBehavior': True,
            'explanation': (
                'ICMP "fragmentation needed" (type 3 code 4) or "packet too big" messages '
                'are part of Path MTU Discovery (PMTUD). This is the network telling the '
                'sender to reduce packet size. Normal for VPC traffic crossing different MTU '
                'boundaries (e.g., 9001 jumbo frames to 1500 standard).'
            ),
        }
        result['anomalies'].append({
            'type': 'pmtud_frag_needed',
            'severity': 'info',
            'isNormalBehavior': True,
            'message': (
                f'{frag_needed} ICMP fragmentation-needed packets detected. This is NORMAL '
                f'Path MTU Discovery behavior.'
            ),
        })

    if unreachable > 0 and unreachable < 20:
        result['icmpUnreachable'] = {
            'count': unreachable,
            'isNormalBehavior': True,
            'explanation': (
                'A small number of ICMP port/host unreachable messages is normal during '
                'rolling updates, pod termination, or when UDP services are briefly '
                'unavailable. The VPC CNI 30-second IP cooldown cache means old pod IPs '
                'may receive traffic briefly after pod deletion.'
            ),
        }
        result['anomalies'].append({
            'type': 'icmp_unreachable_transient',
            'severity': 'info',
            'isNormalBehavior': True,
            'message': (
                f'{unreachable} ICMP unreachable packets detected. Small numbers are NORMAL '
                f'during rolling updates or pod termination (VPC CNI 30s IP cooldown).'
            ),
        })
    elif unreachable >= 20:
        result['anomalies'].append({
            'type': 'icmp_unreachable_high',
            'severity': 'warning',
            'isNormalBehavior': False,
            'message': (
                f'{unreachable} ICMP unreachable packets detected — this is higher than '
                f'expected for normal rolling updates. Investigate for misconfigured '
                f'services, missing endpoints, or network policy blocks.'
            ),
        })

    return result


def _analyze_coredns_transients(lines: list) -> Dict:
    """Detect brief DNS failures that occur during CoreDNS scaling events.
    When CoreDNS pods scale down, there is a propagation delay for kube-proxy to update
    iptables rules. During this window, DNS queries may be sent to a terminating CoreDNS pod
    and get SERVFAIL or timeout. Setting lameduck duration in CoreDNS mitigates this.
    Ref: https://docs.aws.amazon.com/eks/latest/best-practices/scale-cluster-services.html"""
    servfail_re = re.compile(r'SERVFAIL|ServFail', re.IGNORECASE)
    dns_timeout_re = re.compile(r'>\s+\S+\.53:.*\[.*\].*no\s+response', re.IGNORECASE)

    servfail_count = 0
    for line in lines:
        if servfail_re.search(line):
            servfail_count += 1

    if servfail_count == 0:
        return {}

    if servfail_count < 10:
        return {
            'servfailCount': servfail_count,
            'isNormalBehavior': True,
            'explanation': (
                'A small number of SERVFAIL responses can occur during CoreDNS pod scaling '
                'events. When a CoreDNS pod terminates, there is a brief window where '
                'kube-proxy iptables rules still route DNS queries to the terminating pod. '
                'The CoreDNS lameduck plugin mitigates this by delaying shutdown. A few '
                'SERVFAILs during scaling are transient and self-resolving.'
            ),
            'anomalies': [{
                'type': 'coredns_scaling_transient',
                'severity': 'info',
                'isNormalBehavior': True,
                'message': (
                    f'{servfail_count} DNS SERVFAIL responses detected. Small numbers are '
                    f'NORMAL during CoreDNS scaling events (lameduck propagation delay).'
                ),
            }],
        }
    else:
        return {
            'servfailCount': servfail_count,
            'anomalies': [{
                'type': 'high_servfail_rate',
                'severity': 'warning',
                'isNormalBehavior': False,
                'message': (
                    f'{servfail_count} DNS SERVFAIL responses detected — this exceeds '
                    f'normal CoreDNS scaling transients. Investigate CoreDNS health, '
                    f'resource limits, and upstream DNS connectivity.'
                ),
            }],
        }


def _analyze_syn_flood(lines: list) -> Dict:
    """Detect SYN flood / connection flood patterns.
    A high ratio of SYN packets without corresponding SYN-ACK indicates either a SYN flood
    attack or an overwhelmed service that can't accept connections fast enough."""
    syn_re = re.compile(r'Flags\s+\[S\]', re.IGNORECASE)
    synack_re = re.compile(r'Flags\s+\[S\.\]', re.IGNORECASE)
    flow_re = re.compile(r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s+>\s+(\d+\.\d+\.\d+\.\d+)\.(\d+)')

    syn_count = 0
    synack_count = 0
    syn_sources: Dict[str, int] = {}
    syn_targets: Dict[str, int] = {}

    for line in lines:
        if synack_re.search(line):
            synack_count += 1
        elif syn_re.search(line):
            syn_count += 1
            m = flow_re.search(line)
            if m:
                src_ip = m.group(1)
                dst = f"{m.group(3)}:{m.group(4)}"
                syn_sources[src_ip] = syn_sources.get(src_ip, 0) + 1
                syn_targets[dst] = syn_targets.get(dst, 0) + 1

    if syn_count < 20:
        return {}

    result: Dict = {
        'synCount': syn_count,
        'synAckCount': synack_count,
        'anomalies': [],
    }

    # Half-open ratio: SYN without SYN-ACK
    if synack_count > 0:
        half_open_ratio = (syn_count - synack_count) / syn_count
    else:
        half_open_ratio = 1.0 if syn_count > 0 else 0

    top_sources = sorted(syn_sources.items(), key=lambda x: x[1], reverse=True)[:5]
    top_targets = sorted(syn_targets.items(), key=lambda x: x[1], reverse=True)[:5]
    result['topSynSources'] = [{'ip': ip, 'count': c} for ip, c in top_sources]
    result['topSynTargets'] = [{'target': t, 'count': c} for t, c in top_targets]

    if half_open_ratio > 0.7 and syn_count > 50:
        result['anomalies'].append({
            'type': 'syn_flood',
            'severity': 'critical',
            'message': (
                f'{syn_count} SYN packets but only {synack_count} SYN-ACK responses '
                f'({half_open_ratio*100:.0f}% unanswered). Possible SYN flood attack or '
                f'target service is overwhelmed/unreachable. Top source: '
                f'{top_sources[0][0]} ({top_sources[0][1]} SYNs).'
            ),
        })
    elif half_open_ratio > 0.4:
        result['anomalies'].append({
            'type': 'connection_pressure',
            'severity': 'warning',
            'message': (
                f'{syn_count} SYN packets with {synack_count} SYN-ACK responses '
                f'({half_open_ratio*100:.0f}% unanswered). Service may be under connection '
                f'pressure or accept queue is full (check net.core.somaxconn).'
            ),
        })
    elif syn_count > 200:
        result['anomalies'].append({
            'type': 'high_connection_rate',
            'severity': 'info',
            'message': (
                f'{syn_count} new TCP connections in capture window. High but connections '
                f'are being accepted ({synack_count} SYN-ACKs). Monitor for scaling needs.'
            ),
        })

    return result


def _analyze_tcp_window_zero(lines: list) -> Dict:
    """Detect TCP window zero events indicating receiver backpressure.
    When a pod's receive buffer is full, it advertises window size 0, telling the sender
    to stop. This indicates the application can't consume data fast enough — common with
    overwhelmed services, slow consumers, or memory pressure."""
    win_zero_re = re.compile(r'win\s+0\b', re.IGNORECASE)
    flow_re = re.compile(r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s+>\s+(\d+\.\d+\.\d+\.\d+)\.(\d+)')

    zero_window_count = 0
    affected_flows: Dict[str, int] = {}

    for line in lines:
        if win_zero_re.search(line):
            zero_window_count += 1
            m = flow_re.search(line)
            if m:
                flow = f"{m.group(1)}:{m.group(2)}->{m.group(3)}:{m.group(4)}"
                affected_flows[flow] = affected_flows.get(flow, 0) + 1

    if zero_window_count == 0:
        return {}

    top_flows = sorted(affected_flows.items(), key=lambda x: x[1], reverse=True)[:5]

    result: Dict = {
        'zeroWindowCount': zero_window_count,
        'affectedFlows': len(affected_flows),
        'topAffectedFlows': [{'flow': f, 'count': c} for f, c in top_flows],
        'anomalies': [],
    }

    if zero_window_count > 20:
        result['anomalies'].append({
            'type': 'tcp_window_zero_critical',
            'severity': 'critical',
            'message': (
                f'{zero_window_count} TCP zero-window events across {len(affected_flows)} '
                f'flows. Receiver cannot consume data fast enough — application is overwhelmed. '
                f'Check pod memory limits, application processing capacity, and consider '
                f'horizontal scaling. Most affected: {top_flows[0][0]} ({top_flows[0][1]}x).'
            ),
        })
    elif zero_window_count > 5:
        result['anomalies'].append({
            'type': 'tcp_window_zero_warning',
            'severity': 'warning',
            'message': (
                f'{zero_window_count} TCP zero-window events detected. Receiver is '
                f'experiencing backpressure — may indicate slow application processing '
                f'or insufficient memory for socket buffers.'
            ),
        })

    return result


def _analyze_retransmissions(lines: list) -> Dict:
    """Detect TCP retransmission patterns indicating packet loss or network congestion.
    Retransmissions show up as duplicate sequence numbers. High retransmission rates
    indicate packet loss (security group drops, NACL drops, ENA throttling, or congestion)."""
    retrans_re = re.compile(r'retransmit|retrans', re.IGNORECASE)
    dup_ack_re = re.compile(r'dup\s+ack|duplicate\s+ack', re.IGNORECASE)
    flow_re = re.compile(r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s+>\s+(\d+\.\d+\.\d+\.\d+)\.(\d+)')

    retrans_count = 0
    dup_ack_count = 0
    retrans_flows: Dict[str, int] = {}

    # Also detect retransmissions by looking for repeated seq numbers
    seq_re = re.compile(r'seq\s+(\d+)[:\s]')
    seen_seqs: Dict[str, set] = {}  # flow -> set of seq numbers

    for line in lines:
        if retrans_re.search(line):
            retrans_count += 1
            m = flow_re.search(line)
            if m:
                flow = f"{m.group(1)}->{m.group(3)}:{m.group(4)}"
                retrans_flows[flow] = retrans_flows.get(flow, 0) + 1
        if dup_ack_re.search(line):
            dup_ack_count += 1

        # Track seq numbers per flow for duplicate detection
        m_flow = flow_re.search(line)
        m_seq = seq_re.search(line)
        if m_flow and m_seq:
            flow_key = f"{m_flow.group(1)}->{m_flow.group(3)}:{m_flow.group(4)}"
            seq_num = m_seq.group(1)
            if flow_key not in seen_seqs:
                seen_seqs[flow_key] = set()
            if seq_num in seen_seqs[flow_key]:
                retrans_count += 1
                retrans_flows[flow_key] = retrans_flows.get(flow_key, 0) + 1
            seen_seqs[flow_key].add(seq_num)

    if retrans_count == 0 and dup_ack_count == 0:
        return {}

    top_flows = sorted(retrans_flows.items(), key=lambda x: x[1], reverse=True)[:5]

    result: Dict = {
        'retransmissionCount': retrans_count,
        'duplicateAckCount': dup_ack_count,
        'affectedFlows': len(retrans_flows),
        'topRetransmitFlows': [{'flow': f, 'count': c} for f, c in top_flows],
        'anomalies': [],
    }

    total_packets = len(lines)
    retrans_pct = (retrans_count / total_packets * 100) if total_packets > 0 else 0

    if retrans_pct > 5:
        result['anomalies'].append({
            'type': 'high_retransmission_rate',
            'severity': 'critical',
            'message': (
                f'{retrans_count} retransmissions ({retrans_pct:.1f}% of packets). '
                f'Severe packet loss — check ENA throttling (linklocal_allowance_exceeded), '
                f'security group/NACL drops, or network congestion. '
                f'{len(retrans_flows)} flows affected.'
            ),
        })
    elif retrans_pct > 1:
        result['anomalies'].append({
            'type': 'moderate_retransmission_rate',
            'severity': 'warning',
            'message': (
                f'{retrans_count} retransmissions ({retrans_pct:.1f}% of packets). '
                f'Moderate packet loss detected. Check for ENA bandwidth/PPS throttling '
                f'or intermittent network issues.'
            ),
        })
    elif retrans_count > 0:
        result['anomalies'].append({
            'type': 'low_retransmissions',
            'severity': 'info',
            'message': (
                f'{retrans_count} retransmissions detected ({retrans_pct:.1f}%). '
                f'Low level — within normal range for most workloads.'
            ),
        })

    return result


def _analyze_connection_refused(lines: list) -> Dict:
    """Detect connection refused patterns (RST immediately after SYN).
    This indicates the target port is not listening — common when a pod hasn't started,
    a service endpoint is stale, or a NetworkPolicy is blocking traffic."""
    flow_re = re.compile(r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s+>\s+(\d+\.\d+\.\d+\.\d+)\.(\d+)')
    syn_re = re.compile(r'Flags\s+\[S\]', re.IGNORECASE)
    rst_re = re.compile(r'Flags\s+\[R\.?\]', re.IGNORECASE)

    # Track SYN -> RST pairs (connection refused = RST right after SYN)
    recent_syns: Dict[str, str] = {}  # "dst:port" -> src line
    refused: Dict[str, int] = {}  # "dst:port" -> count

    for line in lines:
        m = flow_re.search(line)
        if not m:
            continue
        src_ip, src_port, dst_ip, dst_port = m.groups()

        if syn_re.search(line):
            key = f"{dst_ip}:{dst_port}"
            recent_syns[key] = src_ip
        elif rst_re.search(line):
            # RST coming FROM the destination back to source
            reverse_key = f"{src_ip}:{src_port}"
            if reverse_key in recent_syns:
                refused[reverse_key] = refused.get(reverse_key, 0) + 1

    if not refused:
        return {}

    top_refused = sorted(refused.items(), key=lambda x: x[1], reverse=True)[:10]
    total_refused = sum(refused.values())

    result: Dict = {
        'totalConnectionRefused': total_refused,
        'uniqueTargets': len(refused),
        'topRefusedTargets': [{'target': t, 'count': c} for t, c in top_refused],
        'anomalies': [],
    }

    if total_refused > 50:
        result['anomalies'].append({
            'type': 'mass_connection_refused',
            'severity': 'critical',
            'message': (
                f'{total_refused} connections refused across {len(refused)} targets. '
                f'Services are not listening or pods are not ready. Top target: '
                f'{top_refused[0][0]} ({top_refused[0][1]}x refused). Check pod readiness, '
                f'service endpoints, and NetworkPolicy rules.'
            ),
        })
    elif total_refused > 10:
        result['anomalies'].append({
            'type': 'connection_refused',
            'severity': 'warning',
            'message': (
                f'{total_refused} connections refused to {len(refused)} targets. '
                f'Some services may not be ready or endpoints are stale.'
            ),
        })

    return result


def _analyze_traffic_burst(lines: list) -> Dict:
    """Detect traffic bursts by analyzing packet timestamps.
    Identifies periods of abnormally high packet rates that could indicate
    DDoS, thundering herd, or misconfigured retry storms."""
    ts_re = re.compile(r'^(\d{2}:\d{2}:\d{2}\.\d+)\s')

    # Group packets by second
    packets_per_second: Dict[str, int] = {}
    for line in lines:
        m = ts_re.match(line)
        if m:
            ts = m.group(1).split('.')[0]  # truncate to second
            packets_per_second[ts] = packets_per_second.get(ts, 0) + 1

    if len(packets_per_second) < 5:
        return {}

    rates = list(packets_per_second.values())
    avg_rate = sum(rates) / len(rates)
    max_rate = max(rates)
    max_ts = max(packets_per_second, key=packets_per_second.get)

    # Find burst periods (>3x average)
    burst_seconds = [(ts, count) for ts, count in packets_per_second.items()
                     if count > avg_rate * 3 and count > 20]
    burst_seconds.sort(key=lambda x: x[1], reverse=True)

    result: Dict = {
        'avgPacketsPerSecond': round(avg_rate, 1),
        'maxPacketsPerSecond': max_rate,
        'peakTime': max_ts,
        'captureDurationSeconds': len(packets_per_second),
        'anomalies': [],
    }

    if burst_seconds:
        result['burstPeriods'] = [{'time': ts, 'packetsPerSecond': c}
                                  for ts, c in burst_seconds[:10]]
        if max_rate > avg_rate * 10 and max_rate > 100:
            result['anomalies'].append({
                'type': 'extreme_traffic_burst',
                'severity': 'critical',
                'message': (
                    f'Extreme traffic burst: {max_rate} pps at {max_ts} vs average '
                    f'{avg_rate:.0f} pps ({max_rate/avg_rate:.0f}x spike). '
                    f'{len(burst_seconds)} burst periods detected. Possible DDoS, '
                    f'retry storm, or thundering herd.'
                ),
            })
        elif burst_seconds:
            result['anomalies'].append({
                'type': 'traffic_burst',
                'severity': 'warning',
                'message': (
                    f'Traffic bursts detected: peak {max_rate} pps at {max_ts} vs '
                    f'average {avg_rate:.0f} pps. {len(burst_seconds)} periods exceeded '
                    f'3x average rate.'
                ),
            })

    return result


def _analyze_top_talkers(lines: list) -> Dict:
    """Identify top bandwidth consumers and communication patterns.
    Helps identify which pods/IPs are generating the most traffic and whether
    traffic distribution is skewed (one pod hogging bandwidth)."""
    flow_re = re.compile(
        r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s+>\s+(\d+\.\d+\.\d+\.\d+)\.(\d+).*length\s+(\d+)'
    )

    src_bytes: Dict[str, int] = {}
    dst_bytes: Dict[str, int] = {}
    src_packets: Dict[str, int] = {}
    dst_packets: Dict[str, int] = {}
    flow_bytes: Dict[str, int] = {}

    for line in lines:
        m = flow_re.search(line)
        if m:
            src_ip, src_port, dst_ip, dst_port, length = m.groups()
            length = int(length)
            src_bytes[src_ip] = src_bytes.get(src_ip, 0) + length
            dst_bytes[dst_ip] = dst_bytes.get(dst_ip, 0) + length
            src_packets[src_ip] = src_packets.get(src_ip, 0) + 1
            dst_packets[dst_ip] = dst_packets.get(dst_ip, 0) + 1
            flow_key = f"{src_ip}->{dst_ip}:{dst_port}"
            flow_bytes[flow_key] = flow_bytes.get(flow_key, 0) + length

    if not src_bytes:
        return {}

    top_src = sorted(src_bytes.items(), key=lambda x: x[1], reverse=True)[:10]
    top_dst = sorted(dst_bytes.items(), key=lambda x: x[1], reverse=True)[:10]
    top_flows = sorted(flow_bytes.items(), key=lambda x: x[1], reverse=True)[:10]

    total_bytes = sum(src_bytes.values())

    result: Dict = {
        'totalBytes': total_bytes,
        'totalBytesHuman': f'{total_bytes/1024:.1f} KB' if total_bytes < 1048576 else f'{total_bytes/1048576:.1f} MB',
        'uniqueSources': len(src_bytes),
        'uniqueDestinations': len(dst_bytes),
        'topSenders': [{'ip': ip, 'bytes': b, 'packets': src_packets.get(ip, 0)}
                       for ip, b in top_src],
        'topReceivers': [{'ip': ip, 'bytes': b, 'packets': dst_packets.get(ip, 0)}
                         for ip, b in top_dst],
        'topFlows': [{'flow': f, 'bytes': b} for f, b in top_flows],
        'anomalies': [],
    }

    # Check for traffic skew — one source dominating
    if top_src and total_bytes > 0:
        top_pct = (top_src[0][1] / total_bytes) * 100
        if top_pct > 80 and len(src_bytes) > 3:
            result['anomalies'].append({
                'type': 'traffic_skew',
                'severity': 'warning',
                'message': (
                    f'Traffic heavily skewed: {top_src[0][0]} sends {top_pct:.0f}% of all '
                    f'bytes ({top_src[0][1]} bytes). Possible bandwidth hog or '
                    f'misconfigured client retry loop.'
                ),
            })

    return result


def _analyze_mtu_fragmentation(lines: list) -> Dict:
    """Detect MTU/fragmentation issues from packet captures.
    Fragmented packets indicate MTU mismatch. In EKS, the VPC MTU is typically 9001
    (jumbo frames) but tunnels (VPN, VXLAN) or cross-AZ traffic may have lower MTU.
    Excessive fragmentation causes performance degradation and can break PMTUD."""
    frag_re = re.compile(r'frag\s+\d+|offset\s+\d+|flags\s+\[.*MF.*\]', re.IGNORECASE)
    df_re = re.compile(r'flags\s+\[.*DF.*\]', re.IGNORECASE)
    length_re = re.compile(r'length\s+(\d+)')

    frag_count = 0
    df_count = 0
    large_packets = 0  # packets > 1500 bytes (jumbo)

    for line in lines:
        if frag_re.search(line):
            frag_count += 1
        if df_re.search(line):
            df_count += 1
        m = length_re.search(line)
        if m and int(m.group(1)) > 1500:
            large_packets += 1

    if frag_count == 0 and large_packets == 0:
        return {}

    result: Dict = {
        'fragmentedPackets': frag_count,
        'dontFragmentPackets': df_count,
        'jumboPackets': large_packets,
        'anomalies': [],
    }

    if frag_count > 20:
        result['anomalies'].append({
            'type': 'excessive_fragmentation',
            'severity': 'warning',
            'message': (
                f'{frag_count} fragmented packets detected. MTU mismatch likely — '
                f'check if traffic crosses VPN tunnels, VXLAN overlays, or different '
                f'MTU boundaries. Consider setting pod MTU explicitly or enabling PMTUD. '
                f'EKS VPC default MTU is 9001 (jumbo frames).'
            ),
        })
    elif frag_count > 0:
        result['anomalies'].append({
            'type': 'minor_fragmentation',
            'severity': 'info',
            'message': (
                f'{frag_count} fragmented packets. Low level — may be normal for '
                f'cross-region or VPN traffic.'
            ),
        })

    return result


def _analyze_conntrack_pressure(lines: list) -> Dict:
    """Estimate conntrack table pressure from unique connection count.
    Each TCP/UDP flow consumes a conntrack entry. Default nf_conntrack_max is 131072.
    High unique flow counts in a short capture window suggest conntrack exhaustion risk."""
    flow_re = re.compile(
        r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s+>\s+(\d+\.\d+\.\d+\.\d+)\.(\d+)'
    )

    unique_flows = set()
    for line in lines:
        m = flow_re.search(line)
        if m:
            # Bidirectional: normalize so A->B and B->A count as one flow
            src = f"{m.group(1)}:{m.group(2)}"
            dst = f"{m.group(3)}:{m.group(4)}"
            flow = tuple(sorted([src, dst]))
            unique_flows.add(flow)

    if len(unique_flows) < 100:
        return {}

    result: Dict = {
        'uniqueFlows': len(unique_flows),
        'anomalies': [],
    }

    # Default conntrack max is 131072; warn at 50% observed in a short window
    if len(unique_flows) > 50000:
        result['anomalies'].append({
            'type': 'conntrack_exhaustion_risk',
            'severity': 'critical',
            'message': (
                f'{len(unique_flows)} unique flows observed in capture window. '
                f'Default nf_conntrack_max is 131072 — node may be at risk of '
                f'conntrack table exhaustion. Check: '
                f'cat /proc/sys/net/netfilter/nf_conntrack_count vs nf_conntrack_max. '
                f'Symptoms: "nf_conntrack: table full, dropping packet" in dmesg.'
            ),
        })
    elif len(unique_flows) > 10000:
        result['anomalies'].append({
            'type': 'high_flow_count',
            'severity': 'warning',
            'message': (
                f'{len(unique_flows)} unique flows in capture window. Monitor conntrack '
                f'usage — high flow counts can exhaust the conntrack table '
                f'(default max 131072).'
            ),
        })
    else:
        result['anomalies'].append({
            'type': 'flow_count_info',
            'severity': 'info',
            'message': f'{len(unique_flows)} unique flows observed. Within normal range.',
        })

    return result


def tcpdump_analyze(arguments: Dict) -> Dict:
    """
    Read and analyze a completed tcpdump capture from S3.
    Returns decoded packet text, protocol statistics, and top talkers.


    Inputs:
        instanceId: EC2 instance ID (required)
        commandId: SSM Command ID from tcpdump_capture (optional — finds latest if omitted)
        section: "summary" (first N packets decoded), "stats" (protocol breakdown), "all" (default: "all")
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

    # ── Always find the LATEST capture for this instance ──
    # Even if commandId is provided, we verify it is the latest capture.
    # This prevents analyzing stale data when a newer capture exists.
    metadata = {}
    latest_metadata = {}
    try:
        list_resp = safe_s3_list(f"tcpdump-commands/", max_keys=200)
        if list_resp.get('success'):
            candidates = []
            for obj in list_resp.get('objects', []):
                try:
                    r = s3_client.get_object(Bucket=LOGS_BUCKET, Key=obj['key'])
                    m = json.loads(r['Body'].read().decode('utf-8'))
                    if m.get('instanceId') == instance_id:
                        candidates.append(m)
                except Exception:
                    continue
            if candidates:
                candidates.sort(key=lambda x: x.get('startedAt', ''), reverse=True)
                latest_metadata = candidates[0]
    except Exception:
        pass

    if command_id and latest_metadata:
        # commandId was provided — check if it matches the latest
        if latest_metadata.get('commandId') == command_id:
            metadata = latest_metadata
        else:
            # Requested commandId is NOT the latest — reject with guidance
            return error_response(409,
                f'commandId {command_id} is not the latest capture for {instance_id}. '
                f'Latest capture is commandId={latest_metadata.get("commandId")} '
                f'started at {latest_metadata.get("startedAt", "unknown")}. '
                f'Omit commandId to auto-use the latest, or run a new tcpdump_capture.')
    elif latest_metadata:
        metadata = latest_metadata
    elif command_id:
        # No candidates found at all — try the specific commandId as fallback
        try:
            meta_resp = s3_client.get_object(
                Bucket=LOGS_BUCKET,
                Key=f"tcpdump-commands/{command_id}.json",
            )
            metadata = json.loads(meta_resp['Body'].read().decode('utf-8'))
        except Exception:
            pass

    if not metadata:
        return error_response(404, f'No tcpdump capture found for {instance_id}. Run tcpdump_capture first.')

    # ── Staleness check ──
    capture_age_warning = None
    started_at = metadata.get('startedAt', '')
    if started_at:
        try:
            capture_time = datetime.strptime(started_at, '%Y-%m-%dT%H:%M:%SZ')
            age_seconds = (datetime.utcnow() - capture_time).total_seconds()
            age_minutes = age_seconds / 60
            if age_minutes > 15:
                capture_age_warning = (
                    f'This capture is {int(age_minutes)} minutes old (started {started_at}). '
                    f'Network conditions may have changed. Consider running a fresh tcpdump_capture.'
                )
        except (ValueError, TypeError):
            pass

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
            'captureScope': metadata.get('captureScope', 'hostNamespace'),
            'nsenterUsed': metadata.get('nsenterUsed', False),
            'networkNamespace': metadata.get('networkNamespace', 'host'),
            'podName': metadata.get('podName'),
            'podNamespace': metadata.get('podNamespace'),
            'containerPid': metadata.get('containerPid'),
        },
    }
    if capture_age_warning:
        results['stalenessWarning'] = capture_age_warning

    # Read stats
    if section in ('stats', 'all'):
        stats = {}
        if s3_key_stats:
            try:
                resp = safe_s3_read(s3_key_stats, max_size=65536)
                if resp.get('success') and resp.get('content'):
                    stats = json.loads(resp['content'])
            except (json.JSONDecodeError, Exception):
                stats = {'error': 'Could not parse stats JSON'}
        else:
            stats = {'error': 'No stats file found — capture may still be in progress'}

        results['statistics'] = stats

        # Anomaly detection from stats
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
                        'message': f'{retrans} possible retransmissions detected ({retrans_pct:.1f}%) — network congestion or packet loss',
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
    # NOTE: all_lines holds the FULL packet set for analysis; decoded_lines is truncated for response payload
    all_lines = []
    if section in ('summary', 'all'):
        decoded_lines = []
        if s3_key_txt:
            try:
                resp = safe_s3_read(s3_key_txt, max_size=2 * 1024 * 1024)  # 2MB max
                if resp.get('success') and resp.get('content'):
                    all_lines = resp['content'].split('\n')

                    # Apply text filter if provided (only for display, not analysis)
                    display_lines = all_lines
                    if text_filter:
                        pattern = re.compile(re.escape(text_filter), re.IGNORECASE)
                        display_lines = [l for l in all_lines if pattern.search(l)]

                    total_lines = len(display_lines)
                    decoded_lines = display_lines[:max_packets]

                    results['decodedPackets'] = {
                        'lines': decoded_lines,
                        'totalPackets': total_lines,
                        'returnedPackets': len(decoded_lines),
                        'truncated': total_lines > max_packets,
                        'filter': text_filter or 'none',
                        'analyzedPackets': len(all_lines),
                    }
                else:
                    results['decodedPackets'] = {'error': 'Text summary file is empty or unreadable'}
            except Exception as e:
                results['decodedPackets'] = {'error': f'Failed to read text summary: {str(e)}'}
        else:
            results['decodedPackets'] = {'error': 'No text summary file found — capture may still be in progress'}

    # DNS analysis — scan ALL packets (not just truncated display lines)
    if section in ('summary', 'all') and all_lines:
        dns_analysis = _analyze_dns_packets(all_lines)
        if dns_analysis:
            results['dnsAnalysis'] = dns_analysis
            # Merge DNS anomalies into the main anomalies list
            if 'anomalies' in results:
                results['anomalies'].extend(dns_analysis.get('anomalies', []))
            else:
                results['anomalies'] = dns_analysis.get('anomalies', [])

    # Expected behavior analysis — run on ALL lines for full coverage
    # Each analyzer is independent and returns its own findings
    if section in ('summary', 'all') and all_lines:
        expected_behaviors = {}
        expected_anomalies = []

        tcp_rst = _analyze_tcp_rst_patterns(all_lines)
        if tcp_rst:
            expected_behaviors['tcpRstPatterns'] = tcp_rst
            expected_anomalies.extend(tcp_rst.get('anomalies', []))

        dnat = _analyze_kube_proxy_dnat(all_lines)
        if dnat:
            expected_behaviors['kubeProxyDnat'] = dnat
            expected_anomalies.extend(dnat.get('anomalies', []))

        snat = _analyze_vpc_cni_snat(all_lines)
        if snat:
            expected_behaviors['vpcCniSnat'] = snat
            expected_anomalies.extend(snat.get('anomalies', []))

        keepalive = _analyze_tcp_keepalives(all_lines)
        if keepalive:
            expected_behaviors['tcpKeepalives'] = keepalive
            expected_anomalies.extend(keepalive.get('anomalies', []))

        icmp = _analyze_icmp_expected(all_lines)
        if icmp:
            expected_behaviors['icmpPatterns'] = icmp
            expected_anomalies.extend(icmp.get('anomalies', []))

        coredns = _analyze_coredns_transients(all_lines)
        if coredns:
            expected_behaviors['corednsTransients'] = coredns
            expected_anomalies.extend(coredns.get('anomalies', []))

        # ── Deep network analysis (complex issue detection) ──
        syn_flood = _analyze_syn_flood(all_lines)
        if syn_flood:
            expected_behaviors['synFloodDetection'] = syn_flood
            expected_anomalies.extend(syn_flood.get('anomalies', []))

        win_zero = _analyze_tcp_window_zero(all_lines)
        if win_zero:
            expected_behaviors['tcpWindowZero'] = win_zero
            expected_anomalies.extend(win_zero.get('anomalies', []))

        retrans = _analyze_retransmissions(all_lines)
        if retrans:
            expected_behaviors['retransmissions'] = retrans
            expected_anomalies.extend(retrans.get('anomalies', []))

        conn_refused = _analyze_connection_refused(all_lines)
        if conn_refused:
            expected_behaviors['connectionRefused'] = conn_refused
            expected_anomalies.extend(conn_refused.get('anomalies', []))

        burst = _analyze_traffic_burst(all_lines)
        if burst:
            expected_behaviors['trafficBurst'] = burst
            expected_anomalies.extend(burst.get('anomalies', []))

        talkers = _analyze_top_talkers(all_lines)
        if talkers:
            expected_behaviors['topTalkers'] = talkers
            expected_anomalies.extend(talkers.get('anomalies', []))

        mtu_frag = _analyze_mtu_fragmentation(all_lines)
        if mtu_frag:
            expected_behaviors['mtuFragmentation'] = mtu_frag
            expected_anomalies.extend(mtu_frag.get('anomalies', []))

        conntrack = _analyze_conntrack_pressure(all_lines)
        if conntrack:
            expected_behaviors['conntrackPressure'] = conntrack
            expected_anomalies.extend(conntrack.get('anomalies', []))

        if expected_behaviors:
            results['expectedBehaviors'] = expected_behaviors
            if 'anomalies' in results:
                results['anomalies'].extend(expected_anomalies)
            else:
                results['anomalies'] = expected_anomalies

    # Presigned URL for pcap download
    if s3_key_pcap:
        try:
            results['pcapDownloadUrl'] = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': LOGS_BUCKET, 'Key': s3_key_pcap},
                ExpiresIn=PCAP_PRESIGNED_URL_EXPIRATION,
            )
            results['pcapDownloadUrlExpiresIn'] = f'{PCAP_PRESIGNED_URL_EXPIRATION} seconds'
        except Exception:
            pass

    results['s3Bucket'] = LOGS_BUCKET
    results['s3KeyPcap'] = s3_key_pcap
    results['s3KeyTxt'] = s3_key_txt
    results['s3KeyStats'] = s3_key_stats

    return success_response(results)
