"""Tests for rds-aidba MCP Server."""
import os, sys, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

class TestQueryAllowlist:
    def test_mysql_categories(self):
        from server import MYSQL_QUERIES
        assert set(MYSQL_QUERIES.keys()) == {"1","2","3","4","5","6","7","8","9","10"}

    def test_pg_categories(self):
        from server import PG_QUERIES
        assert set(PG_QUERIES.keys()) == {"1","2","3","4","5","6","7","8","9","10"}

    def test_no_mutative_sql(self):
        from server import MYSQL_QUERIES, PG_QUERIES
        blocked = ["INSERT","UPDATE","DELETE","DROP","CREATE","ALTER","TRUNCATE"]
        for queries in [MYSQL_QUERIES, PG_QUERIES]:
            for cat in queries.values():
                for qid, qdef in cat.items():
                    if qid.startswith("_"): continue
                    for kw in blocked:
                        assert not qdef["sql"].upper().strip().startswith(kw)

    def test_mysql_count(self):
        from server import MYSQL_QUERY_COUNT
        assert MYSQL_QUERY_COUNT == 24

    def test_pg_count(self):
        from server import PG_QUERY_COUNT
        assert PG_QUERY_COUNT == 30

class TestValidation:
    def test_validate_cluster(self):
        from server import validate_cluster, ALLOWED_CLUSTERS
        ALLOWED_CLUSTERS.clear()
        ALLOWED_CLUSTERS.update({"test-cluster"})
        assert validate_cluster("test-cluster")[0] is True
        assert validate_cluster("other")[0] is False

    def test_validate_instance(self):
        from server import validate_instance, ALLOWED_CLUSTERS
        ALLOWED_CLUSTERS.clear()
        ALLOWED_CLUSTERS.update({"my-cluster"})
        assert validate_instance("my-cluster-instance-1")[0] is True
        assert validate_instance("other-instance")[0] is False

    def test_validate_proxy(self):
        from server import validate_proxy, ALLOWED_CLUSTERS
        ALLOWED_CLUSTERS.clear()
        ALLOWED_CLUSTERS.update({"my-proxy"})
        assert validate_proxy("my-proxy")[0] is True
        assert validate_proxy("other")[0] is False

    def test_wildcard_allows_all(self):
        from server import validate_cluster, ALLOWED_CLUSTERS
        ALLOWED_CLUSTERS.clear()
        assert validate_cluster("anything")[0] is True
