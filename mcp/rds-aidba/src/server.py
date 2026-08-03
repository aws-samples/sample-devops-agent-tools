"""
rds-aidba MCP Server — Comprehensive Database Health Diagnostics

Custom MCP server for AWS DevOps Agent providing query-allowlisted diagnostic
access to Aurora MySQL, RDS MySQL, Aurora PostgreSQL, and RDS PostgreSQL.
Includes CloudWatch metrics, Performance Insights, RDS Proxy, and Serverless v2.

Engines: Aurora MySQL, RDS MySQL, Aurora PostgreSQL, RDS PostgreSQL
Queries: 54 predefined (24 MySQL + 30 PostgreSQL) across 10 categories
Data Sources: CloudWatch, Performance Insights, RDS Data API
Transport: Streamable HTTP (Lambda Web Adapter + FastMCP)

Safety:
  + Query allowlist only — no dynamic SQL
  + Cluster/database allowlists (defense-in-depth)
  + Production enforcement blocks wildcards
  + Read-only: no DDL, DML, or DCL
  x No arbitrary query execution

Author: Kiran Mayee Mulupuru, Sr. Specialist Database TAM, AWS Enterprise Support
"""

import os
import json
import logging
from datetime import datetime, timedelta
import boto3
from fastmcp import FastMCP

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# =============================================================================
# CONFIGURATION
# =============================================================================

CLUSTER_ARN = os.environ.get("CLUSTER_ARN", "")
SECRET_ARN = os.environ.get("SECRET_ARN", "")
DATABASE = os.environ.get("DATABASE_NAME", "information_schema")
REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
STAGE = os.environ.get("STAGE_NAME", "dev")

rds_data = boto3.client("rds-data", region_name=REGION)
rds_client = boto3.client("rds", region_name=REGION)
cloudwatch = boto3.client("cloudwatch", region_name=REGION)
pi_client = boto3.client("pi", region_name=REGION)

# =============================================================================
# ALLOWLIST VALIDATION
# =============================================================================


def _load_allowlist(env_var: str) -> set:
    raw = os.environ.get(env_var, "*").strip()
    if raw == "*":
        return set()
    return {v.strip().lower() for v in raw.split(",") if v.strip()}


def _enforce_prod_allowlists():
    if STAGE != "prod":
        return
    wildcards = [v for v in ("ALLOWED_CLUSTERS", "ALLOWED_DATABASES")
                 if os.environ.get(v, "*").strip() == "*"]
    if wildcards:
        raise RuntimeError(f"SECURITY: Prod requires explicit allowlists. Wildcards: {', '.join(wildcards)}")


_enforce_prod_allowlists()
ALLOWED_CLUSTERS = _load_allowlist("ALLOWED_CLUSTERS")
ALLOWED_DATABASES = _load_allowlist("ALLOWED_DATABASES")


def validate_cluster(cluster_id: str) -> tuple:
    if not ALLOWED_CLUSTERS:
        return True, ""
    if cluster_id.lower() not in ALLOWED_CLUSTERS:
        return False, f"ERROR: Cluster '{cluster_id}' not in allowed list."
    return True, ""


def validate_database(db: str) -> tuple:
    if not ALLOWED_DATABASES:
        return True, ""
    if db.lower() not in ALLOWED_DATABASES:
        return False, f"ERROR: Database '{db}' not in allowed list."
    return True, ""


def validate_instance(instance_id: str) -> tuple:
    if not ALLOWED_CLUSTERS:
        return True, ""
    for cluster in ALLOWED_CLUSTERS:
        if cluster in instance_id.lower():
            return True, ""
    return False, f"ERROR: Instance '{instance_id}' not associated with allowed clusters."


def validate_proxy(proxy_name: str) -> tuple:
    if not ALLOWED_CLUSTERS:
        return True, ""
    if proxy_name.lower() in ALLOWED_CLUSTERS:
        return True, ""
    return False, f"ERROR: Proxy '{proxy_name}' not in allowed list."


# =============================================================================
# RDS DATA API EXECUTION
# =============================================================================


def _execute_sql(sql: str, database: str = None) -> dict:
    """Execute read-only SQL via RDS Data API."""
    db = database or DATABASE
    ok, msg = validate_database(db)
    if not ok:
        return {"success": False, "error": msg, "columns": [], "rows": [], "rowCount": 0}
    try:
        response = rds_data.execute_statement(
            resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=db, sql=sql,
        )
        columns = [col["name"] for col in response.get("columnMetadata", [])]
        rows = []
        for record in response.get("records", []):
            row = {}
            for i, field in enumerate(record):
                col_name = columns[i] if i < len(columns) else f"col_{i}"
                if "stringValue" in field:
                    row[col_name] = field["stringValue"]
                elif "longValue" in field:
                    row[col_name] = field["longValue"]
                elif "doubleValue" in field:
                    row[col_name] = field["doubleValue"]
                elif "booleanValue" in field:
                    row[col_name] = field["booleanValue"]
                elif "isNull" in field and field["isNull"]:
                    row[col_name] = None
                else:
                    row[col_name] = str(field)
            rows.append(row)
        return {"success": True, "columns": columns, "rows": rows, "rowCount": len(rows)}
    except Exception as e:
        return {"success": False, "error": str(e), "columns": [], "rows": [], "rowCount": 0}


def _format_table(result: dict) -> str:
    """Format as markdown table."""
    if not result["success"]:
        return f"ERROR: {result['error']}"
    if result["rowCount"] == 0:
        return "Query returned 0 rows."
    columns = result["columns"]
    rows = result["rows"]
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    data = []
    for row in rows[:50]:
        vals = [str(row.get(c, "NULL"))[:100] for c in columns]
        data.append("| " + " | ".join(vals) + " |")
    out = "\n".join([header, sep] + data)
    if result["rowCount"] > 50:
        out += f"\n\n*Showing 50 of {result['rowCount']} rows.*"
    return out



# =============================================================================
# MYSQL QUERY ALLOWLIST — 24 queries across 10 categories
# =============================================================================

MYSQL_QUERIES = {
    "1": {"_category": "Server Information",
        "1.1": {"name": "Server Info", "sql": "SELECT @@version AS version, @@version_comment AS version_comment, @@hostname AS hostname, @@port AS port, @@max_connections AS max_connections, (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Threads_connected') AS threads_connected, (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Uptime') AS uptime_seconds"},
        "1.2": {"name": "Environment Detection", "sql": "SELECT @@version AS version, @@version_comment AS version_comment, @@hostname AS hostname, ROUND(@@innodb_buffer_pool_size/(1024*1024*1024), 2) AS buffer_pool_gb"},
    },
    "2": {"_category": "System Configuration",
        "2.1": {"name": "Critical Variables", "sql": "SELECT @@max_connections AS max_connections, ROUND(@@innodb_buffer_pool_size/(1024*1024*1024), 2) AS buffer_pool_gb, @@innodb_flush_log_at_trx_commit AS flush_at_commit, @@slow_query_log AS slow_log_enabled, @@long_query_time AS slow_query_threshold, @@performance_schema AS perf_schema_enabled, @@wait_timeout AS wait_timeout"},
        "2.2": {"name": "Buffer Pool Status", "sql": "SELECT ROUND((1 - ((SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads') / NULLIF((SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests'), 0))) * 100, 2) AS hit_ratio_pct, (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_dirty') AS dirty_pages, (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_total') AS total_pages, (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_free') AS free_pages"},
    },
    "3": {"_category": "Current Activity",
        "3.1": {"name": "Connection Overview", "sql": "SELECT (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Threads_connected') AS threads_connected, (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Threads_running') AS threads_running, (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Max_used_connections') AS max_used_connections, @@max_connections AS max_connections, ROUND((SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Threads_connected') / @@max_connections * 100, 1) AS utilization_pct"},
        "3.2": {"name": "Thread Details", "sql": "SELECT PROCESSLIST_ID AS id, PROCESSLIST_USER AS user, PROCESSLIST_HOST AS host, PROCESSLIST_DB AS db, PROCESSLIST_COMMAND AS command, PROCESSLIST_TIME AS time_seconds, PROCESSLIST_STATE AS state, LEFT(PROCESSLIST_INFO, 200) AS query_text FROM performance_schema.threads WHERE TYPE = 'FOREGROUND' AND PROCESSLIST_COMMAND != 'Sleep' AND PROCESSLIST_TIME > 1 ORDER BY PROCESSLIST_TIME DESC LIMIT 20"},
        "3.3": {"name": "Active Transactions", "sql": "SELECT trx_id, trx_state, trx_started, TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS duration_seconds, trx_rows_locked, trx_rows_modified, trx_isolation_level, LEFT(trx_query, 200) AS current_query FROM information_schema.innodb_trx ORDER BY trx_started ASC LIMIT 20"},
        "3.4": {"name": "Lock Waits", "sql": "SELECT r.trx_id AS waiting_trx_id, r.trx_mysql_thread_id AS waiting_thread, LEFT(r.trx_query, 150) AS waiting_query, b.trx_id AS blocking_trx_id, b.trx_mysql_thread_id AS blocking_thread, LEFT(b.trx_query, 150) AS blocking_query, TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_seconds FROM information_schema.innodb_lock_waits w INNER JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id INNER JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id ORDER BY wait_seconds DESC LIMIT 10"},
    },
    "4": {"_category": "Replication Status",
        "4.1": {"name": "Aurora Replica Lag", "sql": "SELECT SERVER_ID, SESSION_ID, LAST_UPDATE_TIMESTAMP, REPLICA_LAG_IN_MILLISECONDS, CPU FROM mysql.ro_replica_status"},
    },
    "5": {"_category": "Storage Capacity",
        "5.1": {"name": "Database Sizes", "sql": "SELECT table_schema AS database_name, ROUND(SUM(data_length + index_length) / (1024*1024*1024), 2) AS size_gb, COUNT(*) AS table_count FROM information_schema.tables WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys') GROUP BY table_schema ORDER BY size_gb DESC"},
        "5.2": {"name": "Top 10 Tables", "sql": "SELECT table_schema, table_name, ROUND((data_length + index_length) / (1024*1024), 2) AS total_mb, ROUND(data_length / (1024*1024), 2) AS data_mb, ROUND(index_length / (1024*1024), 2) AS index_mb, table_rows AS estimated_rows FROM information_schema.tables WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys') ORDER BY (data_length + index_length) DESC LIMIT 10"},
        "5.3": {"name": "Fragmentation", "sql": "SELECT table_schema, table_name, ROUND(data_free / (1024*1024), 2) AS fragmented_mb, ROUND(data_length / (1024*1024), 2) AS data_mb, ROUND((data_free / (data_length + 1)) * 100, 1) AS fragmentation_pct FROM information_schema.tables WHERE data_free > 0 AND table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys') AND (data_free / (data_length + 1)) > 0.1 ORDER BY data_free DESC LIMIT 10"},
    },
    "6": {"_category": "Performance Metrics",
        "6.1": {"name": "Top Queries by Time", "sql": "SELECT LEFT(DIGEST_TEXT, 200) AS query_pattern, COUNT_STAR AS exec_count, ROUND(SUM_TIMER_WAIT / 1000000000000, 2) AS total_time_sec, ROUND(AVG_TIMER_WAIT / 1000000000000, 4) AS avg_time_sec, SUM_ROWS_EXAMINED AS rows_examined, SUM_ROWS_SENT AS rows_sent, ROUND(SUM_ROWS_EXAMINED / NULLIF(SUM_ROWS_SENT, 0), 0) AS exam_to_sent_ratio FROM performance_schema.events_statements_summary_by_digest WHERE SCHEMA_NAME IS NOT NULL ORDER BY SUM_TIMER_WAIT DESC LIMIT 10"},
        "6.2": {"name": "CPU Intensive Queries", "sql": "SELECT LEFT(DIGEST_TEXT, 200) AS query_pattern, COUNT_STAR AS exec_count, SUM_SORT_ROWS AS sort_rows, SUM_CREATED_TMP_TABLES AS tmp_tables, SUM_CREATED_TMP_DISK_TABLES AS tmp_disk_tables, SUM_NO_INDEX_USED AS no_index_used FROM performance_schema.events_statements_summary_by_digest WHERE (SUM_SORT_ROWS > 10000 OR SUM_CREATED_TMP_DISK_TABLES > 0 OR SUM_NO_INDEX_USED > 0) ORDER BY (SUM_SORT_ROWS + SUM_CREATED_TMP_DISK_TABLES * 10000) DESC LIMIT 10"},
        "6.3": {"name": "I/O Intensive Queries", "sql": "SELECT LEFT(DIGEST_TEXT, 200) AS query_pattern, COUNT_STAR AS exec_count, SUM_ROWS_EXAMINED AS total_rows_examined, SUM_ROWS_SENT AS total_rows_sent, ROUND(SUM_ROWS_EXAMINED / NULLIF(COUNT_STAR, 0), 0) AS avg_rows_per_exec, ROUND(SUM_TIMER_WAIT / 1000000000000, 2) AS total_time_sec FROM performance_schema.events_statements_summary_by_digest WHERE SUM_ROWS_EXAMINED > 100000 ORDER BY SUM_ROWS_EXAMINED DESC LIMIT 10"},
        "6.4": {"name": "Index Usage Stats", "sql": "SELECT OBJECT_SCHEMA AS schema_name, OBJECT_NAME AS table_name, COUNT_READ AS total_reads, COUNT_FETCH AS index_reads, COUNT_READ - COUNT_FETCH AS full_scans, ROUND((COUNT_READ - COUNT_FETCH) / NULLIF(COUNT_READ, 0) * 100, 1) AS full_scan_pct FROM performance_schema.table_io_waits_summary_by_table WHERE OBJECT_SCHEMA NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys') AND COUNT_READ > 1000 ORDER BY (COUNT_READ - COUNT_FETCH) DESC LIMIT 10"},
    },
    "7": {"_category": "Maintenance Health",
        "7.1": {"name": "Auto-Increment Capacity", "sql": "SELECT table_schema, table_name, table_rows AS estimated_rows, auto_increment, ROUND((auto_increment / 2147483647) * 100, 2) AS auto_inc_usage_pct FROM information_schema.tables WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys') AND auto_increment IS NOT NULL AND (auto_increment / 2147483647) > 0.5 ORDER BY auto_inc_usage_pct DESC LIMIT 10"},
    },
    "8": {"_category": "Index Optimization",
        "8.1": {"name": "Redundant Indexes", "sql": "SELECT table_schema, table_name, redundant_index_name, redundant_index_columns, dominant_index_name, dominant_index_columns, sql_drop_index FROM sys.schema_redundant_indexes WHERE table_schema NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys') LIMIT 20"},
        "8.2": {"name": "Unused Indexes", "sql": "SELECT object_schema AS schema_name, object_name AS table_name, index_name, ROUND(stat_value * @@innodb_page_size / (1024*1024), 2) AS index_size_mb FROM mysql.innodb_index_stats s JOIN performance_schema.table_io_waits_summary_by_index_usage t ON s.table_name = t.OBJECT_NAME AND s.index_name = t.INDEX_NAME AND s.database_name = t.OBJECT_SCHEMA WHERE t.COUNT_STAR = 0 AND t.INDEX_NAME IS NOT NULL AND t.INDEX_NAME != 'PRIMARY' AND s.stat_name = 'size' AND t.OBJECT_SCHEMA NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys') ORDER BY stat_value DESC LIMIT 20"},
    },
    "9": {"_category": "Health Score",
        "9.1": {"name": "Composite Score (40pts)", "sql": "SELECT CASE WHEN (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Threads_connected') / @@max_connections < 0.8 THEN 5 ELSE 0 END AS connection_score, CASE WHEN (1 - ((SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads') / NULLIF((SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests'), 0))) > 0.99 THEN 5 ELSE 0 END AS buffer_pool_score, 5 AS replication_score, CASE WHEN (SELECT COUNT(*) FROM information_schema.innodb_trx WHERE TIMESTAMPDIFF(SECOND, trx_started, NOW()) > 300) = 0 THEN 5 ELSE 0 END AS lock_score, CASE WHEN @@slow_query_log = 1 THEN 5 ELSE 0 END AS monitoring_score, CASE WHEN (SELECT COUNT(*) FROM information_schema.tables WHERE data_free > 0 AND (data_free/(data_length+1)) > 0.2 AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys')) < 5 THEN 5 ELSE 0 END AS storage_score, CASE WHEN (SELECT COUNT(*) FROM performance_schema.table_io_waits_summary_by_index_usage WHERE COUNT_STAR = 0 AND INDEX_NAME IS NOT NULL AND INDEX_NAME != 'PRIMARY' AND OBJECT_SCHEMA NOT IN ('mysql','performance_schema','information_schema','sys')) < 10 THEN 5 ELSE 0 END AS index_score, CASE WHEN @@performance_schema = 1 THEN 5 ELSE 0 END AS instrumentation_score"},
    },
    "10": {"_category": "SQL Tuning",
        "10.1": {"name": "Queries with Temp Disk Tables", "sql": "SELECT LEFT(DIGEST_TEXT, 200) AS query_pattern, COUNT_STAR AS exec_count, SUM_CREATED_TMP_DISK_TABLES AS disk_tmp_tables, SUM_CREATED_TMP_TABLES AS memory_tmp_tables, ROUND(SUM_TIMER_WAIT / 1000000000000, 2) AS total_time_sec FROM performance_schema.events_statements_summary_by_digest WHERE SUM_CREATED_TMP_DISK_TABLES > 0 ORDER BY SUM_CREATED_TMP_DISK_TABLES DESC LIMIT 10"},
        "10.2": {"name": "Full Table Scan Queries", "sql": "SELECT LEFT(DIGEST_TEXT, 200) AS query_pattern, COUNT_STAR AS exec_count, SUM_NO_INDEX_USED AS no_index_count, SUM_ROWS_EXAMINED AS rows_examined, ROUND(SUM_TIMER_WAIT / 1000000000000, 2) AS total_time_sec FROM performance_schema.events_statements_summary_by_digest WHERE SUM_NO_INDEX_USED > 100 ORDER BY SUM_NO_INDEX_USED DESC LIMIT 10"},
        "10.3": {"name": "High Lock Time Queries", "sql": "SELECT LEFT(DIGEST_TEXT, 200) AS query_pattern, COUNT_STAR AS exec_count, ROUND(SUM_LOCK_TIME / 1000000000000, 4) AS total_lock_time_sec, ROUND(SUM_LOCK_TIME / NULLIF(COUNT_STAR, 0) / 1000000000000, 6) AS avg_lock_time_sec, SUM_ROWS_AFFECTED AS rows_affected FROM performance_schema.events_statements_summary_by_digest WHERE SUM_LOCK_TIME > 1000000000 ORDER BY SUM_LOCK_TIME DESC LIMIT 10"},
        "10.4": {"name": "Queries Needing Optimization", "sql": "SELECT LEFT(DIGEST_TEXT, 200) AS query_pattern, COUNT_STAR AS exec_count, ROUND(SUM_TIMER_WAIT / 1000000000000, 2) AS total_time_sec, SUM_ROWS_EXAMINED AS rows_examined, SUM_ROWS_SENT AS rows_sent, SUM_CREATED_TMP_DISK_TABLES AS disk_tmp, SUM_NO_INDEX_USED AS no_index, ROUND(SUM_ROWS_EXAMINED / NULLIF(SUM_ROWS_SENT, 0), 0) AS efficiency_ratio FROM performance_schema.events_statements_summary_by_digest WHERE SCHEMA_NAME IS NOT NULL AND (SUM_CREATED_TMP_DISK_TABLES > 10 OR SUM_NO_INDEX_USED > 100 OR SUM_ROWS_EXAMINED / NULLIF(SUM_ROWS_SENT, 0) > 1000) ORDER BY SUM_TIMER_WAIT DESC LIMIT 10"},
    },
}

MYSQL_QUERY_COUNT = sum(1 for cat in MYSQL_QUERIES.values() for k in cat if not k.startswith("_"))


# =============================================================================
# POSTGRESQL QUERY ALLOWLIST — 30 queries across 10 categories
# =============================================================================

PG_QUERIES = {
    "1": {"_category": "Server Information",
        "1.1": {"name": "Server Info", "sql": "SELECT version() AS version, current_database() AS database, inet_server_addr() AS server_addr, inet_server_port() AS port, current_setting('max_connections') AS max_connections, (SELECT count(*) FROM pg_stat_activity) AS current_connections, EXTRACT(EPOCH FROM (now() - pg_postmaster_start_time()))::int AS uptime_seconds"},
        "1.2": {"name": "Database Sizes", "sql": "SELECT datname AS database_name, pg_size_pretty(pg_database_size(datname)) AS size, pg_database_size(datname) AS size_bytes FROM pg_database WHERE datname NOT IN ('template0', 'template1', 'rdsadmin') ORDER BY pg_database_size(datname) DESC"},
    },
    "2": {"_category": "System Configuration",
        "2.1": {"name": "Key Parameters", "sql": "SELECT name, setting, unit, short_desc FROM pg_settings WHERE name IN ('shared_buffers', 'work_mem', 'maintenance_work_mem', 'effective_cache_size', 'max_connections', 'max_worker_processes', 'max_parallel_workers', 'random_page_cost', 'seq_page_cost', 'autovacuum', 'autovacuum_vacuum_scale_factor', 'autovacuum_analyze_scale_factor', 'log_min_duration_statement', 'shared_preload_libraries') ORDER BY name"},
        "2.2": {"name": "Memory Settings", "sql": "SELECT name, setting, unit, CASE WHEN name = 'shared_buffers' AND setting::bigint < 131072 THEN 'WARNING: < 1GB' WHEN name = 'work_mem' AND setting::bigint < 4096 THEN 'INFO: < 4MB' WHEN name = 'effective_cache_size' AND setting::bigint < 524288 THEN 'WARNING: < 4GB' ELSE 'OK' END AS status FROM pg_settings WHERE name IN ('shared_buffers', 'work_mem', 'maintenance_work_mem', 'effective_cache_size', 'temp_buffers')"},
    },
    "3": {"_category": "Current Activity",
        "3.1": {"name": "Active Sessions", "sql": "SELECT pid, usename, datname, state, wait_event_type, wait_event, EXTRACT(EPOCH FROM (now() - query_start))::int AS duration_seconds, LEFT(query, 200) AS query_text FROM pg_stat_activity WHERE state != 'idle' AND pid != pg_backend_pid() AND usename NOT IN ('rdsadmin', 'rdsrepladmin') ORDER BY query_start ASC LIMIT 20"},
        "3.2": {"name": "Connection State Breakdown", "sql": "SELECT state, count(*) AS count, ROUND(count(*)::numeric / (SELECT count(*) FROM pg_stat_activity) * 100, 1) AS pct FROM pg_stat_activity WHERE usename NOT IN ('rdsadmin', 'rdsrepladmin') GROUP BY state ORDER BY count DESC"},
        "3.3": {"name": "Idle-in-Transaction", "sql": "SELECT pid, usename, datname, EXTRACT(EPOCH FROM (now() - state_change))::int AS idle_seconds, LEFT(query, 200) AS last_query FROM pg_stat_activity WHERE state = 'idle in transaction' AND usename NOT IN ('rdsadmin') ORDER BY state_change ASC LIMIT 10"},
        "3.4": {"name": "Lock Waits", "sql": "SELECT blocked_locks.pid AS blocked_pid, blocked_activity.usename AS blocked_user, LEFT(blocked_activity.query, 200) AS blocked_query, blocking_locks.pid AS blocking_pid, blocking_activity.usename AS blocking_user, LEFT(blocking_activity.query, 200) AS blocking_query, EXTRACT(EPOCH FROM (now() - blocked_activity.query_start))::int AS wait_seconds FROM pg_catalog.pg_locks blocked_locks JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid JOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation AND blocking_locks.pid != blocked_locks.pid JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid WHERE NOT blocked_locks.granted ORDER BY wait_seconds DESC LIMIT 10"},
    },
    "4": {"_category": "Replication Status",
        "4.1": {"name": "Replication Lag", "sql": "SELECT client_addr, state, sent_lsn, write_lsn, flush_lsn, replay_lsn, EXTRACT(EPOCH FROM write_lag)::int AS write_lag_sec, EXTRACT(EPOCH FROM replay_lag)::int AS replay_lag_sec FROM pg_stat_replication"},
        "4.2": {"name": "Replication Slots", "sql": "SELECT slot_name, plugin, slot_type, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS slot_lag FROM pg_replication_slots"},
    },
    "5": {"_category": "Storage & Bloat",
        "5.1": {"name": "Top 10 Tables", "sql": "SELECT schemaname || '.' || relname AS table_name, pg_size_pretty(pg_total_relation_size(relid)) AS total_size, pg_size_pretty(pg_relation_size(relid)) AS data_size, pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) AS index_size, n_live_tup AS row_count FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10"},
        "5.2": {"name": "Table Bloat", "sql": "SELECT schemaname, relname AS table_name, n_live_tup, n_dead_tup, ROUND(n_dead_tup::numeric / NULLIF(n_live_tup, 0), 3) AS bloat_ratio, last_autovacuum, last_autoanalyze FROM pg_stat_user_tables WHERE n_dead_tup > 10000 ORDER BY n_dead_tup DESC LIMIT 10"},
        "5.3": {"name": "Tablespace Usage", "sql": "SELECT spcname AS tablespace, pg_size_pretty(pg_tablespace_size(spcname)) AS size FROM pg_tablespace ORDER BY pg_tablespace_size(spcname) DESC"},
    },
    "6": {"_category": "Performance",
        "6.1": {"name": "Top Queries by Time", "sql": "SELECT LEFT(query, 200) AS query_text, calls, ROUND(total_exec_time::numeric / 1000, 2) AS total_time_sec, ROUND(mean_exec_time::numeric / 1000, 4) AS avg_time_sec, rows, ROUND((shared_blks_hit::numeric / NULLIF(shared_blks_hit + shared_blks_read, 0)) * 100, 2) AS cache_hit_pct FROM pg_stat_statements WHERE userid != (SELECT usesysid FROM pg_user WHERE usename = 'rdsadmin') AND query NOT LIKE '%pg_stat_statements%' ORDER BY total_exec_time DESC LIMIT 10"},
        "6.2": {"name": "Top Queries by I/O", "sql": "SELECT LEFT(query, 200) AS query_text, calls, shared_blks_read AS disk_reads, shared_blks_hit AS cache_hits, ROUND((shared_blks_hit::numeric / NULLIF(shared_blks_hit + shared_blks_read, 0)) * 100, 2) AS cache_hit_pct, ROUND(total_exec_time::numeric / 1000, 2) AS total_time_sec FROM pg_stat_statements WHERE userid != (SELECT usesysid FROM pg_user WHERE usename = 'rdsadmin') AND shared_blks_read > 0 ORDER BY shared_blks_read DESC LIMIT 10"},
        "6.3": {"name": "Cache Hit Ratio by Table", "sql": "SELECT schemaname || '.' || relname AS table_name, heap_blks_hit, heap_blks_read, ROUND(heap_blks_hit::numeric / NULLIF(heap_blks_hit + heap_blks_read, 0) * 100, 2) AS hit_ratio_pct FROM pg_statio_user_tables WHERE heap_blks_hit + heap_blks_read > 1000 ORDER BY hit_ratio_pct ASC LIMIT 10"},
        "6.4": {"name": "Temp File Usage", "sql": "SELECT LEFT(query, 200) AS query_text, calls, temp_blks_written AS temp_blocks, pg_size_pretty(temp_blks_written * 8192) AS temp_size, ROUND(total_exec_time::numeric / 1000, 2) AS total_time_sec FROM pg_stat_statements WHERE temp_blks_written > 0 AND userid != (SELECT usesysid FROM pg_user WHERE usename = 'rdsadmin') ORDER BY temp_blks_written DESC LIMIT 10"},
        "6.5": {"name": "Sequential Scan Ratio", "sql": "SELECT schemaname || '.' || relname AS table_name, seq_scan, idx_scan, ROUND(seq_scan::numeric / NULLIF(seq_scan + idx_scan, 0) * 100, 1) AS seq_scan_pct, seq_tup_read, idx_tup_fetch FROM pg_stat_user_tables WHERE seq_scan + idx_scan > 100 ORDER BY seq_scan_pct DESC LIMIT 10"},
    },
    "7": {"_category": "Vacuum & Maintenance",
        "7.1": {"name": "Vacuum Status", "sql": "SELECT schemaname || '.' || relname AS table_name, pg_size_pretty(pg_total_relation_size(relid)) AS size, n_live_tup, n_dead_tup, last_autovacuum, last_autoanalyze, EXTRACT(EPOCH FROM (now() - COALESCE(last_autovacuum, '1970-01-01')))::int / 86400 AS days_since_vacuum FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10"},
        "7.2": {"name": "Transaction ID Age", "sql": "SELECT datname, age(datfrozenxid) AS txid_age, ROUND(age(datfrozenxid)::numeric / 2147483647 * 100, 2) AS wraparound_pct FROM pg_database WHERE datname NOT IN ('template0', 'template1', 'rdsadmin') ORDER BY age(datfrozenxid) DESC"},
        "7.3": {"name": "Aged Tables (Wraparound)", "sql": "SELECT schemaname || '.' || relname AS table_name, age(relfrozenxid) AS xid_age, ROUND(age(relfrozenxid)::numeric / 2147483647 * 100, 2) AS wraparound_pct, pg_size_pretty(pg_total_relation_size(relid)) AS size FROM pg_stat_user_tables ORDER BY age(relfrozenxid) DESC LIMIT 10"},
        "7.4": {"name": "Autovacuum Queue", "sql": "SELECT schemaname || '.' || relname AS table_name, n_dead_tup, n_live_tup, ROUND(n_dead_tup::numeric / NULLIF(n_live_tup, 0) * 100, 1) AS dead_pct, last_autovacuum FROM pg_stat_user_tables WHERE n_dead_tup > (n_live_tup * current_setting('autovacuum_vacuum_scale_factor')::numeric + current_setting('autovacuum_vacuum_threshold')::numeric) ORDER BY n_dead_tup DESC LIMIT 10"},
    },
    "8": {"_category": "Index Optimization",
        "8.1": {"name": "Unused Indexes", "sql": "SELECT schemaname || '.' || relname AS table_name, indexrelname AS index_name, pg_size_pretty(pg_relation_size(indexrelid)) AS index_size, idx_scan AS scans_since_reset FROM pg_stat_user_indexes WHERE idx_scan = 0 AND indexrelname NOT LIKE '%pkey%' AND indexrelname NOT LIKE '%_pk' AND pg_relation_size(indexrelid) > 8192 ORDER BY pg_relation_size(indexrelid) DESC LIMIT 20"},
        "8.2": {"name": "Duplicate Indexes", "sql": "SELECT pg_size_pretty(sum(pg_relation_size(idx))::bigint) AS total_wasted, (array_agg(idx))[1] AS index_to_keep, array_remove(array_agg(idx), (array_agg(idx))[1]) AS indexes_to_drop, indrelid::regclass AS table_name FROM (SELECT indexrelid::regclass AS idx, indrelid, indkey FROM pg_index WHERE indisvalid) sub GROUP BY indrelid, indkey HAVING count(*) > 1 ORDER BY sum(pg_relation_size(idx)) DESC LIMIT 10"},
        "8.3": {"name": "Missing FK Indexes", "sql": "SELECT conrelid::regclass AS table_name, conname AS constraint_name, a.attname AS column_name FROM pg_constraint c JOIN pg_attribute a ON a.attnum = ANY(c.conkey) AND a.attrelid = c.conrelid WHERE contype = 'f' AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND a.attnum = ANY(i.indkey)) ORDER BY conrelid::regclass::text LIMIT 20"},
        "8.4": {"name": "Index Scan Ratio", "sql": "SELECT schemaname || '.' || indexrelname AS index_name, schemaname || '.' || relname AS table_name, idx_scan, pg_size_pretty(pg_relation_size(indexrelid)) AS size FROM pg_stat_user_indexes WHERE idx_scan < 10 AND pg_relation_size(indexrelid) > 1048576 ORDER BY pg_relation_size(indexrelid) DESC LIMIT 15"},
    },
    "9": {"_category": "Health Score",
        "9.1": {"name": "Composite Score (100pts)", "sql": "SELECT CASE WHEN (SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL)::float / current_setting('max_connections')::float < 0.75 THEN 10 ELSE 0 END AS connection_score, CASE WHEN (SELECT ROUND(sum(blks_hit)::numeric / NULLIF(sum(blks_hit) + sum(blks_read), 0) * 100, 2) FROM pg_stat_database WHERE datname = current_database()) > 99 THEN 10 ELSE 0 END AS cache_score, CASE WHEN (SELECT max(age(datfrozenxid)) FROM pg_database WHERE datname NOT IN ('template0','template1','rdsadmin')) < 1000000000 THEN 10 ELSE 0 END AS txid_score, CASE WHEN (SELECT count(*) FROM pg_stat_user_tables WHERE n_dead_tup::numeric / NULLIF(n_live_tup, 0) > 0.3) = 0 THEN 10 ELSE 0 END AS bloat_score, CASE WHEN current_setting('autovacuum') = 'on' THEN 10 ELSE 0 END AS vacuum_score, CASE WHEN (SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND query_start < now() - interval '5 minutes' AND usename NOT IN ('rdsadmin')) = 0 THEN 10 ELSE 0 END AS long_query_score, CASE WHEN (SELECT count(*) FROM pg_stat_user_indexes WHERE idx_scan = 0 AND indexrelname NOT LIKE '%pkey%') < 10 THEN 10 ELSE 0 END AS index_score, CASE WHEN (SELECT count(*) FROM pg_locks WHERE NOT granted) < 3 THEN 10 ELSE 0 END AS lock_score, CASE WHEN EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements') THEN 10 ELSE 0 END AS instrumentation_score, CASE WHEN current_setting('log_min_duration_statement')::int BETWEEN 0 AND 5000 THEN 10 ELSE 0 END AS logging_score"},
    },
    "10": {"_category": "SQL Tuning",
        "10.1": {"name": "High Buffer Reads", "sql": "SELECT LEFT(query, 200) AS query_text, calls, shared_blks_read + shared_blks_dirtied AS total_buffer_ops, ROUND(mean_exec_time::numeric / 1000, 4) AS avg_time_sec, rows FROM pg_stat_statements WHERE shared_blks_read > 10000 AND userid != (SELECT usesysid FROM pg_user WHERE usename = 'rdsadmin') ORDER BY shared_blks_read DESC LIMIT 10"},
        "10.2": {"name": "Inefficient Queries", "sql": "SELECT LEFT(query, 200) AS query_text, calls, rows, ROUND(rows::numeric / NULLIF(calls, 0), 0) AS rows_per_call, ROUND(total_exec_time::numeric / NULLIF(calls, 0) / 1000, 4) AS avg_sec, temp_blks_written FROM pg_stat_statements WHERE calls > 100 AND total_exec_time / NULLIF(calls, 0) > 1000 AND userid != (SELECT usesysid FROM pg_user WHERE usename = 'rdsadmin') ORDER BY total_exec_time DESC LIMIT 10"},
        "10.3": {"name": "Lock-Heavy Queries", "sql": "SELECT LEFT(query, 200) AS query_text, calls, ROUND(total_exec_time::numeric / 1000, 2) AS total_sec, rows, shared_blks_hit + shared_blks_read AS total_blocks FROM pg_stat_statements WHERE calls > 10 AND total_exec_time / NULLIF(calls, 0) > 5000 AND userid != (SELECT usesysid FROM pg_user WHERE usename = 'rdsadmin') ORDER BY total_exec_time / NULLIF(calls, 0) DESC LIMIT 10"},
    },
}

PG_QUERY_COUNT = sum(1 for cat in PG_QUERIES.values() for k in cat if not k.startswith("_"))


# =============================================================================
# MCP SERVER & TOOLS
# =============================================================================

mcp = FastMCP(
    "rds-aidba",
    instructions=(
        "Comprehensive database health diagnostics for Aurora MySQL, RDS MySQL, "
        "Aurora PostgreSQL, and RDS PostgreSQL. Provides 54 predefined health check "
        "queries (24 MySQL + 30 PostgreSQL) across 10 categories, plus CloudWatch "
        "metrics, Performance Insights, RDS Proxy health, and Serverless v2 capacity. "
        "Only allowlisted queries — no arbitrary SQL."
    ),
)


@mcp.tool()
def execute_health_query(engine: str, category: str, query_id: str) -> str:
    """
    Run a predefined health check query by engine, category, and query ID.

    Args:
        engine: "mysql" or "postgresql"
        category: Category number (1-10)
        query_id: Query ID (e.g., "3.1", "6.2")
    """
    queries = MYSQL_QUERIES if engine == "mysql" else PG_QUERIES
    if category not in queries:
        return f"ERROR: Unknown category '{category}' for {engine}. Available: {', '.join(sorted(queries.keys()))}"
    cat = queries[category]
    if query_id not in cat:
        available = [k for k in cat if not k.startswith("_")]
        return f"ERROR: Unknown query_id '{query_id}'. Available: {', '.join(available)}"
    query = cat[query_id]
    result = _execute_sql(query["sql"])
    return f"## {query_id}: {query['name']}\n**Category {category}: {cat['_category']}** | Engine: {engine}\n\n{_format_table(result)}"


@mcp.tool()
def list_health_queries(engine: str = "mysql") -> str:
    """
    List all available health check queries for an engine.

    Args:
        engine: "mysql" (24 queries) or "postgresql" (30 queries)
    """
    queries = MYSQL_QUERIES if engine == "mysql" else PG_QUERIES
    count = MYSQL_QUERY_COUNT if engine == "mysql" else PG_QUERY_COUNT
    output = f"# {engine.upper()} Health Checks ({count} queries)\n\n"
    for cat_num in sorted(queries.keys()):
        cat = queries[cat_num]
        output += f"## Category {cat_num}: {cat['_category']}\n"
        for qid, qdef in cat.items():
            if not qid.startswith("_"):
                output += f"- **{qid}**: {qdef['name']}\n"
        output += "\n"
    return output


@mcp.tool()
def run_category_check(engine: str, category: str) -> str:
    """
    Run all health checks in a category.

    Args:
        engine: "mysql" or "postgresql"
        category: Category number (1-10)
    """
    queries = MYSQL_QUERIES if engine == "mysql" else PG_QUERIES
    if category not in queries:
        return f"ERROR: Unknown category '{category}'."
    cat = queries[category]
    output = f"# Category {category}: {cat['_category']} ({engine})\n\n"
    for qid, qdef in cat.items():
        if qid.startswith("_"):
            continue
        result = _execute_sql(qdef["sql"])
        output += f"## {qid}: {qdef['name']}\n{_format_table(result)}\n\n"
    return output


@mcp.tool()
def run_full_health_check(engine: str = "mysql") -> str:
    """
    Run key queries from all categories for a comprehensive assessment.

    Args:
        engine: "mysql" or "postgresql"
    """
    if engine == "mysql":
        key_queries = ["1.1", "2.2", "3.1", "5.3", "6.1", "7.1", "8.1", "9.1", "10.4"]
    else:
        key_queries = ["1.1", "2.1", "3.1", "5.2", "6.1", "7.2", "8.1", "9.1", "10.2"]
    queries = MYSQL_QUERIES if engine == "mysql" else PG_QUERIES
    output = f"# Full Health Check ({engine})\n\n"
    for qid in key_queries:
        cat_num = qid.split(".")[0]
        cat = queries.get(cat_num, {})
        qdef = cat.get(qid)
        if not qdef:
            continue
        result = _execute_sql(qdef["sql"])
        output += f"## {qid}: {qdef['name']}\n{_format_table(result)}\n\n"
    return output


@mcp.tool()
def list_clusters() -> str:
    """List all Aurora/RDS clusters in the account with engine, version, and status."""
    try:
        response = rds_client.describe_db_clusters()
        clusters = response.get("DBClusters", [])
        if not clusters:
            return "No clusters found."
        if ALLOWED_CLUSTERS:
            clusters = [c for c in clusters if c["DBClusterIdentifier"].lower() in ALLOWED_CLUSTERS]
        if not clusters:
            return "No allowed clusters found."
        output = "| Cluster | Engine | Version | Status | Members |\n| --- | --- | --- | --- | --- |\n"
        for c in clusters:
            output += f"| {c['DBClusterIdentifier']} | {c.get('Engine', '')} | {c.get('EngineVersion', '')} | {c['Status']} | {len(c.get('DBClusterMembers', []))} |\n"
        return output
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def get_cluster_health(cluster_identifier: str) -> str:
    """
    Quick health: config, encryption, backups, monitoring, endpoints.

    Args:
        cluster_identifier: Aurora/RDS cluster identifier.
    """
    ok, msg = validate_cluster(cluster_identifier)
    if not ok:
        return msg
    try:
        c = rds_client.describe_db_clusters(DBClusterIdentifier=cluster_identifier)["DBClusters"][0]
        return f"""## Cluster: {cluster_identifier}
| Property | Value |
| --- | --- |
| Engine | {c.get('Engine')} |
| Version | {c.get('EngineVersion')} |
| Status | {c.get('Status')} |
| Encrypted | {c.get('StorageEncrypted', False)} |
| Deletion Protection | {c.get('DeletionProtection', False)} |
| Backup Retention | {c.get('BackupRetentionPeriod', 0)} days |
| IAM Auth | {c.get('IAMDatabaseAuthenticationEnabled', False)} |
| CW Logs | {', '.join(c.get('EnabledCloudwatchLogsExports', []))} |
| Multi-AZ | {len(c.get('AvailabilityZones', []))} AZs |
| Members | {len(c.get('DBClusterMembers', []))} |
| Endpoint | {c.get('Endpoint', 'N/A')} |
"""
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def get_cluster_metrics(cluster_identifier: str, hours_back: int = 3) -> str:
    """
    CloudWatch metrics: CPU, connections, memory, IOPS, replica lag, storage.

    Args:
        cluster_identifier: Cluster ID.
        hours_back: Hours of data (1-24, default 3).
    """
    ok, msg = validate_cluster(cluster_identifier)
    if not ok:
        return msg
    end = datetime.utcnow()
    start = end - timedelta(hours=min(hours_back, 24))
    metrics = [
        ("CPUUtilization", "%"), ("DatabaseConnections", "count"),
        ("FreeableMemory", "bytes"), ("ReadIOPS", "count/sec"),
        ("WriteIOPS", "count/sec"), ("AuroraReplicaLag", "ms"),
    ]
    # Get cluster members to query instance-level metrics
    try:
        cluster_resp = rds_client.describe_db_clusters(DBClusterIdentifier=cluster_identifier)
        members = cluster_resp["DBClusters"][0].get("DBClusterMembers", [])
    except Exception:
        members = [{"DBInstanceIdentifier": cluster_identifier, "IsClusterWriter": True}]

    output = f"## Metrics: {cluster_identifier} (last {hours_back}h)\n\n"
    output += "| Instance | Role | Metric | Avg | Max | Min | Unit |\n| --- | --- | --- | --- | --- | --- | --- |\n"
    for member in members:
        instance_id = member["DBInstanceIdentifier"]
        role = "Writer" if member.get("IsClusterWriter", False) else "Reader"
        for metric_name, unit in metrics:
            try:
                resp = cloudwatch.get_metric_statistics(
                    Namespace="AWS/RDS", MetricName=metric_name,
                    Dimensions=[{"Name": "DBInstanceIdentifier", "Value": instance_id}],
                    StartTime=start, EndTime=end, Period=300,
                    Statistics=["Average", "Maximum", "Minimum"],
                )
                dp = resp.get("Datapoints", [])
                if dp:
                    avg = round(sum(d["Average"] for d in dp) / len(dp), 2)
                    mx = round(max(d["Maximum"] for d in dp), 2)
                    mn = round(min(d["Minimum"] for d in dp), 2)
                    output += f"| {instance_id} | {role} | {metric_name} | {avg} | {mx} | {mn} | {unit} |\n"
            except Exception:
                pass
    if "| " not in output.split("\n")[-1]:
        output += "No metrics available.\n"
    return output


@mcp.tool()
def get_performance_insights(instance_identifier: str) -> str:
    """
    Performance Insights: DB load by wait event (last 1 hour).
    Requires Performance Insights enabled on the instance.

    Args:
        instance_identifier: DB instance identifier (not cluster).
    """
    ok, msg = validate_instance(instance_identifier)
    if not ok:
        return msg
    try:
        resource_id = f"db-{instance_identifier}"
        # Try to get the actual DbiResourceId
        try:
            inst = rds_client.describe_db_instances(DBInstanceIdentifier=instance_identifier)
            resource_id = inst["DBInstances"][0]["DbiResourceId"]
        except Exception:
            pass
        end = datetime.utcnow()
        start = end - timedelta(hours=1)
        resp = pi_client.get_resource_metrics(
            ServiceType="RDS",
            Identifier=resource_id,
            MetricQueries=[
                {"Metric": "db.load.avg", "GroupBy": {"Group": "db.wait_event", "Limit": 10}},
            ],
            StartTime=start, EndTime=end, PeriodInSeconds=300,
        )
        output = "## Performance Insights: Top Wait Events (last 1h)\n\n"
        output += "| Wait Event | Avg DB Load |\n| --- | --- |\n"
        rows = []
        for metric in resp.get("MetricList", []):
            key = metric.get("Key", {})
            dimensions = key.get("Dimensions", {})
            event = dimensions.get("db.wait_event.name", "unknown")
            values = [
                point["Value"]
                for point in metric.get("DataPoints", [])
                if isinstance(point.get("Value"), (int, float))
            ]
            if values:
                avg_load = sum(values) / len(values)
                rows.append((event, avg_load))
        if rows:
            for event, avg_load in sorted(rows, key=lambda x: x[1], reverse=True):
                output += f"| {event} | {round(avg_load, 3)} |\n"
        else:
            output += "No PI data available. Ensure Performance Insights is enabled."
        return output
    except Exception as e:
        return f"Performance Insights error: {e}\n\nEnsure PI is enabled on instance '{instance_identifier}'."


@mcp.tool()
def get_proxy_health(proxy_name: str) -> str:
    """
    RDS Proxy health: status, connections, target health.

    Args:
        proxy_name: RDS Proxy name.
    """
    ok, msg = validate_proxy(proxy_name)
    if not ok:
        return msg
    try:
        resp = rds_client.describe_db_proxies(DBProxyName=proxy_name)
        proxy = resp["DBProxies"][0]
        output = f"""## RDS Proxy: {proxy_name}
| Property | Value |
| --- | --- |
| Status | {proxy.get('Status')} |
| Engine | {proxy.get('EngineFamily')} |
| VPC | {proxy.get('VpcId')} |
| Endpoint | {proxy.get('Endpoint')} |
| Auth | {proxy.get('Auth', [{}])[0].get('AuthScheme', 'N/A')} |
| Idle Timeout | {proxy.get('IdleClientTimeout')} sec |
"""
        # Get targets
        try:
            targets = rds_client.describe_db_proxy_targets(DBProxyName=proxy_name)
            output += "\n### Targets\n| Target | Type | State | Health |\n| --- | --- | --- | --- |\n"
            for t in targets.get("Targets", []):
                output += f"| {t.get('RdsResourceId', 'N/A')} | {t.get('Type')} | {t.get('TargetHealth', {}).get('State', 'N/A')} | {t.get('TargetHealth', {}).get('Description', '')} |\n"
        except Exception:
            pass
        return output
    except Exception as e:
        return f"ERROR: {e}. Verify proxy name and IAM permissions."


@mcp.tool()
def get_serverless_capacity(cluster_identifier: str) -> str:
    """
    Aurora Serverless v2 capacity: current ACUs, min/max, scaling activity.

    Args:
        cluster_identifier: Serverless v2 cluster identifier.
    """
    ok, msg = validate_cluster(cluster_identifier)
    if not ok:
        return msg
    end = datetime.utcnow()
    start = end - timedelta(hours=1)
    output = f"## Serverless v2 Capacity: {cluster_identifier} (last 1h)\n\n"
    output += "| Metric | Avg | Max | Min |\n| --- | --- | --- | --- |\n"
    for metric in ["ServerlessDatabaseCapacity", "ACUUtilization"]:
        try:
            resp = cloudwatch.get_metric_statistics(
                Namespace="AWS/RDS", MetricName=metric,
                Dimensions=[{"Name": "DBClusterIdentifier", "Value": cluster_identifier}],
                StartTime=start, EndTime=end, Period=60,
                Statistics=["Average", "Maximum", "Minimum"],
            )
            dp = resp.get("Datapoints", [])
            if dp:
                avg = round(sum(d["Average"] for d in dp) / len(dp), 2)
                output += f"| {metric} | {avg} | {round(max(d['Maximum'] for d in dp), 2)} | {round(min(d['Minimum'] for d in dp), 2)} |\n"
            else:
                output += f"| {metric} | N/A (not Serverless v2?) | - | - |\n"
        except Exception as e:
            output += f"| {metric} | Error: {str(e)[:40]} | - | - |\n"
    return output


# =============================================================================
# ENTRY POINT
# =============================================================================

try:
    handler = mcp.streamable_http_handler()
except AttributeError:
    handler = mcp.http_app()

if __name__ == "__main__":
    mcp.run(transport="stdio")
