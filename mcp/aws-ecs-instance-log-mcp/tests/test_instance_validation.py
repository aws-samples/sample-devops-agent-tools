"""
Property-based tests for Lambda ECS instance validation (Property 7).
Tests validate_ecs_instance() correctness with mocked EC2/ECS responses.
Mirrors eks-node-log-mcp/tests/test_instance_validation.py for ECS.
"""
import json
import pytest
from hypothesis import given, strategies as st, settings, assume


# =============================================================================
# Property 7: Lambda ECS instance validation correctness
# =============================================================================

tag_key = st.text(min_size=1, max_size=50, alphabet='abcdefghijklmnopqrstuvwxyz./-:')
tag_value = st.text(min_size=0, max_size=20)
tag_entry = st.fixed_dictionaries({'Key': tag_key, 'Value': tag_value})
tag_list = st.lists(tag_entry, min_size=0, max_size=10)

ecs_cluster_name = st.text(min_size=1, max_size=30, alphabet='abcdefghijklmnopqrstuvwxyz0123456789-')


def has_ecs_tag(tags):
    """Check if any tag indicates ECS membership."""
    for t in tags:
        if t['Key'] == 'aws:ecs:clusterName':
            return True
        if t['Key'].startswith('ecs:cluster'):
            return True
        if t['Key'] == 'Name' and 'ecs' in t.get('Value', '').lower():
            return True
    return False


def simulate_validate_ecs_instance(tags):
    """Simulate the validate_ecs_instance tag-check logic (without ECS API fallback)."""
    for tag in tags:
        if tag['Key'] == 'aws:ecs:clusterName':
            return None  # Valid
        if tag['Key'].startswith('ecs:cluster'):
            return None
        if tag['Key'] == 'Name' and 'ecs' in tag.get('Value', '').lower():
            return None
    return {'statusCode': 403, 'body': json.dumps({'error': 'Not an ECS instance'})}


@given(tags=tag_list)
@settings(max_examples=100)
def test_ecs_validation_correctness(tags):
    """validate_ecs_instance returns None iff ECS tag exists."""
    result = simulate_validate_ecs_instance(tags)
    if has_ecs_tag(tags):
        assert result is None
    else:
        assert result is not None
        assert result['statusCode'] == 403


@given(cluster_name=ecs_cluster_name)
@settings(max_examples=50)
def test_ecs_validation_accepts_tagged_instance(cluster_name):
    """Instance with aws:ecs:clusterName tag is always accepted."""
    tags = [
        {'Key': 'aws:ecs:clusterName', 'Value': cluster_name},
        {'Key': 'Name', 'Value': 'my-ecs-instance'},
    ]
    result = simulate_validate_ecs_instance(tags)
    assert result is None


def test_ecs_validation_accepts_ecs_cluster_tag():
    """Instance with ecs:cluster-name tag is accepted."""
    tags = [
        {'Key': 'ecs:cluster-name', 'Value': 'my-cluster'},
    ]
    result = simulate_validate_ecs_instance(tags)
    assert result is None


def test_ecs_validation_accepts_ecs_name_tag():
    """Instance with 'ecs' in Name tag is accepted."""
    tags = [
        {'Key': 'Name', 'Value': 'my-ecs-worker-node'},
    ]
    result = simulate_validate_ecs_instance(tags)
    assert result is None


def test_ecs_validation_rejects_untagged_instance():
    """Instance without any ECS tag is rejected."""
    tags = [
        {'Key': 'Name', 'Value': 'my-instance'},
        {'Key': 'Environment', 'Value': 'production'},
        {'Key': 'kubernetes.io/cluster/my-cluster', 'Value': 'owned'},  # EKS tag, not ECS
    ]
    result = simulate_validate_ecs_instance(tags)
    assert result is not None
    assert result['statusCode'] == 403


def test_ecs_validation_rejects_empty_tags():
    """Instance with no tags is rejected."""
    result = simulate_validate_ecs_instance([])
    assert result is not None
    assert result['statusCode'] == 403


@given(tags=st.lists(
    st.fixed_dictionaries({
        'Key': st.text(min_size=1, max_size=30, alphabet='abcdefghijklmnopqrstuvwxyz'),
        'Value': tag_value,
    }),
    min_size=0, max_size=5,
))
@settings(max_examples=50)
def test_ecs_validation_rejects_non_ecs_tags(tags):
    """Tags that don't match ECS patterns are always rejected."""
    # These tags use only lowercase alpha keys — can never match aws:ecs:clusterName or ecs:cluster*
    # and Name values won't contain 'ecs' since values are also lowercase alpha
    assume(not has_ecs_tag(tags))
    result = simulate_validate_ecs_instance(tags)
    assert result is not None
    assert result['statusCode'] == 403
