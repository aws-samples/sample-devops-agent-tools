# Amazon Redshift Health Assessment Checklist

Structured health assessment checklist for Amazon Redshift provisioned clusters and Serverless workgroups. Each check includes the AWS CLI mapping, evaluation criteria, and recommended actions.

Sources: [Amazon Redshift documentation](https://docs.aws.amazon.com/redshift/latest/mgmt/metrics.html), [CloudWatch metrics reference](https://docs.aws.amazon.com/redshift/latest/mgmt/metrics-listing.html), [Redshift Advisor](https://docs.aws.amazon.com/redshift/latest/dg/advisor-recommendations.html). Content was rephrased for compliance with licensing restrictions.

---

## Available AWS CLIs for Health Checks

All API calls are made via `AWS CLI`. The following APIs are available for cluster-level health assessments:

### Redshift APIs

| API | Purpose | Key Response Fields |
|-----|---------|---------------------|
| `aws redshift describe-clusters` | Cluster inventory, status, configuration, encryption, VPC routing, version, snapshots, parameter groups | `ClusterStatus`, `Encrypted`, `EnhancedVpcRouting`, `PubliclyAccessible`, `ClusterVersion`, `AutomatedSnapshotRetentionPeriod`, `MultiAZ`, `NodeType`, `NumberOfNodes` |
| `aws redshift describe-logging-status` | Audit logging configuration | `LoggingEnabled`, `BucketName`, `S3KeyPrefix`, `LogDestinationType`, `LogExports` |
| `aws redshift describe-cluster-parameters` | Active parameter values for the cluster's parameter group | `ParameterName`, `ParameterValue`, `Description`, `Source`, `IsModifiable` (includes `require_ssl`, `enable_user_activity_logging`, `wlm_json_configuration`, `max_concurrency_scaling_clusters`, etc.) |
| `aws redshift describe-default-cluster-parameters` | Default parameter values for baseline comparison | Same structure as `describe-cluster-parameters` — compare against active values to identify parameters that were changed from their defaults |
| `aws redshift describe-cluster-subnet-groups` | Network and subnet configuration | `ClusterSubnetGroupName`, `VpcId`, `Subnets[].SubnetIdentifier`, `Subnets[].SubnetAvailabilityZone` |
| `aws redshift describe-cluster-snapshots` | Snapshot inventory, retention, and status | `SnapshotIdentifier`, `SnapshotType` (automated/manual), `SnapshotCreateTime`, `Status`, `TotalBackupSizeInMegaBytes`, `EncryptedWithHSM` |

### CloudWatch APIs

| API | Purpose | Key Response Fields |
|-----|---------|---------------------|
| `aws cloudwatch list-metrics` | Discover available metrics for a cluster (confirms active dimensions and namespaces) | `MetricName`, `Namespace`, `Dimensions` (ClusterIdentifier, NodeID) |

---

## Pre-Assessment: Account Context

| # | Check | AWS CLI / Tool | Expected Result | Action if Failed |
|---|-------|---------------|-----------------|------------------|
| 0.1 | Identify target account | AWS CLI (`aws sts get-caller-identity`) | Account ID and calling identity resolved | Verify account ID and credentials |
| 0.2 | Check active AWS Health events | AWS Health Dashboard / `aws health describe-events` | No active events, or list of events | Note any active events that may explain findings |

---

## Phase 1: Cluster Discovery & Configuration

| # | Check | AWS CLI Call | Expected Result | Action if Failed |
|---|-------|-------------|-----------------|------------------|
| 1.1 | List all provisioned clusters | `aws redshift describe-clusters` | Cluster list with status, node type, node count, version | Note any clusters in non-"available" state |
| 1.2 | Check cluster version / patch level | From `aws redshift describe-clusters` → `ClusterVersion`, `ClusterRevisionNumber` | Current or recent version | Flag clusters on outdated versions; recommend maintenance window |
| 1.3 | Check Multi-AZ status | From `aws redshift describe-clusters` → `MultiAZ` field | Enabled for production workloads | Recommend Multi-AZ for critical production clusters |
| 1.4 | Check encryption status | From `aws redshift describe-clusters` → `Encrypted` field | `true` | ❌ FAIL if false — recommend enabling encryption (requires snapshot-restore) |
| 1.5 | Check subnet group / network config | `aws redshift describe-cluster-subnet-groups` | Cluster in private subnets within expected VPC | Flag clusters in public subnets or unexpected VPCs |
| 1.6 | Check snapshot inventory | `aws redshift describe-cluster-snapshots` | Automated snapshots present with appropriate retention | ❌ FAIL if no recent snapshots; verify retention period |

---

## Phase 2: CloudWatch Metrics — Provisioned Clusters

Namespace: `AWS/Redshift`. Dimension: `ClusterIdentifier` (cluster-level) or `ClusterIdentifier` + `NodeID` (node-level).

**Available APIs:**

- `aws cloudwatch list-metrics` — Discover which metrics are being emitted for the cluster (confirms active metric dimensions and namespaces)

**Evaluation approach:** Use `aws cloudwatch list-metrics` to confirm which metrics are available for the cluster. For metric values, review the Redshift console or CloudWatch console dashboards.

**Period:** 300 seconds (5 min). **Lookback:** 24 hours for current state, 7 days for trends.

### 2.1 Compute & Resource Metrics

| # | Metric | Statistic | 🟢 PASS | ⚠️ WARN | ❌ FAIL | Action |
|---|--------|-----------|---------|---------|---------|--------|
| 2.1.1 | CPUUtilization | Average, Maximum | < 60% avg | 60-80% avg or > 90% max | > 80% avg sustained or > 95% max | Identify CPU-heavy queries via STL_QUERY; consider resize or query optimization |
| 2.1.2 | PercentageDiskSpaceUsed | Average | < 60% | 60-75% | > 75% | Run VACUUM DELETE; archive old data to S3; consider resize |
| 2.1.3 | DatabaseConnections | Maximum | < 300 | 300-400 | > 400 (approaching 500 limit) | Implement connection pooling; check for leaked connections |
| 2.1.4 | HealthStatus | Minimum | = 1 (healthy) | — | < 1 (unhealthy) | Investigate immediately — check cluster events, recent changes, resource exhaustion |
| 2.1.5 | MaintenanceMode | Maximum | = 0 | = 1 (maintenance active) | — | Note that maintenance is active; check maintenance window schedule |

### 2.2 I/O Metrics (Per Node)

| # | Metric | Statistic | 🟢 PASS | ⚠️ WARN | ❌ FAIL | Action |
|---|--------|-----------|---------|---------|---------|--------|
| 2.2.1 | ReadIOPS | Average | Baseline ± 20% | > 2x baseline sustained | > 5x baseline sustained | Check for full table scans; verify sort keys and zone-map pruning |
| 2.2.2 | WriteIOPS | Average | Baseline ± 20% | > 2x baseline sustained | > 5x baseline sustained | Check for excessive COPY/INSERT/UPDATE; review commit frequency |
| 2.2.3 | ReadLatency | Average | < 5ms | 5-10ms | > 10ms sustained | Check disk health; verify no disk-full conditions |
| 2.2.4 | WriteLatency | Average | < 5ms | 5-10ms | > 10ms sustained | Check commit queue; reduce write concurrency |
| 2.2.5 | ReadThroughput | Average | Baseline ± 30% | Significant deviation | — | Correlate with query patterns |
| 2.2.6 | WriteThroughput | Average | Baseline ± 30% | Significant deviation | — | Correlate with ingestion patterns |

### 2.3 Network Metrics (Per Node)

| # | Metric | Statistic | 🟢 PASS | ⚠️ WARN | ❌ FAIL | Action |
|---|--------|-----------|---------|---------|---------|--------|
| 2.3.1 | NetworkReceiveThroughput | Average | Baseline ± 30% | Sustained saturation | — | Check for data redistribution (DS_BCAST/DS_DIST); review DISTKEY |
| 2.3.2 | NetworkTransmitThroughput | Average | Baseline ± 30% | Sustained saturation | — | Check for large result sets; review UNLOAD operations |

### 2.4 Query Performance Metrics

| # | Metric | Statistic | 🟢 PASS | ⚠️ WARN | ❌ FAIL | Action |
|---|--------|-----------|---------|---------|---------|--------|
| 2.4.1 | QueryDuration | p90 | < 30s | 30-60s | > 60s | Identify slow queries; check EXPLAIN plans; review table design |
| 2.4.2 | QueryThroughput | Average | Baseline ± 20% | > 30% drop | > 50% drop | Check WLM queue contention; check for blocking queries |
| 2.4.3 | WLMQueueWaitTime | Average, Maximum | < 10s avg | 10-30s avg or > 60s max | > 30s avg or > 120s max | Review WLM configuration; add queues; enable Concurrency Scaling |
| 2.4.4 | WLMRunningQueries | Maximum | < concurrency limit | At limit frequently | At limit sustained | Increase concurrency or enable Concurrency Scaling |
| 2.4.5 | WLMQueueLength | Maximum | < 5 | 5-10 | > 10 sustained | Queries are waiting; review WLM priorities and concurrency |
| 2.4.6 | CommitQueueLength | Maximum | < 3 | 3-5 | > 5 sustained | Reduce write transaction frequency; batch commits |
| 2.4.7 | ConcurrencyScalingActiveClusters | Sum | 0 | > 0 occasionally | > 0 sustained | Review cost implications; optimize queries to reduce burst demand |

---

## Phase 3: CloudWatch Metrics — Serverless Workgroups

Namespace: `AWS/Redshift-Serverless`. Dimension: `Workgroup`.

| # | Metric | Statistic | 🟢 PASS | ⚠️ WARN | ❌ FAIL | Action |
|---|--------|-----------|---------|---------|---------|--------|
| 3.1 | ComputeCapacity (RPU) | Maximum | < 80% of max RPU | At max RPU frequently | At max RPU sustained | Increase max RPU; optimize queries |
| 3.2 | ComputeSeconds | Sum (24h) | Within expected budget | > 1.5x expected | > 2x expected | Review query patterns; check for runaway queries; set usage limits |
| 3.3 | DatabaseConnections | Maximum | < 80% of limit | 80-95% of limit | > 95% of limit | Implement connection pooling |
| 3.4 | QueryDuration | p90 | < 30s | 30-60s | > 60s | Identify slow queries via SYS_QUERY_HISTORY |
| 3.5 | QueriesCompletedPerSecond | Average | Baseline ± 20% | > 30% drop | > 50% drop | Check for resource contention |
| 3.6 | QueriesRunning | Maximum | < concurrency limit | At limit frequently | At limit sustained | Increase RPU; optimize query concurrency |

---

## Phase 4: Configuration & Security Audit

**APIs used in this phase:**

- `aws redshift describe-clusters` — Cluster-level config (encryption, VPC routing, public access, snapshots, maintenance window, version)
- `aws redshift describe-logging-status` — Audit logging configuration
- `aws redshift describe-cluster-parameters` — Active parameter values for the cluster's parameter group (require_ssl, WLM config, enable_user_activity_logging, etc.)
- `aws redshift describe-default-cluster-parameters` — Default parameter values for baseline comparison (identify parameters changed from their defaults)
- `aws redshift describe-cluster-subnet-groups` — Network/subnet configuration
- `aws redshift describe-cluster-snapshots` — Snapshot inventory and retention

| # | Check | AWS CLI Call | 🟢 PASS | ❌ FAIL | Action |
|---|-------|-------------|---------|---------|--------|
| 4.1 | Encryption at rest enabled | `aws redshift describe-clusters` → `Encrypted` | `true` | `false` | Recommend enabling (requires snapshot-restore migration) |
| 4.2 | SSL enforced | `aws redshift describe-cluster-parameters` → check `require_ssl` | `true` | `false` | Set `require_ssl = true` in parameter group |
| 4.3 | Audit logging enabled | `aws redshift describe-logging-status` | Logging enabled (S3 or CloudWatch) | Logging disabled | Enable audit logging |
| 4.4 | User activity logging enabled | `aws redshift describe-cluster-parameters` → check `enable_user_activity_logging` | `true` | `false` | Enable for full SQL audit trail |
| 4.5 | Enhanced VPC Routing | `aws redshift describe-clusters` → `EnhancedVpcRouting` | `true` | `false` | Enable to force COPY/UNLOAD through VPC |
| 4.6 | Public accessibility | `aws redshift describe-clusters` → `PubliclyAccessible` | `false` | `true` (unless explicitly required) | Disable public access; use VPC endpoints |
| 4.7 | Subnet group in private subnets | `aws redshift describe-cluster-subnet-groups` → subnet details | All subnets are private | Public subnets detected | Move to private subnets |
| 4.8 | Automated snapshots enabled | `aws redshift describe-clusters` → `AutomatedSnapshotRetentionPeriod` | ≥ 1 day (7+ recommended) | 0 (disabled) | Enable with appropriate retention |
| 4.9 | Snapshot inventory healthy | `aws redshift describe-cluster-snapshots` | Recent automated + manual snapshots present | No recent snapshots | Investigate snapshot failures; verify retention |
| 4.10 | Parameter group (not default) | `aws redshift describe-clusters` → `ClusterParameterGroups` | Custom parameter group | Using `default.redshift-1.0` | Create custom parameter group for tuning |
| 4.11 | Parameter drift from defaults | Compare `aws redshift describe-cluster-parameters` vs `aws redshift describe-default-cluster-parameters` | Intentional deviations documented | Unexpected deviations | Review and document all non-default parameter values, and confirm they were changed intentionally |
| 4.12 | Maintenance window configured | `aws redshift describe-clusters` → `PreferredMaintenanceWindow` | Set to low-traffic window | Default or peak-hours window | Adjust to off-peak hours |
| 4.13 | Cluster version current | `aws redshift describe-clusters` → `ClusterVersion` + `AllowVersionUpgrade` | Current version; auto-upgrade enabled | Outdated version | Enable `AllowVersionUpgrade`; schedule maintenance |

---

## Phase 5: Table Health (via SQL — System Tables)

These checks require running SQL queries against the cluster. Use system tables/views for provisioned clusters or SYS views for Serverless.

| # | Check | Query / View | 🟢 PASS | ⚠️ WARN | ❌ FAIL | Action |
|---|-------|-------------|---------|---------|---------|--------|
| 5.1 | Unsorted rows | `SVV_TABLE_INFO.unsorted` | < 5% | 5-20% | > 20% | Run `VACUUM SORT ONLY` or `VACUUM FULL` |
| 5.2 | Deleted row bloat | `SVV_TABLE_INFO.empty` | < 5% | 5-20% | > 20% | Run `VACUUM DELETE ONLY` |
| 5.3 | Stale statistics | `SVV_TABLE_INFO.stats_off` | < 5% | 5-10% | > 10% | Run `ANALYZE` on affected tables |
| 5.4 | Distribution skew | `SVV_TABLE_INFO.skew_rows` | < 1.5 | 1.5-4.0 | > 4.0 | Review DISTKEY; consider redistribution |
| 5.5 | Sort key skew | `SVV_TABLE_INFO.skew_sortkey1` | < 2.0 | 2.0-4.0 | > 4.0 | Review sort key column; consider compound vs interleaved |
| 5.6 | Tables without compression | `SVV_TABLE_INFO.encoded` = 'N' | All columns encoded | Some columns unencoded | Large tables unencoded | Run `ANALYZE COMPRESSION`; apply recommended encodings |
| 5.7 | Tables without sort keys | `SVV_TABLE_INFO.sortkey1` is NULL | All queried tables have sort keys | Some tables missing sort keys | Large frequently-queried tables without sort keys | Add sort keys based on query filter patterns |
| 5.8 | Query alerts (last 24h) | `STL_ALERT_EVENT_LOG` | No alerts | Occasional alerts | Frequent nested loop / skew alerts | Address root causes per alert type |
| 5.9 | Disk-full events | `STL_DISK_FULL_DIAG` | No events | — | Any events | Immediate action: VACUUM, resize, or archive data |
| 5.10 | Long-running queries | `STL_QUERY` (duration > 300s) | None or rare | Occasional | Frequent | Optimize queries; review WLM; check table design |

---

## Phase 6: WLM & Concurrency Assessment

Uses `aws redshift describe-cluster-parameters` to retrieve WLM configuration (`wlm_json_configuration`), QMR rules, and SQA settings.

| # | Check | Source | 🟢 PASS | ⚠️ WARN | ❌ FAIL | Action |
|---|-------|--------|---------|---------|---------|--------|
| 6.1 | WLM mode | `aws redshift describe-cluster-parameters` → `wlm_json_configuration` | Automatic WLM | Manual WLM (functional) | Manual WLM (misconfigured) | Recommend migration to Automatic WLM |
| 6.2 | Queue wait times | `STL_WLM_QUERY` avg `total_queue_time` | < 10s avg | 10-30s avg | > 30s avg | Adjust WLM priorities; enable Concurrency Scaling |
| 6.3 | Queue depth | `STV_WLM_SERVICE_CLASS_STATE.num_queued_queries` | 0 | 1-5 | > 5 sustained | Increase concurrency or add Concurrency Scaling |
| 6.4 | QMR rules configured | `aws redshift describe-cluster-parameters` → QMR settings | Rules defined for runaway queries | No rules | — | Define QMR rules (execution time, CPU, rows returned) |
| 6.5 | SQA enabled | `aws redshift describe-cluster-parameters` → `max_execution_time` | SQA enabled | — | SQA disabled | Enable Short Query Acceleration |

---

## Phase 7: Data Loading & Ingestion Health

| # | Check | Query / View | 🟢 PASS | ⚠️ WARN | ❌ FAIL | Action |
|---|-------|-------------|---------|---------|---------|--------|
| 7.1 | COPY errors (last 7 days) | `STL_LOAD_ERRORS` | No errors | Occasional errors | Frequent errors | Review `STL_LOADERROR_DETAIL`; fix source data |
| 7.2 | COPY throughput | `STL_S3CLIENT` (MB/s) | > 100 MB/s per node | 50-100 MB/s | < 50 MB/s | Check file count/size; use compression; split files |
| 7.3 | Single-row INSERTs detected | `STL_QUERY` (INSERT patterns) | No single-row INSERTs for bulk loads | Occasional | Frequent | Migrate to COPY command |
| 7.4 | Commit frequency | `STL_COMMIT_STATS` | Batched commits | Frequent small commits | Commit per row | Batch operations; reduce commit frequency |
| 7.5 | Lock contention | `STV_LOCKS` / `STL_TR_CONFLICT` | No contention | Occasional | Frequent | Review transaction isolation; stagger write operations |

---

## Phase 8: Redshift Advisor Recommendations

Advisor findings are surfaced via `aws redshift describe-clusters` (the `ClusterNodes` and cluster metadata response includes Advisor recommendation references). Detailed Advisor recommendations are visible in the Redshift console.

| # | Check | Source | Action |
|---|-------|--------|--------|
| 8.1 | Fetch Advisor recommendations | `aws redshift describe-clusters` + Redshift console Advisor tab | Review and prioritize all active recommendations |
| 8.2 | Distribution key recommendations | Advisor finding type | Alter DISTKEY per Advisor suggestion |
| 8.3 | Sort key recommendations | Advisor finding type | Add or modify sort keys per Advisor suggestion |
| 8.4 | Compression recommendations | Advisor finding type | Apply recommended encodings via deep copy |
| 8.5 | VACUUM recommendations | Advisor finding type | Schedule VACUUM for flagged tables |
| 8.6 | Table design recommendations | Advisor finding type | Review and implement table redesign |

---

## Assessment Scoring Summary

After completing all phases, summarize findings:

| Phase | Total Checks | 🟢 PASS | ⚠️ WARN | ❌ FAIL |
|-------|-------------|---------|---------|---------|
| 1. Discovery | {n} | {n} | {n} | {n} |
| 2. CloudWatch — Provisioned | {n} | {n} | {n} | {n} |
| 3. CloudWatch — Serverless | {n} | {n} | {n} | {n} |
| 4. Configuration & Security | {n} | {n} | {n} | {n} |
| 5. Table Health | {n} | {n} | {n} | {n} |
| 6. WLM & Concurrency | {n} | {n} | {n} | {n} |
| 7. Data Loading | {n} | {n} | {n} | {n} |
| 8. Advisor Recommendations | {n} | {n} | {n} | {n} |

**Overall Health:** 🟢 Healthy / 🟡 Needs Attention / 🔴 Critical

---

## Priority Action Matrix

Prioritize findings by impact and urgency:

| Priority | Category | Examples |
|----------|----------|---------|
| P0 — Immediate | Availability & data integrity | HealthStatus unhealthy, disk full, encryption disabled, no snapshots |
| P1 — Urgent (24-48h) | Performance degradation | CPU > 80% sustained, WLM queue wait > 30s, severe skew (> 4.0) |
| P2 — Soon (1-2 weeks) | Optimization opportunities | Unsorted > 20%, stale stats > 10%, missing sort keys, no QMR rules |
| P3 — Planned | Best practice alignment | Migrate to Automatic WLM, enable SQA, enable Enhanced VPC Routing |
| P4 — Advisory | Cost optimization | Right-size cluster, Reserved Instances, archive cold data to S3 |
