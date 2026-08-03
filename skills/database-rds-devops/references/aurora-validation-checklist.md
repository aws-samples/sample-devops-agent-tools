# Aurora Operational Excellence Validation Checklist

Comprehensive validation framework for Aurora MySQL and Aurora PostgreSQL, organized by operational excellence pillars.

## Monitoring & Observability Checks

| # | Check ID | Description | AWS CLI Call | Severity | Validation Criteria | Remediation |
|---|----------|-------------|-------------|----------|---------------------|-------------|
| 1 | no-cloudwatch-metrics | Cluster monitored with CloudWatch | `aws cloudwatch get-metric-statistics` | WARN | Verify datapoints exist for last hour | Enable Enhanced Monitoring on all instances |
| 2 | no-performance-insights | Performance Insights enabled | `aws rds describe-db-instances` | WARN | Check PerformanceInsightsEnabled=true on all instances | Enable PI on all cluster instances |
| 3 | no-enhanced-monitoring | Enhanced Monitoring configured | `aws rds describe-db-instances` | WARN | Check MonitoringInterval ≤60 on all instances | Configure monitoring on all instances |
| 4 | insufficient-pi-retention | Performance Insights retention | `aws rds describe-db-instances` | LOW | Check PerformanceInsightsRetentionPeriod ≥465 | Increase retention to 465+ days |
| 5 | no-cloudwatch-logs | CloudWatch Logs integration | `aws logs describe-log-groups` | WARN | Verify log groups exist for /aws/rds/cluster/ | Enable log exports in cluster parameter group |

## Alerting & Incident Response Checks

| # | Check ID | Description | AWS CLI Call | Severity | Validation Criteria | Remediation |
|---|----------|-------------|-------------|----------|---------------------|-------------|
| 6 | no-cpu-alarms | Alarms for high CPU utilization | `aws cloudwatch describe-alarms` | FAIL | Alarm exists for CPUUtilization > 80% | Create CloudWatch alarm with SNS notification |
| 7 | no-connection-alarms | Alarms for database connections | `aws cloudwatch describe-alarms` | WARN | Alarm exists for DatabaseConnections | Set threshold based on instance class |
| 8 | no-replica-lag-alarms | Alarms for replica lag | `aws cloudwatch describe-alarms` | FAIL | Alarm exists for AuroraReplicaLag > 1000ms | Create alarm for replica lag monitoring |
| 9 | no-memory-alarms | Alarms for memory pressure | `aws cloudwatch describe-alarms` | WARN | Alarm exists for FreeableMemory < 1GB | Create alarm with SNS notification |
| 10 | no-volume-iops-alarms | Alarms for volume IOPS | `aws cloudwatch describe-alarms` | WARN | Alarms exist for VolumeReadIOPs/VolumeWriteIOPs | Set baseline thresholds for IOPS |

## Automation & Change Management Checks

| # | Check ID | Description | AWS CLI Call | Severity | Validation Criteria | Remediation |
|---|----------|-------------|-------------|----------|---------------------|-------------|
| 11 | no-automated-backups | Automated backups configured | `aws rds describe-db-clusters` | FAIL | Check BackupRetentionPeriod > 0 | Enable automated backups |
| 12 | insufficient-backup-retention | Backup retention period | `aws rds describe-db-clusters` | WARN | Check BackupRetentionPeriod ≥7 | Increase retention to 7-35 days |
| 13 | no-maintenance-window | Maintenance window configured | `aws rds describe-db-clusters` | LOW | Check PreferredMaintenanceWindow set | Configure maintenance window |
| 14 | auto-minor-version-disabled | Auto minor version upgrades | `aws rds describe-db-instances` | LOW | Check AutoMinorVersionUpgrade=true on all instances | Enable auto minor version upgrades |
| 15 | outdated-major-version | Major version currency | `aws rds describe-db-engine-versions` | WARN | Current major = latest major | Plan major version upgrade |
| 16 | outdated-minor-version | Minor version currency | `aws rds describe-db-engine-versions` | LOW | Current minor = latest minor | Schedule minor version upgrade |
| 17 | no-backtrack-aurora-mysql | Backtrack enabled (Aurora MySQL) | `aws rds describe-db-clusters` | LOW | Check BacktrackWindow > 0 | Enable backtrack for point-in-time recovery |

## Security & Compliance Checks

| # | Check ID | Description | AWS CLI Call | Severity | Validation Criteria | Remediation |
|---|----------|-------------|-------------|----------|---------------------|-------------|
| 18 | no-encryption-at-rest | Storage encryption enabled | `aws rds describe-db-clusters` | FAIL | Check StorageEncrypted=true | Create encrypted snapshot and restore |
| 19 | no-iam-authentication | IAM database authentication | `aws rds describe-db-clusters` | WARN | Check IAMDatabaseAuthenticationEnabled=true | Enable IAM authentication |
| 20 | public-accessibility | Public accessibility disabled | `aws rds describe-db-instances` | FAIL | Check PubliclyAccessible=false on all instances | Disable public accessibility |
| 21 | no-deletion-protection | Deletion protection enabled | `aws rds describe-db-clusters` | WARN | Check DeletionProtection=true | Enable deletion protection |
| 22 | weak-security-groups | Security group configuration | `aws ec2 describe-security-groups` | WARN | Verify no 0.0.0.0/0 ingress rules | Restrict security group rules |
| 23 | no-encryption-in-transit | SSL/TLS enforcement | `aws rds describe-db-cluster-parameters` | WARN | Check cluster parameter group for SSL enforcement | Configure SSL/TLS parameters |

## High Availability & Scaling Checks

| # | Check ID | Description | AWS CLI Call | Severity | Validation Criteria | Remediation |
|---|----------|-------------|-------------|----------|---------------------|-------------|
| 24 | no-multi-az-readers | Multi-AZ reader distribution | `aws rds describe-db-instances` | WARN | Verify at least one reader in different AZ from writer | Create readers in multiple AZs |
| 25 | no-auto-scaling | Aurora Auto Scaling configured | `aws application-autoscaling describe-scalable-targets` | WARN | Check scalable targets exist for read replica count | Configure Aurora Auto Scaling |
| 26 | insufficient-reader-capacity | Adequate reader capacity | `aws rds describe-db-instances` | LOW | Verify reader count matches workload requirements | Add read replicas for read scaling |
| 27 | no-global-database | Global Database for DR | `aws rds describe-global-clusters` | LOW | Check if Global Database configured for multi-region DR | Evaluate Global Database for DR requirements |

## Cost Optimization Checks

| # | Check ID | Description | AWS CLI Call | Severity | Validation Criteria | Remediation |
|---|----------|-------------|-------------|----------|---------------------|-------------|
| 28 | oversized-instances | Instance right-sizing | `aws cloudwatch get-metric-statistics` | LOW | Check CPU < 40% consistently across all instances | Downsize instance classes |
| 29 | no-reserved-instances | Reserved instance usage | `aws rds describe-reserved-db-instances` | LOW | Check for RI coverage | Purchase RIs for steady workloads |
| 30 | inefficient-serverless-config | Aurora Serverless v2 optimization | `aws rds describe-db-clusters` | LOW | Verify min/max ACU settings match workload patterns | Optimize ACU configuration |
| 31 | no-io-optimization | I/O optimization evaluation | `aws cloudwatch get-metric-statistics` | LOW | Compare I/O costs vs I/O-Optimized pricing | Consider Aurora I/O-Optimized for high I/O workloads |

## Tagging & Organization Checks

| # | Check ID | Description | AWS CLI Call | Severity | Validation Criteria | Remediation |
|---|----------|-------------|-------------|----------|---------------------|-------------|
| 32 | missing-operational-tags | Operational tags present | `aws rds list-tags-for-resource` | LOW | Check for Environment, Owner, Application tags | Add operational tags |
| 33 | missing-cost-tags | Cost allocation tags present | `aws rds list-tags-for-resource` | LOW | Check for CostCenter, Project tags | Add cost allocation tags |

## Scoring Guidelines

- **FAIL (Critical)**: Immediate action required. Security or availability risk.
- **WARN (Warning)**: Action recommended within 30 days. Operational or performance risk.
- **LOW (Informational)**: Best practice recommendation. Plan for future implementation.

## Remediation Priority

1. **P1 (Immediate)**: FAIL severity checks — security and availability risks
2. **P2 (30 days)**: WARN severity checks — operational and performance risks
3. **P3 (90 days)**: LOW severity checks — optimization and best practices
