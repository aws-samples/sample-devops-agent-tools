---
title: "C2 — Secrets Manager / SSM Parameter Retrieval Failure"
description: "Diagnose failures retrieving secrets or parameters during task startup"
status: active
severity: CRITICAL
triggers:
  - "unable to pull secrets"
  - "unable to retrieve secret from asm"
  - "SecretNotFound"
  - "ParameterNotFound"
  - "secretsmanager:GetSecretValue.*denied"
  - "ssm:GetParameters.*denied"
owner: devops-agent
objective: "Restore secret/parameter retrieval for ECS task startup"
context: "ECS tasks can reference Secrets Manager secrets and SSM parameters in container definitions. Failures occur due to missing permissions, deleted secrets, or VPC endpoint issues."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical
- Use `search` tool with instanceId and query=`unable to pull secrets|SecretNotFound|ParameterNotFound|retrieve secret|retrieve ecr registry auth` to find secret retrieval errors

SHOULD:
- Use `search` tool with query=`secretsmanager|ssm:GetParameters|AccessDenied` to identify permission vs not-found issues

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to check if secrets were recently deleted or rotated
- Determine if this is a permission issue or a resource-not-found issue

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: secret deleted, permission denied, or VPC endpoint missing
- Recommend specific fix

## Common Issues

- symptoms: "SecretNotFound"
  diagnosis: "Secret ARN in task definition references a deleted or non-existent secret"
  resolution: "Verify secret exists in Secrets Manager and ARN matches task definition"

- symptoms: "unable to pull secrets or registry auth: execution resource retrieval failed"
  diagnosis: "VPC endpoint for Secrets Manager not configured in private subnet"
  resolution: "Create VPC endpoint for secretsmanager or ensure NAT gateway is configured"
