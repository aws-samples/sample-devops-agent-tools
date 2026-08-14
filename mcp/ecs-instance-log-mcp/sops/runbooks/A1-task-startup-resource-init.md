---
title: "A1 — Task Startup / ResourceInitializationError"
description: "Diagnose and remediate ECS tasks failing to start due to ResourceInitializationError"
status: active
severity: CRITICAL
triggers:
  - "ResourceInitializationError"
  - "TaskFailedToStart"
  - "CannotStartContainerError"
  - "CannotCreateContainerError"
owner: devops-agent
objective: "Identify the root cause of task startup failure and restore task placement"
context: "ResourceInitializationError occurs when ECS cannot set up the required resources (ENI, secrets, volumes) before starting the container. Common in awsvpc networking mode and Fargate tasks."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected container instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to get pre-indexed findings
- Use `search` tool with instanceId and query=`ResourceInitializationError|TaskFailedToStart|CannotStartContainerError` to find startup failure evidence

SHOULD:
- Use `search` tool with query=`unable to pull secrets|failed to retrieve|ENI.*timeout` to identify the specific resource that failed
- Use `network_diagnostics` tool with instanceId to check ENI and subnet IP availability

MAY:
- Use `cluster_health` tool with clusterName to check if multiple instances are affected
- Use `compare_instances` tool to diff healthy vs failing instances

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline around the startup failure
- Determine which resource failed: ENI provisioning, secrets retrieval, or volume mount
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`subnet|InsufficientFreeAddresses` if ENI-related
- Use `search` tool with query=`secretsmanager|ssm:GetParameters|AccessDenied` if secrets-related

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the startup failure findings
- State root cause: which resource initialization step failed and why
- Recommend immediate mitigation based on failure type

SHOULD:
- Include timeline from correlate showing the sequence of events
- Provide specific remediation steps (e.g., add IPs to subnet, fix IAM role)

## Guardrails

escalation_conditions:
  - "All tasks in a service failing to start"
  - "Subnet IP exhaustion affecting multiple services"
  - "IAM role changes needed require approval"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "IAM role changes: YELLOW — operator action"
  - "Subnet/VPC changes: RED — requires approval"

## Common Issues

- symptoms: "ResourceInitializationError: unable to pull secrets or registry auth"
  diagnosis: "Task execution role lacks permissions for Secrets Manager or SSM Parameter Store"
  resolution: "Add secretsmanager:GetSecretValue or ssm:GetParameters to task execution role"

- symptoms: "ResourceInitializationError: failed to configure ENI"
  diagnosis: "Subnet has no available IP addresses or ENI limit reached"
  resolution: "Add IPs to subnet or use a larger subnet CIDR"

- symptoms: "CannotStartContainerError: exec format error"
  diagnosis: "Image architecture mismatch (e.g., ARM image on x86 instance)"
  resolution: "Use correct image architecture or change instance type"
