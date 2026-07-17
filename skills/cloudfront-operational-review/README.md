# CloudFront Operation Review — AWS DevOps Agent Skill

A comprehensive Amazon CloudFront operational review skill for [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html). Conducts best-practices assessments aligned with the [Amazon CloudFront Developer Guide](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Introduction.html) and the [AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html) across security, reliability, performance, cost optimization, and operational excellence. Generates a shareable report artifact per distribution.

## ⚠️ Non-Production Disclaimer

This skill is provided as **sample code**. It is **not intended for production use without
additional review and testing**. Before relying on it:

- Validate it in a **non-production environment first**.
- Review the IAM permissions below against your organization's security policies.
- Confirm the findings and thresholds match your operational requirements — the severity
  levels in `references/findings-severity-catalog.md` are starting points, not mandates.

The skill is strictly **read-only** — it never modifies, creates, or deletes any CloudFront,
WAF, ACM, or CloudWatch resource — but you remain responsible for validating its behavior in
your environment.

## What It Does

When activated via Chat, this skill instructs the DevOps Agent to:

1. Discover CloudFront distributions in the account (CloudFront is a global service — no per-region loop).
2. Collect distribution config, origins, cache behaviors, policies, OAC/OAI, WAF association, ACM certificate, CloudFront Functions / Lambda@Edge, VPC origins, and tags.
3. Collect 7-day historical CloudWatch metrics (from `us-east-1`), standard access-log signals, and configuration-change notes.
4. Pull 3-month Cost Explorer data and proportionally estimate per-distribution monthly cost.
5. Analyze against the Well-Architected pillars (Security, Reliability, Performance, Cost Optimization, Operational Excellence).
6. Generate a shareable report artifact per distribution, named `cloudfront-review-<distribution-id>-<YYYY-MM-DD>.md`.

All data is gathered through native, read-only AWS APIs (`cloudfront`, `cloudwatch`, `logs`,
`wafv2`, `acm`, `ce`). The skill does **not** use internal tooling, custom scripts, or
non-AWS MCP servers.

## Agent Types

This skill is intended for the following agent types (selected in the Operator Web App at upload time):

- **On-demand** — conversational invocation in Chat ("review my CloudFront distribution", "CDN audit").
- **Evaluation** — proactive operational improvement recommendations.

Select **Generic** instead if you want the skill available to all agent types.

## Prerequisites

### 1. An AWS DevOps Agent Space with the target AWS account

You need an existing [Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html) with the target AWS account configured as a cloud source.

### 2. IAM permissions for the DevOps Agent's primary cloud-source role

The Agent Space's IAM role must have read access to the following. Most are covered by the
AWS managed policy [`AIDevOpsAgentAccessPolicy`](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-devops-agent-iam-permissions.html) — verify in your account before running the review. A per-skill policy block is provided in the repo's `cloudformation/devops-agent-skill-policies.yaml` (`EnableCloudFrontOperationalReview`).

- `cloudfront:ListDistributions`, `cloudfront:GetDistribution`, `cloudfront:GetDistributionConfig`
- `cloudfront:ListCachePolicies`, `cloudfront:ListOriginRequestPolicies`, `cloudfront:ListResponseHeadersPolicies`
- `cloudfront:ListOriginAccessControls`, `cloudfront:ListCloudFrontOriginAccessIdentities`
- `cloudfront:ListFieldLevelEncryptionConfigs`, `cloudfront:ListKeyGroups`, `cloudfront:ListFunctions`
- `cloudfront:ListVpcOrigins`, `cloudfront:GetVpcOrigin`
- `cloudfront:GetMonitoringSubscription`, `cloudfront:ListRealtimeLogConfigs`
- `cloudfront:ListTagsForResource`
- `wafv2:GetWebACLForResource` (Scope=CLOUDFRONT, called in `us-east-1`)
- `acm:DescribeCertificate` (called in `us-east-1`)
- `cloudwatch:GetMetricData`, `cloudwatch:GetMetricStatistics`, `cloudwatch:DescribeAlarms`, `cloudwatch:DescribeAlarmsForMetric`
- `logs:DescribeLogGroups`, `logs:FilterLogEvents`, `logs:GetLogEvents` (if real-time/CloudWatch logs are used)
- `s3:GetObject`, `s3:ListBucket` (only if you want the agent to read standard access logs from the S3 log bucket)
- `ce:GetCostAndUsage`
- `tag:GetResources` (optional — cross-service tag reporting)

The skill operates entirely in **read-only** mode: it never calls `Create*`, `Update*`,
`Delete*`, `Associate*`, `CreateInvalidation`, or `Tag*` CloudFront APIs.

### 3. Additional CloudWatch metrics (recommended)

For cache hit ratio, origin latency, and per-status error rates, enable a **monitoring
subscription** (additional metrics) on the distributions you want to review:

- CloudFront console → distribution → **Monitoring** → enable additional metrics, or
- `aws cloudfront create-monitoring-subscription --distribution-id <id> --monitoring-subscription RealtimeMetricsSubscriptionConfig={RealtimeMetricsSubscriptionStatus=Enabled}`

Additional metrics incur CloudWatch charges. Without them, the skill still produces a
complete report from default metrics, configuration, and access logs — it just derives cache
hit ratio from logs instead of the `CacheHitRate` metric.

### 4. Standard access logging (recommended)

For log-pattern analysis (Step 6), enable standard access logging so the skill can analyze
`x-edge-result-type`, `sc-status`, and TLS fields. If the log bucket is not readable by the
agent role, the skill reports "access logs not reachable" and relies on CloudWatch metrics.

## Uploading to AWS DevOps Agent

> Reference: [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#uploading-a-skill)

### 1. Package the skill

From the `skills/` directory in this repo:

```bash
cd skills
zip -r cloudfront-operational-review.zip cloudfront-operational-review/ \
  -x 'cloudfront-operational-review/evals/*'
```

The resulting `cloudfront-operational-review.zip` contains:

```
cloudfront-operational-review/
├── SKILL.md          # frontmatter + skill instructions (required)
└── references/
    ├── metrics-thresholds.md
    └── findings-severity-catalog.md
```

`evals/` is excluded from the upload to keep the zip small (it's only used for offline
evaluation).

Constraints (enforced at upload time):

- Total zip size ≤ **6 MB**.
- `SKILL.md` is required and must include `name` and `description` frontmatter.
- A `scripts/` directory is **not** allowed — uploads containing scripts are rejected.

### 2. Upload via the Operator Web App

1. Navigate to the **Skills** page in your Agent Space Operator Web App.
2. Click **Add skill** → **Upload skill**.
3. Drag and drop `cloudfront-operational-review.zip` (or browse to it).
4. Select agent types: **On-demand** and **Evaluation** (or leave **Generic** for all types).
5. Review the validation results.
6. Click **Upload**.

## Usage

In the DevOps Agent Chat, use natural language (don't name the skill — let the agent select it):

- *"Run a CloudFront operational review for all distributions."*
- *"Review my CloudFront distribution `E1A2B3C4D5E6F7` for best practices."*
- *"Audit my CDN security — WAF, TLS, and origin access."*
- *"Why is my CloudFront cache hit ratio low?"*
- *"CloudFront health check for `d111111abcdef8.cloudfront.net`."*
- *"ORR for our production CloudFront distributions."*

The agent will:

- Collect all data automatically (no prompts for confirmation).
- Use only read-only AWS APIs — no mutating calls, no external scripts.
- Generate a report artifact per distribution, named `cloudfront-review-<distribution-id>-<YYYY-MM-DD>.md`.

## Skill Contents

```
cloudfront-operational-review/
├── SKILL.md                            # main skill instructions (with frontmatter)
├── README.md                           # this file
├── CHANGELOG.md                        # version history
├── .skilleval.yaml                     # eval config (ignores README.md in audit)
├── references/
│   ├── metrics-thresholds.md           # CloudWatch metric thresholds & severity rules
│   └── findings-severity-catalog.md    # findings catalog mapped to Well-Architected pillars
└── evals/                              # evaluation data (not included in upload zip)
    ├── evals.json
    ├── eval_queries.json
    └── files/
        └── distribution-context.json
```

## Best-Practices Sections Covered

| # | Pillar | Reference |
|---|--------|-----------|
| 1 | Security (WAF, TLS, OAC, FLE, signed URLs, geo) | [Security in CloudFront](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/security.html) |
| 2 | Reliability (origin failover, origin health, VPC origins) | [Origin failover](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/high_availability_origin_failover.html) |
| 3 | Performance (cache hit ratio, compression, HTTP/3, Origin Shield) | [Optimizing caching](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/ConfiguringCaching.html) |
| 4 | Cost Optimization (price class, cache efficiency, idle distributions) | [Price classes](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/PriceClass.html) |
| 5 | Operational Excellence (alarms, logging, monitoring subscription, tagging) | [Monitoring CloudFront](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/monitoring-using-cloudwatch.html) |

## Severity Definitions

| Severity | Definition | SLA |
|----------|------------|-----|
| CRITICAL | Immediate risk to availability, security, or data integrity | 24–48 hours |
| HIGH | Significant gap that could lead to incidents | 1 week |
| MEDIUM | Notable improvement opportunity | 30 days |
| LOW | Minor optimization or hardening | When convenient |
| INFO | Observation, no action required | N/A |

## License

Apache-2.0. See the repository [LICENSE](../../LICENSE).
