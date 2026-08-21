"""
Unit tests for the restored tcpdump tools (M3): restricted-tool opt-in,
BPF filter validation, and the human-in-the-loop aws:approve gating of
tcpdump_capture. Pure-logic paths only — no AWS calls.
"""
import os
import sys
import json
import pytest

os.environ.setdefault('LOGS_BUCKET_NAME', 'test-bucket')
os.environ.setdefault('SSM_AUTOMATION_ROLE_ARN', 'arn:aws:iam::123456789012:role/test')
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('ALLOWED_REGIONS', 'us-east-1,us-west-2')
os.environ.setdefault('SOP_BUCKET_NAME', 'test-sop-bucket')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'lambda'))

mod = __import__('ssm-automation-enhanced')

INSTANCE = 'i-0123456789abcdef0'
APPROVER = 'arn:aws:iam::123456789012:role/Approver'


def _body(result):
    return json.loads(result['body'])


class TestRestrictedToolGating:
    def test_tcpdump_tools_are_restricted(self):
        assert 'tcpdump_capture' in mod.RESTRICTED_TOOLS
        assert 'tcpdump_analyze' in mod.RESTRICTED_TOOLS

    def test_restricted_tool_denied_unless_enabled(self, monkeypatch):
        monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', set())
        result = mod.validate_tool_authorization('tcpdump_capture')
        assert result is not None
        assert result['statusCode'] == 403

    def test_restricted_tool_allowed_when_enabled(self, monkeypatch):
        monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', {'tcpdump_capture'})
        monkeypatch.setattr(mod, 'TOOL_AUTHORIZATION_ACL', {})
        assert mod.validate_tool_authorization('tcpdump_capture') is None


class TestBpfFilterValidation:
    @pytest.mark.parametrize('expr', [
        '', 'port 53', 'udp port 53', 'host 10.0.0.1 and port 443',
        'net 10.0.0.0/16', 'src host 10.0.0.5 and dst port 443',
    ])
    def test_safe_filters_accepted(self, expr):
        assert mod.validate_bpf_filter(expr) is None

    @pytest.mark.parametrize('expr', [
        'port 53; rm -rf /', 'port `id`', 'port 53 $(reboot)',
        'port 53\nreboot', 'port 53 && curl evil',
        # '&' and '!' are hard-rejected even in BPF-legal flag expressions —
        # user-supplied filters get the conservative allowlist; the stats
        # script builds its own flag filters internally.
        'tcp[tcpflags] & (tcp-syn) != 0',
    ])
    def test_injection_attempts_rejected(self, expr):
        assert mod.validate_bpf_filter(expr) is not None


class TestTcpdumpApprovalPreconditions:
    def test_unconfigured_fails_closed(self, monkeypatch):
        monkeypatch.setattr(mod, 'TCPDUMP_APPROVAL_DOCUMENT', '')
        monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', [])
        result = mod.enforce_tcpdump_approval_preconditions('us-east-1')
        assert result is not None
        assert result['statusCode'] == 503

    def test_no_approvers_fails_closed(self, monkeypatch):
        monkeypatch.setattr(mod, 'TCPDUMP_APPROVAL_DOCUMENT', 'stack-tcpdump-with-approval')
        monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', [])
        result = mod.enforce_tcpdump_approval_preconditions('us-east-1')
        assert result is not None
        assert result['statusCode'] == 503

    def test_cross_region_rejected(self, monkeypatch):
        monkeypatch.setattr(mod, 'TCPDUMP_APPROVAL_DOCUMENT', 'stack-tcpdump-with-approval')
        monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', [APPROVER])
        result = mod.enforce_tcpdump_approval_preconditions('us-west-2')
        assert result is not None
        assert result['statusCode'] == 400

    def test_configured_stack_region_proceeds(self, monkeypatch):
        monkeypatch.setattr(mod, 'TCPDUMP_APPROVAL_DOCUMENT', 'stack-tcpdump-with-approval')
        monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', [APPROVER])
        assert mod.enforce_tcpdump_approval_preconditions('us-east-1') is None


class TestConfirmationGate:
    def test_capture_requires_confirmation(self):
        result = mod.tcpdump_capture({'instanceId': INSTANCE})
        assert result['statusCode'] == 400
        body = _body(result)
        assert body['details']['requiresConfirmation'] is True

    def test_invalid_filter_rejected_before_confirmation(self):
        result = mod.tcpdump_capture({
            'instanceId': INSTANCE, 'filter': 'port 53; rm -rf /',
        })
        assert result['statusCode'] == 400
        assert 'BPF filter' in _body(result)['error']


class _FakeSsm:
    def __init__(self):
        self.calls = []

    def start_automation_execution(self, **kwargs):
        self.calls.append(kwargs)
        return {'AutomationExecutionId': 'exec-tcpdump-123'}


class _FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, **kwargs):
        self.objects[kwargs['Key']] = kwargs.get('Body', '')
        return {}


class TestApprovalGatedCapture:
    def _setup(self, monkeypatch, fake_ssm, fake_s3):
        monkeypatch.setattr(mod, 'REQUIRE_COLLECTION_APPROVAL', True)
        monkeypatch.setattr(mod, 'TCPDUMP_APPROVAL_DOCUMENT', 'stack-tcpdump-with-approval')
        monkeypatch.setattr(mod, 'APPROVAL_APPROVERS', [APPROVER])
        monkeypatch.setattr(mod, 'APPROVAL_TOPIC_ARN', '')
        monkeypatch.setattr(mod, 's3_client', fake_s3)
        monkeypatch.setattr(mod, 'resolve_and_validate_region',
                            lambda args, iid=None: ('us-east-1', None))
        monkeypatch.setattr(mod, 'validate_eks_instance', lambda iid, region: None)
        monkeypatch.setattr(mod, 'get_regional_client', lambda svc, region: fake_ssm)
        monkeypatch.setattr(mod, 'store_execution_region', lambda eid, region: True)

    def test_capture_pauses_at_approval(self, monkeypatch):
        fake_ssm, fake_s3 = _FakeSsm(), _FakeS3()
        self._setup(monkeypatch, fake_ssm, fake_s3)

        result = mod.tcpdump_capture({
            'instanceId': INSTANCE, 'confirmCapture': True,
            'durationSeconds': 30, 'filter': 'udp port 53',
        })
        assert result['statusCode'] == 200
        body = _body(result)
        assert body['status'] == 'pending_approval'
        assert body['executionId'] == 'exec-tcpdump-123'
        assert 'approvalConsoleUrl' in body
        assert body['humanApproval']['state'] == 'pending'
        # nextStep must direct the agent to poll the wrapper execution
        assert 'executionId="exec-tcpdump-123"' in body['nextStep']

        # The wrapper (not send_command) was started, with the script attached
        assert len(fake_ssm.calls) == 1
        call = fake_ssm.calls[0]
        assert call['DocumentName'] == 'stack-tcpdump-with-approval'
        assert call['Parameters']['InstanceId'] == [INSTANCE]
        assert 'tcpdump' in call['Parameters']['Commands'][0]
        assert call['Parameters']['Approvers'] == [APPROVER]

        # Capture metadata is persisted keyed by execution id
        assert 'tcpdump-executions/exec-tcpdump-123.json' in fake_s3.objects
        meta = json.loads(fake_s3.objects['tcpdump-executions/exec-tcpdump-123.json'])
        assert meta['instanceId'] == INSTANCE
        assert meta['filter'] == 'udp port 53'

    def test_unconfigured_approval_fails_closed(self, monkeypatch):
        fake_ssm, fake_s3 = _FakeSsm(), _FakeS3()
        self._setup(monkeypatch, fake_ssm, fake_s3)
        monkeypatch.setattr(mod, 'TCPDUMP_APPROVAL_DOCUMENT', '')

        result = mod.tcpdump_capture({
            'instanceId': INSTANCE, 'confirmCapture': True,
        })
        assert result['statusCode'] == 503
        assert fake_ssm.calls == []  # nothing ran without the approval wrapper

    @pytest.mark.parametrize('extra_args', [
        {},                                       # host namespace capture
        {'podName': 'web-abc123'},                # pod namespace (docker/crictl PID discovery)
        {'containerPid': '4242'},                 # explicit container PID
    ])
    def test_script_has_no_ssm_variable_sequences(self, monkeypatch, extra_args):
        """
        Regression: the capture script is passed as a parameter into the
        approval wrapper Automation document, and SSM Automation re-resolves
        any literal '{{ ... }}' inside substituted parameter values. A Go
        template like docker inspect's State.Pid format string caused:
        'Failed to resolve input: .State.Pid ... is not defined in the
        Automation Document'. No generated script may contain a literal '{{'
        (a bare '}}' is harmless — SSM only resolves opening sequences — and
        occurs legitimately where nested JSON objects close).
        """
        fake_ssm, fake_s3 = _FakeSsm(), _FakeS3()
        self._setup(monkeypatch, fake_ssm, fake_s3)

        result = mod.tcpdump_capture({
            'instanceId': INSTANCE, 'confirmCapture': True,
            'durationSeconds': 30, **extra_args,
        })
        assert result['statusCode'] == 200
        script = fake_ssm.calls[0]['Parameters']['Commands'][0]
        assert '{{' not in script, 'script contains SSM variable open sequence'


class TestWrapperPolling:
    def _execution(self, approve_status, extra_steps=None):
        return {
            'AutomationExecutionId': 'exec-tcpdump-123',
            'AutomationExecutionStatus': 'InProgress',
            'DocumentName': 'stack-tcpdump-with-approval',
            'StepExecutions': [
                {'StepName': 'waitForHumanApproval', 'StepStatus': approve_status},
            ] + (extra_steps or []),
        }

    def _client_for(self, execution):
        class _C:
            def get_automation_execution(self, AutomationExecutionId):
                return {'AutomationExecution': execution}
        return _C()

    def _setup(self, monkeypatch, execution):
        monkeypatch.setattr(mod, 'get_execution_region', lambda eid: 'us-east-1')
        monkeypatch.setattr(mod, 'get_regional_client',
                            lambda svc, region: self._client_for(execution))
        monkeypatch.setattr(mod, 'wait_for_approval_decision',
                            lambda client, eid, ex: ex)

    def test_denied_approval_reports_denied(self, monkeypatch):
        self._setup(monkeypatch, self._execution('Failed'))
        result = mod._poll_tcpdump_wrapper('exec-tcpdump-123', INSTANCE, {})
        assert result['statusCode'] == 403
        body = _body(result)
        assert body['details']['humanApproval']['state'] == 'denied_or_expired'

    def test_pending_approval_keeps_polling(self, monkeypatch):
        self._setup(monkeypatch, self._execution('InProgress'))
        result = mod._poll_tcpdump_wrapper('exec-tcpdump-123', INSTANCE, {})
        assert result['statusCode'] == 200
        body = _body(result)
        assert body['status'] == 'pending_approval'
        assert body['humanApproval']['state'] == 'pending'

    def test_approved_without_command_yet_is_in_progress(self, monkeypatch):
        self._setup(monkeypatch, self._execution(
            'Success',
            extra_steps=[{'StepName': 'runTcpdump', 'StepStatus': 'InProgress'}],
        ))
        result = mod._poll_tcpdump_wrapper('exec-tcpdump-123', INSTANCE, {})
        assert result['statusCode'] == 200
        body = _body(result)
        assert body['status'] == 'in_progress'
        assert body['humanApproval']['state'] == 'approved'

    def test_wrapper_document_recognized(self, monkeypatch):
        monkeypatch.setattr(mod, 'TCPDUMP_APPROVAL_DOCUMENT', 'stack-tcpdump-with-approval')
        assert mod._is_approval_wrapper('stack-tcpdump-with-approval') is True
