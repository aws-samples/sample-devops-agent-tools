---
name: investigation-cost-guardrail
description: Estimates the downstream AWS API cost of an incident investigation before the agent runs any CloudWatch Logs Insights, GetMetricData, X-Ray, or cross-region query, shows a per-step cost plan, and cancels if the estimate exceeds a threshold or no time window is provided. This skill applies ONLY to diagnosing a problem that has already happened (an incident, error, outage, alarm, latency spike, or failure) for example 'what happened', 'why is X down', 'look into this alarm', 'investigate the errors'. It does NOT apply to actions that create, change, deploy, scale, or configure resources (for example deploying a CDK or CloudFormation stack, or scaling a service), nor to architecture or Well-Architected reviews, cost or billing reports, inventory listing, or general how-to questions.
metadata:
  author: inesttia
  version: "1.0.0"
  aws-devops-agent-skills.agent-types: "Incident RCA"
  aws-devops-agent-skills.aws-services: "Amazon CloudWatch, AWS X-Ray, AWS CloudTrail"
  aws-devops-agent-skills.technical-domains: "Cost Optimization, Operations"
---

# Investigation Cost Guardrail

## Overview

This skill acts as a cost guardrail for DevOps Agent investigations. Before pulling any data, it:

1. Lists every resource the agent will query (CloudWatch Logs, Metrics, CloudTrail, X-Ray)
2. Queries the actual stored bytes and IncomingBytes metric of relevant CloudWatch Log Groups
3. Detects cross-region data transfer costs if the Agent Space and workload are in different regions
4. Produces a visual investigation plan showing every step, its estimated data volume, and cost
5. Makes a proceed/cancel decision based on total estimated cost vs. threshold (default: $10 — proceeds automatically when the estimate is under $10, cancels and requests operator approval when it is exceeded)
6. If cancelled: tells the user exactly what information is missing to make it cheaper

## Step 1: Parse the investigation request

You MUST determine:
- Which CloudWatch Log Groups are relevant to the investigation
- Which CloudWatch Metrics namespaces/dimensions to query
- Whether X-Ray or CloudTrail is needed
- Whether a time window was explicitly provided by the user
- **ALL AWS regions the investigation will touch** (see region rule below)

### Region determination (MUST follow this order)

DevOps Agent monitors resources across ALL regions in an account, and a single investigation can span multiple regions. You MUST derive each region from concrete evidence — never assume or default to us-east-1:

1. **From the resource ARN** — extract the region segment (e.g., `arn:aws:logs:eu-west-1:...` → `eu-west-1`)
2. **From the triggering alarm** — CloudWatch alarms are region-scoped; use the alarm's region
3. **From the log group / metric** — the region where it is queried
4. **From the topology** — if the investigation spans services in multiple regions, collect EVERY region involved, not just one

You MUST build a list of all involved regions. For each region, Step 2 queries its log groups and its own Pricing API rate separately (rates differ by region).

You MUST identify the Agent Space region (where DevOps Agent is deployed) to detect cross-region transfer in Step 3.

### Agent Space region detection

The Agent Space region is provided in the system context:
- ARN: `arn:aws:aidevops:<REGION>:<ACCOUNT>:agentspace/<ID>`
- Extract the region segment (e.g., `us-east-1`)

If it is not available, ask the user:
> "I need to know which region your DevOps Agent is deployed in to calculate cross-region data transfer costs. Which region is your Agent Space in?"

**Common mistake:** assuming workload region = Agent Space region. Always verify both independently.

You MUST NOT:
- Invent or assume a time window if the user didn't provide one (e.g., do NOT default to "last 1 hour")
- Assume or default any region for rate lookups — if a region cannot be derived from an ARN, alarm, or resource ID, use the worst-case/fallback estimate and ask the user to confirm the region

When inputs are missing (region, log group, or time window), do NOT stop at open-ended questions. Still produce the Step 4 visual plan with worst-case or fallback estimates (flagged ⚠️) and an explicit decision, then list what the user should provide. A missing time window always results in 🚫 CANCEL.

On failure: show the worst-case plan and CANCEL decision, then ask the user to clarify what service, resource, region, or time window is affected.

## Step 2: Fetch actual data volume

You MUST query real data. Do NOT estimate without API calls.

**CRITICAL**: You MUST use the AWS Pricing API to get the correct per-unit rate for the workload region. Do NOT hardcode $0.005/GB — rates vary by region. See [references/pricing-reference.md](references/pricing-reference.md) for exact API calls and region prefix mapping. If the Pricing API fails, fall back to the floor estimates in that file and flag with ⚠️.

**CRITICAL**: If a time window is provided (even a broad one like "last 60 days"), you MUST use Path A. Path B is ONLY for when no time window is provided at all.

### CloudWatch Logs Insights cost

#### Path A: Time window provided (any duration)

You MUST query the IncomingBytes CloudWatch metric for the exact time window:

```bash
aws cloudwatch get-metric-statistics \
  --namespace "AWS/Logs" \
  --metric-name "IncomingBytes" \
  --dimensions Name=LogGroupName,Value="<LOG_GROUP_NAME>" \
  --start-time "<START>" \
  --end-time "<END>" \
  --period <WINDOW_SECONDS> \
  --statistics Sum \
  --region <workload_region>
```

Calculate: `scan_gb = Sum / 1e9` → `cost = scan_gb × regional_rate`  (use 1e9 = GB, matching AWS billing — NOT 1024³, which is GiB and under-counts ~7%)

#### Path B: No time window provided

You MUST query describe-log-groups to get total storedBytes (worst case):

```bash
aws logs describe-log-groups \
  --log-group-name-prefix "<PREFIX>" \
  --region <workload_region>
```

Calculate: `scan_gb = storedBytes / 1e9` → `cost = scan_gb × regional_rate`  (1e9 = GB, matching AWS billing)

### CloudWatch GetMetricData cost

You MUST count the number of metrics and periods the investigation would request:

```
metrics_cost = (num_metrics × num_periods) / 1000 × regional_rate
```

For example: 4 metrics × 60 periods = 240 metric requests → $0.0024

### X-Ray cost (if applicable)

If the investigation would scan X-Ray traces, you MUST estimate:

```
xray_cost = estimated_traces / 1,000,000 × regional_rate
```

If X-Ray is not configured or not relevant, show $0.00 in the visual plan.

#### X-Ray trace count estimation

To estimate trace count for a time window:

```bash
aws xray get-trace-summaries \
  --start-time <START> \
  --end-time <END> \
  --region <workload_region>
```

**Preferred (precise):** paginate `get-trace-summaries` over the FULL window (follow `NextToken`) and sum `TracesProcessedCount`. This is the exact count of traces the investigation would scan — no extrapolation needed. Note: `get-trace-summaries` is itself a billable trace-fetch call, so this estimation has a small X-Ray cost of its own.

**Fallback (only for very large windows, less precise):** query a representative sample window and extrapolate `total_traces = (sample_count / sample_minutes) × total_minutes`. Trace volume is bursty, so flag this with ⚠️: "Trace count extrapolated from sample — actual may vary." Prefer the longest sample you can afford (not 5 min) to reduce error.

If X-Ray returns 0 traces, the service may not have tracing enabled — show $0.00 and note "X-Ray not configured or no traces in window".

### CloudTrail cost (if applicable)

**CloudTrail LookupEvents — FREE.** The standard `cloudtrail:LookupEvents` API (last 90 days of management events) has no cost. Investigations use only this — show it in the visual plan as `$0.00 — free` and do NOT estimate it.

#### CloudTrail limitations (platform may block LookupEvents)

⚠️ **Known issue:** the agent platform may block `cloudtrail:LookupEvents`. If this happens:

1. Show it in the visual plan:
   ```
   ⚠️ CloudTrail LookupEvents — BLOCKED BY PLATFORM
      → Cannot estimate CloudTrail activity
      → "What changed?" analysis will be unavailable
   ```
2. Inform the user:
   > "I won't be able to check what infrastructure changes occurred because CloudTrail access is blocked. The investigation will proceed without change analysis."
3. Do NOT fail the entire cost estimation — `LookupEvents` is free anyway ($0.00), so a block has no cost impact.

### CloudWatch Contributor Insights (if applicable)

If the investigation would use Contributor Insights rules:

```
contributor_insights_cost = num_rules × (matching_events / 1000) × regional_rate
```

### CloudWatch Live Tail (if applicable)

If the investigation would use Live Tail to stream logs in real time:

```
live_tail_cost = session_minutes × regional_rate
```

### Free APIs (include in visual plan with $0.00)

The following APIs have no cost but you MUST still show them in the visual plan for transparency:
- `CloudWatch Logs FilterLogEvents` — free
- `CloudTrail LookupEvents` — free
- `EC2/ECS/RDS Describe*` calls — free
- `CloudWatch GetMetricStatistics` — negligible ($0.01 per 1,000 requests)

### Fallback: Permission denied

If any CLI command fails, fall back to 5 GB per log group as a conservative estimate. You MUST flag this in the visual plan with ⚠️:

```
⚠️ /aws/ecs/prod-api — ESTIMATED (no permission)
   → Assumed: 5 GB │ Cost: $0.0250 (may be higher)
```

## Step 3: Calculate cross-region data transfer cost

DevOps Agent's Agent Space is in one region, but the resources it investigates are often in other regions. Cross-region cost applies ONLY to the **result bytes returned** across regions — NOT the scanned volume. The returned size depends on the query type, so estimate `returned_data_gb` accordingly:

```
for each workload_region ≠ agent_space_region:
    if aggregation/stats query (counts, group-by, summaries):
        returned_data_gb ≈ small  (results are tiny — use ~1% of scan_gb, or a few MB cap)
    elif raw-event / trace fetch (returns matching log lines or traces):
        returned_data_gb ≈ up to 100% of the matched bytes  (use matched/returned bytes if known)
    else (query type unknown):
        returned_data_gb ≈ scan_gb × 0.15   # rough UPPER-BOUND heuristic — flag with ⚠️
    data_transfer_cost += returned_data_gb × $0.02/GB
```

Prefer the actual returned-result size when the query type is known (aggregations return almost nothing; raw fetches can return a large share). The 0.15 factor is only a fallback when the query shape is unknown — label it ⚠️ as an upper-bound estimate, not a precise figure.

If a region matches the Agent Space region: no transfer cost for that region.
If all resources are in the Agent Space region: total data transfer cost = $0.00.

## Step 4: Show the visual investigation plan

You MUST present the visual plan to the user before the decision gate.

**You MUST always render this plan and an explicit decision, even when required inputs are missing or AWS credentials are unavailable.** If you cannot query real data (no credentials, permission denied, or the region/log group is unknown), still produce the plan using worst-case `storedBytes` or the 5 GB-per-log-group fallback, flag those lines with ⚠️, and state the decision. Do NOT replace the plan with open-ended clarifying questions: show the plan and the decision first, then list what is missing. When no time window is provided, the decision is always 🚫 CANCEL (show the worst-case plan and say "CANCELLED — no time window").

You MUST show:
- Every step the agent would take (even if cost = $0)
- Exact GB and exact cost per step
- Running total, threshold, and decision
- Cross-region transfer step with both regions labeled (if applicable)
- ⚠️ flag on any fallback estimates

See [references/example-output.md](references/example-output.md) for format examples.

## Step 5: Decision gate

You MUST apply these rules strictly:

| Condition | Decision | Action |
|-----------|----------|--------|
| No time window provided | 🚫 CANCEL | Show worst-case cost, ask for time window |
| Time window + cost < threshold (default $10) | ✅ PROCEED | Begin investigation |
| Time window + cost ≥ threshold | 🚫 CANCEL | Show cost, ask for operator approval or a narrower scope |

On CANCEL, you MUST:
- Show the visual plan
- List what's missing to make it cheaper
- Stop completely — do NOT proceed with any investigation

On PROCEED, you MUST:
- Show the visual plan
- State: "✅ PROCEEDING — Estimated cost: $X.XX (within $Y.YY threshold)"

## Step 6: Cost reduction suggestions (on cancel)

You MUST provide specific, actionable suggestions:

| User can provide | How it reduces cost | Estimated savings |
|-----------------|-------------------|-------------------|
| Exact timestamp (± 5 min) | Scans 10 min instead of all stored data | ~90%+ |
| Specific log group name | 1 group instead of N | Up to 90% |
| Known error message | Free FilterLogEvents instead of Logs Insights | 100% of Logs Insights cost |
| "Skip X-Ray" / "Skip CloudTrail" | Eliminates those cost components | Variable |
| "Same region only" | No cross-region transfer | Eliminates DT cost |

## Configuration

### Cost threshold

The skill uses a threshold to decide proceed/cancel. Default: `$10.00` — the agent proceeds automatically when the estimate is under the threshold and cancels (requesting approval) when it is exceeded.

The user can set a custom threshold for the session by saying:
- "Set cost threshold to $1.00"
- "Auto-approve investigations under $0.50"
- "Set threshold to $0" (most conservative — always cancel and require approval before any paid query)

Remember it for the session:

| Threshold | Behavior |
|-----------|----------|
| $0.00 | Always show plan, always require approval before any paid query (most conservative) |
| $10.00 (default) | Auto-proceed if under $10, cancel and request approval if exceeded |
| Custom | Auto-proceed if under, cancel if over |

To reset: "Reset cost threshold to default".

The operator can also set a persistent default in the Agent Space configuration instruction (e.g., "use a cost threshold of $5 for all investigations"), which overrides the built-in $10 default. A threshold the user states in the current conversation takes precedence for that session.

## Calibration guidance

You MUST NOT cancel or flag when:
- An alarm-triggered investigation has a narrow window (± 30 min) and targets a single resource — this is expected to be low-cost; run the estimation and proceed if under threshold
- The estimated cost is $0.00 because the log group has no data in the window — proceed, there's nothing to scan
- The user explicitly says "proceed anyway" or "I approve this cost" — respect the operator's decision

You MUST cancel when:
- No time window is provided, regardless of how small the log groups appear
- The estimated cost exceeds the threshold (default $10) — show the plan and cancel, requesting approval or a narrower scope
- The investigation would scan > 100 GB — flag with ⚠️ even if under threshold

"Cannot determine" is valid when:
- Log groups are encrypted and DescribeLogGroups doesn't return storedBytes
- IncomingBytes returns empty datapoints (log group may have no data in that window — cost is $0)

## References

- [Pricing Reference](references/pricing-reference.md) — exact per-unit costs for all APIs
- [Example Output](references/example-output.md) — full visual plan examples for all decision paths
- [CloudWatch Pricing](https://aws.amazon.com/cloudwatch/pricing/)
- [AWS Data Transfer Pricing](https://aws.amazon.com/ec2/pricing/on-demand/#Data_Transfer)
