"""Focused AppSec parity tests for deployment scope and data isolation."""
import json
import re
import time
from pathlib import Path

import pytest

from .test_tcpdump_security import INSTANCE, TASK, body, mod

OTHER = 'i-0aaaaaaaaaaaaaaaa'


def test_cluster_allowlist_fails_closed(monkeypatch):
    monkeypatch.setattr(mod, 'ALLOWED_CLUSTER_NAMES', frozenset())
    assert mod.validate_cluster_name('cluster')['statusCode'] == 503


def test_cluster_allowlist_rejects_unlisted(monkeypatch):
    monkeypatch.setattr(mod, 'ALLOWED_CLUSTER_NAMES', frozenset({'cluster'}))
    assert mod.validate_cluster_name('other')['statusCode'] == 403
    assert mod.validate_cluster_name('cluster') is None


@pytest.mark.parametrize('call', [
    lambda: mod.cluster_health_check({'clusterName': 'cluster', 'region': 'eu-west-1'}),
    lambda: mod.batch_collect({'clusterName': 'cluster', 'region': 'eu-west-1'}),
    lambda: mod.list_collection_history({'region': 'eu-west-1'}),
    lambda: mod.network_diagnostics({
        'instanceId': INSTANCE, 'sections': 'eni', 'region': 'eu-west-1',
    }),
])
def test_public_handlers_reject_disallowed_region(call):
    assert call()['statusCode'] == 403


@pytest.mark.parametrize('key,status', [
    (f'ecs_{INSTANCE}/bundle/extracted/ecs-agent.log', None),
    (f'ecs_{OTHER}/bundle/extracted/ecs-agent.log', 403),
    (f'ecs_{INSTANCE}/../private', 400),
    (f'ecs_{INSTANCE}/_metadata/execution.json', 403),
    (f'ecs_{INSTANCE}/bundle/capture.pcap', 403),
])
def test_public_log_keys_are_instance_bound(key, status):
    result = mod.validate_log_key(key, INSTANCE)
    assert (result is None) if status is None else result['statusCode'] == status


def test_artifact_url_lifetime_is_deployment_capped(monkeypatch):
    key = f'ecs_{INSTANCE}/bundle/extracted/large.log'
    captured = {}
    monkeypatch.setattr(mod, 'PRESIGNED_URL_EXPIRATION', 120)
    monkeypatch.setattr(mod, 'safe_s3_head', lambda value: {
        'success': True, 'size': 10, 'content_type': 'text/plain',
    })
    monkeypatch.setattr(mod.s3_client, 'generate_presigned_url',
                        lambda *args, **kwargs: captured.update(kwargs) or 'https://example.invalid')
    result = body(mod.get_artifact_reference({
        'instanceId': INSTANCE, 'logKey': key, 'expirationMinutes': 60,
    }))
    assert captured['ExpiresIn'] == 120
    assert result['expiresIn'] == '120 seconds'

def test_regex_rejects_catastrophic_patterns():
    assert mod.is_dangerous_regex('(a+)+$') is True
    assert mod.is_dangerous_regex('(a|aa)+$') is True
    assert mod.is_dangerous_regex(r'CannotPull|OOMKilled') is False


def test_compiled_regex_search_and_timeout_cleanup(monkeypatch):
    monkeypatch.setattr(mod, 'safe_s3_head_raw', lambda *args, **kwargs: {
        'ContentLength': 30,
    })
    monkeypatch.setattr(mod, 'safe_s3_read_raw',
                        lambda *args, **kwargs: b'normal\nOOMKilled container\n')
    matches = mod.search_file_for_pattern(
        'bucket', f'ecs_{INSTANCE}/bundle/log', re.compile('oomkilled', re.I),
    )
    assert matches[0]['lineNumber'] == 2
    with pytest.raises(mod.RegexTimeout):
        with mod.regex_time_limit(0.01):
            time.sleep(0.05)
    with mod.regex_time_limit(0.01):
        pass


def test_compare_instances_rejects_non_instance_identifiers():
    result = mod.compare_instances({'instanceIds': [INSTANCE, '../metadata']})
    assert result['statusCode'] == 400


def test_direct_status_rejects_tampered_persisted_region(monkeypatch):
    monkeypatch.setattr(mod, 'get_execution_region', lambda execution_id: 'eu-west-1')
    monkeypatch.setattr(mod, 'get_regional_client', lambda *args: pytest.fail('AWS call made'))
    assert mod._direct_get_collection_status({'executionId': 'execution'})['statusCode'] == 403


def test_cdk_hardening_contracts_are_present():
    source = (Path(__file__).parents[1] / 'src' / 'ecs-log-gateway-construct-v2.ts').read_text()
    assert 'KMS encryption is mandatory and cannot be disabled' in source
    assert '`allowedClusterNames` must contain at least one valid ECS cluster name' in source
    assert '`ecsInstanceRoleArns` must contain at least one explicit IAM role ARN' in source
    assert 'ALLOWED_CLUSTER_NAMES: allowedClusterNames.join' in source
    assert 'noncurrentVersionExpiration' in source
    assert 'abortIncompleteMultipartUploadAfter' in source
    assert 'expiredObjectDeleteMarker: true' in source
    assert 'AnyPrincipal' not in source
    assert "sid: 'AllowEC2InstancesBucketPreflight'" in source
    assert "actions: ['s3:GetBucketPolicyStatus', 's3:GetBucketAcl', 's3:ListBucket']" in source
    assert "actions: ['s3:PutObject']" in source
    assert 'DescribeUserPoolClient' not in source
    assert 'ClientSecretRetriever' not in source


def test_deploy_script_validates_instance_upload_policy():
    deploy = (Path(__file__).parents[1] / 'deploy.sh').read_text()
    assert 'Validating ECS instance upload permissions' in deploy
    assert "required_bucket_actions = {'s3:GetBucketAcl', 's3:GetBucketPolicyStatus', 's3:ListBucket'}" in deploy
    assert "required_object_actions = {'s3:PutObject'}" in deploy


def test_unzip_pipeline_supports_managed_tgz_in_canonical_namespace():
    source = (Path(__file__).parents[1] / 'src' / 'ecs-log-gateway-construct-v2.ts').read_text()
    assert "{ suffix: '.tgz' }" in source
    assert 'from urllib.parse import unquote_plus' in source
    assert 'MANAGED_BUNDLE_RE = re.compile' in source
    assert 'return f"ecs_{instance_id}/{execution_id}/extracted/"' in source
    assert "key.lower().endswith('.tgz')" in source
    assert 'Validating archive extraction notifications' in (
        Path(__file__).parents[1] / 'deploy.sh'
    ).read_text()


def test_tool_schemas_bind_generic_s3_access_to_instance():
    source = (Path(__file__).parents[1] / 'src' / 'ecs-log-gateway-construct-v2.ts').read_text()
    assert source.count("Required: ['instanceId', 'logKey']") >= 2
    assert "executionIds is not accepted; batchId is required" in (
        Path(__file__).parents[1] / 'src' / 'lambda' / 'ecs-log-automation.py'
    ).read_text()

def test_batch_hard_cap_rejects_more_than_fifteen():
    result = mod.batch_collect({
        'clusterName': 'cluster', 'dryRun': True, 'maxTotalCollections': 16,
    })
    assert result['statusCode'] == 400


def test_batch_excludes_non_active_container_instances(monkeypatch):
    class Paginator:
        def paginate(self, **kwargs):
            return [{'containerInstanceArns': ['ci-draining']}]

    class Ecs:
        def get_paginator(self, name):
            return Paginator()

        def describe_container_instances(self, **kwargs):
            return {'containerInstances': [{
                'ec2InstanceId': INSTANCE, 'status': 'DRAINING',
                'agentConnected': True, 'runningTasksCount': 0,
            }]}

    monkeypatch.setattr(
        mod, 'get_regional_client',
        lambda service, region: Ecs() if service == 'ecs' else object(),
    )
    result = body(mod.batch_collect({
        'clusterName': 'cluster', 'filter': 'all', 'dryRun': True,
    }))
    assert result['totalInstances'] == 0
    assert result['plannedCollections'] == 0
