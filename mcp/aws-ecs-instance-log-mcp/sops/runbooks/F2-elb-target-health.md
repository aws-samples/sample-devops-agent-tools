---
title: "F2 — ELB Target Health Check Failures"
description: "Diagnose ALB/NLB target health check failures for ECS services"
status: active
severity: HIGH
triggers:
  - "target.*unhealthy"
  - "failed ELB health checks"
  - "Instance.*port.*is unhealthy"
owner: devops-agent
objective: "Restore ELB target health for ECS service"
context: "ELB health checks are separate from container health checks. Failed ELB health checks cause targets to be deregistered, reducing service capacity. Common causes: security group rules, health check path returning non-200, or application startup time."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=all
- Use `search` tool with instanceId and query=`target.*unhealthy|ELB health|Instance.*unhealthy|deregistering.*target` to find ELB health failures

SHOULD:
- Use `network_diagnostics` tool with instanceId and sections=security-groups to verify port access
- Use `search` tool with query=`health.*path|health.*check.*port` to check health check configuration

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to check if ELB failures correlate with deployments or resource issues

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: security group blocking health check port, wrong health check path, or slow startup
- Recommend specific fix

## Common Issues

- symptoms: "target unhealthy, health check on port 80 returning 502"
  diagnosis: "Application not ready or returning errors on health check path"
  resolution: "Verify health check path returns 200, increase deregistration delay and health check grace period"
