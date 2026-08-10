---
title: "G2 — Instance Registration Failure"
description: "Diagnose ECS container instance registration failures"
status: active
severity: CRITICAL
triggers:
  - "failed.*register.*container.*instance"
  - "No container instances were found"
  - "ECS Agent failed to start"
  - "client version.*is too old"
owner: devops-agent
objective: "Restore instance registration with ECS cluster"
context: "Instance registration failures prevent the instance from joining the cluster. Causes include wrong cluster name in ECS config, outdated agent version, or IAM permission issues."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical
- Use `search` tool with instanceId and query=`failed.*register|No container instances|Agent failed to start|client version.*too old` to find registration errors

SHOULD:
- Use `search` tool with query=`ECS_CLUSTER|ecs.config|cluster.*name` to check cluster configuration
- Use `search` tool with query=`agent.*version|ecs-init.*version` to check agent version

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of registration attempts
- Determine if this is a config issue, version issue, or permission issue

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: wrong cluster name, outdated agent, or missing permissions
- Recommend specific fix

## Common Issues

- symptoms: "failed to register container instance: cluster not found"
  diagnosis: "ECS_CLUSTER in /etc/ecs/ecs.config points to wrong or non-existent cluster"
  resolution: "Fix ECS_CLUSTER value in /etc/ecs/ecs.config and restart ECS agent"

- symptoms: "client version is too old"
  diagnosis: "ECS agent version incompatible with cluster features"
  resolution: "Update ECS agent: sudo yum update -y ecs-init && sudo systemctl restart ecs"
