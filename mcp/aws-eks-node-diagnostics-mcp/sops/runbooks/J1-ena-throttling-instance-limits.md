---
title: "J1 — ENA Driver Throttling and Instance Network Limits"
description: "Diagnose packet loss and latency caused by ENA driver throttling or instance network limits"
status: active
severity: HIGH
triggers:
  - "bw_in_allowance_exceeded.*[1-9]"
  - "bw_out_allowance_exceeded.*[1-9]"
  - "pps_allowance_exceeded.*[1-9]"
  - "conntrack_allowance_exceeded.*[1-9]"
  - "queue_\\d+_tx_cnt.*drop"
owner: devops-agent
objective: "Identify which network limit is exceeded and recommend instance right-sizing"
context: "EC2 instances have network performance limits (bandwidth, PPS, conntrack, linklocal). ENA driver tracks when these limits are exceeded via ethtool counters. Outdated ENA drivers may also have performance issues."
---

## Phase 1 — Triage

FIRST — Check node state before collecting logs:
- Use `list_k8s_resources` with clusterName, kind=Node, apiVersion=v1 to list all nodes — check if the affected node is Ready and identify its instance type from node labels (node.kubernetes.io/instance-type)
- Use `read_k8s_resource` with clusterName, kind=Node, apiVersion=v1, name=<node-name> to get detailed node status — check conditions, allocatable resources, and instance type label for network limit reference
- Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, fieldSelector=spec.nodeName=<node-name> to list pods on the node — check for pods experiencing network timeouts or connection failures
- Use `get_k8s_events` with clusterName, kind=Node, name=<node-name> to check for network-related warning events

MUST:
- Use `collect` tool with instanceId of the affected node to gather node-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to get pre-indexed ENA throttling findings
- Use `search` tool with instanceId and query=`bw_in_allowance_exceeded|bw_out_allowance_exceeded|pps_allowance_exceeded|conntrack_allowance_exceeded|linklocal_allowance_exceeded` to find ENA throttling evidence
- Use `network_diagnostics` tool with instanceId and sections=eni to check ENA stats and interface health

SHOULD:
- Use `search` tool with query=`ena.*version|modinfo ena|ena driver` to check ENA driver version
- Use `search` tool with query=`instance-type|instance type` to identify the instance type and its network limits

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes have ENA throttling
- Use `compare_nodes` tool with instanceIds to compare ENA stats across nodes

## Phase 2 — Enrich

MUST:
- Review findings from `errors` tool and `network_diagnostics` to map nonzero counters to root cause:
  - bw_in/out_allowance_exceeded: bandwidth limit hit — upgrade instance
  - pps_allowance_exceeded: packet rate limit — reduce small packets or upgrade
  - conntrack_allowance_exceeded: connection tracking limit — reduce connections or upgrade
  - linklocal_allowance_exceeded: DNS/IMDS/NTP rate limit — use NodeLocal DNSCache
- Use `search` tool with query=`ena.*version|driver version` to check ENA driver version (< 2.8 may have performance issues)
- Use `correlate` tool with instanceId and pivotEvent=`allowance_exceeded` to build timeline of throttling events

SHOULD:
- Use `network_diagnostics` to compare instance type specs against actual throughput
- Use `search` tool with query=`drop|error|tx_cnt` to check for packet drops on interfaces

MAY:
- Use `compare_nodes` tool to identify which nodes are most affected by throttling

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from ENA throttling findings to generate incident summary
- State which limit(s) are exceeded with counter values from findings and network_diagnostics
- Recommend instance type with sufficient network specs
- Operator action — not available via MCP tools: upgrade instance type, update ENA driver, configure NodeLocal DNSCache

SHOULD:
- Include ENA counter values and instance type from findings
- Include ENA driver version from search results

MAY:
- Recommend CloudWatch alarms on allowance exceeded metrics
- Recommend placement groups for high-throughput workloads

## Guardrails

escalation_conditions:
  - "Largest available instance type still insufficient"
  - "Multiple allowance types exceeded simultaneously — check via network_diagnostics"
  - "ENA driver update requires node replacement"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics, correlate, compare_nodes: GREEN (read-only)"
  - "Upgrade instance type: YELLOW — operator action, not available via MCP tools"
  - "Update ENA driver: YELLOW — operator action, requires node replacement"

## Common Issues

- symptoms: "network_diagnostics shows bw_in/out_allowance_exceeded > 0"
  diagnosis: "Instance bandwidth limit hit. Use search with query=instance-type to identify current instance."
  resolution: "Operator action: upgrade to instance type with higher baseline bandwidth"

- symptoms: "search returns pps_allowance_exceeded > 0"
  diagnosis: "Packets per second limit hit. Use network_diagnostics to confirm PPS counters."
  resolution: "Operator action: reduce small packet workloads or upgrade instance type"

- symptoms: "search returns linklocal_allowance_exceeded > 0"
  diagnosis: "DNS/IMDS/NTP rate limit hit — too many requests to link-local addresses."
  resolution: "Operator action: deploy NodeLocal DNSCache to reduce DNS traffic to link-local"

- symptoms: "search for ena version shows version < 2.8"
  diagnosis: "Outdated ENA driver may have performance issues."
  resolution: "Operator action: update ENA driver to latest version (requires node replacement)"

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get ENA throttling findings
errors(instanceId="i-0abc123def456", severity="high")
# Step 4: Check ENA stats and interface health
network_diagnostics(instanceId="i-0abc123def456", sections="eni")
# Step 5: Search for ENA throttling evidence
search(instanceId="i-0abc123def456", query="bw_in_allowance_exceeded|pps_allowance_exceeded|conntrack_allowance_exceeded")
# Step 6: Check ENA driver version
search(instanceId="i-0abc123def456", query="ena.*version|modinfo ena")
# Step 7: Correlate throttling timeline
correlate(instanceId="i-0abc123def456", pivotEvent="allowance_exceeded", timeWindow=120)
# Step 8: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002"])
```

## Output Format

```yaml
root_cause: "<allowance_type> exceeded on <instance_type>"
evidence:
  - type: network_diagnostics
    content: "<ENA counter values from network_diagnostics>"
  - type: ena_driver
    content: "version=<version> from search results"
severity: HIGH
mitigation:
  immediate: "Operator: upgrade instance type for higher network limits"
  long_term: "Monitor ENA metrics, right-size instances, update ENA driver"
```