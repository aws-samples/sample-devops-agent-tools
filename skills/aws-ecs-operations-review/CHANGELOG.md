# Changelog

## 2.4.0

Capacity provider depth + compute platform awareness (69 baseline checks, up
from 64):

- **Compute platform decision step** (new workflow step 4, rules in
  `references/checks.md`): the agent now classifies the service as Fargate
  (± Spot), EC2 ASG capacity provider, **ECS Managed Instances**
  (`managedInstancesProvider`), launchType-only, or ECS Anywhere — including
  mixed strategies — and this decision drives "Applies To" applicability in
  every pillar. New applicability values: `EC2-ASG-CP`, `MI`, `CP-strategy`.
- **PERF7 deepened** (Low → Medium): flags launchType-only services (ignored
  by managed scaling) and missing cluster `defaultCapacityProviderStrategy`.
- **PERF9 (new)**: managed scaling enabled with `targetCapacity` headroom
  (80-100, <100 for spiky workloads) and `instanceWarmupPeriod` sanity.
- **PERF10 (new)**: metrics-driven capacity analysis — 7-day
  `CapacityProviderReservation` (AWS/ECS/ManagedScaling) baseline compared
  against configured `targetCapacity` to detect capacity-constrained
  scale-outs vs idle over-provisioning.
- **PERF11 (new)**: capacity provider strategy base/weight design — on-demand
  base for production, Spot burst by weight, task-size-fits-instance check.
- **REL14 (new)**: capacity provider infrastructure multi-AZ (ASG subnets or
  Managed Instances `networkConfiguration.subnets` span 2+ AZs).
- **ADD7 (new)**: ECS Managed Instances migration evaluation for self-managed
  EC2 services, keyed off OPS6/OPS7 (agent/AMI currency) signals.
- **OPS6/OPS7/OPS2** now explicitly N/A for Managed Instances (AWS manages
  agent/AMI lifecycle).
- **alarm-thresholds.md**: new Capacity Provider Alarms section
  (`CapacityProviderReservation` saturation alarm) and baseline-metrics row;
  report header now records the resolved compute platform.

## 2.3.2

Fix skill upload rejection (`400 ValidationException` from the AWS DevOps Agent
Asset API):

- Reduced `SKILL.md` frontmatter to **only `name` and `description`**, the
  fields the DevOps Agent uploader supports for zip skills. Removed the
  `license`, `compatibility`, and nested `metadata` blocks added in 2.3.1 — the
  DevOps Agent parser reads only `name`/`description` from frontmatter and
  rejects the extra keys. `agent_types` and other asset metadata are supplied
  in the Asset API request (or the Operator Web App) at upload time, not in
  frontmatter. Description (with its trigger phrases) is unchanged and within
  the 1024-char limit.

## 2.3.1

Compliance with the AgentSkills.io open standard (aligns this skill with the
`aws-eks-operations-review` skill):

- Renamed directory to `aws-ecs-operations-review` (registry
  `aws-<service>-<capability>` naming convention).
- Rewrote SKILL.md frontmatter to the spec: only `name`, `description`,
  `license`, `compatibility`, and `metadata` at the top level. Moved `version`
  and `tags` inside `metadata:`; added `license`, `compatibility`, and the
  `aws-devops-agent-skills.*` + `devops-agent-tools.*` registry metadata.
  Front-loaded the `description` with trigger phrases for discovery.
- Fixed the `name` field to match the directory (`aws-ecs-operations-review`).
- Renamed `reference/` → `references/` (spec convention) and updated all
  SKILL.md links.
- Added `README.md` (packaging / prerequisites / upload / usage) and an
  `evals/` harness (routing + knowledge evals) mirroring the EKS skill. No
  change to the assessment workflow, pillars, checks, or report format.

## 2.3.0

- Baseline: comprehensive ECS operations review across the 6 review pillars
  (Resiliency & HA, Observability, Security, Operations, Performance,
  Additional Analysis) with a 7-day CloudWatch metrics baseline, recommended
  alarm thresholds for IDR onboarding, per-pillar ✓/✗/N/A scorecards, a
  coverage gate, and the `review-common` baseline crosswalk. Read-only AWS API
  data collection; Markdown report by default, DOCX on request.
