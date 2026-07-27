---
name: drift-detection-baseline
description: Use this skill when performing drift detection on production infrastructure
  to identify deviations from operational standards. Activate when you need to compare
  current resource configurations against expected baseline state for security, compliance,
  and lifecycle standards. This skill defines the expected state, verification methods,
  and severity classification for infrastructure drift findings.
metadata:
  author: hmandhad
  version: "1.0.0"
  aws-devops-agent-skills.agent-types: "Chat tasks, Prevention"
  aws-devops-agent-skills.aws-services: "Amazon S3, Amazon RDS, AWS Lambda, Amazon EC2, Amazon EKS, Amazon DynamoDB, AWS Config, AWS CloudTrail"
  aws-devops-agent-skills.technical-domains: "Operations, Security, Compliance"
---

# Drift Detection Baseline

This skill defines the expected production baseline state and provides a systematic approach to detecting drift — deviations between current resource configuration and the defined expected state.

## How to Use This Skill

1. Load this skill to understand the expected baseline state for production resources.
2. Discover resources in the target environment using topology discovery or `Describe*`/`List*` API calls.
3. For each resource, extract the configuration fields listed in the verification columns below.
4. Compare actual state against expected state defined in this baseline.
5. Any mismatch between actual and expected state is a drift finding.
6. Classify each drift finding using the Severity Classification section.

---

## Section 1: Security Posture Baseline (Mandatory — deviations are drift)

These policies represent security standards that all production resources must meet. Any deviation is classified as drift and must be reported.

| Policy | Expected State | Resources to Check | How to Verify |
|--------|---------------|-------------------|---------------|
| Encryption at rest | All data stores encrypted using AWS-managed or customer-managed KMS keys | S3 buckets, RDS instances, EBS volumes, DynamoDB tables, EFS file systems | S3: `GetBucketEncryption` for `ServerSideEncryptionConfiguration`. RDS: `DescribeDBInstances` check `StorageEncrypted = true`. EBS: `DescribeVolumes` check `Encrypted = true`. DynamoDB: `DescribeTable` check `SSEDescription.Status = ENABLED`. EFS: `DescribeFileSystems` check `Encrypted = true` |
| Encryption in transit | TLS enforced on all endpoints and data paths | S3 bucket policies, RDS instances, load balancer listeners, API Gateway endpoints | S3: bucket policy denies requests where `aws:SecureTransport = false`. RDS: check `ssl` parameter in parameter group. ALB/NLB: `DescribeListeners` check protocol is HTTPS/TLS. API Gateway: check `minimumCompressionSize` and endpoint type |
| Network isolation | No direct public internet ingress path to databases or internal-only services | RDS instances, Redshift clusters, ElastiCache clusters, security groups | RDS: `DescribeDBInstances` check `PubliclyAccessible = false`. Security Groups: `DescribeSecurityGroups` check no ingress rules allow `0.0.0.0/0` or `::/0` on database ports (3306, 5432, 1433, 6379, 27017) |
| No public S3 access | S3 Public Access Block enabled at bucket level unless explicitly exempted | All S3 buckets | `GetPublicAccessBlock` — all four settings (`BlockPublicAcls`, `IgnorePublicAcls`, `BlockPublicPolicy`, `RestrictPublicBuckets`) should be `true`. Also check `GetBucketPolicyStatus` for `IsPublic` |
| No exposed secrets | No hardcoded credentials, API keys, or secrets in resource configurations | Lambda environment variables, ECS task definitions, EC2 user data | Lambda: `GetFunctionConfiguration` inspect `Environment.Variables` for patterns matching keys/secrets. ECS: `DescribeTaskDefinition` inspect `containerDefinitions[].environment` and `secrets` |

---

## Section 2: Compliance Baseline (Required — deviations are drift)

These standards ensure operational governance and auditability. Deviations indicate compliance drift.

| Policy | Expected State | Resources to Check | How to Verify |
|--------|---------------|-------------------|---------------|
| Resource tagging | All resources must have required tags: `Environment`, `Owner`, `CostCenter` | All taggable resources | Use `resourcegroupstaggingapi:GetResources` to discover untagged or partially-tagged resources. For each resource, verify presence of required tag keys. Acceptable `Environment` values: `production`, `staging`, `development` |
| CloudTrail logging | At minimum one organization or account-level trail active and logging | CloudTrail trails | `DescribeTrails` and `GetTrailStatus` — verify `IsLogging = true` and trail covers the current region. Check `S3BucketName` is set for log delivery |
| AWS Config recording | Config recorder active for supported resource types | AWS Config | `DescribeConfigurationRecorders` and `DescribeConfigurationRecorderStatus` — verify recorder exists and `recording = true` |
| Backup configuration | Automated backups enabled with retention period >= 7 days for production data stores | RDS instances, DynamoDB tables with PITR | RDS: `DescribeDBInstances` check `BackupRetentionPeriod >= 7`. DynamoDB: `DescribeContinuousBackups` check `PointInTimeRecoveryStatus = ENABLED` |

---

## Section 3: Lifecycle Baseline (Drift if past end-of-support)

Resources running deprecated or end-of-life software versions represent lifecycle drift. These accumulate security risk over time.

| Resource Type | Drift Condition | How to Detect |
|---------------|----------------|---------------|
| Lambda runtimes | Runtime is deprecated or past AWS end-of-support date | `ListFunctions` then check `Runtime` field against AWS Lambda runtime deprecation schedule. Runtimes past Phase 2 deprecation (no creates or updates) are critical drift. Runtimes in Phase 1 (no creates) are high drift |
| RDS engine versions | Engine version past AWS end-of-standard-support | `DescribeDBInstances` check `EngineVersion` against RDS engine version lifecycle. Instances on versions past standard support are in extended support (incurs additional charges) or unsupported |
| EKS cluster versions | Kubernetes version past AWS end-of-standard-support | `DescribeClusters` check `version` against EKS Kubernetes version lifecycle. Clusters on versions past standard support are in extended support or unsupported |
| ECS AMI / platform versions | ECS-optimized AMI or Fargate platform version outdated | For EC2 launch type: check AMI age via `DescribeImages`. For Fargate: check `platformVersion` is `LATEST` or current supported version |

---

## Section 4: Severity Classification

Classify each drift finding based on the combination of risk exposure and blast radius.

| Severity | Criteria | Examples |
|----------|----------|----------|
| **Critical** | Active security exposure with public-facing attack surface, or data loss risk | Unencrypted public S3 bucket with data, publicly accessible production database, missing CloudTrail (no audit trail), secrets in environment variables |
| **High** | Security gap not yet externally exploitable, or running past end-of-support | Missing encryption at rest on internal data store, no backups on production database, deprecated Lambda runtime past Phase 2, database port open to 0.0.0.0/0 in private subnet |
| **Medium** | Compliance gap with no immediate security risk, or approaching end-of-support | Missing required tags, Config recorder not active, Lambda runtime approaching deprecation, backup retention below 7 days |
| **Low** | Best practice deviation with minimal operational risk | Non-production resource without Multi-AZ, resource with `Owner` tag but missing `CostCenter`, Fargate not on latest platform version |

---

## Section 5: AWS Config Rule Coverage Gap Analysis

After evaluating resources against this baseline, check whether existing AWS Config rules provide automated enforcement for these policies.

### How to analyze Config rule coverage:

1. Call `DescribeConfigRules` to list all active Config rules
2. Map each rule to the policies in Sections 1-3 above
3. Identify policies that have NO corresponding Config rule — these are coverage gaps
4. For each gap, determine if an AWS managed rule exists that could enforce it

### Common managed rules that map to this baseline:

| Baseline Policy | AWS Config Managed Rule |
|----------------|------------------------|
| Encryption at rest (S3) | `s3-bucket-server-side-encryption-enabled` |
| Encryption at rest (RDS) | `rds-storage-encrypted` |
| Encryption at rest (EBS) | `encrypted-volumes` |
| No public S3 access | `s3-bucket-public-read-prohibited`, `s3-bucket-public-write-prohibited` |
| Network isolation (RDS) | `rds-instance-public-access-check` |
| Resource tagging | `required-tags` |
| Backup (RDS) | `db-instance-backup-enabled` |
| CloudTrail active | `cloud-trail-cloud-watch-logs-enabled` |

Policies without available managed rules are candidates for custom Config rules. Flag these as recommendations.

---

## Section 6: Extending This Baseline

This baseline covers foundational security, compliance, and lifecycle standards. Organizations should extend it with:

- **Application-specific policies**: latency thresholds, replication lag limits, scaling boundaries
- **Regulatory requirements**: HIPAA, PCI-DSS, SOC2-specific controls
- **Team conventions**: naming standards, cost allocation rules, environment separation

Create additional skills with organization-specific policies and load them alongside this baseline for comprehensive drift detection.
