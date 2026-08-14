---
title: "D3 — CPU Throttling"
description: "Diagnose CPU throttling affecting ECS container performance"
status: active
severity: HIGH
triggers:
  - "cpu.*throttl"
  - "insufficient.*cpu"
owner: devops-agent
objective: "Identify CPU-throttled containers and optimize resource allocation"
context: "CPU throttling occurs when containers hit their CPU limit. Unlike memory, CPU throttling doesn't kill containers but degrades performance, causing health check failures and timeouts."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=all
- Use `search` tool with instanceId and query=`cpu.*throttl|insufficient.*cpu|cpu.*limit` to find CPU throttling evidence

SHOULD:
- Use `search` tool with query=`health.*check.*failed|timeout|slow` to check for symptoms of CPU throttling

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to check if CPU throttling correlates with health check failures

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- Recommend increasing CPU units in task definition or using larger instance type

## Common Issues

- symptoms: "cpu throttling detected, health checks failing intermittently"
  diagnosis: "Container CPU limit too low for workload"
  resolution: "Increase cpu units in task definition (e.g., 256 -> 512)"
