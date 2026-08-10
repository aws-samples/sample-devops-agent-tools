---
title: "K6 — Service Connect / Service Discovery Failures"
description: "Diagnose and remediate ECS Service Connect and AWS Cloud Map service discovery issues"
status: active
severity: HIGH
triggers:
  - "service connect"
  - "service discovery"
  - "Cloud Map"
  - "namespace"
  - "SERVICE_DISCOVERY_INSTANCE_UNHEALTHY"
  - "SERVICE_DISCOVERY_OPERATION_THROTTLED"
  - "envoy"
  - "proxy"
owner: devops-agent
objective: "Identify service-to-service communication failures and restore service discovery"
context: "ECS Service Connect uses Envoy proxy sidecars for service mesh. Service Discovery uses AWS Cloud Map DNS. Failures include namespace mismatches, proxy crashes, DNS resolution failures, and Cloud Map API throttling."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from an affected container instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to find service connect/discovery errors
- Use `search` tool with instanceId and query=`service connect|service discovery|Cloud Map|namespace|envoy|proxy.*crash|UNHEALTHY` to find evidence

SHOULD:
- Use `search` tool with query=`SERVICE_DISCOVERY_INSTANCE_UNHEALTHY|SERVICE_DISCOVERY_OPERATION_THROTTLED|port.*mapping|ingressPortOverride` to check specific failure types
- Use `network_diagnostics` tool with instanceId to check inter-service connectivity

MAY:
- Use `search` tool with query=`DNS.*resolution|NXDOMAIN|SERVFAIL|resolve.*fail` to check DNS issues
- Use `cluster_health` tool with clusterName to check if discovery works for other services

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of discovery failures
- Determine if the issue is Service Connect (proxy), Cloud Map (DNS), or namespace configuration
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`security group|network ACL|port.*block|connection.*refused` to check network access
- Use `search` tool with query=`sidecar|ecs-service-connect|container.*definition` to check proxy container status

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the discovery failure findings
- State root cause: namespace mismatch, proxy failure, DNS issue, or permissions
- Recommend specific remediation

SHOULD:
- Include service mesh topology showing which services can/cannot communicate
- Recommend verifying namespace configuration across all services

## Guardrails

escalation_conditions:
  - "All inter-service communication broken"
  - "Cloud Map API throttling affecting multiple services"
  - "Envoy proxy crashing in a loop"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Namespace/service configuration changes: YELLOW — operator action"
  - "Security group/network ACL changes: RED — requires approval"

## Common Issues

- symptoms: "Services cannot discover each other"
  diagnosis: "Services not in the same Cloud Map namespace"
  resolution: "Verify all services use the same namespace, update service configuration if needed"

- symptoms: "SERVICE_DISCOVERY_INSTANCE_UNHEALTHY"
  diagnosis: "Container health check failing, causing Cloud Map to mark instance unhealthy"
  resolution: "Fix container health check, verify application responds correctly"

- symptoms: "Service Connect proxy container crashing"
  diagnosis: "Insufficient CPU/memory allocated for the Envoy sidecar"
  resolution: "Add 256 CPU units and 64+ MiB memory to task definition for the proxy container"

- symptoms: "SERVICE_DISCOVERY_OPERATION_THROTTLED"
  diagnosis: "Too many Cloud Map API calls from rapid task churn"
  resolution: "Reduce deployment frequency, increase task stability, contact AWS Support if persistent"

- symptoms: "Connection refused between services using Service Connect"
  diagnosis: "Security group not allowing traffic on containerPort or ingressPortOverride"
  resolution: "Update security groups to allow inbound traffic from client service subnets on the Service Connect port"
