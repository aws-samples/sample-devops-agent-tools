---
title: "H1 — CloudWatch Log Driver Issues"
description: "Diagnose CloudWatch logging failures for ECS containers"
status: active
severity: HIGH
triggers:
  - "log.*driver.*error"
  - "failed.*send.*logs"
  - "awslogs.*error"
  - "failed to initialize logging driver"
  - "logs:CreateLogStream.*denied"
owner: devops-agent
objective: "Restore CloudWatch log delivery for ECS containers"
context: "The awslogs log driver sends container stdout/stderr to CloudWatch Logs. Failures can be caused by missing IAM permissions, log group not existing, or network connectivity to CloudWatch endpoint."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=all
- Use `search` tool with instanceId and query=`log.*driver.*error|awslogs.*error|CreateLogStream.*denied|initialize logging driver` to find logging errors

SHOULD:
- Use `search` tool with query=`logs:CreateLogGroup|logs:CreateLogStream|logs:PutLogEvents` to check which permission is missing

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to check if logging failures cause task failures
- Determine if this is a permission issue, log group issue, or network issue

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: missing IAM permissions, log group not created, or VPC endpoint missing
- Recommend specific fix

## Common Issues

- symptoms: "failed to initialize logging driver: AccessDeniedException"
  diagnosis: "Task execution role missing CloudWatch Logs permissions"
  resolution: "Add logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents to task execution role"

- symptoms: "awslogs error: ResourceNotFoundException"
  diagnosis: "Log group does not exist and auto-create is not enabled"
  resolution: "Create log group or set awslogs-create-group=true in log configuration"
