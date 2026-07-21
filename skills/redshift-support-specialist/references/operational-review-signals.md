# Redshift Operational Review — Config Analysis

Analysis of the Redshift Cluster Review JSON configuration used for automated health checks. This document maps every section, signal, observation, and recommendation for review before integrating into the skill documents.

---

## Review Sections Overview

The config defines 12 data collection sections, each with a SQL query, automated signals (threshold-based checks), and manual observations (checks that require human review of CloudWatch or console data).

| # | Section | SQL Source View(s) | Purpose |
|---|---------|-------------------|---------|
| 1 | RPUDetails | SYS_SERVERLESS_USAGE | Serverless capacity, storage utilization, usage percentage |
| 2 | UsagePattern | SYS_QUERY_HISTORY + SYS_QUERY_DETAIL | Hourly workload breakdown: query types, caching, queuing, spill, SQA, priorities |
| 3 | TableInfo | SVV_TABLE_INFO + pg_attribute | Table health: skew, sort, compression, stats, deletion bloat, varchar width |
| 4 | AlterTableRecommendations | SVV_ALTER_TABLE_RECOMMENDATIONS + SVV_TABLE_INFO | Advisor recommendations not yet auto-applied |
| 5 | MaterializedView | SVV_MV_INFO + SYS_MV_STATE + SYS_MV_REFRESH_HISTORY | MV health: staleness, refresh type, auto-refresh eligibility |
| 6 | Top50QueriesByRunTime | SYS_QUERY_HISTORY + SYS_QUERY_DETAIL + SVV_USER_INFO | Worst-performing queries: alerts, spill, nested loops, broadcasts |
| 7 | CopyPerformance | SYS_LOAD_HISTORY + SYS_LOAD_DETAIL | COPY throughput: file counts, sizes, parallelism, split copies |
| 8 | ExtQueryPerformance | SYS_EXTERNAL_QUERY_DETAIL + SYS_EXTERNAL_QUERY_ERROR | Spectrum query performance: partition pruning, file sizes, errors |
| 9 | DataShareProducerObject | SVV_DATASHARE_OBJECTS + SVV_TABLE_INFO + SVV_MV_INFO + SYS_VACUUM_HISTORY | Outbound datashare objects: MV refresh type, vacuum status |
| 10 | DataShareConsumerUsage | SYS_DATASHARE_USAGE_CONSUMER + SYS_QUERY_HISTORY | Consumer query performance: latency percentiles, metadata sync |
| 11 | ATOWorkerActions | SYS_AUTO_TABLE_OPTIMIZATION + SVV_TABLE_INFO | Auto Table Optimization status: encode, distkey, sortkey actions |
| 12 | WorkloadEvaluation | SYS_QUERY_HISTORY + SYS_QUERY_DETAIL | Workload classification: small/medium/large by scan size, duration patterns |

---

## Key Fields Used Per Section

### 1. RPUDetails
- `node_type` — Identifies serverless vs provisioned
- `rpus` — Min compute capacity (RPUs)
- `storage_capacity_gb` — Max storage based on RPU tier (128TB for >=32 RPU, 8TB otherwise)
- `storage_used_gb` — Current storage consumption
- `storage_utilization_pct` — Storage used as percentage of capacity
- `usage_pct` — Percentage of time compute was active

### 2. UsagePattern (Aggregated Hourly)
- `query_count` — Total queries per hour
- `copy_count` / `insert_count` / `update_count` / `ddl_count` / `ctas_count` — Query type breakdown
- `result_cache_hits` — Queries served from cache
- `queued_queries` — Queries that waited in WLM queue
- `error_queries` — Queries with errors
- `burst_queries` / `burst_secs` — Queries on concurrency scaling clusters
- `compiled_queries` — Queries requiring compilation (no cache hit)
- `total_queue_time` / `total_compile_time` / `total_planning_time` / `total_lock_wait_time` — Time breakdown
- `pct_wlm_queue_time` — Queue time as percentage of total elapsed
- `low_priority_query_cnt` / `high_priority_query_cnt` — Priority distribution
- `sqa_queries` — Short Query Acceleration usage
- `rrscan_queries` — Range-restricted scan queries (sort key effective)
- `total_disk_spill_count` / `local_disk_spill_count` / `remote_disk_spill_count` — Disk spill indicators
- `total_local_disk_spill_mb` / `total_remote_disk_spill_mb` — Spill volume
- `small_insert_count` — Single/small row inserts (1-100 rows)

### 3. TableInfo
- `max_varchar` — Widest VARCHAR column
- `sortkey1` — First sort key column (or AUTO/INTERLEAVED)
- `tbl_rows` — Row count
- `skew_rows` — Distribution skew ratio
- `diststyle` — Distribution style (KEY/ALL/EVEN/AUTO)
- `vacuum_sort_benefit` — Expected benefit from VACUUM SORT (0-100)
- `stats_off` — Statistics staleness percentage
- `sortkey1_enc` — Encoding on first sort key column
- `sortkey_num` — Number of sort key columns
- `num_rows_marked_for_deletion` — Ghost rows (deleted but not vacuumed)
- `pct_rows_marked_for_deletion` — Ghost rows as percentage
- `encoded_column_pct` — Percentage of columns with compression
- `column_count` / `encoded_column_count` — Column compression coverage

### 4. AlterTableRecommendations
- `type` — Recommendation type: encode, sortkey, diststyle
- `ddl` — Suggested ALTER TABLE DDL
- `auto_eligible` — Whether ATO can apply automatically (t/f)

### 5. MaterializedView
- `is_stale` — Whether MV data is outdated
- `state` — Refresh capability (0=full, 1=incremental, 101-105=broken)
- `autorewrite` — Whether query rewrite uses this MV
- `autorefresh` — Whether auto-refresh is enabled
- `mv_state` / `event_desc` — Current MV state from SYS_MV_STATE
- `refresh_status` / `refresh_type` / `refresh_duration_secs` — Last refresh details

### 6. Top50QueriesByRunTime
- `generic_query_hash` — Query fingerprint for grouping repeated queries
- `execution_time_sec` — Worst execution time per query pattern
- `query_cnt` — How many times this pattern ran
- `compile_time_sec` / `planning_time_sec` / `lock_wait_time_sec` / `queue_time_sec` — Time breakdown
- `pct_wlm_queue_time` — Queue overhead
- `alerts` — Aggregated alert types: Large nljoin, Scanning unsorted data, Large broadcast, Large distribution, Missing statistics
- `disk_spill` / `local_disk_spill_mb` / `remote_disk_spill_mb` / `total_disk_spill_mb` — Spill metrics
- `table_list` — Tables involved in the query
- `rrscan` — Whether range-restricted scan was used

### 7. CopyPerformance
- `file_format` — Input file format
- `split_copies` — COPYs where file splitting occurred
- `rows_inserted` / `files_scanned` / `mb_scanned` — Volume metrics
- `insert_rate_rows_per_second` — Throughput
- `avg_files_per_copy` — Parallelism indicator
- `avg_file_size_mb` — File size (too small = inefficient)
- `avg_scan_kbps` — Scan throughput
- `no_of_copy` / `total_copy_time_secs` / `avg_copy_time_secs` — COPY frequency and duration
- `error_count` — Load errors

### 8. ExtQueryPerformance (Spectrum)
- `source_type` — External data source type
- `file_format` — File format (Parquet, ORC, CSV, etc.)
- `is_table_partitioned` — Whether external table has partitions
- `external_table_partition_count` — Total partitions
- `total_query_count` — Query volume
- `pct_of_query_using_Partition_Pruning` — Partition pruning effectiveness
- `avg_Qualified_Partitions` — Average partitions scanned per query
- `avg_Files` / `avg_file_size_mb` — File metrics
- `avg_Elapsed_sec` / `Total_Elapsed_sec` — Duration
- `total_scan_error_count` — Scan errors

### 9. DataShareProducerObject
- `share_type` / `share_name` / `include_new` — Datashare config
- `object_type` — Shared object type (table)
- `estimated_visible_rows` — Table size
- `last_vacuum_date` / `vacuum_type` — Last vacuum on shared object
- `mv_is_stale` — Whether shared MV is stale
- `is_mv_incremental_refresh` — Whether MV uses incremental refresh
- `is_mv_auto_refresh` — Whether MV auto-refreshes

### 10. DataShareConsumerUsage
- `query_count` — Consumer query volume
- `avg_query_execution_secs` / `total_query_execution_secs` — Query performance
- `avg_request_duration_secs` — Metadata sync + query duration
- `p80_request_sec` / `p90_request_sec` / `p99_request_sec` — Latency percentiles
- `total_request_error_count` — Consumer errors

### 11. ATOWorkerActions
- `alter_table_type` — encode, distkey, sortkey
- `status` — Action result (Complete, Abort, etc.)
- `alter_from` — Previous configuration

### 12. WorkloadEvaluation
- `workloadtype` — small/medium/large (by scan MB)
- `perc_of_total_workload` — Percentage of total compute time
- `perc_duration_in_day` — How much of the day this workload runs
- `workload_exec_sec_avg` / `workload_exec_sec_min` / `workload_exec_sec_max` — Duration stats
- `query_cnt` — Query count per workload type
- `scan_mb_avg` — Average data scanned


---

## All Automated Signals (Threshold-Based Checks)

### RPUDetails Signals

| Signal | Criteria | Threshold | Recommendations |
|--------|----------|-----------|-----------------|
| Storage exceeds 70% threshold | `storage_utilization_pct > 70` (DC/DS nodes only) | 70% | Increase RPU (#5), Unload to S3/Spectrum (#6), Review compression (#4), Schedule VACUUM DELETE (#2) |

### RPUDetails Manual Observations (Require Human Review)

| Observation | Recommendations |
|-------------|-----------------|
| CloudWatch CPU consistently above 80% — ATO/Auto Vacuum/Auto Analyze may not trigger | Increase RPU (#5), Isolate workloads via data sharing (#26) |
| SSD Cache hit rate shows frequent misses (querying non-cached data) | Increase RPU (#5) |
| CloudWatch Disk Usage has sudden peaks and drops (possible disk spill) | Increase RPU (#5) |
| CloudWatch connections consistently high | Set idle session timeout (#36) |
| Workgroup is on 'Current' track | Use 'Trailing' track for production (#37) |
| AI-Driven Scaling is disabled | Enable AI-driven scaling (#39) |
| QMR Rules have not been setup | Add QMR rules (#22) |

### UsagePattern Signals

| Signal | Criteria | Recommendations |
|--------|----------|-----------------|
| High count of COPY commands | `copy_count > 100` | Optimize COPY operations (#23) |
| High count of small inserts (1-100 rows) | `small_insert_count > 100` | Replace with COPY/bulk insert (#29) |
| High count of DDL commands | `ddl_count > 10` | Reduce drop/create, use delete/copy/insert (#25) |
| High count of CTAS commands | `ctas_count > 10` | Reduce drop/create, use delete/copy/insert (#25) |
| High WLM queue time | `pct_wlm_queue_time > 5` | Isolate workloads via data sharing (#26) |
| High disk spill count | `total_disk_spill_count > 10` | Increase RPU (#5), Reduce varchar (#11), Add QMR (#22), Add join predicates (#24) |
| High compilation count | `compiled_queries > 100` | Reduce drop/create operations (#25) |

### TableInfo Signals

| Signal | Criteria | Population Filter | Recommendations |
|--------|----------|-------------------|-----------------|
| Tables with wide columns | `max_varchar > 1000` | All tables | Reduce varchar to max length (#11) |
| Large tables without sort key | `sortkey1 = ''` | > 5M rows, not AUTO SORTKEY | Choose best sort key (#7) |
| Large tables with skew | `skew_rows >= 4`, not AUTO dist | > 5M rows | Choose best distribution (#8) |
| Large tables with unsorted data | `vacuum_sort_benefit >= 10` | > 5M rows | VACUUM SORT (#13) |
| Tables with interleaved sort keys | `sortkey1 like '%INTERLEAVED%'` | > 5M rows | Replace with MVs (#14) |
| Small tables without ALL distribution | `diststyle not like '%ALL%'` | <= 5M rows, not AUTO | Change to ALL or AUTO (#8) |
| Small tables with a sort key | `sortkey1 != ''` | <= 5M rows, not AUTO SORTKEY | Remove sort key (#7) |
| Large tables needing VACUUM DELETE | `pct_rows_marked_for_deletion > 10` | > 5M rows | Schedule VACUUM DELETE (#2) |
| Tables with stale statistics | `stats_off > 10 or null` | All tables | Schedule ANALYZE (#3) |
| Large tables with encoded sort keys | `sortkey1_enc != 'none'` and not empty | > 5M rows, not AUTO SORTKEY | Remove sort key encoding (#10) |
| Tables with low compression | `encoded_column_pct < 80` | > 5M rows, accounting for unencoded sort key | Review compression (#4) |
| Large tables distributed by date/datetime | `diststyle like '%KEY%date%'` etc. | > 5M rows | Change distribution key (#8) |

### AlterTableRecommendations Signals

| Signal | Criteria | Recommendations |
|--------|----------|-----------------|
| Encoding recommendation not auto-applied | `type='encode' and auto_eligible='f'` | Review compression (#4) |
| Sort key recommendation not auto-applied | `type='sortkey' and auto_eligible='f'` | Choose best sort key (#7) |
| Dist key recommendation not auto-applied | `type='diststyle' and auto_eligible='f'` | Choose best distribution (#8) |

### MaterializedView Signals

| Signal | Criteria | Recommendations |
|--------|----------|-----------------|
| MV doing full refresh (not incremental) | `state=0` | Create MV for incremental refresh (#30) |
| MV cannot be auto-refreshed (broken) | `state > 1 and is_stale='t' and autorefresh='y'` | Recreate the MV (#31) |
| MV is stale | `is_stale = 't'` | Ensure MVs are refreshed for data accuracy and query rewrite |

### Top50QueriesByRunTime Signals

| Signal | Criteria | Recommendations |
|--------|----------|-----------------|
| Long queries with missing statistics | `alerts like '%stat%'` | Schedule ANALYZE (#3) |
| Long queries with nested loop joins | `alerts like '%nl%'` | Add QMR rules (#22), Remove cross-joins (#9) |
| Long queries with dist/broadcast alerts | `alerts like '%dist%' or '%broadcast%'` | Choose best distribution (#8) |
| Long queries with sort alerts | `alerts like '%sort%'` | Choose best sort key (#7) |
| Queries with large disk spill | `total_disk_spill_mb > 100` | Increase RPU (#5), Reduce varchar (#11), Add QMR (#22), Add join predicates (#24) |

### CopyPerformance Signals

| Signal | Criteria | Population Filter | Recommendations |
|--------|----------|-------------------|-----------------|
| Large COPYs not loading in parallel | `avg_files_per_copy < 4 and split_copies = 0` | > 24 COPYs, avg > 60s | Optimize COPY operations (#23) |
| Files with small size | `avg_file_size_mb < 10` | > 24 COPYs | Optimize COPY file sizes (#23) |

### ExtQueryPerformance (Spectrum) Signals

| Signal | Criteria | Population Filter | Recommendations |
|--------|----------|-------------------|-----------------|
| Long queries not using partition pruning | `pct_of_query_using_partition_pruning < 95` | avg partitions > 100, avg elapsed > 60s | Optimize partitioning strategy (#27) |

### DataShareProducerObject Signals

| Signal | Criteria | Recommendations |
|--------|----------|-----------------|
| Shared MV doing full refresh | `is_mv_incremental_refresh = 'N'` | Create incremental MV on producer, share MV (#34) |

### DataShareConsumerUsage Signals

| Signal | Criteria | Recommendations |
|--------|----------|-----------------|
| Long metadata sync duration | `avg_request_duration_secs > 60` | Create incremental MV on producer (#34) |

### ATOWorkerActions Signals

| Signal | Criteria | Population Filter | Recommendations |
|--------|----------|-------------------|-----------------|
| Column encoding not set to auto | `alter_table_type='encode'` | Status not 'already recommended' or 'Complete' | Review compression (#4) |
| Distribution style not set to auto | `alter_table_type='distkey'` | Status not 'already recommended' or 'Complete' | Choose best distribution (#8) |
| Sort key not set to auto | `alter_table_type='sortkey'` | Status not 'already recommended' or 'Complete' | Choose best sort key (#7) |


---

## Complete Recommendation Catalog

All 31 unique recommendations referenced by the signals, organized by category and effort level.

### Capacity & Scaling

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 5 | Increase base RPU (if not using AI Scaling) to address disk spill and memory pressure | Small | Storage/memory pressure, heavy disk spill |
| 6 | Unload infrequently accessed data to S3 and query using Redshift Spectrum | Medium | Storage > 70%, cold data in local tables |
| 35 | Leverage Redshift Serverless for better price-performance with intermittent workloads | Medium | On-demand provisioned with intermittent usage |
| 39 | Enable AI-driven scaling for optimized resource utilization | Medium | Serverless workgroups without AI scaling |

### Table Design — Distribution

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 8 | Choose the best distribution style (AUTO recommended; KEY for large joined tables; ALL for small tables <= 5M rows) | Medium | Skew >= 4, small tables not ALL, date-based DISTKEY, Advisor recommendations |

### Table Design — Sort Keys

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 7 | Choose the best sort key (AUTO for large tables; NONE for small tables <= 5M rows) | Small | Large tables without sort key, small tables with unnecessary sort key, Advisor recommendations |
| 10 | Remove encoding on first column of sort key | Small | Encoded sort key columns on large tables |
| 14 | Replace interleaved sort keys with Materialized Views | Medium | Tables with interleaved sort keys (not eligible for CS/data sharing) |

### Table Design — Compression

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 4 | Review compression encodings (use ENCODE AUTO, AZ64 for numeric, ZSTD for char) | Medium | Low encoded_column_pct, storage pressure, Advisor recommendations |
| 11 | Reduce VARCHAR fields to match actual max length to avoid memory waste and disk spill | Small | max_varchar > 1000, disk spill issues |

### Maintenance — VACUUM

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 2 | Schedule VACUUM DELETE for busy clusters | Small | pct_rows_marked_for_deletion > 10%, storage pressure |
| 13 | Ensure large tables with sort keys are vacuumed (VACUUM SORT RECLUSTER) | Medium | vacuum_sort_benefit >= 10 on large tables |

### Maintenance — ANALYZE

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 3 | Schedule ANALYZE commands for busy clusters (consider ANALYZE PREDICATE COLUMNS for wide tables) | Medium | stats_off > 10, missing statistics alerts on queries |

### Query Performance

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 9 | Remove nested loop joins (cross-joins) | Medium | Nested loop alerts in top queries |
| 22 | Add QMR rules (query_execution_time, query_temp_blocks_to_disk, spectrum_scan_size_mb) | Medium | No QMR configured, disk spill, runaway queries |
| 24 | Add redundant predicates to both sides of joins to help optimizer skip blocks | Medium | Disk spill, large scan queries |
| 25 | Reduce compile overhead — avoid drop/create, use delete/copy/insert instead | Medium | High DDL/CTAS count, high compilation count |

### Data Loading

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 23 | Optimize COPY operations (file count = multiple of slices, file size 1-125 MB compressed, single COPY per table) | Medium | Low parallelism, small files, high COPY count |
| 29 | Replace single-row inserts with COPY, bulk insert, or ALTER TABLE APPEND | Medium | small_insert_count > 100 |

### Materialized Views

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 30 | Create MVs that support incremental refresh (avoid full refresh) | Medium | MV state=0 (full refresh) |
| 31 | Recreate broken MVs (due to DDL changes on base tables) | Small | MV state > 1, stale, auto-refresh enabled but failing |
| 40 | Ensure MVs are not stale for data accuracy and query rewrite | Medium | is_stale = 't' |

### Workload Management

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 16 | Re-allocate WLM memory to add up to 100% (manual WLM) | Small | Manual WLM with < 100% allocation |
| 18 | Create separate query queues per workload (BI, ETL, ad-hoc, etc.) | Medium | Single default queue handling all workloads |
| 21 | Set priorities for each WLM queue (Auto WLM) | Small | Auto WLM without priority differentiation |
| 26 | Isolate workloads using data sharing for scalability | Medium | High WLM queue time, CPU > 80%, connection pressure |

### Spectrum / Data Lake

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 27 | Optimize Spectrum partitioning strategy (partition by frequently filtered columns) | Medium | Partition pruning < 95%, high partition counts |

### Data Sharing

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 34 | Create incremental MV on producer and share to consumers (for frequently updated data) | Medium | Shared MV doing full refresh, long consumer metadata sync |

### Cluster Operations

| ID | Recommendation | Effort | When to Apply |
|----|---------------|--------|---------------|
| 36 | Set idle session timeout and clean up idle sessions | Small | High connection count approaching limits |
| 37 | Use 'Trailing' maintenance track for production workloads | Small | Workgroup on 'Current' track |
| 38 | Perform classic resize to rebalance data after elastic resize | Large | Processing skew after elastic resize |

---

## Summary: Key Thresholds for Automated Detection

| Metric | Threshold | Severity | Action |
|--------|-----------|----------|--------|
| storage_utilization_pct | > 70% | WARN | Increase RPU, unload to S3, review compression |
| skew_rows | >= 4 | FAIL | Change distribution style |
| vacuum_sort_benefit | >= 10 | WARN | VACUUM SORT RECLUSTER |
| stats_off | > 10 | WARN | Run ANALYZE |
| pct_rows_marked_for_deletion | > 10% | WARN | VACUUM DELETE |
| max_varchar | > 1000 | WARN | Reduce column width |
| encoded_column_pct | < 80% | WARN | Review compression |
| pct_wlm_queue_time | > 5% | WARN | Isolate workloads / data sharing |
| total_disk_spill_count | > 10 | WARN | Increase RPU, reduce varchar, add QMR |
| compiled_queries | > 100 | WARN | Reduce DDL/CTAS operations |
| small_insert_count | > 100 | WARN | Replace with COPY/bulk insert |
| copy_count | > 100 | INFO | Optimize COPY parallelism |
| ddl_count | > 10 | WARN | Reduce drop/create patterns |
| avg_files_per_copy | < 4 (with no splits) | WARN | Split files for parallelism |
| avg_file_size_mb | < 10 | WARN | Increase file sizes |
| partition_pruning_pct | < 95% | WARN | Optimize partitioning |
| avg_request_duration_secs (consumer) | > 60s | WARN | Create incremental MV on producer |
| total_disk_spill_mb (per query) | > 100 MB | WARN | Increase RPU, optimize query |
