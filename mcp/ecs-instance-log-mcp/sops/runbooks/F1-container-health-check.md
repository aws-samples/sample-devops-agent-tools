---
title: "F1 — Container Health Check Failures"
description: "Diagnose ECS container health check failures"
status: active
severity: HIGH
triggers:
  - "health.*check.*failed"
  - "UNHEALTHY"
  - "failed container health checks"
owner: devops-agent
objective: "Identify why container health checks are failing and restore healthy state"
context: "ECS container health checks (HEALTHCHECK in Dockerfile or healthCheck in task definition) mark containers as UNHEALTHY when the check command fails consecutively. This can trigger task replacement."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=all
- Use `search` tool with instanceId and query=`health.*check.*failed|UNHEALTHY|health.*status` to find health check failures

SHOULD:
- Use `search` tool with query=`HEALTHCHECK|healthCheck|curl.*localhost|wget` to find health check command configuration
- Use `search` tool with query=`connection.*refused|timeout|exit code` to find why the check command fails

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to check if health check failures correlate with resource exhaustion or network issues

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: application not ready, port mismatch, or resource exhaustion
- Recommend fixing health check command, increasing startPeriod, or fixing application

## Common Issues

- symptoms: "container health check failed immediately after start"
  diagnosis: "Health check startPeriod too short for application startup time"
  resolution: "Increase startPeriod in health check configuration"

- symptoms: "health check curl: connection refused"
  diagnosis: "Application not listening on the expected port"
  resolution: "Verify containerPort matches application listen port"
