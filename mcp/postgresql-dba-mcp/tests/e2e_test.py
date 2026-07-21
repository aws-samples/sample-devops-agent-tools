"""End-to-end tests for PostgreSQL DBA MCP Server.

These tests require:
- A running PostgreSQL instance (RDS or local)
- Secrets Manager secret with credentials (or direct env vars for local)
- AWS credentials configured

Run with: pytest tests/e2e_test.py -v --endpoint=<endpoint> --secret-arn=<arn>
"""

import os
import pytest


def pytest_addoption(parser):
    parser.addoption("--endpoint", action="store", default="")
    parser.addoption("--secret-arn", action="store", default="")
    parser.addoption("--database", action="store", default="postgres")
    parser.addoption("--port", action="store", default="5432")


@pytest.fixture
def endpoint(request):
    return request.config.getoption("--endpoint")


@pytest.fixture
def secret_arn(request):
    return request.config.getoption("--secret-arn")


@pytest.fixture
def database(request):
    return request.config.getoption("--database")


@pytest.fixture
def port(request):
    return int(request.config.getoption("--port"))


@pytest.mark.skipif(
    not os.environ.get("RUN_E2E_TESTS"),
    reason="Set RUN_E2E_TESTS=1 to run end-to-end tests",
)
class TestE2E:
    """End-to-end tests against a real PostgreSQL instance."""

    def test_list_health_queries(self):
        """list_health_queries should return all categories."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from server import list_health_queries

        result = list_health_queries()
        assert "Category 1" in result
        assert "Category 9" in result
        assert "Category 10" in result
        assert "Category 11" in result

    def test_execute_health_query_version(self, endpoint, secret_arn, database, port):
        """Query 1.1 should return the PostgreSQL version."""
        if not endpoint or not secret_arn:
            pytest.skip("No endpoint/secret_arn provided")

        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from server import execute_health_query

        result = execute_health_query(
            category="1",
            query_id="1.1",
            instance_endpoint=endpoint,
            database=database,
            port=port,
            secret_arn=secret_arn,
        )
        assert "PostgreSQL" in result or "version" in result.lower()

    def test_run_full_health_check(self, endpoint, secret_arn, database, port):
        """Full health check should produce a report."""
        if not endpoint or not secret_arn:
            pytest.skip("No endpoint/secret_arn provided")

        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from server import run_full_health_check

        result = run_full_health_check(
            instance_endpoint=endpoint,
            database=database,
            port=port,
            secret_arn=secret_arn,
        )
        assert "Full Health Check Report" in result
        assert "---" in result
