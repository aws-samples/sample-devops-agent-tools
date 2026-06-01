# RDS / Aurora CloudWatch Metrics Thresholds Reference

All metrics retrieved via a single `cloudwatch.GetMetricData` call per resource over a 7-day window with `Period=21600` (6h). Severity reflects sustained values, not single spikes.

## RDS Instance Metrics (Namespace: `AWS/RDS`, Dimension: `DBInstanceIdentifier`)

| Metric | Stat | Normal | Warning | Critical | Finding |
|---|---|---|---|---|---|
| CPUUtilization | Average | < 60% | > 70% | > 90% | Right-size instance class or add a read replica |
| CPUUtilization | Maximum | < 80% | > 90% sustained | 100% sustained | CPU saturation under peak |
| FreeableMemory | Minimum | > 20% of class memory | < 10% of class memory | < 5% of class memory | Memory pressure — upgrade class or tune buffer pool |
| SwapUsage | Average | 0 | > 0 sustained | > 100 MB sustained | Memory undersized — upgrade |
| FreeStorageSpace | Minimum | > 20% allocated | < 20% allocated | < 10% allocated | Increase storage / enable autoscaling |
| ReadIOPS | Average | < 70% provisioned (io1/io2) | > 80% provisioned | > 95% provisioned | Storage I/O saturation |
| WriteIOPS | Average | < 70% provisioned | > 80% provisioned | > 95% provisioned | Storage I/O saturation |
| ReadLatency | Average | < 5 ms | > 10 ms | > 20 ms (gp3) / > 10 ms (io1/io2) | Slow reads — check IOPS, working set fit |
| WriteLatency | Average | < 5 ms | > 10 ms | > 20 ms | Slow writes — check IOPS, sync settings |
| DatabaseConnections | Maximum | < 60% of `max_connections` | > 80% | > 95% | Connection saturation — add pooling (RDS Proxy) |
| ReplicaLag | Maximum | < 10 s | > 30 s | > 300 s | Replica falling behind — investigate write rate, replica class |
| BinLogDiskUsage | Average | bounded | unbounded growth | rapid growth | Tighten `binlog_expire_logs_seconds` |
| NetworkReceiveThroughput | Average | < 80% of class bandwidth | > 80% | > 95% | Network saturation |
| NetworkTransmitThroughput | Average | < 80% of class bandwidth | > 80% | > 95% | Network saturation |
| DiskQueueDepth | Average | < 5 | > 10 | > 30 | I/O queueing — increase IOPS |

## Aurora Cluster Metrics (Namespace: `AWS/RDS`, Dimension: `DBClusterIdentifier`)

| Metric | Stat | Normal | Warning | Critical | Finding |
|---|---|---|---|---|---|
| CPUUtilization | Average | < 60% | > 70% | > 90% | Scale instance class or add reader |
| FreeableMemory | Minimum | > 20% | < 10% | < 5% | Memory pressure |
| DatabaseConnections | Maximum | < 60% | > 80% | > 95% | Add RDS Proxy / pooling |
| AuroraReplicaLag | Maximum | < 50 ms | > 100 ms | > 1000 ms | Replica lag — check writer load |
| BufferCacheHitRatio | Average | > 99% | < 95% | < 90% | Working set doesn't fit — bigger class |
| CommitLatency | Average | < 10 ms | > 20 ms | > 50 ms | Write path latency |
| ReadLatency | Average | < 5 ms | > 10 ms | > 20 ms | Slow reads |
| WriteLatency | Average | < 5 ms | > 10 ms | > 20 ms | Slow writes |
| VolumeReadIOPs | Sum | — | — | sustained spike | Investigate query plan / hot rows |
| VolumeWriteIOPs | Sum | — | — | sustained spike | Investigate write amplification |
| ServerlessDatabaseCapacity (v2) | Average | within configured min/max | hitting `max` | sustained at `max` | Raise ACU ceiling or move to provisioned |
| Deadlocks | Sum | 0 | > 0 sustained | > 100 / hour | Transaction/lock review |

## Aurora Global Database

| Metric | Stat | Warning | Critical | Finding |
|---|---|---|---|---|
| AuroraGlobalDBReplicationLag | Maximum | > 1 s | > 5 s | Cross-region lag — investigate writer region load |
| AuroraGlobalDBRPOLag | Maximum | > 1 s | > 5 s | RPO violation risk |

## Performance Insights (when enabled)

| Metric | Source | Warning | Finding |
|---|---|---|---|
| `db.load.avg` | `pi.GetResourceMetrics` | > vCPU count sustained | DB at or above CPU capacity |
| `db.load.avg` (per `db.wait_event`) | top wait event | dominant wait type | targets tuning area (CPU, IO, Lock, Network) |
| `db.SQL.total_call_count` | top SQL | top 5 statements | inputs for query optimization review |

## Log Pattern Severity (CloudWatch Logs `FilterLogEvents` over 7 days)

| Pattern (case-insensitive) | Severity if found | Action |
|---|---|---|
| `out of memory` / `OOM` | HIGH | Increase memory / tune buffer pool |
| `too many connections` / `connection limit exceeded` | HIGH | Pooling, raise `max_connections` |
| `replication has stopped` / replication thread errors | HIGH | Investigate replica / network |
| `archiver failed` (Postgres WAL) | HIGH | WAL archive / disk pressure |
| `deadlock detected` / `lock wait timeout` | MEDIUM | Transaction / index review |
| `aborted connection` (MySQL) | MEDIUM | Network / client / `wait_timeout` |
| `slow query` / `duration: ` (Postgres) > 1s | MEDIUM | Query optimization |
| `authentication failed` repeated | MEDIUM | Possible credential issue / attack surface |
| `checkpoint too frequent` (Postgres) | MEDIUM | Tune `checkpoint_timeout`, `max_wal_size` |
| Generic `ERROR` > 100 / 7 days | LOW | Triage trends |

## Alarm Coverage Expectations

The skill flags any of these as **MEDIUM** when missing (`cloudwatch.DescribeAlarmsForMetric` returns nothing for the resource + metric):

| Resource | Metric | Threshold (suggested) |
|---|---|---|
| RDS instance | CPUUtilization | > 80% for 5 minutes |
| RDS instance | FreeStorageSpace | < 10% allocated |
| RDS instance | FreeableMemory | < 256 MB or < 10% of class |
| RDS instance | DatabaseConnections | > 80% of `max_connections` |
| RDS instance | ReadLatency / WriteLatency | > 20 ms |
| Read replica | ReplicaLag | > 60 s |
| Aurora cluster | AuroraReplicaLag | > 100 ms |
| Aurora cluster | DatabaseConnections | > 80% of `max_connections` |
| Aurora cluster | BufferCacheHitRatio | < 95% |

## RDS Events of Interest (`rds.DescribeEvents`, last 14 days)

| Event substring | Severity | Action |
|---|---|---|
| `failover` | INFO if scheduled, HIGH if unplanned | Confirm RCA |
| `restart` (unplanned) | HIGH | Investigate logs, alarms |
| `out of memory` / `low memory` | HIGH | Memory tuning / class upgrade |
| `storage-full` / `low-storage` | CRITICAL | Add storage / enable autoscaling |
| `automated-backup-failed` | HIGH | Investigate immediately |
| `pending-maintenance` not applied within window | MEDIUM | Apply at next window |
| `read-replica-error` | HIGH | Replica health |
| `parameter group changed` | INFO | Audit trail |
| `instance-stopped` (production) | HIGH | Verify intentional |
