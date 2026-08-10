---
title: "K11 — Fargate Task Metadata & Credential Retrieval Errors"
description: "Diagnose and remediate task metadata endpoint failures and credential retrieval errors on Fargate"
status: active
severity: HIGH
triggers:
  - "metadata"
  - "credential"
  - "Missing credentials"
  - "could not load credentials"
  - "instance metadata"
  - "IMDS"
  - "ECS_CONTAINER_METADATA"
  - "timeout.*metadata"
  - "provider chain"
owner: devops-agent
objective: "Restore task metadata endpoint access and credential retrieval for Fargate tasks"
context: "Fargate tasks use the task metadata endpoint (v3/v4) for container metadata, Docker stats, and task-level information. AWS SDK credential retrieval uses the container credential provider (169.254.170.2) injected via AWS_CONTAINER_CREDENTIALS_RELATIVE_URI. Failures occur when the metadata endpoint is unreachable, credentials expire, the task execution role is misconfigured, or network issues block the link-local address range. Intermittent failures may indicate container startup race conditions or SDK version issues."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected task/instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to find credential/metadata errors
- Use `search` tool with instanceId and query=`Missing credentials|could not load credentials|metadata.*error|credential.*provider|169\.254\.170` to find evidence

SHOULD:
- Use `search` tool with query=`ECS_CONTAINER_METADATA_URI|AWS_CONTAINER_CREDENTIALS|timeout.*metadata|IMDS` to identify metadata endpoint issues
- Use `search` tool with query=`AssumeRole|sts:AssumeRole|expired.*token|security.*token` to check credential expiry

MAY:
- Use `search` tool with query=`SDK.*version|boto3|aws-sdk|retry.*credential` to check SDK-related issues
- Use `network_diagnostics` tool with instanceId to check network path to metadata endpoint

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of credential/metadata failures
- Determine failure type: metadata endpoint unreachable, credentials expired, or role misconfigured
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`task.*execution.*role|taskRoleArn|executionRoleArn` to verify role configuration
- Use `search` tool with query=`platform.*version|1\.4\.0|LATEST` to check platform version compatibility

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the credential/metadata findings
- State root cause: which credential/metadata mechanism failed and why
- Recommend specific remediation

SHOULD:
- Include timeline showing when credentials started failing
- Recommend SDK upgrade if version-related

## Guardrails

escalation_conditions:
  - "All tasks in a service unable to retrieve credentials"
  - "Credential failures causing cascading application errors"
  - "Metadata endpoint completely unreachable"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "IAM role changes: YELLOW — operator action"
  - "Network/VPC changes: RED — requires approval"

## Common Issues

- symptoms: "'Missing credentials in config, or could not load credentials from any provider'"
  diagnosis: "AWS SDK cannot find credentials. Task role not configured or container credential provider not available."
  resolution: "Ensure taskRoleArn is set in task definition. Verify AWS_CONTAINER_CREDENTIALS_RELATIVE_URI environment variable is present. Upgrade AWS SDK to latest version."

- symptoms: "Intermittent metadata errors on Fargate"
  diagnosis: "Race condition during container startup — metadata endpoint not ready when application starts"
  resolution: "Add retry logic with exponential backoff for metadata/credential calls at application startup. Use SDK built-in retry mechanisms."

- symptoms: "Timeout errors from instance metadata service on Fargate"
  diagnosis: "Network path to 169.254.170.2 blocked or task metadata endpoint overloaded"
  resolution: "Verify task is on platform version 1.4.0+. Check that no custom iptables rules block link-local addresses. Reduce metadata polling frequency."

- symptoms: "'Unable to retrieve instance metadata' in application logs"
  diagnosis: "Application using EC2 IMDS (169.254.169.254) instead of ECS container credential provider"
  resolution: "Configure application to use ECS container credential provider (AWS_CONTAINER_CREDENTIALS_RELATIVE_URI) instead of EC2 IMDS. Update SDK configuration."
