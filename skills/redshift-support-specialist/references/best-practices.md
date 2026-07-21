# Amazon Redshift Best Practices

Comprehensive reference for table design, distribution styles, sort keys, compression encoding, workload management, data loading, query optimization, security patterns, and cost optimization for Amazon Redshift provisioned clusters and Serverless workgroups.

Sources: [Amazon Redshift documentation](https://docs.aws.amazon.com/redshift/latest/dg/), [AWS Big Data Blog](https://aws.amazon.com/blogs/big-data/), [Redshift Advisor recommendations](https://docs.aws.amazon.com/redshift/latest/dg/advisor-recommendations.html). Content was rephrased for compliance with licensing restrictions.

---

## 0. Data Modeling

- Use STAR schema (fact + dimension tables) as the primary data model — this is the most common and recommended pattern for Redshift.
- Highly denormalized models are also effective for Redshift's columnar architecture.
- Snowflake schema is supported but less common.
- Avoid highly normalized models (e.g., 3NF) — these are more appropriate for OLTP systems and result in excessive joins on Redshift.

---

## 1. Table Design

### 1.1 Distribution Styles

| Style | Description | Best For | Trade-offs |
|-------|-------------|----------|------------|
| KEY | Rows distributed by hash of a specified column | High-cardinality join columns; enables co-located joins with matching DISTKEY tables | Skew risk if column values are unevenly distributed |
| ALL | Full copy of the table on every node | Small dimension tables (<= 5 million rows) joined frequently with large tables | Increases storage and COPY/INSERT time; do not use for large or frequently updated tables |
| EVEN | Round-robin distribution across all nodes | Large tables with no clear join column or when no single column avoids skew | No co-location benefit for joins; data redistribution occurs at query time |
| AUTO | Redshift chooses automatically (starts ALL, switches to EVEN as table grows) | Default when unsure; good starting point | Redshift may not always pick the optimal strategy for complex join patterns |

**Decision Flow:**

1. Use `AUTO` wherever possible — it is the recommended default.
2. Specify the primary key and foreign keys for all your tables. This will help AUTO make efficient decisions.
3. Is the table small (<= 5M rows) and frequently joined? → `ALL`
4. Is there a high-cardinality column used in JOINs with other large tables? → `KEY` on that column
5. Is the table large with no clear join pattern or does not participate in joins? → `EVEN`

**Distribution Key Selection (when using KEY):**

- Use a high cardinality join column as the distribution key.
- Avoid date columns as the distribution key.
- When joining a fact table with multiple dimension tables, use the same distribution key for the fact table and the large dimension table for co-located joins.

**Detecting Skew:**

```sql
-- Check distribution skew for all tables
SELECT "table", skew_rows, skew_sortkey1, diststyle, size
FROM svv_table_info
WHERE skew_rows > 1.5
ORDER BY skew_rows DESC;
```

A `skew_rows` value > 1.5 means the largest slice has 1.5x more rows than the smallest. Values > 4.0 are severe and require redistribution.

### 1.2 Sort Keys

**Sort Key Selection Guidelines:**

| Scenario | Recommendation |
|----------|---------------|
| Large table (> 5M rows), no clear pattern | Use Sort Key AUTO |
| Small table (<= 5M rows) | Don't use sort keys — not beneficial |
| Time-series data, most queries filter by date/time | Compound sort key with timestamp as leading column |
| Queries always filter on the same column | Compound sort key with that column first |
| Queries filter on 2-3 columns with equal frequency | Consider interleaved sort key (accept VACUUM overhead) |
| Table is frequently joined but rarely filtered | Sort key may not help; focus on DISTKEY instead |
| Frequently joined table | Specify the join column as both the sort key and the distribution key on both tables — this results in a merge join, which is faster than a hash join |
| No clear filter pattern | Use AUTO sort key or no sort key |

**Sort Key Column Rules:**

- Don't pick more than 4 columns for the SORT KEY — beyond 4, there is no added benefit from the additional columns.
- When there is more than one column in the SORT KEY, their order matters: effective sort key order is lower to higher cardinality (low cardinality columns first, high cardinality columns last).
- Always use the leading sort key column in the filter condition.
- Don't apply compression encoding on sort key columns.
- Don't apply functions in queries when using SORT KEY columns in filters. For example, if `business_date` is the SORT KEY, don't apply a filter like `to_char(business_date,'YYYY') = '2023'`.

**Interleaved Sort Keys:**

- Give equal weight to each column in the key.
- Useful when queries filter on different columns with roughly equal frequency.
- Higher VACUUM overhead — requires periodic `VACUUM REINDEX` when data distribution changes.
- Not recommended for columns with monotonically increasing values (e.g., timestamps, identity columns).

**Anti-Patterns:**

- Applying functions to sort key columns in WHERE clauses defeats zone-map pruning: `WHERE DATE_TRUNC('day', ts) = '2026-01-01'` → use `WHERE ts >= '2026-01-01' AND ts < '2026-01-02'` instead
- Leading wildcard LIKE patterns (`LIKE '%value'`) cannot leverage sort keys
- Sorting on very low-cardinality columns (e.g., boolean, status with 3 values) provides minimal benefit

### 1.3 Compression Encoding

Redshift is columnar — compression reduces storage and I/O, improving query performance. Each column can have its own encoding.

**Recommended Approach:**

- Use `ENCODE AUTO` (default) and let Redshift choose encodings automatically during COPY
- Run `ANALYZE COMPRESSION <table_name>` to review Redshift's recommended encodings for existing tables
- For the initial load of a new table, use COPY (not INSERT) so Redshift can sample data and apply optimal encodings

**Encoding Types:**

| Encoding | Best For | Notes |
|----------|----------|-------|
| RAW | Leading column of a sort key, or columns with high cardinality/randomness | No compression applied; recommended for the first sort key column to preserve zone-map effectiveness |
| AZ64 | Numeric, date, and timestamp columns | Redshift-proprietary; generally the best default choice for numeric/date types |
| ZSTD | General-purpose; works well across most data types | Good default for VARCHAR/CHAR columns and mixed workloads |
| LZO | Text and CHAR/VARCHAR columns | Effective on free-form text; slower to decode than BYTEDICT for low-cardinality data |
| BYTEDICT | Low-cardinality columns (few distinct values) | Builds a dictionary of unique values |
| DELTA / DELTA32K | Monotonically increasing/decreasing numeric columns (e.g., sequential IDs, timestamps) | Stores the difference between consecutive values |
| MOSTLY8 / MOSTLY16 / MOSTLY32 | Numeric columns where most values fit in a smaller type | Compresses the majority of values into fewer bytes |

**Key Rules:**

- Do NOT compress the leading column of a compound sort key — it reduces zone-map effectiveness
- Let COPY auto-apply encodings on the first load to an empty table
- After changing encodings, perform a deep copy (CREATE TABLE AS or UNLOAD/COPY) to rewrite data with new encodings

### 1.4 Data Types

- Use the narrowest type that fits: `SMALLINT` (2 bytes) over `INTEGER` (4 bytes) over `BIGINT` (8 bytes)
- Make columns only as wide as they need to be. Redshift performance is about efficient I/O — do not arbitrarily assign maximum length/precision, as this can slow down query execution time.
- Use appropriate data types — don't store dates as VARCHAR.
- Use `VARCHAR(n)` with a reasonable max length instead of `VARCHAR(MAX)` — Redshift allocates memory based on declared column width for query processing
- Use the VARCHAR data type for UTF-8 multibyte character support (up to a maximum of four bytes per character)
- Use `DATE` (4 bytes) or `TIMESTAMP` (8 bytes) instead of storing dates as strings
- Avoid `CHAR(n)` for variable-length data — it pads with spaces and wastes storage
- Use `BOOLEAN` (1 byte) for true/false values instead of `INTEGER` or `VARCHAR`
- Use `DECIMAL`/`NUMERIC` for exact precision (financial data); use `FLOAT`/`DOUBLE` only when approximate precision is acceptable
- Use the GEOMETRY data type and spatial functions to store, process, and analyze spatial data
- Use the HyperLogLog Sketch (HLLSKETCH) data type to improve performance of count-distinct operations
- Use the SUPER data type for semi-structured data (JSON) and for evolving/schema-less data. Supports up to 16 MB per individual SUPER field or object. Use PartiQL query language extensions for easy access to nested data.

**SUPER Data Type Best Practices:**

- For low-latency inserts or small batch inserts, insert into SUPER — inserts into the SUPER data type are quicker.
- If you join or filter frequently using attributes stored in SUPER, create separate scalar data type columns for those attributes to improve performance.
- Use SUPER when queries require strong consistency, predictable query performance, complex query support, and ease of use with evolving schemas.
- Use Redshift Spectrum instead of loading into SUPER if the data requires integration with other AWS services (e.g., EMR).

---

## 2. Data Loading & Ingestion

### 2.1 COPY Best Practices

- Use the COPY command to load data whenever possible.
- Use a single COPY command per table.
- When using COPY, avoid loading from many small files or from large non-splittable files.
- If COPY is not possible, do bulk inserts using an INSERT statement. Avoid single-row inserts.
- Use COPY JOB for automated/incremental loading of data from Amazon S3 — detects and loads new S3 files without manual intervention, and tracks loaded files to ensure one-time loading.
- Use `COMPUPDATE OFF` and `STATUPDATE OFF` after the initial load (when encodings are already set) to speed up ingestion.
- Use `COPY ... NOLOAD` to validate data without loading — catches format errors, type mismatches, and constraint violations.
- Use `MAXERROR` to control error tolerance (0 = fail on any error, recommended for production).
- Check `STL_LOAD_ERRORS` and `STL_LOADERROR_DETAIL` for error diagnostics after failed loads.
- Use IAM roles (not access keys) for S3 authentication.

COPY leverages Redshift's MPP architecture to load data in parallel across all compute nodes.

**Optimal File Sizes for COPY:**

| File Type | Size Range | Notes |
|-----------|-----------|-------|
| Non-splittable files | 1 MB – 1 GB each (compressed) | One compute slice processes one file |
| Splittable columnar (Parquet, ORC) | 128 MB – 1 GB | Supports parallel reads within a single file |
| Splittable row-oriented (CSV) | 64 MB – 10 GB | Only when NOT using REMOVEQUOTES, ESCAPE, or FIXEDWIDTH keywords |

**File Preparation:**

| Practice | Recommendation |
|----------|---------------|
| File count | Split into a multiple of the number of slices in the cluster (e.g., 16 slices → 16, 32, or 48 files) |
| File size | 1 MB – 1 GB each (compressed). Avoid very small files (< 1 MB) or very large single files |
| Compression | Use GZIP, ZSTD, LZO, or BZIP2. COPY decompresses automatically. Reduces S3 transfer time |
| Format | Delimited (CSV/TSV), JSON, Parquet, ORC, Avro. Columnar formats (Parquet/ORC) are fastest |
| Sort order | Pre-sort files by the table's sort key to minimize the unsorted region after load |
| Manifest | Use a manifest file for production loads to ensure exactly the right files are loaded |

### 2.2 Incremental Loading Patterns

- **Append-only:** COPY new data into the table. Best for time-series or event data
- **Upsert (merge):** Load into a staging table, then DELETE matching rows from target + INSERT from staging (within a single transaction for atomicity)
- **Full refresh:** Truncate and reload. Use `TRUNCATE` (not DELETE) — it's instant and doesn't require VACUUM
- **Deep copy for schema changes:** `CREATE TABLE new_table (LIKE old_table)` → COPY data → rename tables

### 2.3 Data Loading Best Practices (General)

- Load your data in sort key order to avoid needing to vacuum.
- For large amounts of data, load in small sequential blocks according to sort order: this eliminates the need to vacuum, uses much less intermediate sort space during each load, and makes it easier to restart if the COPY fails and is rolled back.
- For data with a fixed retention period, organize your data as a sequence of time-series tables.
- Use the MERGE statement to perform upserts.
- Enforce Primary, Unique, or Foreign Key constraints in ETL.
- Wrap workflow/statements in an explicit transaction.
- Consider using TRUNCATE instead of DELETE.

### 2.4 UNLOAD Best Practices

- Use UNLOAD to export query results to S3 in parallel
- Specify `PARALLEL ON` (default) for fastest export
- Use `FORMAT PARQUET` for downstream analytics consumption
- Use `MAXFILESIZE` to control output file sizes (default 6.2 GB)
- Use `MANIFEST` to generate a manifest of output files

---

## 3. Workload Management (WLM)

### 3.1 Automatic WLM (Recommended)

Automatic WLM dynamically manages memory allocation and concurrency. Instead of configuring slot counts and memory percentages, you assign priority levels to queues.

**Priority Levels:** Highest, High, Normal (default), Low, Lowest

**Recommended Queue Configuration:**

| Queue | Priority | User/Query Group | Purpose |
|-------|----------|-------------------|---------|
| ETL/Ingestion | High | `group:etl_users` or `query_group:etl` | COPY, INSERT, UPDATE, DELETE operations |
| Interactive/BI | Highest | `group:bi_users` or `query_group:dashboard` | Dashboard queries, BI tool queries |
| Ad-hoc/Exploratory | Normal | `group:analyst_users` | Analyst ad-hoc queries |
| Batch/Reports | Low | `group:batch_users` or `query_group:batch` | Long-running batch reports |
| Default | Low | (catch-all) | Unclassified queries |

### 3.2 Manual WLM (Legacy)

If using manual WLM:

- Use manual WLM if you want to manually fine-tune and completely understand your workload patterns, or require throttling certain types of queries depending on the time of day.
- Keep WLM queues to a minimum, typically just three queues, to avoid having unused queues.
- Limit ingestion/ELT concurrency to two or three.
- To maximize query throughput, keep total concurrent queries across all queues to 15 or less.
- Save the superuser queue for administration tasks and canceling queries.
- Allocate memory proportional to workload needs (e.g., ETL 40%, BI 40%, ad-hoc 15%, default 5%).
- Each slot gets `queue_memory% / concurrency` of the queue's memory allocation.
- Higher concurrency = less memory per query = potential disk-based operations for large queries.

### 3.3 Query Monitoring Rules (QMR)

Define rules to automatically handle runaway queries. Use QMR on `query_execution_time`, `query_temp_blocks_to_disk`, and `spectrum_scan_size_mb` or `spectrum_scan_row_count`. QMR actions include LOG, ABORT, and HOP (move query to another queue).

| Rule | Threshold | Action |
|------|-----------|--------|
| Query execution time | > 300 seconds | LOG or ABORT |
| Query CPU time | > 600 seconds | LOG or ABORT |
| Rows returned | > 1,000,000,000 | ABORT |
| Nested loop join row count | > 1,000,000 | LOG |
| Disk spill (query_temp_blocks_to_disk) | > 10 GB | LOG |
| Spectrum scan size (spectrum_scan_size_mb) | Threshold per use case | LOG or ABORT |
| Spectrum scan row count (spectrum_scan_row_count) | Threshold per use case | LOG or ABORT |
| Scan rows vs returned rows ratio | > 1,000,000:1 | LOG |

### 3.4 Concurrency Scaling

- Automatically adds transient cluster capacity to handle bursts of concurrent read queries, with no changes to the application.
- Each active cluster accrues one hour of free Concurrency Scaling credits per day; usage beyond the free credits is billed per-second.
- Only applies to read queries by default; write queries can be enabled via WLM queue configuration.
- Monitor usage with the `ConcurrencyScalingActiveClusters` CloudWatch metric and the `STL_QUERY` `concurrency_scaling_status` column.
- Enable per-queue in WLM configuration; not all queues need Concurrency Scaling enabled.

---

## 4. Query Performance Optimization

### 4.1 Query Plan Analysis

```sql
-- Always check the query plan before running expensive queries
EXPLAIN
SELECT ...
FROM large_table a
JOIN another_large_table b ON a.id = b.id
WHERE a.event_date >= '2026-01-01';
```

**Red Flags in EXPLAIN Output:**

| Pattern | Meaning | Fix |
|---------|---------|-----|
| DS_BCAST_INNER | Inner table is broadcast to all nodes | Co-locate tables with matching DISTKEY or use ALL distribution for small table |
| DS_DIST_ALL_INNER | Inner table distributed to all nodes | Same as above |
| DS_DIST_BOTH | Both tables redistributed | Set matching DISTKEY on the join column |
| Nested Loop | Cartesian product or missing join condition | Add proper join conditions; check for implicit cross joins |
| Seq Scan with no filter | Full table scan | Add WHERE predicates; check sort key alignment |
| Hash Join with large build table | Large hash table in memory | Ensure smaller table is on the build side; check distribution |

### 4.2 Query Writing Best Practices

- **SELECT only needed columns** — Redshift is columnar; fewer columns = less I/O
- Use a CASE expression to perform complex aggregations instead of selecting from the same table multiple times.
- Use subqueries in cases where one table in the query is used only for predicate conditions and the subquery returns a small number of rows (less than about 200).
- **Filter early** — Push WHERE clauses as deep as possible in subqueries and CTEs
- **Use range predicates on sort keys** — `WHERE ts BETWEEN '2026-01-01' AND '2026-01-31'` enables zone-map pruning
- **Avoid functions on sort key columns** — `WHERE EXTRACT(year FROM ts) = 2026` defeats zone maps
- **Avoid `SELECT DISTINCT` on large result sets** — Use `GROUP BY` instead (often more efficient)
- **Avoid correlated subqueries** — Rewrite as JOINs or window functions
- **Use approximate functions** — `APPROXIMATE COUNT(DISTINCT col)` is much faster than exact `COUNT(DISTINCT col)` for large datasets
- **Limit LIKE wildcards** — `LIKE 'prefix%'` can use sort keys; `LIKE '%suffix'` cannot
- In predicates, use the least expensive operators: comparison condition operators are preferable to LIKE operators, and LIKE operators are still preferable to SIMILAR TO or POSIX operators.
- Avoid using functions in query predicates.
- Add predicates to filter tables that participate in joins, even if the predicates apply the same filter on both sides — this helps the optimizer prune data on both sides of the join.
- **Leverage result caching** — Identical queries return cached results instantly (enabled by default)

**Aggregation Best Practices:**

- Use sort keys in the GROUP BY clause so the query planner can use more efficient aggregation.
- If you use both GROUP BY and ORDER BY clauses, make sure the columns are in the same order in both.

**Materialized Views Best Practices:**

- Rely on the automated materialized views feature instead of creating your own equivalents manually.
- Create materialized views that can be incrementally refreshed in order to avoid full refresh.
- Schedule manual refresh for nested materialized views or those not eligible for auto-refresh.
- Follow query best practices when writing materialized view queries.
- Follow table design best practices on distribution style and sort key when creating the materialized view.
- Automatic query rewrite leverages relevant materialized views and can improve query performance by orders of magnitude.
- Incremental materialized views on external data lake tables offer cost-effective incremental updates, avoiding full recomputation.

### 4.3 Join Optimization

- Co-locate large tables on the same DISTKEY column used in JOINs
- Use ALL distribution for small dimension tables joined with large fact tables
- Ensure join columns have matching data types to avoid implicit casting
- Place the larger table on the outer (left) side of the JOIN
- Use INNER JOIN instead of OUTER JOIN when possible (allows more optimizer strategies)

---

## 5. Maintenance

### 5.1 VACUUM

| Command | Purpose | When to Use |
|---------|---------|-------------|
| `VACUUM FULL <table>` | Sort + delete reclaim | After significant DELETE/UPDATE operations |
| `VACUUM SORT ONLY <table>` | Re-sort unsorted region only | When `unsorted` % in SVV_TABLE_INFO > 20% |
| `VACUUM DELETE ONLY <table>` | Reclaim space from deleted rows | When `empty` % in SVV_TABLE_INFO > 20% |
| `VACUUM REINDEX <table>` | Rebuild interleaved sort index | When interleaved sort key performance degrades |
| `VACUUM BOOST <table>` | Full vacuum using all resources | During maintenance windows with no concurrent queries |

**Scheduling:**

- Redshift runs automatic VACUUM DELETE in the background — manual VACUUM DELETE is rarely needed
- Automatic VACUUM SORT also runs but may not keep up with heavy write workloads
- Schedule manual VACUUM during low-traffic windows (nights/weekends)
- Monitor `SVV_TABLE_INFO.unsorted` and `SVV_TABLE_INFO.empty` to identify tables needing attention

### 5.2 ANALYZE

- Redshift runs automatic ANALYZE after COPY and INSERT operations
- Run manual ANALYZE after bulk UPDATE or DELETE operations
- Run ANALYZE on specific columns used in WHERE, JOIN, GROUP BY, ORDER BY
- Check `SVV_TABLE_INFO.stats_off` — values > 10% indicate stale statistics
- Stale statistics lead to suboptimal query plans (wrong join order, wrong join type)

### 5.3 Table Maintenance Queries

```sql
-- Tables needing VACUUM (high unsorted or deleted rows)
SELECT database, schema, "table", size, pct_used,
       unsorted, empty, stats_off, skew_rows
FROM svv_table_info
WHERE unsorted > 20 OR empty > 20
ORDER BY unsorted DESC;

-- Tables needing ANALYZE (stale statistics)
SELECT database, schema, "table", stats_off, size
FROM svv_table_info
WHERE stats_off > 10
ORDER BY stats_off DESC;

-- Tables with severe distribution skew
SELECT database, schema, "table", diststyle, skew_rows, size
FROM svv_table_info
WHERE skew_rows > 4.0
ORDER BY skew_rows DESC;
```

---

## 6. Security Patterns

### 6.1 Encryption

| Layer | Method | Notes |
|-------|--------|-------|
| At rest | AWS KMS (default) or CloudHSM | Must be enabled at cluster creation; enabling on existing cluster requires snapshot-restore migration with downtime |
| In transit | SSL/TLS | Set `require_SSL = true` in parameter group; ensure client applications support SSL |
| S3 data (COPY/UNLOAD) | SSE-S3, SSE-KMS, or CSE-CMK | COPY/UNLOAD support encrypted S3 objects transparently |

### 6.2 Authentication & Access Control

- **IAM authentication:** Use the `GetClusterCredentials` API or IAM Identity Center for temporary database credentials instead of static passwords
- **Database roles:** Create roles with specific privileges; assign users to roles via `GRANT role TO user`
- **Schema-level isolation:** Use separate schemas per team/application with appropriate GRANT/REVOKE
- **Column-level security:** `GRANT SELECT (col1, col2) ON table TO role` — restrict access to specific columns
- **Row-level security (RLS):** Create RLS policies to filter rows based on the querying user's attributes
- **Dynamic Data Masking (DDM):** Attach masking policies to columns to redact sensitive data for unauthorized users

### 6.3 Network Security

- Deploy in a VPC with private subnets
- Use security groups to restrict inbound access (port 5439) to specific CIDR ranges or security groups
- Disable public accessibility unless explicitly required
- Enable Enhanced VPC Routing to force COPY/UNLOAD traffic through VPC (via VPC endpoints, NAT gateways)
- Use VPC endpoints for S3 access to avoid public internet routing

### 6.4 Audit Logging

- Enable audit logging to S3 or CloudWatch Logs
- Three log types: connection log, user activity log, user log
- Use `STL_CONNECTION_LOG` for connection forensics
- Use `STL_USERLOG` for DDL changes (CREATE, ALTER, DROP users)
- Use `STL_QUERYTEXT` for full SQL audit trail
- Retain logs per compliance requirements (SOC2, HIPAA, PCI-DSS)

---

## 7. Redshift Spectrum

- Query data directly in S3 without loading into Redshift tables
- Use columnar formats (Parquet, ORC) for best performance — supports predicate pushdown and column pruning
- Partition external tables by date, region, or other high-cardinality dimensions to enable partition pruning
- Use large files (128 MB – 512 MB) to minimize S3 request overhead
- Push filters to the Spectrum layer by placing WHERE clauses on external table columns
- Use Spectrum for cold/archival data; keep hot data in local Redshift tables
- Create external schemas in AWS Glue Data Catalog or Hive metastore

---

## 8. Data Sharing

- Share live, transactionally consistent data between Redshift clusters without copying
- Producer cluster creates a datashare and adds schemas/tables; consumer cluster creates a database from the datashare
- Consumer queries do not impact producer performance (workload isolation)
- Supported for RA3 provisioned clusters and Serverless workgroups
- Cross-account sharing uses AWS Resource Access Manager (RAM)
- Cross-region data sharing is supported
- Use for multi-tenant architectures: one producer, multiple consumer clusters/workgroups per tenant

---

## 9. Cost Optimization

| Strategy | Savings Potential | Applicability |
|----------|-------------------|---------------|
| Reserved Instances (1yr/3yr) | Up to 75% | Steady-state provisioned workloads |
| Pause/Resume scheduling | Up to 70% (off-hours) | Dev/test/staging clusters |
| Right-sizing (fewer/smaller nodes) | 20-50% | Over-provisioned clusters (CPU < 40% sustained) |
| Concurrency Scaling free credits | 1 hr/day/cluster free | Burst workloads timed to use free credits |
| Compression encoding | 3-4x storage reduction | All tables — reduces managed storage costs |
| Spectrum for cold data | Variable | Archive historical data to S3, query via Spectrum |
| Serverless for intermittent workloads | Variable | Unpredictable or bursty query patterns |
| RPU-hour usage limits (Serverless) | Cost cap | Prevent runaway Serverless costs |

---

## 10. Cluster Sizing Reference

### Provisioned Node Types

| Node Type | Memory (GB) | Storage | Best For |
|-----------|-------------|---------|----------|
| dc2.large | 15 | 160 GB (SSD) | Small workloads, dev/test |
| dc2.8xlarge | 244 | 2.56 TB (SSD) | Legacy high-performance, data fits in SSD |
| ra3.large | 16 | Managed storage (Amazon S3) | Entry-level RA3; separates compute and storage |
| ra3.xlplus | 32 | Managed storage (Amazon S3) | Mid-size RA3 |
| ra3.4xlarge | 96 | Managed storage (Amazon S3) | Standard production RA3 |
| ra3.16xlarge | 384 | Managed storage (Amazon S3) | Large-scale production RA3 |

RA3 node types use Redshift Managed Storage (RMS), which scales storage independently of compute and is billed separately per GB — there is no fixed per-node storage cap to plan around, unlike DC2's local SSD.

### Resize Operations

| Operation | Duration | Downtime | Node Type Change | Node Count Change | Notes |
|-----------|----------|----------|------------------|-------------------|-------|
| Elastic Resize | Minutes | Brief (seconds) | Yes | Yes | Recommended for most resize operations; subject to elastic resize range limits |
| Classic Resize | Hours | Extended (new cluster provisioned, data redistributed in background) | Yes | Yes | Not subject to elastic resize range limits; can also apply cluster encryption |
| Snapshot + Restore | Minutes to hours | New cluster | Yes | Yes (can also restore to a Serverless namespace) | Useful for migrating to a different configuration, node type, or region |

### Serverless

- No node selection; specify base RPU (min 8) and max RPU
- Auto-scales within the RPU range based on workload demand
- Pay-per-use: RPU-hours billed per second
- Set max RPU and RPU-hour usage limits to control costs

---

## 11. Operations Best Practices

Source: [Amazon Redshift management documentation](https://docs.aws.amazon.com/redshift/latest/mgmt/). Content was rephrased for compliance with licensing restrictions.

### 11.1 Automatic Maintenance Operations (ML-Based)

Redshift provides ML-based automatic optimizations that handle most operational tasks without DBA intervention:

| Auto Feature | What It Does | Behavior |
|--------------|-------------|----------|
| Automatic Table Optimization | Continuously scans workload patterns and adjusts sort keys, distribution style, and encoding | Applied during low-compute periods; can be enabled/disabled per table/column |
| Auto-Analyze | Collects table statistics in the background | Runs every hour during light workloads; processes tables in windows of 100M rows; skips tables with up-to-date statistics |
| Auto-Vacuum Delete | Reclaims disk space from deleted/updated rows | Priority-based and incremental; starts with most frequently used tables; runs concurrently across different tables |
| Auto-Vacuum Sort (Auto Table Sort) | Restores sorted state of data after ingestion | Lessens the need to run manual VACUUM SORT |
| Auto-Materialized View Refresh | Refreshes MVs most likely to be used next | Prioritizes based on predicted usage |
| Auto Workload Manager | Dynamically manages memory and concurrency | Assigns priorities instead of manual slot counts |

Auto-Analyze and Auto-Vacuum both delay start if user queries are running (or have been running for more than 15 seconds in the last 300 seconds), and automatically pause if cluster workloads spike.

### 11.2 ANALYZE Best Practices (Advanced)

- For the majority of workloads, AUTO ANALYZE will collect statistics — no manual intervention needed.
- Run manual ANALYZE after: modifying/loading a large number of rows (> 5% of existing rows), changing distribution or sort keys, adding or removing foreign keys, or after a VACUUM operation.
- ANALYZE the columns that are frequently used in: sorting and grouping operations, joins, and query predicates.
- ANALYZE will skip tables that already have up-to-date statistics or where the extent of changes is small.
- ANALYZE will skip tables that are greater than 97% sorted — if ANALYZE completes faster than expected, this is likely the reason.
- External tables (Redshift Spectrum tables) cannot be analyzed. Use Glue crawler to update numRows for external tables, which helps query planning performance. Glue Data Catalog column statistics can also be leveraged.
- Use Redshift Advisor to identify stale table statistics, or use the `missing_table_stats.sql` query from the [amazon-redshift-utils](https://github.com/awslabs/amazon-redshift-utils) GitHub repository.

### 11.3 VACUUM / Table Defragmentation Best Practices (Advanced)

Redshift uses append-only data storage — data blocks in column-storage files are never modified, new blocks are added to the end. Deleted/updated rows represent wasted storage, cause fragmentation, and can cause columns to lose their sorted state.

- For the majority of workloads, AUTO VACUUM DELETE will reclaim space and AUTO TABLE SORT will sort the needed portions of the table.
- Run manual VACUUM SORT/DELETE after modifying/deleting a large number of rows (> 5% of existing rows).
- VACUUM SORT/DELETE is NOT required after DROP or TRUNCATE tables.
- Only VACUUM SORT/DELETE tables that have a large number of unsorted/deleted rows and `vacuum_sort_benefit > 30` for VACUUM SORT.
- VACUUM is an expensive operation — perform during off-peak hours.
- Use `VACUUM SORT` for re-establishing sort order (instead of VACUUM FULL).
- Use `VACUUM DELETE` for defragmenting/reclaiming space (instead of VACUUM FULL).
- Use `VACUUM RECLUSTER` on large tables for more efficient re-sorting.
- Redshift will automatically run VACUUM SORT to reestablish sorted state after COPY/UPDATE/DELETE operations.
- Use a time-series table approach to avoid large, recurring merge operations that could result in fragmented or unsorted tables. Multiple tables with identical schemas each hold data for a specific date range (daily, weekly, monthly).
- Track Auto-Vacuum progress by monitoring "Space reclaimed by auto vacuum delete" on the Cluster Performance tab and the CloudWatch metric `AutoVacuumSpaceFreed`.
- Rely on Redshift Advisor to identify fragmented/unsorted tables.
- Support for running multiple vacuum commands concurrently across different tables.

### 11.4 Monitoring Best Practices

Two categories of performance data are displayed in the Redshift console:

| Category | What It Monitors |
|----------|-----------------|
| Amazon CloudWatch Metrics | Physical performance: CPU utilization, latency, throughput, disk usage, I/O rates, network throughput, cluster health |
| Query/Load Performance Data | Database-level activity: query execution times (plan, wait, read, write stages), data load performance |

Monitoring recommendations:

- Use the Redshift console for summary CloudWatch metrics, cluster performance, database performance, and workload concurrency.
- Use detailed query monitoring to see how long queries spent in plan, wait, read, and write stages.
- Use the Query Profiler to introspect queries and review execution plans to discover performance bottlenecks.
- Use Query Hash to track query performance over time and identify recurring patterns in resource-intensive queries.
- Subscribe to Amazon SNS for Redshift event notifications (Configuration, Management, Pending, Monitoring, Security events).
- Consider the Amazon Redshift Grafana plugin for visualizing operational metrics and business insights directly from Grafana dashboards. A default operational dashboard is available out of the box. Query System Views directly for in-depth metrics beyond CloudWatch.

### 11.5 Backup & Restore Best Practices

**Provisioned Cluster Snapshots:**

| Aspect | Automated Snapshots | Manual Snapshots |
|--------|-------------------|-----------------|
| Frequency & Retention | Default every 8 hours or every 5GB of block changes (whichever first); configurable with cron-style granularity; up to 35 days retention | User-defined retention period |
| Cross-Region Replication | Can be replicated automatically (transfer charges apply); automated cross-region snapshots are free | Can be replicated automatically (transfer charges apply) |
| Cost | Free regardless of frequency, retention, or location | Storage fees at S3 rates + transfer charges for cross-region |
| Cluster Termination | Deleted upon termination (both in-region and cross-region) | Retained according to specified period |
| Extended Retention | Can be converted to manual snapshots if retention beyond 35 days is needed | No conversion needed |

- Snapshots are incremental and typically complete within minutes.
- Snapshot restoration results in a new cluster (original or different size/instance type).
- During restore, cluster is provisioned in ~10 minutes and available for reads/writes ~20 minutes after restoration begins. Data continues restoring in the background.
- Data for in-flight queries is prioritized and restored first.
- Single-table restore is supported — select snapshot, source database, schema, and table; restore to same cluster with a new table name.
- Multi-table restore is available via the [amazon-redshift-utils](https://github.com/awslabs/amazon-redshift-utils) MultipleTableRestoreUtility.
- AWS Backup integration provides centralized data protection, automated backup scheduling/retention via backup plans, and point-in-time restore for entire clusters or individual tables.

**Serverless Recovery Points & Snapshots:**

- Recovery points are automatically created every 30 minutes and saved for 24 hours (no storage charge).
- Recovery points can be restored to a serverless namespace or converted into a snapshot.
- Serverless snapshots have the same functionality as provisioned manual snapshots and can be restored to a serverless namespace or a provisioned cluster.
- Single-table restore to a namespace is supported.

**Backup/Recovery Strategy Design:**

- Define Recovery Time Objective (RTO): how long to recover a cluster, database, or table.
- Define Recovery Point Objective (RPO): how recent must the restored data be. RPO is reliant on snapshot schedules.
- Redshift does not have redo logs like transactional databases — after restoring from a snapshot, source files that were loaded after the snapshot was taken must be reloaded.
- RTO is reliant upon database size and node-count throughput.

### 11.6 Resize & Scaling Best Practices

**Provisioned Clusters:**

- Use Elastic Resize for most resize operations — completes in minutes, allows both node type and node count changes.
- Use Classic Resize when elastic resize range limits don't accommodate the target configuration, or when applying cluster encryption. Classic resize creates a new target cluster and migrates data; the new cluster is available for reads/writes within minutes while data redistribution continues in the background.
- Use Scheduled Elastic Resize to automate resizing based on predictable workload patterns (e.g., scale up during business hours, scale down overnight). Configurable via console, CLI, or API.
- Snapshot + Restore can also be used for resize and supports restoring to a serverless namespace.

**Serverless:**

- Adjust base RPU capacity based on workload performance requirements.
- The system applies workload management automatically to ensure maximum throughput from the base compute.
- Set daily/weekly/monthly RPU-hour usage limits to put thresholds on costs.
- Set query execution timeout to safeguard from runaway queries.
- AI-driven scaling provides intelligent auto-scaling for dynamic workloads with ML-driven optimizations.
- Adjust price-performance targets along a spectrum from "Optimizes for cost" to "Balanced" to "Optimizes for performance."

### 11.7 Redshift Advisor Recommendations

Redshift Advisor analyzes performance and usage metrics and provides prioritized recommendations ranked by impact:

- Compress S3 file objects loaded by COPY
- Isolate multiple active databases
- Reallocate workload management (WLM) memory
- Skip compression analysis during COPY
- Split Amazon S3 objects loaded by COPY
- Update table statistics
- Enable short query acceleration
- Alter distribution keys on tables
- Alter sort keys on tables
- Alter compression encodings on columns
- Data type recommendations
