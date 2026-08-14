import json
import os
import sys
from pathlib import Path

os.environ.setdefault('LOGS_BUCKET_NAME', 'test-bucket')
os.environ.setdefault('SSM_AUTOMATION_ROLE_ARN', 'arn:aws:iam::123456789012:role/test')
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('ALLOWED_REGIONS', 'us-east-1,us-west-2')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'lambda'))
mod = __import__('ecs-log-automation')

INSTANCE = 'i-0123456789abcdef0'
APPROVER = 'arn:aws:iam::123456789012:role/Approver'


def body(result):
    return json.loads(result['body'])


def test_collect_uses_ecs_approval_wrapper(monkeypatch):
    calls = []
    class Ssm:
        def start_automation_execution(self, **kwargs):
            calls.append(kwargs)
            return {'AutomationExecutionId': 'approval-1'}
    monkeypatch.setattr(mod, 'REQUIRE_COLLECTION_APPROVAL', True)
    monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', 'ecs-collect-approval')
    monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', [APPROVER])
    monkeypatch.setattr(mod, 'resolve_and_validate_region', lambda args, iid=None: ('us-east-1', None))
    monkeypatch.setattr(mod, 'validate_ecs_instance', lambda iid, region: None)
    monkeypatch.setattr(mod, 'get_regional_client', lambda service, region: Ssm())
    monkeypatch.setattr(mod, 'store_execution_provenance', lambda *args, **kwargs: True)
    monkeypatch.setattr(mod, 'notify_approvers', lambda *args: None)
    result = mod.start_log_collection({'instanceId': INSTANCE})
    assert body(result)['status'] == 'pending_approval'
    assert calls[0]['DocumentName'] == 'ecs-collect-approval'
    assert calls[0]['Parameters']['ECSInstanceId'] == [INSTANCE]
    assert {'AutomationAssumeRole', 'Approvers', 'SNSTopicArn'}.isdisjoint(
        calls[0]['Parameters']
    )


def test_status_surfaces_pending(monkeypatch):
    execution = {'AutomationExecutionId': 'approval-1', 'DocumentName': 'ecs-collect-approval',
                 'Parameters': {'ECSInstanceId': [INSTANCE]},
                 'AutomationExecutionStatus': 'InProgress', 'StepExecutions': [
                     {'StepName': 'waitForHumanApproval', 'StepStatus': 'InProgress'}]}
    class Ssm:
        def get_automation_execution(self, **kwargs): return {'AutomationExecution': execution}
    monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', 'ecs-collect-approval')
    monkeypatch.setattr(mod, 'get_execution_provenance', lambda eid: {
        'executionId': eid, 'tool': 'collect', 'instanceId': INSTANCE,
        'instanceIds': [], 'region': 'us-east-1',
        'expectedDocument': 'ecs-collect-approval',
    })
    monkeypatch.setattr(mod, 'get_execution_region', lambda eid: 'us-east-1')
    monkeypatch.setattr(mod, 'get_regional_client', lambda *args: Ssm())
    monkeypatch.setattr(mod, 'wait_for_approval_decision', lambda *args: execution)
    assert body(mod.get_collection_status({'executionId': 'approval-1'}))['automation']['humanApproval']['state'] == 'pending'


def test_status_surfaces_denied(monkeypatch):
    execution = {'AutomationExecutionId': 'approval-1', 'DocumentName': 'ecs-collect-approval',
                 'Parameters': {'ECSInstanceId': [INSTANCE]},
                 'AutomationExecutionStatus': 'Failed', 'StepExecutions': [
                     {'StepName': 'waitForHumanApproval', 'StepStatus': 'Failed'}]}
    class Ssm:
        def get_automation_execution(self, **kwargs): return {'AutomationExecution': execution}
    monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', 'ecs-collect-approval')
    monkeypatch.setattr(mod, 'get_execution_provenance', lambda eid: {
        'executionId': eid, 'tool': 'collect', 'instanceId': INSTANCE,
        'instanceIds': [], 'region': 'us-east-1',
        'expectedDocument': 'ecs-collect-approval',
    })
    monkeypatch.setattr(mod, 'get_execution_region', lambda eid: 'us-east-1')
    monkeypatch.setattr(mod, 'get_regional_client', lambda *args: Ssm())
    result = body(mod.get_collection_status({'executionId': 'approval-1'}))
    assert result['automation']['humanApproval']['state'] == 'denied_or_expired'
    assert result['automation']['status'] == 'Denied'


def test_status_surfaces_approved_child(monkeypatch):
    execution = {'AutomationExecutionId': 'approval-1', 'DocumentName': 'ecs-collect-approval',
                 'Parameters': {'ECSInstanceId': [INSTANCE]},
                 'AutomationExecutionStatus': 'Success', 'StepExecutions': [
                     {'StepName': 'waitForHumanApproval', 'StepStatus': 'Success'},
                     {'StepName': 'collectLogs', 'StepStatus': 'Success',
                      'Outputs': {'ExecutionId': ['child-1']}}]}
    class Ssm:
        def get_automation_execution(self, **kwargs): return {'AutomationExecution': execution}
    monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', 'ecs-collect-approval')
    monkeypatch.setattr(mod, 'get_execution_provenance', lambda eid: {
        'executionId': eid, 'tool': 'collect', 'instanceId': INSTANCE,
        'instanceIds': [], 'region': 'us-east-1',
        'expectedDocument': (
            'ecs-collect-approval' if eid == 'approval-1'
            else 'AWSSupport-CollectECSInstanceLogs'
        ),
    })
    monkeypatch.setattr(mod, 'get_execution_region', lambda eid: 'us-east-1')
    monkeypatch.setattr(mod, 'get_regional_client', lambda *args: Ssm())
    monkeypatch.setattr(mod, '_direct_get_collection_status', lambda args: response_status('child-1'))
    result = body(mod.get_collection_status({'executionId': 'approval-1'}))['automation']
    assert result['humanApproval']['state'] == 'approved'
    assert result['childExecutionId'] == 'child-1'


def response_status(execution_id):
    return {'statusCode': 200, 'body': json.dumps({'success': True, 'automation': {
        'executionId': execution_id, 'status': 'InProgress'}})}


def _construct_source():
    return (Path(__file__).parents[1] / 'src' / 'ecs-log-gateway-construct-v2.ts').read_text()


def test_cdk_bakes_deployment_owned_approval_values():
    source = _construct_source()
    assert "AutomationAssumeRole: { type: 'String' }" not in source
    assert "Approvers: { type: 'StringList' }" not in source
    assert "SNSTopicArn: { type: 'String' }" not in source
    assert 'assumeRole: this.ssmAutomationRole.roleArn' in source
    assert 'NotificationArn: this.collectionApprovalTopic.topicArn' in source
    assert 'Approvers: approverArns' in source
    assert 'AutomationAssumeRole: this.ssmAutomationRole.roleArn' in source
    assert '"AutomationAssumeRole": ["${this.ssmAutomationRole.roleArn}"]' in source


def test_cdk_ssm_permissions_are_region_and_document_scoped():
    source = _construct_source()
    assert 'SendAutomationSignal' not in source
    assert 'automation-definition/AWSSupport-CollectECSInstanceLogs:*' in source
    assert 'document/AWSSupport-CollectECSInstanceLogs' in source
    assert 'document/AWS-RunShellScript' in source
    assert 'automation-definition/${documentName}:*' in source
    assert ':document/${documentName}' in source
    assert "if (!requireCollectionApproval && enabledRestrictedTools.includes('tcpdump_capture'))" in source
    assert "'aws:RequestedRegion': allowedRegions" in source
    assert ':document/*' not in source
