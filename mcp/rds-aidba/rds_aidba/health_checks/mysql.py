"""
Health Check Query Registry
Author: Kiran Mayee Mulupuru, Sr. Specialist Database TAM, AWS Enterprise Support

✅ UPDATED:
- environment_detection REMOVED from ALL_HEALTH_CHECKS and CATEGORIES
  (environment is now probed silently once at session start via
   MySQLMCPClient.probe_environment() — never run as a visible health check)
- Full SQL text display (4000 chars)
- rdsadmin query filtering
- CloudWatch Logs health checks
"""

from typing import Any, Dict, List

# ============================================================================
# SECTION 1 — CONNECTION & BASIC INFO
# ============================================================================

CONNECTIONS: List[Dict[str, Any]] = [
    {
        "name": "server_information",
        "description": "MySQL version, hostname, port, uptime, connection counts, timeouts",
        "category": "connections",
        "severity": "baseline",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT 'MySQL Version' AS metric, @@version AS value,
                'Complete MySQL version and build information' AS description,
                CASE
                    WHEN @@version LIKE '8.%' THEN 'OK: MySQL 8.x'
                    WHEN @@version LIKE '5.7%' THEN 'OK: MySQL 5.7'
                    WHEN @@version LIKE '5.6%' THEN 'WARNING: MySQL 5.6 - End of life'
                    ELSE 'INFO: Version detected'
                END AS status
            UNION ALL
            SELECT 'Server ID', CAST(@@server_id AS CHAR),
                'Unique server identifier for replication topology',
                CASE
                    WHEN @@server_id = 0 THEN 'WARNING: Server ID not set'
                    WHEN @@server_id = 1 THEN 'INFO: Default server ID'
                    ELSE 'OK: Server ID configured'
                END
            UNION ALL
            SELECT 'Hostname', @@hostname, 'Server hostname', 'INFO'
            UNION ALL
            SELECT 'Port', CAST(@@port AS CHAR), 'TCP port number',
                CASE WHEN @@port = 3306 THEN 'OK: Standard MySQL port'
                     ELSE 'INFO: Custom port' END
            UNION ALL
            SELECT 'Max Connections', CAST(@@max_connections AS CHAR),
                'Maximum allowed concurrent connections',
                CASE
                    WHEN @@max_connections < 100 THEN 'WARNING: Low connection limit'
                    WHEN @@max_connections > 1000 THEN 'INFO: Very high connection limit'
                    ELSE 'OK'
                END
            UNION ALL
            SELECT 'Current Connections',
                COALESCE((SELECT VARIABLE_VALUE FROM performance_schema.global_status
                         WHERE VARIABLE_NAME = 'Threads_connected'), '0'),
                'Currently active client connections',
                CONCAT(ROUND(COALESCE(
                    CAST((SELECT VARIABLE_VALUE FROM performance_schema.global_status
                          WHERE VARIABLE_NAME = 'Threads_connected') AS UNSIGNED), 0)
                    * 100.0 / @@max_connections, 1), '% of max_connections in use')
            UNION ALL
            SELECT 'Server Uptime',
                COALESCE((SELECT VARIABLE_VALUE FROM performance_schema.global_status
                         WHERE VARIABLE_NAME = 'Uptime'), 'N/A'),
                'Seconds since MySQL server startup',
                CASE
                    WHEN CAST(COALESCE(
                        (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                         WHERE VARIABLE_NAME = 'Uptime'), '999999') AS UNSIGNED) < 300
                    THEN 'WARNING: Server recently restarted (<5 min)'
                    ELSE 'OK: Server running stable'
                END
            ORDER BY FIELD(metric,
                'MySQL Version','Server ID','Hostname','Port',
                'Max Connections','Current Connections','Server Uptime');
        """,
    },
    {
        "name": "connection_overview",
        "description": "Connection utilisation %, running/sleeping/waiting thread counts",
        "category": "connections",
        "severity": "warning",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT 'CONNECTION_OVERVIEW' AS metric_category,
                metric_name, current_value, threshold_status, description
            FROM (
                SELECT 'Total Connections' AS metric_name,
                    CONCAT(
                        COALESCE((SELECT VARIABLE_VALUE FROM performance_schema.global_status
                                 WHERE VARIABLE_NAME = 'Threads_connected'), '0'),
                        ' / ', @@max_connections, ' (',
                        ROUND(COALESCE(
                            (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                             WHERE VARIABLE_NAME = 'Threads_connected'), 0)
                            * 100.0 / @@max_connections, 1), '%)'
                    ) AS current_value,
                    CASE
                        WHEN COALESCE(
                            (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                             WHERE VARIABLE_NAME = 'Threads_connected'), 0)
                            / @@max_connections > 0.9
                        THEN 'CRITICAL: >90% connection usage'
                        WHEN COALESCE(
                            (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                             WHERE VARIABLE_NAME = 'Threads_connected'), 0)
                            / @@max_connections > 0.8
                        THEN 'WARNING: >80% connection usage'
                        ELSE 'OK'
                    END AS threshold_status,
                    'Current active connections vs maximum allowed' AS description,
                    1 AS sort_order
                UNION ALL
                SELECT 'Running Threads',
                    COALESCE((SELECT VARIABLE_VALUE FROM performance_schema.global_status
                             WHERE VARIABLE_NAME = 'Threads_running'), '0'),
                    CASE
                        WHEN COALESCE(
                            (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                             WHERE VARIABLE_NAME = 'Threads_running'), 0) > 50
                        THEN 'CRITICAL: High CPU activity'
                        WHEN COALESCE(
                            (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                             WHERE VARIABLE_NAME = 'Threads_running'), 0) > 20
                        THEN 'WARNING: Elevated activity'
                        ELSE 'OK'
                    END,
                    'Threads currently executing queries (high = CPU bottleneck)', 2
            ) t ORDER BY sort_order;
        """,
    },
]

# ============================================================================
# SECTION 2 — SYSTEM CONFIGURATION
# ✅ environment_detection REMOVED — now handled by MySQLMCPClient.probe_environment()
# ============================================================================

CONFIGURATION: List[Dict[str, Any]] = [
    {
        "name": "system_variables",
        "description": "Key MySQL system variables: performance_schema, InnoDB engine status",
        "category": "configuration",
        "severity": "baseline",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT
                'performance_schema'   AS variable_name,
                CAST(@@performance_schema AS CHAR) AS current_value,
                CASE WHEN @@performance_schema = 1
                     THEN 'OK: Enabled — full monitoring available'
                     ELSE 'WARNING: Disabled — limited monitoring capability'
                END AS status,
                'Required for query-level monitoring and diagnostics' AS description
            UNION ALL
            SELECT
                'innodb_buffer_pool_size',
                CONCAT(ROUND(@@innodb_buffer_pool_size/1073741824, 2), ' GB'),
                CASE
                    WHEN @@innodb_buffer_pool_size < 134217728
                    THEN 'WARNING: Very small buffer pool (<128 MB)'
                    ELSE 'OK'
                END,
                'InnoDB buffer pool — primary memory cache for data and indexes'
            UNION ALL
            SELECT
                'max_connections',
                CAST(@@max_connections AS CHAR),
                CASE
                    WHEN @@max_connections < 100 THEN 'WARNING: Low'
                    WHEN @@max_connections > 2000 THEN 'INFO: Very high'
                    ELSE 'OK'
                END,
                'Maximum concurrent connections allowed'
            UNION ALL
            SELECT
                'wait_timeout',
                CAST(@@wait_timeout AS CHAR),
                CASE
                    WHEN @@wait_timeout > 28800 THEN 'WARNING: Very long idle timeout'
                    WHEN @@wait_timeout < 60    THEN 'WARNING: Very short idle timeout'
                    ELSE 'OK'
                END,
                'Seconds before idle connections are closed'
            UNION ALL
            SELECT
                'innodb_flush_log_at_trx_commit',
                CAST(@@innodb_flush_log_at_trx_commit AS CHAR),
                CASE @@innodb_flush_log_at_trx_commit
                    WHEN 1 THEN 'OK: Full ACID compliance'
                    WHEN 2 THEN 'INFO: OS-buffered writes (minor durability trade-off)'
                    WHEN 0 THEN 'WARNING: Log buffered in memory (data loss risk on crash)'
                    ELSE 'INFO'
                END,
                'Controls InnoDB redo log flush behaviour'
            UNION ALL
            SELECT
                'slow_query_log',
                CAST(@@slow_query_log AS CHAR),
                CASE WHEN @@slow_query_log = 1
                     THEN 'OK: Slow query logging enabled'
                     ELSE 'INFO: Slow query logging disabled'
                END,
                'Enables logging of queries exceeding long_query_time'
            UNION ALL
            SELECT
                'long_query_time',
                CAST(@@long_query_time AS CHAR),
                CASE
                    WHEN @@long_query_time < 0.1 THEN 'INFO: Very aggressive threshold (<0.1s)'
                    WHEN @@long_query_time > 10  THEN 'WARNING: High threshold (>10s) — may miss slow queries'
                    ELSE 'OK'
                END,
                'Queries exceeding this threshold are logged as slow queries';
        """,
    },
]

# ============================================================================
# SECTION 3 — CURRENT ACTIVITY
# ============================================================================

ACTIVITY: List[Dict[str, Any]] = [
    {
        "name": "lock_detection",
        "description": "Active lock waits, blocked threads, long-running queries",
        "category": "activity",
        "severity": "critical",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT 'PROCESSLIST_ANALYSIS' AS detection_method,
                CONCAT('Process_ID: ', ID,
                       ' | Command: ', COMMAND,
                       ' | State: ',   STATE) AS lock_info,
                CONCAT('User: ', USER, '@', HOST,
                       ' | DB: ', COALESCE(DB,'None')) AS connection_details,
                CONCAT('Duration: ', TIME, ' seconds') AS timing_info,
                LEFT(COALESCE(INFO,'No active query'), 500) AS query_info,
                CASE
                    WHEN STATE LIKE '%Waiting for table%' THEN 'TABLE_LOCK_WAIT'
                    WHEN STATE LIKE '%Waiting for%lock%' THEN 'ROW_LOCK_WAIT'
                    WHEN STATE LIKE '%Locked%'           THEN 'LOCKED'
                    WHEN COMMAND = 'Query' AND TIME > 30 THEN 'LONG_RUNNING_QUERY'
                    ELSE 'NORMAL'
                END AS lock_status
            FROM information_schema.PROCESSLIST
            WHERE (STATE LIKE '%lock%'
                OR STATE LIKE '%wait%'
                OR (COMMAND = 'Query' AND TIME > 10))
              AND USER != 'rdsadmin'
            ORDER BY TIME DESC
            LIMIT 50;
        """,
    },
    {
        "name": "active_queries",
        "description": "All currently executing queries with full text, excluding rdsadmin",
        "category": "activity",
        "severity": "warning",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT
                p.ID            AS process_id,
                p.USER,
                p.HOST,
                p.DB,
                p.COMMAND,
                p.TIME          AS seconds_running,
                p.STATE,
                LEFT(COALESCE(p.INFO, ''), 500) AS query_text,
                CASE
                    WHEN p.TIME > 300 THEN 'CRITICAL: >5 min'
                    WHEN p.TIME > 60  THEN 'WARNING: >1 min'
                    WHEN p.TIME > 10  THEN 'INFO: >10 sec'
                    ELSE 'OK'
                END AS duration_status
            FROM information_schema.PROCESSLIST p
            WHERE p.USER != 'rdsadmin'
              AND p.COMMAND != 'Sleep'
            ORDER BY p.TIME DESC
            LIMIT 30;
        """,
    },
    {
        "name": "innodb_lock_waits",
        "description": "InnoDB row-level lock waits with blocking and waiting query details",
        "category": "activity",
        "severity": "critical",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT
                r.trx_id            AS waiting_trx_id,
                r.trx_mysql_thread_id AS waiting_thread,
                r.trx_query         AS waiting_query,
                b.trx_id            AS blocking_trx_id,
                b.trx_mysql_thread_id AS blocking_thread,
                b.trx_query         AS blocking_query,
                b.trx_started       AS blocking_started,
                TIMESTAMPDIFF(SECOND, b.trx_started, NOW()) AS blocking_duration_sec
            FROM information_schema.innodb_lock_waits w
            JOIN information_schema.innodb_trx b
                ON b.trx_id = w.blocking_trx_id
            JOIN information_schema.innodb_trx r
                ON r.trx_id = w.requesting_trx_id
            ORDER BY blocking_duration_sec DESC
            LIMIT 20;
        """,
    },
]

# ============================================================================
# SECTION 4 — REPLICATION STATUS
# ============================================================================

REPLICATION: List[Dict[str, Any]] = [
    {
        "name": "replication_configuration",
        "description": "Binary logging, GTID mode, server ID, replication threads",
        "category": "replication",
        "severity": "critical",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT 'Binary Logging' AS component,
                CASE WHEN @@log_bin = 1 THEN 'Enabled' ELSE 'Disabled' END AS status,
                CASE
                    WHEN @@log_bin = 1
                    THEN CONCAT('Format: ', @@binlog_format,
                                ' | Retention: ', @@binlog_expire_logs_seconds, 's')
                    ELSE 'Binary logging not enabled'
                END AS details,
                'Binary log configuration' AS description
            UNION ALL
            SELECT 'GTID Mode',
                @@gtid_mode,
                CONCAT('Enforce Consistency: ', @@enforce_gtid_consistency),
                'Global Transaction ID configuration'
            UNION ALL
            SELECT 'Server ID',
                CAST(@@server_id AS CHAR),
                CASE WHEN @@server_id = 0
                     THEN 'WARNING: Server ID not configured'
                     ELSE 'OK: Unique server ID set'
                END,
                'Server identifier in replication topology'
            UNION ALL
            SELECT 'Read Only',
                CASE WHEN @@read_only = 1 THEN 'ON (Replica/Reader)'
                     ELSE 'OFF (Primary/Writer)' END,
                CASE WHEN @@super_read_only = 1
                     THEN 'super_read_only also ON'
                     ELSE 'super_read_only OFF'
                END,
                'Read-only status indicates writer vs replica role';
        """,
    },
]

# ============================================================================
# SECTION 5 — STORAGE & CAPACITY
# ============================================================================

STORAGE: List[Dict[str, Any]] = [
    {
        "name": "database_size_health",
        "description": "Per-schema data/index size, fragmentation %, row counts",
        "category": "storage",
        "severity": "baseline",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT
                table_schema                                        AS schema_name,
                COUNT(*)                                            AS table_count,
                ROUND(SUM(data_length)  / 1048576, 2)              AS data_size_mb,
                ROUND(SUM(index_length) / 1048576, 2)              AS index_size_mb,
                ROUND(SUM(data_length + index_length) / 1048576, 2) AS total_size_mb,
                SUM(table_rows)                                     AS estimated_rows
            FROM information_schema.tables
            WHERE table_schema NOT IN
                ('information_schema','performance_schema','mysql','sys')
              AND table_type = 'BASE TABLE'
            GROUP BY table_schema
            ORDER BY total_size_mb DESC;
        """,
    },
    {
        "name": "largest_tables",
        "description": "Top 20 largest tables by total size with fragmentation",
        "category": "storage",
        "severity": "baseline",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT
                CONCAT(table_schema, '.', table_name) AS table_name,
                engine,
                ROUND((data_length + index_length) / 1048576, 2) AS total_size_mb,
                ROUND(data_free / 1048576, 2)                     AS free_space_mb,
                ROUND(
                    data_free / NULLIF(data_length + index_length, 0) * 100, 1
                ) AS fragmentation_pct,
                table_rows AS estimated_rows,
                CASE
                    WHEN data_free / NULLIF(data_length + index_length, 0) > 0.3
                    THEN 'WARNING: >30% fragmentation'
                    WHEN data_free / NULLIF(data_length + index_length, 0) > 0.15
                    THEN 'INFO: >15% fragmentation'
                    ELSE 'OK'
                END AS frag_status
            FROM information_schema.tables
            WHERE table_schema NOT IN
                ('information_schema','performance_schema','mysql','sys')
              AND table_type  = 'BASE TABLE'
              AND (data_length + index_length) > 1048576
            ORDER BY total_size_mb DESC
            LIMIT 20;
        """,
    },
]

# ============================================================================
# SECTION 6 — INNODB / BUFFER POOL
# ============================================================================

INNODB_BUFFER_POOL: List[Dict[str, Any]] = [
    {
        "name": "buffer_pool_statistics",
        "description": "InnoDB buffer pool size, hit ratio, free/dirty pages",
        "category": "innodb",
        "severity": "critical",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT 'Buffer Pool Size' AS metric_name,
                CONCAT(ROUND(@@innodb_buffer_pool_size/1073741824, 2), ' GB') AS metric_value,
                'Total memory allocated for InnoDB buffer pool' AS description,
                CASE
                    WHEN @@innodb_buffer_pool_size < 134217728
                    THEN 'WARNING: Very small buffer pool (<128 MB)'
                    ELSE 'OK'
                END AS health_status
            UNION ALL
            SELECT 'Buffer Pool Hit Ratio',
                CONCAT(ROUND((1 - CAST(COALESCE(
                    (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                     WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads'), '0') AS DECIMAL)
                    / NULLIF(CAST(COALESCE(
                        (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                         WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests'), '1')
                    AS DECIMAL), 0)) * 100, 2), '%'),
                'Percentage of reads served from memory (target: >99%)',
                CASE
                    WHEN (1 - CAST(COALESCE(
                        (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                         WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads'), '0') AS DECIMAL)
                        / NULLIF(CAST(COALESCE(
                            (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                             WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests'), '1')
                        AS DECIMAL), 0)) * 100 < 95
                    THEN 'WARNING: Low hit ratio — consider increasing buffer pool'
                    ELSE 'OK: Good buffer pool performance'
                END
            UNION ALL
            SELECT 'Dirty Pages',
                COALESCE(
                    (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                     WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_dirty'), '0'),
                'Pages modified in memory not yet flushed to disk',
                'INFO'
            UNION ALL
            SELECT 'Free Pages',
                COALESCE(
                    (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                     WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_free'), '0'),
                'Unallocated pages in the buffer pool',
                'INFO';
        """,
    },
]

# ============================================================================
# SECTION 7 — PERFORMANCE
# ============================================================================

PERFORMANCE: List[Dict[str, Any]] = [
    {
        "name": "top_queries_by_total_time",
        "description": "Top 50 queries by total execution time with full SQL and optimization guidance",
        "category": "performance",
        "severity": "warning",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT
                SCHEMA_NAME AS schema_name,
                SUBSTRING(DIGEST_TEXT, 1, 4000)                            AS query_text,
                SUBSTRING(COALESCE(QUERY_SAMPLE_TEXT, DIGEST_TEXT), 1, 4000) AS sample_query,
                COUNT_STAR                                                  AS exec_count,
                ROUND(SUM_TIMER_WAIT  / 1000000000, 3)                     AS total_exec_time_sec,
                ROUND(AVG_TIMER_WAIT  / 1000000000, 6)                     AS avg_exec_time_sec,
                ROUND(MAX_TIMER_WAIT  / 1000000000, 3)                     AS max_exec_time_sec,
                CASE
                    WHEN ROUND(AVG_TIMER_WAIT/1000000000,6) > 10 THEN 'VERY SLOW (>10s)'
                    WHEN ROUND(AVG_TIMER_WAIT/1000000000,6) > 1  THEN 'SLOW (1-10s)'
                    WHEN ROUND(AVG_TIMER_WAIT/1000000000,6) > .1 THEN 'MEDIUM (0.1-1s)'
                    ELSE 'FAST (<0.1s)'
                END AS performance_category,
                ROUND(SUM_LOCK_TIME / 1000000000, 3)                       AS total_lock_time_sec,
                ROUND((SUM_LOCK_TIME / NULLIF(SUM_TIMER_WAIT,0))*100, 2)   AS lock_time_pct,
                SUM_ROWS_EXAMINED                                           AS total_rows_examined,
                SUM_ROWS_SENT                                               AS total_rows_sent,
                ROUND(SUM_ROWS_EXAMINED / NULLIF(SUM_ROWS_SENT,0), 2)      AS rows_examined_per_sent,
                ROUND((SUM_NO_INDEX_USED / NULLIF(COUNT_STAR,0))*100, 2)   AS no_index_used_pct,
                ROUND(((SUM_SELECT_SCAN+SUM_SELECT_FULL_JOIN)
                        / NULLIF(COUNT_STAR,0))*100, 2)                    AS full_scan_pct,
                SUM_CREATED_TMP_DISK_TABLES                                AS tmp_disk_tables,
                SUM_ERRORS                                                  AS total_errors,
                FIRST_SEEN,
                LAST_SEEN,
                CASE
                    WHEN (SUM_NO_INDEX_USED/NULLIF(COUNT_STAR,0))*100 > 50
                        THEN 'Missing indexes on WHERE/JOIN columns'
                    WHEN SUM_ROWS_EXAMINED/NULLIF(SUM_ROWS_SENT,0) > 1000
                        THEN 'Poor efficiency — examining too many rows per result'
                    WHEN ROUND((SUM_LOCK_TIME/NULLIF(SUM_TIMER_WAIT,0))*100,2) > 20
                        THEN 'High lock contention'
                    WHEN SUM_CREATED_TMP_DISK_TABLES > 100
                        THEN 'Creating temp tables on disk'
                    WHEN ROUND(AVG_TIMER_WAIT/1000000000,6) > 10
                        THEN 'Very slow execution time'
                    ELSE 'No critical issues detected'
                END AS performance_issue,
                CASE
                    WHEN ROUND(AVG_TIMER_WAIT/1000000000,6) > 10 AND COUNT_STAR > 10
                        THEN 'URGENT: Add indexes or rewrite immediately'
                    WHEN ROUND(AVG_TIMER_WAIT/1000000000,6) > 1  AND COUNT_STAR > 100
                        THEN 'HIGH: Optimize with indexes or query rewrite'
                    WHEN SUM_ROWS_EXAMINED/NULLIF(SUM_ROWS_SENT,0) > 1000 AND COUNT_STAR > 50
                        THEN 'HIGH: Add covering index'
                    WHEN (SUM_NO_INDEX_USED/NULLIF(COUNT_STAR,0))*100 > 50
                        THEN 'MEDIUM: Add indexes on WHERE/JOIN columns'
                    WHEN SUM_CREATED_TMP_DISK_TABLES > 100
                        THEN 'MEDIUM: Increase tmp_table_size or optimize query'
                    ELSE 'OK: Acceptable performance'
                END AS optimization_priority
            FROM performance_schema.events_statements_summary_by_digest
            WHERE DIGEST_TEXT IS NOT NULL
              AND COUNT_STAR  >= 5
              AND SCHEMA_NAME IS NOT NULL
              AND DIGEST_TEXT NOT LIKE '%rds_heartbeat%'
              AND DIGEST_TEXT NOT LIKE '%rds_replication_status%'
              AND DIGEST_TEXT NOT LIKE '%information_schema.replica%'
              AND DIGEST_TEXT NOT LIKE '%mysql.rds_%'
              AND SCHEMA_NAME != 'mysql'
            ORDER BY total_exec_time_sec DESC, COUNT_STAR DESC
            LIMIT 50;
        """,
    },
]

# ============================================================================
# SECTION 8 — MAINTENANCE
# ============================================================================

MAINTENANCE: List[Dict[str, Any]] = [
    {
        "name": "table_maintenance_status",
        "description": "Fragmentation %, free space, maintenance commands for tables >1 MB",
        "category": "maintenance",
        "severity": "warning",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT
                CONCAT(table_schema, '.', table_name)                    AS table_name,
                engine                                                    AS storage_engine,
                ROUND((data_length + index_length) / 1048576, 2)         AS total_size_mb,
                ROUND(data_free / 1048576, 2)                            AS fragmented_space_mb,
                ROUND(
                    data_free / NULLIF(data_length + index_length, 0) * 100, 2
                ) AS fragmentation_pct,
                CASE
                    WHEN data_free / NULLIF(data_length + index_length,0) >= 0.5
                    THEN 'CRITICAL: >=50% fragmentation'
                    WHEN data_free / NULLIF(data_length + index_length,0) >= 0.25
                    THEN 'WARNING: >=25% fragmentation'
                    ELSE 'OK'
                END AS fragmentation_status
            FROM information_schema.tables
            WHERE table_schema NOT IN
                ('information_schema','performance_schema','mysql','sys')
              AND table_type = 'BASE TABLE'
              AND (data_length + index_length) > 1048576
            ORDER BY fragmentation_pct DESC
            LIMIT 50;
        """,
    },
]

# ============================================================================
# SECTION 9 — OPTIMIZATION
# ============================================================================

OPTIMIZATION: List[Dict[str, Any]] = [
    {
        "name": "index_usage_analysis",
        "description": "Unused and low-use indexes with drop recommendations",
        "category": "optimization",
        "severity": "warning",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT
                i.object_schema                         AS schema_name,
                i.object_name                           AS table_name,
                COALESCE(i.index_name, 'TABLE SCAN')    AS index_name,
                i.count_read                            AS read_count,
                i.count_write                           AS write_count,
                CASE
                    WHEN i.index_name = 'PRIMARY'   THEN 'PRIMARY KEY'
                    WHEN i.index_name IS NULL        THEN 'TABLE SCAN'
                    ELSE 'SECONDARY INDEX'
                END AS index_type,
                CASE
                    WHEN i.count_read = 0 AND i.index_name IS NOT NULL THEN 'UNUSED'
                    WHEN i.count_read = 0 THEN 'WRITE ONLY'
                    WHEN i.count_write = 0 THEN 'READ ONLY'
                    ELSE 'READ/WRITE'
                END AS access_pattern,
                CASE
                    WHEN i.index_name = 'PRIMARY'
                        THEN 'PRIMARY KEY: Required — do not drop'
                    WHEN i.count_read = 0 AND i.index_name IS NOT NULL
                        THEN CONCAT('UNUSED: Consider DROP INDEX `',
                                    i.index_name, '` ON `',
                                    i.object_schema, '`.`', i.object_name, '`;')
                    WHEN i.count_read < 10 AND i.index_name IS NOT NULL
                        THEN CONCAT('LOW USAGE: Only ', i.count_read,
                                    ' reads — review necessity')
                    ELSE CONCAT('ACTIVE: ', i.count_read, ' reads / ',
                                i.count_write, ' writes')
                END AS recommendation
            FROM performance_schema.table_io_waits_summary_by_index_usage i
            WHERE i.object_schema NOT IN
                ('information_schema','performance_schema','mysql','sys')
            ORDER BY i.count_read ASC, (i.count_write - i.count_read) DESC
            LIMIT 50;
        """,
    },
]

# ============================================================================
# SECTION 10 — SUMMARY
# ============================================================================

SUMMARY: List[Dict[str, Any]] = [
    {
        "name": "health_summary",
        "description": "Consolidated health scorecard",
        "category": "summary",
        "severity": "baseline",
        "aurora_only": False,
        "min_version": "5.7",
        "sql": """
            SELECT 'Server Uptime' AS check_category,
                CONCAT(ROUND(COALESCE(
                    (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                     WHERE VARIABLE_NAME = 'Uptime'), 0) / 86400, 1), ' days') AS current_status,
                CASE
                    WHEN COALESCE(
                        (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                         WHERE VARIABLE_NAME = 'Uptime'), 0) < 300
                    THEN 'WARNING: Recently restarted'
                    ELSE 'OK'
                END AS priority_level,
                'Server uptime' AS recommendation
            UNION ALL
            SELECT 'Connection Usage',
                CONCAT(
                    COALESCE(
                        (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                         WHERE VARIABLE_NAME = 'Threads_connected'), 0),
                    ' of ', @@max_connections),
                CASE
                    WHEN COALESCE(
                        (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                         WHERE VARIABLE_NAME = 'Threads_connected'), 0)
                        / NULLIF(@@max_connections, 0) > 0.8
                    THEN 'CRITICAL: >80% usage'
                    ELSE 'OK'
                END,
                'Connection utilisation'
            UNION ALL
            SELECT 'Buffer Pool Hit Ratio',
                CONCAT(ROUND((1 - CAST(COALESCE(
                    (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                     WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads'), '0') AS DECIMAL)
                    / NULLIF(CAST(COALESCE(
                        (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                         WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests'), '1')
                    AS DECIMAL), 0)) * 100, 2), '%'),
                CASE
                    WHEN (1 - CAST(COALESCE(
                        (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                         WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads'), '0') AS DECIMAL)
                        / NULLIF(CAST(COALESCE(
                            (SELECT VARIABLE_VALUE FROM performance_schema.global_status
                             WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests'), '1')
                        AS DECIMAL), 0)) * 100 < 95
                    THEN 'WARNING: Low hit ratio'
                    ELSE 'OK'
                END,
                'InnoDB buffer pool efficiency';
        """,
    },
]

# ============================================================================
# SECTION 11 — CLOUDWATCH (metadata only — no SQL)
# ============================================================================

CLOUDWATCH: List[Dict[str, Any]] = [
    {
        "name": "cloudwatch_error_logs",
        "description": "CloudWatch error log analysis",
        "category": "cloudwatch",
        "severity": "critical",
        "aurora_only": False,
        "min_version": "5.7",
        "is_cloudwatch": True,
        "sql": None,
    },
    {
        "name": "cloudwatch_slow_query_logs",
        "description": "CloudWatch slow query log analysis",
        "category": "cloudwatch",
        "severity": "warning",
        "aurora_only": False,
        "min_version": "5.7",
        "is_cloudwatch": True,
        "sql": None,
    },
    {
        "name": "cloudwatch_audit_logs",
        "description": "CloudWatch audit log analysis",
        "category": "cloudwatch",
        "severity": "warning",
        "aurora_only": False,
        "min_version": "5.7",
        "is_cloudwatch": True,
        "sql": None,
    },
]

# ============================================================================
# Master registry
# ============================================================================

ALL_HEALTH_CHECKS: List[Dict[str, Any]] = (
    CONNECTIONS
    + CONFIGURATION
    + ACTIVITY
    + REPLICATION
    + STORAGE
    + INNODB_BUFFER_POOL
    + PERFORMANCE
    + MAINTENANCE
    + OPTIMIZATION
    + SUMMARY
    + CLOUDWATCH
)

CATEGORIES: Dict[str, List[Dict[str, Any]]] = {
    "connections":   CONNECTIONS,
    "configuration": CONFIGURATION,
    "activity":      ACTIVITY,
    "replication":   REPLICATION,
    "storage":       STORAGE,
    "innodb":        INNODB_BUFFER_POOL,
    "performance":   PERFORMANCE,
    "maintenance":   MAINTENANCE,
    "optimization":  OPTIMIZATION,
    "summary":       SUMMARY,
    "cloudwatch":    CLOUDWATCH,
}

CATEGORY_DESCRIPTIONS = {
    "connections":   "Connection utilization, thread activity, sleeping/waiting threads",
    "configuration": "Key system variables, performance_schema, InnoDB engine status",
    "innodb":        "Buffer pool hit ratio, dirty/free pages, I/O activity",
    "performance":   "Top queries by total time with full SQL, full table scans",
    "replication":   "Binary logging, GTID mode, read-only status, replica role",
    "storage":       "Table sizes, database sizes, fragmentation",
    "activity":      "Lock detection, active queries, long-running transactions",
    "maintenance":   "Table fragmentation, free space for tables >1 MB",
    "optimization":  "Index usage, unused indexes, drop recommendations",
    "summary":       "Consolidated health scorecard — uptime, connections, buffer pool",
    "cloudwatch":    "CloudWatch logs — error, slow query, audit log analysis",
}


def get_checks_by_category(category: str) -> List[Dict[str, Any]]:
    return CATEGORIES.get(category, [])


def get_check_by_name(name: str) -> Dict[str, Any] | None:
    return next((c for c in ALL_HEALTH_CHECKS if c["name"] == name), None)


def get_critical_checks() -> List[Dict[str, Any]]:
    return [c for c in ALL_HEALTH_CHECKS if c["severity"] == "critical"]


def get_aurora_checks() -> List[Dict[str, Any]]:
    return [c for c in ALL_HEALTH_CHECKS if c["aurora_only"]]


def get_cloudwatch_checks() -> List[Dict[str, Any]]:
    return [c for c in ALL_HEALTH_CHECKS if c.get("is_cloudwatch", False)]