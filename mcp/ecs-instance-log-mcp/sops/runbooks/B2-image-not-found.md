---
title: "B2 — Image Not Found / Manifest Not Found"
description: "Diagnose image pull failures due to missing image or tag"
status: active
severity: HIGH
triggers:
  - "manifest.*not.*found"
  - "repository.*does.*not.*exist"
  - "image.*not.*found"
  - "failed to resolve ref.*not found"
owner: devops-agent
objective: "Identify why the container image cannot be found and fix the reference"
context: "Image not found errors occur when the image URI, tag, or digest in the task definition does not match any image in the registry."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical
- Use `search` tool with instanceId and query=`manifest.*not found|repository.*not exist|image.*not found|resolve ref` to find the exact image URI that failed

SHOULD:
- Use `search` tool with query=`image=|imageUri|container.*image` to extract the full image URI from task definition logs

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to check if this is a new issue or recurring
- Confirm the exact image URI and tag that failed

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: image tag deleted, typo in URI, or repository does not exist
- Recommend verifying image exists in registry

## Common Issues

- symptoms: "manifest for <image>:latest not found"
  diagnosis: "Image tag was overwritten or deleted from registry"
  resolution: "Use immutable tags or image digests instead of :latest"

- symptoms: "repository does not exist"
  diagnosis: "ECR repository name is wrong or repository was deleted"
  resolution: "Verify ECR repository name matches task definition"
