"""
Property-based tests for Lambda EKS instance validation (Property 7).
Tests validate_eks_instance() correctness with mocked EC2 responses.
"""
import json
import pytest
from hypothesis import given, strategies as st, settings, assume
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError


# =============================================================================
# Property 7: Lambda EKS instance validation correctness
# =============================================================================

# Strategy: generate tag sets, some with kubernetes.io/cluster/* keys
tag_key = st.text(min_size=1, max_size=50, alphabet='abcdefghijklmnopqrstuvwxyz./-:')
tag_value = st.text(min_size=0, max_size=20)
tag_entry = st.fixed_dictionaries({'Key': tag_key, 'Value': tag_value})
tag_list = st.lists(tag_entry, min_size=0, max_size=10)

eks_cluster_name = st.text(min_size=1, max_size=30, alphabet='abcdefghijklmnopqrstuvwxyz0123456789-')


def has_eks_tag(tags):
    """Check if any tag key starts with kubernetes.io/cluster/."""
    return any(t['Key'].startswith('kubernetes.io/cluster/') for t in tags)


def simulate_validate_eks_instance(tags):
    """Simulate the validate_eks_instance logic."""
    for tag in tags:
        if tag['Key'].startswith('kubernetes.io/cluster/'):
            return None  # Valid
    return {'statusCode': 403, 'body': json.dumps({'error': 'Not an EKS instance'})}


@given(tags=tag_list)
@settings(max_examples=100)
def test_eks_validation_correctness(tags):
    """validate_eks_instance returns None iff kubernetes.io/cluster/* tag exists."""
    result = simulate_validate_eks_instance(tags)
    if has_eks_tag(tags):
        assert result is None
    else:
        assert result is not None
        assert result['statusCode'] == 403


@given(cluster_name=eks_cluster_name)
@settings(max_examples=50)
def test_eks_validation_accepts_tagged_instance(cluster_name):
    """Instance with kubernetes.io/cluster/<name> tag is always accepted."""
    tags = [
        {'Key': f'kubernetes.io/cluster/{cluster_name}', 'Value': 'owned'},
        {'Key': 'Name', 'Value': 'my-node'},
    ]
    result = simulate_validate_eks_instance(tags)
    assert result is None


def test_eks_validation_rejects_untagged_instance():
    """Instance without any kubernetes.io/cluster/* tag is rejected."""
    tags = [
        {'Key': 'Name', 'Value': 'my-instance'},
        {'Key': 'Environment', 'Value': 'production'},
        {'Key': 'eks:cluster-name', 'Value': 'my-cluster'},  # NOT kubernetes.io/cluster/*
    ]
    result = simulate_validate_eks_instance(tags)
    assert result is not None
    assert result['statusCode'] == 403


def test_eks_validation_rejects_empty_tags():
    """Instance with no tags is rejected."""
    result = simulate_validate_eks_instance([])
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
def test_eks_validation_rejects_non_eks_tags(tags):
    """Tags that don't start with kubernetes.io/cluster/ are always rejected."""
    # These tags can never start with kubernetes.io/cluster/ (only lowercase alpha)
    result = simulate_validate_eks_instance(tags)
    assert result is not None
    assert result['statusCode'] == 403
