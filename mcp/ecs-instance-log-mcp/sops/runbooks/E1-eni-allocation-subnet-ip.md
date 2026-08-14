---
title: "E1 — ENI Allocation / Subnet IP Exhaustion"
description: "Diagnose ENI provisioning failures and subnet IP exhaustion in ECS"
status: active
severity: CRITICAL
triggers:
  - "ENI.*allocation.*failed"
  - "InsufficientFreeAddressesInSubnet"
  - "Timeout waiting for network interface"
  - "failed.*create.*network.*interface"
owner: devops-agent
objective: "Restore ENI provisioning for ECS tasks using awsvpc networking"
context: "Tasks using awsvpc mode require an ENI per task. Failures occur when the subnet runs out of IPs, ENI limits are reached, or security group limits are exceeded."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical
- Use `search` tool with instanceId and query=`ENI.*alloc|InsufficientFreeAddresses|network interface.*fail|Timeout.*network` to find ENI errors
- Use `network_diagnostics` tool with instanceId and sections=eni to check ENI attachment status

SHOULD:
- Use `search` tool with query=`subnet|available.*ip|ip.*address` to check subnet capacity

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to determine when ENI failures started
- Use `network_diagnostics` tool with sections=eni,security-groups to get full network picture

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: subnet IP exhaustion, ENI limit, or security group limit
- Recommend adding IPs to subnet or using ENI trunking

## Common Issues

- symptoms: "InsufficientFreeAddressesInSubnet"
  diagnosis: "Subnet CIDR too small for number of tasks"
  resolution: "Use larger subnet, enable ENI trunking, or reduce task count"

- symptoms: "Timeout waiting for network interface provisioning"
  diagnosis: "ENI creation taking too long, possible API throttling"
  resolution: "Check AWS service health, reduce concurrent task launches"
