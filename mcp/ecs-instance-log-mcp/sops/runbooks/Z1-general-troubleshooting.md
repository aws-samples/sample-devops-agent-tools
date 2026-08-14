---
title: "Z1 — General ECS Troubleshooting"
description: "General troubleshooting guide for ECS issues that don't match specific SOPs"
status: active
severity: INFO
triggers:
  - "general"
  - "unknown"
owner: devops-agent
objective: "Provide a systematic approach to diagnosing unclassified ECS issues"
context: "Use this SOP when the issue doesn't match any specific category. Follow the systematic approach to narrow down the problem."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected container instance
- Use `status` tool with executionId to poll until collection completes
- Use `validate` tool with instanceId to verify log bundle completeness
- Use `errors` tool with instanceId and severity=all to get full error summary
- Use `cluster_health` tool with clusterName to check overall cluster state

SHOULD:
- Use `search` tool with instanceId and query=`error|fail|denied|timeout` for broad error search
- Use `network_diagnostics` tool with instanceId and sections=all for network overview

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build event timeline
- Review findings by component to identify the affected subsystem
- Use `compare_instances` tool if healthy instances are available for comparison

SHOULD:
- Use `search` tool with targeted queries based on findings from Phase 1

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the most relevant findings
- Document what was found and what was ruled out
- Recommend next steps for further investigation

## Systematic Approach

1. Check ECS agent connectivity (G1, G2 SOPs)
2. Check task startup (A1, A2 SOPs)
3. Check image pulls (B1, B2, B3 SOPs)
4. Check IAM/secrets (C1, C2 SOPs)
5. Check resources (D1, D2, D3 SOPs)
6. Check networking (E1, E2, E3 SOPs)
7. Check health checks (F1, F2 SOPs)
8. Check logging (H1 SOP)
9. Check deployments (I1 SOP)
10. Check container runtime (J1 SOP)
