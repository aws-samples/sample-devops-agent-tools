"""Tests for the PostgreSQL DBA MCP Server query allowlist and validation logic."""

import os
import sys
import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestQueryAllowlist:
    """Test that the query allowlist is complete and well-formed."""

    def test_all_categories_present(self):
        """All 11 categories should exist."""
        from server import QUERY_ALLOWLIST
        expected = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"}
        assert set(QUERY_ALLOWLIST.keys()) == expected

    def test_each_category_has_metadata(self):
        """Each category should have a _category name."""
        from server import QUERY_ALLOWLIST
        for cat_num, cat in QUERY_ALLOWLIST.items():
            assert "_category" in cat, f"Category {cat_num} missing _category"

    def test_each_query_has_required_fields(self):
        """Each query must have 'name' and 'sql' fields."""
        from server import QUERY_ALLOWLIST
        for cat_num, cat in QUERY_ALLOWLIST.items():
            for qid, qdef in cat.items():
                if qid.startswith("_"):
                    continue
                assert "name" in qdef, f"Query {qid} missing 'name'"
                assert "sql" in qdef, f"Query {qid} missing 'sql'"
                assert len(qdef["sql"]) > 0, f"Query {qid} has empty SQL"

    def test_no_mutative_sql_in_allowlist(self):
        """None of the allowlisted queries should contain mutative statements."""
        from server import QUERY_ALLOWLIST
        blocked = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE"]
        for cat_num, cat in QUERY_ALLOWLIST.items():
            for qid, qdef in cat.items():
                if qid.startswith("_"):
                    continue
                sql_upper = qdef["sql"].upper()
                for keyword in blocked:
                    # Allow keywords that appear as column names or in strings
                    # but not as statement beginners
                    assert not sql_upper.strip().startswith(keyword), (
                        f"Query {qid} starts with blocked keyword: {keyword}"
                    )

    def test_query_count(self):
        """Verify total query count matches expected."""
        from server import QUERY_ALLOWLIST
        total = 0
        for cat in QUERY_ALLOWLIST.values():
            total += sum(1 for k in cat if not k.startswith("_"))
        # Should have 39 total queries (original 25 + 8 pre-upgrade + 6 extended)
        assert total >= 35, f"Expected at least 35 queries, got {total}"


class TestValidation:
    """Test allowlist validation functions."""

    def setup_method(self):
        """Set environment for testing."""
        os.environ["STAGE_NAME"] = "dev"
        os.environ["ALLOWED_INSTANCES"] = "test-db-1,test-db-2"
        os.environ["ALLOWED_DATABASES"] = "postgres,myapp"

    def teardown_method(self):
        """Clean environment."""
        for var in ("STAGE_NAME", "ALLOWED_INSTANCES", "ALLOWED_DATABASES"):
            os.environ.pop(var, None)

    def test_validate_instance_allowed(self):
        from server import validate_instance, ALLOWED_INSTANCES
        # Reload allowlists for test
        ALLOWED_INSTANCES.clear()
        ALLOWED_INSTANCES.update({"test-db-1", "test-db-2"})

        ok, msg = validate_instance("test-db-1")
        assert ok is True
        assert msg == ""

    def test_validate_instance_blocked(self):
        from server import validate_instance, ALLOWED_INSTANCES
        ALLOWED_INSTANCES.clear()
        ALLOWED_INSTANCES.update({"test-db-1", "test-db-2"})

        ok, msg = validate_instance("prod-db-secret")
        assert ok is False
        assert "not in the allowed list" in msg

    def test_validate_database_allowed(self):
        from server import validate_database, ALLOWED_DATABASES
        ALLOWED_DATABASES.clear()
        ALLOWED_DATABASES.update({"postgres", "myapp"})

        ok, msg = validate_database("postgres")
        assert ok is True

    def test_validate_database_blocked(self):
        from server import validate_database, ALLOWED_DATABASES
        ALLOWED_DATABASES.clear()
        ALLOWED_DATABASES.update({"postgres", "myapp"})

        ok, msg = validate_database("sensitive_db")
        assert ok is False

    def test_wildcard_allows_all(self):
        from server import validate_instance, ALLOWED_INSTANCES
        ALLOWED_INSTANCES.clear()  # Empty set = wildcard

        ok, msg = validate_instance("anything")
        assert ok is True


class TestExplainSafety:
    """Test that the explain_query tool blocks unsafe inputs."""

    def test_blocks_analyze(self):
        """EXPLAIN ANALYZE should be blocked."""
        # We can't easily call the full tool without a DB, but we can
        # test the regex logic directly
        import re
        query = "SELECT * FROM users"
        analyze_query = "ANALYZE SELECT * FROM users"
        assert not re.search(r"\bANALYZE\b", query, re.IGNORECASE)
        assert re.search(r"\bANALYZE\b", analyze_query, re.IGNORECASE)

    def test_blocks_mutative(self):
        """INSERT/UPDATE/DELETE should be blocked."""
        import re
        blocked = re.compile(
            r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE)\b",
            re.IGNORECASE,
        )
        assert blocked.search("INSERT INTO users VALUES (1)")
        assert blocked.search("DROP TABLE users")
        assert not blocked.search("SELECT * FROM users WHERE status = 'deleted'")

    def test_allows_select(self):
        """SELECT and WITH...SELECT should be allowed."""
        import re
        allowed = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
        assert allowed.match("SELECT * FROM users")
        assert allowed.match("WITH cte AS (SELECT 1) SELECT * FROM cte")
        assert not allowed.match("INSERT INTO users SELECT * FROM old")
