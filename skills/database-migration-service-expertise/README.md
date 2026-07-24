# DMS Operational Review — AWS DevOps Agent Skill

A comprehensive AWS Database Migration Service (DMS) operational review and troubleshooting skill for [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html). Conducts best-practices assessments, health checks, performance diagnostics, cost optimization reviews, and migration cutover guidance.

## What It Does

When activated via Chat, this skill instructs the DevOps Agent to:

1. Discover DMS replication instances, tasks, and endpoints in the configured account/regions.
2. Collect instance configuration, task settings, endpoint connectivity, table statistics, and version data.
3. Collect 7-day historical CloudWatch metrics and task logs.
4. Analyze against 5 assessment categories (Instance Health, Task Health, Endpoint Security, Performance, Cost Efficiency) using a weighted 100-point scoring model.
5. For troubleshooting: classify errors, run targeted diagnostics, and provide root cause analysis.
6. For cutover planning: validate pre-cutover checklist, guide execution, and document rollback procedures.
7. Generate a shareable report artifact per environment, named `dms-review-<instance-name>-<YYYY-MM-DD>.md`.

## Agent Types

This skill is intended for the following agent types (selected in the Operator Web App at upload time):

- **On-demand** — conversational invocation in Chat ("review my DMS environment", "troubleshoot DMS task failure").
- **Evaluation** — proactive operational improvement recommendations.

Select **Generic** instead if you want the skill available to all agent types.

## Prerequisites

### 1. An AWS DevOps Agent Space with the target AWS account

You need an existing [Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html) with the target AWS account configured as a cloud source.

### 2. IAM permissions for DMS read access

The Agent Space IAM role needs read-only permissions for DMS resources. The following managed policies cover the required access:

- `AmazonDMSReadOnlyAccess` — DMS describe/list operations
- `CloudWatchReadOnlyAccess` — CloudWatch metrics and logs
- `AWSHealthFullAccess` — AWS Health deprecation events (or scoped to DMS)

Alternatively, create a custom policy with these minimum actions:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dms:Describe*",
        "dms:List*",
        "dms:TestConnection",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "logs:FilterLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "health:DescribeEvents",
        "health:DescribeEventDetails"
      ],
      "Resource": "*"
    }
  ]
}
```

### 3. DMS task logging enabled

For troubleshooting and log analysis, DMS tasks must have CloudWatch Logs enabled. Tasks with `EnableLogging: false` will have limited diagnostic capability.

## Uploading to AWS DevOps Agent

> Reference: [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#uploading-a-skill)

### 1. Package the skill

From the `skills/` directory in this repo:

```bash
cd skills
zip -r database-migration-service-expertise.zip database-migration-service-expertise/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
```

The resulting `database-migration-service-expertise.zip` contains:

```
database-migration-service-expertise/
├── SKILL.md                    # frontmatter + skill instructions (required)
└── references/
    ├── dms-best-practices.md   # comprehensive best practices reference
    ├── dms-validation-checklist.md  # step-by-step validation criteria
    └── dms-version-reference.md     # version lifecycle and known issues
```

Constraints (enforced at upload time):

- Total zip size ≤ **6 MB**.
- `SKILL.md` is required and must include `name` and `description` frontmatter.
- A `scripts/` directory is **not** allowed — uploads containing scripts are rejected.

### 2. Upload via the Operator Web App

1. Navigate to the **Skills** page in your Agent Space Operator Web App.
2. Click **Add skill** → **Upload skill**.
3. Drag and drop `database-migration-service-expertise.zip` (or browse to it).
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

- *"Run a DMS operational review for all instances in us-east-1."*
- *"Review my DMS replication instance `prod-migrator` for best practices."*
- *"Troubleshoot CDC latency on task `oracle-to-aurora-cdc`."*
- *"Assess cost optimization opportunities for my DMS environment."*
- *"Help me plan a migration cutover for task `full-load-cdc-orders`."*
- *"Check DMS version deprecations and recommend upgrades."*

The agent will:

- Collect all data automatically (no prompts for confirmation).
- Cross-reference versions against known issues and deprecation timelines.
- Generate a report artifact per environment, named `dms-review-<instance-name>-<YYYY-MM-DD>.md`.

## Skill Contents

```
database-migration-service-expertise/
├── SKILL.md                           # main skill instructions (with frontmatter)
├── README.md                          # this file
├── CHANGELOG.md                       # version history
├── skilleval.yaml                     # evaluation config
├── references/
│   ├── dms-best-practices.md          # comprehensive DMS best practices
│   ├── dms-validation-checklist.md    # operations review validation criteria
│   └── dms-version-reference.md       # version lifecycle and known bugs
└── evals/                             # evaluation data (not included in upload zip)
    ├── evals.json                     # functional evaluation test cases
    ├── eval_queries.json              # trigger/routing test queries
    └── files/
        └── dms-context.json           # mock DMS environment for evals
```

`evals/` is for skill evaluation tracking and is **not** included in the upload zip.

## Assessment Categories

| # | Category | Weight | Scope |
|---|----------|--------|-------|
| 1 | Instance Health | 25 pts | Status, version, Multi-AZ, public access, storage, encryption |
| 2 | Task Health | 25 pts | Task status, table errors, CDC latency, logging |
| 3 | Endpoint Security | 20 pts | SSL mode, Secrets Manager, connectivity, SG rules |
| 4 | Performance | 15 pts | CPU, memory, swap, disk queue, CDC throughput |
| 5 | Cost Efficiency | 15 pts | Right-sizing, idle resources, Multi-AZ justification |

## Additional Capabilities

| Capability | Description |
|------------|-------------|
| Troubleshooting | Error classification, targeted diagnostics, log analysis |
| Migration Cutover | Pre-cutover checklist, execution steps, rollback procedures |
| Version Management | Deprecation tracking, upgrade guidance, known bug cross-reference |
| DMS Serverless | Suitability assessment, provisioned vs serverless evaluation |
| Homogeneous Migration | Native tool alternatives for same-engine migrations |
| Pre-Migration Assessment | Fleet Advisor, SCT integration, task assessment guidance |
| Data Validation | Validation failure investigation and remediation |

## Severity Definitions

| Severity | Definition | SLA |
|----------|------------|-----|
| CRITICAL | Immediate risk to migration integrity, data loss, or security | 24–48 hours |
| HIGH | Significant gap that could lead to task failures | 1 week |
| MEDIUM | Notable improvement opportunity | 30 days |
| LOW | Minor optimization or hardening | When convenient |
| INFO | Observation, no action required | N/A |

## License

⚠️ **This skill is sample code, not intended for production use without additional review and testing.** Users should validate all recommendations in a non-production environment first. Always test DMS configuration changes (version upgrades, task setting modifications, instance resizing) in a staging environment before applying to production migrations.
