---
title: "K10 — API Throttling & Service Quota Exceeded"
description: "Diagnose and remediate ECS API throttling, rate limiting, and service quota exhaustion"
status: active
severity: HIGH
triggers:
  - "throttl"
  - "rate limit"
  - "Rate exceeded"
  - "TooManyRequestsException"
  - "Limit exceeded"
  - "service quota"
  - "RequestLimitExceeded"
  - "Operations are being throttled"
owner: devops-agent
objective: "Identify throttled APIs and quota limits, restore normal API throughput and task scheduling"
context: "ECS integrates with ELB, Cloud Map, EC2, and other services that each have independent API rate limits. Synchronous throttling returns immediate errors. Asynchronous throttling occurs when ECS invokes APIs on behalf of the user (e.g., ENI provisioning, target registration). At scale, RegisterTarget/DeregisterTarget, EC2 ENI APIs, and Cloud Map APIs are common throttle points. Service quotas limit concurrent Fargate tasks, registered instances per cluster (5000), and tasks in PROVISIONING state."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId (any active instance) to gather cluster-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to find throttling errors
- Use `search` tool with instanceId and query=`throttl|rate.*limit|Rate exceeded|TooManyRequests|Limit exceeded|RequestLimitExceeded` to find evidence

SHOULD:
- Use `search` tool with query=`Operations are being throttled|Will try again later|Cloud Map|RegisterTarget|DeregisterTarget` to identify async throttling
- Use `cluster_health` tool with clusterName to check cluster-wide impact

MAY:
- Use `search` tool with query=`CloudTrail|ErrorCode.*Throttling|exponential.*backoff` to find CloudTrail throttle evidence
- Use `search` tool with query=`service.*quota|concurrent.*tasks|PROVISIONING.*quota` to check quota limits

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of throttling events
- Determine which API is being throttled: ECS, ELB, EC2, or Cloud Map
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`DescribeTargetHealth|CreateNetworkInterface|RegisterInstance` to identify specific throttled operations
- Use `search` tool with query=`desired.*count|running.*count|pending.*count` to assess scale of deployment

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the throttling findings
- State root cause: which API/service is throttled and current vs allowed rate
- Recommend specific remediation to reduce throttling

SHOULD:
- Include deployment scaling recommendations (stagger deployments, reduce batch size)
- Recommend quota increase request if hard limits are hit

## Guardrails

escalation_conditions:
  - "Sustained throttling blocking all deployments"
  - "Service quota hard limit reached"
  - "Multiple services affected by cascading throttle"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Deployment configuration changes: YELLOW — operator action"
  - "Service quota increase request: YELLOW — requires AWS Support"

## Common Issues

- symptoms: "Service event: 'Operations are being throttled. Will try again later'"
  diagnosis: "Cloud Map API throttling during service discovery registration at scale"
  resolution: "Reduce concurrent deployments. Stagger service updates. Contact AWS Support for Cloud Map API limit increase."

- symptoms: "Slow task launches, tasks stuck in PROVISIONING"
  diagnosis: "EC2 ENI API throttling when using awsvpc network mode at scale"
  resolution: "Enable ENI trunking to reduce ENI API calls. Use bridge network mode where possible. Stagger deployments."

- symptoms: "'Limit exceeded' error when creating tasks"
  diagnosis: "Fargate concurrent task quota or tasks-in-PROVISIONING-per-cluster quota exceeded"
  resolution: "Delete unused task definition revisions. Request Fargate quota increase via Service Quotas console."

- symptoms: "ELB target registration delays during deployment"
  diagnosis: "RegisterTarget/DeregisterTarget API throttled with many services behind load balancers"
  resolution: "Reduce number of concurrent service deployments. Use deployment circuit breaker to prevent rapid retry loops."
