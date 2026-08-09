# Common-check coverage (review-common baseline)

The shared **`review-common`** skill defines a small set of checks that apply to **every** AWS
service (tagging, encryption, IAM least-privilege, alarms, logging, cost). An ECS operations review
must cover that baseline too. This file is the **crosswalk**: it maps each `review-common` common
check to the ECS check(s) that satisfy it, so the coverage gate can confirm the baseline is met
without adding a parallel `C*` check set.

The ECS review keeps its own check IDs (REL*, OBS*, SEC*, OPS*, PERF*, ADD*); it does **not**
renumber to the common `C*` IDs. This table shows the mapping.

## Crosswalk

| Common check (review-common) | Baseline severity | Covered by (ECS) | Notes |
|------------------------------|-------------------|------------------|-------|
| **COp1** — Resource tagging (`Environment`, `Owner`, `CostCenter`) | Low | **OPS1** (required tags: Name, Environment, Owner, Application, CostCenter) | Direct match; OPS1 already requires the three common tags. |
| **COp2** — IaC / CloudFormation managed | Low | **OPS8** (Infrastructure-as-Code managed) | Added for the baseline — detects CloudFormation/CDK (`aws:cloudformation:*` tags) or a Terraform/Pulumi management tag. |
| **CS1** — Encryption at rest (KMS) | High | **SEC17** (encryption at rest for task storage — EBS KMS; Fargate ephemeral encrypted by default 1.4.0+) | Direct match. |
| **CS2** — Encryption in transit (TLS) | High | **SEC20** (encryption in transit — LB HTTPS/TLS listener + Service Connect TLS) | Added for the baseline. N/A only for an internal task with no LB and no Service Connect. |
| **CS3** — IAM least privilege (no wildcards) | High | **SEC1** (execution role scoped) + **SEC2** (task role least privilege) | Both roles graded for wildcards / over-broad managed policies. |
| **CO1** — CloudWatch alarms exist | Critical | **OBS3** (CPU alarm) + **OBS4** (memory alarm) + **OBS5** (running-task-count alarm) | Base ECS alarms; the full recommended set is in `alarm-thresholds.md`. |
| **CO2** — Logging enabled | High | **OBS2** (awslogs/awsfirelens configured) + **OBS8** (log retention bounded) | Log pipeline + retention together satisfy logging-enabled. |
| **CA1** — Cost optimization review (not over-provisioned / idle) | Low | **PERF8** (Compute Optimizer rightsizing) + **ADD1** (Graviton) + **ADD2** (Fargate Spot) | Rightsizing + capacity-type optimization satisfy the cost baseline. |

## How to use during a review

- For a full ECS operations review / CWR, the eight common checks above are **already graded**
  through their ECS equivalents — no separate pass is needed. Cite the ECS check ID as evidence.
- **Two checks were added for the baseline**: **OPS8** (IaC-managed → COp2) and **SEC20**
  (encryption in transit → CS2). Prior checks covered the other six.
- SEC20 needs `elbv2.describeListeners` (LB TLS) plus `serviceConnectConfiguration` from
  `ecs.describeServices`; mark ⚪ N/A with the access-limitation note if that API is denied, or if
  the service has no load balancer and no Service Connect.

## Coverage-gate addition

An ECS operations review / CWR is not complete unless all eight `review-common` baseline checks are
accounted for — either graded via their ECS equivalent above, or ⚪ N/A with a reason. Confirm this
crosswalk is satisfied alongside the per-pillar coverage gate in [`report-format.md`](report-format.md).
