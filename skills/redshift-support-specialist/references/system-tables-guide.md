# Amazon Redshift System Tables & Views Guide

Comprehensive reference for STL, SVL, SVV, STV, SVCS, and SYS system tables and views used for diagnostics, monitoring, and troubleshooting Amazon Redshift provisioned clusters and Serverless workgroups.

Sources: [Amazon Redshift system tables reference](https://docs.aws.amazon.com/redshift/latest/dg/cm_chap_system-tables.html), [SYS monitoring views](https://docs.aws.amazon.com/redshift/latest/dg/sys-monitoring-views.html), [AWS re:Post Redshift monitoring articles](https://repost.aws/). Content was rephrased for compliance with licensing restrictions.

---

## System Table Prefixes Overview

| Prefix | Full Name | Data Source | Availability | Retention | Description |
|--------|-----------|-------------|--------------|-----------|-------------|
| STL | System Table Log | Persisted log files on disk | Provisioned only | 2-5 days (varies by table) | Historical log data — query execution, errors, loads, alerts |
| STV | System Table View (snapshot) | In-memory transient data | Provisioned only | Current snapshot only | Real-time cluster state — running queries, locks, slices, sessions |
| SVL | System View Log | Joins across STL tables | Provisioned only | Same as underlying STL tables | Convenience views combining multiple log tables |
| SVV | System View Visibility | Joins across STV + catalog | Provisioned + Serverless (some) | Current snapshot + catalog | Database object metadata, table info, user info |
| SVCS | System View Concurrency Scaling | Log data from main + CS clusters | Provisioned only | 2-5 days | Like SVL but includes Concurrency Scaling cluster data |
| SYS | System Monitoring Views | Unified monitoring layer | Provisioned + Serverless | 7 days (default) | Preferred for Serverless; unified query/workload monitoring |

---

## STL Tables — Query Execution & Logging (Provisioned Only)

### Query Execution

| Table | Purpose | Key Columns | Common Use |
|-------|---------|-------------|------------|
| `STL_QUERY` | Query execution history | `query`, `userid`, `querytxt`, `starttime`, `endtime`, `elapsed`, `aborted` | Find slow queries, query duration analysis |
| `STL_QUERYTEXT` | Full SQL text (for queries > 200 chars) | `query`, `sequence`, `text` | Retrieve complete SQL for long queries (join on `query`, order by `sequence`) |
| `STL_EXPLAIN` | Query plan for executed queries | `query`, `nodeid`, `parentid`, `plannode`, `info` | Analyze execution plans post-hoc |
| `STL_PLAN_INFO` | Query plan step details | `query`, `nodeid`, `segment`, `step`, `rows`, `bytes` | Detailed plan step analysis |
| `STL_STREAM_SEGS` | Stream-to-segment mapping | `query`, `stream`, `segment` | Understand query parallelism |

**Diagnostic Query — Top Slow Queries (Last 24h):**

```sql
SELECT query, userid, TRIM(database) AS db,
       DATEDIFF(seconds, starttime, endtime) AS duration_sec,
       SUBSTRING(TRIM(querytxt), 1, 200) AS query_text,
       aborted
FROM stl_query
WHERE starttime >= DATEADD(hour, -24, GETDATE())
  AND userid > 1  -- Exclude system queries
ORDER BY duration_sec DESC
LIMIT 50;
```

### Query Alerts & Diagnostics

| Table | Purpose | Key Columns | Common Use |
|-------|---------|-------------|------------|
| `STL_ALERT_EVENT_LOG` | Alerts raised during query execution | `query`, `event`, `solution`, `event_time` | Identify nested loops, skew, ghost rows, very selective filters |
| `STL_ERROR` | Query errors | `query`, `userid`, `process`, `errcode`, `error` | Debug query failures |
| `STL_DISK_FULL_DIAG` | Disk-full events | `query`, `node`, `temp_blocks`, `perm_blocks` | Diagnose disk space exhaustion |

**Alert Event Types (STL_ALERT_EVENT_LOG):**

| Event | Meaning | Typical Solution |
|-------|---------|-----------------|
| Nested Loop | Cartesian product in join | Add proper join conditions; check DISTKEY alignment |
| Very selective filter | Filter returns very few rows from large scan | Add or adjust sort key on filter column |
| Excessive ghost rows | Table has many deleted but not vacuumed rows | Run `VACUUM DELETE` on the table |
| Large distribution | Large data redistribution during query | Co-locate tables with matching DISTKEY |
| Large broadcast | Large table broadcast to all nodes | Use KEY distribution on join column or ALL for small table |
| Serial execution | Query step running on single slice | Check for skewed distribution |
| Missing statistics | Table statistics are stale | Run `ANALYZE` on the table |

**Diagnostic Query — Recent Alerts:**

```sql
SELECT query, TRIM(event) AS event,
       TRIM(solution) AS solution,
       event_time
FROM stl_alert_event_log
WHERE event_time >= DATEADD(hour, -24, GETDATE())
ORDER BY event_time DESC
LIMIT 100;
```

### WLM & Queue Management

| Table | Purpose | Key Columns | Common Use |
|-------|---------|-------------|------------|
| `STL_WLM_QUERY` | WLM queue assignment per query | `query`, `service_class`, `total_queue_time`, `total_exec_time`, `queue_start_time`, `exec_start_time` | Analyze queue wait times, identify contention |
| `STL_WLM_RULE_ACTION` | QMR rule actions taken | `query`, `rule`, `action`, `service_class` | Track queries aborted or logged by QMR rules |
| `STL_COMMIT_STATS` | Commit queue statistics | `node`, `startqueue`, `startwork`, `endtime`, `queuelen` | Diagnose commit queue bottlenecks |

**Diagnostic Query — WLM Wait Times by Queue (Last 24h):**

```sql
SELECT service_class,
       COUNT(*) AS query_count,
       AVG(total_queue_time / 1000000.0) AS avg_queue_sec,
       MAX(total_queue_time / 1000000.0) AS max_queue_sec,
       AVG(total_exec_time / 1000000.0) AS avg_exec_sec,
       MAX(total_exec_time / 1000000.0) AS max_exec_sec
FROM stl_wlm_query
WHERE service_class > 4  -- User-defined queues only (5+)
  AND queue_start_time >= DATEADD(hour, -24, GETDATE())
GROUP BY service_class
ORDER BY avg_queue_sec DESC;
```

### Data Loading

| Table | Purpose | Key Columns | Common Use |
|-------|---------|-------------|------------|
| `STL_LOAD_ERRORS` | COPY load errors | `query`, `userid`, `tbl`, `starttime`, `filename`, `colname`, `type`, `col_length`, `raw_field_value`, `err_reason` | Diagnose failed COPY operations |
| `STL_LOADERROR_DETAIL` | Detailed error info per failed row | `query`, `filename`, `line_number`, `value`, `err_reason` | Pinpoint exact rows/values causing load failures |
| `STL_LOAD_COMMITS` | Successful COPY commit history | `query`, `table`, `lines_scanned`, `lines_loaded`, `starttime`, `endtime` | Track load volumes and timing |
| `STL_S3CLIENT` | S3 transfer details for COPY/UNLOAD | `query`, `http_method`, `transfer_size`, `transfer_time`, `start_time`, `end_time` | Measure COPY throughput (MB/s) |
| `STL_FILE_SCAN` | File scan details during COPY | `query`, `name`, `lines`, `bytes`, `loadtime` | Identify slow files in a COPY operation |

**Diagnostic Query — COPY Errors:**

```sql
SELECT le.query, le.starttime,
       TRIM(le.filename) AS filename,
       le.line_number, le.colname,
       TRIM(le.err_reason) AS err_reason,
       TRIM(d.value) AS raw_value
FROM stl_load_errors le
LEFT JOIN stl_loaderror_detail d
  ON le.query = d.query AND le.line_number = d.line_number
WHERE le.starttime >= DATEADD(day, -7, GETDATE())
ORDER BY le.starttime DESC
LIMIT 50;
```

**Diagnostic Query — COPY Throughput (Last 7 Days):**

```sql
SELECT q.starttime, s.query,
       SUBSTRING(q.querytxt, 1, 120) AS querytxt,
       s.n_files, s.size_mb, s.time_seconds,
       ROUND(s.size_mb / DECODE(s.time_seconds, 0, 1, s.time_seconds), 2) AS mb_per_s
FROM (
  SELECT query, COUNT(*) AS n_files,
         SUM(transfer_size / (1024.0 * 1024.0)) AS size_mb,
         (MAX(end_time) - MIN(start_time)) / 1000000.0 AS time_seconds,
         MAX(end_time) AS end_time
  FROM stl_s3client
  WHERE http_method = 'GET' AND query > 0 AND transfer_time > 0
  GROUP BY query
) AS s
LEFT JOIN stl_query AS q ON q.query = s.query
WHERE s.end_time >= DATEADD(day, -7, CURRENT_DATE)
ORDER BY s.time_seconds DESC
LIMIT 50;
```

### Connection & User Activity

| Table | Purpose | Key Columns | Common Use |
|-------|---------|-------------|------------|
| `STL_CONNECTION_LOG` | Connection attempts (success/failure) | `event`, `recordtime`, `username`, `dbname`, `remotehost`, `remoteport`, `pid`, `authmethod` | Audit connection patterns; detect failed auth attempts |
| `STL_USERLOG` | DDL changes to users | `userid`, `username`, `oldusername`, `action`, `usecreatedb`, `usesuper`, `recordtime` | Track user creation, deletion, privilege changes |
| `STL_DDLTEXT` | DDL statement text | `userid`, `query`, `text`, `starttime` | Audit schema changes (CREATE, ALTER, DROP) |
| `STL_UTILITYTEXT` | Utility command text (VACUUM, ANALYZE, etc.) | `userid`, `query`, `text`, `starttime` | Track maintenance operations |

---

## STV Tables — Real-Time Cluster State (Provisioned Only)

| Table | Purpose | Key Columns | Common Use |
|-------|---------|-------------|------------|
| `STV_INFLIGHT` | Currently executing queries | `userid`, `query`, `text`, `starttime`, `suspended` | Monitor active queries; find long-running queries |
| `STV_RECENTS` | Recently executed and current queries | `userid`, `query`, `pid`, `starttime`, `duration`, `status` | Quick view of recent activity |
| `STV_BLOCKLIST` | Disk block allocation per table | `tbl`, `col`, `slice`, `num`, `minvalue`, `maxvalue` | Analyze storage distribution and zone maps |
| `STV_TBL_PERM` | Table row counts and disk blocks | `id`, `name`, `rows`, `sorted_rows`, `temp`, `db_id`, `slice` | Quick row counts; check sorted vs unsorted rows |
| `STV_SLICES` | Slice-to-node mapping | `node`, `slice` | Understand cluster topology |
| `STV_SESSIONS` | Active user sessions | `userid`, `process`, `user_name`, `db_name`, `starttime` | Monitor active connections |
| `STV_LOCKS` | Current table locks | `table_id`, `lock_owner`, `lock_owner_pid`, `lock_mode` | Diagnose lock contention |
| `STV_WLM_SERVICE_CLASS_STATE` | Current WLM queue state | `service_class`, `num_executing_queries`, `num_queued_queries`, `num_slots`, `evictable_mem` | Real-time WLM queue monitoring |
| `STV_WLM_SERVICE_CLASS_CONFIG` | WLM queue configuration | `service_class`, `num_query_tasks`, `query_working_mem`, `max_execution_time`, `priority` | Review WLM configuration |
| `STV_WLM_QUERY_STATE` | Current state of WLM-managed queries | `query`, `service_class`, `wlm_start_time`, `state`, `queue_time`, `exec_time` | Monitor individual query WLM state |

**Diagnostic Query — Currently Running Queries:**

```sql
SELECT userid, query, TRIM(text) AS query_text,
       DATEDIFF(seconds, starttime, GETDATE()) AS running_sec,
       suspended
FROM stv_inflight
ORDER BY running_sec DESC;
```

**Diagnostic Query — Active Locks:**

```sql
SELECT l.table_id, t.name AS table_name,
       l.lock_owner, l.lock_owner_pid, l.lock_mode
FROM stv_locks l
JOIN stv_tbl_perm t ON l.table_id = t.id AND t.slice = 0
ORDER BY l.lock_owner;
```

**Diagnostic Query — Current WLM Queue State:**

```sql
SELECT service_class, num_executing_queries,
       num_queued_queries, num_slots,
       evictable_mem / (1024 * 1024) AS evictable_mem_mb
FROM stv_wlm_service_class_state
WHERE service_class > 4  -- User-defined queues
ORDER BY service_class;
```

---

## SVL Views — Combined Log Views (Provisioned Only)

| View | Purpose | Key Columns | Common Use |
|------|---------|-------------|------------|
| `SVL_QUERY_SUMMARY` | Aggregated query step metrics | `query`, `stm`, `seg`, `step`, `label`, `rows`, `bytes`, `elapsed_time`, `is_diskbased` | Identify disk-based steps (memory spill); find slow steps |
| `SVL_QUERY_REPORT` | Per-node query step execution | `query`, `segment`, `step`, `label`, `rows`, `bytes`, `elapsed_time`, `slice` | Detect per-node skew in query execution |
| `SVL_QLOG` | Simplified query log | `userid`, `query`, `elapsed`, `substring`, `source_query` | Quick query history with duration |
| `SVL_STATEMENTTEXT` | Complete statement text (includes CS) | `userid`, `query`, `text`, `starttime`, `type` | Full SQL audit trail including Concurrency Scaling queries |
| `SVL_QUERY_METRICS` | Query-level resource metrics | `query`, `segment`, `step_type`, `query_cpu_time`, `query_blocks_read`, `query_temp_blocks_to_disk` | Resource consumption per query |
| `SVL_QUERY_METRICS_SUMMARY` | Aggregated query resource metrics | `query`, `query_cpu_time`, `query_blocks_read`, `query_temp_blocks_to_disk`, `spectrum_scan_size_mb` | Summary resource usage per query |
| `SVL_S3QUERY` | Spectrum query details | `query`, `segment`, `external_table_name`, `s3_scanned_rows`, `s3_scanned_bytes`, `s3query_returned_rows` | Monitor Spectrum query performance |
| `SVL_S3QUERY_SUMMARY` | Spectrum query summary | `query`, `elapsed`, `s3_scanned_rows`, `s3_scanned_bytes`, `s3query_returned_rows`, `files` | Spectrum scan efficiency |
| `SVL_COMPILE` | Query compilation details | `query`, `segment`, `compile`, `compile_time` | Identify compilation overhead |
| `SVL_VACUUM_PERCENTAGE` | VACUUM progress | `table_name`, `status`, `time_remaining_estimate` | Monitor VACUUM operations |

**Diagnostic Query — Disk-Based Query Steps (Memory Spill):**

```sql
SELECT query, stm, seg, step, TRIM(label) AS label,
       rows, bytes, elapsed_time,
       is_diskbased
FROM svl_query_summary
WHERE is_diskbased = 't'
  AND query IN (
    SELECT query FROM stl_query
    WHERE starttime >= DATEADD(hour, -24, GETDATE())
  )
ORDER BY elapsed_time DESC
LIMIT 50;
```

---

## SVV Views — Object Metadata & Visibility

| View | Purpose | Key Columns | Availability | Common Use |
|------|---------|-------------|--------------|------------|
| `SVV_TABLE_INFO` | Table metadata (size, skew, sort, encoding) | `database`, `schema`, `table`, `size`, `pct_used`, `unsorted`, `stats_off`, `skew_rows`, `skew_sortkey1`, `diststyle`, `sortkey1`, `encoded` | Provisioned + Serverless | Primary table health assessment view |
| `SVV_DISKUSAGE` | Per-table disk usage by slice | `db_id`, `name`, `slice`, `col`, `num`, `blocknum`, `minvalue`, `maxvalue` | Provisioned | Analyze storage distribution across slices |
| `SVV_COLUMNS` | Column metadata for all tables | `table_schema`, `table_name`, `column_name`, `data_type`, `encoding`, `distkey`, `sortkey` | Provisioned + Serverless | Review column definitions, encodings, keys |
| `SVV_TABLES` | Table catalog | `table_schema`, `table_name`, `table_type` | Provisioned + Serverless | List all tables |
| `SVV_EXTERNAL_TABLES` | Spectrum external tables | `schemaname`, `tablename`, `location`, `input_format` | Provisioned + Serverless | Review Spectrum table definitions |
| `SVV_EXTERNAL_PARTITIONS` | Spectrum partition metadata | `schemaname`, `tablename`, `values`, `location` | Provisioned + Serverless | Review Spectrum partitions |
| `SVV_QUERY_STATE` | Currently executing query steps | `query`, `seg`, `step`, `label`, `rows`, `bytes`, `is_diskbased` | Provisioned | Real-time query progress monitoring |
| `SVV_TRANSACTIONS` | Active transactions | `txn_owner`, `txn_db`, `txn_start`, `lock_mode` | Provisioned + Serverless | Monitor open transactions; detect long-running transactions |
| `SVV_DATASHARES` | Data share metadata | `share_name`, `share_type`, `producer_account`, `producer_namespace` | Provisioned + Serverless | Review data sharing configuration |
| `SVV_RLS_POLICY` | Row-level security policies | `polname`, `polrelname`, `polcmd`, `polqual` | Provisioned + Serverless | Audit RLS policies |

**Diagnostic Query — Complete Table Health Assessment:**

```sql
SELECT database, schema, "table",
       diststyle, sortkey1, encoded,
       size AS size_mb,
       pct_used,
       unsorted,
       stats_off,
       skew_rows,
       skew_sortkey1,
       tbl_rows
FROM svv_table_info
WHERE schema NOT IN ('pg_catalog', 'information_schema', 'pg_internal')
ORDER BY size DESC;
```

---

## SVCS Views — Concurrency Scaling Inclusive (Provisioned Only)

SVCS views are equivalent to SVL views but include data from both the main cluster and Concurrency Scaling clusters.

| View | Purpose | Equivalent SVL View |
|------|---------|---------------------|
| `SVCS_QUERY_SUMMARY` | Query step metrics (main + CS) | `SVL_QUERY_SUMMARY` |
| `SVCS_EXPLAIN` | Query plans (main + CS) | `STL_EXPLAIN` |
| `SVCS_COMPILE` | Compilation details (main + CS) | `SVL_COMPILE` |
| `SVCS_PLAN_INFO` | Plan step info (main + CS) | `STL_PLAN_INFO` |
| `SVCS_STREAM_SEGS` | Stream-segment mapping (main + CS) | `STL_STREAM_SEGS` |
| `SVCS_S3QUERY` | Spectrum queries (main + CS) | `SVL_S3QUERY` |
| `SVCS_S3QUERY_SUMMARY` | Spectrum summary (main + CS) | `SVL_S3QUERY_SUMMARY` |

Use SVCS views instead of SVL views when Concurrency Scaling is enabled to get a complete picture of all query execution.

---

## SYS Views — Unified Monitoring (Provisioned + Serverless)

SYS views are the preferred monitoring interface for Serverless workgroups and are also available on provisioned clusters. They provide a unified, consistent monitoring experience across deployment types.

### Query Monitoring

| View | Purpose | Key Columns | Common Use |
|------|---------|-------------|------------|
| `SYS_QUERY_HISTORY` | Query history with metrics | `query_id`, `user_id`, `database_name`, `query_text`, `status`, `start_time`, `end_time`, `elapsed_time`, `queue_time`, `execution_time`, `result_cache_hit`, `query_type` | Primary query monitoring view for both provisioned and Serverless |
| `SYS_QUERY_DETAIL` | Detailed execution plan per query | `query_id`, `child_query_sequence`, `stream_id`, `segment_id`, `step_id`, `table_id`, `rows_pre_filter`, `rows_post_filter`, `is_diskbased`, `duration`, `input_bytes`, `output_bytes`, `is_active` | Deep-dive into query execution steps; identify slow segments |
| `SYS_QUERY_TEXT` | Full query text | `query_id`, `sequence`, `text` | Retrieve complete SQL for long queries |
| `SYS_EXTERNAL_QUERY_DETAIL` | Spectrum query execution | `query_id`, `segment_id`, `source_type`, `external_scanned_bytes`, `external_returned_rows`, `external_returned_bytes`, `file_format` | Monitor Spectrum query performance |

**Diagnostic Query — Slow Queries (SYS — Works on Serverless):**

```sql
SELECT query_id, user_id, TRIM(database_name) AS db,
       status, elapsed_time / 1000000.0 AS elapsed_sec,
       queue_time / 1000000.0 AS queue_sec,
       execution_time / 1000000.0 AS exec_sec,
       result_cache_hit,
       SUBSTRING(TRIM(query_text), 1, 200) AS query_text
FROM sys_query_history
WHERE start_time >= DATEADD(hour, -24, GETDATE())
  AND user_id > 1
ORDER BY elapsed_time DESC
LIMIT 50;
```

**Diagnostic Query — Disk-Based Steps (SYS):**

```sql
SELECT qd.query_id, qd.stream_id, qd.segment_id, qd.step_id,
       qd.table_id, qd.rows_pre_filter, qd.rows_post_filter,
       qd.spilled_block_local_disk, qd.spilled_block_remote_disk,
       qd.duration / 1000000.0 AS duration_sec,
       qd.input_bytes / (1024.0 * 1024.0) AS input_mb
FROM sys_query_detail qd
WHERE (qd.spilled_block_local_disk > 0 OR qd.spilled_block_remote_disk > 0)
  AND qd.query_id IN (
    SELECT query_id FROM sys_query_history
    WHERE start_time >= DATEADD(hour, -24, GETDATE())
  )
ORDER BY qd.duration DESC
LIMIT 50;
```

### Data Loading

| View | Purpose | Key Columns | Common Use |
|------|---------|-------------|------------|
| `SYS_LOAD_HISTORY` | COPY load history | `query_id`, `table_name`, `data_source`, `loaded_rows`, `loaded_bytes`, `start_time`, `end_time`, `status` | Track load operations and throughput |
| `SYS_LOAD_ERROR_DETAIL` | Load error details | `query_id`, `table_name`, `line_number`, `column_name`, `column_type`, `error_message`, `raw_value` | Diagnose COPY failures on Serverless |

**Diagnostic Query — Recent Load History:**

```sql
SELECT query_id, TRIM(table_name) AS table_name,
       TRIM(data_source) AS source,
       loaded_rows, loaded_bytes / (1024.0 * 1024.0) AS loaded_mb,
       DATEDIFF(seconds, start_time, end_time) AS duration_sec,
       status
FROM sys_load_history
WHERE start_time >= DATEADD(day, -7, GETDATE())
ORDER BY start_time DESC
LIMIT 50;
```

### Connection Monitoring

| View | Purpose | Key Columns | Common Use |
|------|---------|-------------|------------|
| `SYS_CONNECTION_LOG` | Connection events | `event`, `record_time`, `user_name`, `database_name`, `remote_host`, `remote_port`, `auth_method`, `duration` | Audit connections; detect failed auth |

### Serverless-Specific

| View | Purpose | Key Columns | Common Use |
|------|---------|-------------|------------|
| `SYS_SERVERLESS_USAGE` | RPU consumption and billing | `start_time`, `end_time`, `compute_seconds`, `compute_type`, `data_scanned`, `charged_seconds` | Monitor Serverless costs; track RPU usage patterns |

**Diagnostic Query — Serverless RPU Usage (Last 7 Days):**

```sql
SELECT DATE_TRUNC('hour', start_time) AS hour,
       SUM(compute_seconds) AS total_compute_sec,
       SUM(charged_seconds) AS total_charged_sec,
       SUM(data_scanned) / (1024.0 * 1024.0 * 1024.0) AS data_scanned_gb
FROM sys_serverless_usage
WHERE start_time >= DATEADD(day, -7, GETDATE())
GROUP BY DATE_TRUNC('hour', start_time)
ORDER BY hour DESC;
```

**Diagnostic Query — Serverless Daily Cost Estimate:**

```sql
SELECT DATE_TRUNC('day', start_time) AS day,
       SUM(charged_seconds) / 3600.0 AS charged_rpu_hours,
       -- Approximate cost at $0.375/RPU-hour (us-east-1, check current pricing)
       ROUND(SUM(charged_seconds) / 3600.0 * 0.375, 2) AS estimated_cost_usd
FROM sys_serverless_usage
WHERE start_time >= DATEADD(day, -30, GETDATE())
GROUP BY DATE_TRUNC('day', start_time)
ORDER BY day DESC;
```

---

## Quick Reference: Which View to Use

| Task | Provisioned | Serverless | Recommended |
|------|-------------|------------|-------------|
| Query history | `STL_QUERY` / `SVL_QLOG` | `SYS_QUERY_HISTORY` | `SYS_QUERY_HISTORY` (unified) |
| Query plan analysis | `STL_EXPLAIN` | `SYS_QUERY_DETAIL` | `SYS_QUERY_DETAIL` (unified) |
| Full SQL text | `STL_QUERYTEXT` | `SYS_QUERY_TEXT` | `SYS_QUERY_TEXT` (unified) |
| Query alerts | `STL_ALERT_EVENT_LOG` | N/A (use SYS_QUERY_DETAIL) | `STL_ALERT_EVENT_LOG` (provisioned) |
| WLM queue analysis | `STL_WLM_QUERY` / `STV_WLM_SERVICE_CLASS_STATE` | `SYS_QUERY_HISTORY` (queue_time) | Depends on deployment |
| COPY errors | `STL_LOAD_ERRORS` | `SYS_LOAD_ERROR_DETAIL` | Match to deployment type |
| COPY throughput | `STL_S3CLIENT` | `SYS_LOAD_HISTORY` | Match to deployment type |
| Table health | `SVV_TABLE_INFO` | `SVV_TABLE_INFO` | `SVV_TABLE_INFO` (both) |
| Active queries | `STV_INFLIGHT` | `SYS_QUERY_HISTORY` (status='running') | Match to deployment type |
| Active locks | `STV_LOCKS` | N/A | `STV_LOCKS` (provisioned only) |
| Connections | `STL_CONNECTION_LOG` / `STV_SESSIONS` | `SYS_CONNECTION_LOG` | Match to deployment type |
| Spectrum queries | `SVL_S3QUERY_SUMMARY` | `SYS_EXTERNAL_QUERY_DETAIL` | Match to deployment type |
| RPU / billing | N/A | `SYS_SERVERLESS_USAGE` | `SYS_SERVERLESS_USAGE` (Serverless only) |
| Disk usage | `SVV_DISKUSAGE` / `STV_BLOCKLIST` | N/A | Provisioned only |
| VACUUM progress | `SVL_VACUUM_PERCENTAGE` | N/A | Provisioned only |
| Concurrency Scaling | `SVCS_*` views | N/A | `SVCS_*` (provisioned with CS) |

---

## Data Retention & Limitations

| View Type | Default Retention | Notes |
|-----------|-------------------|-------|
| STL tables | 2-5 days | Varies by table; logs rotate automatically; persist to S3 for long-term retention |
| STV tables | Current snapshot only | Transient; data lost on restart |
| SVL views | Same as underlying STL | Derived from STL tables |
| SVV views | Current + catalog | Catalog data is persistent; snapshot data is transient |
| SYS views | 7 days (default) | Configurable; longer retention available |
| SVCS views | Same as underlying STL | Includes Concurrency Scaling data |

**Important Notes:**

- System tables are node-local on provisioned clusters — queries aggregate across nodes automatically
- STL/STV/SVL/SVCS views are NOT available on Serverless — use SYS views instead
- SVV views (especially `SVV_TABLE_INFO`) work on both provisioned and Serverless
- For long-term audit retention, enable audit logging to S3 or CloudWatch Logs
- System table queries run on the leader node and do not consume compute slices
