---
title: "K8 — Task Stuck in PENDING State"
description: "Diagnose and remediate ECS tasks that remain in PENDING or PROVISIONING state"
status: active
severity: CRITICAL
triggers:
  - "PENDING"
  - "PROVISIONING"
  - "stuck"
  - "task not starting"
  - "waiting for capacity"
  - "timed out waiting"
owner: devops-agent
objective: "Identify why tasks are stuck in PENDING and restore task scheduling"
context: "Tasks stuck in PENDING/PROVISIONING indicate the scheduler cannot find suitable placement. Causes include no available instances, insufficient resources, ENI limits, subnet IP exhaustion, or capacity provider scaling delays."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId (any active instance) to gather cluster-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to find pending-related errors
- Use `search` tool with instanceId and query=`PENDING|PROVISIONING|stuck|waiting.*capacity|timed out|unable to place` to find evidence

SHOULD:
- Use `cluster_health` tool with clusterName to check cluster capacity and registered instances
- Use `search` tool with query=`ENI.*limit|network.*interface|subnet.*IP|InsufficientFreeAddresses` to check ENI/IP exhaustion

MAY:
- Use `search` tool with query=`capacity.*provider|managed.*scaling|Auto Scaling|launch.*template` to check scaling pipeline
- Use `compare_instances` tool to check resource availability across instances

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of pending events
- Determine the bottleneck: CPU, memory, ENI, IP addresses, or instance availability
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`desired.*capacity|running.*count|pending.*count` to check demand vs supply
- Use `search` tool with query=`awsvpc|bridge|host|network.*mode` to check if awsvpc mode is causing ENI limits

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the pending task findings
- State root cause: which resource is the bottleneck
- Recommend specific remediation to unblock task scheduling

SHOULD:
- Include capacity analysis showing available vs required resources
- Recommend ENI trunking if awsvpc mode is hitting ENI limits

## Guardrails

escalation_conditions:
  - "Tasks stuck in PENDING for more than 15 minutes"
  - "All new deployments blocked"
  - "Capacity provider unable to scale out"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Instance type or count changes: YELLOW — operator action"
  - "Subnet/VPC changes: RED — requires approval"

## Common Issues

- symptoms: "Tasks stuck in PROVISIONING — Fargate"
  diagnosis: "Fargate capacity not available in the selected AZ or subnet has no IPs"
  resolution: "Add subnets in multiple AZs, ensure subnets have available IP addresses"

- symptoms: "Tasks stuck in PENDING — EC2 launch type"
  diagnosis: "No container instances with sufficient CPU/memory"
  resolution: "Add instances, use larger instance types, or enable capacity provider managed scaling"

- symptoms: "Tasks stuck due to ENI limit on EC2 instance"
  diagnosis: "awsvpc network mode requires one ENI per task, instance ENI limit reached"
  resolution: "Enable ENI trunking (ECS_AWSVPC_TRUNKING=true), use instances with higher ENI limits, or switch to bridge network mode"

- symptoms: "Task timed out waiting for capacity"
  diagnosis: "Capacity provider scaling too slow or instances failing to register"
  resolution: "Reduce instanceWarmupPeriod, check instance user data for correct cluster name, verify ECS agent connectivity"
