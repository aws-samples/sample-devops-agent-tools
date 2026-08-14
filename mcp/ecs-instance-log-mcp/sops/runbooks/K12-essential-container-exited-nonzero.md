---
title: "K12 — Essential Container Exited with Non-Zero Exit Code"
description: "Diagnose and remediate ECS tasks stopped due to essential container exit with non-zero codes"
status: active
severity: CRITICAL
triggers:
  - "EssentialContainerExited"
  - "exit code"
  - "non-zero"
  - "exit 1"
  - "exit 137"
  - "exit 139"
  - "exit 143"
  - "exit 255"
  - "stopped"
  - "essential container"
  - "SIGKILL"
  - "SIGTERM"
  - "segfault"
owner: devops-agent
objective: "Identify why essential containers are exiting and restore task stability"
context: "When an essential container in an ECS task exits, the entire task is stopped. The exit code indicates the failure type: 0=normal, 1=application error, 137=OOM kill or SIGKILL (128+9), 139=segfault SIGSEGV (128+11), 143=SIGTERM (128+15), 255=container runtime error. The DescribeTasks API provides stoppedReason and container exit codes. Common causes include application crashes, OOM kills, signal handling issues, and entrypoint/command errors."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to find exit-related errors
- Use `search` tool with instanceId and query=`EssentialContainerExited|exit code|non-zero|stopped.*reason|SIGKILL|SIGTERM|segfault` to find evidence

SHOULD:
- Use `search` tool with query=`exit.*137|oom.*kill|invoked oom-killer|Memory cgroup` to check for OOM kills
- Use `search` tool with query=`exit.*139|SIGSEGV|segmentation fault|core dump` to check for segfaults
- Use `search` tool with query=`exit.*143|SIGTERM|graceful.*shutdown|signal.*15` to check for termination signals

MAY:
- Use `search` tool with query=`exit.*1|error|exception|fatal|panic` to check for application errors
- Use `search` tool with query=`entrypoint|CMD|command.*not.*found|exec format error` to check entrypoint issues

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline around the container exit
- Determine exit code and map to failure category (OOM, signal, application error, runtime error)
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`memory.*limit|memory.*reservation|memoryReservation` to check memory configuration
- Use `search` tool with query=`health.*check|UNHEALTHY|health.*status` to check if health check failure triggered stop

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the exit code findings
- State root cause: specific exit code, signal, and triggering condition
- Recommend specific remediation based on exit code category

SHOULD:
- Include container logs leading up to the exit
- Provide exit code reference table in summary

## Guardrails

escalation_conditions:
  - "Essential container repeatedly exiting (crash loop)"
  - "Exit code 137 (OOM) across multiple tasks"
  - "Exit code 139 (segfault) indicating memory corruption"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Task definition memory/CPU changes: YELLOW — operator action"
  - "Application code changes: RED — requires development team"

## Common Issues

- symptoms: "Task stopped: Essential container exited, exit code 137"
  diagnosis: "Container killed by OOM killer — memory usage exceeded container memory limit"
  resolution: "Increase memory limit in task definition. Profile application memory usage. Check for memory leaks. Set memoryReservation (soft limit) below memory (hard limit)."

- symptoms: "Task stopped: Essential container exited, exit code 1"
  diagnosis: "Application error — unhandled exception, configuration error, or dependency failure"
  resolution: "Check container logs for error messages. Verify environment variables and secrets. Test container locally with same configuration."

- symptoms: "Task stopped: Essential container exited, exit code 139"
  diagnosis: "Segmentation fault (SIGSEGV) — memory corruption, null pointer, or binary incompatibility"
  resolution: "Check for architecture mismatch (ARM vs x86). Update application dependencies. Enable core dumps for debugging."

- symptoms: "Task stopped: Essential container exited, exit code 143"
  diagnosis: "Container received SIGTERM — graceful shutdown requested by ECS (deployment, scaling, spot interruption)"
  resolution: "Implement SIGTERM handler in application for graceful shutdown. Increase stopTimeout in task definition to allow more time for cleanup."

- symptoms: "Task stopped: Essential container exited, exit code 255"
  diagnosis: "Container runtime error — Docker/containerd failed to start or manage the container"
  resolution: "Check container image validity. Verify entrypoint and CMD. Check for exec format errors (wrong architecture). Review container runtime logs."
