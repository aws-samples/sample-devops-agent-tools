"""
Shared pytest configuration for ECS Instance Log MCP tests.
Sets required environment variables before any test module imports the Lambda.
"""
import os

# These must be set before boto3 clients are created at module-level in the Lambda
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('LOGS_BUCKET_NAME', 'test-bucket')
os.environ.setdefault('SSM_AUTOMATION_ROLE_ARN', 'arn:aws:iam::123456789012:role/test')
os.environ.setdefault('ALLOWED_REGIONS', 'us-east-1,us-west-2')
os.environ.setdefault('ALLOWED_CLUSTER_NAMES', 'cluster-one,cluster-two,cluster')
