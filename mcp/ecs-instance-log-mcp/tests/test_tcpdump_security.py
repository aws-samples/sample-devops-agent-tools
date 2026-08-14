import json
import os
import sys
import pytest

os.environ.setdefault('LOGS_BUCKET_NAME', 'test-bucket')
os.environ.setdefault('SSM_AUTOMATION_ROLE_ARN', 'arn:aws:iam::123456789012:role/test')
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('ALLOWED_REGIONS', 'us-east-1,us-west-2')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'lambda'))
mod = __import__('ecs-log-automation')
TASK = '0123456789abcdef0123456789abcdef'
INSTANCE = 'i-0123456789abcdef0'


def body(result): return json.loads(result['body'])


def test_restricted_tools_fail_closed(monkeypatch):
    monkeypatch.setattr(mod, 'ENABLED_RESTRICTED_TOOLS', set())
    assert mod.validate_tool_authorization('tcpdump_capture')['statusCode'] == 403


@pytest.mark.parametrize('value,expected', [
    (TASK, TASK),
    ('arn:aws:ecs:us-east-1:123456789012:task/cluster/' + TASK, TASK),
])
def test_task_id_normalization(value, expected):
    assert mod.normalize_ecs_task_id(value) == (expected, None)


@pytest.mark.parametrize('value', ['short', TASK + '0', 'prefix-' + TASK,
                                    'arn:aws:ecs:us-east-1:123:task/' + TASK])
def test_task_id_rejects_non_exact_values(value):
    assert mod.normalize_ecs_task_id(value)[1]


@pytest.mark.parametrize('expr', ['', 'port 53', 'udp port 53',
                                  'host 10.0.0.1 and port 443', 'net 10.0.0.0/16'])
def test_bpf_safe(expr): assert mod.validate_bpf_filter(expr) is None


@pytest.mark.parametrize('expr', ['port 53; id', 'port `id`', 'port 53 $(id)',
                                  'port 53\nreboot', 'tcp[tcpflags] & 2 != 0'])
def test_bpf_rejects_shell_and_complex_flags(expr):
    assert mod.validate_bpf_filter(expr)


def test_script_is_exact_task_namespace_and_has_no_template_sequence():
    keys = {'pcap': f'tcpdump/{INSTANCE}/t/capture.pcap',
            'text': f'tcpdump/{INSTANCE}/t/capture.txt',
            'stats': f'tcpdump/{INSTANCE}/t/stats.json'}
    script = mod._build_ecs_tcpdump_script(TASK, 'web', 'any', 'port 443', 30, 't', keys)
    assert '{{' not in script
    assert "resolved == task_id" in script
    assert "c.get('Name') == requested" in script
    assert 'explicit containerName did not match exactly once' in script
    assert 'explicit containerName must identify a RUNNING application container' in script
    assert 'containerName is required unless exactly one RUNNING application container is eligible' in script
    assert 'stat -Lc %i /proc/1/ns/net' in script
    assert 'process start time changed' in script
    assert 'network namespace inode changed' in script
    assert 'nsenter -n -t "$TARGET_PID" -- tcpdump' in script
    assert 'docker inspect --format' not in script
    assert "data[0].get('Id') == sys.argv[1]" in script
    assert 'containers ls -q' not in script
    assert 'yum install' not in script and 'apt-get install' not in script
