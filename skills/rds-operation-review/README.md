# RDS Operation Review — AWS DevOps Agent Skill

A comprehensive Amazon RDS and Aurora operational review skill for [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html). Conducts best-practices assessments aligned with the [Amazon RDS Best Practices Guide](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_BestPractices.html), the [Aurora Best Practices Guide](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/CHAP_BestPractices.html), and the [AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html). Generates a shareable report artifact per resource.

## What It Does

When activated via Chat, this skill instructs the DevOps Agent to:

1. Discover RDS instances and Aurora clusters in the configured account/regions.
2. Collect database configuration, parameter groups, security groups, subnet groups, snapshots, and events.
3. Collect 7-day historical CloudWatch metrics, RDS log group entries, and 14-day RDS events.
4. Pull 3-month Cost Explorer data and proportionally estimate per-database monthly cost.
5. Analyze against five Well-Architected pillars (Security, Reliability, Performance, Cost Optimization, Operational Excellence) plus engine-specific guidance.
6. Generate a shareable report artifact per resource, named `rds-review-<resource>-<YYYY-MM-DD>.md`.

All data is gathered through native AWS APIs (`rds`, `cloudwatch`, `logs`, `ec2`, `kms`, `costexplorer`, `applicationautoscaling`, `pi`). The skill does **not** require Kubernetes (`k8s`), EKS, or Dante MCP servers.

## Agent Types

This skill is intended for the following agent types (selected in the Operator Web App at upload time):

- **On-demand** — conversational invocation in Chat ("review my Aurora cluster", "RDS health check").
- **Evaluation** — proactive operational improvement recommendations.

Select **Generic** instead if you want the skill available to all agent types.

## Prerequisites

### 1. An AWS DevOps Agent Space with the target AWS account

You need an existing [Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html) with the target AWS account configured as a cloud source.

### 2. IAM permissions for the DevOps Agent's primary cloud-source role

The Agent Space's IAM role must have read access to RDS, Aurora, CloudWatch, Logs, EC2, KMS, and Cost Explorer APIs. The AWS managed policy `AWSDevOpsAgent...ReadOnlyAccess` (or your custom equivalent) typically covers all of the following — verify in your account before running the review:

- `rds:Describe*`, `rds:ListTagsForResource`, `rds:DownloadDBLogFilePortion`
- `pi:DescribeDimensionKeys`, `pi:GetResourceMetrics` (Performance Insights — only if PI is enabled on the target databases)
- `cloudwatch:GetMetricData`, `cloudwatch:GetMetricStatistics`, `cloudwatch:DescribeAlarms`, `cloudwatch:DescribeAlarmsForMetric`
- `logs:DescribeLogGroups`, `logs:DescribeLogStreams`, `logs:FilterLogEvents`, `logs:GetLogEvents`
- `ec2:DescribeSecurityGroups`, `ec2:DescribeSubnets`, `ec2:DescribeVpcs`
- `kms:DescribeKey`, `kms:ListAliases`
- `application-autoscaling:DescribeScalableTargets`, `application-autoscaling:DescribeScalingPolicies`
- `ce:GetCostAndUsage`, `ce:GetReservationCoverage`, `ce:GetReservationUtilization`
- `tag:GetResources` (optional — for cross-service tag reporting)

The skill operates entirely in **read-only** mode: it never calls `Modify*`, `Reboot*`, `Restore*`, `Create*`, or `Delete*` RDS APIs.

### 3. Performance Insights (recommended)

For richer query-level analysis, enable Performance Insights on databases you want to review:

- RDS console → **Modify** the instance → **Performance Insights** → enable (free 7-day retention by default).
- Or `aws rds modify-db-instance --db-instance-identifier <id> --enable-performance-insights --apply-immediately`.

Without Performance Insights, the skill still produces a complete report from CloudWatch metrics, logs, and configuration data — it just won't include `db.load.avg` and top-SQL breakdowns.

### 4. CloudWatch Logs export (recommended)

For log-pattern analysis in Step 6, enable CloudWatch Logs export for the engine logs you care about:

- **MySQL/MariaDB**: `audit, error, general, slowquery`
- **PostgreSQL**: `postgresql, upgrade`
- **Oracle**: `alert, audit, listener, trace`
- **SQL Server**: `agent, error`

RDS console → **Modify** the instance → **Log exports** → select log types. The skill reports "log exports disabled" as a finding when this is missing, but cannot retrieve historical log events without it.

### 5. (Conditional) Network reachability for databases in private VPCs

The `rds`, `cloudwatch`, `logs`, and other AWS APIs the skill calls are **regional control-plane APIs** — they're reached over the public AWS API endpoints regardless of whether the database itself sits in a private VPC. There is **no need for a private connection** to run the operational review against a database with a private endpoint, because the skill only reads metadata via control-plane APIs (it never connects to the database engine on port 3306 / 5432 / 1521 / 1433).

If your account requires all AWS API traffic to traverse VPC endpoints (org-level guardrail), ensure the following [interface VPC endpoints](https://docs.aws.amazon.com/vpc/latest/privatelink/vpce-interface.html) are reachable from the agent's source path: `com.amazonaws.<region>.rds`, `com.amazonaws.<region>.monitoring` (CloudWatch), `com.amazonaws.<region>.logs`, `com.amazonaws.<region>.ec2`, `com.amazonaws.<region>.kms`. Cost Explorer is global (`ce.us-east-1.amazonaws.com`) and does not have a VPC endpoint.

If you separately want the agent to **execute SQL** inside a private database (out of scope for this skill — but a related capability), use the [DevOps Agent private connection](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-capabilities-for-aws-devops-agent-connecting-to-privately-hosted-tools.html) mechanism with an MCP server that fronts the database.

## Uploading to AWS DevOps Agent

> Reference: [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#uploading-a-skill)

### 1. Package the skill

From the `skills/` directory in this repo:

```bash
cd skills
zip -r rds-operation-review.zip rds-operation-review/ -x 'rds-operation-review/evals/*'
```

The resulting `rds-operation-review.zip` contains:

```
rds-operation-review/
├── SKILL.md          # frontmatter + skill instructions (required)
└── references/
    ├── best-practices-checklist.md
    └── metrics-thresholds.md
```

`evals/` is excluded from the upload to keep the zip small (it's only used for offline evaluation).

Constraints (enforced at upload time):

- Total zip size ≤ **6 MB**.
- `SKILL.md` is required and must include `name` and `description` frontmatter.
- A `scripts/` directory is **not** allowed — uploads containing scripts are rejected.

### 2. Upload via the Operator Web App

1. Navigate to the **Skills** page in your Agent Space Operator Web App.
2. Click **Add skill** → **Upload skill**.
3. Drag and drop `rds-operation-review.zip` (or browse to it).
4. Select agent types: **On-demand** and **Evaluation** (or leave **Generic** to make it available to all agent types).
5. Review the validation results.
6. Click **Upload**.

### 3. (Optional) Connect additional observability sources

For richer analysis, connect your observability tools to the Agent Space:

| Tool | Setup Guide |
|------|-------------|
| CloudWatch | Built-in (no setup needed) |
| Datadog | [Connecting Datadog](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-telemetry-sources-connecting-datadog.html) |
| Dynatrace | [Connecting Dynatrace](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-telemetry-sources-connecting-dynatrace.html) |
| New Relic | [Connecting New Relic](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-telemetry-sources-connecting-new-relic.html) |
| Splunk | [Connecting Splunk](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-telemetry-sources-connecting-splunk.html) |
| Grafana | [Connecting Grafana](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-telemetry-sources-connecting-grafana.html) |
| Custom MCP | [Connecting MCP Servers](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-capabilities-for-aws-devops-agent-connecting-mcp-servers.html) |

## Usage

In the DevOps Agent Chat, use natural language:

- *"Run an RDS operational review for all databases."*
- *"Review my Aurora cluster `prod-aurora` in `us-east-1` for best practices."*
- *"Audit RDS security and cost optimization across all regions."*
- *"Generate an RDS best-practices report for instance `analytics-pg`."*
- *"ORR for our production databases."*

The agent will:

- Collect all data automatically (no prompts for confirmation).
- Use only AWS APIs — no Kubernetes or external scripts.
- Generate a report artifact per resource, named `rds-review-<resource-name>-<YYYY-MM-DD>.md`.

## Skill Contents

```
rds-operation-review/
├── SKILL.md                           # main skill instructions (with frontmatter)
├── README.md                          # this file
├── references/
│   ├── best-practices-checklist.md    # checklist mapped to RDS / Aurora best practices
│   └── metrics-thresholds.md          # CloudWatch metric thresholds & severity rules
└── evals/                             # evaluation data (not included in upload zip)
```

## Best-Practices Sections Covered

| # | Pillar | Reference |
|---|--------|-----------|
| 1 | Security (Network, Encryption, IAM auth, Audit logging) | [RDS security](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.html), [Encryption](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.Encryption.html) |
| 2 | Reliability (Multi-AZ, Backups, DR, Replication) | [Multi-AZ](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html), [Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html) |
| 3 | Performance (CPU, Memory, IOPS, PI, Connection mgmt) | [Monitoring RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_Monitoring.html), [Performance Insights](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.html) |
| 4 | Cost Optimization (RIs, gp3, Graviton, idle DBs) | [Cost-optimized RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_BestPractices.html#CHAP_BestPractices.Optimization) |
| 5 | Operational Excellence (Alarms, Events, Maintenance, Tagging) | [RDS events](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_Events.html), [Well-Architected Operational Excellence](https://docs.aws.amazon.com/wellarchitected/latest/operational-excellence-pillar/welcome.html) |
| Engine-specific | MySQL / PostgreSQL / Oracle / SQL Server / Aurora tuning | linked in SKILL.md |

## Severity Definitions

| Severity | Definition | SLA |
|----------|------------|-----|
| CRITICAL | Immediate risk to availability, security, or data integrity | 24–48 hours |
| HIGH | Significant gap that could lead to incidents | 1 week |
| MEDIUM | Notable improvement opportunity | 30 days |
| LOW | Minor optimization or hardening | When convenient |
| INFO | Observation, no action required | N/A |

## License

Internal use.
