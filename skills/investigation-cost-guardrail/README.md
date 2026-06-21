# Investigation Cost Guardrail

A pre-investigation cost estimation skill for AWS DevOps Agent. It queries the actual data volume an investigation would scan, calculates the dollar cost **before** any query runs, and cancels if the cost exceeds a configurable threshold. It always guides the user on how to reduce scope and cost.

## ⚠️ Disclaimer

This skill is sample code provided for educational and demonstration purposes. It is **not intended for production use** without additional review, testing, and validation. Validate in a non-production environment first and adjust thresholds, IAM permissions, and log group scoping to match your organization's requirements.

## Why This Skill Exists

AWS DevOps Agent works backwards from the incident, identifying the right observability signals, querying the right data sources, and correlating findings across services to help reduce MTTR and minimize customer impact.

In large production environments with high log ingestion rates, a broad investigation can scan significant data volumes. This skill gives operators visibility into that cost upfront, so they can make an informed decision: proceed as-is, narrow the scope, or approve a higher budget. It answers the question "How much will this investigation cost me?" before the first query runs.

## The Insight

You can estimate CloudWatch Logs Insights cost **before** running a single query.

CloudWatch publishes an `IncomingBytes` metric for every log group: exactly how many bytes were ingested during any time period. Since Logs Insights charges per GB scanned, and the scan volume equals the data within the query's time window, you can calculate the cost in advance:

```
cost = IncomingBytes(time_window) / 1e9 × regional_rate
```

Because the estimate is grounded in the log group's actual ingested volume rather than a heuristic, it reflects what a query over that window will genuinely scan. Volume is computed in GB, and `regional_rate` is resolved at runtime via the AWS Price List API. A representative Logs Insights rate is ~$0.005/GB, though it varies by region.

The same before-you-run principle extends to the other cost components: GetMetricData is priced from the metric-and-period count, X-Ray from the trace count, and cross-region transfer from the result bytes returned, each at its own per-region rate.

## How It Works

Before any investigation begins, the guardrail:

1. **Plans:** identifies every data source the investigation will call (CloudWatch log groups, metric namespaces, X-Ray, CloudTrail) and the regions involved, then builds a step-by-step investigation plan
2. **Measures:** quantifies the real volume for each step, scoped to the user's time window: bytes to scan per log group (`IncomingBytes`), metrics × periods to fetch, traces to pull, and any cross-region results
3. **Prices:** calculates the cost per step using the per-region rate, including cross-region data transfer where the workload and Agent Space regions differ
4. **Decides:** proceed (≤ threshold), or cancel and request operator approval (> threshold)

If no time window is provided, the guardrail fetches total `storedBytes` per log group (worst case) and cancels immediately, asking the user to provide a specific time window.

### Cost Components the Guardrail Estimates

The investigation can call several pay-per-use APIs. The guardrail estimates each and shows every line in the plan:

| Component | Billed on |
|---|---|
| CloudWatch Logs Insights | per GB scanned |
| CloudWatch GetMetricData | per 1,000 metrics requested |
| AWS X-Ray | per million traces scanned/retrieved |
| CloudWatch Contributor Insights | per million matching events |
| CloudWatch Live Tail | per streaming minute |
| Cross-region data transfer | $0.02/GB on results returned across regions |

Shown in the plan at **$0.00** for transparency (free or negligible): `CloudWatch Logs FilterLogEvents`, `CloudTrail LookupEvents`, `EC2/ECS/RDS Describe*`, and `GetMetricStatistics`.

### Estimate Accuracy

The figures shown are a pre-flight approximation intended to inform a proceed or cancel decision. Actual charges can differ, by design the skill leans conservative, favoring worst-case assumptions when data is uncertain, which is the appropriate bias for a guardrail.

## Scenarios

**Scenario A: Scoped investigation, within threshold**
```
User: "Investigate the ECS task crashes on payments-api, 14:00-14:30 UTC today"

  📋 Pre-Investigation Plan (threshold: $10.00)
  1. Logs Insights  /aws/ecs/payments-api    → 6.4 GB
  2. Logs Insights  /aws/ecs/payments-worker → 2.1 GB
  3. GetMetricData  6 metrics × 30 periods
  4. X-Ray traces   ~18,000

  💰 Cost Estimate:
  - Logs Insights:  8.5 GB × rate   = $0.0425
  - GetMetricData:  180 requests    = $0.0018
  - X-Ray:          18,000 traces   = $0.0090
  - Total:                            $0.0533

  ✅ PROCEEDING: $0.05 is within the $10.00 threshold.
```

**Scenario B: Broad investigation, exceeds threshold**
```
User: "Investigate intermittent platform latency over the last 7 days"

  📋 Pre-Investigation Plan (threshold: $10.00)
  1. Logs Insights  5 log groups, 7-day window → 2,850 GB
  2. GetMetricData  40 metrics × 2,016 periods
  3. X-Ray traces   ~3,200,000
  4. Cross-region   results us-east-1 ← eu-west-1

  💰 Cost Estimate:
  - Logs Insights:      2,850 GB × rate     = $14.25
  - GetMetricData:      80,640 requests     = $0.81
  - X-Ray:              3.2M traces         = $1.60
  - Cross-region xfer:  ~85 GB × $0.02      = $1.71
  - Total:                                    $18.37

  🚫 CANCELLED: estimated cost ($18.37) exceeds the $10.00 threshold.

  💡 Suggestions to reduce cost:
  - Narrow to a 1-hour window around the spike → ~$0.12
  - Target the specific service instead of all 5 log groups
  - Provide the exact error/latency signature → use FilterLogEvents (free)
  - "Same region only" → removes the $1.71 cross-region transfer

  Approve to proceed at $18.37, or provide a narrower scope.
```



## Cross-Region Data Transfer

When the Agent Space region differs from the workload region, query **results** returned across regions incur cross-region data transfer ($0.02/GB). The guardrail detects the mismatch and adds an estimated transfer cost based on the result bytes returned (aggregations return almost nothing; raw fetches can return more), not the full scanned volume.

## Benefits

| Capability | What It Gives the User |
|---|---|
| **Cost transparency** | See the exact cost of every investigation step before it runs |
| **Safe experimentation** | In dev/staging, teams exploring the agent's capabilities can keep costs minimal |
| **Production control** | Set a threshold and stay in control |
| **Smarter scoping** | When cancelled, the skill tells the user exactly how to make the investigation cost effective |
| **Visibility and choice** | Users decide: proceed, narrow scope, or approve a higher budget |

## Technical Implementation

The skill relies on AWS APIs that are themselves free or negligible:

- `logs:DescribeLogGroups`: `storedBytes` per log group (free)
- `cloudwatch:GetMetricStatistics` (`AWS/Logs` / `IncomingBytes`): actual bytes ingested in the window (~$0.01 per 1,000 requests)
- `pricing:GetProducts`: resolves the per-region rate at runtime (free)

The cost of running the guardrail itself is effectively **$0.00** (excluding agent compute time): a few free API calls that provide full cost visibility before any paid query executes.

## Required IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": [
    "logs:DescribeLogGroups",
    "cloudwatch:GetMetricStatistics",
    "pricing:GetProducts"
  ],
  "Resource": "*"
}
```

These are **read-only** calls with negligible cost. `pricing:GetProducts` is used for per-region rate lookups; if unavailable, the skill degrades to floor estimates flagged with ⚠️ rather than failing.

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `cost_threshold` | $10 | Estimated cost the agent may proceed up to without asking; see note below |
| `agent_space_region` | us-east-1 | Region where Agent Space is deployed |
| `workload_region` | (auto-detect) | Region where workloads/log groups reside |

The default `$10` threshold lets investigations proceed automatically when the estimate is under $10 and cancels, requesting explicit approval, when it is exceeded. Set it to `$0` for the most conservative mode, where the guardrail always shows the cost and cancels pending approval before running any paid query (this does not mean "only proceed when free"; it means nothing chargeable runs without the operator seeing the cost first). Adjust the value to match your team's risk tolerance.

## Uploading to AWS DevOps Agent

You can add this skill to your Agent Space in three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/investigation-cost-guardrail` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository — including this one — even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Zip the `investigation-cost-guardrail/` directory (only including allowed extensions):

   ```bash
   cd skills
   zip -r investigation-cost-guardrail.zip investigation-cost-guardrail/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
   ```

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the `investigation-cost-guardrail.zip` file (max 6 MB).
5. Select the agent type: **Incident RCA**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign the skill to the `INCIDENT_RCA` agent type. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

For more details, see [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) in the AWS DevOps Agent User Guide.

The skill is loaded on demand: the agent matches the skill's description against the task and chooses to load it, so it activates on investigation, debug, or root-cause requests.

> **Note on activation:** Skills are matched and loaded by the agent at its discretion, not auto-enforced by the platform, so activation depends on description matching and is not guaranteed for every investigation. To make the guardrail run reliably before any investigation, add an explicit instruction in your Agent Space configuration telling the agent to invoke this skill first, before executing investigation queries. You can also set your team's default threshold in that same instruction (for example, "use a cost threshold of $5 for all investigations"), which overrides the skill's built-in $10 default.

## Author

Ines Attia, Technical Account Manager, AWS Enterprise Support

