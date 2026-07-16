# MySQL Health Check Queries — RDS & Aurora MySQL

## Overview

23 curated read-only diagnostic queries covering 9 categories for Aurora MySQL and RDS MySQL health assessment. All queries use `performance_schema`, `information_schema`, and `sys` schema — no data-plane writes.

### Prerequisites

- `performance_schema` enabled (default ON for Aurora MySQL 2.x+)
- Monitoring user: `SELECT` on `performance_schema.*`, `information_schema.*`, `sys.*`
- For Query 8.1: `sys` schema available (default for Aurora MySQL)

### Permissions Required

```sql
GRANT SELECT ON performance_schema.* TO 'monitor_user'@'%';
GRANT SELECT ON information_schema.* TO 'monitor_user'@'%';
GRANT SELECT ON sys.* TO 'monitor_user'@'%';
GRANT SELECT ON mysql.innodb_index_stats TO 'monitor_user'@'%';
GRANT SELECT ON mysql.ro_replica_status TO 'monitor_user'@'%';  -- Aurora only
GRANT PROCESS ON *.* TO 'monitor_user'@'%';
```

---

## Category 1: Server Information

### Query 1.1 — Server Information

**Purpose:** Establish baseline environment context — version, uptime, identification.

```sql
SELECT 
  @@version AS version,
  @@version_comment AS version_comment,
  @@server_uuid AS server_uuid,
  @@hostname AS hostname,
  @@port AS port,
  SEC_TO_TIME(VARIABLE_VALUE) AS uptime
FROM performance_schema.global_status
WHERE VARIABLE_NAME = 'Uptime';
```

**Interpretation:**
- `version_comment` contains "Aurora" → Aurora MySQL
- Short uptime (< 1 hour) + recent issues → Possible recent restart/failover

### Query 1.2 — Environment Detection

**Purpose:** Programmatically detect Aurora MySQL vs RDS MySQL vs self-managed.

```sql
SELECT 
  @@aurora_version AS aurora_version,
  @@innodb_read_only AS is_reader,
  @@version_comment AS platform;
```

**Interpretation:**
- `aurora_version IS NOT NULL` → Aurora MySQL
- `is_reader = 1` → Aurora Reader instance
- `aurora_version IS NULL` → RDS MySQL or self-managed

---

## Category 2: System Configuration

### Query 2.1 — Critical MySQL Variables

**Purpose:** Validate critical system variables affecting performance, durability, and observability.

```sql
SELECT 
  @@max_connections AS max_connections,
  @@innodb_buffer_pool_size / (1024*1024*1024) AS buffer_pool_gb,
  @@innodb_log_file_size / (1024*1024) AS log_file_mb,
  @@innodb_flush_log_at_trx_commit AS flush_at_commit,
  @@sync_binlog AS sync_binlog,
  @@long_query_time AS slow_query_threshold,
  @@slow_query_log AS slow_log_enabled,
  @@performance_schema AS perf_schema_enabled,
  @@innodb_file_per_table AS file_per_table,
  @@character_set_server AS charset,
  @@collation_server AS collation;
```

**Thresholds:**

| Variable | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|----------|--------|------------|-------------|
| max_connections | Scaled to instance class | Default unchanged | Exceeds instance memory |
| flush_at_commit | 1 (full durability) | 2 (relaxed) | 0 (no durability) |
| slow_log_enabled | 1 (ON) | — | 0 (OFF) |
| perf_schema_enabled | 1 (ON) | — | 0 (OFF) |
| long_query_time | ≤ 2 seconds | > 5 seconds | > 10 seconds |

**Aurora Notes:** `buffer_pool_size` auto-configured per instance class; `flush_at_commit = 1` enforced on writer.

### Query 2.2 — Buffer Pool Status

**Purpose:** Assess InnoDB buffer pool efficiency — hit ratio, dirty pages, memory utilization.

```sql
SELECT
  FORMAT((1 - (
    (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads') /
    (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests')
  )) * 100, 2) AS hit_ratio_pct,
  (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_dirty') AS dirty_pages,
  (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_total') AS total_pages,
  (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_free') AS free_pages;
```

**Thresholds:**

| Metric | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|--------|--------|------------|-------------|
| hit_ratio_pct | > 99% | 95-99% | < 95% |
| dirty_pages / total_pages | < 25% | 25-50% | > 50% |
| free_pages / total_pages | > 10% | 5-10% | < 5% |

---

## Category 3: Current Activity

### Query 3.1 — Connection Overview

**Purpose:** Real-time snapshot of connection utilization vs capacity.

```sql
SELECT
  (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Threads_connected') AS threads_connected,
  (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Threads_running') AS threads_running,
  (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Max_used_connections') AS max_used_connections,
  @@max_connections AS max_connections,
  FORMAT(
    (SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Threads_connected') / @@max_connections * 100, 1
  ) AS utilization_pct;
```

**Thresholds:**

| Metric | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|--------|--------|------------|-------------|
| utilization_pct | < 80% | 80-90% | > 90% |
| threads_running | < 20 | 20-50 | > 50 |

### Query 3.2 — Thread Details

**Purpose:** Identify active threads, states, and running queries to find bottlenecks.

```sql
SELECT 
  PROCESSLIST_ID AS id,
  PROCESSLIST_USER AS user,
  PROCESSLIST_HOST AS host,
  PROCESSLIST_DB AS db,
  PROCESSLIST_COMMAND AS command,
  PROCESSLIST_TIME AS time_seconds,
  PROCESSLIST_STATE AS state,
  LEFT(PROCESSLIST_INFO, 200) AS query_text
FROM performance_schema.threads
WHERE TYPE = 'FOREGROUND'
  AND PROCESSLIST_COMMAND != 'Sleep'
  AND PROCESSLIST_TIME > 1
ORDER BY PROCESSLIST_TIME DESC
LIMIT 20;
```

**Key States:**

| State | Meaning | Severity |
|-------|---------|----------|
| "Sending data" | Full scan likely | 🟡 WARNING |
| "Waiting for table metadata lock" | DDL blocking queries | 🔴 CRITICAL |
| "Waiting for lock" | Row-level lock contention | 🔴 CRITICAL |
| "Creating sort index" | Large sort operation | 🟡 WARNING |
| "copying to tmp table on disk" | Temp table spilled to disk | 🔴 CRITICAL |

### Query 3.3 — Active Transactions

**Purpose:** Identify long-running transactions causing lock contention or replica lag.

```sql
SELECT 
  trx_id,
  trx_state,
  trx_started,
  TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS duration_seconds,
  trx_rows_locked,
  trx_rows_modified,
  trx_isolation_level,
  LEFT(trx_query, 200) AS current_query
FROM information_schema.innodb_trx
ORDER BY trx_started ASC
LIMIT 20;
```

**Thresholds:**

| Metric | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|--------|--------|------------|-------------|
| duration_seconds | < 60 | 60-300 | > 300 |
| trx_rows_locked | < 1,000 | 1,000-10,000 | > 10,000 |

### Query 3.4 — Lock Waits

**Purpose:** Detect active lock contention — blocking vs waiting transactions.

```sql
SELECT
  r.trx_id AS waiting_trx_id,
  r.trx_mysql_thread_id AS waiting_thread,
  LEFT(r.trx_query, 150) AS waiting_query,
  b.trx_id AS blocking_trx_id,
  b.trx_mysql_thread_id AS blocking_thread,
  LEFT(b.trx_query, 150) AS blocking_query,
  TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_seconds
FROM information_schema.innodb_lock_waits w
INNER JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id
INNER JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id
ORDER BY wait_seconds DESC
LIMIT 10;
```

**Thresholds:** wait_seconds — 🟢 < 10 | 🟡 10-60 | 🔴 > 60

**Platform Note:** Aurora MySQL 3.x → use `performance_schema.data_lock_waits` (MySQL 8.0 syntax)

---

## Category 4: Replication Status

### Query 4.1 — Replication Health

**Purpose:** Check replication thread status and lag (RDS MySQL only).

```sql
SHOW REPLICA STATUS;
```

**Key Fields:**

| Field | 🟢 OK | 🔴 CRITICAL |
|-------|--------|-------------|
| Replica_IO_Running | Yes | No |
| Replica_SQL_Running | Yes | No |
| Seconds_Behind_Source | < 10 | > 60 |

**Platform Note:** For Aurora MySQL, use CloudWatch `AuroraReplicaLag` instead.

### Query 4.2 — Aurora Replica Lag Detail (Aurora-Specific)

**Purpose:** Per-replica lag analysis using Aurora's internal metadata.

```sql
SELECT 
  SERVER_ID,
  SESSION_ID,
  LAST_UPDATE_TIMESTAMP,
  REPLICA_LAG_IN_MILLISECONDS,
  CPU
FROM mysql.ro_replica_status;
```

**Thresholds:** REPLICA_LAG_IN_MILLISECONDS — 🟢 < 100 | 🟡 100-1000 | 🔴 > 1000

**Note:** This table exists ONLY on Aurora MySQL.

---

## Category 5: Storage Capacity

### Query 5.1 — Database Sizes

**Purpose:** Total storage consumed per database schema.

```sql
SELECT 
  table_schema AS database_name,
  ROUND(SUM(data_length + index_length) / (1024*1024*1024), 2) AS size_gb,
  COUNT(*) AS table_count
FROM information_schema.tables
WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
GROUP BY table_schema
ORDER BY size_gb DESC;
```

**Aurora Note:** Storage auto-scales to 128 TiB but does NOT auto-shrink. Reclaim via `OPTIMIZE TABLE`.

### Query 5.2 — Top 10 Biggest Tables

**Purpose:** Identify tables consuming the most storage.

```sql
SELECT 
  table_schema,
  table_name,
  ROUND((data_length + index_length) / (1024*1024), 2) AS total_mb,
  ROUND(data_length / (1024*1024), 2) AS data_mb,
  ROUND(index_length / (1024*1024), 2) AS index_mb,
  table_rows AS estimated_rows,
  engine
FROM information_schema.tables
WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
ORDER BY (data_length + index_length) DESC
LIMIT 10;
```

### Query 5.3 — Table Fragmentation

**Purpose:** Detect tables with significant wasted space.

```sql
SELECT 
  table_schema,
  table_name,
  ROUND(data_free / (1024*1024), 2) AS fragmented_mb,
  ROUND(data_length / (1024*1024), 2) AS data_mb,
  ROUND((data_free / (data_length + 1)) * 100, 1) AS fragmentation_pct
FROM information_schema.tables
WHERE data_free > 0
  AND table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
  AND (data_free / (data_length + 1)) > 0.1
ORDER BY data_free DESC
LIMIT 10;
```

**Thresholds:** fragmentation_pct — 🟢 < 10% | 🟡 10-20% | 🔴 > 20%

---

## Category 6: Performance Metrics

### Query 6.1 — Top 10 Queries by Total Execution Time

**Purpose:** Identify queries consuming the most cumulative execution time.

```sql
SELECT 
  DIGEST_TEXT AS query_pattern,
  COUNT_STAR AS exec_count,
  ROUND(SUM_TIMER_WAIT / 1000000000000, 2) AS total_time_sec,
  ROUND(AVG_TIMER_WAIT / 1000000000000, 4) AS avg_time_sec,
  SUM_ROWS_EXAMINED AS rows_examined,
  SUM_ROWS_SENT AS rows_sent,
  ROUND(SUM_ROWS_EXAMINED / NULLIF(SUM_ROWS_SENT, 0), 0) AS exam_to_sent_ratio
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME IS NOT NULL
ORDER BY SUM_TIMER_WAIT DESC
LIMIT 10;
```

**Interpretation:**

| Pattern | Meaning | Action |
|---------|---------|--------|
| exam_to_sent_ratio > 1000 | Full table scans | Add appropriate index |
| High exec_count + moderate avg_time | "Death by 1000 cuts" | Optimize hot path query |
| Low exec_count + very high total_time | Single expensive query | Rewrite or add index |

### Query 6.2 — CPU Intensive Queries

**Purpose:** Identify queries driving CPU via sorts, temp tables, and full scans.

```sql
SELECT 
  DIGEST_TEXT AS query_pattern,
  COUNT_STAR AS exec_count,
  SUM_SORT_ROWS AS sort_rows,
  SUM_CREATED_TMP_TABLES AS tmp_tables,
  SUM_CREATED_TMP_DISK_TABLES AS tmp_disk_tables,
  SUM_NO_INDEX_USED AS no_index_used,
  SUM_NO_GOOD_INDEX_USED AS no_good_index
FROM performance_schema.events_statements_summary_by_digest
WHERE (SUM_SORT_ROWS > 10000 OR SUM_CREATED_TMP_DISK_TABLES > 0 OR SUM_NO_INDEX_USED > 0)
ORDER BY (SUM_SORT_ROWS + SUM_CREATED_TMP_DISK_TABLES * 10000) DESC
LIMIT 10;
```

**Thresholds:** tmp_disk_tables — 🟢 0 | 🟡 > 0 | 🔴 > 100

### Query 6.3 — I/O Intensive Queries

**Purpose:** Identify queries generating excessive I/O through large row scans.

```sql
SELECT 
  DIGEST_TEXT AS query_pattern,
  COUNT_STAR AS exec_count,
  SUM_ROWS_EXAMINED AS total_rows_examined,
  SUM_ROWS_SENT AS total_rows_sent,
  ROUND(SUM_ROWS_EXAMINED / NULLIF(COUNT_STAR, 0), 0) AS avg_rows_per_exec,
  ROUND(SUM_TIMER_WAIT / 1000000000000, 2) AS total_time_sec
FROM performance_schema.events_statements_summary_by_digest
WHERE SUM_ROWS_EXAMINED > 100000
ORDER BY SUM_ROWS_EXAMINED DESC
LIMIT 10;
```

**Thresholds:** avg_rows_per_exec — 🟢 < 10K | 🟡 10K-100K | 🔴 > 100K

### Query 6.4 — Index Usage Statistics

**Purpose:** Identify tables where reads are predominantly full scans.

```sql
SELECT 
  OBJECT_SCHEMA AS schema_name,
  OBJECT_NAME AS table_name,
  COUNT_READ AS total_reads,
  COUNT_FETCH AS index_reads,
  COUNT_READ - COUNT_FETCH AS full_scans,
  ROUND((COUNT_READ - COUNT_FETCH) / NULLIF(COUNT_READ, 0) * 100, 1) AS full_scan_pct
FROM performance_schema.table_io_waits_summary_by_table
WHERE OBJECT_SCHEMA NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
  AND COUNT_READ > 1000
ORDER BY (COUNT_READ - COUNT_FETCH) DESC
LIMIT 10;
```

**Thresholds:** full_scan_pct — 🟢 < 10% | 🟡 10-30% | 🔴 > 30%

---

## Category 7: Maintenance Health

### Query 7.1 — Auto-Increment Capacity

**Purpose:** Detect tables approaching auto-increment integer overflow.

```sql
SELECT 
  table_schema,
  table_name,
  update_time AS last_updated,
  table_rows AS estimated_rows,
  auto_increment,
  CASE 
    WHEN auto_increment IS NOT NULL THEN 
      ROUND((auto_increment / 2147483647) * 100, 2)
    ELSE NULL
  END AS auto_inc_usage_pct
FROM information_schema.tables
WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
  AND auto_increment IS NOT NULL
  AND (auto_increment / 2147483647) > 0.5
ORDER BY auto_inc_usage_pct DESC
LIMIT 10;
```

**Thresholds:** auto_inc_usage_pct — 🟢 < 50% | 🟡 50-75% | 🔴 > 75%

**Impact:** INT SIGNED max = 2,147,483,647 → INSERT fails on overflow. Fix: ALTER to BIGINT.

---

## Category 8: Optimization Opportunities

### Query 8.1 — Redundant Indexes

**Purpose:** Detect duplicate/overlapping indexes wasting storage and write performance.

```sql
SELECT 
  table_schema,
  table_name,
  redundant_index_name,
  redundant_index_columns,
  dominant_index_name,
  dominant_index_columns,
  subpart_exists,
  sql_drop_index
FROM sys.schema_redundant_indexes
WHERE table_schema NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
LIMIT 20;
```

### Query 8.2 — Unused Indexes

**Purpose:** Identify indexes never used since last server restart.

```sql
SELECT 
  object_schema AS schema_name,
  object_name AS table_name,
  index_name,
  ROUND(stat_value * @@innodb_page_size / (1024*1024), 2) AS index_size_mb
FROM mysql.innodb_index_stats s
JOIN performance_schema.table_io_waits_summary_by_index_usage t
  ON s.table_name = t.OBJECT_NAME
  AND s.index_name = t.INDEX_NAME
  AND s.database_name = t.OBJECT_SCHEMA
WHERE t.COUNT_STAR = 0
  AND t.INDEX_NAME IS NOT NULL
  AND t.INDEX_NAME != 'PRIMARY'
  AND s.stat_name = 'size'
  AND t.OBJECT_SCHEMA NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
ORDER BY stat_value DESC
LIMIT 20;
```

**Reliability:** Uptime must be > 7 days. Always check batch job schedules before dropping.

---

## Category 9: Summary & Health Score

### Query 9.1 — Composite Health Score (50 points max)

**Purpose:** Single-query assessment across 8 database dimensions.

```sql
SELECT
  -- Dimension 1: Connection Health (0-5)
  CASE WHEN (SELECT VARIABLE_VALUE FROM performance_schema.global_status 
    WHERE VARIABLE_NAME = 'Threads_connected') / @@max_connections < 0.8 
    THEN 5 ELSE 0 END AS connection_score,
  -- Dimension 2: Buffer Pool Efficiency (0-5)
  CASE WHEN (1 - ((SELECT VARIABLE_VALUE FROM performance_schema.global_status 
    WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads') / 
    NULLIF((SELECT VARIABLE_VALUE FROM performance_schema.global_status 
    WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests'), 0))) > 0.99 
    THEN 5 ELSE 0 END AS buffer_pool_score,
  -- Dimension 3: Replication (0-5)
  5 AS replication_score,  -- Default pass; override from SHOW REPLICA STATUS
  -- Dimension 4: Lock Health (0-5)
  CASE WHEN (SELECT COUNT(*) FROM information_schema.innodb_trx 
    WHERE TIMESTAMPDIFF(SECOND, trx_started, NOW()) > 300) = 0 
    THEN 5 ELSE 0 END AS lock_score,
  -- Dimension 5: Slow Query Control (0-5)
  CASE WHEN @@slow_query_log = 1 THEN 5 ELSE 0 END AS monitoring_score,
  -- Dimension 6: Fragmentation (0-5)
  CASE WHEN (SELECT COUNT(*) FROM information_schema.tables 
    WHERE data_free > 0 AND (data_free/(data_length+1)) > 0.2 
    AND table_schema NOT IN ('mysql','information_schema','performance_schema','sys')) < 5 
    THEN 5 ELSE 0 END AS storage_score,
  -- Dimension 7: Index Efficiency (0-5)
  CASE WHEN (SELECT COUNT(*) FROM performance_schema.table_io_waits_summary_by_index_usage 
    WHERE COUNT_STAR = 0 AND INDEX_NAME IS NOT NULL AND INDEX_NAME != 'PRIMARY' 
    AND OBJECT_SCHEMA NOT IN ('mysql','performance_schema','information_schema','sys')) < 10 
    THEN 5 ELSE 0 END AS index_score,
  -- Dimension 8: Performance Schema (0-5)
  CASE WHEN @@performance_schema = 1 THEN 5 ELSE 0 END AS instrumentation_score;
```

**Grading:** 45-50 = A | 40-44 = B | 35-39 = C | 25-34 = D | < 25 = F

---

## Version Compatibility

| Query | Aurora MySQL 2.x (5.7) | Aurora MySQL 3.x (8.0) | RDS MySQL 5.7 | RDS MySQL 8.0 |
|-------|------------------------|------------------------|---------------|---------------|
| 1.1-2.2 | ✅ | ✅ | ✅ | ✅ |
| 3.1-3.3 | ✅ | ✅ | ✅ | ✅ |
| 3.4 | ✅ | ✅ (use data_lock_waits) | ✅ | ✅ (use data_lock_waits) |
| 4.1 | ⚠️ (use CloudWatch) | ⚠️ (use CloudWatch) | ✅ | ✅ |
| 4.2 | ✅ | ✅ | N/A | N/A |
| 5.1-9.1 | ✅ | ✅ | ✅ | ✅ |
