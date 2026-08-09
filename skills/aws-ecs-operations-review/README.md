# ECS Operations Review — AWS DevOps Agent Skill

An end-to-end Amazon ECS operational review skill for [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html). It assesses an ECS service against the ECS Best Practices Guide across six review pillars and produces a prioritized, remediation-linked report artifact per service, plus recommended CloudWatch alarms for IDR onboarding.

It is **strictly read-only**: it uses only `describe*` / `list*` / `get*` AWS API operations (ECS, CloudWatch, IAM, Application Auto Scaling, ELB, ECR, EC2, GuardDuty, Compute Optimizer). It never runs a mutating call; remediations are drafted for human approval, not applied.

> ⚠️ **Non-production disclaimer.** This skill is sample code, not intended for
> production use without additional review and testing. Users should validate in
> a non-production environment first.

## What It Does

1. **Parse the service ARN** (`arn:aws*:ecs:*:*:service/*/*`) and validate region.
2. **Collect configuration data** across the API tier dependency chain — Tier 1 (`ecs.describeServices`) must succeed first; non-Tier-1 access errors mark dependent checks N/A and continue.
3. **Resolve the compute platform** — Fargate (± Spot), EC2 ASG capacity provider, ECS Managed Instances, launchType-only, or ECS Anywhere — from the capacity provider strategy and `ecs.describeCapacityProviders`; this decision drives check applicability in every pillar.
4. **Run pillar checks** — read `references/checks.md` (the index) first, then each `references/pillars/<pillar>.md` one at a time, grading ✓ / ✗ / N/A with evidence, severity, and a recommendation.
5. **Report** — write a per-service artifact following `references/report-format.md`: workload details, per-pillar scorecards for all 6 pillars, prioritized action plan, detailed findings, a Recommended CloudWatch Alarms table, access limitations, and a review summary.

Pillars graded: **Resiliency & HA (REL), Observability (OBS), Security (SEC), Operations (OPS), Performance (PERF), and Additional Analysis (ADD)**.

## Data Sources

| Source | Used for | Required? |
|--------|----------|-----------|
| Read-only AWS APIs (AWS CLI / SDK / AWS API MCP) | All configuration data across the six pillars | Yes |
| CloudWatch `getMetricStatistics` | 7-day baseline (CPU, memory, task count) + `describeAlarms` | Yes (limitation noted if <7 days) |
| AWS Knowledge MCP | Doc-link lookups for findings and alarm recommendations | Yes |

## Agent Types

Intended for these agent types (selected in the Operator Web App at upload time):

- **On-demand** — conversational invocation in Chat ("run an ECS operations review on service X", "ECS security review").
- **Evaluation** — proactive operational improvement recommendations.

Select **Generic** to make the skill available to all agent types.

## Prerequisites

### 1. An AWS DevOps Agent Space with the target AWS account

An existing [Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html) with the target AWS account configured as a cloud source.

### 2. Read-only permissions

The Agent Space IAM role needs read-only (`describe*` / `list*` / `get*`) access to: ECS, CloudWatch, CloudWatch Logs, IAM, Application Auto Scaling, Elastic Load Balancing v2, ECR, EC2/VPC, GuardDuty, and Compute Optimizer. The AWS managed **`ReadOnlyAccess`** policy (or a least-privilege subset of the above) is sufficient. No cluster-level access entry or kubectl connectivity is required — ECS is assessed entirely through AWS control-plane APIs.

### 3. AWS Knowledge MCP

Used for documentation-link lookups on findings and alarm recommendations. This is built into AWS DevOps Agent.

## Packaging the skill

From the directory **containing** `aws-ecs-operations-review/`:

```bash
zip -r aws-ecs-operations-review.zip aws-ecs-operations-review/ \
  -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' \
  -x '*/.git/*' '*/evals/*' '*/CHANGELOG.md' '*/README.md' '*.DS_Store'
```

The uploaded zip contains:

```
aws-ecs-operations-review/
├── SKILL.md                    # frontmatter + skill instructions (required)
└── references/
    ├── checks.md               # checks index (read first)
    ├── alarm-thresholds.md      # recommended CloudWatch alarm thresholds
    ├── common-checks-coverage.md
    ├── report-format.md
    └── pillars/                # one file per pillar (REL/OBS/SEC/OPS/PERF/ADD)
```

Upload-time constraints: `SKILL.md` required with `name` + `description` frontmatter; **no `scripts/` directory**. `evals/`, `README.md`, and `CHANGELOG.md` are dev-only and excluded above.

## Uploading to AWS DevOps Agent

> Reference: [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#uploading-a-skill)

1. Open the **Skills** page in your Agent Space Operator Web App.
2. **Add skill** → **Upload skill**.
3. Drag and drop `aws-ecs-operations-review.zip`.
4. Select agent types: **On-demand** and **Evaluation** (or **Generic**).
5. Review validation results → **Upload**.

## Usage

In Chat, use natural language:

- *"Run an ECS operations review on service `arn:aws:ecs:us-east-1:111122223333:service/prod/web`."*
- *"ECS security review for the `web` service in cluster `prod`."*
- *"Assess reliability and cost for my ECS services."*

The agent validates the service ARN, collects read-only AWS data, grades the six pillars, and writes a per-service review artifact.

## Evaluation

The `evals/` directory holds an evaluation harness:

- `eval_queries.json` — routing checks (does the right query trigger the skill?).
- `evals.json` — skill-knowledge evals (six pillars, read-only contract, ARN validation, coverage gate, alarm deliverable), run against `evals/files/service-context.json`.

Run them with your skill-eval runner. Record results in [`evals/TESTING.md`](evals/TESTING.md) (model × eval-suite pass-rate matrix) and re-run after any change to the frontmatter or workflow steps. Results are recorded from real runs, never fabricated.

## Severity

Internally the skill grades on `Critical / High / Medium / Low / Info` tiers; the report writer maps these to customer-facing descriptive labels (see [`references/report-format.md`](references/report-format.md)). It never emits internal severity numbers in customer-facing output.

## Source attribution

- [About AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html)
- [Amazon ECS Best Practices Guide](https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/intro.html)

## License

Internal use.
