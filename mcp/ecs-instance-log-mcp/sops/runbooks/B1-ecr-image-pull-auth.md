---
title: "B1 — ECR Image Pull Authentication Failure"
description: "Diagnose and remediate ECR image pull failures due to authentication or authorization"
status: active
severity: CRITICAL
triggers:
  - "CannotPullECRContainerError"
  - "ecr:GetAuthorizationToken.*denied"
  - "ecr:BatchGetImage.*not authorized"
  - "pull.*access.*denied"
owner: devops-agent
objective: "Restore ECR image pull capability by fixing authentication/authorization"
context: "ECR image pulls require the task execution role to have ecr:GetAuthorizationToken, ecr:BatchGetImage, and ecr:GetDownloadUrlForLayer permissions. Cross-account pulls need additional ECR repository policy."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical to find image pull errors
- Use `search` tool with instanceId and query=`CannotPullECRContainerError|ecr.*denied|pull.*access.*denied|authorization.*token` to find auth evidence

SHOULD:
- Use `search` tool with query=`ecr:GetAuthorizationToken|ecr:BatchGetImage|ecr:GetDownloadUrlForLayer` to check which permission is missing
- Use `search` tool with query=`cross-account|registry.*id` to check for cross-account pull attempts

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline
- Determine if this is a permission issue (IAM) or a network issue (VPC endpoint)

SHOULD:
- Use `network_diagnostics` tool to check if VPC endpoints for ECR are configured
- Use `search` tool with query=`vpc.*endpoint|443.*timeout` to check ECR connectivity

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: missing IAM permissions, expired token, or network connectivity
- Recommend specific IAM policy additions or VPC endpoint configuration

## Common Issues

- symptoms: "ecr:GetAuthorizationToken denied"
  diagnosis: "Task execution role missing ECR auth permissions"
  resolution: "Attach AmazonEC2ContainerRegistryReadOnly policy to task execution role"

- symptoms: "ecr:BatchGetImage not authorized for cross-account"
  diagnosis: "ECR repository policy does not allow cross-account access"
  resolution: "Add cross-account principal to ECR repository policy"
