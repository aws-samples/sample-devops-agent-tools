---
title: "A2 — Task Startup / Container Runtime Failure"
description: "Diagnose ECS tasks failing due to container runtime errors (Docker/containerd)"
status: active
severity: CRITICAL
triggers:
  - "ContainerRuntimeError"
  - "ContainerRuntimeTimeoutError"
  - "CannotCreateContainerError"
owner: devops-agent
objective: "Identify container runtime issue preventing task startup and restore service"
context: "Container runtime errors occur when Docker or containerd cannot create or start the container process. May indicate daemon issues, resource exhaustion, or configuration problems."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical to find runtime errors
- Use `search` tool with instanceId and query=`ContainerRuntimeError|docker.*daemon|containerd.*error|OCI runtime` to find runtime evidence

SHOULD:
- Use `search` tool with query=`no space left|disk full|inode` to check disk exhaustion
- Use `search` tool with query=`docker.*restart|containerd.*restart` to check daemon restarts

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline
- Use `validate` tool to confirm docker and containerd logs are present

SHOULD:
- Use `search` tool with query=`docker.*version|containerd.*version` to check versions

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: Docker/containerd daemon issue, disk exhaustion, or config error
- Recommend restart of container runtime or instance replacement

## Guardrails

escalation_conditions:
  - "Container runtime down on multiple instances"
  - "Disk exhaustion requiring volume resize"

safety_ratings:
  - "Log collection, search: GREEN (read-only)"
  - "Docker daemon restart: YELLOW — operator action"
  - "Instance replacement: RED — requires drain first"
