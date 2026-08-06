# Investigation Cost Guardrail Extended

A cost guardrail skill for AWS DevOps Agent that covers **all AWS services and native agent tools**. Before the agent makes any paid API call, this skill classifies the operation, estimates the cost, enforces per-investigation budgets, and halts if thresholds are exceeded. It always guides the user toward cost-efficient alternatives.

## ⚠️ CRITICAL ACTIVATION REQUIREMENT:

The agent decides whether to load a skill based on description matching. To guarantee the cost guardrail skill activates every time, you **must** add a space-level instruction to your Agent Space configuration.

**Pre-flight checklist:**

1.  **Classify:** Determine if the operation is free or paid
    
2.  **Estimate:** Calculate expected cost using known formulas or heuristics
    
3.  **Check budget:** Compare estimated cost against per-investigation budget
    
4.  **Proceed or halt:** Execute if within budget; otherwise halt and suggest alternatives
    

**Recommended space-level instruction operators can paste into their Agent Space config:**

> 🚨 "Before executing any tool call that interacts with AWS, Azure, or third-party services, ALWAYS consult the cost guardrail skill to estimate cost and check budget. 
Never skip cost evaluation."

## Context

When an agentic system investigates an incident, it reasons about what data to fetch next. That reasoning is invisible to the operator until after the calls are made.

The downstream APIs that power investigations — CloudWatch Logs Insights, Athena, X-Ray, GetMetricData, DynamoDB Scans — all charge based on **volume**: gigabytes scanned, traces processed, capacity units consumed. A single broad query against a high-volume production log group can cost more than a hundred narrowly-scoped ones.

This skill adds cost awareness to the agent. It intercepts every paid operation before execution, estimates the cost, enforces a per-investigation budget, and presents the operator with cost-efficient alternatives when thresholds are approached. The operator stays in control without losing the speed advantage of agentic investigation.

## How It Works

Rather than hardcoding every free/paid operation across 200+ AWS services, this skill uses a **four-layer architecture** that covers any current or future service:

```
┌─────────────────────────────────────────────────┐
│ Layer 0: Native Tool Classification             │
│ • PromQL, use_azure, shell, subagent            │
│ • Explicit cost/free designation per tool       │
│ • Covers all agent-native and MCP tools         │
├─────────────────────────────────────────────────┤
│ Layer 1: Heuristic Rules                        │
│ • Verb-based classification (Describe=free)     │
│ • Pattern matching (Scan/Query/Execute=paid)    │
│ • Covers ANY current or future AWS service      │
├─────────────────────────────────────────────────┤
│ Layer 2: Known-Paid Registry                    │
│ • Exact pricing formulas for high-cost ops      │
│ • Extensible by operator configuration          │
│ • Athena, DynamoDB, S3, X-Ray, SageMaker...     │
├─────────────────────────────────────────────────┤
│ Layer 3: Response Validation                    │
│ • Detects cost AFTER execution via response     │
│ • Self-learning: reclassifies unknowns          │
│ • Catches new paid operations automatically     │
└─────────────────────────────────────────────────┘

```

Even if AWS launches a new service tomorrow, the heuristic rules will correctly classify most operations, and response validation will catch any paid operations that slip through.

### Layer 0: Native Agent Tools

| Tool | Classification | Cost Model | Guardrail |
| --- | --- | --- | --- |
| get_prometheus_metrics | **PAID** | $0.01 / 1,000 metrics×periods (same billing meter as CloudWatch GetMetricData) | Track series × datapoints per call |
| use_aws | **VARIABLE** | Depends on operation — apply Layers 1–3 | Full heuristic pipeline |
| use_azure | **FREE** | Azure Reader role, no per-call billing | Track count only |
| grafana_query_prometheus | **CAUTION** | Depends on Grafana data source billing model | Track count, warn at 50+ |
| use_datadog | **CAUTION** | Datadog API rate limits (no per-call $ cost, but may throttle) | Track count, warn at 100+ |
| use_splunk | **PAID** | Splunk search license (per GB ingested/searched) | Treat like CW Logs StartQuery |
| use_pagerduty | **FREE** | PagerDuty API (rate limited, not per-call billed) | Track count only |
| shell | **CAUTION** | May invoke aws, az, kubectl — untracked by Layers 1–3 | Log commands, warn if aws/az detected |
| subagent | **PAID** | Counts toward agent-seconds billing ($0.0083/sec) | Track spawns, enforce total time |
| fs_read, fs_write, fs_tree | **FREE** | Local file I/O | No guardrail needed |
| datetime | **FREE** | Internal state ops | No guardrail needed |
| write_scratchpad, read_scratchpad | **FREE** | Internal state (may not be available in all environments) | No guardrail needed |
| read_memories | **FREE** | Internal memory recall | No guardrail needed |

### Layer 1: Heuristic Classification

| Classification | Rule |
| --- | --- |
| **FREE** | Verb is Describe, List, Get, Lookup, Check, Validate, Tag: returns metadata only |
| **PAID** | Verb contains Query, Scan, Execute, Invoke, Insights: processes or scans data |
| **CAUTION** | Paginated List/Describe with broad scope |

### Layer 2: Known-Paid Registry

| Service | Operation | Cost Formula |
| --- | --- | --- |
| CloudWatch Logs | StartQuery | $0.005/GB scanned |
| CloudWatch Logs | StartLiveTail | $0.01/minute |
| CloudWatch | GetMetricData / PromQL | $0.01/1,000 metrics×periods |
| X-Ray | GetTraceSummaries, BatchGetTraces | $0.50/1M traces |
| Athena | StartQueryExecution | $5.00/TB scanned |
| DynamoDB | Scan | ~$0.25/1M RCU. **BLOCKED unless approved** |
| S3 | GetObject | $0.0004/1K requests + $0.09/GB transfer |
| S3 | SelectObjectContent | $0.002/GB scanned + $0.0007/GB returned |
| SageMaker | InvokeEndpoint | **BLOCKED: requires explicit approval** |
| Lambda | Invoke | **BLOCKED unless user explicitly requests** |
| Kinesis | GetRecords | $0.015/1M records |

**Operators can extend this registry:**

```
"Treat opensearch:Search as paid at $0.01 per 1000 requests."

```

### Layer 3: Response Validation (Self-Learning)

After execution, the skill checks response fields for metered indicators:

| Field | Meaning |
| --- | --- |
| BytesScanned, DataScanned | Scanning charge incurred |
| ConsumedCapacity | DynamoDB RCU/WCU consumed |
| TracesProcessedCount | X-Ray processing |
| DataScannedInBytes (Athena) | Athena scan |
| ContentLength > 100MB | Large object transfer |
| NextToken after 10+ pages | Pagination runaway |

If a previously-unclassified operation returns metered fields, it is reclassified as paid for the remainder of the investigation.

## Budget Enforcement

The skill maintains a **running cost accumulator** throughout each investigation using the scratchpad:

```
📋 INVESTIGATION BUDGET STATUS
═══════════════════════════════════════════════════════════
Budget:      $10.00
Spent:       $2.34 (12 paid operations)
Free calls:  47 operations (no cost)
Next op:     logs:StartQuery — estimated $3.20
Projected:   $5.54 (within budget)

✅ PROCEEDING

```

When the next operation would exceed the budget:

```
🚫 HALTED — would exceed $10.00 budget.

💡 Options:
  → Approve additional $X.XX to continue
  → Narrow the time window to reduce scan volume
  → Skip this operation and continue with free alternatives
  → End investigation with findings so far

```

### Volume Guardrails

| Threshold | Action |
| --- | --- |
| Single service > 200 calls | ⚠️ WARN |
| Single service > 500 calls | 🚫 HALT |
| Total calls > 1,000 | 🚫 HALT |

## Time Window Enforcement

| Scenario | Action |
| --- | --- |
| User provided time window | ✅ Estimate cost for that window |
| No time window, operation scans data | 🚫 CANCEL: show worst-case, ask for window |
| No time window, bounded lookup | ✅ Proceed: no scan involved |

## Cross-Region Detection

When the target region differs from the Agent Space region, the skill adds estimated data transfer cost ($0.02/GB) based on return size:

- Aggregation queries: ~KB (negligible)
- Raw log/trace fetches: up to 100% of matched bytes
- Unknown: 15% of scan volume as upper bound (flagged ⚠️)

## Cost Reduction Suggestions

When halting, the skill always suggests cost-efficient alternatives:

| Instead of... | Use... | Savings |
| --- | --- | --- |
| logs:StartQuery | logs:FilterLogEvents (known string) | 100% |
| cloudwatch:GetMetricData (many) | cloudwatch:GetMetricStatistics (single) | ~100% |
| get_prometheus_metrics (broad) | Add `sum by (label)` or `topk(5, ...)` | 90%+ |
| dynamodb:Scan | dynamodb:Query with key condition | ~100% |
| athena:StartQueryExecution (full) | Add partition filter in WHERE | 90%+ |
| xray:GetTraceSummaries (broad) | Narrow time + add filter expression | 90%+ |
| Broad time window | Narrow to ±30 min around incident | 90%+ |

## Scenarios

**Scenario A: Scoped investigation, within budget**

```
User: "Investigate the ECS task crashes on payments-api, 14:00-14:30 UTC today"

  📋 Budget: $10.00 | Spent: $0.00
  ✅ logs:StartQuery (payments-api, 30min)    → 6.4 GB  → $0.032
  ✅ logs:StartQuery (payments-worker, 30min) → 2.1 GB  → $0.011
  ✅ GetMetricData (6 metrics × 30 periods)             → $0.002
  ✅ X-Ray (18,000 traces)                              → $0.009

  📋 Budget: $10.00 | Spent: $0.054 | Remaining: $9.946
  ✅ PROCEEDING — investigation continues normally.

```

**Scenario B: No time window, cancels immediately**

```
User: "Investigate errors on the order-service"

  🚫 CANCELLED: No time window provided.
  💰 Worst-case estimate: $5.61 (289 GB across 3 log groups, full retention)

  💡 "When did the errors start?
      - 'last 30 minutes' → ~0.3 GB → $0.002
      - 'last 2 hours' → ~1.2 GB → $0.006
      - Or specify the error message → FilterLogEvents (free)"

```

**Scenario C: Athena full table scan blocked**

```
Agent attempts: athena:StartQueryExecution (full scan, no WHERE clause)

  🚫 HALTED: Athena full scan on table 'access_logs' (~2.4 TB)
  💰 Estimated cost: $12.00 (exceeds $10.00 budget)

  💡 Suggestions:
    → Add partition filter: WHERE dt = '2025-07-15' → ~$0.08
    → Add column projection: SELECT specific_columns
    → Use CloudWatch Logs if the data is also in a log group

```

**Scenario D: PromQL broad query warned**

```
Agent attempts: get_prometheus_metrics (no label filter, 7d range, 60s step)

  ⚠️ WARN: PromQL query would fetch ~5M metrics×periods
  💰 Estimated cost: $50.00

  💡 Suggestions:
    → Add label filters to reduce series count
    → Use topk(10, ...) to cap series
    → Increase step to 300s
    → Narrow time range to 1h

```

## Configuration

| Setting | Default | User Override |
| --- | --- | --- |
| Per-investigation budget | $10.00 | "Set budget to $5" |
| Single-service call warn | 200 | "Set call limit warn to 300" |
| Single-service call halt | 500 | — |
| Total call halt | 1,000 | "Set total limit to 2000" |
| PromQL cost warn | $0.50 per query | "Set PromQL warn to $1" |
| PromQL cost halt | $2.00 per query | — |
| Time window requirement | Strict for scans | "Skip time window check" (requires approval) |

### Operator Configuration (Agent Space instructions)

```
"Use a per-investigation budget of $5.00"
"Always require approval before any Athena query"
"Treat <service>:<operation> as paid at $X per <unit>"
"Block DynamoDB Scan operations entirely"
"Limit PromQL range queries to 1 hour maximum"
```

## How to Use the Skill

Add the skill to your Agent Space and adjust the threshold to match your organization's requirements:

**Option A:** Fork or copy the skill into your own GitHub repository and import it directly into your Agent Space via the GitHub integration. This lets you version and customize the skill independently.

**Option B:** Download the `.zip` directly from the [repository](https://github.com/aws-samples/sample-devops-agent-tools/tree/main/skills/investigation-cost-guardrail) and upload it as a skill in your Agent Space.

## Known Limitations

- **Budget is scoped to a single investigation:** each investigation starts with a fresh budget; cumulative tracking across multiple investigations at the agent space level is not currently supported.
- **Skill-halted investigations show "Completed" status:** halt reason is only visible in the investigation output.

