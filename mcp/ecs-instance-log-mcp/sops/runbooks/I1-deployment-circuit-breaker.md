---
title: "I1 — Deployment Circuit Breaker Triggered"
description: "Diagnose ECS deployment failures that triggered the circuit breaker"
status: active
severity: CRITICAL
triggers:
  - "deployment circuit breaker.*triggered"
  - "ECS Deployment Circuit Breaker was triggered"
  - "deployment circuit breaker.*rolling back"
owner: devops-agent
objective: "Identify why the deployment failed and fix the underlying issue before redeploying"
context: "The ECS deployment circuit breaker automatically rolls back a deployment when tasks repeatedly fail to reach RUNNING state. This prevents bad deployments from taking down the entire service."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical
- Use `search` tool with instanceId and query=`circuit breaker|rolling back|deployment.*fail|unable to place a task` to find deployment failure evidence

SHOULD:
- Use `search` tool with query=`TaskFailedToStart|ResourceInitializationError|CannotPullContainerError` to find the underlying task failure
- Use `cluster_health` tool with clusterName to check overall cluster state

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of deployment events
- Identify the root cause: image pull failure, resource exhaustion, health check failure, or permission issue

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: the underlying task failure that triggered the circuit breaker
- Recommend fixing the root cause before redeploying

## Common Issues

- symptoms: "circuit breaker triggered, tasks failing with CannotPullContainerError"
  diagnosis: "New image tag does not exist or ECR permissions changed"
  resolution: "Verify image exists in registry, fix permissions, then redeploy"

- symptoms: "circuit breaker triggered, tasks failing health checks"
  diagnosis: "New application version has a bug or misconfiguration"
  resolution: "Check application logs, fix the issue, then redeploy"
