# Report format — the ECS operations review artifact

The review produces one written artifact **per ECS service** (Markdown by default; render DOCX only if the user/orchestrator asks — build it from the same content, don't drop sections). It must show, for **every graded check** across all 6 pillars: the ✓ / ✗ / N/A state, the observed current state (evidence), and — for every ✗ — a detailed recommendation tagged Critical / High / Medium / Low with a link to the authoritative AWS source. This file defines the required structure. Do not abbreviate it away; a scorecard without current-state evidence and linked recommendations is incomplete.

## Contents

- Severity model
- Required artifact structure (§1–§7)
- Finding block format (§4)
- Coverage gate (do not skip)
- Customer-facing rules
- Evidence discipline
- Output mechanics

## Severity model

Every ✗ finding (and any Medium+ N/A worth flagging) carries one of four descriptive tiers. Use the check's own severity as the baseline, escalated/lowered by the blast radius observed on this service.

| Tier | Meaning |
|------|---------|
| 🔴 **Critical** | Active exposure or imminent outage/data-loss risk; fix now (e.g. privileged containers, plaintext secrets, public exposure). |
| 🟠 **High** | Serious gap likely to cause an incident (e.g. desiredCount=1 with no auto scaling on a prod service, no circuit breaker, over-permissive IAM). |
| 🟡 **Medium** | Best-practice deviation degrading efficiency/operability/defence-in-depth. |
| 🔵 **Low** | Optimization / hygiene; do when convenient. |

Never use internal severity numbers (Sev 1-5). Always say in the recommendation *why* you set the tier.

## Required artifact structure

```
# ECS Operations Review — {service name}
**Cluster:** {cluster}   **Account / Region:** {…}   **Compute platform:** {Fargate | Fargate + Spot | EC2 (ASG capacity provider) | EC2 (launchType-only) | Managed Instances | mixed — list all}
**Pillars graded:** Resiliency & HA · Observability · Security · Operations · Performance · Additional Analysis
**Review run:** {UTC}

## 1. Executive summary
- 2–4 sentences: overall posture, biggest risks, headline numbers.
- **Findings by severity:** 🔴 {n} Critical · 🟠 {n} High · 🟡 {n} Medium · 🔵 {n} Low
- **Checks:** ✓ {n} pass · ✗ {n} fail · N/A {n} (of {total})
- One-line posture statement per pillar (all 6).

## 2. Workload details (current state)
- Service config: desiredCount / runningCount, compute platform + capacity provider strategy (providers, base/weight, managed scaling targetCapacity where applicable), platform version, deployment controller + config, network mode, subnets/AZs, load balancer.
- Task definition: CPU/memory, container count, roles, log config.
- 7-day baseline metrics (CPU, memory, task count) — or a note if unavailable.

## 3. Prioritized action plan
All ✗ findings across all 6 pillars, ordered Critical → Low. Each row links to its detailed finding in §4.

| # | Severity | Pillar | Finding | Affected | Effort |
|---|----------|--------|---------|----------|--------|

## 4. Detailed findings
One block per ✗ (and per notable N/A), grouped by pillar, ordered by severity. Use the finding block format below.

## 5. Pillar scorecards
One table per pillar — ALL 6 — every check (pass, fail, N/A) with observed current state, so the reader sees full coverage.

| Check | Result | Severity | Current state (evidence) |
|-------|--------|----------|--------------------------|

## 6. Recommended CloudWatch alarms
The alarm table from `alarm-thresholds.md` (base ECS alarms + any Container Insights / LB / relative alarms that apply), marking which already exist (from `cloudwatch.describeAlarms`) vs are missing. Mandatory for IDR onboarding.

## 7. Access limitations / what was not assessed
Every check marked N/A due to access denial or missing data, with the API that failed and the manual follow-up.
```

## Finding block format (§4)

Every ✗ gets a block. Recommendations must be detailed and actionable — what to change, why it matters, and concrete steps/snippet — not a one-liner. Every block ends with at least one authoritative AWS link.

```
### [🟠 High] No Application Auto Scaling configured
**Pillar:** Resiliency & HA
**Current state:** No scalable target registered for service `svc` (from
`applicationautoscaling.describeScalableTargets`); desiredCount=1.
**Why this severity:** production service cannot absorb load spikes or replace lost
tasks beyond a single instance.
**Impact:** capacity events / task loss cause degradation or outage.
**Recommendation:**
1. Register a scalable target and a target-tracking policy on CPU or ALB
   request count. Example: {snippet}
2. Set minCapacity ≥ 2 for HA.
**References:**
- [ECS Service Auto Scaling](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-auto-scaling.html)
```

Pull each ✗ block's why/steps/snippet from the pillar file's recommendation and the doc link there; re-resolve the doc URL at report time via `aws___search_documentation` — never paste a URL you haven't confirmed resolves.

## Coverage gate — verify ALL of this before finalizing (a review that misses any is incomplete)

- **Every check ID** in all 6 pillar files appears in a §5 scorecard as ✓ / ✗ / N/A — **including passes**. Grade internally against the exact check IDs (REL/OBS/SEC/OPS/PERF/ADD); do **not** merge, invent, or drop checks.
- **All 6 pillars** have a scorecard table — never omit or truncate a pillar. Launch-type-specific checks that don't apply are N/A (Fargate-only N/A on EC2 and vice versa), not dropped.
- **7-day baseline metrics** (§2) collected via `cloudwatch.getMetricStatistics`, or the limitation noted.
- **Recommended CloudWatch alarms** table (§6) present, marking exist-vs-missing from `cloudwatch.describeAlarms`.
- **Every ✗** has a detailed §4 finding block with evidence, impact, remediation, and a resolvable AWS link.
- Anything unobtainable is **N/A with the real reason** (the failed API) — never silently omitted, never a guessed ✓/✗.

## Customer-facing rules

- Strip internal check IDs (REL1, SEC4, …) from the customer-facing report — present check names/descriptions. Grade with the IDs internally for the coverage gate.
- Use the four descriptive severity tiers, never internal Sev numbers.
- No internal tool names/aliases in the deliverable.

## Evidence discipline

- Every ✗ current-state quotes the actual AWS API value (field, count, ARN) — never a generic restatement.
- Never fabricate a finding, count, or link. If a check couldn't be evaluated, it's N/A in §5 and listed in §7 — not a guessed pass/fail.

## Output mechanics

- Default output is a per-service artifact / Markdown file (e.g. `ecs-review-{service}-{date}.md`).
- Render DOCX only when asked (e.g. UOPS/IDR DOCX deliverable) — build from the same content with `python-docx`, dropping no sections.
- The executive summary (§1), prioritized action plan (§3), all 6 pillar scorecards (§5), and the alarms table (§6) are mandatory.
