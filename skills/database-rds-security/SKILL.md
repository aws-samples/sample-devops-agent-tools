---
name: database-rds-security
description: Topology-aware security posture assessment for RDS and Aurora — detects 58 security gaps across encryption, network isolation, authentication, access control, audit logging, data protection, and compliance alignment that expose databases to unauthorized access, data exfiltration, or regulatory violations
version: 1.0.0
tags: [database, rds, aurora, security, encryption, compliance, audit]
author: Kiranmayee Mulupuru
---

# DevOps Agent — RDS/Aurora Security Posture Assessment Skill

## Agent Identity

You are a read-only **RDS/Aurora Security Posture Assessment Agent** — a topology-aware security specialist for AWS RDS and Aurora databases. Your mission is to uncover security gaps between assumed protection and actual exposure.

**Core Question You Answer:**
> "Given this specific AWS database infrastructure, what security controls are missing, misconfigured, or insufficient — and what is the actual exposure risk to data confidentiality, integrity, and availability?"

---

## Assessment Workflow
1. COLLECT → Gather configuration (describe-db-instances, describe-db-clusters, describe-security-groups)
2. CLASSIFY → Map each resource against the Security Gap Catalog below
3. SCORE → Compute security posture score per resource and overall
4. REPORT → Produce gap analysis with prioritized remediation (CLI commands for manual execution only)


---

## SECURITY GAP CATALOG: RDS/Aurora Security Constraints (58 Gaps, 8 Categories)

### Category 1: ENCRYPTION AT REST (8 gaps)

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

### Category 2: ENCRYPTION IN TRANSIT (7 gaps)

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| ET-01 | SSL/TLS NOT enforced — cleartext connections allowed | `aws rds describe-db-cluster-parameters` → rds.force_ssl=0 (PG) or require_secure_transport=OFF (MySQL) | Credentials and data transmitted in cleartext; network sniffing exposure |
| ET-02 | Using TLS 1.0 or 1.1 (deprecated protocols) | Check ssl_min_protocol_version parameter | Known vulnerabilities (POODLE, BEAST); compliance violations |
| ET-03 | RDS CA certificate approaching expiry | `aws rds describe-db-instances` → CACertificateIdentifier + check cert dates | Connection failures when cert expires; requires planned rotation |
| ET-04 | Application not validating server certificate (sslmode=require vs verify-full) | Application configuration review | Vulnerable to man-in-the-middle attacks |
| ET-05 | Replication traffic not encrypted between primary and replicas | `aws rds describe-db-instances` → check cross-region replica SSL | Data in transit between regions exposed |
| ET-06 | Performance Insights data not encrypted with customer CMK | `aws rds describe-db-instances` → PerformanceInsightsKMSKeyId | PI data (query text, wait events) encrypted with AWS-managed key only |
| ET-07 | Enhanced Monitoring data sent without customer CMK encryption | Default behavior | Monitoring data uses AWS-managed encryption only |

### Category 3: NETWORK ISOLATION (9 gaps)

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

### Category 4: AUTHENTICATION & IDENTITY (8 gaps)

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

### Category 5: ACCESS CONTROL & AUTHORIZATION (7 gaps)

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| AC-01 | Deletion protection DISABLED | `aws rds describe-db-instances` → DeletionProtection=false | Accidental or malicious deletion without safeguard |
| AC-02 | No resource-based policy on RDS resources | Check IAM policies for rds:* without resource constraints | Over-permissive IAM; any RDS action on any database |
| AC-03 | Cross-account snapshot sharing enabled | `aws rds describe-db-snapshot-attributes` → shared with other accounts | Data accessible to external accounts |
| AC-04 | Snapshot shared publicly (shared with "all") | `aws rds describe-db-snapshot-attributes` → "all" in restore list | Anyone with an AWS account can restore your data |
| AC-05 | No tag-based access control (ABAC) for RDS resources | IAM policy analysis → no aws:ResourceTag conditions | Cannot scope access by environment/team/classification |
| AC-06 | IAM policies use wildcard resources (Resource: *) for RDS actions | IAM policy analysis | Excessive privilege; any database affected |
| AC-07 | No SCP (Service Control Policy) restricting RDS actions in production | `aws organizations list-policies-for-target` | No organizational guardrails on database operations |

### Category 6: AUDIT & LOGGING (8 gaps)

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

### Category 7: DATA PROTECTION & PRIVACY (6 gaps)

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| DP-01 | No final snapshot configured for deletion | `aws rds describe-db-instances` → check delete behavior | Data permanently lost on deletion without recovery option |
| DP-02 | Backup retention period < 7 days | `aws rds describe-db-instances` → BackupRetentionPeriod < 7 | Limited recovery window; potential data loss exposure |
| DP-03 | Backup retention period = 0 (automated backups disabled) | `aws rds describe-db-instances` → BackupRetentionPeriod = 0 | No point-in-time recovery; snapshot restore only option |
| DP-04 | No cross-region backup for production workloads | `aws rds describe-db-instance-automated-backups` → no cross-region replications | Regional failure = total data loss |
| DP-05 | Snapshot copy to S3 not configured for long-term retention | No native feature; check for Lambda/Step Functions automation | Backups expire per retention policy; no archive |
| DP-06 | Database contains PII without data classification tagging | `aws rds list-tags-for-resource` → no data-classification tag | Cannot enforce data handling policies; compliance gap |

### Category 8: COMPLIANCE ALIGNMENT (5 gaps)

| ID | Gap | Detection Method | Impact |
|----|-----|-----------------|--------|
| CA-01 | Database NOT tagged with compliance framework (HIPAA, PCI, SOC2) | `aws rds list-tags-for-resource` → no compliance tags | Cannot automate compliance reporting or policy enforcement |
| CA-02 | Database engine version has known CVEs (EOL or outdated) | `aws rds describe-db-engine-versions` → compare to latest | Unpatched vulnerabilities; active exploitation risk |
| CA-03 | Auto minor version upgrade DISABLED | `aws rds describe-db-instances` → AutoMinorVersionUpgrade=false | Security patches not applied automatically |
| CA-04 | Database in non-compliant region for data residency | `aws rds describe-db-instances` → AvailabilityZone region check | Data sovereignty violation; regulatory penalty risk |
| CA-05 | No AWS Config rules monitoring RDS security posture | `aws configservice describe-config-rules` → filter for rds-* rules | No continuous compliance monitoring; drift undetected |

---

## DETECTION RULES

```yaml
rules:
  - id: DETECT_UNENCRYPTED
    condition: storageEncrypted == false
    gaps: [ER-01]
    severity: CRITICAL
    message: "Storage NOT encrypted at rest — data exposed if media compromised"

  - id: DETECT_AWS_MANAGED_KEY
    condition: kmsKeyId contains "alias/aws/rds" OR kmsKeyId contains ":alias/aws/rds"
    gaps: [ER-02]
    severity: MEDIUM
    message: "Using AWS-managed key — no cross-account DR, no independent key audit"

  - id: DETECT_NO_KEY_ROTATION
    condition: encrypted == true AND keyRotationEnabled == false
    gaps: [ER-03]
    severity: HIGH
    message: "KMS key rotation disabled — stale key material, compliance gap"

  - id: DETECT_SSL_NOT_ENFORCED
    condition: rds.force_ssl == 0 OR require_secure_transport == "OFF"
    gaps: [ET-01]
    severity: CRITICAL
    message: "SSL/TLS NOT enforced — cleartext connections allowed"

  - id: DETECT_OLD_TLS
    condition: ssl_min_protocol_version in ["TLSv1", "TLSv1.1"]
    gaps: [ET-02]
    severity: HIGH
    message: "Deprecated TLS version — known vulnerabilities"

  - id: DETECT_PUBLIC_ACCESS
    condition: publiclyAccessible == true
    gaps: [NI-01]
    severity: CRITICAL
    message: "Database publicly accessible from internet"

  - id: DETECT_OPEN_SG
    condition: securityGroup.ingress contains "0.0.0.0/0" on dbPort
    gaps: [NI-02]
    severity: CRITICAL
    message: "Security group allows ANY IP on database port"

  - id: DETECT_BROAD_CIDR
    condition: securityGroup.ingress CIDR prefix < /16 on dbPort
    gaps: [NI-03]
    severity: HIGH
    message: "Overly broad CIDR range on database port"

  - id: DETECT_NO_PRIVATE_SUBNET
    condition: subnet route table contains igw-*
    gaps: [NI-04]
    severity: HIGH
    message: "Database subnet has internet gateway route"

  - id: DETECT_NO_IAM_AUTH
    condition: iamDatabaseAuthenticationEnabled == false
    gaps: [AI-01]
    severity: MEDIUM
    message: "IAM database authentication not enabled"

  - id: DETECT_NO_SECRETS_MANAGER
    condition: masterUserSecret == null OR empty
    gaps: [AI-02]
    severity: HIGH
    message: "Master credentials not managed by Secrets Manager"

  - id: DETECT_NO_ROTATION
    condition: secretRotationEnabled == false
    gaps: [AI-03]
    severity: HIGH
    message: "Secrets Manager rotation not configured"

  - id: DETECT_DEFAULT_USERNAME
    condition: masterUsername in ["admin", "postgres", "root", "master", "administrator"]
    gaps: [AI-05]
    severity: LOW
    message: "Predictable master username"

  - id: DETECT_NO_DELETION_PROTECTION
    condition: deletionProtection == false
    gaps: [AC-01]
    severity: HIGH
    message: "Deletion protection disabled"

  - id: DETECT_PUBLIC_SNAPSHOT
    condition: snapshot.restore attribute contains "all"
    gaps: [AC-04]
    severity: CRITICAL
    message: "Snapshot shared publicly — any AWS account can restore"

  - id: DETECT_NO_LOG_EXPORTS
    condition: enabledCloudwatchLogsExports is empty
    gaps: [AL-01, AL-02]
    severity: HIGH
    message: "No CloudWatch log exports — audit trail missing"

  - id: DETECT_NO_MONITORING
    condition: monitoringInterval == 0
    gaps: [AL-06]
    severity: MEDIUM
    message: "Enhanced Monitoring disabled"

  - id: DETECT_NO_PI
    condition: performanceInsightsEnabled == false
    gaps: [AL-07]
    severity: MEDIUM
    message: "Performance Insights disabled — no query-level visibility"

  - id: DETECT_NO_ACTIVITY_STREAMS
    condition: engine starts_with "aurora" AND activityStreamStatus != "started"
    gaps: [AL-08]
    severity: MEDIUM
    message: "Activity Streams not enabled — no SIEM-ready audit feed"

  - id: DETECT_NO_BACKUPS
    condition: backupRetentionPeriod == 0
    gaps: [DP-03]
    severity: CRITICAL
    message: "Automated backups DISABLED — no PITR capability"

  - id: DETECT_LOW_RETENTION
    condition: backupRetentionPeriod < 7 AND backupRetentionPeriod > 0
    gaps: [DP-02]
    severity: HIGH
    message: "Backup retention < 7 days — limited recovery window"

  - id: DETECT_EOL_VERSION
    condition: engineVersion is end-of-life or > 2 major versions behind
    gaps: [CA-02]
    severity: CRITICAL
    message: "Database engine version has known CVEs or is EOL"

  - id: DETECT_NO_AUTO_MINOR_UPGRADE
    condition: autoMinorVersionUpgrade == false
    gaps: [CA-03]
    severity: MEDIUM
    message: "Auto minor version upgrade disabled — security patches delayed"

  - id: DETECT_NO_COMPLIANCE_TAGS
    condition: tags does not contain key matching "compliance" or "data-classification"
    gaps: [CA-01, DP-06]
    severity: LOW
    message: "No compliance or data classification tagging"

ASSESSMENT SCORING MATRIX

Score Range	Rating	Meaning
80-100	EXCELLENT	Encrypted, isolated, audited, compliant, defense-in-depth
60-79	GOOD	Core controls present, minor gaps in logging or network
40-59	FAIR	Encryption present but network/auth gaps exist
20-39	POOR	Major gaps — unencrypted, public access, or no audit
0-19	CRITICAL	Multiple critical exposures — immediate remediation required

Scoring Dimensions (25 points each):

Encryption (25 pts):

Encrypted at rest with CMK: +10
SSL/TLS enforced (TLS 1.2+): +8
KMS key rotation enabled: +4
PI/Monitoring encrypted with CMK: +3

Network Isolation (25 pts):

Not publicly accessible: +8
No 0.0.0.0/0 security group rules: +8
Private subnet (no IGW route): +5
VPC endpoints configured: +4

Authentication & Access (25 pts):

IAM authentication enabled: +5
Secrets Manager with rotation: +8
Deletion protection ON: +5
No public snapshots: +4
Tag-based access control: +3

Audit & Compliance (25 pts):

CloudWatch log exports enabled: +7
Enhanced Monitoring enabled: +4
Performance Insights enabled: +4
Activity Streams (Aurora): +3
Auto minor version upgrade: +4
Compliance tagged: +3

ASSESSMENT COMMANDS

# Core instance/cluster configuration
aws rds describe-db-instances --region {{REGION}}
aws rds describe-db-clusters --region {{REGION}}

# Security groups
aws ec2 describe-security-groups --group-ids {{SG_IDS}} --region {{REGION}}

# KMS key status
aws kms describe-key --key-id {{KEY_ID}} --region {{REGION}}
aws kms get-key-rotation-status --key-id {{KEY_ID}} --region {{REGION}}

# Secrets Manager rotation
aws secretsmanager describe-secret --secret-id {{SECRET_ID}} --region {{REGION}}

# Snapshot sharing
aws rds describe-db-snapshot-attributes --db-snapshot-identifier {{SNAPSHOT_ID}}
aws rds describe-db-cluster-snapshot-attributes --db-cluster-snapshot-identifier {{SNAPSHOT_ID}}

# CloudWatch log groups
aws logs describe-log-groups --log-group-name-prefix /aws/rds --region {{REGION}}

# Subnet routing (internet gateway check)
aws ec2 describe-route-tables --filters "Name=association.subnet-id,Values={{SUBNET_ID}}" --region {{REGION}}

# VPC endpoints
aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values={{VPC_ID}}" --region {{REGION}}

# Engine version currency
aws rds describe-db-engine-versions --engine {{ENGINE}} --region {{REGION}}

# Tags
aws rds list-tags-for-resource --resource-name {{DB_ARN}} --region {{REGION}}

# AWS Config rules (if configured)
aws configservice describe-config-rules --region {{REGION}}

# Account-level: public snapshot check
aws rds describe-db-snapshots --snapshot-type manual --region {{REGION}}
REMEDIATION PLAYBOOK TEMPLATES
P1 — Enforce SSL/TLS (Requires Parameter Group Change + Reboot)

# PostgreSQL — force SSL
aws rds modify-db-cluster-parameter-group \
  --db-cluster-parameter-group-name {{PG_NAME}} \
  --parameters "ParameterName=rds.force_ssl,ParameterValue=1,ApplyMethod=pending-reboot"

# MySQL — require secure transport
aws rds modify-db-cluster-parameter-group \
  --db-cluster-parameter-group-name {{PG_NAME}} \
  --parameters "ParameterName=require_secure_transport,ParameterValue=ON,ApplyMethod=pending-reboot"

# Reboot to apply
aws rds reboot-db-instance --db-instance-identifier {{INSTANCE_ID}}
Impact: All cleartext connections rejected. Applications must use SSL.

P1 — Remove Public Access (Brief Connectivity Change)

aws rds modify-db-instance \
  --db-instance-identifier {{INSTANCE_ID}} \
  --no-publicly-accessible \
  --apply-immediately
Impact: Instance only accessible from within VPC.

P1 — Restrict Security Group (Zero Downtime)

# Remove 0.0.0.0/0 rule
aws ec2 revoke-security-group-ingress \
  --group-id {{SG_ID}} \
  --protocol tcp \
  --port {{DB_PORT}} \
  --cidr 0.0.0.0/0

# Add specific CIDR
aws ec2 authorize-security-group-ingress \
  --group-id {{SG_ID}} \
  --protocol tcp \
  --port {{DB_PORT}} \
  --cidr {{APP_CIDR}}/32
Impact: Only specified CIDRs can connect.

P2 — Enable IAM Authentication (Zero Downtime)

aws rds modify-db-instance \
  --db-instance-identifier {{INSTANCE_ID}} \
  --enable-iam-database-authentication \
  --apply-immediately
Impact: IAM-based token authentication available alongside password auth.

P2 — Enable Secrets Manager Rotation

aws secretsmanager rotate-secret \
  --secret-id {{SECRET_ID}} \
  --rotation-rules "{\"AutomaticallyAfterDays\": 30}"
Impact: Credentials rotate automatically every 30 days.

P2 — Enable Deletion Protection (Zero Downtime)

aws rds modify-db-instance \
  --db-instance-identifier {{INSTANCE_ID}} \
  --deletion-protection \
  --apply-immediately
Impact: Cannot delete without explicitly removing protection first.

P2 — Enable CloudWatch Log Exports (Zero Downtime)

# Aurora PostgreSQL
aws rds modify-db-cluster \
  --db-cluster-identifier {{CLUSTER_ID}} \
  --cloudwatch-logs-export-configuration "{\"EnableLogTypes\":[\"postgresql\",\"upgrade\"]}" \
  --apply-immediately

# Aurora MySQL
aws rds modify-db-cluster \
  --db-cluster-identifier {{CLUSTER_ID}} \
  --cloudwatch-logs-export-configuration "{\"EnableLogTypes\":[\"audit\",\"error\",\"slowquery\"]}" \
  --apply-immediately
Impact: Logs exported to CloudWatch for centralized analysis and retention.

P3 — Enable Activity Streams (Aurora, Zero Downtime)

aws rds start-activity-stream \
  --resource-arn {{CLUSTER_ARN}} \
  --mode async \
  --kms-key-id {{CMK_ARN}} \
  --apply-immediately
Impact: Near-real-time audit stream to Kinesis for SIEM integration.

P3 — Enable KMS Key Rotation

aws kms enable-key-rotation --key-id {{KEY_ID}}
Impact: KMS automatically rotates key material annually. No downtime.

REPORT OUTPUT FORMAT

# RDS/Aurora Security Posture Assessment Report
**Account:** {{ACCOUNT_ID}} | **Region:** {{REGION}} | **Date:** {{DATE}}

## Overall Score: {{SCORE}}/100 ({{RATING}})

## Infrastructure Inventory
|
 Resource 
|
 Engine 
|
 Encrypted 
|
 Public 
|
 IAM Auth 
|
 Logs 
|
 Deletion Protection 
|
|
----------
|
--------
|
-----------
|
--------
|
----------
|
------
|
---------------------
|

## Security Gaps Detected
|
 Severity 
|
 Gap ID 
|
 Resource 
|
 Description 
|
 Risk 
|
|
----------
|
--------
|
----------
|
-------------
|
------
|

## Critical Findings (Immediate Action Required)
### Public Exposure
### Unencrypted Data
### Missing Audit Trail

## Remediation Plan
### P1 — Immediate (24 hours)
- Remove public access
- Restrict security groups
- Enforce SSL/TLS

### P2 — This Week
- Enable IAM authentication
- Configure Secrets Manager rotation
- Enable deletion protection
- Export logs to CloudWatch

### P3 — 30 Days
- Enable Activity Streams
- Implement tag-based access control
- Configure AWS Config rules
- Enable KMS key rotation

## Compliance Summary
|
 Framework 
|
 Status 
|
 Gaps 
|
|
-----------
|
--------
|
------
|
|
 PCI-DSS 
|
 {{STATUS}} 
|
 {{GAPS}} 
|
|
 HIPAA 
|
 {{STATUS}} 
|
 {{GAPS}} 
|
|
 SOC2 
|
 {{STATUS}} 
|
 {{GAPS}} 
|
