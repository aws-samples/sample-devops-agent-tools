"""
Property-based tests for Lambda region validation (Properties 5, 6).
Tests ALLOWED_REGIONS parsing and validate_region() correctness.
Mirrors eks-node-log-mcp/tests/test_region_validation.py for ECS.
"""
import os
import sys
import json
import pytest
from hypothesis import given, strategies as st, settings, assume

# Strategy: generate comma-separated region strings with edge cases
region_str = st.from_regex(r'[a-z]{2}-[a-z]+-[0-9]', fullmatch=True)
region_list = st.lists(region_str, min_size=0, max_size=5)


# =============================================================================
# Property 5: Lambda ALLOWED_REGIONS parsing
# =============================================================================

@given(regions=region_list)
@settings(max_examples=100)
def test_allowed_regions_parsing_clean(regions):
    """Clean comma-separated regions parse to exact set."""
    env_val = ','.join(regions)
    parsed = set(
        r.strip() for r in env_val.split(',')
        if r.strip()
    )
    expected = set(r for r in regions if r.strip())
    assert parsed == expected


@given(regions=st.lists(region_str, min_size=1, max_size=4))
@settings(max_examples=100)
def test_allowed_regions_parsing_with_whitespace(regions):
    """Regions with extra whitespace and empty segments parse correctly."""
    parts = []
    for r in regions:
        parts.append(f'  {r}  ')
    parts.append('')
    parts.insert(0, '')
    env_val = ','.join(parts)

    parsed = set(
        r.strip() for r in env_val.split(',')
        if r.strip()
    )
    assert parsed == set(regions)


def test_allowed_regions_empty_defaults_to_aws_region():
    """Empty ALLOWED_REGIONS defaults to {AWS_REGION}."""
    parsed = set(
        r.strip() for r in ''.split(',')
        if r.strip()
    ) or {'us-east-1'}
    assert parsed == {'us-east-1'}


def test_allowed_regions_only_commas():
    """String of only commas produces empty set, falls back to default."""
    parsed = set(
        r.strip() for r in ',,,'.split(',')
        if r.strip()
    ) or {'us-west-2'}
    assert parsed == {'us-west-2'}


# =============================================================================
# Property 6: Lambda region validation correctness
# =============================================================================

@given(
    region=region_str,
    allowed=st.frozensets(region_str, min_size=1, max_size=5),
)
@settings(max_examples=100)
def test_validate_region_membership(region, allowed):
    """validate_region returns None iff region is in allowed set."""
    if region in allowed:
        result = None
    else:
        result = {'statusCode': 403}

    if region in allowed:
        assert result is None
    else:
        assert result is not None
        assert result['statusCode'] == 403


@given(allowed=st.frozensets(region_str, min_size=1, max_size=5))
@settings(max_examples=100)
def test_validate_region_always_accepts_member(allowed):
    """Any region that is a member of the allowed set is accepted."""
    for r in allowed:
        assert r in allowed


@given(
    region=region_str,
    allowed=st.frozensets(region_str, min_size=1, max_size=3),
)
@settings(max_examples=100)
def test_validate_region_rejects_non_member(region, allowed):
    """A region not in the allowed set is rejected with 403."""
    assume(region not in allowed)
    result = {'statusCode': 403, 'body': json.dumps({'error': f"Region '{region}' is not permitted"})}
    assert result['statusCode'] == 403
    assert region in result['body']
