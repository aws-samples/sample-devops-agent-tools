---
name: investigation-cost-guardrail
description: Cost guardrail for AWS DevOps Agent that covers ALL AWS services and native agent tools. Before the agent makes any paid API call, this skill estimates cost, enforces budgets per investigation, detects expensive operations across all services (Athena queries, S3 scans, DynamoDB scans, SageMaker inference, PromQL, etc.), enforces time window requirements, monitors cumulative call volume, and cancels if thresholds are exceeded. This skill applies to ALL investigations regardless of which services are involved.
metadata:
  author: tqquresh, inesttia
  version: "2.1.0"
  aws-devops-agent-skills.agent-types: "Incident RCA"
  aws-devops-agent-skills.aws-services: "All"
  aws-devops-agent-skills.technical-domains: "Cost Optimization, Operations"
---
# Investigation Cost Guardrail Skill

## Overview

This skill provides cost guardrails for ANY AWS service and ALL native agent tools — not just a hardcoded list. It uses **heuristic classification** to determine whether an API operation is free or paid, estimates cost before execution, and enforces per-investigation budgets.

## Design Principle

Rather than listing every free/paid operation across 200+ AWS services, this skill uses three layers:

1. **Heuristic rules** — classify any operation based on naming patterns and behavior
2. **Known-paid registry** — explicit overrides for high-cost operations with pricing formulas
3. **Response validation** — detect metered usage from API response fields after execution

## Activation

This skill MUST be ALWAYS ACTIVE during investigations. It does NOT require user invocation.

## Fetch Live Rate Before Estimating

The **first time** an operation is classified **PAID** by Layer 2, fetch the live rate **before** estimating cost.

For **AWS operations**: read `references/pricing-reference.md` for the exact Pricing API call patterns, exceptions, caching rules, and failure handling.

For **non-AWS tools** (Splunk, Datadog, Grafana, etc.): use the cost model from Layer 0 directly — no live lookup available.

---

## Layer 0: Native Agent Tool Classification

Before Layer 1 heuristics, classify the agent's own tools. These are NOT `use_aws` calls but have distinct billing implications:

### Tool Cost Matrix

| Tool | Classification | Cost Model | Guardrail |
|---|---|---|---|
| `get_prometheus_metrics` | **PAID** | $0.01 / 1,000 metrics×periods (same billing meter as CloudWatch GetMetricData) | Track series × datapoints per call |
| `use_aws` | **VARIABLE** | Depends on operation — apply Layers 1–3 | Full heuristic pipeline |
| `use_azure` | **FREE** | Azure Reader role, no per-call billing | Track count only |
| `grafana_query_prometheus` | **CAUTION** | Depends on Grafana data source billing model | Track count, warn at 50+ |
| `use_datadog` | **CAUTION** | Datadog API rate limits (no per-call $ cost, but may throttle) | Track count, warn at 100+ |
| `use_splunk` | **PAID** | Splunk search license (per GB ingested/searched) | Treat like CW Logs StartQuery |
| `use_pagerduty` | **FREE** | PagerDuty API (rate limited, not per-call billed) | Track count only |
| `shell` | **CAUTION** | May invoke `aws`, `az`, `kubectl` — untracked by Layers 1–3 | Log commands, warn if aws/az detected |
| `subagent` | **PAID** | Counts toward agent-seconds billing ($0.0083/sec) | Track spawns, enforce total time |
| `fs_read`, `fs_write`, `fs_tree` | **FREE** | Local file I/O | No guardrail needed |
| `datetime` | **FREE** | Internal state ops | No guardrail needed |
| `write_scratchpad`, `read_scratchpad` | **FREE** | Internal state (may not be available in all environments) | No guardrail needed |
| `read_memories` | **FREE** | Internal memory recall | No guardrail needed |

### PromQL-Specific Controls

`get_prometheus_metrics` deserves special handling because:

- Cost = (number of series returned) × (number of datapoints per series) / 1000 × $0.01
- Maximum 500 series per query — a broad query hitting the cap costs ~$0.005 per period
- Range queries with small `step` multiply cost: `7d / 60s step = 10,080 datapoints × 500 series = 5M metrics`

**Before each PromQL call:**

```text
estimated_metrics = min(500, estimated_series) × (time_range_seconds / step_seconds)
estimated_cost = (estimated_metrics / 1000) * 0.01

if estimated_cost > $0.50:
    ⚠️ WARN — suggest narrower time range, larger step, or label filters
if estimated_cost > $2.00:
    🚫 HALT — require approval or suggest aggregation (sum, topk, avg)
```

**Cost reduction for PromQL:**

- Use `sum by (label)` to reduce series count
- Use `topk(N, ...)` to cap returned series
- Increase `step` (300s instead of 60s = 5× cheaper)
- Narrow time range (1h instead of 7d = 168× cheaper)

---

## Layer 1: Heuristic Classification

Before making ANY `use_aws` call, classify the operation using these rules IN ORDER:

### Rule 1: FREE by default — Metadata operations

An operation is FREE if it matches ALL of these:

- Verb is: `Describe`, `List`, `Get`, `Lookup`, `Check`, `Validate`, `Tag`, `Untag`
- It returns metadata/configuration (not data content or query results)
- It does NOT scan, process, or transform customer data

**Examples:** `DescribeInstances`, `ListFunctions`, `GetRole`, `LookupEvents`

> ⚠️ **Exception:** Some services charge per-request even for Get/List operations. Layer 2 overrides this heuristic for: **S3** ($0.0004/1K GET, $0.005/1K LIST), **SQS** ($0.40/1M requests after free tier), **Lambda Invoke** ($0.20/1M). When Layer 2 has an entry, it takes precedence over Rule 1.

> ⚠️ **Tool policy can override cost classification.** Some operations classified as FREE here (e.g., `cloudtrail:LookupEvents`) may be blocked by tool policy in certain environments. If an operation is denied, it costs $0.00 (never executed) — proceed with alternatives.

### Rule 2: PAID — Data-scanning operations

An operation is PAID if it matches ANY of these patterns:

| Pattern | Why It Costs Money | Examples |
|---|---|---|
| Verb contains `Query` or `Search` | Scans indexed data | `StartQuery`, `Search`, `StartQueryExecution` |
| Verb contains `Scan` | Full table/index scan | `Scan` (DynamoDB), `StartScan` |
| Verb contains `Execute` + processes data | Runs a computation | `StartQueryExecution` (Athena), `ExecuteStatement` |
| Verb contains `Invoke` + runs workload | Triggers compute | `InvokeEndpoint` (SageMaker), `Invoke` (Lambda) |
| Operation reads **content** (not metadata) | Data transfer | `GetObject` (S3, large), `GetLogEvents` (bulk), `BatchGetTraces` |
| Operation starts a **streaming session** | Per-time billing | `StartLiveTail`, `SubscribeToShard` |
| Operation name contains `Insights` | Analytics processing | `GetContributorInsights`, `StartQuery` |

### Rule 3: CAUTION — High-volume free operations

An operation is FREE but CAUTION if:

- It's a paginated List/Describe that could return thousands of results
- It has no built-in limit and the scope is broad (e.g., all resources in a region)

**Examples:** `ListObjectsV2` (large bucket), `ListMetrics` (unfiltered), `DescribeTasks` (large cluster)

### Rule 4: UNKNOWN — Cannot classify

If an operation doesn't clearly fit Rules 1–3:

- Treat as **CAUTION** (proceed but track)
- After execution, check response for metered fields (see Layer 3)
- If metered: add to the known-paid list for this session

---

## Layer 2: Known-Paid Registry

These operations have **confirmed pricing**. Always fetch the live rate via the Pricing API (see `references/pricing-reference.md`) before estimating. If the Pricing API call fails, halt — do not estimate from memory.

### Confirmed Paid Operations

| ServiceCode | Operation | Cost Formula | Estimation Method |
|---|---|---|---|
| `AmazonCloudWatch` | `StartQuery` | `scan_gb × rate` | Query `IncomingBytes` metric for time window |
| `AmazonCloudWatch` | `StartLiveTail` | Duration-based | Duration-based |
| `AmazonCloudWatch` | `GetMetricData` | `(metrics × periods) / 1K × rate` | Count metrics and periods |
| `AmazonCloudWatch` | `get_prometheus_metrics` | `(series × datapoints) / 1K × rate` | Count series × (range/step) |
| `AmazonCloudWatch` | `GetInsightRuleReport` | `rules × (events / 1K) × rate` | Count rules and event volume |
| `AWSXRay` | `GetTraceSummaries` | `traces / 1M × rate` | Paginate or sample to estimate count |
| `AWSXRay` | `BatchGetTraces` | `traces / 1M × rate` | Count trace IDs in request |
| `AmazonAthena` | `StartQueryExecution` | `scan_tb × rate`; min 10MB | Check table metadata; require `WHERE` clause |
| `AmazonDynamoDB` | `Scan` | RCU consumed | Check `TableSizeBytes`; BLOCK unless user approves |
| `AmazonDynamoDB` | `Query` | RCU consumed | Check `ItemCount`; warn if > 10K items |
| `AmazonS3` | `GetObject` | See pricing-reference.md | Count requests; flag if cross-region or >100MB |
| `AmazonS3` | `ListObjectsV2`, `ListObjects` | See pricing-reference.md | Count calls; warn if paginating heavily |
| `AmazonS3` | `PutObject`, `CopyObject` | See pricing-reference.md | Count calls |
| `AmazonS3` | `SelectObjectContent` | `scan_gb × rate` | Check object size |
| `AWSResourceExplorer2` | `Search` | Per query | Count calls |
| `AmazonSageMaker` | `InvokeEndpoint` | — | BLOCK — require explicit user approval |
| `AmazonKinesis` | `GetRecords` | `records / 1M × rate` | Estimate from shard count × duration |
| `AmazonSQS` | All operations | Per request | Count total SQS calls; usually negligible |
| `AWSLambda` | `Invoke` | Per request + compute | BLOCK unless user explicitly requests function execution |

---

## Layer 3: Response Validation

After ANY operation executes, check the response for metered fields:

### Metered Response Fields (indicates cost was incurred)

| Field Pattern | Meaning | Action |
|---|---|---|
| `BytesScanned`, `DataScanned` | Data scanning charge | Record GB scanned, add to running cost |
| `RecordsProcessed`, `ItemCount` | Record processing | Record count, estimate RCU/cost |
| `QueryExecutionId` + `DataScannedInBytes` | Athena scan | Add to cost at live rate |
| `TracesProcessedCount` | X-Ray processing | Add to cost at live rate |
| `ConsumedCapacity` | DynamoDB RCU/WCU | Add to cost at live rate |
| `ContentLength` > 100MB | Large object fetch | Flag for transfer cost |
| `NextToken` after 10+ pages | Pagination runaway | Trigger volume guardrail |
| `warnings` containing "500 series" | PromQL truncation | Flag max-cost query, suggest narrowing |

If a previously-unclassified operation returns metered fields:

1. Log it as a paid operation for this session
2. Add the cost to the running total
3. Warn the user: `⚠️ Discovered paid operation: <servicecode>:<operation> cost $X.XX`

---

## Budget Enforcement

### Per-Investigation Budget

The agent MUST mentally track a running cost estimate throughout the investigation. Since write_scratchpad/read_scratchpad are not available in all environments, budget enforcement is behavioral — the agent maintains the accumulator in its context window.

**At investigation start:**

```text
Budget: $10.00
Running cost: $0.00
Call counts: {}
```

**Before each PAID operation:**

```text
estimated_cost = estimate(operation)
if running_cost + estimated_cost > budget:
    🚫 HALT — show budget display
else:
    proceed
    # After execution:
    running_cost += actual_cost (from response fields or estimation)
    call_counts[servicecode] += 1
```

**Volume guardrails:**

```text
if call_counts[any_servicecode] > 200: ⚠️ WARN
if call_counts[any_servicecode] > 500: 🚫 HALT
if sum(all_call_counts) > 1000: 🚫 HALT
```

> ℹ️ If `write_scratchpad` becomes available in your environment, use it for persistent state across subagent boundaries. Check with: `search_user_tools("scratchpad")`. If found, store `{budget, running_cost, call_counts}` as JSON.

### Budget Display (on halt)

```text
📋 INVESTIGATION BUDGET STATUS
════════════════════════════════════════════════════════════
Budget:      $10.00
Spent:       $X.XX (Y paid operations)
Free calls:  Z operations (no cost)
PromQL:      X,XXX metrics×periods consumed ($X.XX)
Next op:     <servicecode>:<operation> — estimated $X.XX
Projected:   $X.XX (exceeds budget by $X.XX)

🚫 HALTED — would exceed $10.00 budget.
💡 Options:
  → Approve additional $X.XX to continue
  → Narrow the time window to reduce scan volume
  → Skip this operation and continue with free alternatives
  → End investigation with findings so far
```

---

## Time Window Enforcement

For ANY operation classified as PAID that scans data over a time range:

| Scenario | Action |
|---|---|
| User provided time window | ✅ Use it — estimate cost for that window |
| No time window, operation scans data | 🚫 CANCEL — show worst-case cost, ask for window |
| No time window, operation is bounded (single resource lookup) | ✅ Proceed — no scan involved |

**Key distinction:** "Get me the config of Lambda X" (bounded, free) vs. "Search logs for errors" (unbounded scan, needs window).

**PromQL-specific:** Range queries without explicit start/end default to "now" which is safe. But broad label selectors (`{}` with just metric name) can hit 500 series cap — always prefer specific labels.

---

## Cross-Region Detection

For EVERY paid operation:

```text
if target_region ≠ agent_space_region:
    fetch transfer_rate = transfer_rate_cache[target_region]
                       ?? live lookup (see references/pricing-reference.md)
    estimated_return_size = estimate_return_bytes(operation_type)
    transfer_cost = estimated_return_size × transfer_rate
    total_estimate += transfer_cost
    flag: "⚠️ Cross-region transfer: <target> → <agent_space>"
```

Return size heuristics:

- Aggregation queries (stats, count, group-by): ~KB (negligible)
- PromQL with aggregation (sum, topk): ~KB (negligible)
- PromQL range query (500 series × 10K points): ~50MB (flag ⚠️)
- Raw log/trace fetches: up to 100% of matched bytes
- Describe/List results: ~KB (negligible)
- Unknown: use 15% of scan volume as upper bound, flag ⚠️

---

## Cost Reduction Suggestions

When halting or warning, ALWAYS suggest free or cheaper alternatives:

### Generic Alternatives (apply to any service)

| Pattern | Free/Cheaper Alternative |
|---|---|
| Broad time window scan | Narrow to ±30 min around the incident |
| Multiple resource query | Target specific resource ID |
| Full scan (DynamoDB, Athena) | Add filter/WHERE/key condition |
| Analytics query for known string | Use free filter API (FilterLogEvents) — note: LookupEvents may be tool-policy-blocked in some environments |
| Cross-region operation | Suggest user run from workload region |
| Large object fetch | Use SelectObjectContent with SQL filter |
| Pagination explosion | Add limit, filter, or narrower scope |
| Broad PromQL (no label filters) | Add specific label matchers or use aggregation |
| PromQL small step (60s over 7d) | Increase to 300s+ or reduce time range |

### Service-Specific Alternatives

| Instead of... | Use... | Savings |
|---|---|---|
| `logs:StartQuery` | `logs:FilterLogEvents` (if searching for known string) | 100% |
| `cloudwatch:GetMetricData` (many) | `cloudwatch:GetMetricStatistics` (single) | ~100% |
| `get_prometheus_metrics` (broad) | Add `sum by (label)` or `topk(5, ...)` | 90%+ |
| `dynamodb:Scan` | `dynamodb:Query` with key condition | ~100% |
| `athena:StartQueryExecution` (full) | Add partition filter in `WHERE` | 90%+ |
| `xray:GetTraceSummaries` (broad) | Narrow time + add filter expression | 90%+ |
| `s3:GetObject` (large) | `s3:SelectObjectContent` with SQL | Variable |
