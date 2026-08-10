---
title: "C1 — Task Execution Role Permission Issues"
description: "Diagnose IAM permission failures for ECS task execution role"
status: active
severity: CRITICAL
triggers:
  - "AccessDeniedException"
  - "is not authorized to perform"
  - "No valid providers in chain"
  - "AssumeRoleUnauthorizedAccess"
  - "execution role.*does not have"
owner: devops-agent
objective: "Identify missing IAM permissions and restore task execution"
context: "The task execution role is used by the ECS agent to pull images, retrieve secrets, and send logs. Missing permissions cause ResourceInitializationError or AccessDenied errors."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical
- Use `search` tool with instanceId and query=`AccessDenied|not authorized|No valid providers|execution role` to find IAM errors

SHOULD:
- Use `search` tool with query=`ecr:GetAuthorizationToken|secretsmanager:GetSecretValue|ssm:GetParameters|logs:CreateLogStream` to identify which specific API call is denied

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to determine if IAM errors are the root cause or a symptom
- Identify the exact IAM action and resource that was denied

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: specific missing IAM permission
- Recommend exact IAM policy statement to add

## Common Issues

- symptoms: "No valid providers in chain"
  diagnosis: "Task execution role ARN is invalid or role does not exist"
  resolution: "Verify task execution role ARN in task definition and ensure role exists"

- symptoms: "is not authorized to perform ecr:GetAuthorizationToken"
  diagnosis: "Task execution role missing ECR permissions"
  resolution: "Attach AmazonEC2ContainerRegistryReadOnly managed policy"

- symptoms: "AccessDenied for secretsmanager:GetSecretValue"
  diagnosis: "Task execution role missing Secrets Manager permissions"
  resolution: "Add secretsmanager:GetSecretValue permission for the specific secret ARN"
