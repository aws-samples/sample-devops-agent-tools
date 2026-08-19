```markdown
# Blocker Catalog — RDS/Aurora Hidden Resilience Constraints

66 blockers across 7 categories. Referenced by `SKILL.md` detection rules via the ID column.

## Category 1: FAILOVER TIMING (In-Region HA) — 8 blockers

| ID | Blocker | Documentation Source | Impact |
|----|---------|---------------------|--------|
| FT-01 | RDS Multi-AZ failover takes 60-120 seconds (single standby) | [Multi-AZ Failover](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.Failover.html) | Applications experience 1-2 min downtime minimum |
| FT-02 | Multi-AZ with two readable standbys: failover <35 seconds | [Multi-AZ Features](https://aws.amazon.com/rds/features/multi-az/) | Only available for PostgreSQL and MySQL; not all engines |
| FT-03 | Large transactions or lengthy recovery processes INCREASE failover time beyond 120s | [Multi-AZ Failover](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.Failover.html) | Unpredictable failover duration under load |
| FT-04 | Aurora DNS TTL = 5 seconds, but client/JVM/OS DNS caching can extend staleness | [DNS Caching](https://docs.aws.amazon.com/whitepapers/latest/amazon-aurora-mysql-db-admin-handbook/dns-caching.html) | Applications route to dead endpoint until cache expires |
| FT-05 | RDS (non-Aurora) DNS CNAME TTL = 60 seconds | [Multi-AZ Failover](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.Failover.html) | 60s of stale routing even after failover completes |
| FT-06 | Aurora single-writer cluster without readers: NO automatic failover target exists | [Aurora Fast Failover](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.BestPractices.FastFailover.html) | Must launch new instance from scratch (10-15 min) |
| FT-07 | Aurora secondary cluster readers restart when primary writer restarts or fails over | [Aurora Global Database Limitations](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Global database secondary becomes unavailable during primary events |
| FT-08 | Single-AZ RDS instance: AZ failure = full outage requiring snapshot restore | [RDS Deployment Options](https://aws.amazon.com/blogs/database/choose-the-right-amazon-rds-deployment-option-single-az-instance-multi-az-instance-or-multi-az-database-cluster/) | RPO typically 5 minutes based on transaction log upload interval to S3 |

## Category 2: SNAPSHOT RESTORE CONSTRAINTS — 10 blockers

| ID | Blocker | Documentation Source | Impact |
|----|---------|---------------------|--------|
| SR-01 | Snapshot restore uses LAZY LOADING from S3 — data loads in background | [Restoring from Snapshot](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html) | Instance shows "available" but first-access reads hit S3 latency |
| SR-02 | Changing storage type during restore SLOWS the process significantly | [Restoring from Snapshot](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html) | Migration between magnetic/gp2/gp3/io1 adds substantial time |
| SR-03 | Cannot restore to an EXISTING instance — always creates NEW instance | [Restoring from Snapshot](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html) | Endpoint changes; application reconfiguration required |
| SR-04 | Cannot reduce allocated storage on restore | [Restoring from Snapshot](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html) | Storage size locked at snapshot time |
| SR-05 | Default parameter group assigned on restore — custom parameters LOST unless you choose a different one | [Parameter Group Considerations](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html) | Performance tuning, replication settings, memory config all revert to defaults |
| SR-06 | Default VPC security group assigned on restore — access rules LOST | [Security Group Considerations](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html) | Restored DB may be unreachable until SG manually re-applied |
| SR-07 | Aurora PITR restores ONLY the cluster — DB instances must be created separately | [restore_db_cluster_to_point_in_time](https://docs.aws.amazon.com/boto3/latest/reference/services/rds/client/restore_db_cluster_to_point_in_time.html) | Additional 5-10 min per instance after cluster restore |
| SR-08 | Aurora PITR granularity: transaction logs uploaded to S3 every 5 minutes | [PITR for RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIT.html) | Maximum 5-minute RPO gap even with continuous backups |
| SR-09 | Cannot restore directly from a shared and encrypted RDS snapshot cross-account | [Restoring from Snapshot](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html) | Must first copy to target account re-encrypting with target KMS key, adding time |
| SR-10 | RDS PITR time varies significantly based on transaction log volume | [RDS Snapshot Restore Demystified](https://aws.amazon.com/blogs/database/amazon-rds-snapshot-restore-and-recovery-demystified/) | PITR has two components: volume restore + transaction log replay; log replay time is unpredictable |

## Category 3: ENCRYPTION CONSTRAINTS — 5 blockers

| ID | Blocker | Documentation Source | Impact |
|----|---------|---------------------|--------|
| EN-01 | CANNOT enable encryption on an existing unencrypted RDS/Aurora instance | [Encrypt Existing RDS](https://docs.aws.amazon.com/prescriptive-guidance/latest/patterns/encrypt-an-existing-amazon-rds-for-postgresql-db-instance.html) | Requires snapshot-encrypt-restore migration (downtime + endpoint change) |
| EN-02 | Once encrypted, KMS key CANNOT be changed directly — requires snapshot/copy/restore cycle | [RDS Encryption](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.Encryption.html) | Key rotation requires full migration event |
| EN-03 | Cross-region snapshot copy requires RE-ENCRYPTION with destination region KMS key | [Cross-Account Cross-Region Aurora](https://aws.amazon.com/blogs/architecture/field-notes-how-to-set-up-your-cross-account-and-cross-region-database-for-amazon-aurora/) | Adds time + requires pre-provisioned KMS key in target region |
| EN-04 | AWS-managed KMS key (aws/rds) CANNOT be used for cross-account backup copy | [Cross-Account Backups](https://aws.amazon.com/blogs/storage/protecting-amazon-rds-db-instances-encrypted-using-kms-aws-managed-key-with-cross-account-and-cross-region-backups/) | Must use customer-managed CMK for any cross-account DR |
| EN-05 | KMS inaccessible-encryption-credentials state is TERMINAL for Aurora Global Database if key deleted | [Aurora Global Database Limitations](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | No recovery possible if KMS key access is lost |

## Category 4: KMS API THROTTLING — 4 blockers

| ID | Blocker | Documentation Source | Impact |
|----|---------|---------------------|--------|
| KT-01 | Symmetric cryptographic operations quota: 5,500-50,000 req/s depending on region | [KMS Request Quotas](https://docs.aws.amazon.com/kms/latest/developerguide/requests-per-second.html) | Parallel encrypted restores share this quota with ALL other services (S3 SSE, EBS, Lambda, DynamoDB) |
| KT-02 | KMS quota is SHARED across all services using the same key in the same region | [KMS Throttling](https://docs.aws.amazon.com/kms/latest/developerguide/throttling.html) | RDS restore competes with S3 SSE, EBS, Lambda, etc. for KMS capacity |
| KT-03 | Exceeding KMS quota returns ThrottlingException — restore operations may stall or fail | [KMS ThrottlingException](https://repost.aws/knowledge-center/kms-throttlingexception-error) | Causing restore operations to stall or fail |
| KT-04 | CreateGrant quota: 50 req/s — each encrypted RDS operation requires a KMS grant | [KMS Request Quotas](https://docs.aws.amazon.com/kms/latest/developerguide/requests-per-second.html) | Bottleneck when restoring many encrypted instances simultaneously during DR |

## Category 5: CROSS-REGION DR CONSTRAINTS — 13 blockers

| ID | Blocker | Documentation Source | Impact |
|----|---------|---------------------|--------|
| CR-01 | Cross-region automated backup replication NOT supported for Aurora (must use Global Database) | [Replicating Automated Backups](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReplicateBackups.html) | Aurora cross-region DR requires Global Database or manual snapshot copies |
| CR-02 | Cross-region automated backup replication NOT supported for Multi-AZ DB clusters | [Replicating Automated Backups](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReplicateBackups.html) | Multi-AZ cluster architecture loses cross-region automated backup capability |
| CR-03 | Maximum 20 cross-region automated backup replications per account | [Replicating Automated Backups](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReplicateBackups.html) | Large fleets hit this limit; requires prioritization |
| CR-04 | Specific source-to-destination region pairs supported (not all-to-all) | [Replicating Automated Backups](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReplicateBackups.html) | DR region choice may be constrained by supported pairs |
| CR-05 | Aurora Global Database switchover/failover requires SAME major+minor engine version | [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Version mismatch between primary/secondary blocks DR execution |
| CR-06 | Some engine versions require IDENTICAL patch levels for switchover/failover | [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Patch drift silently breaks DR capability |
| CR-07 | Aurora Global Database does NOT support Backtrack | [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Cannot use fast point-in-time rollback with global topology |
| CR-08 | Aurora Global Database does NOT support Aurora Auto Scaling for secondary clusters | [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Secondary must be manually sized; may be under-provisioned for DR promotion |
| CR-09 | Cannot apply custom parameter group during major version upgrade of global database | [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Post-upgrade manual PG application required per region |
| CR-10 | Automatic minor version upgrade has NO EFFECT on global database clusters | [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Manual upgrade coordination required across all regions |
| CR-11 | Aurora Global Database: primary cluster based on RDS PostgreSQL replica CANNOT create secondary | [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Specific migration path blocks global DR setup; attempts time out |
| CR-12 | Cannot stop/start Aurora DB clusters in global database individually | [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Cost management limited; cannot hibernate secondary clusters |
| CR-13 | Aurora Global Database replication is ASYNCHRONOUS — sub-second typical but NOT guaranteed | [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Under heavy write load, replication lag can exceed 1 second |

## Category 6: APPLICATION-LAYER RESILIENCE GAPS — 6 blockers

| ID | Blocker | Documentation Source | Impact |
|----|---------|---------------------|--------|
| AL-01 | Without RDS Proxy or AWS JDBC Driver, failover depends entirely on DNS propagation | [Fast Failover](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.BestPractices.FastFailover.html) | 5-60 second stale routing window |
| AL-02 | Connection pools hold stale connections after failover — must be drained/refreshed | [Resolve Aurora Failover](https://repost.aws/knowledge-center/failovers-aurora-mysql) | Applications throw errors until pool cycles |
| AL-03 | TCP keepalive defaults (2+ hours) mean dead connections are not detected for minutes | [Fast Failover](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.BestPractices.FastFailover.html) | Recommended: tcp_keepalives_idle=1, interval=1, count=5 |
| AL-04 | RDS Proxy with Global Database: proxy on secondary fails read/write requests (no writer) | [RDS Proxy with Global DB](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-proxy-gdb.html) | Must redirect to new primary proxy after global failover manually |
| AL-05 | Write forwarding adds latency on secondary cluster writes forwarded to primary | [Write Forwarding](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-write-forwarding.html) | Not a replacement for local writes; consistency delays |
| AL-06 | Cluster cache management NOT supported for Aurora PostgreSQL secondary clusters in global databases | [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) | Cold buffer pool after global failover; performance degradation |

## Category 7: ACCOUNT-LEVEL SERVICE QUOTAS (Silent DR Blockers) — 20 blockers

| ID | Blocker | Default Limit | Impact |
|----|---------|---------------|--------|
| QT-01 | Manual DB cluster snapshots per account | 100 | Cannot create pre-DR safety snapshot if at limit |
| QT-02 | Manual DB instance snapshots per account | 100 | Blocks backup-before-failover pattern |
| QT-03 | DB instances per account (per region) | 40 | Cannot restore/create instances in DR region if at limit |
| QT-04 | DB clusters per account (per region) | 40 | Cannot create new Aurora cluster from snapshot in target region |
| QT-05 | Total storage across all DB instances per account | 100 TB | Large fleet restore may exceed; new instances rejected |
| QT-06 | Cross-region automated backup replications per account | 20 | Cannot replicate all DBs cross-region if fleet >20 |
| QT-07 | **Concurrent** cross-region snapshot copies per destination region | **5** | Mass DR bottleneck — only 5 copies at a time; remaining queue adds 15-60+ min per batch of 5 |
| QT-08 | DB parameter groups per account | 50 | Cannot create custom PG in DR region; restored instances get default PG |
| QT-09 | DB subnet groups per account | 50 | Cannot restore in DR region without available subnet group slot |
| QT-10 | Aurora Global Databases per account | 5 | Limits how many clusters can have cross-region DR |
| QT-11 | Read replicas per source instance | 5 (RDS) / 15 (Aurora) | Limits HA topology depth |
| QT-12 | VPC security groups per DB instance | 5 | Complex SG setups may not restore cleanly |
| QT-13 | Event subscriptions per account | 20 | May miss DR/failover alerts if limit reached |
| QT-14 | Reserved DB instances per account | 40 | DR region may lack reserved capacity |
| QT-15 | KMS CreateGrant API calls | 50 req/sec | Parallel restores of encrypted fleet self-throttle |
| QT-16 | KMS grants per key | 50,000 | Large fleets with frequent restores can approach |
| QT-17 | Option groups per account | 20 | RDS restore may fail if limit reached (Oracle/SQL Server) |
| QT-18 | Custom endpoints per Aurora cluster | 5 | Post-DR cluster may not recreate all custom endpoints |
| QT-19 | Proxies per account | 20 | Cannot deploy RDS Proxy in DR region if at limit |
| QT-20 | IAM roles per account (for monitoring/proxy) | 1,000 | Complex DR automation may need roles |

> **Note on QT-06 vs QT-07:** these are two distinct quotas. QT-06 (20) caps the total number of cross-region *automated backup replications* configured per account. QT-07 (5) caps how many cross-region *snapshot copy operations* can run **concurrently** at a time in the destination region — this is the batching constraint that drives the RTO adjustment formula in SKILL.md.
