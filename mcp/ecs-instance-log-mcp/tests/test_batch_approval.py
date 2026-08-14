import json
from .test_tcpdump_security import mod, body

APPROVER = 'arn:aws:iam::123456789012:role/Approver'
EXECUTION = '12345678-1234-1234-1234-123456789abc'
PLAN = {'success': True, 'clusterName': 'cluster', 'region': 'us-east-1',
        'buckets': [{'sampleInstances': ['i-0123456789abcdef0']}],
        'plannedCollections': 1}


def response(payload): return {'statusCode': 200, 'body': json.dumps(payload)}


def test_batch_defaults_to_dry_run(monkeypatch):
    seen = []
    monkeypatch.setattr(mod, '_direct_batch_collect', lambda args: seen.append(args) or response(PLAN))
    mod.batch_collect({'clusterName': 'cluster'})
    assert seen[0]['dryRun'] is True


def test_batch_starts_one_approval_wrapper(monkeypatch):
    calls = []
    class Ssm:
        def start_automation_execution(self, **kwargs):
            calls.append(kwargs); return {'AutomationExecutionId': 'batch-wrapper'}
    monkeypatch.setattr(mod, 'REQUIRE_COLLECTION_APPROVAL', True)
    monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', 'collect-wrapper')
    monkeypatch.setattr(mod, 'BATCH_APPROVAL_DOCUMENT', 'batch-wrapper-doc')
    monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', [APPROVER])
    monkeypatch.setattr(mod, '_direct_batch_collect', lambda args: response(PLAN))
    monkeypatch.setattr(mod, 'get_regional_client', lambda *args: Ssm())
    monkeypatch.setattr(mod, 'validate_ecs_instance', lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, 'store_execution_provenance', lambda *args, **kwargs: True)
    monkeypatch.setattr(mod, 'notify_approvers', lambda *args: None)
    monkeypatch.setattr(mod.s3_client, 'put_object', lambda **kwargs: {})
    result = mod.batch_collect({'clusterName': 'cluster', 'dryRun': False})
    assert body(result)['status'] == 'pending_approval'
    assert len(calls) == 1
    assert calls[0]['Parameters']['InstanceIds'] == ['i-0123456789abcdef0']
    assert {'AutomationAssumeRole', 'Approvers', 'SNSTopicArn'}.isdisjoint(
        calls[0]['Parameters']
    )


def test_batch_child_output_parser():
    assert mod._parse_batch_children([f'i-0123456789abcdef0|{EXECUTION}', 'bad']) == [
        {'instanceId': 'i-0123456789abcdef0', 'executionId': EXECUTION}]


def test_batch_status_extracts_and_polls_children(monkeypatch):
    meta = {'batchId': 'b1', 'clusterName': 'cluster', 'region': 'us-east-1',
            'approvalExecutionId': 'wrapper', 'executions': []}
    wrapper = {'AutomationExecutionId': 'wrapper',
               'AutomationExecutionStatus': 'Success', 'StepExecutions': [
                   {'StepName': 'waitForHumanApproval', 'StepStatus': 'Success'},
                   {'StepName': 'fanOutCollections', 'StepStatus': 'Success',
                    'Outputs': {'Executions': [f'i-0123456789abcdef0|{EXECUTION}']}}]}
    class Ssm:
        def get_automation_execution(self, **kwargs):
            return {'AutomationExecution': wrapper}
    stored = []
    monkeypatch.setattr(mod, 'safe_s3_read', lambda key: {
        'success': True, 'content': json.dumps(meta)})
    monkeypatch.setattr(mod, 'get_regional_client', lambda *args: Ssm())
    monkeypatch.setattr(mod, 'get_execution_provenance', lambda eid: {
        'executionId': eid, 'tool': 'batch_collect', 'region': 'us-east-1',
        'expectedDocument': 'batch-wrapper-doc',
        'instanceIds': ['i-0123456789abcdef0'],
    })
    monkeypatch.setattr(mod, 'validate_execution_details', lambda *args: None)
    monkeypatch.setattr(mod, 'store_execution_provenance', lambda *args, **kwargs: True)
    monkeypatch.setattr(mod.s3_client, 'put_object', lambda **kwargs: stored.append(kwargs))
    monkeypatch.setattr(mod, '_direct_batch_status', lambda args: response({
        'allComplete': False, 'executions': [{'executionId': EXECUTION}]}))
    result = body(mod.batch_status({'batchId': 'b1'}))
    assert result['humanApproval']['state'] == 'approved'
    assert result['executions'][0]['executionId'] == EXECUTION
    persisted = json.loads(stored[0]['Body'])
    assert persisted['executions'][0]['instanceId'] == 'i-0123456789abcdef0'


def _batch_meta():
    return {
        'batchId': 'b1', 'clusterName': 'cluster', 'region': 'us-east-1',
        'approvalExecutionId': 'wrapper', 'executions': [],
        'plannedInstanceIds': ['i-0123456789abcdef0', 'i-0aaaaaaaaaaaaaaaa'],
    }


def _install_wrapper(monkeypatch, wrapper, stored, metadata=None):
    class Ssm:
        def get_automation_execution(self, **kwargs):
            return {'AutomationExecution': wrapper}
    monkeypatch.setattr(mod, 'safe_s3_read', lambda key: {
        'success': True, 'content': json.dumps(metadata or _batch_meta())})
    monkeypatch.setattr(mod, 'get_regional_client', lambda *args: Ssm())
    monkeypatch.setattr(mod, 'get_execution_provenance', lambda eid: {
        'executionId': eid, 'tool': 'batch_collect', 'region': 'us-east-1',
        'expectedDocument': 'batch-wrapper-doc',
        'instanceIds': _batch_meta()['plannedInstanceIds'],
    })
    monkeypatch.setattr(mod, 'validate_execution_details', lambda *args: None)
    monkeypatch.setattr(mod, 'store_execution_provenance', lambda *args, **kwargs: True)
    monkeypatch.setattr(mod.s3_client, 'put_object', lambda **kwargs: stored.append(kwargs))


def test_batch_all_starts_failed_is_terminal_and_persists_errors(monkeypatch):
    stored = []
    wrapper = {
        'AutomationExecutionStatus': 'Success',
        'StepExecutions': [
            {'StepName': 'waitForHumanApproval', 'StepStatus': 'Success'},
            {'StepName': 'fanOutCollections', 'StepStatus': 'Success', 'Outputs': {
                'Executions': ['malformed'],
                'Errors': ['i-0123456789abcdef0|start denied', 'bad|ignored'],
            }},
        ],
    }
    _install_wrapper(monkeypatch, wrapper, stored)
    result = body(mod.batch_status({'batchId': 'b1'}))
    assert result['allComplete'] is True
    assert result['status'] == 'failed'
    assert result['counts'] == {'planned': 2, 'started': 0, 'startFailed': 1}
    assert result['fanOutErrors'] == [
        {'instanceId': 'i-0123456789abcdef0', 'error': 'start denied'}]
    assert json.loads(stored[-1]['Body'])['fanOutErrors'] == result['fanOutErrors']


def test_batch_partial_starts_keep_polling_and_surface_counts(monkeypatch):
    stored = []
    wrapper = {
        'AutomationExecutionStatus': 'Success',
        'StepExecutions': [
            {'StepName': 'waitForHumanApproval', 'StepStatus': 'Success'},
            {'StepName': 'fanOutCollections', 'StepStatus': 'Success', 'Outputs': {
                'Executions': [f'i-0123456789abcdef0|{EXECUTION}'],
                'Errors': ['i-0aaaaaaaaaaaaaaaa|start failed'],
            }},
        ],
    }
    _install_wrapper(monkeypatch, wrapper, stored)
    polled = []
    monkeypatch.setattr(mod, '_direct_batch_status', lambda args: polled.append(args) or response({
        'allComplete': False,
        'summary': {'total': 1, 'succeeded': 0, 'failed': 0, 'inProgress': 1, 'unknown': 0},
        'executions': [{'executionId': EXECUTION, 'status': 'InProgress'}],
    }))
    result = body(mod.batch_status({'batchId': 'b1'}))
    assert polled == [{'executionIds': [EXECUTION]}]
    assert result['allComplete'] is False
    assert result['status'] == 'partial_failure'
    assert result['counts'] == {'planned': 2, 'started': 1, 'startFailed': 1}
    assert result['fanOutErrors'][0]['instanceId'] == 'i-0aaaaaaaaaaaaaaaa'


def test_terminal_fanout_failure_is_terminal_with_parsed_outputs(monkeypatch):
    stored = []
    wrapper = {
        'AutomationExecutionStatus': 'Failed',
        'FailureMessage': 'fan-out failed',
        'StepExecutions': [
            {'StepName': 'waitForHumanApproval', 'StepStatus': 'Success'},
            {'StepName': 'fanOutCollections', 'StepStatus': 'Failed', 'Outputs': {
                'Executions': [f'i-0123456789abcdef0|{EXECUTION}'],
                'Errors': ['i-0aaaaaaaaaaaaaaaa|start failed'],
            }},
        ],
    }
    _install_wrapper(monkeypatch, wrapper, stored)
    monkeypatch.setattr(mod, '_direct_batch_status', lambda args: (_ for _ in ()).throw(
        AssertionError('terminal wrapper must not poll children')))
    result = body(mod.batch_status({'batchId': 'b1'}))
    assert result['allComplete'] is True
    assert result['status'] == 'failed'
    assert result['executions'][0]['executionId'] == EXECUTION
    assert result['fanOutErrors'][0]['error'] == 'start failed'
    assert json.loads(stored[-1]['Body'])['fanOutErrors'] == result['fanOutErrors']


def test_terminal_wrapper_failure_overrides_persisted_children(monkeypatch):
    metadata = _batch_meta()
    metadata['executions'] = [{
        'instanceId': 'i-0123456789abcdef0',
        'executionId': EXECUTION,
        'status': 'Started',
    }]
    wrapper = {
        'AutomationExecutionStatus': 'Failed',
        'FailureMessage': 'wrapper failed after a child start',
        'StepExecutions': [
            {'StepName': 'waitForHumanApproval', 'StepStatus': 'Success'},
            {'StepName': 'fanOutCollections', 'StepStatus': 'Failed'},
        ],
    }
    _install_wrapper(monkeypatch, wrapper, [], metadata)
    monkeypatch.setattr(mod, '_direct_batch_status', lambda args: (_ for _ in ()).throw(
        AssertionError('terminal wrapper must not poll persisted children')))
    result = body(mod.batch_status({'batchId': 'b1'}))
    assert result['allComplete'] is True
    assert result['status'] == 'failed'
    assert result['counts']['started'] == 1
