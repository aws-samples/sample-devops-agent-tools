"""
PostgreSQL DBA MCP Server for AWS DevOps Agent

A custom MCP server that provides safe, read-only diagnostic access to
Amazon RDS for PostgreSQL and Aurora PostgreSQL instances via predefined
health check queries.

Safety: Query-allowlist approach — only predefined diagnostic queries are
permitted. No dynamic SQL or arbitrary queries accepted.

Transport: Streamable HTTP (required by DevOps Agent)
Auth: SigV4 (via Lambda Function URL with AWS_IAM auth)
"""

import json
import os
import boto3
import pg8000.native
from fastmcp import FastMCP

# Initialize MCP server
mcp = FastMCP(
    "postgresql-dba-mcp",
    instructions=(
        "Read-only diagnostic access to Amazon RDS/Aurora PostgreSQL instances. "
        "Provides tools for running predefined health check queries across 9 categories: "
        "server info, system configuration, current activity, replication, storage/bloat, "
        "performance (pg_stat_statements), vacuum/maintenance, index optimization, and "
        "composite health scoring. Only allowlisted queries are permitted."
    ),
)

# AWS clients
rds_client = boto3.client("rds")
secretsmanager_client = boto3.client("secretsmanager")
ec2_client = boto3.client("ec2")
cloudwatch_client = boto3.client("cloudwatch")


# ============================================================
# Configuration & Allowlists
# ============================================================

def _load_allowlist(env_var: str) -> set[str]:
    """Load a comma-separated allowlist from an environment variable."""
    raw = os.environ.get(env_var, "*").strip()
    if raw == "*":
        return set()  # Empty set means "allow all"
    return {v.strip().lower() for v in raw.split(",") if v.strip()}


def _enforce_prod_allowlists():
    """Fail-closed: refuse to start if any allowlist is '*' in production."""
    stage = os.environ.get("STAGE_NAME", "").lower()
    if stage != "prod":
        return
    wildcards = []
    for var in ("ALLOWED_INSTANCES", "ALLOWED_DATABASES"):
        val = os.environ.get(var, "*").strip()
        if val == "*":
            wildcards.append(var)
    if wildcards:
        raise RuntimeError(
            f"SECURITY: Production deployment requires explicit allowlists. "
            f"The following are set to '*' (wildcard): {', '.join(wildcards)}. "
            f"Set each to a comma-separated list of permitted values."
        )


_enforce_prod_allowlists()

ALLOWED_INSTANCES = _load_allowlist("ALLOWED_INSTANCES")
ALLOWED_DATABASES = _load_allowlist("ALLOWED_DATABASES")


def validate_instance(instance_id: str) -> tuple[bool, str]:
    """Validate instance_id against the allowlist."""
    if not ALLOWED_INSTANCES:
        return True, ""
    if instance_id.lower() not in ALLOWED_INSTANCES:
        return False, (
            f"ERROR: Instance '{instance_id}' is not in the allowed list. "
            f"Permitted instances: {', '.join(sorted(ALLOWED_INSTANCES))}."
        )
    return True, ""


def validate_database(database: str) -> tuple[bool, str]:
    """Validate database name against the allowlist."""
    if not ALLOWED_DATABASES:
        return True, ""
    if database.lower() not in ALLOWED_DATABASES:
        return False, (
            f"ERROR: Database '{database}' is not in the allowed list. "
            f"Permitted databases: {', '.join(sorted(ALLOWED_DATABASES))}."
        )
    return True, ""


# ============================================================
# Query Allowlist — Predefined health check queries
# ============================================================

QUERY_ALLOWLIST: dict[str, dict[str, dict]] = {
    "1": {
        "_category": "Server Information",
        "1.1": {
            "name": "PostgreSQL Version",
            "sql": "SELECT version()",
        },
        "1.2": {
            "name": "Server Uptime",
            "sql": "SELECT pg_postmaster_start_time(), now() - pg_postmaster_start_time() AS uptime",
        },
        "1.3": {
            "name": "Database Size",
            "sql": (
                "SELECT datname, pg_size_pretty(pg_database_size(datname)) AS size "
                "FROM pg_database WHERE datistemplate = false ORDER BY pg_database_size(datname) DESC"
            ),
        },
    },
    "2": {
        "_category": "System Configuration",
        "2.1": {
            "name": "Key Parameters",
            "sql": (
                "SELECT name, setting, unit, source, context "
                "FROM pg_settings "
                "WHERE name IN ("
                "'shared_buffers','work_mem','maintenance_work_mem','effective_cache_size',"
                "'random_page_cost','seq_page_cost','effective_io_concurrency',"
                "'checkpoint_timeout','max_wal_size','min_wal_size','wal_buffers',"
                "'max_connections','jit','default_statistics_target',"
                "'autovacuum_vacuum_cost_delay','autovacuum_vacuum_scale_factor',"
                "'autovacuum_analyze_scale_factor','autovacuum_max_workers',"
                "'vacuum_cost_limit','idle_in_transaction_session_timeout',"
                "'statement_timeout','lock_timeout','ssl','password_encryption',"
                "'log_min_duration_statement','log_connections','log_disconnections'"
                ") ORDER BY name"
            ),
        },
        "2.2": {
            "name": "Memory Settings (Computed)",
            "sql": (
                "SELECT name, setting, unit, "
                "pg_size_pretty(setting::bigint * "
                "CASE unit WHEN '8kB' THEN 8192 WHEN 'kB' THEN 1024 "
                "WHEN 'MB' THEN 1048576 ELSE 1 END) AS pretty_value "
                "FROM pg_settings "
                "WHERE name IN ('shared_buffers','work_mem','maintenance_work_mem',"
                "'effective_cache_size','wal_buffers') ORDER BY name"
            ),
        },
    },
    "3": {
        "_category": "Current Activity",
        "3.1": {
            "name": "Connection Summary",
            "sql": (
                "SELECT state, count(*) AS count "
                "FROM pg_stat_activity "
                "WHERE backend_type = 'client backend' "
                "GROUP BY state ORDER BY count DESC"
            ),
        },
        "3.2": {
            "name": "Long Running Queries (>30s)",
            "sql": (
                "SELECT pid, now() - query_start AS duration, state, "
                "left(query, 200) AS query_snippet "
                "FROM pg_stat_activity "
                "WHERE state != 'idle' "
                "AND query_start < now() - interval '30 seconds' "
                "AND backend_type = 'client backend' "
                "ORDER BY duration DESC LIMIT 20"
            ),
        },
        "3.3": {
            "name": "Lock Waits",
            "sql": (
                "SELECT blocked.pid AS blocked_pid, "
                "blocked.query AS blocked_query, "
                "blocking.pid AS blocking_pid, "
                "blocking.query AS blocking_query, "
                "now() - blocked.query_start AS wait_duration "
                "FROM pg_stat_activity blocked "
                "JOIN pg_locks bl ON bl.pid = blocked.pid "
                "JOIN pg_locks lk ON lk.locktype = bl.locktype "
                "AND lk.database IS NOT DISTINCT FROM bl.database "
                "AND lk.relation IS NOT DISTINCT FROM bl.relation "
                "AND lk.page IS NOT DISTINCT FROM bl.page "
                "AND lk.tuple IS NOT DISTINCT FROM bl.tuple "
                "AND lk.virtualxid IS NOT DISTINCT FROM bl.virtualxid "
                "AND lk.transactionid IS NOT DISTINCT FROM bl.transactionid "
                "AND lk.classid IS NOT DISTINCT FROM bl.classid "
                "AND lk.objid IS NOT DISTINCT FROM bl.objid "
                "AND lk.objsubid IS NOT DISTINCT FROM bl.objsubid "
                "AND lk.pid != bl.pid "
                "JOIN pg_stat_activity blocking ON blocking.pid = lk.pid "
                "WHERE NOT bl.granted LIMIT 20"
            ),
        },
        "3.4": {
            "name": "Connection Counts by User and Database",
            "sql": (
                "SELECT usename, datname, state, count(*) "
                "FROM pg_stat_activity "
                "WHERE backend_type = 'client backend' "
                "GROUP BY usename, datname, state "
                "ORDER BY count DESC LIMIT 30"
            ),
        },
    },
}

# Category 4: Replication
QUERY_ALLOWLIST["4"] = {
    "_category": "Replication",
    "4.1": {
        "name": "Replication Status",
        "sql": (
            "SELECT client_addr, state, sent_lsn, write_lsn, flush_lsn, "
            "replay_lsn, "
            "pg_wal_lsn_diff(sent_lsn, replay_lsn) AS replay_lag_bytes, "
            "write_lag, flush_lag, replay_lag "
            "FROM pg_stat_replication"
        ),
    },
    "4.2": {
        "name": "Replication Slots",
        "sql": (
            "SELECT slot_name, slot_type, active, "
            "pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS retained_bytes, "
            "pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained_size "
            "FROM pg_replication_slots"
        ),
    },
}

# Category 5: Storage & Bloat
QUERY_ALLOWLIST["5"] = {
    "_category": "Storage and Bloat",
    "5.1": {
        "name": "Top 20 Tables by Size",
        "sql": (
            "SELECT schemaname, relname, "
            "pg_size_pretty(pg_total_relation_size(schemaname || '.' || relname)) AS total_size, "
            "pg_size_pretty(pg_relation_size(schemaname || '.' || relname)) AS table_size, "
            "pg_size_pretty(pg_indexes_size(schemaname || '.' || relname)) AS index_size, "
            "n_live_tup, n_dead_tup "
            "FROM pg_stat_user_tables "
            "ORDER BY pg_total_relation_size(schemaname || '.' || relname) DESC "
            "LIMIT 20"
        ),
    },
    "5.2": {
        "name": "Table Bloat Estimate",
        "sql": (
            "SELECT schemaname, relname, n_live_tup, n_dead_tup, "
            "CASE WHEN n_live_tup > 0 "
            "THEN round(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 2) "
            "ELSE 0 END AS dead_tuple_pct, "
            "pg_size_pretty(pg_total_relation_size(schemaname || '.' || relname)) AS total_size "
            "FROM pg_stat_user_tables "
            "WHERE n_dead_tup > 1000 "
            "ORDER BY n_dead_tup DESC LIMIT 20"
        ),
    },
    "5.3": {
        "name": "Tablespace Usage",
        "sql": (
            "SELECT spcname, pg_size_pretty(pg_tablespace_size(spcname)) AS size "
            "FROM pg_tablespace ORDER BY pg_tablespace_size(spcname) DESC"
        ),
    },
}

# Category 6: Performance (pg_stat_statements)
QUERY_ALLOWLIST["6"] = {
    "_category": "Performance",
    "6.1": {
        "name": "Top 20 Queries by Total Time",
        "sql": (
            "SELECT queryid, left(query, 200) AS query_snippet, "
            "calls, round(total_exec_time::numeric, 2) AS total_ms, "
            "round(mean_exec_time::numeric, 2) AS mean_ms, "
            "rows, "
            "round((shared_blks_hit * 100.0 / NULLIF(shared_blks_hit + shared_blks_read, 0))::numeric, 2) "
            "AS cache_hit_pct "
            "FROM pg_stat_statements "
            "WHERE userid != 10 "
            "ORDER BY total_exec_time DESC LIMIT 20"
        ),
    },
    "6.2": {
        "name": "Top 20 Queries by Mean Time",
        "sql": (
            "SELECT queryid, left(query, 200) AS query_snippet, "
            "calls, round(mean_exec_time::numeric, 2) AS mean_ms, "
            "round(total_exec_time::numeric, 2) AS total_ms, "
            "rows "
            "FROM pg_stat_statements "
            "WHERE calls > 10 AND userid != 10 "
            "ORDER BY mean_exec_time DESC LIMIT 20"
        ),
    },
    "6.3": {
        "name": "Cache Hit Ratio (Overall)",
        "sql": (
            "SELECT "
            "sum(blks_hit) AS blocks_hit, "
            "sum(blks_read) AS blocks_read, "
            "round(sum(blks_hit) * 100.0 / NULLIF(sum(blks_hit) + sum(blks_read), 0), 2) "
            "AS cache_hit_pct "
            "FROM pg_stat_database"
        ),
    },
    "6.4": {
        "name": "Index Hit Ratio",
        "sql": (
            "SELECT "
            "sum(idx_blks_hit) AS index_blocks_hit, "
            "sum(idx_blks_read) AS index_blocks_read, "
            "round(sum(idx_blks_hit) * 100.0 / "
            "NULLIF(sum(idx_blks_hit) + sum(idx_blks_read), 0), 2) AS index_hit_pct "
            "FROM pg_statio_user_indexes"
        ),
    },
}

# Category 7: Vacuum & Maintenance
QUERY_ALLOWLIST["7"] = {
    "_category": "Vacuum and Maintenance",
    "7.1": {
        "name": "Tables Needing Vacuum (Most Dead Tuples)",
        "sql": (
            "SELECT schemaname, relname, n_live_tup, n_dead_tup, "
            "last_vacuum, last_autovacuum, last_analyze, last_autoanalyze, "
            "vacuum_count, autovacuum_count "
            "FROM pg_stat_user_tables "
            "ORDER BY n_dead_tup DESC LIMIT 20"
        ),
    },
    "7.2": {
        "name": "Tables Never Vacuumed",
        "sql": (
            "SELECT schemaname, relname, n_live_tup, n_dead_tup, "
            "last_vacuum, last_autovacuum "
            "FROM pg_stat_user_tables "
            "WHERE last_vacuum IS NULL AND last_autovacuum IS NULL "
            "AND n_live_tup > 1000 "
            "ORDER BY n_dead_tup DESC LIMIT 20"
        ),
    },
    "7.3": {
        "name": "Transaction ID Age (Wraparound Risk)",
        "sql": (
            "SELECT datname, age(datfrozenxid) AS xid_age, "
            "current_setting('autovacuum_freeze_max_age')::bigint AS freeze_max_age, "
            "round(100.0 * age(datfrozenxid) / "
            "current_setting('autovacuum_freeze_max_age')::bigint, 2) AS pct_toward_wraparound "
            "FROM pg_database "
            "WHERE datistemplate = false "
            "ORDER BY age(datfrozenxid) DESC"
        ),
    },
}

# Category 8: Index Optimization
QUERY_ALLOWLIST["8"] = {
    "_category": "Index Optimization",
    "8.1": {
        "name": "Unused Indexes",
        "sql": (
            "SELECT schemaname, relname, indexrelname, "
            "pg_size_pretty(pg_relation_size(indexrelid)) AS index_size, "
            "idx_scan, idx_tup_read "
            "FROM pg_stat_user_indexes "
            "WHERE idx_scan = 0 "
            "AND indexrelid NOT IN "
            "(SELECT conindid FROM pg_constraint WHERE contype IN ('p','u')) "
            "ORDER BY pg_relation_size(indexrelid) DESC LIMIT 20"
        ),
    },
    "8.2": {
        "name": "Duplicate Indexes",
        "sql": (
            "SELECT pg_size_pretty(sum(pg_relation_size(idx))::bigint) AS size, "
            "(array_agg(idx))[1] AS idx1, (array_agg(idx))[2] AS idx2, "
            "count(*) AS num_duplicates "
            "FROM ("
            "  SELECT indexrelid::regclass AS idx, "
            "  (indrelid::text || E'\\n' || indclass::text || E'\\n' || "
            "  indkey::text || E'\\n' || coalesce(indexprs::text,'') || E'\\n' || "
            "  coalesce(indpred::text,'')) AS key "
            "  FROM pg_index"
            ") sub "
            "GROUP BY key HAVING count(*) > 1 "
            "ORDER BY sum(pg_relation_size(idx)) DESC LIMIT 10"
        ),
    },
    "8.3": {
        "name": "Index Scan vs Sequential Scan Ratio",
        "sql": (
            "SELECT schemaname, relname, "
            "seq_scan, idx_scan, "
            "CASE WHEN (seq_scan + idx_scan) > 0 "
            "THEN round(100.0 * idx_scan / (seq_scan + idx_scan), 2) "
            "ELSE 0 END AS idx_scan_pct, "
            "n_live_tup "
            "FROM pg_stat_user_tables "
            "WHERE n_live_tup > 10000 "
            "ORDER BY seq_scan DESC LIMIT 20"
        ),
    },
}

# Category 9: Composite Health Score
QUERY_ALLOWLIST["9"] = {
    "_category": "Summary Health Score",
    "9.1": {
        "name": "Composite Health Metrics",
        "sql": (
            "SELECT "
            "'cache_hit_ratio' AS metric, "
            "round(sum(blks_hit) * 100.0 / NULLIF(sum(blks_hit) + sum(blks_read), 0), 2)::text AS value "
            "FROM pg_stat_database WHERE datname = current_database() "
            "UNION ALL "
            "SELECT 'dead_tuple_ratio', "
            "round(sum(n_dead_tup) * 100.0 / NULLIF(sum(n_live_tup) + sum(n_dead_tup), 0), 2)::text "
            "FROM pg_stat_user_tables "
            "UNION ALL "
            "SELECT 'active_connections', count(*)::text "
            "FROM pg_stat_activity WHERE backend_type = 'client backend' "
            "UNION ALL "
            "SELECT 'max_connections', current_setting('max_connections') "
            "UNION ALL "
            "SELECT 'xid_age_pct', "
            "round(100.0 * max(age(datfrozenxid)) / "
            "current_setting('autovacuum_freeze_max_age')::bigint, 2)::text "
            "FROM pg_database WHERE datistemplate = false "
            "UNION ALL "
            "SELECT 'uptime_hours', "
            "round(extract(epoch FROM now() - pg_postmaster_start_time()) / 3600, 1)::text"
        ),
    },
}


# ============================================================
# Database Connection Helper
# ============================================================

def _get_db_credentials(secret_arn: str) -> dict:
    """Retrieve database credentials from Secrets Manager."""
    response = secretsmanager_client.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


def _get_connection(instance_endpoint: str, port: int, database: str, secret_arn: str):
    """Create a pg8000 connection using credentials from Secrets Manager."""
    import logging
    import ssl
    logger = logging.getLogger(__name__)
    
    logger.info(f"Getting credentials from Secrets Manager: {secret_arn[:50]}...")
    creds = _get_db_credentials(secret_arn)
    logger.info(f"Got credentials for user: {creds['username']}")
    
    logger.info(f"Connecting to {instance_endpoint}:{port}/{database}...")
    
    # RDS requires SSL (rds.force_ssl=1). Use permissive context since
    # RDS uses Amazon's own CA which may not be in the default trust store.
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    
    return pg8000.native.Connection(
        host=instance_endpoint,
        port=port,
        database=database,
        user=creds["username"],
        password=creds["password"],
        ssl_context=ssl_ctx,
        timeout=15,
    )


def _execute_query(conn, sql: str) -> list[dict]:
    """Execute a query and return results as list of dicts."""
    rows = conn.run(sql)
    if not rows:
        return []
    columns = [col["name"] for col in conn.columns]
    return [dict(zip(columns, row)) for row in rows]


def _format_results_table(results: list[dict], query_name: str) -> str:
    """Format query results as a markdown table."""
    if not results:
        return f"**{query_name}**: No rows returned."

    columns = list(results[0].keys())
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"

    rows = []
    for row in results[:100]:  # Cap at 100 rows
        values = []
        for col in columns:
            val = row[col]
            if val is None:
                val = "NULL"
            values.append(str(val)[:100])  # Truncate long values
        rows.append("| " + " | ".join(values) + " |")

    table = f"**{query_name}**\n\n" + "\n".join([header, separator] + rows)

    if len(results) > 100:
        table += f"\n\n*Showing 100 of {len(results)} total rows.*"

    return table


# ============================================================
# MCP Tools
# ============================================================

@mcp.tool()
def execute_health_query(
    category: str,
    query_id: str,
    instance_endpoint: str,
    database: str = "postgres",
    port: int = 5432,
    secret_arn: str = "",
) -> str:
    """
    Execute a predefined PostgreSQL health check query by category and query ID.

    Only allowlisted diagnostic queries can be run — no dynamic SQL is accepted.
    Queries span 9 categories: server info (1), configuration (2), activity (3),
    replication (4), storage/bloat (5), performance (6), vacuum (7), indexes (8),
    and composite health (9).

    Use list_health_queries to see all available queries and their IDs.

    Args:
        category: Category number (1-9)
        query_id: Query ID within the category (e.g., "1.1", "3.2")
        instance_endpoint: RDS/Aurora endpoint hostname
        database: Database name (default: "postgres")
        port: Port number (default: 5432)
        secret_arn: Secrets Manager ARN containing database credentials.
                    If not provided, uses the SECRET_ARN environment variable.

    Returns:
        Query results as a formatted markdown table.
    """
    # Resolve secret ARN
    resolved_secret = secret_arn or os.environ.get("SECRET_ARN", "")
    if not resolved_secret:
        return "ERROR: No secret_arn provided and SECRET_ARN env var not set."

    # Validate allowlists
    is_valid, error_msg = validate_database(database)
    if not is_valid:
        return error_msg

    # Validate query exists in allowlist
    cat = QUERY_ALLOWLIST.get(category)
    if not cat:
        return (
            f"ERROR: Invalid category '{category}'. "
            f"Valid categories: {', '.join(sorted(QUERY_ALLOWLIST.keys()))}"
        )

    query_def = cat.get(query_id)
    if not query_def or query_id.startswith("_"):
        valid_ids = [k for k in cat.keys() if not k.startswith("_")]
        return (
            f"ERROR: Invalid query_id '{query_id}' for category {category}. "
            f"Valid IDs: {', '.join(sorted(valid_ids))}"
        )

    # Execute the predefined query
    try:
        conn = _get_connection(instance_endpoint, port, database, resolved_secret)
        try:
            results = _execute_query(conn, query_def["sql"])
            return _format_results_table(results, query_def["name"])
        finally:
            conn.close()
    except Exception as e:
        return f"ERROR executing query {query_id} ({query_def['name']}): {str(e)}"


@mcp.tool()
def list_health_queries() -> str:
    """
    List all available predefined health check queries organized by category.

    Returns a formatted list of all 9 categories and their query IDs,
    which can be used with the execute_health_query tool.
    """
    output = "## PostgreSQL Health Check Queries\n\n"
    for cat_num in sorted(QUERY_ALLOWLIST.keys()):
        cat = QUERY_ALLOWLIST[cat_num]
        category_name = cat.get("_category", f"Category {cat_num}")
        output += f"### Category {cat_num}: {category_name}\n\n"
        output += "| Query ID | Name |\n| --- | --- |\n"
        for qid in sorted(k for k in cat.keys() if not k.startswith("_")):
            output += f"| {qid} | {cat[qid]['name']} |\n"
        output += "\n"
    return output


@mcp.tool()
def run_full_health_check(
    instance_endpoint: str,
    database: str = "postgres",
    port: int = 5432,
    secret_arn: str = "",
) -> str:
    """
    Run a quick health check with the 5 most critical diagnostic queries.

    Returns: version, connection summary, cache hit ratio, XID wraparound risk,
    and composite health metrics. For deeper investigation, use execute_health_query
    with specific categories.

    Args:
        instance_endpoint: RDS/Aurora endpoint hostname
        database: Database name (default: "postgres")
        port: Port number (default: 5432)
        secret_arn: Secrets Manager ARN for database credentials.
                    If not provided, uses the SECRET_ARN environment variable.

    Returns:
        Quick health report with critical findings.
    """
    resolved_secret = secret_arn or os.environ.get("SECRET_ARN", "")
    if not resolved_secret:
        return "ERROR: No secret_arn provided and SECRET_ARN env var not set."

    is_valid, error_msg = validate_database(database)
    if not is_valid:
        return error_msg

    # Reduced to 5 critical queries to avoid timeout
    key_queries = [
        ("1", "1.1"),  # Version
        ("3", "3.1"),  # Connection summary
        ("6", "6.3"),  # Cache hit ratio
        ("7", "7.3"),  # XID wraparound risk
        ("9", "9.1"),  # Composite health score
    ]

    report = "# PostgreSQL Quick Health Check\n\n"
    report += f"**Endpoint:** {instance_endpoint}\n"
    report += f"**Database:** {database}\n\n"
    report += "*For deeper analysis, use execute_health_query with specific categories "
    report += "(bloat: 5.2, vacuum: 7.1, indexes: 8.1, parameters: 2.1, long queries: 3.2)*\n\n---\n\n"

    try:
        conn = _get_connection(instance_endpoint, port, database, resolved_secret)
        try:
            for cat_num, qid in key_queries:
                query_def = QUERY_ALLOWLIST[cat_num][qid]
                try:
                    results = _execute_query(conn, query_def["sql"])
                    report += _format_results_table(results, query_def["name"])
                    report += "\n\n---\n\n"
                except Exception as e:
                    report += f"**{query_def['name']}**: ERROR — {str(e)}\n\n---\n\n"
        finally:
            conn.close()
    except Exception as e:
        return f"ERROR connecting to database: {str(e)}"

    return report


@mcp.tool()
def list_rds_instances() -> str:
    """
    List all RDS and Aurora PostgreSQL instances in the current AWS account.

    Returns instance identifier, engine version, class, status, endpoint,
    and key configuration flags.
    """
    try:
        paginator = rds_client.get_paginator("describe_db_instances")
        instances = []
        for page in paginator.paginate(
            Filters=[{"Name": "engine", "Values": ["postgres", "aurora-postgresql"]}]
        ):
            instances.extend(page["DBInstances"])

        if not instances:
            return "No RDS/Aurora PostgreSQL instances found in this account."

        header = "| Instance ID | Engine | Class | Status | Endpoint | Multi-AZ | Encrypted |"
        separator = "| --- | --- | --- | --- | --- | --- | --- |"
        rows = []

        for inst in instances:
            endpoint = inst.get("Endpoint", {}).get("Address", "N/A")
            rows.append(
                f"| {inst['DBInstanceIdentifier']} "
                f"| {inst['Engine']} {inst['EngineVersion']} "
                f"| {inst['DBInstanceClass']} "
                f"| {inst['DBInstanceStatus']} "
                f"| {endpoint} "
                f"| {inst.get('MultiAZ', False)} "
                f"| {inst.get('StorageEncrypted', False)} |"
            )

        return "\n".join([header, separator] + rows)

    except Exception as e:
        return f"ERROR listing instances: {str(e)}"


@mcp.tool()
def get_instance_config(instance_id: str) -> str:
    """
    Get detailed configuration of an RDS/Aurora PostgreSQL instance.

    Returns engine version, instance class, RAM, storage, parameter group,
    encryption, Multi-AZ, Performance Insights, backup, and monitoring settings.

    Args:
        instance_id: The RDS DB instance identifier
    """
    is_valid, error_msg = validate_instance(instance_id)
    if not is_valid:
        return error_msg

    try:
        response = rds_client.describe_db_instances(
            DBInstanceIdentifier=instance_id
        )
        inst = response["DBInstances"][0]

        # Get RAM from instance type
        instance_type = inst["DBInstanceClass"].replace("db.", "")
        ram_gib = "N/A"
        try:
            ec2_resp = ec2_client.describe_instance_types(
                InstanceTypes=[instance_type]
            )
            if ec2_resp["InstanceTypes"]:
                ram_mib = ec2_resp["InstanceTypes"][0]["MemoryInfo"]["SizeInMiB"]
                ram_gib = f"{ram_mib / 1024:.1f} GiB"
        except Exception:
            pass

        # Parameter group info
        param_groups = inst.get("DBParameterGroups", [])
        pg_info = ", ".join(
            f"{pg['DBParameterGroupName']} ({pg['ParameterApplyStatus']})"
            for pg in param_groups
        )

        endpoint = inst.get("Endpoint", {})

        config = f"""## Instance Configuration: {instance_id}

| Property | Value |
| --- | --- |
| Engine | {inst['Engine']} {inst['EngineVersion']} |
| Instance Class | {inst['DBInstanceClass']} ({ram_gib} RAM) |
| Status | {inst['DBInstanceStatus']} |
| Endpoint | {endpoint.get('Address', 'N/A')}:{endpoint.get('Port', 5432)} |
| Multi-AZ | {inst.get('MultiAZ', False)} |
| Storage Encrypted | {inst.get('StorageEncrypted', False)} |
| Storage Type | {inst.get('StorageType', 'N/A')} |
| Allocated Storage | {inst.get('AllocatedStorage', 'N/A')} GB |
| Publicly Accessible | {inst.get('PubliclyAccessible', False)} |
| Deletion Protection | {inst.get('DeletionProtection', False)} |
| Performance Insights | {inst.get('PerformanceInsightsEnabled', False)} |
| PI Retention | {inst.get('PerformanceInsightsRetentionPeriod', 'N/A')} days |
| Enhanced Monitoring | {inst.get('MonitoringInterval', 0)}s interval |
| Backup Retention | {inst.get('BackupRetentionPeriod', 'N/A')} days |
| Auto Minor Upgrade | {inst.get('AutoMinorVersionUpgrade', False)} |
| Parameter Group | {pg_info} |
| CA Certificate | {inst.get('CACertificateIdentifier', 'N/A')} |
"""
        return config

    except Exception as e:
        return f"ERROR getting instance config: {str(e)}"


@mcp.tool()
def get_instance_metrics(
    instance_id: str,
    period_minutes: int = 60,
) -> str:
    """
    Get key CloudWatch metrics for an RDS/Aurora PostgreSQL instance.

    Returns CPU utilization, freeable memory, database connections, IOPS,
    latency, free storage, and swap usage for the specified time period.

    Args:
        instance_id: The RDS DB instance identifier
        period_minutes: Lookback period in minutes (default: 60, max: 1440)
    """
    is_valid, error_msg = validate_instance(instance_id)
    if not is_valid:
        return error_msg

    if period_minutes > 1440:
        period_minutes = 1440

    from datetime import datetime, timedelta, timezone

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=period_minutes)
    period = 300  # 5 min intervals

    metrics_to_fetch = [
        ("CPUUtilization", "Percent", "Average"),
        ("FreeableMemory", "Bytes", "Average"),
        ("DatabaseConnections", "Count", "Average"),
        ("ReadIOPS", "Count/Second", "Average"),
        ("WriteIOPS", "Count/Second", "Average"),
        ("ReadLatency", "Seconds", "Average"),
        ("WriteLatency", "Seconds", "Average"),
        ("FreeStorageSpace", "Bytes", "Average"),
        ("SwapUsage", "Bytes", "Average"),
    ]

    report = f"## CloudWatch Metrics: {instance_id}\n"
    report += f"**Period:** Last {period_minutes} minutes\n\n"
    report += "| Metric | Latest | Average | Max |\n| --- | --- | --- | --- |\n"

    for metric_name, unit, stat in metrics_to_fetch:
        try:
            response = cloudwatch_client.get_metric_statistics(
                Namespace="AWS/RDS",
                MetricName=metric_name,
                Dimensions=[{"Name": "DBInstanceIdentifier", "Value": instance_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=period,
                Statistics=["Average", "Maximum"],
            )
            datapoints = sorted(
                response.get("Datapoints", []), key=lambda x: x["Timestamp"]
            )
            if datapoints:
                latest = datapoints[-1]["Average"]
                avg = sum(d["Average"] for d in datapoints) / len(datapoints)
                mx = max(d["Maximum"] for d in datapoints)

                # Format values
                if "Memory" in metric_name or "Storage" in metric_name or "Swap" in metric_name:
                    latest_fmt = f"{latest / (1024**3):.2f} GiB"
                    avg_fmt = f"{avg / (1024**3):.2f} GiB"
                    mx_fmt = f"{mx / (1024**3):.2f} GiB"
                elif "Latency" in metric_name:
                    latest_fmt = f"{latest * 1000:.2f} ms"
                    avg_fmt = f"{avg * 1000:.2f} ms"
                    mx_fmt = f"{mx * 1000:.2f} ms"
                elif "Percent" in unit:
                    latest_fmt = f"{latest:.1f}%"
                    avg_fmt = f"{avg:.1f}%"
                    mx_fmt = f"{mx:.1f}%"
                else:
                    latest_fmt = f"{latest:.1f}"
                    avg_fmt = f"{avg:.1f}"
                    mx_fmt = f"{mx:.1f}"

                report += f"| {metric_name} | {latest_fmt} | {avg_fmt} | {mx_fmt} |\n"
            else:
                report += f"| {metric_name} | No data | — | — |\n"
        except Exception as e:
            report += f"| {metric_name} | Error: {str(e)[:50]} | — | — |\n"

    return report


@mcp.tool()
def explain_query(
    query: str,
    instance_endpoint: str,
    database: str = "postgres",
    port: int = 5432,
    secret_arn: str = "",
) -> str:
    """
    Run EXPLAIN (plan-only, NOT ANALYZE) on a SQL query to show its execution plan.

    SAFETY: Only EXPLAIN is used — the query is NOT executed against actual data.
    EXPLAIN ANALYZE is explicitly blocked. Only SELECT/WITH statements are accepted.

    Use this for SQL tuning — identify sequential scans, missing indexes, sort spills,
    and inefficient join strategies.

    Args:
        query: The SQL query to explain (must be SELECT or WITH...SELECT)
        instance_endpoint: RDS/Aurora endpoint hostname
        database: Database name (default: "postgres")
        port: Port number (default: 5432)
        secret_arn: Secrets Manager ARN for database credentials.
    """
    import re

    resolved_secret = secret_arn or os.environ.get("SECRET_ARN", "")
    if not resolved_secret:
        return "ERROR: No secret_arn provided and SECRET_ARN env var not set."

    is_valid, error_msg = validate_database(database)
    if not is_valid:
        return error_msg

    # Safety: only allow SELECT/WITH statements
    stripped = query.strip().rstrip(";").strip()
    if not stripped:
        return "ERROR: Empty query."

    # Block EXPLAIN ANALYZE in the query itself
    if re.search(r"\bANALYZE\b", stripped, re.IGNORECASE):
        return "ERROR: EXPLAIN ANALYZE is not permitted. Only plan-only EXPLAIN is allowed."

    # Only allow SELECT or WITH...SELECT
    allowed = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
    if not allowed.match(stripped):
        return "ERROR: Only SELECT or WITH...SELECT queries can be explained."

    # Block any mutative keywords
    blocked = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE)\b",
        re.IGNORECASE,
    )
    if blocked.search(stripped):
        return "ERROR: Mutative SQL detected. Only read-only queries can be explained."

    try:
        conn = _get_connection(instance_endpoint, port, database, resolved_secret)
        try:
            explain_sql = f"EXPLAIN (FORMAT TEXT) {stripped}"
            results = _execute_query(conn, explain_sql)
            if results:
                plan_lines = [str(list(row.values())[0]) for row in results]
                plan = "\n".join(plan_lines)
                return f"**EXPLAIN Plan:**\n\n```\n{plan}\n```"
            else:
                return "EXPLAIN returned no output."
        finally:
            conn.close()
    except Exception as e:
        return f"ERROR running EXPLAIN: {str(e)}"



# ============================================================
# Category 10: Pre-Upgrade Checks (from v2 pre_upgrade_check skill)
# ============================================================

QUERY_ALLOWLIST["10"] = {
    "_category": "Pre-Upgrade Checks",
    "10.1": {
        "name": "Open Prepared Transactions",
        "sql": "SELECT gid, prepared, owner, database FROM pg_catalog.pg_prepared_xacts",
    },
    "10.2": {
        "name": "Unsupported reg* Data Types",
        "sql": (
            "SELECT n.nspname AS schema, c.relname AS table_name, a.attname AS column_name, "
            "a.atttypid::regtype::text AS data_type "
            "FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON c.relnamespace = n.oid "
            "JOIN pg_catalog.pg_attribute a ON c.oid = a.attrelid "
            "WHERE NOT a.attisdropped "
            "AND a.atttypid IN ("
            "'pg_catalog.regproc'::pg_catalog.regtype,"
            "'pg_catalog.regprocedure'::pg_catalog.regtype,"
            "'pg_catalog.regoper'::pg_catalog.regtype,"
            "'pg_catalog.regoperator'::pg_catalog.regtype,"
            "'pg_catalog.regconfig'::pg_catalog.regtype,"
            "'pg_catalog.regdictionary'::pg_catalog.regtype) "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
        ),
    },
    "10.3": {
        "name": "Logical Replication Slots",
        "sql": (
            "SELECT slot_name, slot_type, active, database, "
            "pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS lag_bytes "
            "FROM pg_replication_slots WHERE slot_type = 'logical'"
        ),
    },
    "10.4": {
        "name": "Unknown Data Types",
        "sql": (
            "SELECT table_schema, table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE data_type ILIKE 'unknown'"
        ),
    },
    "10.5": {
        "name": "sql_identifier Data Type Usage",
        "sql": (
            "SELECT pg_namespace.nspname AS schema, pg_class.relname AS table_name, "
            "attname AS column_name "
            "FROM pg_attribute "
            "JOIN pg_class ON attrelid = oid "
            "JOIN pg_namespace ON relnamespace = pg_namespace.oid "
            "WHERE atttypid::regtype::text LIKE '%sql_identifier' "
            "AND nspname NOT IN ('information_schema', 'oracle')"
        ),
    },
    "10.6": {
        "name": "Extensions Installed (for upgrade compatibility)",
        "sql": (
            "SELECT e.extname AS name, e.extversion AS version, "
            "n.nspname AS schema "
            "FROM pg_catalog.pg_extension e "
            "LEFT JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace "
            "ORDER BY e.extname"
        ),
    },
    "10.7": {
        "name": "Views Dependent on System Catalogs",
        "sql": (
            "SELECT n.nspname AS schema, c.relname AS name, "
            "CASE c.relkind WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized view' END AS type, "
            "pg_catalog.pg_get_userbyid(c.relowner) AS owner "
            "FROM pg_catalog.pg_class c "
            "LEFT JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('v','m') "
            "AND n.nspname NOT IN ('pg_catalog','information_schema') "
            "AND n.nspname !~ '^pg_toast' "
            "AND pg_catalog.pg_table_is_visible(c.oid) "
            "AND pg_catalog.pg_get_userbyid(c.relowner) NOT LIKE 'rdsadmin' "
            "ORDER BY 1, 2"
        ),
    },
    "10.8": {
        "name": "Current User Privileges",
        "sql": (
            "SELECT r.rolname, r.rolsuper, r.rolcreaterole, r.rolcreatedb, "
            "ARRAY(SELECT b.rolname FROM pg_catalog.pg_auth_members m "
            "JOIN pg_catalog.pg_roles b ON m.roleid = b.oid "
            "WHERE m.member = r.oid) AS member_of "
            "FROM pg_catalog.pg_roles r WHERE r.rolname = current_user"
        ),
    },
}


# ============================================================
# Category 11: Extended Health Checks (from v2 health_check skill)
# ============================================================

QUERY_ALLOWLIST["11"] = {
    "_category": "Extended Health Checks",
    "11.1": {
        "name": "Tables Without Primary Key",
        "sql": (
            "SELECT n.nspname AS schema_name, c.relname AS table_name, "
            "pg_size_pretty(pg_total_relation_size(c.oid)) AS table_size "
            "FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid "
            "WHERE c.relkind = 'r' "
            "AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast') "
            "AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = c.oid AND contype = 'p') "
            "ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 15"
        ),
    },
    "11.2": {
        "name": "Invalid Indexes",
        "sql": (
            "SELECT n.nspname AS schema_name, c.relname AS index_name, "
            "t.relname AS table_name, "
            "pg_size_pretty(pg_relation_size(c.oid)) AS index_size "
            "FROM pg_class c "
            "JOIN pg_index i ON c.oid = i.indexrelid "
            "JOIN pg_class t ON i.indrelid = t.oid "
            "JOIN pg_namespace n ON c.relnamespace = n.oid "
            "WHERE NOT i.indisvalid "
            "ORDER BY pg_relation_size(c.oid) DESC"
        ),
    },
    "11.3": {
        "name": "Sequences Near Exhaustion (>30% used)",
        "sql": (
            "SELECT schemaname AS schema_name, sequencename AS sequence_name, "
            "data_type, last_value, max_value, "
            "ROUND(100.0 * last_value / max_value, 2) AS pct_used "
            "FROM pg_sequences WHERE last_value IS NOT NULL "
            "AND ROUND(100.0 * last_value / max_value, 2) > 30 "
            "ORDER BY pct_used DESC LIMIT 10"
        ),
    },
    "11.4": {
        "name": "Database Transaction ID Age",
        "sql": (
            "SELECT datname, age(datfrozenxid) AS age, "
            "2147483647 - age(datfrozenxid) AS remaining_until_wraparound "
            "FROM pg_database ORDER BY age DESC LIMIT 5"
        ),
    },
    "11.5": {
        "name": "Table Transaction ID Age (Top 10)",
        "sql": (
            "SELECT c.relnamespace::regnamespace AS schema_name, "
            "c.relname AS table_name, "
            "greatest(age(c.relfrozenxid), age(t.relfrozenxid)) AS age, "
            "2147483647 - greatest(age(c.relfrozenxid), age(t.relfrozenxid)) AS remaining "
            "FROM pg_class c "
            "LEFT JOIN pg_class t ON c.reltoastrelid = t.oid "
            "WHERE c.relkind IN ('r','m') "
            "ORDER BY age DESC LIMIT 10"
        ),
    },
    "11.6": {
        "name": "UPDATE/DELETE Heavy Tables",
        "sql": (
            "SELECT relname, "
            "round(100.0 * n_tup_upd / NULLIF(n_tup_ins + n_tup_upd + n_tup_del, 0), 2) AS update_pct, "
            "round(100.0 * n_tup_del / NULLIF(n_tup_ins + n_tup_upd + n_tup_del, 0), 2) AS delete_pct, "
            "round(100.0 * n_tup_ins / NULLIF(n_tup_ins + n_tup_upd + n_tup_del, 0), 2) AS insert_pct, "
            "n_tup_ins + n_tup_upd + n_tup_del AS total_ops "
            "FROM pg_stat_user_tables "
            "WHERE (n_tup_ins + n_tup_upd + n_tup_del) > 0 "
            "ORDER BY coalesce(n_tup_upd,0) + coalesce(n_tup_del,0) DESC LIMIT 10"
        ),
    },
}


# ============================================================
# Tool: get_parameter_group (DescribeDBParameters)
# ============================================================


@mcp.tool()
def get_parameter_group(
    instance_id: str,
    filter_modified: bool = True,
) -> str:
    """
    Get the RDS parameter group settings for a PostgreSQL instance.

    Returns parameter values from the RDS API (parameter group level).
    By default shows only modified (non-default) parameters. Set filter_modified=False
    to see all parameters.

    This complements category 2 queries which read live values from pg_settings inside
    the database. This tool shows what's configured at the RDS parameter group level.

    Args:
        instance_id: RDS DB instance identifier
        filter_modified: If True, only show parameters with non-default values (default: True)
    """
    is_valid, error_msg = validate_instance(instance_id)
    if not is_valid:
        return error_msg

    try:
        # Get parameter group name from instance
        resp = rds_client.describe_db_instances(DBInstanceIdentifier=instance_id)
        inst = resp["DBInstances"][0]
        param_groups = inst.get("DBParameterGroups", [])
        if not param_groups:
            return "ERROR: No parameter group found for this instance."
        pg_name = param_groups[0]["DBParameterGroupName"]

        # Get parameters
        paginator = rds_client.get_paginator("describe_db_parameters")
        params = []
        for page in paginator.paginate(DBParameterGroupName=pg_name):
            params.extend(page["Parameters"])

        # Filter
        if filter_modified:
            params = [p for p in params if p.get("Source") != "engine-default"]

        if not params:
            return (
                f"**Parameter Group:** {pg_name}\n\n"
                f"All parameters are at engine defaults. No modifications found.\n"
                f"(Set filter_modified=False to see all {len(params)} parameters.)"
            )

        # Format as table
        report = f"**Parameter Group:** {pg_name}\n"
        report += f"**Showing:** {'Modified only' if filter_modified else 'All parameters'} ({len(params)} parameters)\n\n"
        report += "| Parameter | Value | Apply Type | Source |\n"
        report += "| --- | --- | --- | --- |\n"

        for p in sorted(params, key=lambda x: x.get("ParameterName", "")):
            name = p.get("ParameterName", "")
            value = p.get("ParameterValue", "NULL")
            apply_type = p.get("ApplyType", "")
            source = p.get("Source", "")
            report += f"| {name} | {value} | {apply_type} | {source} |\n"

        return report

    except Exception as e:
        return f"ERROR getting parameter group: {str(e)}"


# ============================================================
# Tool: get_log_files (DescribeDBLogFiles)
# ============================================================


@mcp.tool()
def get_log_files(
    instance_id: str,
    max_files: int = 20,
) -> str:
    """
    List recent PostgreSQL log files for an RDS instance with sizes.

    Useful for checking if log files are growing unexpectedly large (which could
    indicate excessive logging, errors, or slow query logging filling up storage).

    Args:
        instance_id: RDS DB instance identifier
        max_files: Maximum number of log files to return (default: 20, most recent first)
    """
    is_valid, error_msg = validate_instance(instance_id)
    if not is_valid:
        return error_msg

    try:
        resp = rds_client.describe_db_log_files(
            DBInstanceIdentifier=instance_id,
            MaxRecords=max_files,
        )
        log_files = resp.get("DescribeDBLogFiles", [])

        if not log_files:
            return f"No log files found for instance {instance_id}."

        # Sort by last written (most recent first)
        log_files.sort(key=lambda x: x.get("LastWritten", 0), reverse=True)

        report = f"## Log Files: {instance_id}\n\n"
        report += f"**Total files shown:** {len(log_files)}\n\n"
        report += "| File Name | Size | Last Written |\n"
        report += "| --- | --- | --- |\n"

        total_bytes = 0
        from datetime import datetime, timezone
        for lf in log_files[:max_files]:
            name = lf.get("LogFileName", "")
            size = lf.get("Size", 0)
            total_bytes += size
            last_written = lf.get("LastWritten", 0)
            # Convert epoch ms to readable
            if last_written:
                ts = datetime.fromtimestamp(last_written / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            else:
                ts = "N/A"

            # Format size
            if size > 1048576:
                size_fmt = f"{size / 1048576:.1f} MB"
            elif size > 1024:
                size_fmt = f"{size / 1024:.1f} KB"
            else:
                size_fmt = f"{size} B"

            report += f"| {name} | {size_fmt} | {ts} |\n"

        # Total
        if total_bytes > 1073741824:
            total_fmt = f"{total_bytes / 1073741824:.2f} GB"
        elif total_bytes > 1048576:
            total_fmt = f"{total_bytes / 1048576:.1f} MB"
        else:
            total_fmt = f"{total_bytes / 1024:.1f} KB"
        report += f"\n**Total log size:** {total_fmt}"

        return report

    except Exception as e:
        return f"ERROR listing log files: {str(e)}"


# ============================================================
# Tool: check_upgrade_readiness (control-plane / RDS API checks)
# ============================================================

# Unsupported instance classes for newer PG versions
UNSUPPORTED_CLASSES = ["db.m4", "db.r4", "db.t2", "db.m3", "db.r3", "db.t1"]


@mcp.tool()
def check_upgrade_readiness(
    instance_id: str,
    target_major_version: int = 0,
) -> str:
    """
    Run control-plane pre-upgrade checks for a PostgreSQL major version upgrade.

    Checks instance class compatibility, target version availability, read replica
    configuration, primary user name, pending maintenance, and storage capacity.
    These complement the data-plane checks in category 10 (execute_health_query).

    Args:
        instance_id: RDS DB instance identifier
        target_major_version: Target PG major version (e.g., 16, 17, 18).
                              If 0, auto-detects the latest available.
    """
    is_valid, error_msg = validate_instance(instance_id)
    if not is_valid:
        return error_msg

    report = "## Pre-Upgrade Readiness (Control-Plane Checks)\n\n"
    checks_pass = 0
    checks_fail = 0
    checks_warn = 0

    try:
        resp = rds_client.describe_db_instances(DBInstanceIdentifier=instance_id)
        inst = resp["DBInstances"][0]
    except Exception as e:
        return f"ERROR: Could not describe instance {instance_id}: {str(e)}"

    engine = inst.get("Engine", "postgres")
    current_version = inst.get("EngineVersion", "")
    current_major = int(current_version.split(".")[0]) if current_version else 0
    instance_class = inst.get("DBInstanceClass", "")
    master_user = inst.get("MasterUsername", "")

    # Auto-detect target version if not specified
    if target_major_version == 0:
        target_major_version = current_major + 1

    report += f"**Instance:** {instance_id}\n"
    report += f"**Current:** {engine} {current_version}\n"
    report += f"**Target:** PostgreSQL {target_major_version}\n"
    report += f"**Class:** {instance_class}\n\n"
    report += "| # | Check | Status | Details |\n"
    report += "| --- | --- | --- | --- |\n"

    # CHECK 1: Target version availability
    try:
        ver_resp = rds_client.describe_db_engine_versions(
            Engine=engine, EngineVersion=current_version
        )
        if ver_resp["DBEngineVersions"]:
            targets = ver_resp["DBEngineVersions"][0].get("ValidUpgradeTarget", [])
            target_versions = [t["EngineVersion"] for t in targets]
            available = any(v.startswith(str(target_major_version) + ".") for v in target_versions)
        else:
            available = False

        if available:
            report += f"| 1 | Target version available | PASS | PG {target_major_version} is a valid upgrade target |\n"
            checks_pass += 1
        else:
            report += f"| 1 | Target version available | FAIL | PG {target_major_version} not in valid upgrade targets for {current_version} |\n"
            checks_fail += 1
    except Exception as e:
        report += f"| 1 | Target version available | ERROR | {str(e)[:80]} |\n"
        checks_warn += 1

    # CHECK 2: Instance class compatibility
    class_ok = not any(instance_class.startswith(c) for c in UNSUPPORTED_CLASSES)
    if class_ok:
        report += f"| 2 | Instance class supported | PASS | {instance_class} is supported |\n"
        checks_pass += 1
    else:
        report += f"| 2 | Instance class supported | FAIL | {instance_class} is deprecated — upgrade to m6i/r6i/t3 |\n"
        checks_fail += 1

    # CHECK 3: Primary user name
    if master_user.startswith("pg_"):
        report += f"| 3 | Primary user name | FAIL | '{master_user}' starts with pg_ — upgrade will fail |\n"
        checks_fail += 1
    else:
        report += f"| 3 | Primary user name | PASS | '{master_user}' is valid |\n"
        checks_pass += 1

    # CHECK 4: Read replicas
    if engine == "aurora-postgresql":
        cluster_id = inst.get("DBClusterIdentifier", "")
        if cluster_id:
            try:
                cluster_resp = rds_client.describe_db_clusters(DBClusterIdentifier=cluster_id)
                members = cluster_resp["DBClusters"][0].get("DBClusterMembers", [])
                readers = [m["DBInstanceIdentifier"] for m in members if not m.get("IsClusterWriter", False)]
                if readers:
                    report += f"| 4 | Read replicas | WARNING | {len(readers)} readers will have brief outage during upgrade: {', '.join(readers)} |\n"
                    checks_warn += 1
                else:
                    report += f"| 4 | Read replicas | PASS | No readers in cluster |\n"
                    checks_pass += 1
            except Exception:
                report += f"| 4 | Read replicas | WARNING | Could not check cluster members |\n"
                checks_warn += 1
        else:
            report += f"| 4 | Read replicas | PASS | Not part of a cluster |\n"
            checks_pass += 1
    else:
        replicas = inst.get("ReadReplicaDBInstanceIdentifiers", [])
        if replicas:
            report += f"| 4 | Read replicas | WARNING | {len(replicas)} replicas add to downtime: {', '.join(replicas)} |\n"
            checks_warn += 1
        else:
            report += f"| 4 | Read replicas | PASS | No read replicas |\n"
            checks_pass += 1

    # CHECK 5: Pending maintenance
    try:
        maint_resp = rds_client.describe_pending_maintenance_actions(
            Filters=[{"Name": "db-instance-id", "Values": [inst["DBInstanceArn"]]}]
        )
        actions = maint_resp.get("PendingMaintenanceActions", [])
        pending = []
        for a in actions:
            for detail in a.get("PendingMaintenanceActionDetails", []):
                pending.append(detail.get("Action", "unknown"))
        if pending:
            report += f"| 5 | Pending maintenance | WARNING | {len(pending)} pending: {', '.join(pending)} — apply before upgrade |\n"
            checks_warn += 1
        else:
            report += f"| 5 | Pending maintenance | PASS | No pending maintenance |\n"
            checks_pass += 1
    except Exception as e:
        report += f"| 5 | Pending maintenance | WARNING | Could not check: {str(e)[:60]} |\n"
        checks_warn += 1

    # CHECK 6: Storage headroom
    try:
        from datetime import datetime, timedelta, timezone
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=5)
        cw_resp = cloudwatch_client.get_metric_statistics(
            Namespace="AWS/RDS", MetricName="FreeStorageSpace",
            Dimensions=[{"Name": "DBInstanceIdentifier", "Value": instance_id}],
            StartTime=start_time, EndTime=end_time, Period=300, Statistics=["Average"],
        )
        points = cw_resp.get("Datapoints", [])
        allocated = inst.get("AllocatedStorage", 0)  # in GB
        if points and allocated:
            free_gb = sorted(points, key=lambda x: x["Timestamp"])[-1]["Average"] / (1024**3)
            pct_free = (free_gb / allocated) * 100 if allocated else 0
            if pct_free < 15:
                report += f"| 6 | Storage headroom | WARNING | {free_gb:.1f} GB free ({pct_free:.0f}%) — need 15-20% for upgrade |\n"
                checks_warn += 1
            else:
                report += f"| 6 | Storage headroom | PASS | {free_gb:.1f} GB free ({pct_free:.0f}%) |\n"
                checks_pass += 1
        else:
            report += f"| 6 | Storage headroom | WARNING | Could not determine free space |\n"
            checks_warn += 1
    except Exception as e:
        report += f"| 6 | Storage headroom | WARNING | {str(e)[:60]} |\n"
        checks_warn += 1

    # CHECK 7: Multi-AZ (informational)
    multi_az = inst.get("MultiAZ", False)
    if multi_az:
        report += f"| 7 | Multi-AZ | INFO | Enabled — failover will occur during upgrade |\n"
    else:
        report += f"| 7 | Multi-AZ | INFO | Disabled — single point of failure during upgrade |\n"
    checks_pass += 1

    # Summary
    report += f"\n**Summary:** {checks_pass} passed, {checks_fail} failed, {checks_warn} warnings\n"
    if checks_fail > 0:
        report += "\n⚠️ **Upgrade blocked** — resolve FAIL items before proceeding.\n"
    else:
        report += "\n✅ **No hard blockers** from control-plane checks. Combine with data-plane checks (category 10) for full assessment.\n"

    return report


# ============================================================
# Entry point (MUST be at end of file after all QUERY_ALLOWLIST definitions)
# ============================================================

# For Lambda + Lambda Web Adapter (Streamable HTTP)
try:
    handler = mcp.streamable_http_handler()
except AttributeError:
    # FastMCP 3.x: use the ASGI app for Lambda Web Adapter
    handler = mcp.http_app()

if __name__ == "__main__":
    # For local testing
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
