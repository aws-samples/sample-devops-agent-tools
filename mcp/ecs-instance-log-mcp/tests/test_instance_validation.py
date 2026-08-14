"""Tests for exact ECS API membership validation."""
import json
import os
import sys

from botocore.exceptions import ClientError

os.environ.setdefault('LOGS_BUCKET_NAME', 'test-bucket')
os.environ.setdefault('SSM_AUTOMATION_ROLE_ARN', 'arn:aws:iam::123456789012:role/test')
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('ALLOWED_REGIONS', 'us-east-1,us-west-2')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'lambda'))
mod = __import__('ecs-log-automation')

INSTANCE = 'i-0123456789abcdef0'
OTHER = 'i-0aaaaaaaaaaaaaaaa'


def response_body(result):
    return json.loads(result['body'])


class Ec2:
    def __init__(self, instances=None, error=None):
        self.instances = instances if instances is not None else [
            {'InstanceId': INSTANCE, 'Tags': [{'Key': 'Name', 'Value': 'ordinary-host'}]}
        ]
        self.error = error

    def describe_instances(self, **kwargs):
        assert kwargs == {'InstanceIds': [INSTANCE]}
        if self.error:
            raise self.error
        return {'Reservations': [{'Instances': self.instances}]}


class Ecs:
    def __init__(self, active=True, failures=None, list_error=None):
        self.active = active
        self.failures = failures or []
        self.list_error = list_error
        self.cluster_calls = []
        self.container_calls = []
        self.describe_calls = []

    def list_clusters(self, **kwargs):
        self.cluster_calls.append(kwargs)
        if self.list_error:
            raise self.list_error
        if not kwargs:
            return {'clusterArns': ['cluster-one'], 'nextToken': 'clusters-2'}
        assert kwargs == {'nextToken': 'clusters-2'}
        return {'clusterArns': ['cluster-two']}

    def list_container_instances(self, **kwargs):
        self.container_calls.append(kwargs)
        if self.list_error:
            raise self.list_error
        cluster = kwargs['cluster']
        if cluster == 'cluster-one' and 'nextToken' not in kwargs:
            return {'containerInstanceArns': ['ci-other'], 'nextToken': 'containers-2'}
        if cluster == 'cluster-one':
            assert kwargs['nextToken'] == 'containers-2'
            return {'containerInstanceArns': []}
        return {'containerInstanceArns': ['ci-target']}

    def describe_container_instances(self, **kwargs):
        self.describe_calls.append(kwargs)
        if self.failures:
            return {'failures': self.failures, 'containerInstances': []}
        arn = kwargs['containerInstances'][0]
        if arn == 'ci-target':
            return {'containerInstances': [{
                'containerInstanceArn': arn,
                'ec2InstanceId': INSTANCE,
                'status': 'ACTIVE' if self.active else 'DRAINING',
            }]}
        return {'containerInstances': [{
            'containerInstanceArn': arn,
            'ec2InstanceId': OTHER,
            'status': 'ACTIVE',
        }]}


def install_clients(monkeypatch, ec2=None, ecs=None):
    clients = {'ec2': ec2 or Ec2(), 'ecs': ecs or Ecs()}
    monkeypatch.setattr(mod, 'get_regional_client', lambda service, region: clients[service])
    return clients


def test_accepts_exact_active_membership_across_all_pages(monkeypatch):
    monkeypatch.setattr(mod, 'ALLOWED_CLUSTER_NAMES', frozenset({'cluster-one', 'cluster-two'}))
    clients = install_clients(monkeypatch)
    assert mod.validate_ecs_instance(INSTANCE, 'us-east-1') is None
    assert clients['ecs'].cluster_calls == []
    assert {'cluster': 'cluster-one', 'nextToken': 'containers-2'} in clients['ecs'].container_calls
    assert clients['ecs'].describe_calls[-1]['cluster'] == 'cluster-two'
    assert clients['ecs'].describe_calls[-1]['containerInstances'] == ['ci-target']


def test_rejects_name_and_tag_heuristics_without_membership(monkeypatch):
    ec2 = Ec2([{'InstanceId': INSTANCE, 'Tags': [
        {'Key': 'Name', 'Value': 'my-ecs-worker'},
        {'Key': 'aws:ecs:clusterName', 'Value': 'cluster-two'},
    ]}])
    ecs = Ecs()
    ecs.list_container_instances = lambda **kwargs: {'containerInstanceArns': []}
    install_clients(monkeypatch, ec2, ecs)
    result = mod.validate_ecs_instance(INSTANCE, 'us-east-1')
    assert result['statusCode'] == 403
    assert 'not an ACTIVE container instance' in response_body(result)['error']


def test_rejects_exact_membership_when_not_active(monkeypatch):
    install_clients(monkeypatch, ecs=Ecs(active=False))
    assert mod.validate_ecs_instance(INSTANCE, 'us-east-1')['statusCode'] == 403


def test_rejects_missing_ec2_instance(monkeypatch):
    install_clients(monkeypatch, ec2=Ec2([]))
    assert mod.validate_ecs_instance(INSTANCE, 'us-east-1')['statusCode'] == 404


def test_fails_closed_on_ecs_list_error(monkeypatch):
    install_clients(monkeypatch, ecs=Ecs(list_error=RuntimeError('unavailable')))
    result = mod.validate_ecs_instance(INSTANCE, 'us-east-1')
    assert result['statusCode'] == 500
    assert 'Failed to validate instance' in response_body(result)['error']


def test_fails_closed_on_describe_failures(monkeypatch):
    install_clients(monkeypatch, ecs=Ecs(failures=[{'reason': 'MISSING'}]))
    result = mod.validate_ecs_instance(INSTANCE, 'us-east-1')
    assert result['statusCode'] == 500
    assert 'Failed to verify ECS membership' in response_body(result)['error']


def test_maps_invalid_instance_client_error_to_not_found(monkeypatch):
    error = ClientError(
        {'Error': {'Code': 'InvalidInstanceID.NotFound', 'Message': 'missing'}},
        'DescribeInstances',
    )
    install_clients(monkeypatch, ec2=Ec2(error=error))
    assert mod.validate_ecs_instance(INSTANCE, 'us-east-1')['statusCode'] == 404
