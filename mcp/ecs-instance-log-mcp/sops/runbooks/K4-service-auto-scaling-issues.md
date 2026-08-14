---
title: "K4 — Service Auto Scaling / Capacity Provider Issues"
description: "Diagnose and remediate ECS service auto scaling failures and capacity provider scaling problems"
status: active
severity: HIGH
triggers:
  - "CapacityProviderReservation"
  - "scaling policy"
  - "desired count"
  - "instance count discrepancy"
  - "Limit exceeded"
  - "InsufficientCapacity"
  - "VcpuLimitExceeded"
  - "managed scaling"
owner: devops-agent
objective: "Identify auto scaling or capacity provider issues and restore proper scaling behavior"
context: "ECS uses Application Auto Scaling for service task count and cluster auto scaling (capacity providers) for EC2 instance count. Failures can occur at either level — tasks not scaling, or instances not launching to support task demand."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from a container instance in the cluster
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to find scaling-related errors
- Use `search` tool with instanceId and query=`scaling|capacity.*provider|desired.*count|Limit exceeded|InsufficientCapacity|VcpuLimitExceeded` to find scaling failure evidence

SHOULD:
- Use `cluster_health` tool with clusterName to check cluster capacity metrics
- Use `search` tool with query=`CapacityProviderReservation|targetCapacity|minimumScalingStepSize|maximumScalingStepSize` to check capacity provider configuration

MAY:
- Use `search` tool with query=`CloudWatch.*alarm|target.*tracking|scaling.*policy` to check scaling policy triggers
- Use `compare_instances` tool to check instance distribution across AZs

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of scaling events
- Determine if the issue is at service level (task count) or cluster level (instance count)
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`Auto Scaling group|ASG|launch.*template|instance.*type` to check ASG configuration
- Use `search` tool with query=`service quota|rate limit|throttl` to check for API throttling

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the scaling failure findings
- State root cause: service scaling policy, capacity provider, ASG limits, or service quotas
- Recommend specific remediation

SHOULD:
- Include scaling timeline showing demand vs capacity
- Recommend capacity provider configuration adjustments

## Guardrails

escalation_conditions:
  - "Tasks stuck in PENDING due to no available capacity"
  - "Service quota limits reached"
  - "Capacity provider unable to launch instances in any AZ"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Scaling policy adjustments: YELLOW — operator action"
  - "Service quota increase requests: YELLOW — operator action"
  - "ASG/capacity provider changes: RED — requires approval"

## Common Issues

- symptoms: "Tasks stuck in PENDING, no new instances launching"
  diagnosis: "Capacity provider managed scaling not enabled or ASG at MaxSize"
  resolution: "Enable managed scaling on capacity provider, increase ASG MaxSize, or add instances manually"

- symptoms: "Instance count discrepancy between ASG and ECS cluster"
  diagnosis: "Instances launched but not registering with ECS cluster"
  resolution: "Check ECS agent connectivity, verify instance user data sets correct cluster name, check security groups allow ECS agent traffic"

- symptoms: "VcpuLimitExceeded error"
  diagnosis: "EC2 vCPU service quota reached for the instance type family"
  resolution: "Request service quota increase, use different instance types, or terminate unused instances"

- symptoms: "Service not scaling despite high CPU/memory"
  diagnosis: "Auto scaling policy not configured or CloudWatch alarm not triggering"
  resolution: "Verify target tracking policy exists, check CloudWatch metrics are being published, ensure Container Insights is enabled"
