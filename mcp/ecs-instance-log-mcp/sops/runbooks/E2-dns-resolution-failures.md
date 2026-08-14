---
title: "E2 — DNS Resolution Failures"
description: "Diagnose DNS resolution failures affecting ECS containers"
status: active
severity: HIGH
triggers:
  - "DNS.*failed"
  - "name.*resolution.*failed"
  - "no.*route.*host"
owner: devops-agent
objective: "Restore DNS resolution for ECS containers"
context: "DNS failures in ECS can affect service discovery, ECR image pulls, and application connectivity. VPC DNS settings, Docker DNS config, and Route 53 resolver rules all play a role."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=all
- Use `search` tool with instanceId and query=`DNS.*failed|resolve.*fail|SERVFAIL|NXDOMAIN|no route to host` to find DNS errors
- Use `network_diagnostics` tool with instanceId and sections=dns to check resolv.conf

SHOULD:
- Use `search` tool with query=`nameserver|resolv.conf|search.*domain` to check DNS configuration

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to check if DNS failures correlate with other network issues
- Use `network_diagnostics` tool with sections=dns,routes to verify network path to DNS server

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- State root cause: VPC DNS disabled, security group blocking UDP 53, or DNS server unreachable
- Recommend enabling VPC DNS or fixing security group rules

## Common Issues

- symptoms: "DNS resolution failed for ecr.*.amazonaws.com"
  diagnosis: "VPC DNS resolution disabled or security group blocks UDP/TCP 53"
  resolution: "Enable enableDnsSupport and enableDnsHostnames on VPC, check security group outbound rules"
