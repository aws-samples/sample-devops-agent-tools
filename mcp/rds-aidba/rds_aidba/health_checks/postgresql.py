"""
PostgreSQL Health Check Query Registry
Author: Kiran Mayee Mulupuru, Sr. Specialist Database TAM, AWS Enterprise Support

Aurora PostgreSQL and RDS PostgreSQL diagnostic queries.
All queries use pg_stat_* views, pg_settings, and system catalogs.
Read-only — no DDL, DML, or DCL operations.

Prerequisites:
- pg_stat_statements extension installed and enabled
- Monitoring user with SELECT on system catalogs
"""

from typing import Any, Dict, List


# ============================================================================
# SECTION 1 — SERVER INFORMATION
# ============================================================================

PG_SERVER_INFO: List[Dict[str, Any]] = [
    {
        "name": "pg_server_information",
        "description": "PostgreSQL version, uptime, connection counts, current database",
        "category": "connections",
        "severity": "baseline",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                'PostgreSQL Version' AS metric,
                version() AS value,
                'Full PostgreSQL version and build info' AS description,
                'INFO' AS status
            UNION ALL
            SELECT
                'Server Start Time',
                pg_postmaster_start_time()::text,
                'When the PostgreSQL server was started',
                CASE
                    WHEN EXTRACT(EPOCH FROM (now() - pg_postmaster_start_time())) < 300
                    THEN 'WARNING: Server recently restarted (<5 min)'
                    ELSE 'OK: Server running stable'
                END
            UNION ALL
            SELECT
                'Current Connections',
                (SELECT count(*)::text FROM pg_stat_activity WHERE state IS NOT NULL),
                'Currently active connections',
                CASE
                    WHEN (SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL)::float
                         / current_setting('max_connections')::float > 0.9
                    THEN 'CRITICAL: >90% connection usage'
                    WHEN (SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL)::float
                         / current_setting('max_connections')::float > 0.75
                    THEN 'WARNING: >75% connection usage'
                    ELSE 'OK'
                END
            UNION ALL
            SELECT
                'Max Connections',
                current_setting('max_connections'),
                'Maximum allowed concurrent connections',
                'INFO'
            UNION ALL
            SELECT
                'Current Database',
                current_database(),
                'Database connected to',
                'INFO';
        """,
    },
]

# ============================================================================
# SECTION 2 — SYSTEM CONFIGURATION
# ============================================================================

PG_CONFIGURATION: List[Dict[str, Any]] = [
    {
        "name": "pg_system_settings",
        "description": "Key PostgreSQL configuration parameters affecting performance",
        "category": "configuration",
        "severity": "baseline",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                name AS parameter_name,
                setting AS current_value,
                unit,
                CASE
                    WHEN name = 'shared_buffers' AND setting::bigint < 131072
                        THEN 'WARNING: shared_buffers < 1GB'
                    WHEN name = 'work_mem' AND setting::bigint < 4096
                        THEN 'INFO: work_mem < 4MB (may cause temp files)'
                    WHEN name = 'maintenance_work_mem' AND setting::bigint < 65536
                        THEN 'INFO: maintenance_work_mem < 64MB'
                    WHEN name = 'effective_cache_size' AND setting::bigint < 524288
                        THEN 'WARNING: effective_cache_size < 4GB'
                    WHEN name = 'random_page_cost' AND setting::numeric > 1.5
                        THEN 'INFO: random_page_cost > 1.5 (consider lowering for SSD/Aurora)'
                    ELSE 'OK'
                END AS status,
                short_desc AS description
            FROM pg_settings
            WHERE name IN (
                'shared_buffers', 'work_mem', 'maintenance_work_mem',
                'effective_cache_size', 'random_page_cost', 'seq_page_cost',
                'max_connections', 'max_worker_processes', 'max_parallel_workers',
                'autovacuum', 'autovacuum_vacuum_scale_factor',
                'autovacuum_analyze_scale_factor', 'autovacuum_freeze_max_age',
                'log_min_duration_statement', 'shared_preload_libraries'
            )
            ORDER BY name;
        """,
    },
]

# ============================================================================
# SECTION 3 — CURRENT ACTIVITY
# ============================================================================

PG_ACTIVITY: List[Dict[str, Any]] = [
    {
        "name": "pg_active_sessions",
        "description": "Active queries, long-running transactions, wait events",
        "category": "activity",
        "severity": "critical",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                pid,
                usename,
                datname,
                state,
                wait_event_type,
                wait_event,
                EXTRACT(EPOCH FROM (now() - query_start))::int AS duration_seconds,
                LEFT(query, 300) AS query_text,
                CASE
                    WHEN EXTRACT(EPOCH FROM (now() - query_start)) > 300
                        THEN 'CRITICAL: Query running > 5 minutes'
                    WHEN EXTRACT(EPOCH FROM (now() - query_start)) > 60
                        THEN 'WARNING: Query running > 1 minute'
                    ELSE 'OK'
                END AS status
            FROM pg_stat_activity
            WHERE state != 'idle'
              AND pid != pg_backend_pid()
              AND usename NOT IN ('rdsadmin', 'rdsrepladmin')
            ORDER BY query_start ASC
            LIMIT 20;
        """,
    },
    {
        "name": "pg_lock_detection",
        "description": "Blocked queries and lock waits",
        "category": "activity",
        "severity": "critical",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                blocked_locks.pid AS blocked_pid,
                blocked_activity.usename AS blocked_user,
                LEFT(blocked_activity.query, 200) AS blocked_query,
                blocking_locks.pid AS blocking_pid,
                blocking_activity.usename AS blocking_user,
                LEFT(blocking_activity.query, 200) AS blocking_query,
                EXTRACT(EPOCH FROM (now() - blocked_activity.query_start))::int AS wait_seconds
            FROM pg_catalog.pg_locks blocked_locks
            JOIN pg_catalog.pg_stat_activity blocked_activity
                ON blocked_activity.pid = blocked_locks.pid
            JOIN pg_catalog.pg_locks blocking_locks
                ON blocking_locks.locktype = blocked_locks.locktype
                AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
                AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
                AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
                AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
                AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
                AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
                AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
                AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
                AND blocking_locks.pid != blocked_locks.pid
            JOIN pg_catalog.pg_stat_activity blocking_activity
                ON blocking_activity.pid = blocking_locks.pid
            WHERE NOT blocked_locks.granted
            ORDER BY wait_seconds DESC
            LIMIT 10;
        """,
    },
]

# ============================================================================
# SECTION 4 — REPLICATION STATUS
# ============================================================================

PG_REPLICATION: List[Dict[str, Any]] = [
    {
        "name": "pg_replication_status",
        "description": "Replication lag, slot status, WAL position",
        "category": "replication",
        "severity": "warning",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                client_addr,
                state,
                sent_lsn,
                write_lsn,
                flush_lsn,
                replay_lsn,
                EXTRACT(EPOCH FROM (now() - write_lag))::int AS write_lag_seconds,
                EXTRACT(EPOCH FROM (now() - replay_lag))::int AS replay_lag_seconds,
                CASE
                    WHEN replay_lag > interval '60 seconds'
                        THEN 'CRITICAL: Replay lag > 60s'
                    WHEN replay_lag > interval '10 seconds'
                        THEN 'WARNING: Replay lag > 10s'
                    ELSE 'OK'
                END AS status
            FROM pg_stat_replication;
        """,
    },
]

# ============================================================================
# SECTION 5 — STORAGE & TABLE BLOAT
# ============================================================================

PG_STORAGE: List[Dict[str, Any]] = [
    {
        "name": "pg_database_sizes",
        "description": "Database sizes across all databases",
        "category": "storage",
        "severity": "baseline",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                datname AS database_name,
                pg_size_pretty(pg_database_size(datname)) AS size,
                pg_database_size(datname) AS size_bytes
            FROM pg_database
            WHERE datname NOT IN ('template0', 'template1', 'rdsadmin')
            ORDER BY pg_database_size(datname) DESC;
        """,
    },
    {
        "name": "pg_largest_tables",
        "description": "Top 10 largest tables by total size",
        "category": "storage",
        "severity": "baseline",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                schemaname || '.' || relname AS table_name,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                pg_size_pretty(pg_relation_size(relid)) AS data_size,
                pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) AS index_size,
                n_live_tup AS row_count
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT 10;
        """,
    },
    {
        "name": "pg_table_bloat",
        "description": "Tables with significant dead tuples (bloat)",
        "category": "storage",
        "severity": "warning",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                schemaname,
                relname AS table_name,
                n_live_tup,
                n_dead_tup,
                ROUND(n_dead_tup::numeric / NULLIF(n_live_tup, 0), 3) AS bloat_ratio,
                last_autovacuum,
                last_autoanalyze,
                CASE
                    WHEN n_dead_tup::numeric / NULLIF(n_live_tup, 0) > 0.3
                        THEN 'CRITICAL: Bloat ratio > 0.3'
                    WHEN n_dead_tup::numeric / NULLIF(n_live_tup, 0) > 0.1
                        THEN 'WARNING: Bloat ratio > 0.1'
                    ELSE 'OK'
                END AS status
            FROM pg_stat_user_tables
            WHERE n_dead_tup > 10000
            ORDER BY n_dead_tup DESC
            LIMIT 10;
        """,
    },
]

# ============================================================================
# SECTION 6 — PERFORMANCE (pg_stat_statements)
# ============================================================================

PG_PERFORMANCE: List[Dict[str, Any]] = [
    {
        "name": "pg_top_queries_by_time",
        "description": "Top 10 queries by total execution time (requires pg_stat_statements)",
        "category": "performance",
        "severity": "warning",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                LEFT(query, 300) AS query_text,
                calls,
                ROUND(total_exec_time::numeric / 1000, 2) AS total_time_sec,
                ROUND(mean_exec_time::numeric / 1000, 4) AS avg_time_sec,
                rows,
                ROUND((shared_blks_hit::numeric / NULLIF(shared_blks_hit + shared_blks_read, 0)) * 100, 2) AS cache_hit_pct,
                CASE
                    WHEN mean_exec_time > 10000 THEN 'CRITICAL: Avg > 10s'
                    WHEN mean_exec_time > 1000 THEN 'WARNING: Avg > 1s'
                    ELSE 'OK'
                END AS status
            FROM pg_stat_statements
            WHERE userid != (SELECT usesysid FROM pg_user WHERE usename = 'rdsadmin')
              AND query NOT LIKE '%pg_stat_statements%'
            ORDER BY total_exec_time DESC
            LIMIT 10;
        """,
    },
    {
        "name": "pg_top_queries_by_io",
        "description": "Top 10 queries by disk I/O (shared blocks read)",
        "category": "performance",
        "severity": "warning",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                LEFT(query, 300) AS query_text,
                calls,
                shared_blks_read AS disk_reads,
                shared_blks_hit AS cache_hits,
                ROUND((shared_blks_hit::numeric / NULLIF(shared_blks_hit + shared_blks_read, 0)) * 100, 2) AS cache_hit_pct,
                ROUND(total_exec_time::numeric / 1000, 2) AS total_time_sec,
                CASE
                    WHEN (shared_blks_hit::numeric / NULLIF(shared_blks_hit + shared_blks_read, 0)) < 0.9
                        THEN 'WARNING: Cache hit < 90%'
                    ELSE 'OK'
                END AS status
            FROM pg_stat_statements
            WHERE userid != (SELECT usesysid FROM pg_user WHERE usename = 'rdsadmin')
              AND shared_blks_read > 0
            ORDER BY shared_blks_read DESC
            LIMIT 10;
        """,
    },
    {
        "name": "pg_temp_file_usage",
        "description": "Queries generating temp files (work_mem overflow)",
        "category": "performance",
        "severity": "warning",
        "engine": "postgresql",
        "min_version": "13",
        "sql": """
            SELECT
                LEFT(query, 300) AS query_text,
                calls,
                temp_blks_written AS temp_blocks,
                pg_size_pretty(temp_blks_written * 8192) AS temp_size,
                ROUND(total_exec_time::numeric / 1000, 2) AS total_time_sec,
                CASE
                    WHEN temp_blks_written > 10000 THEN 'WARNING: Heavy temp file usage'
                    ELSE 'INFO: Moderate temp usage'
                END AS status
            FROM pg_stat_statements
            WHERE temp_blks_written > 0
              AND userid != (SELECT usesysid FROM pg_user WHERE usename = 'rdsadmin')
            ORDER BY temp_blks_written DESC
            LIMIT 10;
        """,
    },
]

# ============================================================================
# SECTION 7 — MAINTENANCE (Vacuum & Transaction ID)
# ============================================================================

PG_MAINTENANCE: List[Dict[str, Any]] = [
    {
        "name": "pg_vacuum_status",
        "description": "Top 10 largest tables last vacuumed — vacuum freshness",
        "category": "maintenance",
        "severity": "warning",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                schemaname || '.' || relname AS table_name,
                pg_size_pretty(pg_total_relation_size(relid)) AS size,
                n_live_tup,
                n_dead_tup,
                last_autovacuum,
                last_autoanalyze,
                EXTRACT(EPOCH FROM (now() - COALESCE(last_autovacuum, '1970-01-01')))::int / 86400 AS days_since_vacuum,
                CASE
                    WHEN last_autovacuum IS NULL THEN 'WARNING: Never vacuumed'
                    WHEN now() - last_autovacuum > interval '7 days' THEN 'WARNING: Vacuum > 7 days ago'
                    ELSE 'OK'
                END AS status
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT 10;
        """,
    },
    {
        "name": "pg_transaction_id_age",
        "description": "Database transaction ID age — wraparound risk",
        "category": "maintenance",
        "severity": "critical",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                datname,
                age(datfrozenxid) AS txid_age,
                ROUND(age(datfrozenxid)::numeric / 2147483647 * 100, 2) AS wraparound_pct,
                CASE
                    WHEN age(datfrozenxid) > 1500000000
                        THEN 'CRITICAL: Age > 1.5 billion — immediate VACUUM required'
                    WHEN age(datfrozenxid) > 1000000000
                        THEN 'WARNING: Age > 1 billion — plan VACUUM soon'
                    ELSE 'OK'
                END AS status
            FROM pg_database
            WHERE datname NOT IN ('template0', 'template1', 'rdsadmin')
            ORDER BY age(datfrozenxid) DESC;
        """,
    },
    {
        "name": "pg_aged_tables",
        "description": "Top 5 tables with oldest transaction IDs",
        "category": "maintenance",
        "severity": "critical",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                schemaname || '.' || relname AS table_name,
                age(relfrozenxid) AS xid_age,
                ROUND(age(relfrozenxid)::numeric / 2147483647 * 100, 2) AS wraparound_pct,
                pg_size_pretty(pg_total_relation_size(relid)) AS size,
                CASE
                    WHEN age(relfrozenxid) > 1500000000
                        THEN 'CRITICAL: Immediate VACUUM FREEZE required'
                    WHEN age(relfrozenxid) > 1000000000
                        THEN 'WARNING: Plan VACUUM FREEZE'
                    ELSE 'OK'
                END AS status
            FROM pg_stat_user_tables
            ORDER BY age(relfrozenxid) DESC
            LIMIT 5;
        """,
    },
]

# ============================================================================
# SECTION 8 — INDEX OPTIMIZATION
# ============================================================================

PG_OPTIMIZATION: List[Dict[str, Any]] = [
    {
        "name": "pg_unused_indexes",
        "description": "Indexes never used since last stats reset",
        "category": "optimization",
        "severity": "warning",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                schemaname || '.' || relname AS table_name,
                indexrelname AS index_name,
                pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
                idx_scan AS scans_since_reset,
                CASE
                    WHEN idx_scan = 0 THEN 'WARNING: Never used — candidate for removal'
                    WHEN idx_scan < 10 THEN 'INFO: Rarely used'
                    ELSE 'OK'
                END AS status
            FROM pg_stat_user_indexes
            WHERE idx_scan = 0
              AND indexrelname NOT LIKE '%pkey%'
              AND indexrelname NOT LIKE '%_pk'
              AND pg_relation_size(indexrelid) > 8192
            ORDER BY pg_relation_size(indexrelid) DESC
            LIMIT 20;
        """,
    },
    {
        "name": "pg_duplicate_indexes",
        "description": "Indexes with identical definitions (redundant)",
        "category": "optimization",
        "severity": "warning",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                pg_size_pretty(sum(pg_relation_size(idx))::bigint) AS total_wasted,
                (array_agg(idx))[1] AS index_to_keep,
                array_remove(array_agg(idx), (array_agg(idx))[1]) AS indexes_to_drop,
                indrelid::regclass AS table_name,
                count(*) AS duplicate_count
            FROM (
                SELECT indexrelid::regclass AS idx,
                       indrelid,
                       indkey
                FROM pg_index
                WHERE indisvalid
            ) sub
            GROUP BY indrelid, indkey
            HAVING count(*) > 1
            ORDER BY sum(pg_relation_size(idx)) DESC
            LIMIT 10;
        """,
    },
    {
        "name": "pg_missing_indexes_fk",
        "description": "Foreign keys without supporting indexes",
        "category": "optimization",
        "severity": "warning",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                conrelid::regclass AS table_name,
                conname AS constraint_name,
                a.attname AS column_name,
                'WARNING: No index on FK column — JOINs may full-scan' AS status
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attnum = ANY(c.conkey) AND a.attrelid = c.conrelid
            WHERE contype = 'f'
              AND NOT EXISTS (
                  SELECT 1 FROM pg_index i
                  WHERE i.indrelid = c.conrelid
                    AND a.attnum = ANY(i.indkey)
              )
            ORDER BY conrelid::regclass::text
            LIMIT 20;
        """,
    },
]

# ============================================================================
# SECTION 9 — SUMMARY HEALTH SCORE
# ============================================================================

PG_SUMMARY: List[Dict[str, Any]] = [
    {
        "name": "pg_health_score",
        "description": "Composite PostgreSQL health score (100 points max, 10 dimensions)",
        "category": "summary",
        "severity": "baseline",
        "engine": "postgresql",
        "min_version": "10",
        "sql": """
            SELECT
                -- Dimension 1: Connection Health (0-10)
                CASE WHEN (SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL)::float
                     / current_setting('max_connections')::float < 0.75
                     THEN 10 ELSE 0 END AS connection_score,
                -- Dimension 2: Cache Hit Ratio (0-10)
                CASE WHEN (SELECT ROUND(sum(blks_hit)::numeric / NULLIF(sum(blks_hit) + sum(blks_read), 0) * 100, 2)
                          FROM pg_stat_database WHERE datname = current_database()) > 99
                     THEN 10 ELSE 0 END AS cache_score,
                -- Dimension 3: Transaction ID Age (0-10)
                CASE WHEN (SELECT max(age(datfrozenxid)) FROM pg_database
                          WHERE datname NOT IN ('template0','template1','rdsadmin')) < 1000000000
                     THEN 10 ELSE 0 END AS txid_score,
                -- Dimension 4: Table Bloat (0-10)
                CASE WHEN (SELECT count(*) FROM pg_stat_user_tables
                          WHERE n_dead_tup::numeric / NULLIF(n_live_tup, 0) > 0.3) = 0
                     THEN 10 ELSE 0 END AS bloat_score,
                -- Dimension 5: Autovacuum Running (0-10)
                CASE WHEN current_setting('autovacuum') = 'on'
                     THEN 10 ELSE 0 END AS vacuum_score,
                -- Dimension 6: No Long Queries (0-10)
                CASE WHEN (SELECT count(*) FROM pg_stat_activity
                          WHERE state = 'active' AND query_start < now() - interval '5 minutes'
                          AND usename NOT IN ('rdsadmin')) = 0
                     THEN 10 ELSE 0 END AS long_query_score,
                -- Dimension 7: Unused Indexes < 10 (0-10)
                CASE WHEN (SELECT count(*) FROM pg_stat_user_indexes
                          WHERE idx_scan = 0 AND indexrelname NOT LIKE '%pkey%') < 10
                     THEN 10 ELSE 0 END AS index_score,
                -- Dimension 8: No Lock Waits (0-10)
                CASE WHEN (SELECT count(*) FROM pg_locks WHERE NOT granted) < 3
                     THEN 10 ELSE 0 END AS lock_score,
                -- Dimension 9: pg_stat_statements Enabled (0-10)
                CASE WHEN EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')
                     THEN 10 ELSE 0 END AS instrumentation_score,
                -- Dimension 10: Logging Configured (0-10)
                CASE WHEN current_setting('log_min_duration_statement')::int >= 0
                     AND current_setting('log_min_duration_statement')::int <= 5000
                     THEN 10 ELSE 0 END AS logging_score;
        """,
    },
]

# ============================================================================
# Master PostgreSQL Registry
# ============================================================================

ALL_PG_HEALTH_CHECKS: List[Dict[str, Any]] = (
    PG_SERVER_INFO
    + PG_CONFIGURATION
    + PG_ACTIVITY
    + PG_REPLICATION
    + PG_STORAGE
    + PG_PERFORMANCE
    + PG_MAINTENANCE
    + PG_OPTIMIZATION
    + PG_SUMMARY
)

PG_CATEGORIES: Dict[str, List[Dict[str, Any]]] = {
    "connections":   PG_SERVER_INFO,
    "configuration": PG_CONFIGURATION,
    "activity":      PG_ACTIVITY,
    "replication":   PG_REPLICATION,
    "storage":       PG_STORAGE,
    "performance":   PG_PERFORMANCE,
    "maintenance":   PG_MAINTENANCE,
    "optimization":  PG_OPTIMIZATION,
    "summary":       PG_SUMMARY,
}

PG_CATEGORY_DESCRIPTIONS = {
    "connections":   "Connection utilization, server info, session counts",
    "configuration": "Key PostgreSQL settings (shared_buffers, work_mem, autovacuum)",
    "activity":      "Active queries, lock detection, long-running transactions",
    "replication":   "Replication lag, WAL position, slot status",
    "storage":       "Database sizes, table sizes, table bloat (dead tuples)",
    "performance":   "Top queries by time/IO, temp file usage (pg_stat_statements)",
    "maintenance":   "Vacuum status, transaction ID age, wraparound risk",
    "optimization":  "Unused indexes, duplicate indexes, missing FK indexes",
    "summary":       "Composite health score — 10 dimensions, 100 points max",
}


def get_pg_checks_by_category(category: str) -> List[Dict[str, Any]]:
    return PG_CATEGORIES.get(category, [])


def get_pg_check_by_name(name: str) -> Dict[str, Any] | None:
    return next((c for c in ALL_PG_HEALTH_CHECKS if c["name"] == name), None)
