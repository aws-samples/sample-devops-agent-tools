---
title: "G1 — ECS Agent Disconnected"
description: "Diagnose ECS agent disconnection from the cluster"
status: active
severity: CRITICAL
triggers:
  - "agent.*connected.*false"
  - "AGENT_DISCONNECTED"
  - "websocket.*unable to dial"
  - "Error getting ECS instance credentials"
owner: devops-agent
objective: "Restore ECS agent connectivity to the cluster"
context: "When the ECS agent disconnects, the instance cannot receive new task placements and existing tasks may become orphaned. Common causes: network issues, IAM credential expiry, or agent crash."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical
- Use `search` tool with instanceId and query=`AGENT_DISCONNECTED|agent.*connected.*false|websocket.*unable|ECS Agent failed` to find agent disconnection evidence

SHOULD:
- Use `search` tool with query=`ecs-agent.*restart|ecs-init|agent.*start` to check agent restart history
- Use `network_diagnostics` tool with instanceId to check network connectivity

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to determine when agent disconnected and what else happened
- Determine if this is a network issue, IAM issue, or agent crash

SHOULD:
- Use `search` tool with query=`credential|token.*expir|sts.*assume` to check IAM credential issues

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: network connectivity, IAM credentials, or agent crash
- Recommend restarting ECS agent or replacing instance

## Common Issues

- symptoms: "AGENT_DISCONNECTED, websocket unable to dial"
  diagnosis: "Network connectivity to ECS service endpoint lost"
  resolution: "Check VPC endpoints, NAT gateway, and security group outbound rules for HTTPS (443)"

- symptoms: "Error getting ECS instance credentials"
  diagnosis: "Instance profile or IAM role issue"
  resolution: "Verify instance profile is attached and has AmazonEC2ContainerServiceforEC2Role policy"
