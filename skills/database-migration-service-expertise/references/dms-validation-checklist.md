
# AWS DMS Validation Checklist

## Overview

This checklist provides a structured approach to validating AWS DMS configurations, performing health assessments, and conducting operations reviews. Use this document with the AWS API MCP server (`awslabs.aws-api-mcp-server`) to programmatically assess customer environments.

---

## Section 1: Replication Instance Health Check

### 1.1 Instance Configuration Assessment

**Data Gathering Command:**
```bash
aws dms describe-replication-instances \
  --query "ReplicationInstances[*].{
    ID:ReplicationInstanceIdentifier,
    Class:ReplicationInstanceClass,
    Version:EngineVersion,
    MultiAZ:MultiAZ,
    Status:ReplicationInstanceStatus,
    Storage:AllocatedStorage,
    AZ:AvailabilityZone,
    VPC:ReplicationSubnetGroup.VpcId,
    PublicAccess:PubliclyAccessible,
    AutoUpgrade:AutoMinorVersionUpgrade,
    KmsKey:KmsKeyId
  }"
```

**Validation Criteria:**

| # | Check | Pass Criteria | Severity |
|---|---|---|---|
| 1.1.1 | Instance status | `ReplicationInstanceStatus` = "available" | Critical |
| 1.1.2 | Engine version current | On latest or latest-1 active (non-EOL) version | High |
| 1.1.3 | Instance class appropriate | CPU < 80% avg, Memory > 20% free over 7 days | High |
| 1.1.4 | Multi-AZ for production | `MultiAZ: true` for production/long-running CDC | Medium |
| 1.1.5 | Storage sufficient | Used storage < 80% of allocated | High |
| 1.1.6 | Not publicly accessible | `PubliclyAccessible: false` | High |
| 1.1.7 | KMS encryption enabled | `KmsKeyId` is set | Medium |
| 1.1.8 | Auto minor upgrade | `AutoMinorVersionUpgrade: true` (non-prod) | Low |
| 1.1.9 | VPC configured properly | Instance in expected VPC and subnet group | Critical |

### 1.2 Instance Performance Metrics

**Data Gathering Commands:**
```bash
# CPU Utilization - last 24 hours
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CPUUtilization \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 3600 \
  --statistics Average Maximum

# Freeable Memory - last 24 hours
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name FreeableMemory \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 3600 \
  --statistics Average Minimum

# Swap Usage - last 24 hours
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name SwapUsage \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 3600 \
  --statistics Average Maximum

# Disk Queue Depth - last 24 hours
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name DiskQueueDepth \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 3600 \
  --statistics Average Maximum

# Read/Write IOPS - last 24 hours
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name ReadIOPS \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 3600 \
  --statistics Average Maximum

aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name WriteIOPS \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 3600 \
  --statistics Average Maximum
```

**Threshold Evaluation:**

| # | Metric | OK | Warning | Critical | Remediation |
|---|---|---|---|---|---|
| 1.2.1 | CPUUtilization (Avg) | < 60% | 60-80% | > 80% | Scale up instance class |
| 1.2.2 | CPUUtilization (Max) | < 80% | 80-95% | > 95% | Scale up; reduce parallel tasks |
| 1.2.3 | FreeableMemory (Min) | > 4 GB | 2-4 GB | < 2 GB | Scale up or reduce LOB/parallelism |
| 1.2.4 | SwapUsage (Max) | 0 | 1-100 MB | > 100 MB | Immediate scale up required |
| 1.2.5 | DiskQueueDepth (Avg) | < 5 | 5-10 | > 10 | Increase IOPS / add storage |
| 1.2.6 | ReadLatency (Avg) | < 10ms | 10-20ms | > 20ms | Check storage performance |
| 1.2.7 | WriteLatency (Avg) | < 10ms | 10-20ms | > 20ms | Check storage performance |

---

## Section 2: Endpoint Validation

### 2.1 Endpoint Connectivity

**Data Gathering Commands:**
```bash
# List all endpoints
aws dms describe-endpoints \
  --query "Endpoints[*].{
    ID:EndpointIdentifier,
    Type:EndpointType,
    Engine:EngineName,
    Server:ServerName,
    Port:Port,
    SSL:SslMode,
    Status:Status,
    ARN:EndpointArn
  }"

# Test endpoint connectivity (run for each endpoint)
aws dms test-connection \
  --replication-instance-arn <instance-arn> \
  --endpoint-arn <endpoint-arn>

# Check connection status
aws dms describe-connections \
  --filter Name=endpoint-arn,Values=<endpoint-arn> \
  --query "Connections[*].{
    Endpoint:EndpointIdentifier,
    Instance:ReplicationInstanceIdentifier,
    Status:Status,
    LastFailure:LastFailureMessage
  }"
```

**Validation Criteria:**

| # | Check | Pass Criteria | Severity |
|---|---|---|---|
| 2.1.1 | Connection test succeeds | Status = "successful" | Critical |
| 2.1.2 | SSL/TLS enabled | `SslMode` != "none" | High |
| 2.1.3 | No recent connection failures | No failures in last 24 hours | Medium |
| 2.1.4 | Endpoint accessible from DMS VPC | Security groups allow traffic on DB port | Critical |
| 2.1.5 | Credentials valid | No authentication errors in connection test | Critical |
| 2.1.6 | Uses Secrets Manager | `SecretsManagerSecretId` configured | Medium |

### 2.2 Source Endpoint Configuration

| # | Database Engine | Check | Best Practice | Severity |
|---|---|---|---|---|
| 2.2.1 | Oracle | CDC method configured | Binary Reader for performance | High |
| 2.2.2 | Oracle | Supplemental logging | Enabled at DB and table level | Critical |
| 2.2.3 | Oracle | UNDO tablespace sized | Sufficient for full load duration | High |
| 2.2.4 | Oracle | Archive log mode | Enabled for CDC | Critical |
| 2.2.5 | SQL Server | CDC enabled | MS-CDC or MS-Replication configured | Critical |
| 2.2.6 | SQL Server | Transaction log space | Sufficient space, regular backups | High |
| 2.2.7 | SQL Server | Snapshot isolation | `ALLOW_SNAPSHOT_ISOLATION ON` | Medium |
| 2.2.8 | PostgreSQL | WAL level | Self-managed: `wal_level = logical`; RDS/Aurora: `rds.logical_replication = 1` | Critical |
| 2.2.9 | PostgreSQL | Replication slots | No inactive slots accumulating WAL; check `max_slot_wal_keep_size` risk | High |
| 2.2.10 | PostgreSQL | max_replication_slots | >= DMS tasks + 2 (self-managed; auto-set on RDS) | Medium |
| 2.2.11 | PostgreSQL | max_wal_senders | >= DMS tasks + 2 (self-managed; auto-set on RDS) | Medium |
| 2.2.12 | PostgreSQL | Heartbeat enabled | `heartbeatEnable: true` in endpoint settings | Medium |
| 2.2.13 | MySQL | Binary logging format | `binlog_format = ROW` | Critical |
| 2.2.14 | MySQL | Binlog retention | >= 24 hours retention | High |
| 2.2.15 | MySQL | Row image | `binlog_row_image = full` | High |

### 2.3 Target Endpoint Configuration

| # | Check | Best Practice | Severity |
|---|---|---|---|
| 2.3.1 | Foreign keys disabled during full load | Prevents constraint violations | High |
| 2.3.2 | Triggers disabled during full load | Prevents unintended side effects | High |
| 2.3.3 | Secondary indexes disabled during full load | Improves load performance | Medium |
| 2.3.4 | Batch apply configured for CDC | `BatchApplyEnabled: true` | Medium |
| 2.3.5 | Connection timeout adequate | Based on network latency | Low |
| 2.3.6 | Max file size for S3 targets | Appropriate for downstream processing | Medium |
| 2.3.7 | Error handling configured | `DataErrorPolicy` set appropriately | High |

---

## Section 3: Task Configuration Validation

### 3.1 Task Status and Health

**Data Gathering Commands:**
```bash
# Get task details and status
aws dms describe-replication-tasks \
  --query "ReplicationTasks[*].{
    ID:ReplicationTaskIdentifier,
    ARN:ReplicationTaskArn,
    Status:Status,
    Type:MigrationType,
    StartDate:ReplicationTaskStartDate,
    CDCStart:CdcStartPosition,
    CDCStop:CdcStopPosition,
    LastFailure:LastFailureMessage,
    StopReason:StopReason,
    Stats:ReplicationTaskStats
  }"

# Get table-level statistics for a specific task
aws dms describe-table-statistics \
  --replication-task-arn <task-arn> \
  --query "TableStatistics[*].{
    Schema:SchemaName,
    Table:TableName,
    State:TableState,
    Inserts:Inserts,
    Deletes:Deletes,
    Updates:Updates,
    DDLs:Ddls,
    FullLoadRows:FullLoadRows,
    FullLoadCondtnlChkFailedRows:FullLoadCondtnlChkFailedRows,
    FullLoadErrorRows:FullLoadErrorRows,
    ValidationState:ValidationState,
    ValidationSuspended:ValidationSuspendedRecords,
    ValidationFailed:ValidationFailedRecords
  }"
```

**Validation Criteria:**

| # | Check | Pass Criteria | Severity |
|---|---|---|---|
| 3.1.1 | Task status healthy | Status = "running" or "stopped" (not "failed", "error") | Critical |
| 3.1.2 | No tables in error state | All `TableState` != "Error" | High |
| 3.1.3 | Full load completed | All expected tables show "Table completed" | High |
| 3.1.4 | CDC replication active | CDCLatencySource < threshold | High |
| 3.1.5 | No validation failures | `ValidationState` = "Validated" or "Not enabled" | Medium |
| 3.1.6 | No suspended validations | `ValidationSuspendedRecords` = 0 | Medium |
| 3.1.7 | No full load error rows | `FullLoadErrorRows` = 0 | High |
| 3.1.8 | Task not stalled | Metrics show active processing in last 5 min | Critical |
| 3.1.9 | No recent failure messages | `LastFailureMessage` is empty | High |

### 3.2 Task Settings Validation

**Data Gathering Command:**
```bash
aws dms describe-replication-tasks \
  --filters Name=replication-task-id,Values=<task-id> \
  --query "ReplicationTasks[0].ReplicationTaskSettings"
```

**Validation Criteria:**

| # | Setting Path | Recommended Value | Rationale | Severity |
|---|---|---|---|---|
| 3.2.1 | `Logging.EnableLogging` | `true` | Required for troubleshooting | High |
| 3.2.2 | `Logging.LogComponents[].Severity` | `LOGGER_SEVERITY_DEFAULT` | DEBUG wastes disk in production | Medium |
| 3.2.3 | `FullLoadSettings.MaxFullLoadSubTasks` | 8-49 (by instance size) | Controls parallelism | Medium |
| 3.2.4 | `FullLoadSettings.CommitRate` | 10000-50000 | Balance speed vs memory | Medium |
| 3.2.5 | `TargetMetadata.BatchApplyEnabled` | `true` (for CDC) | Improves CDC throughput | Medium |
| 3.2.6 | `TargetMetadata.ParallelApplyThreads` | 4-32 (by target capacity) | Controls CDC parallelism | Medium |
| 3.2.7 | `ChangeProcessingTuning.BatchApplyPreserveTransaction` | `true` (if ordering matters) | Maintains transaction integrity | High |
| 3.2.8 | `ErrorBehavior.DataErrorPolicy` | `LOG_ERROR` or `SUSPEND_TABLE` | Prevents silent data loss | High |
| 3.2.9 | `ErrorBehavior.TableErrorPolicy` | `SUSPEND_TABLE` | Isolates failures per table | Medium |
| 3.2.10 | `ValidationSettings.EnableValidation` | `true` (critical migrations) | Ensures data accuracy | Medium |
| 3.2.11 | `ControlTablesSettings.ControlSchema` | Set to dedicated schema | Avoids conflicts with user schemas | Low |

### 3.3 Table Mapping Review

| # | Check | Best Practice | Severity |
|---|---|---|---|
| 3.3.1 | Unnecessary tables excluded | Reduce migration scope and resource usage | Medium |
| 3.3.2 | Archive/temp tables excluded | Tables matching ARC_*, TMP_*, ARCHIVE_* excluded | Low |
| 3.3.3 | Large tables use parallel load | Range-based partitioning for > 100M rows | High |
| 3.3.4 | Transformation rules minimal | Excessive transformations impact performance | Medium |
| 3.3.5 | Filter rules validated | Date ranges and column filters correct | High |
| 3.3.6 | Schema mapping correct | Source-to-target schema naming verified | Critical |
| 3.3.7 | LOB columns identified | Appropriate LOB mode selected per table | High |

---

## Section 4: CDC-Specific Validation

### 4.1 CDC Latency Monitoring

**Data Gathering Commands:**
```bash
# Source CDC latency - last 1 hour, 1-minute granularity
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CDCLatencySource \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Average Maximum

# Target CDC latency
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CDCLatencyTarget \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Average Maximum

# Incoming changes queue depth
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CDCIncomingChanges \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Average Maximum

# Memory-based CDC changes
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CDCChangesMemorySource \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Average Maximum

# Disk-based CDC changes (overflow indicator)
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CDCChangesDiskSource \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Average Maximum
```

**Threshold Evaluation:**

| # | Metric | OK | Warning | Critical | Remediation |
|---|---|---|---|---|---|
| 4.1.1 | CDCLatencySource (Avg) | < 30s | 30-120s | > 120s | Check source DB performance, network bandwidth |
| 4.1.2 | CDCLatencyTarget (Avg) | < 30s | 30-120s | > 120s | Scale target, enable batch apply, add threads |
| 4.1.3 | CDCIncomingChanges (Avg) | < 10,000 | 10K-100K | > 100K | Scale instance, increase parallelism |
| 4.1.4 | CDCChangesMemorySource | < 50% avail | 50-80% | > 80% | Scale up memory or reduce concurrent tasks |
| 4.1.5 | CDCChangesDiskSource | 0 | > 0 intermittent | > 0 sustained | Memory insufficient; immediate scale up |
| 4.1.6 | CDCChangesDiskTarget | 0 | > 0 intermittent | > 0 sustained | Target write bottleneck; optimize target |

### 4.2 CDC Configuration Checks

| # | Check | Best Practice | Severity |
|---|---|---|---|
| 4.2.1 | CDC start position documented | Required for recovery/restart | High |
| 4.2.2 | Source supports change capture | WAL/Redo/Binlog properly configured | Critical |
| 4.2.3 | Latency trend stable | Not increasing over time | High |
| 4.2.4 | Memory not swapping to disk | CDCChangesDisk* metrics at 0 | High |
| 4.2.5 | Transaction ordering correct | Based on application requirements | Medium |
| 4.2.6 | Batch apply configured | `BatchApplyEnabled: true` for throughput | Medium |
| 4.2.7 | Parallel apply configured | Threads set based on target capacity | Medium |

---

## Section 5: Cost Optimization Assessment

### 5.1 Resource Utilization Review

**Data Gathering Commands:**
```bash
# Instance utilization over 7 days (hourly)
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CPUUtilization \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --start-time $(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 3600 \
  --statistics Average Maximum

aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name FreeableMemory \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --start-time $(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 3600 \
  --statistics Average Minimum

# Check for idle instances (instances with no running tasks)
aws dms describe-replication-tasks \
  --filter Name=replication-instance-arn,Values=<instance-arn> \
  --query "ReplicationTasks[].{ TaskID:ReplicationTaskIdentifier, Status:Status, StopReason:StopReason, LastError:LastFailureMessage}"
```

**Cost Optimization Findings:**

| # | Finding | Recommendation | Estimated Savings |
|---|---|---|---|
| 5.1.1 | CPU avg < 30% for 7+ days | Downsize instance class by one size | 30-50% of instance cost |
| 5.1.2 | No running tasks for 7+ days | Delete instance (or stop if needed later) | 100% of instance cost |
| 5.1.3 | Memory avg > 70% free (7 days) | Consider smaller instance class | 20-40% of instance cost |
| 5.1.4 | Single task, small dataset, variable | Evaluate DMS Serverless | 40-60% typical savings |
| 5.1.5 | Multi-AZ on non-critical task | Switch to Single-AZ | ~50% of instance cost |
| 5.1.6 | Debug logging enabled in production | Disable debug; reduce storage allocation | 10-20% storage cost |
| 5.1.7 | Over-provisioned storage | Reduce allocated storage to used + 30% buffer | Storage cost reduction |
| 5.1.8 | Same-engine migration using DMS | Evaluate homogeneous migration or native tools | Significant (may eliminate DMS cost) |

### 5.2 Serverless Evaluation Criteria

| Criteria | DMS Serverless Recommended | Provisioned Recommended |
|---|---|---|
| Workload pattern | Intermittent, variable, bursty | Consistent, predictable |
| Migration duration | Short-term (days to weeks) | Long-term (months+) |
| Peak-to-trough ratio | > 3:1 | < 2:1 |
| Budget priority | Cost optimization | Performance optimization |
| Scaling needs | Auto-scaling essential | Fixed capacity acceptable |
| Instance customization | Standard settings sufficient | Advanced tuning required |

### 5.3 Homogeneous Migration Evaluation

| Source to Target | Consider Homogeneous DMS? | Alternative Native Tools |
|---|---|---|
| PostgreSQL to Aurora PostgreSQL | Yes (DMS Homogeneous) | pg_dump/restore + logical replication |
| MySQL to Aurora MySQL | Yes (DMS Homogeneous) | mysqldump + binlog replication |
| Oracle to RDS Oracle | Consider | Data Pump + GoldenGate/Streams |
| SQL Server to RDS SQL Server | Consider | Backup/Restore + Log Shipping |
| Any to Different Engine | No - Use standard DMS | N/A (heterogeneous requires DMS) |

---

## Section 6: Security Validation

### 6.1 Network Security

**Data Gathering Commands:**
```bash
# Get replication instance security groups
aws dms describe-replication-instances \
  --query "ReplicationInstances[*].{
    ID:ReplicationInstanceIdentifier,
    PublicAccess:PubliclyAccessible,
    SecurityGroups:VpcSecurityGroups[*].VpcSecurityGroupId,
    SubnetGroup:ReplicationSubnetGroup.ReplicationSubnetGroupIdentifier
  }"

# Verify security group rules (for each security group)
aws ec2 describe-security-groups \
  --group-ids <sg-id> \
  --query "SecurityGroups[*].{
    ID:GroupId,
    Name:GroupName,
    IngressRules:IpPermissions[*].{
      Protocol:IpProtocol,
      FromPort:FromPort,
      ToPort:ToPort,
      Sources:IpRanges[*].CidrIp
    },
    EgressRules:IpPermissionsEgress[*].{
      Protocol:IpProtocol,
      FromPort:FromPort,
      ToPort:ToPort,
      Destinations:IpRanges[*].CidrIp
    }
  }"
```

**Validation Criteria:**

| # | Check | Pass Criteria | Severity |
|---|---|---|---|
| 6.1.1 | No open ingress rules | No [0.0.0.0/0 or ::/0] IP address inbound rules | Critical |
| 6.1.2 | Minimal port exposure | Only source/target DB ports open | High |
| 6.1.3 | Instance not publicly accessible | `PubliclyAccessible: false` | High |
| 6.1.4 | VPC endpoints configured | S3, KMS, CloudWatch via VPC endpoints | Medium |
| 6.1.5 | Network ACLs aligned | Subnet NACLs allow DMS traffic | Medium |
| 6.1.6 | Security group source restriction | Rules reference specific SGs or CIDRs | High |

### 6.2 Encryption and Access Control

| # | Check | Best Practice | Severity |
|---|---|---|---|
| 6.2.1 | SSL/TLS on all endpoints | `SslMode: "require"` or `"verify-ca"` | High |
| 6.2.2 | KMS encryption at rest | Replication instance uses CMK | Medium |
| 6.2.3 | IAM roles used (not inline creds) | DMS service role properly configured | High |
| 6.2.4 | Secrets Manager for credentials | Endpoint passwords in Secrets Manager | Medium |
| 6.2.5 | Credential rotation | Last rotation < 90 days | Medium |
| 6.2.6 | CloudTrail enabled | DMS API calls audited | Medium |
| 6.2.7 | Least privilege IAM | Only required permissions granted | High |
| 6.2.8 | Resource tagging | All DMS resources tagged per policy | Low |

---

## Section 7: Version and Deprecation Assessment

### 7.1 Version Assessment

**Data Gathering Commands:**
```bash
# Get current instance versions
aws dms describe-replication-instances \
  --query "ReplicationInstances[*].{
    ID:ReplicationInstanceIdentifier,
    Version:EngineVersion,
    AutoUpgrade:AutoMinorVersionUpgrade,
    PendingVersion:PendingModifiedValues.EngineVersion
  }"

# Get all available versions
aws dms describe-orderable-replication-instances \
  --query "OrderableReplicationInstances[*].EngineVersion" \
  --output text | tr '\t' '
' | sort -Vu

# Check pending maintenance actions
aws dms describe-pending-maintenance-actions \
  --query "PendingMaintenanceActions[*].{
    Resource:ResourceIdentifier,
    Actions:PendingMaintenanceActionDetails[*].{
      Action:Action,
      AutoApplyDate:AutoAppliedAfterDate,
      CurrentApplyDate:CurrentApplyDate,
      Description:Description
    }
  }"
```

**Validation Criteria:**

| # | Check | Pass Criteria | Severity |
|---|---|---|---|
| 7.1.1 | Running supported version | Not end-of-life or past deprecation date | Critical |
| 7.1.2 | On latest or latest-1 active version | Stay current for bug fixes and features | High |
| 7.1.3 | No pending forced maintenance | All maintenance is optional or scheduled | Medium |
| 7.1.4 | Source/target engine compatible | Verified against DMS compatibility matrix | Critical |
| 7.1.5 | Known issues reviewed | No unpatched critical bugs in current version | High |
| 7.1.6 | Upgrade tested in non-prod | Validation completed before production upgrade | High |

### 7.2 Deprecation Monitoring

**Data Gathering Commands:**
```bash
# Check AWS Health events for DMS scheduled changes
aws health describe-events \
  --filter '{
    "services": ["DMS"],
    "eventTypeCategories": ["scheduledChange"],
    "eventStatusCodes": ["open", "upcoming"]
  }'

# Check for deprecated instance classes (older generations)
aws dms describe-orderable-replication-instances \
  --query "OrderableReplicationInstances[?starts_with(ReplicationInstanceClass, 'dms.t2') || starts_with(ReplicationInstanceClass, 'dms.c4') || starts_with(ReplicationInstanceClass, 'dms.r4')]"
```

**Deprecation Checks:**

| # | Check | Action Required | Severity |
|---|---|---|---|
| 7.2.1 | Pending engine version deprecation | Plan upgrade within 90 days | High |
| 7.2.2 | Pending forced maintenance | Schedule maintenance window proactively | Medium |
| 7.2.3 | Using deprecated instance class | Migrate to current-generation (c5/r5/c6i/r6i) | High |
| 7.2.4 | SSL certificate expiring | Rotate certificate before expiration date | Critical |
| 7.2.5 | Source/target DB version EOL | Plan database upgrade path | High |
| 7.2.6 | Deprecated DMS features in use | Review release notes for alternatives | Medium |

---

## Section 8: Troubleshooting Workflows

### 8.1 Common Issues Quick Reference

| Issue | Diagnostic Commands | Common Cause | Resolution |
|---|---|---|---|
| Task failed to start | Check task logs; `describe-replication-tasks` | Endpoint connectivity or credentials | Test connection; verify credentials |
| High CDC latency | Check CDCLatencySource/Target metrics | Insufficient resources or slow target | Scale up; enable batch apply |
| Tables in error state | `describe-table-statistics` | DDL changes or data type issues | Reload table; check compatibility |
| Memory pressure | Check FreeableMemory, SwapUsage | Too many parallel tasks or LOBs | Reduce parallelism; scale up |
| Disk full | Check storage metrics and log size | Debug logging or long CDC cache | Disable debug; add storage |
| Validation failures | Check ValidationState per table | Data type differences or timing | Review differences; re-validate |
| Network timeout | Check connection test results | Security groups, routing, or DNS | Verify network path; check SGs |
| Slow full load | Check FullLoadThroughputRows | Table size, LOBs, or parallelism | Increase CommitRate; parallel load |
| Task keeps stopping | Check StopReason and LastFailureMessage | Resource exhaustion or source issues | Address root cause per error |
| Data mismatch | Run validation-only task | Triggers, defaults, or CDC gaps | Disable triggers; reload affected tables |

### 8.2 Structured Troubleshooting Workflow

#### Step 1: Gather Context

```bash
# Get task status and last error
aws dms describe-replication-tasks \
  --filters Name=replication-task-id,Values=<task-id> \
  --query "ReplicationTasks[0].{
    Status:Status,
    LastFailure:LastFailureMessage,
    StopReason:StopReason,
    Type:MigrationType,
    StartDate:ReplicationTaskStartDate
  }"

# Get replication instance details
aws dms describe-replication-instances \
  --filters Name=replication-instance-id,Values=<instance-id> \
  --query "ReplicationInstances[0].{
    Class:ReplicationInstanceClass,
    Version:EngineVersion,
    Status:ReplicationInstanceStatus,
    MultiAZ:MultiAZ,
    Storage:AllocatedStorage
  }"
```

#### Step 2: Check Resources

```bash
# CPU, Memory, Swap, Disk - last 6 hours
for metric in CPUUtilization FreeableMemory SwapUsage DiskQueueDepth ReadIOPS WriteIOPS; do
  aws cloudwatch get-metric-statistics \
    --namespace AWS/DMS \
    --metric-name $metric \
    --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
    --start-time $(date -u -d '6 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
    --period 300 \
    --statistics Average Maximum Minimum
done
```

#### Step 3: Review Task Logs

```bash
# Search for errors in the last 24 hours
# NOTE: DMS log group = dms-tasks-<replication-instance-id>
#       DMS log stream = dms-task-<task-id>
aws logs filter-log-events \
  --log-group-name "dms-tasks-<replication-instance-id>" \
  --log-stream-name-prefix "dms-task-<task-id>" \
  --filter-pattern "ERROR" \
  --start-time $(date -u -d '24 hours ago' +%s)000 \
  --limit 50

# Search for warnings
aws logs filter-log-events \
  --log-group-name "dms-tasks-<replication-instance-id>" \
  --log-stream-name-prefix "dms-task-<task-id>" \
  --filter-pattern "WARN" \
  --start-time $(date -u -d '24 hours ago' +%s)000 \
  --limit 50

# Search for specific table issues
aws logs filter-log-events \
  --log-group-name "dms-tasks-<replication-instance-id>" \
  --log-stream-name-prefix "dms-task-<task-id>" \
  --filter-pattern "<table-name>" \
  --start-time $(date -u -d '24 hours ago' +%s)000 \
  --limit 50

# Search for memory-related messages
aws logs filter-log-events \
  --log-group-name "dms-tasks-<replication-instance-id>" \
  --log-stream-name-prefix "dms-task-<task-id>" \
  --filter-pattern "memory" \
  --start-time $(date -u -d '24 hours ago' +%s)000 \
  --limit 20
```

#### Step 4: Validate Endpoints

```bash
# Test source connectivity
aws dms test-connection \
  --replication-instance-arn <instance-arn> \
  --endpoint-arn <source-endpoint-arn>

# Test target connectivity
aws dms test-connection \
  --replication-instance-arn <instance-arn> \
  --endpoint-arn <target-endpoint-arn>

# Check recent connection history
aws dms describe-connections \
  --filter Name=replication-instance-arn,Values=<instance-arn> \
  --query "Connections[*].{
    Endpoint:EndpointIdentifier,
    Status:Status,
    LastFailure:LastFailureMessage
  }"
```

#### Step 5: Check Table-Level Status

```bash
# Get all table states
aws dms describe-table-statistics \
  --replication-task-arn <task-arn> \
  --query "TableStatistics[?TableState=='Error' || TableState=='Before load' || ValidationState=='Failed'].{
    Schema:SchemaName,
    Table:TableName,
    State:TableState,
    ErrorRows:FullLoadErrorRows,
    ValidationState:ValidationState,
    ValidationFailed:ValidationFailedRecords
  }"
```

#### Step 6: Check for Known Issues

```bash
# Get current DMS version
aws dms describe-replication-instances \
  --filters Name=replication-instance-id,Values=<instance-id> \
  --query "ReplicationInstances[0].EngineVersion"

# Use AWS Documentation MCP server to look up known issues for this version
# Search: "DMS <version> known issues" and "DMS <version> release notes"
```

### 8.3 Issue-Specific Diagnostic Procedures

#### High CDC Latency Troubleshooting

```bash
# 1. Determine if bottleneck is source or target
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CDCLatencySource \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average

aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CDCLatencyTarget \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average

# 2. Check if changes are queuing in memory or disk
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CDCIncomingChanges \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average Maximum

# 3. Check DMS instance resources
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CPUUtilization \
  --dimensions Name=ReplicationInstanceIdentifier,Value=<instance-id> \
  --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average Maximum
```

**Decision Matrix for CDC Latency:**

| CDCLatencySource | CDCLatencyTarget | CPU | Memory | Diagnosis |
|---|---|---|---|---|
| High | Low | Normal | Normal | Source read bottleneck (network or source DB) |
| Low | High | Normal | Normal | Target write bottleneck (scale target or batch apply) |
| High | High | High | Low | DMS instance under-sized (scale up) |
| High | High | Normal | Normal | Network bandwidth constraint |
| Low | Low | Normal | Normal | No issue (within thresholds) |

#### Task Failure Troubleshooting

```bash
# 1. Get failure details
aws dms describe-replication-tasks \
  --filters Name=replication-task-id,Values=<task-id> \
  --query "ReplicationTasks[0].{
    Status:Status,
    LastFailure:LastFailureMessage,
    StopReason:StopReason,
    Stats:ReplicationTaskStats
  }"

# 2. Check if specific tables caused the failure
aws dms describe-table-statistics \
  --replication-task-arn <task-arn> \
  --query "TableStatistics[?TableState=='Error'].{
    Schema:SchemaName,
    Table:TableName,
    ErrorRows:FullLoadErrorRows,
    CondCheckFailed:FullLoadCondtnlChkFailedRows
  }"

# 3. Get recent error logs
aws logs filter-log-events \
  --log-group-name "dms-tasks-<replication-instance-id>" \
  --log-stream-name-prefix "dms-task-<task-id>" \
  --filter-pattern "ERROR" \
  --start-time $(date -u -d '1 hour ago' +%s)000 \
  --limit 20

# 4. Check for OOM or resource issues
aws logs filter-log-events \
  --log-group-name "dms-tasks-<replication-instance-id>" \
  --log-stream-name-prefix "dms-task-<task-id>" \
  --filter-pattern "?\"out of memory\" ?\"OOM\" ?\"insufficient\" ?\"exceeded\"" \
  --start-time $(date -u -d '24 hours ago' +%s)000 \
  --limit 10
```

#### Data Validation Failure Troubleshooting

```bash
# 1. Identify tables with validation failures
aws dms describe-table-statistics \
  --replication-task-arn <task-arn> \
  --query "TableStatistics[?ValidationState!='Validated' && ValidationState!='Not enabled'].{
    Schema:SchemaName,
    Table:TableName,
    State:ValidationState,
    Pending:ValidationPendingRecords,
    Failed:ValidationFailedRecords,
    Suspended:ValidationSuspendedRecords
  }"

# 2. Check if CDC is caught up (validation requires CDC to be current)
aws cloudwatch get-metric-statistics \
  --namespace AWS/DMS \
  --metric-name CDCLatencyTarget \
  --dimensions Name=ReplicationTaskIdentifier,Value=<task-id> \
  --start-time $(date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Average

# 3. Check for active DDL operations on source
aws logs filter-log-events \
  --log-group-name "dms-tasks-<replication-instance-id>" \
  --log-stream-name-prefix "dms-task-<task-id>" \
  --filter-pattern "DDL" \
  --start-time $(date -u -d '24 hours ago' +%s)000 \
  --limit 20
```

**Common Validation Failure Causes:**

| Cause | Indicator | Resolution |
|---|---|---|
| Active CDC lag | CDCLatencyTarget > 0 during validation | Wait for CDC to catch up; re-validate |
| Triggers on target | Unexpected row counts | Disable triggers; reload and re-validate |
| Default values differ | Consistent mismatches on specific columns | Align default values or exclude from validation |
| Timezone differences | Timestamp columns show offset | Align timezone settings between source/target |
| Precision differences | Numeric/decimal column mismatches | Match precision/scale on target schema |
| Active DML during validation | Intermittent failures | Schedule validation during low-traffic window |

---

## Section 9: Premigration Assessment

### 9.1 Running Premigration Assessments

**Data Gathering Commands:**
```bash
# Start a premigration assessment
aws dms start-replication-task-assessment-run \
  --replication-task-arn <task-arn> \
  --service-access-role-arn <dms-assessment-role-arn> \
  --result-location-bucket <s3-bucket-name> \
  --result-location-folder dms-assessments

# Check assessment status
aws dms describe-replication-task-assessment-runs \
  --filters Name=replication-task-arn,Values=<task-arn> \
  --query "ReplicationTaskAssessmentRuns[0].{
    Status:Status,
    ResultLocation:ResultLocationBucket,
    AssessmentResults:AssessmentProgress
  }"

# Get individual assessment results
aws dms describe-replication-task-individual-assessments \
  --filters Name=replication-task-assessment-run-arn,Values=<assessment-run-arn> \
  --query "ReplicationTaskIndividualAssessments[*].{
    Name:IndividualAssessmentName,
    Status:Status
  }"
```

### 9.2 Key Assessment Checks

| Assessment | What It Validates | Impact if Failed |
|---|---|---|
| unsupported-data-types-in-source | Source data types DMS can migrate | Data loss or task failure |
| full-lob-not-nullable-at-target | LOB columns with NOT NULL at target | Full load errors |
| source-engine-oracle-cdc-redo-check | Oracle redo log configuration | CDC will not start |
| target-engine-check | Target engine compatibility | Task configuration errors |
| source-postgres-cdc-logical-replication | PostgreSQL logical replication setup | CDC will not capture changes |
| source-mysql-binlog-check | MySQL binary log configuration | CDC will not start |
| source-mssql-cdc-check | SQL Server CDC configuration | CDC will not capture changes |
| large-objects-check | LOB handling requirements | Performance issues or data truncation |

---

## Section 10: Operations Review Report Template

### 10.1 Assessment Summary Format

```
============================================================
AWS DMS OPERATIONS REVIEW REPORT
============================================================

Date:           [Assessment Date]
Environment:    [Production / Staging / Development]
Account ID:     [AWS Account ID]
Region:         [AWS Region]
Assessed By:    AWS DMS Advisor Agent

============================================================
EXECUTIVE SUMMARY
============================================================

Total Replication Instances:  [N]
Total Replication Tasks:      [N]
  - Running:                  [N]
  - Stopped:                  [N]
  - Failed:                   [N]
Total Endpoints:              [N]

Overall Health Score:         [X / 100]

Critical Findings:            [N]
High-Priority Findings:       [N]
Medium-Priority Findings:     [N]
Low-Priority Findings:        [N]

============================================================
CRITICAL FINDINGS (Immediate Action Required)
============================================================

[#] [Finding Title]
    Severity:    CRITICAL
    Resource:    [Resource identifier]
    Evidence:    [Specific metric or configuration value]
    Impact:      [What happens if not addressed]
    Remediation: [Specific steps to fix]
    CLI Command: [AWS CLI command to remediate]

============================================================
HIGH-PRIORITY RECOMMENDATIONS
============================================================

[#] [Recommendation Title]
    Severity:    HIGH
    Resource:    [Resource identifier]
    Current:     [Current value/state]
    Recommended: [Target value/state]
    Rationale:   [Why this matters]
    Steps:       [How to implement]

============================================================
COST OPTIMIZATION OPPORTUNITIES
============================================================

[#] [Opportunity Title]
    Resource:          [Resource identifier]
    Current Cost:      [Estimated current monthly cost]
    Projected Savings: [Estimated monthly savings]
    Recommendation:    [Specific action]
    Risk Level:        [Low / Medium / High]

============================================================
BEST PRACTICES COMPLIANCE
============================================================

Category                    | Status | Score
----------------------------|--------|------
Instance Health             | [✅/⚠️/❌] | [X/25]
Task Health                 | [✅/⚠️/❌] | [X/25]
Endpoint Security           | [✅/⚠️/❌] | [X/20]
Performance                 | [✅/⚠️/❌] | [X/15]
Cost Efficiency             | [✅/⚠️/❌] | [X/15]

============================================================
NEXT STEPS
============================================================

1. [Highest priority action with timeline]
2. [Second priority action with timeline]
3. [Third priority action with timeline]

============================================================
APPENDIX: DETAILED METRICS
============================================================

[Raw metrics data, instance configurations, task settings]
```

### 10.2 Health Scoring Methodology

The health score uses a **5-category, 100-point deduction model** as defined in the main SKILL.md Health Score Formula. Start at 100 and deduct points per finding.

| Category | Max Points | Scope |
|---|---|---|
| Instance Health | 25 | Status, version, Multi-AZ, public access, storage, encryption |
| Task Health | 25 | Task status, table errors, CDC latency, logging |
| Endpoint Security | 20 | SSL mode, Secrets Manager, connectivity, SG rules |
| Performance | 15 | CPU, memory, swap, disk queue, CDC throughput |
| Cost Efficiency | 15 | Right-sizing, idle resources, Multi-AZ justification |

**Scoring Rules:**
- Start each category at its maximum points
- Deduct per specific finding (see SKILL.md "Deduction Rules" tables for exact values)
- Category floor is 0 (cannot go negative)
- Overall score = sum of all category scores (0–100)

**Score Interpretation:**

| Score | Rating | Action |
|---|---|---|
| 90-100 | 🟢 Excellent | Minor optimizations only |
| 75-89 | 🟡 Good | Address warnings at next opportunity |
| 60-74 | 🟠 Needs Attention | Plan remediation within 1-2 weeks |
| 40-59 | 🔴 Poor | Immediate action required on critical items |
| 0-39 | ⚫ Critical | Migration at risk — escalate immediately |

---

## Section 11: Automated Remediation Commands

### 11.1 Common Remediation Actions

**Scale up replication instance:**
```bash
aws dms modify-replication-instance \
  --replication-instance-arn <instance-arn> \
  --replication-instance-class dms.r5.xlarge \
  --apply-immediately
```

**Enable Multi-AZ:**
```bash
aws dms modify-replication-instance \
  --replication-instance-arn <instance-arn> \
  --multi-az \
  --apply-immediately
```

**Add storage:**
```bash
aws dms modify-replication-instance \
  --replication-instance-arn <instance-arn> \
  --allocated-storage 200 \
  --apply-immediately
```

**Upgrade engine version:**
```bash
aws dms modify-replication-instance \
  --replication-instance-arn <instance-arn> \
  --engine-version 3.5.3 \
  --allow-major-version-upgrade \
  --apply-immediately
```

**Reload a failed table:**
```bash
aws dms reload-tables \
  --replication-task-arn <task-arn> \
  --tables-to-reload SchemaName=<schema>,TableName=<table> \
  --reload-option data-reload
```

**Stop and restart task with new settings:**
```bash
# Stop task
aws dms stop-replication-task \
  --replication-task-arn <task-arn>

# Wait for stopped status, then modify
aws dms modify-replication-task \
  --replication-task-arn <task-arn> \
  --replication-task-settings file://updated-task-settings.json

# Resume from where it left off
aws dms start-replication-task \
  --replication-task-arn <task-arn> \
  --start-replication-task-type resume-processing
```

**Enable SSL on endpoint:**
```bash
aws dms modify-endpoint \
  --endpoint-arn <endpoint-arn> \
  --ssl-mode require \
  --certificate-arn <certificate-arn>
```

**Delete idle replication instance:**
```bash
# First verify no tasks are using it
aws dms describe-replication-tasks \
  --filter Name=replication-instance-arn,Values=<instance-arn> \
  --query "ReplicationTasks[*].ReplicationTaskIdentifier"

# If empty, safe to delete
aws dms delete-replication-instance \
  --replication-instance-arn <instance-arn>
```

### 11.2 Batch Assessment Script

```bash
#!/bin/bash
# DMS Environment Assessment Script
# Run this to gather all data needed for a comprehensive review

REGION=${1:-us-east-1}
OUTPUT_DIR="dms-assessment-$(date +%Y%m%d-%H%M%S)"
mkdir -p $OUTPUT_DIR

echo "=== Gathering DMS Assessment Data ==="
echo "Region: $REGION"
echo "Output: $OUTPUT_DIR"

# Replication Instances
echo "[1/6] Collecting replication instance data..."
aws dms describe-replication-instances \
  --region $REGION \
  --output json > "$OUTPUT_DIR/replication-instances.json"

# Replication Tasks
echo "[2/6] Collecting replication task data..."
aws dms describe-replication-tasks \
  --region $REGION \
  --output json > "$OUTPUT_DIR/replication-tasks.json"

# Endpoints
echo "[3/6] Collecting endpoint data..."
aws dms describe-endpoints \
  --region $REGION \
  --output json > "$OUTPUT_DIR/endpoints.json"

# Connections
echo "[4/6] Collecting connection data..."
aws dms describe-connections \
  --region $REGION \
  --output json > "$OUTPUT_DIR/connections.json"

# Pending Maintenance
echo "[5/6] Collecting maintenance data..."
aws dms describe-pending-maintenance-actions \
  --region $REGION \
  --output json > "$OUTPUT_DIR/pending-maintenance.json"

# Table Statistics (for each running task)
echo "[6/6] Collecting table statistics..."
for task_arn in $(aws dms describe-replication-tasks \
  --region $REGION \
  --query "ReplicationTasks[?Status=='running'].ReplicationTaskArn" \
  --output text); do
  task_id=$(echo $task_arn | rev | cut -d: -f1 | rev)
  aws dms describe-table-statistics \
    --region $REGION \
    --replication-task-arn $task_arn \
    --output json > "$OUTPUT_DIR/tables-${task_id}.json"
done

echo "=== Assessment data collected in $OUTPUT_DIR ==="
echo "Files:"
ls -la $OUTPUT_DIR/
```

---

## Section 12: Periodic Review Schedule

### Recommended Review Cadence

| Review Type | Frequency | Sections to Cover | Typical Duration |
|---|---|---|---|
| Quick Health Check | Daily (automated) | 1.2, 4.1 | 5 minutes |
| Task Status Review | Weekly | 3.1, 3.2, 4.1, 4.2 | 15 minutes |
| Full Operations Review | Monthly | All sections | 60 minutes |
| Cost Optimization Review | Quarterly | 5.1, 5.2, 5.3 | 30 minutes |
| Security Audit | Quarterly | 6.1, 6.2 | 30 minutes |
| Version/Deprecation Check | Monthly | 7.1, 7.2 | 15 minutes |
| Premigration Assessment | Before each new task | 9.1, 9.2 | 20 minutes |

### Automated Monitoring Recommendations

| Monitoring Type | Implementation | Alert Channel |
|---|---|---|
| Instance health metrics | CloudWatch Alarms | SNS / PagerDuty |
| Task state changes | EventBridge rules | SNS / Slack |
| CDC latency thresholds | CloudWatch Alarms | SNS / PagerDuty |
| Version deprecation | AWS Health events | Email / SNS |
| Cost anomalies | AWS Cost Anomaly Detection | Email / SNS |
| Security findings | AWS Config rules | SecurityHub / SNS