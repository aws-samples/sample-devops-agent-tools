---
title: "K15 — Docker Daemon & Container Runtime Errors"
description: "Diagnose and remediate Docker API 500 errors, Docker daemon issues, containerd failures, and ECS agent runtime problems"
status: active
severity: CRITICAL
triggers:
  - "Docker"
  - "docker"
  - "containerd"
  - "API error 500"
  - "devmapper"
  - "daemon"
  - "thin pool"
  - "storage driver"
  - "docker.sock"
  - "container runtime"
owner: devops-agent
objective: "Restore Docker daemon and container runtime health on ECS container instances"
context: "Docker API 500 errors typically indicate the thin pool storage on a container instance is full, preventing new container creation. The default ECS-optimized AMI provides 8 GiB for OS and 22 GiB for images/metadata. When storage fills up, the Docker daemon cannot create containers. Other runtime issues include containerd failures, docker.sock permission errors, and stale container cleanup. The ECS_ENGINE_TASK_CLEANUP_WAIT_DURATION agent variable controls how long stopped containers remain."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected container instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to find Docker/runtime errors
- Use `search` tool with instanceId and query=`API error.*500|devmapper|thin pool|docker.*daemon|containerd.*error|docker\.sock` to find evidence

SHOULD:
- Use `search` tool with query=`disk.*full|no space left|storage.*driver|overlay2|devicemapper` to check storage driver issues
- Use `search` tool with query=`ECS_ENGINE_TASK_CLEANUP|cleanup.*wait|stopped.*container|dead.*container` to check container cleanup

MAY:
- Use `search` tool with query=`docker.*version|containerd.*version|runc.*version` to check runtime versions
- Use `cluster_health` tool with clusterName to check if multiple instances are affected

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of Docker/runtime failures
- Determine failure type: storage exhaustion, daemon crash, containerd failure, or permission error
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`docker.*info|Storage Driver|Data Space|Metadata Space|Thin Pool` to check Docker storage info
- Use `search` tool with query=`systemctl.*docker|service.*docker|restart.*docker|docker.*start` to check daemon restart attempts

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the Docker/runtime findings
- State root cause: storage exhaustion, daemon failure, or configuration issue
- Recommend specific remediation to restore container runtime

SHOULD:
- Include storage utilization data
- Recommend preventive measures (cleanup duration, larger volumes, monitoring)

## Guardrails

escalation_conditions:
  - "Docker daemon completely unresponsive"
  - "All container launches failing on instance"
  - "Multiple instances with same Docker error"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Docker daemon restart: YELLOW — causes brief task disruption"
  - "Instance replacement: RED — requires draining and replacement"

## Common Issues

- symptoms: "Docker API error (500): devmapper — thin pool full"
  diagnosis: "Container instance thin pool storage exhausted by accumulated images and stopped containers"
  resolution: "Terminate instance and launch new one with larger data volume. Reduce ECS_ENGINE_TASK_CLEANUP_WAIT_DURATION (default 3h). Remove unused images with 'docker image prune'. Use fstrim to reclaim space."

- symptoms: "Cannot connect to Docker daemon at unix:///var/run/docker.sock"
  diagnosis: "Docker daemon not running or crashed"
  resolution: "Check Docker daemon status: systemctl status docker. Review /var/log/docker for crash logs. Restart Docker: systemctl restart docker. If persistent, replace instance."

- symptoms: "containerd: runtime error during container creation"
  diagnosis: "containerd runtime failure — may be caused by corrupted container state or resource exhaustion"
  resolution: "Restart containerd service. Check system memory and disk. If persistent, drain and replace the instance."

- symptoms: "Stale containers consuming disk space"
  diagnosis: "ECS_ENGINE_TASK_CLEANUP_WAIT_DURATION too long, stopped containers accumulating"
  resolution: "Set ECS_ENGINE_TASK_CLEANUP_WAIT_DURATION to a shorter value (e.g., 15m). Manually clean: docker container prune. Monitor disk usage with CloudWatch agent."
