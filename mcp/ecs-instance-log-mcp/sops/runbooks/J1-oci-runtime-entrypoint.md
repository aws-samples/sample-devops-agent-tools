---
title: "J1 — OCI Runtime / Entrypoint Failures"
description: "Diagnose container runtime failures related to OCI, entrypoint, or architecture mismatch"
status: active
severity: CRITICAL
triggers:
  - "OCI runtime create failed"
  - "exec format error"
  - "no such file or directory.*entrypoint"
  - "permission denied.*entrypoint"
  - "container_linux.go.*starting container process"
owner: devops-agent
objective: "Fix container startup failure at the runtime level"
context: "OCI runtime errors occur at the lowest level of container creation. Common causes: wrong CPU architecture (ARM vs x86), missing entrypoint binary, or permission issues on the entrypoint script."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical
- Use `search` tool with instanceId and query=`OCI runtime|exec format error|entrypoint.*not found|permission denied.*entrypoint|container_linux.go` to find runtime errors

SHOULD:
- Use `search` tool with query=`architecture|platform|amd64|arm64` to check for architecture mismatch

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to check if this affects all tasks or specific images
- Determine if this is an architecture mismatch, missing binary, or permission issue

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: architecture mismatch, missing entrypoint, or permission denied
- Recommend building correct image architecture or fixing Dockerfile

## Common Issues

- symptoms: "exec format error"
  diagnosis: "Image built for different CPU architecture (e.g., ARM image on x86 instance)"
  resolution: "Build multi-arch image or use correct platform: docker build --platform linux/amd64"

- symptoms: "no such file or directory: /app/entrypoint.sh"
  diagnosis: "Entrypoint binary missing from image"
  resolution: "Verify ENTRYPOINT/CMD in Dockerfile, ensure binary is included in image"

- symptoms: "permission denied: /app/entrypoint.sh"
  diagnosis: "Entrypoint script not executable"
  resolution: "Add RUN chmod +x /app/entrypoint.sh in Dockerfile"
