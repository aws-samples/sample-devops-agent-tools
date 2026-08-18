```markdown
# Database RDS Security Skill

A skill for AWS DevOps Agent that performs **read-only** topology-aware security
posture assessments for Amazon RDS and Aurora, detecting gaps between assumed
protection and actual exposure.

## Purpose

Security misconfigurations in RDS/Aurora databases frequently go undetected because
they require correlating data across multiple AWS services (RDS, EC2, KMS, Secrets
Manager, CloudWatch Logs, Config) that no single console view surfaces together. This
skill assembles that correlated view and produces a prioritized, severity-tiered
remediation plan.

## Key Capabilities

- **58-gap catalog** across 8 categories: Encryption at Rest, Encryption in Transit,
  Network Isolation, Authentication & Identity, Access Control & Authorization, Audit &
  Logging, Data Protection & Privacy, and Compliance Alignment
- **4-dimension scoring (0-100)**: Encryption, Network Isolation, Authentication &
  Access, Audit & Compliance
- **CLI remediation playbooks** with explicit prerequisite call-outs — see
  `references/remediation-playbooks.md`

## Prerequisites

### IAM Permissions

The DevOps Agent role needs the following read-only permissions:

rds:DescribeDBInstances rds:DescribeDBClusters rds:DescribeDBSnapshotAttributes rds:DescribeDBClusterSnapshotAttributes rds:DescribeDBSnapshots rds:DescribeDBEngineVersions rds:ListTagsForResource ec2:DescribeSecurityGroups ec2:DescribeRouteTables ec2:DescribeVpcEndpoints ec2:DescribeNetworkAcls kms:DescribeKey kms:GetKeyRotationStatus kms:GetKeyPolicy secretsmanager:DescribeSecret logs:DescribeLogGroups cloudwatch:DescribeAlarms config:DescribeConfigRules

The following permissions are **not** included in `AIDevOpsAgentAccessPolicy` and must
be added explicitly to the DevOps Agent execution role, or the corresponding gap checks
will be silently skipped:
`kms:DescribeKey`, `kms:GetKeyRotationStatus`, `kms:GetKeyPolicy`,
`ec2:DescribeRouteTables`, `ec2:DescribeVpcEndpoints`, `ec2:DescribeNetworkAcls`,
`secretsmanager:DescribeSecret`, `config:DescribeConfigRules`.

**Optional (organization-level, often unavailable to a member-account role):**
`organizations:ListPoliciesForTarget` — required only for the AC-07 (SCP restricting
RDS actions) check. If this permission is not granted, the skill reports AC-07 as
"unable to verify" rather than a false negative.

### AWS Resources

- An Amazon RDS or Aurora instance/cluster
- No VPC access, no database credentials required — this skill is control-plane only

## Limitations

- **Advisory only.** This skill produces findings and CLI remediation *suggestions*;
  it never applies changes. All commands in `references/remediation-playbooks.md`
  are for manual execution by an operator.
- **Secrets Manager rotation remediation requires a pre-existing rotation function.**
  `aws secretsmanager rotate-secret --rotation-rules` alone does not configure rotation
  on a secret that has never had it enabled — see the prerequisite note in
  `references/remediation-playbooks.md`.
- **Some gaps require in-database or application-level verification** (e.g., ET-04
  application certificate validation mode) that cannot be checked from AWS APIs alone
  — these are flagged as "requires manual review."

## Agent Types

This skill is used by the following agent types (selected in the Operator Web App at
upload time):

- **Chat tasks** — interactive security posture assessments and targeted category checks
- **Incident RCA** — root cause analysis where a security misconfiguration is a
  contributing factor

Select **Generic** instead if you want the skill available to all agent types.

## Uploading to AWS DevOps Agent

**Option A: Import from GitHub (recommended)**

In the DevOps Agent web app, go to Settings → Add Skill → Import from repository,
then point to the `skills/database-rds-security` directory.

**Option B: Upload as a zip file**

```bash
cd skills
zip -r database-rds-security.zip database-rds-security/ -i '*.md' '*.txt' '*.json' -x '*/evals/*'

Upload via Skills → Add skill → Upload skill in the DevOps Agent web app. Select agent types Chat tasks and Incident RCA.

Option C: Upload via the Asset API — see the AWS DevOps Agent User Guide.
How to Use This Skill
"Review the security posture of my RDS instance prod-orders-1"
"Is my database publicly accessible?"
"Check if my database credentials are rotating"
"Am I compliant with PCI-DSS on this database?"
"What security gaps exist on my Aurora cluster?"
Skill Structure
database-rds-security/
├── SKILL.md                              # Main skill instructions
├── README.md                             # This file
├── CHANGELOG.md                          # Version history
├── references/
│   ├── security-gap-catalog.md           # 58 gaps across 8 categories
│   └── remediation-playbooks.md          # CLI remediation + report format
└── evals/
    ├── evals.json                        # Functional test scenarios
    ├── eval_queries.json                 # Trigger tests
    └── report.json                       # Evaluation results
Safety

This skill operates in read-only mode:

    No DDL, DML, or DCL — no infrastructure changes of any kind
    All CLI commands in references/remediation-playbooks.md are for manual execution by an operator, with explicit prerequisite call-outs (e.g., the Secrets Manager rotation prerequisite)

Non-production disclaimer

    ⚠️ This skill is sample code, not intended for production use without additional review and testing. Validate in a non-production environment first. Severity thresholds are general guidance and should be tuned to your compliance requirements.

