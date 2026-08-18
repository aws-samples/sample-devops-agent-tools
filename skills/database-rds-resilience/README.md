```markdown
# Database RDS Resilience Skill

A skill for AWS DevOps Agent that performs **read-only** topology-aware resilience
assessments for Amazon RDS and Aurora, uncovering hidden blockers that silently
prevent meeting stated RTO/RPO targets.

## Purpose

Customers often assume their database tier is resilient because Multi-AZ is enabled
or backups are configured — but architectural and account-level constraints (encryption
dependencies, KMS quota throttling, snapshot restore mechanics, cross-region service
limits) frequently block a real recovery from meeting the stated target. This skill
surfaces those blockers proactively, before a DR event exposes them, and calculates
realistic RTO/RPO values based on actual configuration rather than documentation
assumptions.

## Key Capabilities

- **66-blocker catalog** across 7 categories: Failover Timing, Snapshot Restore,
  Encryption, KMS API Throttling, Cross-Region DR, Application-Layer Resilience Gaps,
  and Account-Level Service Quotas
- **Quota-aware RTO adjustment** — accounts for the default 5-concurrent-copy limit
  (QT-07) when calculating recovery time for fleets needing cross-region DR
- **4-dimension scoring (0-100)**: Regional HA, Data Protection, Cross-Region DR,
  Application Resilience
- **CLI remediation playbooks** with explicit downtime/performance-impact call-outs —
  see `references/remediation-playbooks.md`

## Prerequisites

### IAM Permissions

The DevOps Agent role needs the following read-only permissions:

rds:DescribeDBClusters rds:DescribeDBInstances rds:DescribeGlobalClusters rds:DescribeAccountAttributes service-quotas:GetServiceQuota service-quotas:ListServiceQuotas

`service-quotas:*` permissions above are **not** included in `AIDevOpsAgentAccessPolicy`
and must be added explicitly to the DevOps Agent execution role, or quota-pressure
findings (Category 7 / QT-01 through QT-20) will be skipped.

### AWS Resources

- An Amazon RDS or Aurora instance/cluster
- No VPC access, no database credentials, and no Data API required — this skill is
  control-plane only

## Limitations

- **Advisory only.** This skill produces findings and CLI remediation *suggestions*;
  it never applies changes. All commands in `references/remediation-playbooks.md`
  are for manual execution by an operator.
- **Quota checks reflect account defaults unless increased.** If you have requested
  quota increases via AWS Support, actual limits may exceed the defaults in
  `references/blocker-catalog.md` — the skill reads live values via
  `describe-account-attributes` and `service-quotas`, but the catalog's documented
  defaults are shown for context.
- **Some blockers cannot be detected from the control plane alone** (e.g., whether a
  DR runbook is documented, or whether a DR test was actually performed in the last 90
  days) — these are flagged as scoring inputs the operator must confirm manually.

## Agent Types

This skill is used by the following agent types (selected in the Operator Web App at
upload time):

- **Chat tasks** — interactive resilience assessments and targeted category checks
- **Incident RCA** — root cause analysis where a failed or slow recovery is a
  contributing factor

Select **Generic** instead if you want the skill available to all agent types.

## Uploading to AWS DevOps Agent

**Option A: Import from GitHub (recommended)**

In the DevOps Agent web app, go to Settings → Add Skill → Import from repository,
then point to the `skills/database-rds-resilience` directory.

**Option B: Upload as a zip file**

```bash
cd skills
zip -r database-rds-resilience.zip database-rds-resilience/ -i '*.md' '*.txt' '*.json' -x '*/evals/*'

Upload via Skills → Add skill → Upload skill in the DevOps Agent web app. Select agent types Chat tasks and Incident RCA.

Option C: Upload via the Asset API — see the AWS DevOps Agent User Guide.
How to Use This Skill
"Assess resilience for my Aurora cluster prod-orders-1"
"What's my actual RTO if I lose this region?"
"Am I blocked from setting up Aurora Global Database on this cluster?"
"Check my account for cross-region DR quota pressure"
"Why would my snapshot restore take longer than expected?"
Skill Structure
database-rds-resilience/
├── SKILL.md                              # Main skill instructions
├── README.md                             # This file
├── CHANGELOG.md                          # Version history
├── references/
│   ├── blocker-catalog.md                # 66 blockers across 7 categories
│   └── remediation-playbooks.md          # CLI remediation + report format
└── evals/
    ├── evals.json                        # Functional test scenarios
    ├── eval_queries.json                 # Trigger tests
    └── report.json                       # Evaluation results
Safety

This skill operates in read-only mode:

    No DDL, DML, or DCL — no infrastructure changes of any kind
    All CLI commands in references/remediation-playbooks.md are for manual execution by an operator, with explicit prerequisite and impact call-outs where a remediation carries downtime or performance-impact risk (e.g., Multi-AZ conversion)

Non-production disclaimer

    ⚠️ This skill is sample code, not intended for production use without additional review and testing. Validate in a non-production environment first. Quota defaults and RTO/RPO estimates are general guidance and should be confirmed against your account's actual limits and tested recovery times.
