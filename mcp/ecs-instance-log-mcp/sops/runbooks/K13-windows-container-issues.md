---
title: "K13 — Windows Container Issues"
description: "Diagnose and remediate Windows-specific ECS container failures including OS mismatch, IAM role bootstrap, and awslogs driver"
status: active
severity: HIGH
triggers:
  - "Windows"
  - "windows"
  - "OS mismatch"
  - "operating system does not match"
  - "EnableTaskIAMRole"
  - "ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE"
  - "No valid providers in chain"
  - "Unable to assume the role"
  - "Windows Server"
owner: devops-agent
objective: "Resolve Windows-specific ECS task failures and configuration issues"
context: "Windows containers on ECS have unique requirements: the container base image OS version must match the host OS version, IAM roles for tasks require explicit bootstrap configuration (-EnableTaskIAMRole), the awslogs driver needs ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE=true, and several Linux task definition parameters are unsupported (linuxParameters, privileged, readonlyRootFilesystem, user, ulimits). Windows and Linux tasks must run in separate clusters. Windows Server 2016 is deprecated and cannot run the latest Docker version."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected Windows instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to find Windows-specific errors
- Use `search` tool with instanceId and query=`operating system does not match|OS mismatch|Windows.*error|EnableTaskIAMRole|AWSLOGS_EXECUTIONROLE` to find evidence

SHOULD:
- Use `search` tool with query=`No valid providers in chain|Unable to assume.*role|credential.*provider` to check IAM role issues
- Use `search` tool with query=`Windows Server 2016|Windows Server 2019|Windows Server 2022|Windows Server 2025` to identify OS version

MAY:
- Use `search` tool with query=`user data|bootstrap|Initialize-ECSAgent|Set-Variable` to check instance bootstrap configuration
- Use `cluster_health` tool with clusterName to check cluster-wide Windows instance health

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of Windows-specific failures
- Determine failure category: OS mismatch, IAM bootstrap, awslogs driver, or unsupported parameter
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`container.*image|base.*image|nanoserver|servercore|ltsc` to check image OS version
- Use `search` tool with query=`awslogs|log.*driver|logConfiguration|CreateLogStream` to check logging configuration

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the Windows-specific findings
- State root cause: which Windows-specific configuration is incorrect
- Recommend specific remediation

SHOULD:
- Include OS version compatibility matrix
- Recommend migration path if on deprecated Windows Server version

## Guardrails

escalation_conditions:
  - "All Windows tasks failing across the cluster"
  - "OS version mismatch requiring AMI update"
  - "Windows Server 2016 deprecation blocking updates"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "User data / bootstrap changes: YELLOW — requires instance replacement"
  - "AMI update: RED — requires approval and rolling replacement"

## Common Issues

- symptoms: "'The container operating system does not match the host operating system'"
  diagnosis: "Container base image OS version does not match the EC2 host or Fargate platform OS version"
  resolution: "Ensure container image base (e.g., ltsc2022) matches host OS. Use matching ECS-optimized Windows AMI. For Fargate, use compatible Windows platform version."

- symptoms: "'No valid providers in chain' error on Windows tasks"
  diagnosis: "ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE not set on Windows container instance"
  resolution: "Add 'ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE=true' to instance user data bootstrap script."

- symptoms: "'Unable to assume the role' on Windows EC2 tasks"
  diagnosis: "IAM roles for tasks not enabled — missing -EnableTaskIAMRole in bootstrap"
  resolution: "Add '-EnableTaskIAMRole' flag to Initialize-ECSAgent in instance user data. Ensure Windows instance meets IAM role configuration requirements."

- symptoms: "Task definition validation fails with unsupported parameters"
  diagnosis: "Linux-only parameters used in Windows task definition (linuxParameters, privileged, readonlyRootFilesystem, user, ulimits)"
  resolution: "Remove unsupported parameters from task definition. Specify container-level CPU and memory instead of task-level for Windows EC2 tasks."

- symptoms: "Windows container logs not appearing in CloudWatch"
  diagnosis: "awslogs driver requires ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE on Windows instances"
  resolution: "Set ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE=true in instance user data. Verify task execution role has logs:CreateLogStream and logs:PutLogEvents permissions."
