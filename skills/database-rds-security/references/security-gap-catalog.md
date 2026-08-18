
```markdown
# Security Gap Catalog — RDS/Aurora Security Constraints

58 gaps across 8 categories. Referenced by `SKILL.md` detection rules via the ID column.

## Category 1: ENCRYPTION AT REST — 8 gaps

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| ER-01 | Database storage NOT encrypted at rest | `aws rds describe-db-instances` → StorageEncrypted=false | Data at rest readable if storage media compromised; blocks cross-region DR |
| ER-02 | Using AWS-managed key (aws/rds) instead of customer-managed CMK | `aws rds describe-db-instances` → KmsKeyId contains "alias/aws/rds" | Cannot control key policy, cannot share cross-account, cannot audit key usage independently |
| ER-03 | KMS key rotation NOT enabled for customer-managed CMK | `aws kms get-key-rotation-status --key-id {{KEY_ID}}` → KeyRotationEnabled=false | Stale key material; compliance violation for PCI-DSS, HIPAA |
| ER-04 | KMS key scheduled for deletion — database will become inaccessible | `aws kms describe-key --key-id {{KEY_ID}}` → KeyState=PendingDeletion | Irrecoverable data loss once key is deleted |
| ER-05 | Snapshot NOT encrypted (even if source instance is encrypted) | `aws rds describe-db-snapshots` → Encrypted=false | Snapshot data exposed at rest; cannot copy cross-region for DR |
| ER-06 | Automated backups NOT encrypted | `aws rds describe-db-instance-automated-backups` → Encrypted=false | Backup data at rest is unprotected |
| ER-07 | KMS key policy allows broad access (Principal: *) | `aws kms get-key-policy --key-id {{KEY_ID}} --policy-name default` | Any principal in any account can use the key |
| ER-08 | Multiple databases sharing same KMS key | Cross-reference KmsKeyId across instances | Blast radius: key compromise affects all databases using it |

## Category 2: ENCRYPTION IN TRANSIT — 7 gaps

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| ET-01 | SSL/TLS NOT enforced — cleartext connections allowed | `aws rds describe-db-cluster-parameters` → rds.force_ssl=0 (PG) or require_secure_transport=OFF (MySQL) | Credentials and data transmitted in cleartext; network sniffing exposure |
| ET-02 | Using TLS 1.0 or 1.1 (deprecated protocols) | Check ssl_min_protocol_version parameter | Known vulnerabilities (POODLE, BEAST); compliance violations |
| ET-03 | RDS CA certificate approaching expiry | `aws rds describe-db-instances` → CACertificateIdentifier + check cert dates | Connection failures when cert expires; requires planned rotation |
| ET-04 | Application not validating server certificate (sslmode=require vs verify-full) | Application configuration review | Vulnerable to man-in-the-middle attacks |
| ET-05 | Replication traffic not encrypted between primary and replicas | `aws rds describe-db-instances` → check cross-region replica SSL | Data in transit between regions exposed |
| ET-06 | Performance Insights data not encrypted with customer CMK | `aws rds describe-db-instances` → PerformanceInsightsKMSKeyId | PI data (query text, wait events) encrypted with AWS-managed key only |
| ET-07 | Enhanced Monitoring data sent without customer CMK encryption | Default behavior | Monitoring data uses AWS-managed encryption only |

## Category 3: NETWORK ISOLATION — 9 gaps

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| NI-01 | Database publicly accessible (PubliclyAccessible=true) | `aws rds describe-db-instances` → PubliclyAccessible=true | Direct internet exposure; attack surface includes all DB protocol ports |
| NI-02 | Security group allows 0.0.0.0/0 inbound on database port | `aws ec2 describe-security-groups --group-ids {{SG_ID}}` | Any IP can attempt connection; brute force exposure |
| NI-03 | Security group allows broad CIDR ranges (>/16) on database port | `aws ec2 describe-security-groups` → check CIDR prefix length | Overly permissive; lateral movement risk |
| NI-04 | Database NOT in private subnet (route table has internet gateway) | `aws ec2 describe-route-tables --filters Name=association.subnet-id,Values={{SUBNET_ID}}` | Traffic routes through internet even if not publicly accessible |
| NI-05 | No VPC endpoints for AWS services (S3, KMS, CloudWatch) | `aws ec2 describe-vpc-endpoints --filters Name=vpc-id,Values={{VPC_ID}}` | Service API calls traverse internet; data exfiltration path |
| NI-06 | Security group has unused/stale rules (referencing deleted resources) | `aws ec2 describe-security-groups` → cross-reference UserIdGroupPairs | Audit complexity; false sense of security |
| NI-07 | Multiple databases sharing same security group | Cross-reference VpcSecurityGroupId across instances | Blast radius: SG change affects all databases |
| NI-08 | No network ACL restrictions on database subnets | `aws ec2 describe-network-acls` → check subnet associations | Missing defense-in-depth layer |
| NI-09 | Database accessible from peered VPCs without explicit approval | Check VPC peering routes + SG rules referencing peered VPC CIDRs | Cross-account/cross-VPC access without explicit authorization |

## Category 4: AUTHENTICATION & IDENTITY — 8 gaps

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| AI-01 | IAM database authentication NOT enabled | `aws rds describe-db-instances` → IAMDatabaseAuthenticationEnabled=false | Relies solely on username/password; no short-lived token rotation |
| AI-02 | Master user credentials not managed by Secrets Manager | `aws rds describe-db-instances` → MasterUserSecret absent | Static credentials; no automatic rotation; exposure risk |
| AI-03 | Secrets Manager rotation NOT configured | `aws secretsmanager describe-secret --secret-id {{SECRET_ID}}` → RotationEnabled=false | Stale credentials; no automatic password cycling |
| AI-04 | Secrets Manager rotation period > 90 days | `aws secretsmanager describe-secret` → RotationRules.AutomaticallyAfterDays > 90 | Compliance violation (PCI-DSS requires <=90 days) |
| AI-05 | Master username uses default value (admin, postgres, root) | `aws rds describe-db-instances` → MasterUsername | Predictable usernames simplify brute-force attacks |
| AI-06 | No IAM condition keys restricting database access by IP/VPC | IAM policy analysis | Overly broad IAM access; any network location can authenticate |
| AI-07 | RDS Proxy authentication not using IAM | `aws rds describe-db-proxies` → Auth[].AuthScheme | Proxy relies on static Secrets Manager credentials only |
| AI-08 | Kerberos authentication not configured (where applicable) | `aws rds describe-db-instances` → DomainMemberships empty | No Active Directory integration for enterprise SSO |

## Category 5: ACCESS CONTROL & AUTHORIZATION — 7 gaps

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| AC-01 | Deletion protection DISABLED | `aws rds describe-db-instances` → DeletionProtection=false | Accidental or malicious deletion without safeguard |
| AC-02 | No resource-based policy on RDS resources | Check IAM policies for rds:* without resource constraints | Over-permissive IAM; any RDS action on any database |
| AC-03 | Cross-account snapshot sharing enabled | `aws rds describe-db-snapshot-attributes` → shared with other accounts | Data accessible to external accounts |
| AC-04 | Snapshot shared publicly (shared with "all") | `aws rds describe-db-snapshot-attributes` → "all" in restore list | Anyone with an AWS account can restore your data |
| AC-05 | No tag-based access control (ABAC) for RDS resources | IAM policy analysis → no aws:ResourceTag conditions | Cannot scope access by environment/team/classification |
| AC-06 | IAM policies use wildcard resources (Resource: *) for RDS actions | IAM policy analysis | Excessive privilege; any database affected |
| AC-07 | No SCP (Service Control Policy) restricting RDS actions in production | `aws organizations list-policies-for-target` | No organizational guardrails on database operations |

## Category 6: AUDIT & LOGGING — 8 gaps

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| AL-01 | Database audit logging NOT enabled | `aws rds describe-db-instances` → EnabledCloudwatchLogsExports empty | No record of who accessed what data; compliance violation |
| AL-02 | CloudWatch log exports not configured | `aws rds describe-db-instances` → EnabledCloudwatchLogsExports missing audit/error/slowquery | Logs only on instance; lost if instance terminated |
| AL-03 | CloudWatch log group retention set to "Never Expire" | `aws logs describe-log-groups` → retentionInDays=null | Unbounded storage cost; no data lifecycle management |
| AL-04 | CloudWatch log group NOT encrypted with CMK | `aws logs describe-log-groups` → kmsKeyId absent | Log data (containing query text, usernames) encrypted with AWS-managed key only |
| AL-05 | No CloudWatch alarms on security-relevant events | `aws cloudwatch describe-alarms` → check for login failure, permission denied patterns | Security events go undetected |
| AL-06 | Enhanced Monitoring NOT enabled | `aws rds describe-db-instances` → MonitoringInterval=0 | No OS-level visibility; cannot detect anomalous process activity |
| AL-07 | Performance Insights NOT enabled | `aws rds describe-db-instances` → PerformanceInsightsEnabled=false | Cannot identify unusual query patterns indicative of compromise |
| AL-08 | Activity Streams not enabled (Aurora) | `aws rds describe-db-clusters` → ActivityStreamStatus != "started" | No near-real-time audit feed for SIEM integration |

## Category 7: DATA PROTECTION & PRIVACY — 6 gaps

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| DP-01 | No final snapshot configured for deletion | `aws rds describe-db-instances` → check delete behavior | Data permanently lost on deletion without recovery option |
| DP-02 | Backup retention period < 7 days | `aws rds describe-db-instances` → BackupRetentionPeriod < 7 | Limited recovery window; potential data loss exposure |
| DP-03 | Backup retention period = 0 (automated backups disabled) | `aws rds describe-db-instances` → BackupRetentionPeriod = 0 | No point-in-time recovery; snapshot restore only option |
| DP-04 | No cross-region backup for production workloads | `aws rds describe-db-instance-automated-backups` → no cross-region replications | Regional failure = total data loss |
| DP-05 | Snapshot copy to S3 not configured for long-term retention | No native feature; check for Lambda/Step Functions automation | Backups expire per retention policy; no archive |
| DP-06 | Database contains PII without data classification tagging | `aws rds list-tags-for-resource` → no data-classification tag | Cannot enforce data handling policies; compliance gap |

## Category 8: COMPLIANCE ALIGNMENT — 5 gaps

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| CA-01 | Database NOT tagged with compliance framework (HIPAA, PCI, SOC2) | `aws rds list-tags-for-resource` → no compliance tags | Cannot automate compliance reporting or policy enforcement |
| CA-02 | Database engine version has known CVEs (EOL or outdated) | `aws rds describe-db-engine-versions` → compare to latest | Unpatched vulnerabilities; active exploitation risk |
| CA-03 | Auto minor version upgrade DISABLED | `aws rds describe-db-instances` → AutoMinorVersionUpgrade=false | Security patches not applied automatically |
| CA-04 | Database in non-compliant region for data residency | `aws rds describe-db-instances` → AvailabilityZone region check | Data sovereignty violation; regulatory penalty risk |
| CA-05 | No AWS Config rules monitoring RDS security posture | `aws configservice describe-config-rules` → filter for rds-* rules | No continuous compliance monitoring; drift undetected |

