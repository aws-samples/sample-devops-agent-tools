---
title: "K5 — ECS Exec Failures"
description: "Diagnose and remediate failures when using ECS Exec to run commands in containers"
status: active
severity: MEDIUM
triggers:
  - "execute command failed"
  - "TargetNotConnectedException"
  - "ExecuteCommandAgent"
  - "SSM agent"
  - "session manager"
  - "ecs exec"
owner: devops-agent
objective: "Identify why ECS Exec cannot connect to a container and restore interactive access"
context: "ECS Exec uses AWS Systems Manager (SSM) Session Manager to establish connections to containers. Failures occur due to missing IAM permissions, SSM agent issues, VPC endpoint gaps, or the feature not being enabled on the service."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected container instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=medium to find exec-related errors
- Use `search` tool with instanceId and query=`execute command|ExecuteCommandAgent|TargetNotConnected|SSM.*agent|session.*manager` to find exec failure evidence

SHOULD:
- Use `search` tool with query=`enableExecuteCommand|executeCommandConfiguration|task.*role|ssmmessages` to check ECS Exec configuration
- Use `network_diagnostics` tool with instanceId to check VPC endpoint connectivity

MAY:
- Use `search` tool with query=`vpc.*endpoint|com.amazonaws.*ssmmessages|com.amazonaws.*ssm` to check SSM VPC endpoints
- Use `cluster_health` tool with clusterName to check if exec works on other tasks

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline around exec failures
- Determine the specific failure: IAM permissions, SSM agent, VPC endpoints, or feature not enabled
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`task.*IAM.*role|iam:PassRole|ssm:StartSession` to check IAM role configuration
- Use `search` tool with query=`managed.*agent|RUNNING|STOPPED` to check ExecuteCommandAgent status

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the exec failure findings
- State root cause: which component is preventing ECS Exec
- Recommend specific remediation steps

SHOULD:
- Recommend running the ECS Exec Checker script for comprehensive validation
- Include IAM policy requirements for task role

## Guardrails

escalation_conditions:
  - "ECS Exec needed for production incident debugging but unavailable"
  - "SSM agent not running on any container instances"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "IAM role policy changes: YELLOW — operator action"
  - "VPC endpoint creation: RED — requires approval"

## Common Issues

- symptoms: "The execute command failed because execute command was not enabled"
  diagnosis: "ECS Exec not enabled on the service or task"
  resolution: "Update service with --enable-execute-command flag, then force new deployment"

- symptoms: "TargetNotConnectedException"
  diagnosis: "SSM agent in the container cannot reach SSM endpoints"
  resolution: "Create VPC endpoints for ssmmessages, ssm, and ec2messages, or ensure NAT gateway for internet access"

- symptoms: "The execute command failed — missing permissions"
  diagnosis: "Task IAM role lacks SSM permissions"
  resolution: "Add ssmmessages:CreateControlChannel, ssmmessages:CreateDataChannel, ssmmessages:OpenControlChannel, ssmmessages:OpenDataChannel to task role"

- symptoms: "ExecuteCommandAgent status is STOPPED"
  diagnosis: "SSM agent crashed or container restarted"
  resolution: "Force new deployment to restart tasks with fresh SSM agent, check container has enough memory for SSM agent overhead"
