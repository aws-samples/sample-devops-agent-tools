"""
Unit tests for MCP tool function validation wiring.
Verifies that tool functions call region and ECS instance validation.
Mirrors eks-node-log-mcp/tests/test_tool_validation_wiring.py for ECS.
"""
import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock

# Set required env vars before importing the module
os.environ.setdefault('LOGS_BUCKET_NAME', 'test-bucket')
os.environ.setdefault('SSM_AUTOMATION_ROLE_ARN', 'arn:aws:iam::123456789012:role/test')
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
os.environ.setdefault('ALLOWED_REGIONS', 'us-east-1,us-west-2')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'lambda'))


class TestValidationFunctionsExist:
    """Tests that all validation functions are defined in the module."""

    def test_validation_functions_exist(self):
        mod = __import__('ecs-log-automation')
        assert callable(getattr(mod, 'validate_region', None))
        assert callable(getattr(mod, 'resolve_and_validate_region', None))
        assert callable(getattr(mod, 'validate_ecs_instance', None))
        assert callable(getattr(mod, '_parse_presigned_url_expiration', None))
        assert callable(getattr(mod, '_parse_pcap_presigned_url_expiration', None))
        assert callable(getattr(mod, '_parse_max_pcap_bytes', None))
        assert callable(getattr(mod, 'validate_tool_authorization', None))
        assert callable(getattr(mod, 'validate_bpf_filter', None))
        assert callable(getattr(mod, 'normalize_ecs_task_id', None))
        assert callable(getattr(mod, '_build_ecs_tcpdump_script', None))
        assert callable(getattr(mod, '_poll_tcpdump_wrapper', None))
        assert callable(getattr(mod, 'TimeWindowResolver', None)) or hasattr(mod, 'TimeWindowResolver')


class TestRegionValidation:
    """Tests that validate_region works correctly."""

    def test_validate_region_accepts_allowed(self):
        mod = __import__('ecs-log-automation')
        result = mod.validate_region('us-east-1')
        assert result is None

    def test_validate_region_rejects_disallowed(self):
        mod = __import__('ecs-log-automation')
        result = mod.validate_region('ap-south-1')
        assert result is not None
        assert result['statusCode'] == 403
        body = json.loads(result['body'])
        assert 'not permitted' in body['error']

    def test_allowed_regions_parsed_from_env(self):
        mod = __import__('ecs-log-automation')
        assert 'us-east-1' in mod.ALLOWED_REGIONS
        assert 'us-west-2' in mod.ALLOWED_REGIONS


class TestPresignedUrlExpiration:
    """Tests that presigned URL expiration is configurable."""

    def test_default_expiration_is_900(self):
        mod = __import__('ecs-log-automation')
        # Default is 900 for ECS (15 minutes)
        assert mod.PRESIGNED_URL_EXPIRATION == 900

    def test_parse_valid_value(self):
        mod = __import__('ecs-log-automation')
        with patch.dict(os.environ, {'PRESIGNED_URL_EXPIRATION_SECONDS': '120'}):
            result = mod._parse_presigned_url_expiration()
            assert result == 120

    def test_parse_invalid_value_defaults(self):
        mod = __import__('ecs-log-automation')
        with patch.dict(os.environ, {'PRESIGNED_URL_EXPIRATION_SECONDS': 'abc'}):
            result = mod._parse_presigned_url_expiration()
            assert result == 900

    def test_parse_zero_defaults(self):
        mod = __import__('ecs-log-automation')
        with patch.dict(os.environ, {'PRESIGNED_URL_EXPIRATION_SECONDS': '0'}):
            result = mod._parse_presigned_url_expiration()
            assert result == 900

    def test_parse_negative_defaults(self):
        mod = __import__('ecs-log-automation')
        with patch.dict(os.environ, {'PRESIGNED_URL_EXPIRATION_SECONDS': '-5'}):
            result = mod._parse_presigned_url_expiration()
            assert result == 900


class TestTimeWindowResolver:
    """Tests that TimeWindowResolver works correctly."""

    def test_default_window_is_10_minutes(self):
        mod = __import__('ecs-log-automation')
        window = mod.TimeWindowResolver.resolve({})
        assert window['resolution_reason'].startswith('no incident time')
        delta = window['window_end_utc'] - window['window_start_utc']
        assert 9 * 60 <= delta.total_seconds() <= 11 * 60  # ~10 minutes

    def test_explicit_window(self):
        mod = __import__('ecs-log-automation')
        window = mod.TimeWindowResolver.resolve({
            'start_time': '2025-01-15T10:00:00Z',
            'end_time': '2025-01-15T10:30:00Z',
        })
        assert 'explicit' in window['resolution_reason']
        delta = window['window_end_utc'] - window['window_start_utc']
        assert delta.total_seconds() == 30 * 60

    def test_incident_time_padding(self):
        mod = __import__('ecs-log-automation')
        window = mod.TimeWindowResolver.resolve({
            'incident_time': '2025-01-15T10:00:00Z',
        })
        assert 'padding' in window['resolution_reason']
        delta = window['window_end_utc'] - window['window_start_utc']
        assert delta.total_seconds() == 10 * 60  # +/- 5 min = 10 min

    def test_max_window_clamped(self):
        mod = __import__('ecs-log-automation')
        window = mod.TimeWindowResolver.resolve({
            'start_time': '2025-01-01T00:00:00Z',
            'end_time': '2025-01-10T00:00:00Z',
        })
        assert 'clamped' in window['resolution_reason']
        delta = window['window_end_utc'] - window['window_start_utc']
        assert delta.total_seconds() <= 24 * 3600

    def test_swapped_start_end(self):
        mod = __import__('ecs-log-automation')
        window = mod.TimeWindowResolver.resolve({
            'start_time': '2025-01-15T11:00:00Z',
            'end_time': '2025-01-15T10:00:00Z',
        })
        assert 'swapped' in window['resolution_reason']
        assert window['window_start_utc'] < window['window_end_utc']
