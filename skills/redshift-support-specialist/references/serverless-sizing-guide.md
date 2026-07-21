# Redshift Serverless Sizing — Usage Guide

Reference for provisioned-to-serverless migration cost analysis. Based on the Redshift Serverless Sizing methodology.

Source: [Amazon Redshift Serverless documentation](https://docs.aws.amazon.com/redshift/latest/mgmt/serverless-whatis.html), [Redshift Serverless billing](https://docs.aws.amazon.com/redshift/latest/mgmt/serverless-billing.html). Content was rephrased for compliance with licensing restrictions.

---

## Overview

Serverless sizing is a two-step approach:

1. **Q1 — Workload Categorization:** Scans existing production workload and categorizes queries into size types based on data scanned per query step.
2. **Q2 — Cost Estimation:** Estimates serverless costs based on the dominant workload and existing provisioned cluster configuration. Factors in the 1-minute minimum charge and provides daily usage minutes and percentage estimates.

---

## Workload Size Types (RPU Mapping)

Queries are classified by the maximum bytes scanned in any single segment:

| Size Type | Max Scan Bytes | Recommended RPU |
|-----------|---------------|-----------------|
| xx-small | < 1 GB | 8 RPU |
| x-small | < 10 GB | 32 RPU |
| small | < 100 GB | 64 RPU |
| medium | < 500 GB | 128 RPU |
| large | < 1 TB | 256 RPU |
| x-large | < 3 TB | 512 RPU |
| xx-large | > 3 TB | 1024 RPU |

The **dominant workload** is the size_type with the highest weightage (sum of execution time = query_count × avg_exec_sec).

---

## Provisioned Node Type Reference (Pricing)

Default on-demand and reserved instance hourly prices. Update Q2 with pricing for the target region before running it.

| Node Type | Memory (GB) | On-Demand ($/hr) | 1yr PURI ($/hr) | 3yr PURI ($/hr) |
|-----------|-------------|-------------------|-----------------|-----------------|
| dc2.large | 15 | 0.25 | 0.161 | 0.10 |
| dc2.8xlarge | 244 | 4.80 | 3.22 | 1.607 |
| ra3.large | 16 | 0.543 | 0.364 | 0.214 |
| ra3.xlplus | 32 | 1.086 | 0.728 | 0.429 |
| ra3.4xlarge | 96 | 3.26 | 2.184 | 1.288 |
| ra3.16xlarge | 384 | 13.04 | 8.737 | 5.151 |

RPU-like equivalent for provisioned: `nodes × memory_gb / 16`

---

## RPU Sizing Logic

The recommended base RPU is determined by:

1. Calculate `current_rpu_like = nodes × memory / 16` (provisioned equivalent)
2. Determine dominant workload RPU from Q1
3. If dominant workload RPU > current_rpu_like × 1.2 → use dominant workload RPU
4. Otherwise → use `round((current_rpu_like × 1.2 + 4) / 8) × 8` (20% buffer, rounded to nearest available RPU)

---

## Guideline Steps

1. Update Q2's serverless price (line 125) and provisioned prices (lines 36-41) for the target region before running it
2. Run both Q1 and Q2 as superuser on the existing provisioned cluster
3. Review Q1 output: the size_type with maximum weightage is the dominant workload
4. Review Q2 output: recommended_base_rpu is based on dominant workload + cluster config
5. Present both outputs and evaluate options together with the account owner

### Override Scenarios

If a different size_type should be used instead of the dominant workload:

1. In Q2, find the line with comment `-- Override default recommendation if necessary`
2. Change `serverless_RPU` to the desired RPU value (e.g., `32 as serverless_RPU`)
3. Rerun modified Q2 as superuser

### Alerts to Evaluate

| Condition | Alert |
|-----------|-------|
| overridden_rpu < recommended_base_rpu | Potential performance degradation for large queries |
| overridden_rpu > recommended_base_rpu | Potential higher costs than necessary |
| Difference > ±32 RPUs | Strongly recommend a proof-of-concept before committing |

### Decision Factors Beyond Cost

Not every migration decision is purely cost-driven. Consider:

- Ease of use priorities
- Level of control/customization needed
- Integration requirements with existing tools
- Workload predictability (bursty vs steady-state)

---

## Q1 — Workload Categorization Query

**Filename:** `ServerlessSizing_Q1.csv`

Must be run as superuser on the existing provisioned cluster.

```sql
WITH scan_sum AS (
  SELECT query_id,
         segment_id,
         SUM(output_bytes) AS bytes,
         ROUND(datediff(ms, MIN(start_time), MAX(end_time)) / 1000.0, 3) AS seg_sec
  FROM sys_query_detail
  WHERE user_id > 1
    AND step_name = 'scan'
    AND start_time > '2000-01-01 00:00:00'
    AND end_time > '2000-01-01 00:00:00'
  GROUP BY query_id, segment_id
),
scan_list AS (
  SELECT query_id,
         MAX(bytes) AS max_scan_bytes,
         MAX(CASE WHEN seg_sec > 0 THEN seg_sec ELSE 0 END) AS seg_sec_max
  FROM scan_sum
  GROUP BY query_id
),
query_list AS (
  SELECT w.query_id,
         start_time,
         end_time,
         ROUND(execution_time / 1000 / 1000.0, 3) AS exec_sec,
         max_scan_bytes,
         seg_sec_max,
         CASE
           WHEN max_scan_bytes <     1000000000 THEN 'xx-small-8RPU'
           WHEN max_scan_bytes <    10000000000 THEN 'x-small-32RPU'
           WHEN max_scan_bytes <   100000000000 THEN 'small-64RPU'
           WHEN max_scan_bytes <   500000000000 THEN 'medium-128RPU'
           WHEN max_scan_bytes <  1000000000000 THEN 'large-256RPU'
           WHEN max_scan_bytes <  3000000000000 THEN 'x-large-512RPU'
           WHEN max_scan_bytes >  3000000000000 THEN 'xx-large-1024RPU'
           ELSE 'N/A'
         END AS size_type
  FROM sys_query_history w,
       scan_list sc
  WHERE sc.query_id = w.query_id
)
SELECT size_type,
       COUNT(*) AS query_cnt,
       AVG(exec_sec) AS exec_sec_avg,
       MAX(exec_sec) AS exec_sec_max,
       MIN(exec_sec) AS exec_sec_min,
       AVG(seg_sec_max) AS seg_sec_max_avg,
       AVG(max_scan_bytes) AS max_scan_bytes_avg,
       COUNT(*) * AVG(exec_sec) AS weightage
FROM query_list
GROUP BY 1
ORDER BY max_scan_bytes_avg;
```

### Q1 Example Output

| size_type | query_cnt | exec_sec_avg | exec_sec_max | exec_sec_min | seg_sec_max_avg | max_scan_bytes_avg | weightage |
|-----------|-----------|-------------|-------------|-------------|----------------|-------------------|-----------|
| xx-small-8RPU | 22 | 1.8 | 7.83 | 0 | 0.61 | 6,850,635 | 39.62 |
| medium-128RPU | 71 | 126.96 | 1381.88 | 10.81 | 488.51 | 79,607,948,918 | 7888.7 |

### Q1 Output Metadata

| Column | Description |
|--------|-------------|
| size_type | Workload type based on data scanned and corresponding RPU recommendation |
| query_cnt | Number of queries in this size category |
| exec_sec_avg | Average execution seconds for this size type |
| exec_sec_max | Maximum execution seconds for this size type |
| exec_sec_min | Minimum execution seconds for this size type |
| seg_sec_max_avg | Average of the maximum segment execution seconds |
| max_scan_bytes_avg | Average maximum bytes scanned per query |
| weightage | Total execution time weight (query_cnt × exec_sec_avg). The size_type with the highest weightage is the dominant workload |

### Q1 Analysis Guide

When analyzing Q1 results:

1. Identify the dominant workload: the row with the highest `weightage` value
2. Note the query distribution: are most queries small but a few large ones dominate execution time?
3. Check for outliers: if `exec_sec_max` is significantly higher than `exec_sec_avg`, there may be runaway queries
4. The dominant workload drives the RPU recommendation — if it's medium-128RPU, the base RPU will be at least 128

---

## Q2 — Cost Estimation Query

**Filename:** `ServerlessSizing_Q2.csv`

Must be run as superuser on the existing provisioned cluster. Before running, update:

- Line 125: serverless price per RPU-hour for the target region (default: $0.375 for us-east-1)
- Lines 36-41: provisioned cluster on-demand and reserved pricing for the target region

```sql
WITH recursive numbers(rown) AS (
  SELECT 1
  UNION ALL
  SELECT rown + 1 FROM numbers WHERE rown < 10080
),
epochval AS (
  SELECT extract(epoch FROM date_trunc('day', current_timestamp)) - rown * 60 ep
  FROM numbers
),
t_minute AS (
  SELECT TIMESTAMP 'epoch' + (cast(ep AS bigint)) * INTERVAL '1 second' minutes_week
  FROM epochval
  ORDER BY minutes_week
),
my_cluster AS (
  SELECT type AS node_type, count(DISTINCT node) AS nodes
  FROM (
    SELECT CASE
      WHEN capacity = 760956 THEN 'dc2.8xlarge'
      WHEN capacity = 190633 THEN 'dc2.large'
      WHEN capacity = 952455 THEN 'ds2.xlarge'
      WHEN capacity = 945026 THEN 'ds2.8xlarge'
      WHEN capacity = 869530 OR capacity = 924825 THEN 'ra3.large'
      WHEN capacity = 954367 OR capacity = 2002943 THEN 'ra3.xlplus'
      WHEN (capacity = 6772561 OR capacity = 3339176) AND count(1) OVER (PARTITION BY host) = 1 THEN 'ra3.4xlarge'
      WHEN (capacity = 6772561 OR capacity = 3339176) AND count(1) OVER (PARTITION BY host) != 1 THEN 'ra3.16xlarge'
    END AS TYPE,
    OWNER node
    FROM stv_partitions WHERE OWNER = host
  )
  GROUP BY TYPE
),
node_types AS (
  SELECT * FROM
    (SELECT 'dc2.large' AS node_type, 15 AS memory, 0.25 AS OD_cost, 0.161 AS puri1y, 0.1 AS puri3y)
  UNION ALL (SELECT 'dc2.8xlarge', 244, 4.8, 3.22, 1.607)
  UNION ALL (SELECT 'ra3.large', 16, 0.543, 0.364, 0.214)
  UNION ALL (SELECT 'ra3.xlplus', 32, 1.086, 0.728, 0.429)
  UNION ALL (SELECT 'ra3.4xlarge', 96, 3.26, 2.184, 1.288)
  UNION ALL (SELECT 'ra3.16xlarge', 384, 13.04, 8.737, 5.151)
),
scan_sum AS (
  SELECT query_id, segment_id,
         SUM(output_bytes) AS bytes,
         ROUND(datediff(ms, MIN(start_time), MAX(end_time)) / 1000.0, 3) AS seg_sec
  FROM sys_query_detail
  WHERE user_id > 1
    AND step_name = 'scan'
    AND start_time > '2000-01-01 00:00:00'
    AND end_time > '2000-01-01 00:00:00'
  GROUP BY query_id, segment_id
),
scan_list AS (
  SELECT query_id,
         MAX(bytes) AS max_scan_bytes,
         MAX(CASE WHEN seg_sec > 0 THEN seg_sec ELSE 0 END) AS seg_sec_max
  FROM scan_sum
  GROUP BY query_id
),
query_list AS (
  SELECT max_scan_bytes,
         ROUND(execution_time / 1000 / 1000.0, 3) AS exec_sec,
         CASE
           WHEN max_scan_bytes <     1000000000 THEN '8'
           WHEN max_scan_bytes <    10000000000 THEN '32'
           WHEN max_scan_bytes <   100000000000 THEN '64'
           WHEN max_scan_bytes <   500000000000 THEN '128'
           WHEN max_scan_bytes <  1000000000000 THEN '256'
           WHEN max_scan_bytes <  3000000000000 THEN '512'
           WHEN max_scan_bytes >  3000000000000 THEN '1024'
           ELSE 'N/A'
         END AS recommended_RPU_by_workload
  FROM sys_query_history w, scan_list sc
  WHERE sc.query_id = w.query_id
),
workload_weightage AS (
  SELECT recommended_RPU_by_workload,
         COUNT(*) * AVG(exec_sec) weightage
  FROM query_list
  GROUP BY 1
),
dominant_workload AS (
  SELECT recommended_RPU_by_workload
  FROM workload_weightage
  WHERE weightage = (SELECT max(weightage) FROM workload_weightage)
),
my_serverless_cluster AS (
  SELECT m.node_type, m.nodes,
         od_cost * nodes AS current_od_cost,
         puri1y * nodes AS current_puri1y_cost,
         puri3y * nodes AS current_puri3y_cost,
         nodes * memory / 16 AS current_rpu_like,
         CASE
           WHEN (SELECT cast(recommended_RPU_by_workload AS int) FROM dominant_workload) > current_rpu_like * 1.2
             THEN (SELECT cast(recommended_RPU_by_workload AS int) FROM dominant_workload)
           ELSE round((current_rpu_like * 1.2 + 4) / 8, 0) * 8
         END AS serverless_RPU  -- Override default recommendation if necessary
  FROM my_cluster m
  INNER JOIN node_types i ON m.node_type = i.node_type
)
SELECT DISTINCT trunc(minutes_week) data_day,
       fd.node_type,
       fd.nodes AS node_count,
       fd.daily_on_demand,
       fd.recommended_base_RPU,
       fd.estimated_serverless_minutes_per_day,
       fd.estimated_serverless_usage_percentage,
       fd.estimated_serverless_daily_cost
FROM t_minute
LEFT JOIN (
  SELECT trunc(minutes_week) _day,
         node_type, nodes, current_rpu_like,
         round(current_od_cost * 24, 2) AS daily_on_demand,
         round(current_puri1y_cost * 24, 2) AS daily_1y_PURI,
         serverless_RPU AS recommended_base_RPU,
         ceiling(1.0 * count(DISTINCT minutes_week)) AS estimated_serverless_minutes_per_day,
         concat(round(100.0 * count(DISTINCT minutes_week) / 1440, 2), '%') AS estimated_serverless_usage_percentage,
         -- Update price below as per your region (default: $0.375/RPU-hour for us-east-1)
         round(0.375 * 24 * serverless_RPU * count(DISTINCT minutes_week) / 1440, 2) AS estimated_serverless_daily_cost
  FROM (
    SELECT start_time, end_time, current_rpu_like, current_od_cost,
           current_puri1y_cost,
           serverless_RPU,  -- Override default recommendation if necessary
           node_type, nodes
    FROM sys_query_history t
    INNER JOIN my_serverless_cluster msc ON 1 = 1
    WHERE user_id <> 1
      AND start_time BETWEEN (SELECT dateadd(day, -7, DATE_TRUNC('day', GETDATE())))
                         AND (SELECT DATE_TRUNC('day', GETDATE()))
    ORDER BY start_time
  ) serverless_expected_duration
  INNER JOIN t_minute tm
    ON minutes_week BETWEEN Date_trunc('minute', start_time)
       AND dateadd('ms', Datediff('ms', start_time, end_time)
           * cast(1.0 * current_rpu_like / serverless_RPU * 1000 AS int) / 1000, start_time)
  GROUP BY node_type, trunc(minutes_week), nodes, current_rpu_like,
           serverless_RPU, daily_on_demand, daily_1y_PURI
) fd ON trunc(minutes_week) = _day
ORDER BY data_day;
```

### Q2 Example Output

| data_day | node_type | node_count | daily_on_demand_cost | recommended_base_rpu | estimated_serverless_minutes_per_day | estimated_serverless_usage_percentage | estimated_serverless_daily_cost |
|----------|-----------|------------|---------------------|---------------------|-------------------------------------|--------------------------------------|-------------------------------|
| 2/8/2024 | ra3.4xlarge | 2 | 156.48 | 128 | 157 | 10.90% | 7.85 |
| 2/9/2024 | ra3.4xlarge | 2 | 156.48 | 128 | 245 | 17.01% | 12.25 |
| 2/10/2024 | ra3.4xlarge | 2 | 156.48 | 128 | 49 | 3.40% | 2.45 |
| 2/11/2024 | ra3.4xlarge | 2 | 156.48 | 128 | 40 | 2.78% | 2 |
| 2/12/2024 | ra3.4xlarge | 2 | 156.48 | 128 | 163 | 11.32% | 8.15 |
| 2/13/2024 | ra3.4xlarge | 2 | 156.48 | 128 | 49 | 3.40% | 2.45 |
| 2/14/2024 | ra3.4xlarge | 2 | 156.48 | 128 | 18 | 1.25% | 14.4 |

### Q2 Output Metadata

| Column | Description |
|--------|-------------|
| data_day | Date of the assessed workload |
| node_type | Existing provisioned cluster node type |
| node_count | Existing provisioned cluster node count |
| daily_on_demand_cost | Current provisioned cluster daily cost at on-demand pricing |
| recommended_base_rpu | Recommended serverless base RPU based on dominant workload |
| estimated_serverless_minutes_per_day | Estimated active compute minutes per day at recommended RPU |
| estimated_serverless_usage_percentage | Estimated percentage of the day with active compute |
| estimated_serverless_daily_cost | Estimated daily serverless cost at recommended RPU |

### Q2 Analysis Guide

When analyzing Q2 results:

1. **Cost comparison:** Compare `daily_on_demand_cost` (provisioned) vs `estimated_serverless_daily_cost` (serverless) for each day. If serverless is consistently lower, it's a strong migration candidate.
2. **Usage pattern:** Look at `estimated_serverless_usage_percentage`. Low percentages (< 30%) indicate intermittent workloads that benefit most from serverless pay-per-use.
3. **Day-to-day variance:** High variance in daily costs suggests bursty workloads — ideal for serverless. Steady high usage may favor provisioned with reserved instances.
4. **Weekly patterns:** Weekends often show lower usage — factor this into monthly cost projections.
5. **Savings calculation:** `(daily_on_demand_cost - estimated_serverless_daily_cost) × 30` gives approximate monthly savings. Compare against reserved instance pricing too.

### Key Insights to Capture

When presenting results, highlight:

- **Dominant workload type** from Q1 and what it means for RPU sizing
- **Daily cost savings** (provisioned on-demand vs serverless estimated)
- **Monthly projected savings** extrapolated from the 7-day sample
- **Usage efficiency** — what percentage of the day is the cluster actually computing?
- **Peak vs off-peak patterns** — are there opportunities for different RPU settings?
- **Reserved instance comparison** — if reserved instances are already in place, compare RI cost vs serverless
- **Risk factors** — performance implications if RPU is undersized, cost implications if oversized

---

## Optional Filters

The queries support filtering by user_id or service_class_id for targeted analysis:

- **User ID filter:** Replace `user_id > 1` with `user_id in (<<Input user IDs>>)` in both Q1 and Q2
- **Service class filter:** Add `AND service_class_id in (<<Input serviceIDs>>)` to the sys_query_history WHERE clause in both Q1 and Q2

This is useful when evaluating serverless migration for specific workloads (e.g., only ETL, only reporting).
