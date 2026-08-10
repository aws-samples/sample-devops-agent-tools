---
title: "K9 — Fargate Platform Version & Ephemeral Storage Issues"
description: "Diagnose and remediate Fargate platform version incompatibilities and ephemeral storage exhaustion"
status: active
severity: HIGH
triggers:
  - "platform version"
  - "ephemeral storage"
  - "disk space"
  - "no space left"
  - "platform 1.3"
  - "platform 1.4"
  - "LATEST"
  - "storage exceeded"
  - "EFS"
  - "volume mount"
owner: devops-agent
objective: "Resolve Fargate platform version mismatches and ephemeral storage exhaustion"
context: "Fargate tasks on platform version 1.4.0+ receive 20 GiB ephemeral storage (expandable to 200 GiB). Older platform versions (1.3.0 and earlier) only get 10 GiB for Docker layers plus 4 GiB for volume mounts. Features like EFS, Secrets Manager injection, task metadata v4, and containerd runtime require platform 1.4.0+. Using LATEST resolves to the newest version but explicit pinning can cause drift."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected task/instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to find storage or platform errors
- Use `search` tool with instanceId and query=`no space left|disk.*full|ephemeral.*storage|platform.*version|LATEST` to find evidence

SHOULD:
- Use `search` tool with query=`EFS|volume.*mount|bind.*mount|storage.*exceeded` to check volume-related failures
- Use `search` tool with query=`containerd|docker|Fargate.*agent|platform.*1\.[0-3]` to detect old platform version symptoms

MAY:
- Use `cluster_health` tool with clusterName to check if multiple tasks are affected
- Use `search` tool with query=`encryption|KMS|AES-256` to verify ephemeral storage encryption status

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of storage/platform events
- Determine whether the issue is ephemeral storage exhaustion or platform version incompatibility
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`ephemeralStorage|sizeInGiB|20.*GiB|200.*GiB` to check configured storage
- Use `search` tool with query=`task metadata|ECS_CONTAINER_METADATA_URI` to verify metadata endpoint availability

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the storage/platform findings
- State root cause: ephemeral storage limit hit or platform version too old for required features
- Recommend specific remediation

SHOULD:
- Include storage utilization data if available
- Recommend platform version upgrade path if on older version

## Guardrails

escalation_conditions:
  - "All Fargate tasks failing due to storage exhaustion"
  - "Platform version change required across multiple services"
  - "EFS mount failures blocking critical workloads"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Task definition ephemeralStorage update: YELLOW — operator action"
  - "Platform version change: YELLOW — requires deployment"

## Common Issues

- symptoms: "Task fails with 'no space left on device' on Fargate"
  diagnosis: "Default 20 GiB ephemeral storage exhausted by large container images or runtime data"
  resolution: "Increase ephemeralStorage in task definition (up to 200 GiB). Reduce container image size. Clean temp files in entrypoint."

- symptoms: "EFS volume mount fails on Fargate"
  diagnosis: "Task using platform version older than 1.4.0 which does not support EFS"
  resolution: "Update service to use platform version 1.4.0 or LATEST. Verify EFS security group allows NFS (port 2049) from task security group."

- symptoms: "Secrets Manager injection fails on Fargate"
  diagnosis: "Platform version 1.3.0 or earlier does not support Secrets Manager environment variable injection"
  resolution: "Upgrade to platform version 1.4.0 or LATEST. Ensure task execution role has secretsmanager:GetSecretValue permission."

- symptoms: "Task metadata endpoint v4 not available"
  diagnosis: "Platform version older than 1.4.0 only supports metadata endpoint v3"
  resolution: "Upgrade to platform version 1.4.0 or LATEST for ECS_CONTAINER_METADATA_URI_V4 support."
