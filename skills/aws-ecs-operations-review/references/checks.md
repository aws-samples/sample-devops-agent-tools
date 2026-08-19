# ECS Operations Review Checks Index

Complete reference of all operations review checks for Amazon ECS services organized by the **6 review pillars**. Each pillar's checks live in its own file under [`pillars/`](pillars/) — read ONLY the pillar file(s) you are about to grade, one at a time.

**IMPORTANT:** These checks are a **minimum baseline**, not a fixed set. The agent MUST run all applicable checks AND may add additional checks based on what it discovers about the service. There is no maximum number of checks per pillar. When adding a check, use the next sequential ID in that pillar (e.g., REL15, SEC21).

**ACCESS LIMITATIONS:** When an AWS API call returns an access denied or authorization error, the agent MUST:
- Mark the dependent check(s) as N/A with observation: "Unable to assess — access denied on {{api_name}}. Manual verification recommended."
- Include a dedicated "Access Limitations" section in the report listing all checks that could not be evaluated
- Never silently skip checks — every check MUST have a definitive ✓, ✗, or N/A with explanation

**COMPUTE PLATFORM (decide FIRST, before grading any pillar):** Determine the service's compute platform from Tier 1/2 data and use it to resolve every "Applies To" column. Decision logic:

1. From `ecs.describeServices`: read `launchType` and `capacityProviderStrategy`.
2. If a `capacityProviderStrategy` is present, call `ecs.describeCapacityProviders` on every named provider and classify each:
   - `FARGATE` / `FARGATE_SPOT` → **Fargate** (Fargate Spot in use if `FARGATE_SPOT` appears in the strategy)
   - `autoScalingGroupProvider` present → **EC2 (ASG capacity provider)**
   - `managedInstancesProvider` present → **Managed Instances** (AWS-managed EC2 — no ASG, no agent/AMI management by the customer)
3. If only `launchType` is set (no strategy): `FARGATE` → **Fargate**; `EC2` → **EC2 (launchType-only — no capacity provider; note this for PERF7)**; `EXTERNAL` → **ECS Anywhere** (mark cloud-compute-specific checks N/A with reason).
4. Mixed strategies (e.g., EC2 ASG + Fargate Spot, or base/weight splits) are valid — grade the checks applicable to EACH platform present, and say so in the report.

Record the resolved platform(s) in the report header and Workload Details. "Applies To" values used in the pillar files: **All**, **Fargate**, **EC2** (ASG capacity provider or launchType EC2), **EC2-ASG-CP** (only when an ASG capacity provider exists), **MI** (Managed Instances), **CP-strategy** (any service using a capacity provider strategy), **LB-attached**. Mark non-matching checks N/A with the platform as the reason — never silently skip. For Managed Instances, agent/AMI lifecycle checks (OPS6, OPS7) and ASG-specific checks (REL12, PERF9, PERF10) are N/A because AWS manages the instances.

---

## Pillar files

Read each file only when running that pillar's checks:

| Pillar | File | Check IDs | Count |
|--------|------|-----------|-------|
| Resiliency and High Availability | [`pillars/resiliency.md`](pillars/resiliency.md) | REL1-REL14 | 14 |
| Observability | [`pillars/observability.md`](pillars/observability.md) | OBS1-OBS9 | 9 |
| Security | [`pillars/security.md`](pillars/security.md) | SEC1-SEC20 | 20 |
| Operations | [`pillars/operations.md`](pillars/operations.md) | OPS1-OPS8 | 8 |
| Performance | [`pillars/performance.md`](pillars/performance.md) | PERF1-PERF11 | 11 |
| Additional Analysis & Recommendations | [`pillars/additional-analysis.md`](pillars/additional-analysis.md) | ADD1-ADD7 | 7 |

Total baseline checks: **69** (varies per service based on compute platform — platform-specific checks are N/A where they don't apply, per the COMPUTE PLATFORM rules above).

The shared **`review-common`** baseline (tagging, encryption, IAM least-privilege, alarms, logging, cost) is covered by these checks — see [`common-checks-coverage.md`](common-checks-coverage.md) for the crosswalk.

Recommended CloudWatch alarms for the Observability pillar live in [`alarm-thresholds.md`](alarm-thresholds.md).

---

## Summary

| Pillar | Check IDs | Focus |
|--------|-----------|-------|
| Resiliency and High Availability | REL1-REL14 | Multi-AZ, desired count, circuit breaker, deployment alarms, auto scaling, health checks, subnet AZ spread, managed termination protection, deregistration delay, capacity provider infrastructure multi-AZ |
| Observability | OBS1-OBS9 | Container Insights, logging, log retention, distributed tracing, CloudWatch alarms, CPU/memory baselines |
| Security | SEC1-SEC20 | IAM least privilege, network mode, privileged containers, secrets, ECR scanning, security groups, VPC endpoints, private connectivity, encryption at rest, encryption in transit (TLS), VPC Flow Logs, GuardDuty Runtime Monitoring |
| Operations | OPS1-OPS8 | Tagging, IaC-managed, platform version, ECS Exec, health checks, agent version, AMI currency |
| Performance | PERF1-PERF11 | Rightsizing, auto scaling, resource limits, capacity provider strategy, managed scaling / targetCapacity, CapacityProviderReservation baseline, base/weight design, Compute Optimizer |
| Additional Analysis & Recommendations | ADD1-ADD7 | Graviton, Fargate Spot, Service Connect, image tags, CloudWatch Logs Insights queries, Managed Instances evaluation |

## Context Management

Do NOT read all 6 pillar files at once. Read this index first, then read each `pillars/<pillar>.md` file only when you are about to run that pillar's checks. After analyzing a pillar, extract findings before moving to the next pillar file.
