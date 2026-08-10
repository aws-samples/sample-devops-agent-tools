---
title: "D1 — OOM Kill / Memory Exhaustion"
description: "Diagnose container or instance OOM kills in ECS"
status: active
severity: CRITICAL
triggers:
  - "OutOfMemoryError"
  - "oom.*kill"
  - "Memory cgroup out of memory"
  - "invoked oom-killer"
  - "exit code 137"
owner: devops-agent
objective: "Identify OOM-killed process, determine memory pressure source, and prevent recurrence"
context: "OOM kills occur when a container exceeds its memory limit (hard limit) or the instance runs out of memory. Exit code 137 indicates SIGKILL from OOM killer."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical to find OOM findings
- Use `search` tool with instanceId and query=`oom-killer|OOMKilled|out of memory|Memory cgroup|exit code 137` to find OOM evidence in dmesg

SHOULD:
- Use `search` tool with query=`Killed process.*total-vm|memory.*limit|memory.*usage` to find which process was killed and memory stats
- Use `search` tool with query=`insufficient.*memory|memory.*pressure` to check for instance-level memory pressure

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`oom-killer` to build timeline
- Determine if OOM is at container level (cgroup limit) or instance level (system OOM)

SHOULD:
- Use `search` tool with query=`memory.*hard.*limit|memoryReservation|memory=` to check task definition memory settings

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: container memory limit too low or memory leak
- Recommend increasing memory limit or investigating memory leak

## Common Issues

- symptoms: "exit code 137, Memory cgroup out of memory"
  diagnosis: "Container exceeded its hard memory limit"
  resolution: "Increase container memory limit in task definition, or investigate memory leak"

- symptoms: "invoked oom-killer on instance level, multiple containers affected"
  diagnosis: "Instance total memory exhausted by sum of all containers"
  resolution: "Use larger instance type or reduce number of tasks per instance"
