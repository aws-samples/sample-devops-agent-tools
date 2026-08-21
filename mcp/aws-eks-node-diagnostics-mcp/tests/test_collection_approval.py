"""
Unit tests for the SSM-native human-in-the-loop collection approval (M1/M2) and
the E4/E5 hardening helpers. These cover the pure-logic paths that do not
require AWS calls (fail-closed preconditions, wrapper status augmentation,
log-key scoping, regex safety).
"""
import os
import sys
import json
import pytest

# Set required env vars before importing the module
os.environ.setdefault('LOGS_BUCKET_NAME', 'test-bucket')
os.environ.setdefault('SSM_AUTOMATION_ROLE_ARN', 'arn:aws:iam::123456789012:role/test')
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('ALLOWED_REGIONS', 'us-east-1,us-west-2')
os.environ.setdefault('SOP_BUCKET_NAME', 'test-sop-bucket')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'lambda'))

mod = __import__('ssm-automation-enhanced')

VALID_KEY = 'eks_i-0123456789abcdef0_exec123/var/log/kubelet.log'
INSTANCE = 'i-0123456789abcdef0'
OTHER_INSTANCE = 'i-0fedcba9876543210'


class TestApprovalPreconditions:
    def test_unconfigured_fails_closed(self, monkeypatch):
        """Approval required but wrapper doc/approvers unset -> 503, never runs unapproved."""
        monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', '')
        monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', [])
        result = mod.enforce_approval_preconditions('us-east-1')
        assert result is not None
        assert result['statusCode'] == 503

    def test_no_approvers_fails_closed(self, monkeypatch):
        """A wrapper document without designated approvers is not usable."""
        monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', 'stack-collect-with-approval')
        monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', [])
        result = mod.enforce_approval_preconditions('us-east-1')
        assert result is not None
        assert result['statusCode'] == 503

    def test_cross_region_rejected(self, monkeypatch):
        """The wrapper doc is regional — approval-gated collection is stack-region only."""
        monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', 'stack-collect-with-approval')
        monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', ['arn:aws:iam::123456789012:role/Approver'])
        result = mod.enforce_approval_preconditions('us-west-2')
        assert result is not None
        assert result['statusCode'] == 400

    def test_configured_stack_region_proceeds(self, monkeypatch):
        monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', 'stack-collect-with-approval')
        monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', ['arn:aws:iam::123456789012:role/Approver'])
        assert mod.enforce_approval_preconditions('us-east-1') is None


class TestConsoleUrl:
    def test_deep_link_shape(self):
        url = mod.console_automation_url('us-east-1', 'exec-123')
        assert url == (
            'https://us-east-1.console.aws.amazon.com/systems-manager/'
            'automation/execution/exec-123?region=us-east-1'
        )


def _wrapper_execution(approve_status, extra_steps=None):
    steps = [{'StepName': 'waitForHumanApproval', 'StepStatus': approve_status}]
    steps.extend(extra_steps or [])
    return {
        'AutomationExecutionId': 'wrapper-1',
        'AutomationExecutionStatus': 'InProgress',
        'StepExecutions': steps,
    }


class TestWrapperStatusAugmentation:
    def test_waiting_reports_pending_with_console_url(self):
        result = {'executionId': 'wrapper-1', 'task': {'message': ''}}
        mod.augment_wrapper_status(_wrapper_execution('Waiting'), result, 'us-east-1')
        assert result['humanApproval']['state'] == 'pending'
        assert 'console.aws.amazon.com' in result['humanApproval']['consoleUrl']
        assert result['task']['message'].startswith('Waiting for human approval')

    def test_denied_or_timed_out_reports_denied(self):
        result = {'executionId': 'wrapper-1', 'task': {'state': 'running', 'message': ''}}
        mod.augment_wrapper_status(_wrapper_execution('TimedOut'), result, 'us-east-1')
        assert result['humanApproval']['state'] == 'denied_or_expired'
        assert result['task']['state'] == 'failed'

    def test_approved_exposes_child_execution(self):
        collect_step = {
            'StepName': 'collectLogs',
            'StepStatus': 'InProgress',
            'Outputs': {'ExecutionId': ['child-42']},
        }
        result = {'executionId': 'wrapper-1'}
        mod.augment_wrapper_status(
            _wrapper_execution('Success', [collect_step]), result, 'us-east-1'
        )
        assert result['humanApproval']['state'] == 'approved'
        assert result['childExecutionId'] == 'child-42'

    def test_approved_batch_exposes_children(self):
        fanout_step = {
            'StepName': 'fanOutCollections',
            'StepStatus': 'Success',
            'Outputs': {'Executions': [f'{INSTANCE}|child-1', f'{OTHER_INSTANCE}|child-2']},
        }
        result = {'executionId': 'wrapper-1'}
        mod.augment_wrapper_status(
            _wrapper_execution('Success', [fanout_step]), result, 'us-east-1'
        )
        assert result['childExecutions'] == [
            {'instanceId': INSTANCE, 'executionId': 'child-1'},
            {'instanceId': OTHER_INSTANCE, 'executionId': 'child-2'},
        ]

    def test_non_wrapper_document_not_flagged(self, monkeypatch):
        monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', 'stack-collect-with-approval')
        monkeypatch.setattr(mod, 'BATCH_APPROVAL_DOCUMENT', 'stack-batch-collect-with-approval')
        assert mod._is_approval_wrapper('AWSSupport-CollectEKSInstanceLogs') is False
        assert mod._is_approval_wrapper('stack-collect-with-approval') is True
        assert mod._is_approval_wrapper('') is False


class TestLogKeyScoping:
    def test_valid_key_no_instance(self):
        assert mod.validate_log_key(VALID_KEY) is None

    def test_valid_key_matching_instance(self):
        assert mod.validate_log_key(VALID_KEY, expected_instance_id=INSTANCE) is None

    def test_key_for_other_instance_rejected(self):
        result = mod.validate_log_key(VALID_KEY, expected_instance_id=OTHER_INSTANCE)
        assert result is not None and result['statusCode'] == 403

    def test_path_traversal_rejected(self):
        result = mod.validate_log_key('eks_i-0123456789abcdef0_exec/../../etc/passwd')
        assert result is not None and result['statusCode'] == 400

    def test_non_bundle_key_rejected(self):
        result = mod.validate_log_key('some/random/object.json')
        assert result is not None and result['statusCode'] == 400


class TestRegexSafety:
    @pytest.mark.parametrize('pattern', ['(a+)+', '(a*)+', r'(\d+){3,}'])
    def test_flags_catastrophic(self, pattern):
        assert mod.is_catastrophic_regex(pattern) is True

    @pytest.mark.parametrize('pattern', ['ERROR|WARN', 'kubelet.*failed', r'\bOOMKilled\b'])
    def test_allows_normal(self, pattern):
        assert mod.is_catastrophic_regex(pattern) is False


class TestIdempotentReplayApproval:
    """A retried collect() with the same idempotencyToken must carry the same
    human-approval context that status() reports — not a bare InProgress."""

    def _replay(self, monkeypatch, document_name):
        monkeypatch.setattr(mod, 'COLLECT_APPROVAL_DOCUMENT', 'stack-collect-with-approval')
        monkeypatch.setattr(mod, 'BATCH_APPROVAL_DOCUMENT', 'stack-batch-collect-with-approval')
        monkeypatch.setattr(mod, 'resolve_and_validate_region',
                            lambda arguments, instance_id: ('us-east-1', None))
        monkeypatch.setattr(mod, 'validate_eks_instance',
                            lambda instance_id, region: None)
        monkeypatch.setattr(mod, 'get_regional_client',
                            lambda service, region: object())
        monkeypatch.setattr(
            mod, 'find_execution_by_idempotency_token',
            lambda instance_id, token: {
                'executionId': 'wrapper-1',
                'status': 'InProgress',
                'documentName': document_name,
                'region': 'us-east-1',
                '_execution': _wrapper_execution('Waiting'),
            },
        )
        result = mod.start_log_collection(
            {'instanceId': INSTANCE, 'idempotencyToken': 'tok-1'}
        )
        assert result['statusCode'] == 200
        return json.loads(result['body'])

    def test_replay_of_pending_wrapper_reports_human_approval(self, monkeypatch):
        body = self._replay(monkeypatch, 'stack-collect-with-approval')
        assert body['idempotent'] is True
        assert body['status'] == 'InProgress'
        assert body['humanApproval']['state'] == 'pending'
        assert 'console.aws.amazon.com' in body['humanApproval']['consoleUrl']
        assert 'nextStep' in body

    def test_replay_of_plain_collection_is_unchanged(self, monkeypatch):
        body = self._replay(monkeypatch, 'AWSSupport-CollectEKSInstanceLogs')
        assert body['idempotent'] is True
        assert body['status'] == 'InProgress'
        assert 'humanApproval' not in body


class TestClusterAllowlistFailClosed:
    """E2 hardening: an empty allowlist only permits clusters when the operator
    explicitly acknowledged any-cluster scope (ALLOW_ANY_CLUSTER_NAME)."""

    def test_empty_allowlist_without_acknowledgment_rejects(self, monkeypatch):
        monkeypatch.setattr(mod, 'ALLOWED_CLUSTER_NAMES', set())
        monkeypatch.setattr(mod, 'ALLOW_ANY_CLUSTER_NAME', False)
        result = mod.validate_cluster_name('prod-cluster')
        assert result is not None and result['statusCode'] == 403

    def test_empty_allowlist_with_acknowledgment_permits(self, monkeypatch):
        monkeypatch.setattr(mod, 'ALLOWED_CLUSTER_NAMES', set())
        monkeypatch.setattr(mod, 'ALLOW_ANY_CLUSTER_NAME', True)
        assert mod.validate_cluster_name('prod-cluster') is None

    def test_allowlisted_cluster_permits(self, monkeypatch):
        monkeypatch.setattr(mod, 'ALLOWED_CLUSTER_NAMES', {'prod-cluster'})
        monkeypatch.setattr(mod, 'ALLOW_ANY_CLUSTER_NAME', False)
        assert mod.validate_cluster_name('prod-cluster') is None

    def test_unlisted_cluster_rejected_even_with_acknowledgment(self, monkeypatch):
        # An explicit allowlist always wins over the any-cluster acknowledgment.
        monkeypatch.setattr(mod, 'ALLOWED_CLUSTER_NAMES', {'prod-cluster'})
        monkeypatch.setattr(mod, 'ALLOW_ANY_CLUSTER_NAME', True)
        result = mod.validate_cluster_name('other-cluster')
        assert result is not None and result['statusCode'] == 403


class TestReadArtifactRequireInstanceId:
    """E4 hardening: read()/artifact() must always be scoped to the instance
    under investigation — omitting instanceId is rejected before any S3 call."""

    def test_read_without_instance_id_rejected(self):
        result = mod.read_log_chunk({'logKey': VALID_KEY})
        assert result['statusCode'] == 400
        assert 'instanceId is required' in result['body']

    def test_artifact_without_instance_id_rejected(self):
        result = mod.get_artifact_reference({'logKey': VALID_KEY})
        assert result['statusCode'] == 400
        assert 'instanceId is required' in result['body']

    def test_read_cross_instance_key_still_rejected(self):
        result = mod.read_log_chunk({'logKey': VALID_KEY, 'instanceId': OTHER_INSTANCE})
        assert result['statusCode'] == 403
