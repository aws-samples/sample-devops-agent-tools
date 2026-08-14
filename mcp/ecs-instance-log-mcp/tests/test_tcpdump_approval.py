import json
from datetime import datetime, timezone
from pathlib import Path
from .test_tcpdump_security import mod, TASK, INSTANCE, body

APPROVER = 'arn:aws:iam::123456789012:role/Approver'
COMMAND = '12345678-1234-1234-1234-123456789abc'


def capture_metadata(instance_id=INSTANCE, command_id=COMMAND):
    prefix = f'tcpdump/{instance_id}/20250101T000000Z'
    return {
        'commandId': command_id,
        'instanceId': instance_id,
        'region': 'us-east-1',
        'startedAt': '20250101T000000Z',
        's3Key': f'{prefix}/capture.pcap',
        's3KeyTxt': f'{prefix}/capture_summary.txt',
        's3KeyStats': f'{prefix}/capture_stats.json',
    }


def install_wrapper_provenance(monkeypatch):
    monkeypatch.setattr(mod, 'get_execution_provenance', lambda execution_id: {
        'executionId': execution_id, 'tool': 'tcpdump_capture',
        'instanceId': INSTANCE, 'instanceIds': [], 'region': 'us-east-1',
        'expectedDocument': 'ecs-tcpdump-approval',
    })
    monkeypatch.setattr(mod, 'validate_execution_details', lambda *args: None)


def test_tcpdump_requires_task_and_confirmation(monkeypatch):
    monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', {'tcpdump_capture'})
    assert mod.tcpdump_capture({'instanceId': INSTANCE})['statusCode'] == 400
    result = mod.tcpdump_capture({'instanceId': INSTANCE, 'taskId': TASK})
    assert body(result)['details']['requiresConfirmation'] is True


def test_capture_starts_approval_not_command(monkeypatch):
    calls = []
    class Ssm:
        def start_automation_execution(self, **kwargs):
            calls.append(('automation', kwargs))
            return {'AutomationExecutionId': 'tcp-approval'}
        def send_command(self, **kwargs): calls.append(('command', kwargs))
    monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', {'tcpdump_capture'})
    monkeypatch.setattr(mod, 'REQUIRE_COLLECTION_APPROVAL', True)
    monkeypatch.setattr(mod, 'TCPDUMP_APPROVAL_DOCUMENT', 'ecs-tcpdump-approval')
    monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', [APPROVER])
    monkeypatch.setattr(mod, 'resolve_and_validate_region', lambda args, iid=None: ('us-east-1', None))
    monkeypatch.setattr(mod, 'validate_ecs_instance', lambda *args: None)
    monkeypatch.setattr(mod, 'get_regional_client', lambda *args: Ssm())
    monkeypatch.setattr(mod, '_store_tcpdump_metadata', lambda *args: None)
    monkeypatch.setattr(mod, 'store_execution_region', lambda *args: None)
    monkeypatch.setattr(mod, 'store_execution_provenance', lambda *args, **kwargs: True)
    monkeypatch.setattr(mod, 'notify_approvers', lambda *args: None)
    result = mod.tcpdump_capture({'instanceId': INSTANCE, 'taskId': TASK,
                                  'containerName': 'web', 'confirmCapture': True})
    assert body(result)['status'] == 'pending_approval'
    assert [kind for kind, _ in calls] == ['automation']
    parameters = calls[0][1]['Parameters']
    assert parameters['CaptureScope'] == [f'task/{TASK}/container/web']
    assert {'AutomationAssumeRole', 'Approvers', 'SNSTopicArn'}.isdisjoint(parameters)
    script = parameters['Commands'][0]
    assert '{{' not in script

    calls.clear()
    result = mod.tcpdump_capture({
        'instanceId': INSTANCE, 'taskId': TASK, 'confirmCapture': True})
    assert body(result)['status'] == 'pending_approval'
    assert calls[0][1]['Parameters']['CaptureScope'] == [
        f'task/{TASK}/container/auto']


def test_analyze_rejects_cross_instance_metadata(monkeypatch):
    monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', {'tcpdump_analyze'})
    monkeypatch.setattr(
        mod, '_read_tcpdump_metadata',
        lambda key: capture_metadata('i-0aaaaaaaaaaaaaaaa'),
    )
    result = mod.tcpdump_analyze({'instanceId': INSTANCE, 'commandId': COMMAND})
    assert result['statusCode'] == 403


def test_analyze_rejects_missing_command_metadata(monkeypatch):
    monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', {'tcpdump_analyze'})
    monkeypatch.setattr(mod, '_read_tcpdump_metadata', lambda key: None)
    result = mod.tcpdump_analyze({'instanceId': INSTANCE, 'commandId': COMMAND})
    assert result['statusCode'] == 404
    assert 'refusing latest-capture fallback' in body(result)['error']


def test_analyze_rejects_artifact_outside_instance_prefix(monkeypatch):
    monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', {'tcpdump_analyze'})
    metadata = capture_metadata()
    metadata['s3Key'] = 'tcpdump/other-instance/t/capture.pcap'
    monkeypatch.setattr(mod, '_read_tcpdump_metadata', lambda key: metadata)
    assert mod.tcpdump_analyze({
        'instanceId': INSTANCE, 'commandId': COMMAND})['statusCode'] == 403


def test_analyze_requires_valid_command_id(monkeypatch):
    monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', {'tcpdump_analyze'})
    assert mod.tcpdump_analyze({'instanceId': INSTANCE})['statusCode'] == 400
    assert mod.tcpdump_analyze({
        'instanceId': INSTANCE, 'commandId': 'latest'})['statusCode'] == 400


def test_capture_poll_rejects_invalid_command_id(monkeypatch):
    monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', {'tcpdump_capture'})
    result = mod.tcpdump_capture({'instanceId': INSTANCE, 'commandId': 'bad'})
    assert result['statusCode'] == 400


def test_wrapper_terminal_without_command_id_fails(monkeypatch):
    wrapper = {
        'AutomationExecutionStatus': 'Failed',
        'StepExecutions': [
            {'StepName': 'waitForHumanApproval', 'StepStatus': 'Success'},
            {'StepName': 'runTcpdump', 'StepStatus': 'Failed'},
        ],
    }
    class Ssm:
        def get_automation_execution(self, **kwargs):
            return {'AutomationExecution': wrapper}
    install_wrapper_provenance(monkeypatch)
    monkeypatch.setattr(mod, 'get_execution_region', lambda execution_id: 'us-east-1')
    monkeypatch.setattr(mod, 'get_regional_client', lambda *args: Ssm())
    result = mod._poll_tcpdump_wrapper('wrapper', INSTANCE, {})
    assert result['statusCode'] == 500
    assert 'without a command ID' in body(result)['error']


def test_wrapper_refreshes_started_at_from_run_step(monkeypatch):
    started = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    wrapper = {
        'AutomationExecutionStatus': 'Success',
        'StepExecutions': [
            {'StepName': 'waitForHumanApproval', 'StepStatus': 'Success'},
            {'StepName': 'runTcpdump', 'StepStatus': 'Success',
             'ExecutionStartTime': started,
             'Outputs': {'CommandId': [COMMAND]}},
        ],
    }
    class Ssm:
        def get_automation_execution(self, **kwargs):
            return {'AutomationExecution': wrapper}
    stored = []
    install_wrapper_provenance(monkeypatch)
    monkeypatch.setattr(mod, 'get_execution_region', lambda execution_id: 'us-east-1')
    monkeypatch.setattr(mod, 'get_regional_client', lambda *args: Ssm())
    monkeypatch.setattr(mod, '_read_tcpdump_metadata', lambda key: {
        **capture_metadata(), 'startedAt': '20240101T000000Z'})
    monkeypatch.setattr(mod, '_store_tcpdump_metadata',
                        lambda key, metadata: stored.append((key, dict(metadata))))
    monkeypatch.setattr(mod, '_poll_tcpdump_status', lambda *args: {
        'statusCode': 200, 'body': json.dumps({
            'status': 'in_progress', 'commandId': COMMAND})})
    result = body(mod._poll_tcpdump_wrapper('wrapper', INSTANCE, {}))
    assert result['commandId'] == COMMAND
    assert len(stored) == 2
    assert all(item[1]['startedAt'] == '2025-01-02T03:04:05Z' for item in stored)


def test_cdk_capture_scope_contract_matches_lambda_format():
    source = (Path(__file__).parents[1] / 'src' / 'ecs-log-gateway-construct-v2.ts').read_text()
    assert "allowedPattern: '^task/[0-9a-f]{32}/container/[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$'" in source


def test_analyze_rejects_command_metadata_mismatch(monkeypatch):
    monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', {'tcpdump_analyze'})
    metadata = capture_metadata(command_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    monkeypatch.setattr(mod, '_read_tcpdump_metadata', lambda key: metadata)
    assert mod.tcpdump_analyze({
        'instanceId': INSTANCE, 'commandId': COMMAND})['statusCode'] == 403


def test_analyze_rejects_incomplete_artifact_metadata(monkeypatch):
    monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', {'tcpdump_analyze'})
    metadata = capture_metadata()
    metadata.pop('s3KeyStats')
    monkeypatch.setattr(mod, '_read_tcpdump_metadata', lambda key: metadata)
    assert mod.tcpdump_analyze({
        'instanceId': INSTANCE, 'commandId': COMMAND})['statusCode'] == 403
