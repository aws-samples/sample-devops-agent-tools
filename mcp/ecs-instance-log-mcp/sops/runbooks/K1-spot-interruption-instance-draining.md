---
title: "K1 — Spot Interruption / Instance Draining"
description: "Diagnose and remediate ECS task disruptions caused by Spot Instance interruptions or container instance draining"
status: active
severity: HIGH
triggers:
  - "SpotInterruption"
  - "DRAINING"
  - "instance is being terminated"
  - "Spot Instance interruption notice"
  - "managed instance draining"
  - "ECS_ENABLE_SPOT_INSTANCE_DRAINING"
owner: devops-agent
objective: "Identify Spot interruption or draining events and ensure tasks are gracefully rescheduled"
context: "When EC2 Spot capacity is reclaimed or an instance enters DRAINING state, ECS stops scheduling new tasks on it and attempts to replace running service tasks. Standalone tasks are NOT automatically replaced. Two-minute warning for Spot terminations."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected container instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to find Spot/draining related errors
- Use `search` tool with instanceId and query=`SpotInterruption|DRAINING|instance.*terminat|spot.*interrupt` to find interruption evidence

SHOULD:
- Use `search` tool with query=`ECS_ENABLE_SPOT_INSTANCE_DRAINING|managed.*draining|lifecycle.*hook` to check draining configuration
- Use `cluster_health` tool with clusterName to check if multiple instances are affected

MAY:
- Use `compare_instances` tool to diff healthy vs draining instances
- Use `search` tool with query=`standalone.*task|PENDING.*stop` to find standalone tasks that won't auto-replace

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of draining/interruption events
- Determine if tasks were successfully rescheduled to other instances
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`replacement.*task|new.*placement|capacity.*provider` to verify replacement task launches
- Check if managed termination protection is enabled for the capacity provider

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the interruption findings
- State root cause: Spot reclamation, manual draining, or ASG lifecycle event
- Recommend enabling ECS_ENABLE_SPOT_INSTANCE_DRAINING if not configured

SHOULD:
- Include timeline from correlate showing draining sequence
- Recommend using multiple capacity providers with Spot and On-Demand mix
- Recommend enabling managed instance draining on capacity providers

## Guardrails

escalation_conditions:
  - "Multiple instances draining simultaneously causing capacity shortage"
  - "Standalone tasks lost without replacement"
  - "Service unable to place replacement tasks"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Capacity provider configuration changes: YELLOW — operator action"
  - "ASG/instance type changes: RED — requires approval"

## Common Issues

- symptoms: "SpotInterruption: Spot capacity is no longer available"
  diagnosis: "EC2 reclaimed Spot capacity in the Availability Zone"
  resolution: "Use multiple AZs, diversify instance types, enable Spot Instance draining, consider On-Demand fallback"

- symptoms: "Container instance set to DRAINING but tasks not replaced"
  diagnosis: "No available capacity on other instances or standalone tasks not auto-replaced"
  resolution: "Ensure sufficient capacity in other AZs, manually restart standalone tasks, enable managed instance draining"

- symptoms: "Tasks stuck in STOPPING state during draining"
  diagnosis: "stopTimeout too long or application not handling SIGTERM gracefully"
  resolution: "Reduce stopTimeout in task definition, implement graceful shutdown handler in application"
