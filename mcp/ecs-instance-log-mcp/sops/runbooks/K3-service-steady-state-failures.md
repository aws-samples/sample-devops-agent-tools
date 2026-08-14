---
title: "K3 — Service Steady State Failures"
description: "Diagnose and remediate ECS services that fail to reach or maintain steady state"
status: active
severity: CRITICAL
triggers:
  - "service.*unable to reach steady state"
  - "SERVICE_TASK_START_IMPAIRED"
  - "tasks failed to start"
  - "deployment failed"
  - "SERVICE_DEPLOYMENT_FAILED"
  - "rolling back"
  - "not healthy in target-group"
owner: devops-agent
objective: "Identify why a service cannot reach steady state and restore service stability"
context: "A service fails to reach steady state when tasks repeatedly fail health checks, exit with errors, cannot be placed, or fail to start. The service scheduler continuously tries to replace failed tasks, creating a restart loop."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from an affected container instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to find service-level errors
- Use `search` tool with instanceId and query=`steady state|deployment failed|tasks failed to start|SERVICE_TASK_START_IMPAIRED|rolling back` to find steady state failure evidence

SHOULD:
- Use `search` tool with query=`health check|unhealthy|target.*not.*found|deregistered` to check health check failures
- Use `search` tool with query=`exit code|non-zero|EssentialContainerExited|OOMKilled` to check task exit reasons
- Use `cluster_health` tool with clusterName to check overall service health

MAY:
- Use `compare_instances` tool to diff instances running healthy vs failing tasks
- Use `network_diagnostics` tool with instanceId to check network-related causes

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of service events
- Determine the specific failure mode: health check, task crash, placement, or configuration
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`HealthCheckGracePeriod|healthCheckPath|deregistration.*delay` to check health check configuration
- Use `search` tool with query=`minimumHealthyPercent|maximumPercent|desiredCount` to check deployment configuration

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the steady state failure findings
- State root cause: which component is preventing steady state
- Recommend specific remediation based on failure mode

SHOULD:
- Include deployment timeline showing task start/stop cycles
- Recommend adjusting HealthCheckGracePeriodSeconds if tasks need more startup time

## Guardrails

escalation_conditions:
  - "Service in continuous restart loop for more than 30 minutes"
  - "All tasks in service failing simultaneously"
  - "Deployment circuit breaker triggered"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Health check parameter changes: YELLOW — operator action"
  - "Service/task definition changes: YELLOW — operator action"
  - "Rollback to previous task definition: RED — requires approval"

## Common Issues

- symptoms: "service unable to reach steady state — tasks failing health checks"
  diagnosis: "Application not responding on health check path within timeout"
  resolution: "Increase HealthCheckGracePeriodSeconds, verify health check path returns 200, check application startup time"

- symptoms: "service unable to reach steady state — ELB health checks failing"
  diagnosis: "Security group blocking health check traffic or wrong port/path"
  resolution: "Verify security group allows ALB to reach container port, confirm health check path and expected response code"

- symptoms: "tasks exiting with non-zero exit code"
  diagnosis: "Application crashing due to configuration error, missing env vars, or dependency failure"
  resolution: "Check CloudWatch Logs for application errors, verify environment variables and secrets, test container locally"

- symptoms: "SERVICE_DEPLOYMENT_FAILED with circuit breaker"
  diagnosis: "Too many consecutive task failures triggered the deployment circuit breaker"
  resolution: "Fix the underlying task failure, then create a new deployment with the corrected task definition"
