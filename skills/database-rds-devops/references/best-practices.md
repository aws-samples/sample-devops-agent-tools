# Aurora MySQL & RDS MySQL Best Practices Guide

Comprehensive best practices organized by operational domain, with platform-specific recommendations for Aurora MySQL, RDS MySQL, and self-managed MySQL on EC2.

> **Source:** AWS Official Documentation, AWS Prescriptive Guidance, AWS Premium Support validated runbooks.

---

## Connection Management

### Aurora MySQL

- Use **RDS Proxy** for connection pooling — reduces connection overhead by up to 80%
- Monitor `DatabaseConnections` CloudWatch metric; alarm at 75% of `max_connections`
- Adjust `wait_timeout` via Aurora Cluster Parameter Group (not my.cnf)
- Use the **reader endpoint** for read-only application connections
- Aurora automatically handles connection draining during failover via RDS Proxy
- Connection limits are instance-class dependent — scale instance class for higher limits

**Thresholds:**
| Metric | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|--------|--------|------------|-------------|
| Connection usage % | < 75% | 75-90% | > 90% |
| Threads_running | < 20 | 20-50 | > 50 |

**Reference:** https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-proxy.html

### RDS MySQL

- Use **RDS Proxy** for connection pooling
- Adjust `max_connections` via DB Parameter Group
- Monitor `DatabaseConnections` CloudWatch metric; alarm at 75%
- Set `wait_timeout = 300-900s` for web applications via Parameter Group
- RDS Proxy handles connection draining automatically during Multi-AZ failover

**Reference:** https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-proxy.html

---

## System Configuration

### Aurora MySQL

- All parameter changes must go through **Aurora Cluster or Instance Parameter Groups**
- Aurora **auto-tunes** `innodb_buffer_pool_size` — do not set it manually
- Enable **Performance Insights** (7-day free retention) for query analysis
- Enable **Enhanced Monitoring** for OS-level metrics at 1-second granularity
- Aurora uses redo logs internally — binary logging is optional and off by default
- Use **Aurora Serverless v2** for variable workloads (scales 0.5–128 ACUs)
- Static parameters require cluster reboot; dynamic parameters apply immediately

**Reference:** https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.Managing.Tuning.concepts.html

### RDS MySQL

- All parameter changes must go through **RDS DB Parameter Groups**
- Set `innodb_buffer_pool_size` to 70-75% of instance RAM via Parameter Group
- Enable **Performance Insights** for query-level analysis
- Enable **Enhanced Monitoring** for OS-level metrics
- Static parameters require instance reboot; dynamic parameters apply immediately

**Reference:** https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithParamGroups.html

---

## InnoDB Buffer Pool & Memory

### Aurora MySQL

- Aurora **auto-tunes** `innodb_buffer_pool_size` — do not override manually
- Use Performance Insights to identify queries with high wait event counts
- **Aurora Parallel Query** automatically offloads full table scans to the storage layer
- Use Read Replicas to offload read-heavy analytical queries from the writer
- Monitor `BufferCacheHitRatio` CloudWatch metric (target: >99%)
- Aurora uses a distributed cache — buffer pool restores automatically after restart (warm restart)

**Thresholds:**
| Metric | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|--------|--------|------------|-------------|
| Buffer pool hit ratio | > 99% | 95-99% | < 95% |
| Dirty pages % | < 25% | 25-50% | > 50% |
| Free pages % | > 10% | 5-10% | < 5% |

### RDS MySQL

- Set `innodb_buffer_pool_size` to 70-75% of instance RAM via DB Parameter Group
- Enable Performance Insights for query-level wait event analysis
- Monitor `FreeableMemory` CloudWatch metric; alarm below 10% of total RAM
- Enable `innodb_buffer_pool_dump_at_shutdown` and `innodb_buffer_pool_load_at_startup` for warm restarts

---

## Replication & High Availability

### Aurora MySQL

- Aurora uses **shared storage replication** — 6 copies across 3 AZs, automatic
- Create Read Replicas via AWS Console or CLI — no manual replication setup needed
- Monitor `AuroraReplicaLag` CloudWatch metric (target: **< 100ms**)
- Use **Aurora Global Database** for cross-region replication with < 1s lag
- Configure replica **promotion tiers** (tier 0 = highest failover priority)
- Aurora Failover completes in **< 30 seconds** with RDS Proxy in place
- Use **cluster reader endpoint** for automatic load balancing across replicas

**Thresholds:**
| Metric | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|--------|--------|------------|-------------|
| AuroraReplicaLag | < 100ms | 100-1000ms | > 1000ms (unusual for Aurora) |

**Reference:** https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Replication.html

### RDS MySQL

- Enable **Multi-AZ** for synchronous standby replication and automatic failover (1-2 min RTO)
- Create **Read Replicas** for read scaling (up to 15 per source instance)
- Monitor `ReplicaLag` CloudWatch metric (target: < 30 seconds)
- Enable automated backups with 7-35 day retention for point-in-time recovery
- Use **RDS Blue/Green Deployments** for zero-downtime major version upgrades

**Thresholds:**
| Metric | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|--------|--------|------------|-------------|
| ReplicaLag | < 10s | 10-60s | > 60s |

---

## Storage & Capacity

### Aurora MySQL

- Aurora storage **auto-grows** in 10 GB increments up to 128 TiB — no pre-provisioning needed
- Storage billed per GB-month consumed (not provisioned capacity)
- Monitor `FreeLocalStorage` CloudWatch metric for local temp space
- Use `ALTER TABLE ... FORCE` to reclaim space (`OPTIMIZE TABLE` is equivalent on Aurora InnoDB)
- Use **Aurora Fast Cloning** for instant test environment copies (copy-on-write, low cost)
- Enable **Aurora Backtracking** (up to 72 hours) for point-in-time rewind without restore
- Aurora storage does NOT auto-shrink — space must be explicitly reclaimed

**Thresholds:**
| Metric | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|--------|--------|------------|-------------|
| Table fragmentation | < 15% | 15-30% | > 30% |

### RDS MySQL

- Enable **RDS Storage Auto Scaling** — expands automatically at configured threshold
- Use **gp3** storage: baseline 3000 IOPS, independent throughput (cost-effective default)
- Use **io1/io2 Block Express** for latency-sensitive workloads requiring guaranteed IOPS
- Monitor `FreeStorageSpace` CloudWatch metric; alarm at 20% remaining
- Enable encryption at rest using AWS KMS — must be set at creation time

---

## Query Performance Optimization

### Aurora MySQL

- Use **Performance Insights** as the primary tool for slow query identification
- Add indexes on columns used in `WHERE`, `JOIN ON`, `ORDER BY`, `GROUP BY`
- Use **covering indexes** to eliminate table lookups
- Avoid `SELECT *` — specify only required columns
- **Aurora Parallel Query** handles large analytical scans automatically
- Use `EXPLAIN ANALYZE` (MySQL 8.0+) for actual vs estimated row counts
- Batch `INSERT`/`UPDATE` operations; avoid single-row loops
- Avoid functions on indexed columns in WHERE clauses (prevents index use)

**Thresholds:**
| Metric | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|--------|--------|------------|-------------|
| Avg query latency | < 1s | 1-10s | > 10s |
| Rows examined / rows sent | < 100 | 100-1000 | > 1000 |
| Full scan % | < 20% | 20-50% | > 50% |

**Reference:** https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.BestPractices.Performance.html

---

## Index Optimization

### Aurora MySQL

- Use Performance Insights to identify queries with high `rows_examined` counts
- Drop indexes with zero reads in `table_io_waits_summary_by_index_usage`
- Use MySQL 8.0 **INVISIBLE indexes** to safely test removal before dropping
- Build **composite indexes** covering WHERE + JOIN + ORDER BY columns together
- Aurora Parallel Query may partially compensate for missing indexes on large scans

### RDS MySQL

- Use Performance Insights to correlate index I/O with query performance
- Use **RDS Blue/Green Deployments** to safely test index additions in production
- Use **INVISIBLE indexes** to validate removal impact before dropping
- Monitor `ReadIOPS` drops after adding indexes to confirm improvement

---

## CloudWatch Logs Analysis

### Aurora MySQL

- Log groups: `/aws/rds/cluster/{cluster-name}/{log-type}` (error, slowquery, audit, general)
- Set `long_query_time = 1` via Aurora Cluster Parameter Group
- Enable Aurora MySQL audit plugin via Parameter Group for compliance logging
- Use **CloudWatch Logs Insights** for ad-hoc SQL-like log queries
- Create **CloudWatch Metric Filters** for automated alerting on error patterns
- Export logs to S3 for long-term retention beyond 90 days
- **Filter `rdsadmin` queries** from slow query analysis — focus on application queries only

**Error Log Thresholds:**
| Pattern | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|---------|--------|------------|-------------|
| Aborted connections/hour | < 5 | 5-20 | > 20 |
| Access denied/hour | < 5 | 5-20 | > 20 |
| InnoDB errors | 0 | — | ≥ 1 |
| Deadlocks/hour | 0 | 2-10 | > 10 |
| Replication errors | 0 | — | ≥ 1 |
| Slow queries/hour | < 10 | 10-50 | > 50 |

**Reference:** https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.BestPractices.CW.html

---

## Table Maintenance

### Aurora MySQL

- Aurora handles storage-level defragmentation automatically
- Use `ALTER TABLE ... FORCE` to rebuild a specific table when needed
- `OPTIMIZE TABLE` on Aurora InnoDB is equivalent to `ALTER TABLE ... FORCE`
- Schedule `ANALYZE TABLE` via Lambda or Aurora MySQL Event Scheduler
- Take a **manual RDS snapshot** before any `ALTER TABLE` on large tables
- Use **Aurora Fast Cloning** to create a safe test copy before large operations

### RDS MySQL

- Schedule `OPTIMIZE TABLE` and `ANALYZE TABLE` via RDS maintenance window or Lambda
- Take an **RDS snapshot** before `ALTER TABLE` on large tables
- Monitor `FreeStorageSpace` after `OPTIMIZE TABLE` — temporary space required
- Use **RDS Blue/Green Deployments** for zero-downtime schema changes

---

## Current Activity & Lock Analysis

### Aurora MySQL

- Use **Performance Insights Active Sessions** view for real-time query analysis
- Monitor `Threads_running` via CloudWatch or `performance_schema`
- Long-running transactions block Aurora storage garbage collection — keep transactions short
- Use `KILL <thread_id>` with caution — verify query is not a critical process
- Enable `performance_schema.events_statements_current` for live SQL text

**Thresholds:**
| Metric | 🟢 OK | 🟡 WARNING | 🔴 CRITICAL |
|--------|--------|------------|-------------|
| Long-running queries | < 30s | 30-300s | > 300s |
| Lock wait time | < 10s | 10-60s | > 60s |
| Active transactions | < 60s | 60-300s | > 300s |

---

## Security Best Practices

### Aurora MySQL

- ✅ `StorageEncrypted = true` (cannot enable after creation — must recreate)
- ✅ `IAMDatabaseAuthenticationEnabled = true`
- ✅ `PubliclyAccessible = false` on all instances
- ✅ `DeletionProtection = true` (production clusters)
- ✅ SSL/TLS enforced for all connections (`require_secure_transport = ON`)
- ✅ Security groups: no `0.0.0.0/0` ingress rules
- ✅ Use AWS Secrets Manager for credential rotation
- ✅ Enable audit logging for compliance requirements

---

## Cost Optimization

### Aurora MySQL

- Use **Aurora Serverless v2** for dev/test and variable workloads (pay per ACU-hour)
- Right-size instances: if CPU consistently < 40%, downsize instance class
- Use **Reserved Instances** (1-year or 3-year) for steady production workloads (up to 72% savings)
- Use **Aurora I/O-Optimized** for high I/O workloads (eliminates per-I/O charges)
- Use **Aurora Fast Cloning** instead of full snapshot-restore for dev/test copies
- Monitor `VolumeBytesUsed` trend — reclaim unused space to reduce storage costs

### RDS MySQL

- Use **gp3** storage (cheaper than gp2 for same performance)
- Right-size instances based on CloudWatch CPU/Memory utilization
- Use **Reserved Instances** for steady workloads
- Enable **Storage Auto Scaling** to avoid over-provisioning
- Use **RDS Extended Support** awareness — older versions incur extra charges
