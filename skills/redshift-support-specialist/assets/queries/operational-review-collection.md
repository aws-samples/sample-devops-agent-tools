# Operational Review — Live Data Collection Queries

These read-only queries collect the data the Detailed Operational Review needs
directly from the cluster or workgroup via the connected `execute_query` tool —
no CSV upload required. Run each block, then evaluate the results against
`assets/config/thresholds.yaml` and map findings using
`references/operational-review-signals.md`.

Notes:
- All queries are read-only SELECTs and run inside the read-only transaction.
- SVV_* and SYS_* views work on both provisioned and Serverless. STL/STV views
  are provisioned-only; for Serverless, skip the STV-based storage query and use
  the SYS_SERVERLESS_USAGE query instead.
- If a view or column is not available on the target's Redshift version/type,
  report that section as "not available" and continue with the others. Do not
  guess values.
- Default lookback is 7 days; adjust the DATEADD interval as needed.

---

## Section 1 — Storage Utilization

Provisioned (STV_PARTITIONS):

```sql
SELECT SUM(capacity) / 1024.0 AS capacity_gb,
       SUM(used) / 1024.0 AS used_gb,
       ROUND(100.0 * SUM(used) / NULLIF(SUM(capacity), 0), 1) AS storage_utilization_pct
FROM stv_partitions
WHERE part_begin = 0;
```

Serverless (SYS_SERVERLESS_USAGE, last 24h):

```sql
SELECT MAX(storage_capacity_used) / 1024.0 AS storage_used_gb,
       AVG(compute_capacity) AS avg_rpu,
       MAX(compute_capacity) AS max_rpu
FROM sys_serverless_usage
WHERE start_time >= DATEADD(day, -1, GETDATE());
```

Signal: storage_utilization_pct > 70 → WARN (recommendation #5/#6/#4/#2).

---

## Section 2 — Usage Pattern (hourly workload, last 7 days)

```sql
SELECT DATE_TRUNC('hour', start_time) AS hour,
       COUNT(*) AS query_count,
       SUM(CASE WHEN query_type = 'COPY' THEN 1 ELSE 0 END) AS copy_count,
       SUM(CASE WHEN query_type = 'INSERT' THEN 1 ELSE 0 END) AS insert_count,
       SUM(CASE WHEN query_type = 'DDL' THEN 1 ELSE 0 END) AS ddl_count,
       SUM(CASE WHEN query_type = 'CTAS' THEN 1 ELSE 0 END) AS ctas_count,
       SUM(CASE WHEN result_cache_hit THEN 1 ELSE 0 END) AS result_cache_hits,
       SUM(CASE WHEN queue_time > 0 THEN 1 ELSE 0 END) AS queued_queries,
       SUM(CASE WHEN compile_time > 0 THEN 1 ELSE 0 END) AS compiled_queries,
       ROUND(100.0 * SUM(queue_time) / NULLIF(SUM(elapsed_time), 0), 2) AS pct_wlm_queue_time
FROM sys_query_history
WHERE start_time >= DATEADD(day, -7, GETDATE())
  AND user_id > 1
GROUP BY 1
ORDER BY 1 DESC;
```

Small single-row inserts (last 7 days):

```sql
SELECT COUNT(*) AS small_insert_count
FROM sys_query_history
WHERE start_time >= DATEADD(day, -7, GETDATE())
  AND query_type = 'INSERT'
  AND returned_rows BETWEEN 1 AND 100
  AND user_id > 1;
```

Disk spill counts (last 7 days, from step detail):

```sql
SELECT COUNT(DISTINCT d.query_id) AS total_disk_spill_count,
       SUM(d.spill_local) / (1024*1024.0) AS total_local_spill_mb,
       SUM(d.spill_remote) / (1024*1024.0) AS total_remote_spill_mb
FROM sys_query_detail d
JOIN sys_query_history h ON d.query_id = h.query_id
WHERE h.start_time >= DATEADD(day, -7, GETDATE())
  AND (d.spill_local > 0 OR d.spill_remote > 0);
```

Signals: pct_wlm_queue_time > 5, copy_count > 100, ddl_count > 10, ctas_count > 10,
compiled_queries > 100, small_insert_count > 100, total_disk_spill_count > 10 → WARN.

---

## Section 3 — Table Info (design health)

```sql
SELECT "schema",
       "table",
       tbl_rows,
       diststyle,
       sortkey1,
       sortkey_num,
       sortkey1_enc,
       skew_rows,
       stats_off,
       unsorted,
       vacuum_sort_benefit,
       empty AS pct_rows_marked_for_deletion,
       max_varchar,
       encoded,
       size AS size_mb,
       pct_used
FROM svv_table_info
WHERE "schema" NOT IN ('pg_catalog', 'information_schema', 'pg_internal')
ORDER BY size DESC
LIMIT 200;
```

Signals (per row): skew_rows >= 4 → FAIL; vacuum_sort_benefit >= 10, stats_off > 10,
pct_rows_marked_for_deletion > 10, max_varchar > 1000 → WARN; large tables (> 5M rows)
without sortkey1 or with EVEN/date DISTKEY → WARN. See operational-review-signals.md
for the full population filters and recommendation IDs.

---

## Section 4 — Advisor (Alter Table) Recommendations

```sql
SELECT type,
       ddl,
       auto_eligible
FROM svv_alter_table_recommendations;
```

Signals: type in (encode, sortkey, diststyle) with auto_eligible = 'f' → surface the
DDL as a recommendation (#4/#7/#8).

---

## Section 5 — Materialized Views

```sql
SELECT database_name,
       schema_name,
       name,
       is_stale,
       state,
       autorefresh,
       autorewrite
FROM svv_mv_info;
```

Recent refresh history:

```sql
SELECT db_name,
       schema_name,
       mv_name,
       status,
       refresh_type,
       duration / 1000000.0 AS refresh_duration_sec,
       start_time
FROM sys_mv_refresh_history
WHERE start_time >= DATEADD(day, -7, GETDATE())
ORDER BY start_time DESC;
```

Signals: state = 0 (full refresh) → recommend incremental MV (#30); is_stale = 't' → (#40);
broken + stale + autorefresh → recreate (#31).

---

## Section 6 — Top Queries by Run Time

Use the existing template in `assets/queries/top50-queries.md` (SYS_QUERY_HISTORY).
For per-query spill and alerts, join SYS_QUERY_DETAIL and flag total_disk_spill_mb > 100.

---

## Section 7 — COPY / Load Performance

Use the existing template in `assets/queries/copy-performance.md`
(SYS_LOAD_HISTORY / SYS_LOAD_DETAIL). Signals: avg_files_per_copy < 4, avg_file_size_mb < 10.

---

## Section 8 — Auto Table Optimization actions (last 30 days)

```sql
SELECT table_id,
       type AS alter_table_type,
       status,
       start_time
FROM sys_auto_table_optimization
WHERE start_time >= DATEADD(day, -30, GETDATE())
ORDER BY start_time DESC;
```

Signal: encode/distkey/sortkey actions not in ('Complete','already recommended') → review (#4/#7/#8).
If SYS_AUTO_TABLE_OPTIMIZATION is not present on the target, report this section as not available.

---

## Section 9 — Workload Evaluation (by scan size, last 7 days)

```sql
WITH q AS (
    SELECT h.query_id,
           h.elapsed_time / 1000000.0 AS elapsed_sec,
           COALESCE(SUM(d.bytes_scanned), 0) / (1024*1024.0) AS scan_mb
    FROM sys_query_history h
    LEFT JOIN sys_query_detail d ON h.query_id = d.query_id
    WHERE h.start_time >= DATEADD(day, -7, GETDATE())
      AND h.user_id > 1
    GROUP BY h.query_id, h.elapsed_time
)
SELECT CASE
           WHEN scan_mb < 100 THEN 'small'
           WHEN scan_mb < 500000 THEN 'medium'
           ELSE 'large'
       END AS workload_type,
       COUNT(*) AS query_cnt,
       ROUND(AVG(scan_mb), 1) AS scan_mb_avg,
       ROUND(AVG(elapsed_sec), 2) AS exec_sec_avg,
       ROUND(MAX(elapsed_sec), 2) AS exec_sec_max
FROM q
GROUP BY 1
ORDER BY query_cnt DESC;
```

Use to describe the dominant workload and support cost/serverless-sizing discussion.

---

## Section 10 — Spectrum / External Query Performance (if used)

```sql
SELECT COUNT(*) AS external_query_count,
       AVG(elapsed_time) / 1000000.0 AS avg_elapsed_sec,
       SUM(total_partitions) AS total_partitions,
       SUM(qualified_partitions) AS qualified_partitions,
       ROUND(100.0 * SUM(qualified_partitions) / NULLIF(SUM(total_partitions), 0), 1) AS partition_pruning_pct
FROM sys_external_query_detail
WHERE start_time >= DATEADD(day, -7, GETDATE());
```

Signal: partition_pruning_pct < 95 → optimize partitioning (#27). If the target has no
external/Spectrum queries or the view is unavailable, report this section as not available.

---

## Section 11 — Data Sharing (if used)

Consumer latency:

```sql
SELECT COUNT(*) AS request_count,
       AVG(duration) / 1000000.0 AS avg_request_duration_secs
FROM sys_datashare_usage_consumer
WHERE record_time >= DATEADD(day, -7, GETDATE());
```

Signal: avg_request_duration_secs > 60 → incremental MV on producer (#34). If not a
datashare consumer or the view is unavailable, report this section as not available.
