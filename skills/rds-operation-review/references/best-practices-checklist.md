# RDS / Aurora Best Practices Checklist

Organized by AWS Well-Architected pillar. Maps directly to the checks in `SKILL.md` Step 9.

## Security

- [ ] Database **not publicly accessible** (`PubliclyAccessible=false`)
- [ ] Database security group ingress **scoped to application SGs** (no `0.0.0.0/0` on the DB port)
- [ ] **Encryption at rest** enabled (`StorageEncrypted=true`); customer-managed KMS key preferred
- [ ] **KMS key rotation** enabled
- [ ] **Encryption in transit** enforced (`rds.force_ssl=1` for Postgres, `require_secure_transport=ON` for MySQL/MariaDB)
- [ ] **IAM database authentication** enabled where supported
- [ ] **Master credentials** stored in AWS Secrets Manager with automatic rotation (not hard-coded)
- [ ] Engine **audit logs** exported to CloudWatch (PCI/HIPAA: required)
- [ ] **CloudTrail** enabled for RDS API activity
- [ ] No use of the **default master username** for runtime access (per-app users)
- [ ] **VPC endpoint** for RDS API used in restricted-egress accounts

## Reliability

- [ ] **Multi-AZ** enabled for all production databases (Aurora: ≥1 reader in a different AZ)
- [ ] DB **subnet group spans ≥2 AZs** (≥3 for tier-1)
- [ ] **Automated backups** enabled with retention ≥7 days (35 days for production)
- [ ] **Deletion protection** enabled for production
- [ ] **Final snapshot** policy set on deletion
- [ ] **Custom parameter group** (not `default.<engine>`) — required to tune anything
- [ ] **Pending maintenance actions** applied within window
- [ ] Read-replica `ReplicaLag` 7-day max < 30s
- [ ] **Cross-region** read replica or **Aurora Global Database** for tier-1 DR
- [ ] **Failover tested** at least quarterly
- [ ] RTO / RPO documented for each database tier

## Performance

- [ ] **Performance Insights** enabled (free 7-day tier)
- [ ] **Enhanced Monitoring** enabled, interval ≤60s for production
- [ ] CPU 7-day average < 70%
- [ ] FreeStorageSpace ≥20% of allocated
- [ ] FreeableMemory ≥10% of instance class memory; `SwapUsage` near 0
- [ ] DatabaseConnections 7-day max < 80% of `max_connections`
- [ ] ReadLatency / WriteLatency < 20ms (< 10ms for io1/io2)
- [ ] Aurora `BufferCacheHitRatio` > 95%
- [ ] **RDS Proxy / connection pooling** in place for serverless or many-client workloads
- [ ] Storage type sized correctly (gp3 default; io1/io2 only for >16k sustained IOPS)
- [ ] Slow query log enabled and reviewed (`long_query_time=1`, `log_min_duration_statement=1000`)
- [ ] Indexes reviewed; N+1 query patterns eliminated

## Cost Optimization

- [ ] No **stopped instances** still incurring storage cost (delete or convert to final snapshot)
- [ ] No **previous-gen instance classes** (`db.m4`, `db.r4`, `db.t2`) in production
- [ ] **gp2 → gp3** migration for general-purpose storage
- [ ] **Provisioned IOPS** (io1/io2) actually utilized > 50% — otherwise downsize or move to gp3
- [ ] **Reserved Instances** for steady-state production (1-year No Upfront baseline)
- [ ] **Storage autoscaling** enabled with sensible `MaxAllocatedStorage`
- [ ] Idle databases identified (low connections + low CPU) and stopped/deleted
- [ ] Manual snapshots cleaned up (delete those >90 days old unless retention policy says otherwise)
- [ ] **Graviton** (`db.r6g`, `db.m6g`, `db.t4g`) used where the engine supports it
- [ ] **Cost-allocation tags** applied: `Environment`, `Owner`, `CostCenter`, `Application`
- [ ] **Cost Anomaly Detection** enabled for RDS

## Operational Excellence

- [ ] CloudWatch alarms on: `CPUUtilization`, `FreeStorageSpace`, `DatabaseConnections`, `FreeableMemory`, `ReplicaLag` / `AuroraReplicaLag`, `WriteLatency`, `ReadLatency`
- [ ] **RDS event subscription** in place, routing to SNS / chat / ticketing
- [ ] **CloudWatch Logs export** enabled for engine logs (error, slow query, audit, general)
- [ ] **AutoMinorVersionUpgrade=true** for non-production
- [ ] **Maintenance window** configured outside business hours
- [ ] Engine version is the **latest minor** within current major; major version not within 6 months of EOL
- [ ] **Operational tags** present: `Environment`, `Owner`, `Runbook`, `OnCall`
- [ ] AWS Config rules detect parameter / configuration drift
- [ ] DR / failover **runbooks** documented and linked from tags

## Engine-Specific (spot checks)

### MySQL / MariaDB

- [ ] `innodb_buffer_pool_size` ≈ 75% of memory
- [ ] `max_connections` sized to instance class
- [ ] `query_cache_type=OFF` on MySQL 8+
- [ ] Slow query log: `long_query_time=1`, `slow_query_log=1`
- [ ] Binlog retention bounded (`binlog_expire_logs_seconds`)

### PostgreSQL

- [ ] `shared_buffers` ≈ 25% of memory
- [ ] `effective_cache_size` ≈ 75% of memory
- [ ] `work_mem` 4–64 MB
- [ ] Autovacuum tuned for write-heavy tables (per-table thresholds)
- [ ] `pg_stat_statements` enabled
- [ ] `log_min_duration_statement` ≥ 1000 ms

### Oracle

- [ ] BYOL vs. License Included clear in tags
- [ ] SGA/PGA sized for workload
- [ ] AWR snapshots retained
- [ ] Tablespace autoextend on with sane MAXSIZE

### SQL Server

- [ ] Tempdb: 1 file per vCPU, up to 8
- [ ] MAXDOP set to vCPU count
- [ ] Cost threshold for parallelism = 50
- [ ] Always On AG vs. Multi-AZ choice documented
- [ ] Index maintenance jobs scheduled

### Aurora

- [ ] Cluster has ≥1 reader (≥2 for tier-1)
- [ ] Reader auto-scaling policy in place where read traffic varies
- [ ] **Aurora Global Database** for cross-region DR (tier-1)
- [ ] Backtrack window sized appropriately (Aurora MySQL only)
- [ ] **Serverless v2**: ACU floor set high enough to avoid cold-start; ceiling caps spend
- [ ] Cluster cache management enabled for failover-sensitive workloads (Aurora PG)
