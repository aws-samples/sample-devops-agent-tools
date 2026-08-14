---
title: "E3 — Connection Timeout / Network Unreachable"
description: "Diagnose connection timeouts and network unreachable errors in ECS"
status: active
severity: HIGH
triggers:
  - "connection.*timeout"
  - "network.*unreachable"
  - "dial.*tcp.*timeout"
  - "i/o timeout"
owner: devops-agent
objective: "Identify network connectivity issue and restore communication"
context: "Connection timeouts in ECS can be caused by security group rules, NACLs, route table misconfigurations, or VPC endpoint issues."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=all
- Use `search` tool with instanceId and query=`connection.*timeout|network.*unreachable|dial.*tcp.*timeout|i/o timeout` to find timeout errors
- Use `network_diagnostics` tool with instanceId and sections=all to get full network picture

SHOULD:
- Use `search` tool with query=`security.*group|nacl|route.*table` to check network config

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to determine if timeouts are intermittent or persistent
- Use `network_diagnostics` tool with sections=routes,security-groups to check routing and SG rules

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: security group, NACL, route table, or VPC endpoint issue
- Recommend specific network configuration fix

## Common Issues

- symptoms: "connection timeout to 443 for ECR/S3 endpoints"
  diagnosis: "Missing VPC endpoints or NAT gateway for private subnet"
  resolution: "Create VPC endpoints for ECR, S3, and CloudWatch Logs, or configure NAT gateway"
