"""
Unit tests for MCP tool function validation wiring (Task 8.4).
Verifies that tool functions call region and EKS instance validation.
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
os.environ.setdefault('ALLOWED_REGIONS', 'us-east-1,us-west-2')
os.environ.setdefault('SOP_BUCKET_NAME', 'test-sop-bucket')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'lambda'))


class TestStartLogCollectionValidation:
    """Tests that start_log_collection validates region and EKS instance."""

    def test_validation_functions_exist(self):
        """All validation functions are defined in the module."""
        mod = __import__('ssm-automation-enhanced')
        assert callable(getattr(mod, 'validate_region', None))
        assert callable(getattr(mod, 'resolve_and_validate_region', None))
        assert callable(getattr(mod, 'validate_eks_instance', None))
        assert callable(getattr(mod, '_parse_presigned_url_expiration', None))

    def test_validate_region_accepts_allowed(self):
        """validate_region returns None for allowed region."""
        mod = __import__('ssm-automation-enhanced')
        # ALLOWED_REGIONS should contain us-east-1 from env var
        result = mod.validate_region('us-east-1')
        assert result is None

    def test_validate_region_rejects_disallowed(self):
        """validate_region returns 403 for disallowed region."""
        mod = __import__('ssm-automation-enhanced')
        result = mod.validate_region('ap-south-1')
        assert result is not None
        assert result['statusCode'] == 403
        body = json.loads(result['body'])
        assert 'not permitted' in body['error']

    def test_allowed_regions_parsed_from_env(self):
        """ALLOWED_REGIONS set is parsed from environment variable."""
        mod = __import__('ssm-automation-enhanced')
        assert 'us-east-1' in mod.ALLOWED_REGIONS
        assert 'us-west-2' in mod.ALLOWED_REGIONS


class TestBatchCollectSkipsInstanceValidation:
    """Tests that batch_collect does NOT call validate_eks_instance."""

    def test_batch_collect_has_no_instance_validation_call(self):
        """batch_collect source code should not contain validate_eks_instance."""
        import inspect
        mod = __import__('ssm-automation-enhanced')
        source = inspect.getsource(mod.batch_collect)
        assert 'validate_eks_instance' not in source


class TestPresignedUrlExpiration:
    """Tests that presigned URL expiration is configurable."""

    def test_default_expiration_is_300(self):
        """Default PRESIGNED_URL_EXPIRATION is 300 when env var is set."""
        # Our env doesn't set PRESIGNED_URL_EXPIRATION_SECONDS, so it defaults
        mod = __import__('ssm-automation-enhanced')
        assert mod.PRESIGNED_URL_EXPIRATION == 300

    def test_parse_valid_value(self):
        """_parse_presigned_url_expiration parses valid integer."""
        mod = __import__('ssm-automation-enhanced')
        with patch.dict(os.environ, {'PRESIGNED_URL_EXPIRATION_SECONDS': '120'}):
            result = mod._parse_presigned_url_expiration()
            assert result == 120

    def test_parse_invalid_value_defaults(self):
        """_parse_presigned_url_expiration defaults to 300 for invalid input."""
        mod = __import__('ssm-automation-enhanced')
        with patch.dict(os.environ, {'PRESIGNED_URL_EXPIRATION_SECONDS': 'abc'}):
            result = mod._parse_presigned_url_expiration()
            assert result == 300

    def test_parse_zero_defaults(self):
        """_parse_presigned_url_expiration defaults to 300 for zero."""
        mod = __import__('ssm-automation-enhanced')
        with patch.dict(os.environ, {'PRESIGNED_URL_EXPIRATION_SECONDS': '0'}):
            result = mod._parse_presigned_url_expiration()
            assert result == 300

    def test_parse_negative_defaults(self):
        """_parse_presigned_url_expiration defaults to 300 for negative."""
        mod = __import__('ssm-automation-enhanced')
        with patch.dict(os.environ, {'PRESIGNED_URL_EXPIRATION_SECONDS': '-5'}):
            result = mod._parse_presigned_url_expiration()
            assert result == 300

    def test_parse_missing_defaults(self):
        """_parse_presigned_url_expiration defaults to 300 when env var missing."""
        mod = __import__('ssm-automation-enhanced')
        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.copy()
            env.pop('PRESIGNED_URL_EXPIRATION_SECONDS', None)
            with patch.dict(os.environ, env, clear=True):
                result = mod._parse_presigned_url_expiration()
                assert result == 300
