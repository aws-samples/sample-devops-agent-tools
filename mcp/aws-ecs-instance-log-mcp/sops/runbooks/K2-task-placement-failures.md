---
title: "K2 — Task Placement Failures"
description: "Diagnose and remediate ECS tasks failing to place due to insufficient resources, missing attributes, or constraint violations"
status: active
severity: CRITICAL
triggers:
  - "no container instance met all of its requirements"
  - "unable to place a task"
  - "insufficient CPU"
  - "insufficient memory"
  - "AGENT"
  - "MemberOf placement constraint unsatisfied"
  - "SERVICE_TASK_PLACEMENT_FAILURE"
  - "PROVISIONING"
owner: devops-agent
objective: "Identify why tasks cannot be placed and restore task scheduling"
context: "Task placement failures occur when no container instance in the cluster meets the task's CPU, memory, port, attribute, or constraint requirements. Tasks remain in PROVISIONING/PENDING state."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId (any active instance) to gather cluster-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to find placement-related errors
- Use `search` tool with instanceId and query=`no container instance|unable to place|insufficient.*CPU|insufficient.*memory|placement.*constraint` to find placement failure evidence

SHOULD:
- Use `cluster_health` tool with clusterName to check overall cluster capacity and instance count
- Use `search` tool with query=`AGENT|agent.*disconnected|agent.*not.*connected` to check for disconnected agents

MAY:
- Use `compare_instances` tool to diff instances that can vs cannot accept tasks
- Use `search` tool with query=`port.*already.*use|required.*port` to check port conflicts

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of placement failures
- Determine the specific constraint that failed: CPU, memory, port, attribute, or placement constraint
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`attribute.*missing|ecs.capability|ecs.instance-type` to check missing attributes
- Use `search` tool with query=`desired.*count|running.*count|capacity.*provider` to check capacity vs demand

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the placement failure findings
- State root cause: which requirement could not be satisfied
- Recommend specific remediation based on failure type

SHOULD:
- Include cluster capacity analysis showing available vs required resources
- Recommend right-sizing task definitions or adding capacity

## Guardrails

escalation_conditions:
  - "All tasks in a service stuck in PENDING/PROVISIONING"
  - "Cluster has zero registered container instances"
  - "Capacity provider unable to scale out"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Task definition changes: YELLOW — operator action"
  - "Instance type or ASG changes: RED — requires approval"

## Common Issues

- symptoms: "no container instance met all of its requirements — insufficient CPU units"
  diagnosis: "Task requires more CPU than any instance has available"
  resolution: "Reduce task CPU, use larger instance types, or add more instances"

- symptoms: "no container instance met all of its requirements — insufficient memory"
  diagnosis: "Task requires more memory than any instance has available"
  resolution: "Reduce task memory, use larger instance types, or terminate unused tasks"

- symptoms: "closest matching container-instance encountered error AGENT"
  diagnosis: "ECS agent on the instance is disconnected"
  resolution: "SSH to instance and restart ecs agent: sudo systemctl restart ecs"

- symptoms: "MemberOf placement constraint unsatisfied"
  diagnosis: "No instances match the placement constraint expression"
  resolution: "Add custom attributes to instances or relax placement constraints"

- symptoms: "closest matching container instance already uses a required port"
  diagnosis: "Host port conflict — another task already bound to the required port"
  resolution: "Use dynamic port mapping with ALB, or add more container instances"
