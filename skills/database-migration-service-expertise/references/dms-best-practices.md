
# AWS DMS Best Practices Reference

## 1. Replication Instance Sizing and Configuration

### Instance Type Selection

| Workload Type | Recommended Instance Class | Use Case |
|---|---|---|
| Memory-intensive | R-class (dms.r5, dms.r6i) | LOB migrations, high-TPS CDC, large data volumes |
| CPU-intensive | C-class (dms.c5, dms.c6i) | Heterogeneous migrations, parallel tasks, transformations |
| Development/Test | T-class (dms.t3) | Non-production, low-throughput testing |
| Variable workloads | DMS Serverless | Unpredictable traffic, intermittent migrations |

### Sizing Best Practices

- **Right-size based on workload phase**: Full load typically requires more memory than CDC. Evaluate and resize between phases.
- **Monitor CloudWatch metrics continuously**: Set alarms for CPUUtilization > 80%, FreeableMemory < 2GB, SwapUsage > 0.
- **Use Multi-AZ for critical migrations**: Required for long-running CDC tasks, production migrations, and data protection.
- **Use Single-AZ for non-critical workloads**: Appropriate for transient full-load tasks, development environments, and one-time migrations.
- **Allocate sufficient storage**: R/C instances include 100GB GP2; T instances include 50GB. Add storage for extended debug logging.
- **Regularly evaluate instance size**: Scale up during full load, scale down during CDC when possible.

### Memory Estimation Formulas

For **Full LOB mode**:
```
Required Memory ≈ (LOB columns per table) × (MaxFullLoadSubTasks) × (LOB chunk size) × (CommitRate)
```

For **Limited LOB mode**:
```
Required Memory ≈ (LOB columns per table) × (MaxFullLoadSubTasks) × (Max LOB size setting) × (BulkArraySize)
```

### Instance Sizing Quick Reference

| Tables | LOB Columns | Parallel Tasks | Recommended Minimum |
|---|---|---|---|
| < 100 | None | 8 | dms.c5.large |
| < 100 | Few | 8 | dms.r5.large |
| 100-500 | None | 16 | dms.c5.xlarge |
| 100-500 | Many | 16 | dms.r5.xlarge |
| 500+ | None | 32 | dms.c5.2xlarge |
| 500+ | Many | 32 | dms.r5.2xlarge |

---

## 2. Endpoint Configuration

### Source Endpoint Best Practices

#### Oracle
- Use **Binary Reader** over LogMiner for CDC performance (up to 5x faster)
- Ensure UNDO/TEMP tablespace sizing is adequate for full load duration
- Enable supplemental logging at both database and table level
- Set `addSupplementalLogging: Y` in endpoint extra connection attributes
- For ASM-based Oracle, configure ASM user credentials separately

#### SQL Server
- Enable **MS-CDC** or **MS-Replication** for change capture
- For AlwaysOn Availability Groups, connect to the primary replica
- Monitor transaction log growth; ensure regular log backups
- Use `safeguardPolicy: EXCLUSIVE_AUTOMATIC_TRUNCATION` for managed CDC cleanup
- Set `readBackupOnly: Y` for read-from-backup scenarios

#### PostgreSQL
- **Self-managed PostgreSQL**: Set `wal_level = logical` directly in postgresql.conf
- **RDS / Aurora PostgreSQL**: Set `rds.logical_replication = 1` in the DB cluster parameter group (this auto-sets `wal_level`, `max_wal_senders`, `max_replication_slots`, and `max_connections`). Requires DB reboot.
- Configure replication slots: `max_replication_slots >= DMS tasks + 2` (self-managed only; RDS auto-sets this)
- Set `max_wal_senders >= DMS tasks + 2` (self-managed only; RDS auto-sets this)
- Set `wal_sender_timeout` appropriately: default is 30000ms (30s) for RDS. DMS requires minimum 10000ms if non-zero. Keep < 5 minutes for Multi-AZ failover safety. Set to 0 to disable (valid for DMS).
- **Note:** Aurora PostgreSQL ignores `wal_sender_timeout` — it is fixed by the service and cannot be configured. This setting applies to self-managed PostgreSQL and RDS PostgreSQL only.
- Monitor and clean inactive replication slots — if `max_slot_wal_keep_size` is set and a slot falls behind the current LSN by more than this size, DMS task fails due to WAL removal
- Use `pglogical` or `test_decoding` plugin for CDC (DMS uses pglogical if available, otherwise test_decoding)
- Set `heartbeatEnable: true` in endpoint settings to keep the replication slot active during low-traffic periods
- For Aurora PostgreSQL CDC, set `synchronous_commit = ON`

#### MySQL / MariaDB
- **Self-managed MySQL**: Set `binlog_expire_logs_seconds >= 86400` (MySQL 8.0+) or `expire_logs_days >= 1` (MySQL 5.7)
- **RDS / Aurora MySQL**: Call `CALL mysql.rds_set_configuration('binlog retention hours', 24);` — the `expire_logs_days` parameter does not apply to replication binlog retention on RDS
- Enable binary logging: `binlog_format = ROW`
- Set `binlog_row_image = full`
- For Aurora MySQL, enable `binlog_format` at cluster parameter group level
- Enable GTIDs when possible for consistent positioning

### Target Endpoint Best Practices

- **Disable foreign keys during full load**: Prevents constraint violations during bulk loading
- **Disable triggers during full load**: Prevents unintended side effects
- **Disable secondary indexes during full load**: Dramatically improves load performance; recreate after completion
- **Use batch apply mode for CDC**: Set `BatchApplyEnabled: true` for improved throughput
- **Configure appropriate connection timeouts**: Increase for high-latency network paths
- **S3 targets**: Set appropriate `maxFileSize`, `cdcPath`, and `timestampColumnName`
- **Redshift targets**: Use `acceptanydate: true` and configure `maxFileSize` for optimal COPY operations

### General Endpoint Best Practices

- **Test connectivity before starting tasks**: Use `aws dms test-connection`
- **Use SSL/TLS for all endpoint connections**: Set `SslMode: require` or `verify-ca`
- **Minimize network hops**: Place DMS instance in same VPC/AZ as source or target when possible
- **Use separate endpoints for full load vs CDC** if different optimization is needed
- **Store credentials in AWS Secrets Manager**: Reference via `SecretsManagerSecretId` in endpoint settings

---

## 3. Task Configuration

### Full Load Optimization

- **MaxFullLoadSubTasks**: Controls parallel table loading. Default: 8. Increase to 16-49 for large instance classes.
- **Partition large tables**: Use range-based parallel load for tables with > 100M rows:
  ```json
  {
    "rules": [{
      "rule-type": "table-settings",
      "rule-id": "1",
      "rule-name": "parallel-load-large-table",
      "object-locator": {"schema-name": "dbo", "table-name": "orders"},
      "parallel-load": {
        "type": "ranges",
        "columns": ["order_id"],
        "boundaries": [[1000000], [2000000], [3000000]]
      }
    }]
  }
  ```
- **CommitRate**: Increase from default 10,000 for large tables (e.g., 30,000-50,000)
- **LOB handling**:
  - **Limited LOB mode** — When max LOB size is known (best performance)
  - **Inline LOB mode** — For mixed small/large LOBs (DMS 3.4.7+)
  - **Full LOB mode** — Only when LOB sizes are unknown (slowest, most memory)
- **Table mapping**: Exclude unnecessary tables, archived data, and non-essential schemas

### CDC Optimization

- **BatchApplyEnabled**: `true` — Groups transactions for efficient target writes
- **BatchApplyPreserveTransaction**: `true` — Maintains transaction boundaries with batch apply
- **ParallelApplyThreads**: 4-32 based on target capacity and instance size
- **ParallelApplyBufferSize**: Increase for high-TPS workloads (default: 100)
- **CDCLatencySource/CDCLatencyTarget**: Monitor and alert when > acceptable thresholds
- **MemoryLimitTotal**: Set to 80% of instance memory for CDC buffer management
- **MemoryKeepTime**: Adjust based on transaction size patterns (default: 60 seconds)

### Data Validation Settings

- Enable for critical migrations: `EnableValidation: true`
- Use **validation-only tasks** for post-migration verification (DMS 3.4.6+)
- Schedule validation during low-traffic periods
- Set `ValidationPartialLobSize` for LOB column validation
- Configure `FailureMaxCount` to control when validation stops
- Be aware that validation increases source/target load and network traffic

### Table Mapping Examples

**Include specific schemas, exclude archive tables:**
```json
{
  "rules": [
    {
      "rule-type": "selection",
      "rule-id": "1",
      "rule-name": "include-production-schema",
      "object-locator": {"schema-name": "production", "table-name": "%"},
      "rule-action": "include"
    },
    {
      "rule-type": "selection",
      "rule-id": "2",
      "rule-name": "exclude-archive-tables",
      "object-locator": {"schema-name": "production", "table-name": "ARC_%"},
      "rule-action": "exclude"
    },
    {
      "rule-type": "selection",
      "rule-id": "3",
      "rule-name": "exclude-temp-tables",
      "object-locator": {"schema-name": "production", "table-name": "TMP_%"},
      "rule-action": "exclude"
    }
  ]
}
```

**Rename schema during migration:**
```json
{
  "rules": [
    {
      "rule-type": "transformation",
      "rule-id": "10",
      "rule-name": "rename-schema",
      "rule-action": "rename",
      "rule-target": "schema",
      "object-locator": {"schema-name": "legacy_app"},
      "value": "app"
    }
  ]
}
```

---

## 4. Performance Tuning

### Key CloudWatch Metrics to Monitor

**Replication Instance Metrics:**

| Metric | Description | Alert Threshold |
|---|---|---|
| CPUUtilization | Instance CPU usage | > 80% sustained |
| FreeableMemory | Available RAM | < 2 GB |
| SwapUsage | Swap space used | > 0 bytes |
| ReadIOPS / WriteIOPS | Disk I/O operations | Near provisioned limit |
| ReadLatency / WriteLatency | Disk latency | > 20ms |
| DiskQueueDepth | Pending I/O requests | > 10 sustained |
| NetworkTransmitThroughput | Outbound network | Near instance limit |
| NetworkReceiveThroughput | Inbound network | Near instance limit |

**Task Metrics:**

| Metric | Description | Alert Threshold |
|---|---|---|
| FullLoadThroughputBandwidthTarget | MB/s to target during full load | Declining trend |
| FullLoadThroughputRowsTarget | Rows/sec to target during full load | Below baseline |
| CDCIncomingChanges | Pending changes from source | > 10,000 sustained |
| CDCLatencySource | Seconds behind source | > 120 seconds |
| CDCLatencyTarget | Seconds behind target | > 120 seconds |
| CDCChangesMemorySource | Memory used for source CDC | > 80% of available |
| CDCChangesMemoryTarget | Memory used for target CDC | > 80% of available |
| CDCChangesDiskSource | Changes swapped to disk (source) | > 0 sustained |
| CDCChangesDiskTarget | Changes swapped to disk (target) | > 0 sustained |

### Performance Optimization Strategies

1. **Distribute tables across multiple tasks**: Separate large high-throughput tables from small tables
2. **Use parallel load with range partitioning**: For tables > 100M rows
3. **Optimize LOB settings**: Choose the most restrictive LOB mode your data allows
4. **Tune network**: Ensure sufficient bandwidth; use same-AZ placement when possible
5. **Minimize transformations**: Complex transformation rules increase CPU usage significantly
6. **Benchmark before production**: Run full-scale test migrations to validate performance
7. **Separate full load and CDC**: Use dedicated tasks for each phase when table counts are high
8. **Use appropriate commit intervals**: Larger commits = fewer transactions = higher throughput
9. **Monitor and address bottlenecks**: The slowest component (source, network, DMS, target) determines overall throughput

---

## 5. Cost Optimization

### Right-Sizing Strategies

1. **Phase-based sizing**: Use larger instances during full load; resize smaller for CDC steady-state
2. **Monitor utilization patterns**: If CPU < 40% and memory > 60% free consistently for 7+ days, downsize
3. **Consolidate tasks**: Run multiple small tasks on a single instance when total resource demand allows
4. **Delete idle instances**: Remove replication instances not actively running tasks
5. **Clean up after migration**: Terminate instances once migration and validation complete
6. **Remove debug logging in production**: Debug logging consumes disk and incurs storage costs

### DMS Serverless Evaluation

**When to use DMS Serverless:**
- Variable or unpredictable migration workloads
- Intermittent one-time migrations
- Unknown peak capacity requirements
- Cost-conscious environments with bursty traffic
- Development and testing environments

**When to use Provisioned instances:**
- Consistent, predictable throughput requirements
- Long-running CDC with stable change rates
- Maximum control over instance placement and configuration
- Workloads requiring specific instance features (e.g., Enhanced VPC Routing)

**Serverless configuration example:**
```bash
aws dms create-replication-config \
  --replication-config-identifier my-serverless-task \
  --source-endpoint-arn arn:aws:dms:us-east-1:123456789012:endpoint:SOURCE \
  --target-endpoint-arn arn:aws:dms:us-east-1:123456789012:endpoint:TARGET \
  --replication-type full-load-and-cdc \
  --compute-config '{
    "MinCapacityUnits": 1,
    "MaxCapacityUnits": 16,
    "MultiAZ": false,
    "PreferredMaintenanceWindow": "sun:10:00-sun:14:00",
    "ReplicationSubnetGroupId": "my-subnet-group",
    "VpcSecurityGroupIds": ["sg-12345678"]
  }'
```

### Multi-AZ Cost Trade-offs

| Scenario | Recommendation | Cost Impact |
|---|---|---|
| Production cutover with CDC | Multi-AZ | 2x instance cost (worth it for HA) |
| Long-running CDC (months) | Multi-AZ | 2x instance cost (protects continuity) |
| One-time full load | Single-AZ | 1x (restart acceptable if failure) |
| Dev/test migrations | Single-AZ | 1x (non-critical workload) |
| Short-term migration (< 1 week) | Single-AZ | 1x (limited exposure window) |

### Selective Data Migration

- **Filter by date range**: Migrate only recent/relevant data partitions
- **Exclude archived tables**: Use table mapping exclude rules for historical data
- **Migrate incrementally**: Phase large migrations to reduce peak resource needs
- **Use homogeneous data migration**: For same-engine migrations, DMS Homogeneous Data Migration is more cost-effective than standard DMS
- **Consider native tools**: For simple same-engine migrations, native database tools (pg_dump, mysqldump, Data Pump) may be free

---

## 6. Security Best Practices

### IAM Configuration

- **Use DMS service role**: Create `dms-vpc-role` and `dms-cloudwatch-logs-role` with minimum permissions
- **Use Secrets Manager**: Store endpoint credentials in AWS Secrets Manager; reference via endpoint settings
- **Restrict IAM policies**: Grant only required DMS, EC2, S3, KMS, and CloudWatch permissions
- **Use VPC endpoints**: Access S3, KMS, Secrets Manager, and CloudWatch without public internet

### Network Security

- **Security groups**: Allow only required ports (source DB port, target DB port) from DMS security group
- **No 0.0.0.0/0 rules**: Restrict to specific CIDR ranges or security group references
- **Private subnets**: Place DMS instances in private subnets; avoid public accessibility
- **NACLs**: Verify subnet NACLs allow bidirectional traffic on required ports
- **VPC peering / Transit Gateway**: Use for cross-VPC or cross-account migrations

### Encryption

- **In transit**: Enable SSL/TLS on all endpoint connections (`SslMode: require` or `verify-ca`)
- **At rest**: Use AWS KMS CMK for replication instance storage encryption
- **Certificate management**: Upload custom CA certificates for endpoints using `aws dms import-certificate`
- **Rotate regularly**: Update endpoint passwords and SSL certificates before expiration

### Audit and Compliance

- **CloudTrail**: Enable for all DMS API calls
- **CloudWatch Logs**: Enable task logging for audit trail
- **AWS Config**: Track DMS resource configuration changes
- **Resource tagging**: Tag all DMS resources for cost allocation and access control

---

## 7. Version Management and Deprecations

### Version Best Practices

- **Stay on supported versions**: Check AWS DMS release notes regularly for EOL announcements
- **Test upgrades in non-production first**: Validate task compatibility before upgrading production
- **Review release notes per version**: Each version includes bug fixes, new features, and behavioral changes
- **Plan upgrade windows**: Schedule during low-activity periods; tasks are briefly interrupted during upgrade
- **Use automatic minor version upgrades**: Enable `AutoMinorVersionUpgrade` for non-production instances

### Monitoring Deprecations

```bash
# Check available engine versions
aws dms describe-orderable-replication-instances \
  --query "OrderableReplicationInstances[*].EngineVersion" --output text | tr '\t' '
' | sort -u

# Check pending maintenance
aws dms describe-pending-maintenance-actions

# Check AWS Health events for DMS
aws health describe-events \
  --filter '{"services":["DMS"],"eventTypeCategories":["scheduledChange"]}'
```

### Upgrade Procedure

1. Identify target version from `describe-orderable-replication-instances`
2. Review release notes for breaking changes
3. Test in non-production with same task configurations
4. Schedule production upgrade during maintenance window
5. Stop tasks before upgrade (for clean restart)
6. Apply upgrade: `aws dms modify-replication-instance --engine-version <version>`
7. Verify instance status returns to "available"
8. Restart tasks and validate operation

---

## 8. Monitoring and Alerting

### Recommended CloudWatch Alarms

```bash
# Critical: High CPU utilization
aws cloudwatch put-metric-alarm \
  --alarm-name "DMS-HighCPU-<instance-id>" \
  --metric-name CPUUtilization \
  --namespace AWS/DMS \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 3 \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --alarm-actions <sns-topic-arn>

# Critical: Low freeable memory
aws cloudwatch put-metric-alarm \
  --alarm-name "DMS-LowMemory-<instance-id>" \
  --metric-name FreeableMemory \
  --namespace AWS/DMS \
  --statistic Average \
  --period 300 \
  --threshold 2147483648 \
  --comparison-operator LessThanThreshold \
  --evaluation-periods 3 \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --alarm-actions <sns-topic-arn>

# High: Swap usage detected
aws cloudwatch put-metric-alarm \
  --alarm-name "DMS-SwapUsage-<instance-id>" \
  --metric-name SwapUsage \
  --namespace AWS/DMS \
  --statistic Maximum \
  --period 300 \
  --threshold 104857600 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --alarm-actions <sns-topic-arn>

# High: CDC latency from source
aws cloudwatch put-metric-alarm \
  --alarm-name "DMS-CDCLatencySource-<task-id>" \
  --metric-name CDCLatencySource \
  --namespace AWS/DMS \
  --statistic Average \
  --period 300 \
  --threshold 120 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 3 \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --alarm-actions <sns-topic-arn>

# High: CDC latency to target
aws cloudwatch put-metric-alarm \
  --alarm-name "DMS-CDCLatencyTarget-<task-id>" \
  --metric-name CDCLatencyTarget \
  --namespace AWS/DMS \
  --statistic Average \
  --period 300 \
  --threshold 120 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 3 \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --alarm-actions <sns-topic-arn>

# Warning: Disk-based CDC caching (memory exhaustion indicator)
aws cloudwatch put-metric-alarm \
  --alarm-name "DMS-CDCDiskUsage-<task-id>" \
  --metric-name CDCChangesDiskSource \
  --namespace AWS/DMS \
  --statistic Maximum \
  --period 300 \
  --threshold 0 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 6 \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --alarm-actions <sns-topic-arn>
```

### Logging Best Practices

- **Enable CloudWatch Logs** for all production tasks
- **Use DEFAULT severity** for normal operations (SOURCE_UNLOAD, SOURCE_CAPTURE, TARGET_LOAD, TARGET_APPLY)
- **Enable DETAILED_DEBUG only during active troubleshooting** — significantly increases disk usage
- **Set log retention policies**: 30 days for production, 7 days for development
- **Key log components**:
  - `SOURCE_UNLOAD` — Full load reads from source
  - `SOURCE_CAPTURE` — CDC reads from source change stream
  - `TARGET_LOAD` — Full load writes to target
  - `TARGET_APPLY` — CDC writes to target
  - `TASK_MANAGER` — Overall task orchestration and status
  - `TABLES_MANAGER` — Table-level state management

---

## 9. Resilience and Recovery

### Task Recovery Strategies

- **Enable premigration assessment**: Run `start-replication-task-assessment-run` before starting tasks
- **Use table-level recovery**: Reload individual failed tables without restarting entire task
- **Implement checkpointing**: Resume CDC from last checkpoint after failure using `CdcStartPosition`
- **Store CDC start position**: Record source native start point (LSN/SCN/binlog position) for disaster recovery
- **Version control task configurations**: Store table mappings and task settings in source control

### High Availability Patterns

- **Multi-AZ deployment**: Automatic failover with minimal CDC position loss
- **Cross-region replication**: Use separate DMS instances in DR region with same CDC start position
- **Monitoring-driven recovery**: Use CloudWatch alarms to trigger automated recovery runbooks
- **Regular task assessment**: Schedule periodic `describe-replication-task-assessment-results` reviews

### Disaster Recovery Checklist

1. Document CDC resume positions for all active tasks
2. Maintain runbooks for task restart procedures
3. Test Multi-AZ failover quarterly
4. Verify backup endpoint connectivity paths
5. Keep task configurations in version control (table mappings, settings JSON)
6. Monitor replication slots (PostgreSQL) — remove inactive slots to prevent WAL disk exhaustion
7. Set up automated alerts for task state changes
