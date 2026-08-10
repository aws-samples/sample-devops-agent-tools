# Fleet Orchestration

This document defines the multi-bucket review behavior. Load this file ONLY when the input contains more than one bucket. Single-bucket reviews do not use this file.

## Routing Thresholds

| Input count | Behavior |
|---|---|
| 2-10 | Single pass, summary matrix + full details for all buckets |
| 11-20 | Single pass, summary matrix + details for Low-rated only |
| 21+ | Batched (10 per batch), manifest tracking, resume support |

## Execution Flow (Fleet)

1. Parse all bucket names from input
2. Group buckets by account (for caching)
3. For each unique account: query account-level BPA once, cache result
4. For each bucket: collect configuration per `references/data-collection.md`
   - Pass cached account-level BPA to avoid redundant calls
5. For each bucket: apply finding logic from `references/finding-logic.md`
6. Compute resiliency rating per bucket
7. Sort results (see Sort Options)
8. Render fleet report (see Fleet Report Structure)
9. If batched: save manifest, present batch summary, ask to continue

## Caching Strategy

| Data | Scope | Cache key | Benefit |
|---|---|---|---|
| Account-level BPA | Per account | `account_id` | 20 buckets in 4 accounts = 4 calls, not 20 |
| CloudTrail trails | Per account + region | `account_id:region` | Trails are account-wide; query once per account |
| Bucket region | Per bucket | `bucket_name` | Each bucket may be in a different region |

**Cache lifetime:** One fleet review run. Do not persist cache across sessions.

## Batching (21+ buckets)

### User notification

When >20 buckets are provided, present:

> ⚠️ You've provided **<N> buckets**. For reliable results, I'll process these in batches of 10.
>
> - Estimated batches: <ceil(N/10)>
> - Each batch takes ~2-3 minutes
> - Progress tracked in `reports/s3-resiliency/fleet-<YYYY-MM-DD>-manifest.json`
>
> I'll produce a per-batch summary after each batch and a consolidated fleet summary when all batches are complete. You can pause between batches and resume later with "continue the fleet review."
>
> Ready to start batch 1?

Wait for user confirmation before starting.

### Manifest file

Created at: `reports/s3-resiliency/fleet-<YYYY-MM-DD>-manifest.json`

```json
{
  "fleet_review_id": "fleet-<YYYY-MM-DD>-<sequence>",
  "created": "<ISO timestamp>",
  "total_buckets": <N>,
  "batch_size": 10,
  "sort": "rating",
  "buckets": [
    {
      "name": "<bucket-name>",
      "account_id": "<discovered or null>",
      "region": "<discovered or null>",
      "batch": 1,
      "status": "completed|pending|failed",
      "rating": "High|Medium|Low|null",
      "critical_findings": <count>,
      "warning_findings": <count>
    }
  ],
  "batches": [
    {
      "batch": 1,
      "status": "completed|in_progress|pending",
      "started_at": "<ISO timestamp or null>",
      "completed_at": "<ISO timestamp or null>",
      "results_file": "reports/s3-resiliency/fleet-<date>-batch-01.md"
    }
  ],
  "summary": {
    "reviewed": <count>,
    "pending": <count>,
    "failed": <count>,
    "high": <count>,
    "medium": <count>,
    "low": <count>
  },
  "previous_run": "<fleet_review_id or null>"
}
```

### Resume support

When the user says "continue the fleet review" or similar:
1. Look for the most recent manifest in `reports/s3-resiliency/fleet-*-manifest.json`
2. Find the first batch with `status: "pending"`
3. Resume from that batch
4. After all batches complete, generate the consolidated fleet summary

### Batch failure handling

If a bucket fails (data collection returns an error, e.g., bucket not found):
- Mark that bucket as `status: "failed"` in the manifest
- Continue with remaining buckets in the batch
- Include failed buckets in the summary with a note
- Do NOT abort the entire batch for one failure

## Sort Options

| Sort | Trigger keywords | Behavior |
|---|---|---|
| Rating (default) | No sort specified | ❌ Low first, then ⚠️ Medium, then ✅ High. Within same rating: alphabetical. |
| Input order | "keep order", "in order", "as listed" | Preserve the sequence the user provided |
| Size | "by size", "largest first" | Requires size data from list-multipart-uploads or bucket metrics |

## Fleet Report Structure

### For 2-20 buckets (single file)

```markdown
# S3 Fleet Resiliency Review — <N> Buckets

## Summary

- **Buckets reviewed:** <N>
- **Accounts:** <unique account count>
- **Date:** <YYYY-MM-DD>
- **Data source:** AWS control-plane APIs

### Resiliency Distribution

| Rating | Count | Buckets |
|---|---|---|
| ✅ High | <N> | <bucket names, comma-separated> |
| ⚠️ Medium | <N> | <bucket names> |
| ❌ Low | <N> | <bucket names> |

### Common Gaps (sorted by frequency)

| Gap | Count | % | Affected Buckets |
|---|---|---|---|
| <gap description> | <N> | <pct> | <bucket names> |

### Dimensions Matrix

| Bucket | Region | Acct | Ver | Repl | OLock | Policy | BPA | Enc | Own | Log | Web | Rating |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| <bucket> | <region> | ...<last 4> | <emoji> | <emoji> | <emoji> | <emoji> | <emoji> | <emoji> | <emoji> | <emoji> | <emoji> | <emoji> <rating> |

## Bucket Details

<Per-bucket detail sections>

## References

<Consolidated references — deduplicated>
```

### For 21+ buckets (batched)

Each batch: `reports/s3-resiliency/fleet-<date>-batch-<NN>.md`
Consolidated: `reports/s3-resiliency/fleet-<date>-summary.md`

## Detail Threshold

| Bucket count | Detail for |
|---|---|
| 2-10 | All buckets |
| 11-20 | ❌ Low-rated only + any degraded since last review |
| 21+ | ❌ Low-rated only + any degraded since last review |

**On request:** "full details" or "all details" → show all regardless of rating.

## Incremental Diffing

### When to diff

If a previous manifest exists with overlapping bucket names (>50% overlap), automatically load it and produce a diff section.

### Diff output

```markdown
## Changes Since Last Review (<previous date>)

| Bucket | Previous | Current | Change |
|---|---|---|---|
| <bucket> | ❌ Low | ⚠️ Medium | ⬆️ Improved (<reason>) |
| <bucket> | ⚠️ Medium | ❌ Low | ⬇️ Degraded (<reason>) |
| <bucket> | — | ❌ Low | 🆕 New (first review) |

### Resolved Findings
- <bucket>: <check name> → now <new status> ✅

### New Findings
- <bucket>: <check name> → <new finding summary> ❌

### Unchanged (still Low)
- <bucket>: No changes — <brief description of persistent gaps>
```

### Diff logic

For each bucket present in both current and previous:
1. Compare ratings: improved (⬆️), degraded (⬇️), unchanged
2. Compare per-check severities: identify which checks changed
3. For degraded buckets: always include in detail section

For buckets in current but not previous: mark as 🆕 New
For buckets in previous but not current: note as "Dropped from review set"

## Pre-flight (Fleet)

Run pre-flight per bucket. Aggregate results:

- If ALL buckets have clean pre-flight → proceed silently
- If SOME buckets have AccessDenied → present summary:

> ⚠️ Pre-flight found access gaps for **<N>/<total>** buckets:
>
> | Bucket | Gaps |
> |---|---|
> | <bucket> | <check1>, <check2> |
>
> How would you like to proceed?
> 1. **Skip affected buckets** — review only the <M> buckets with full access
> 2. **Continue with reduced accuracy for all** — affected buckets get partial reports
> 3. **Stop and investigate**

Wait for user response.

## Critical Rules (Fleet-specific)

- **Cache is per-run only.** Do not persist across sessions.
- **Manifest is the source of truth for resume.**
- **One collection pass per bucket.**
- **Failed buckets don't block the batch.**
- **Sort applies to final output, not execution order.**
- **Diff is automatic when a previous run exists.**
