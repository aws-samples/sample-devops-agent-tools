---
title: "D6 — ENA Throttling / Bandwidth Allowance"
description: "Diagnose packet loss and latency caused by ENA network allowance exceeded"
status: active
severity: HIGH
triggers:
  - "bw_in_allowance_exceeded"
  - "bw_out_allowance_exceeded"
  - "pps_allowance_exceeded"
  - "conntrack_allowance_exceeded"
  - "linklocal_allowance_exceeded"
owner: devops-agent
objective: "Identify which ENA allowance is exceeded and recommend instance right-sizing"
context: "AWS instances have network performance limits (bandwidth, PPS, conntrack, linklocal). When exceeded, packets are silently dropped causing intermittent failures. These are instance-level limits that cannot be changed via OS configuration."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for pods experiencing network throttling or timeouts
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for ENA throttling errors
- Use `search` tool with instanceId and query=`allowance_exceeded|bw_in_allowance|bw_out_allowance|pps_allowance|conntrack_allowance|linklocal_allowance` to find ENA throttling evidence

SHOULD:
- Use `network_diagnostics` tool with instanceId and sections=eni to get ENI stats from collected logs
- Use `search` tool with query=`instance-type|meta-data` to identify instance type

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes have ENA throttling
- Use `compare_nodes` tool to compare ENA stats across nodes

## Phase 2 — Enrich

MUST:
- Map nonzero counters from search results to root cause:
  - bw_*_allowance_exceeded: traffic volume exceeds instance bandwidth
  - pps_allowance_exceeded: small packet rate too high
  - conntrack_allowance_exceeded: too many tracked connections (see D3 SOP)
  - linklocal_allowance_exceeded: DNS/IMDS/NTP request rate too high (see D5 SOP)
- Use `correlate` tool with instanceId and pivotEvent=`allowance_exceeded` to correlate throttling with application failures

SHOULD:
- Use `search` tool with query=`ethtool.*drop|rx_drop|tx_drop` to check for packet drops
- Determine if throttling is sustained or burst from correlate timeline

MAY:
- Use `compare_nodes` tool to identify which nodes are most affected

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from ENA-related findings
- State which allowance(s) are exceeded with counter values from search results
- Recommend instance type upgrade with specific network specs needed (operator action)
- Confirm throttling should resolve after mitigation

SHOULD:
- Include ENA counter values from search results
- Include instance type and its network limits

MAY:
- Recommend CloudWatch alarms on allowance exceeded metrics
- Recommend NodeLocal DNSCache for linklocal throttling

## Guardrails

escalation_conditions:
  - "Multiple allowance types exceeded simultaneously"
  - "Largest available instance type still insufficient"
  - "Throttling causing cascading application failures"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics: GREEN (read-only)"
  - "Upsize instance: RED — operator action, requires approval"

## Common Issues

- symptoms: "search returns bw_in/out_allowance_exceeded > 0"
  diagnosis: "Instance bandwidth limit hit"
  resolution: "Operator action: upgrade to instance type with higher baseline bandwidth"

- symptoms: "search returns pps_allowance_exceeded > 0"
  diagnosis: "Packets per second limit hit (common with many small packets)"
  resolution: "Operator action: reduce small packet workloads or upgrade instance type"

- symptoms: "search returns linklocal_allowance_exceeded > 0"
  diagnosis: "DNS/IMDS/NTP request rate too high"
  resolution: "Operator action: deploy NodeLocal DNSCache, use IRSA instead of IMDS"

- symptoms: "search returns conntrack_allowance_exceeded > 0 alongside bw or pps allowance exceeded"
  diagnosis: "Multiple ENA allowances exceeded simultaneously. Connection tracking limit is separate from bandwidth/PPS limits. See D3-conntrack-exhaustion SOP for conntrack-specific troubleshooting."
  resolution: "Operator action: upgrade to instance type with higher limits across all allowance types. Configure security group rules to avoid tracking where possible (untracked connections do not count against conntrack allowance)."

- symptoms: "search returns ENA keep-alive watchdog timeout or 'Trigger reset is on' in dmesg"
  diagnosis: "ENA device experienced a failure and triggered a reset. This causes brief traffic loss while the driver reinitializes. Check ethtool -S for wd_expired counter."
  resolution: "Operator action: check for ENA driver version compatibility — update to latest ENA driver. If persistent, check for instance hardware issues and consider replacing the instance."

- symptoms: "search returns queue_N_tx_queue_stop > 0 or queue_N_rx_page_alloc_fail > 0"
  diagnosis: "ENA queue-level issues — tx_queue_stop indicates transmit queue full (bandwidth saturation), rx_page_alloc_fail indicates low memory preventing packet reception."
  resolution: "Operator action: for tx_queue_stop, reduce traffic or upgrade instance. For rx_page_alloc_fail, check memory pressure on the node (see G2-oomkill SOP) and ensure sufficient free memory."

- symptoms: "search returns conntrack_allowance_available showing low values (approaching 0)"
  diagnosis: "Connection tracking allowance is nearly exhausted. New connections will be dropped when it reaches 0. Monitor conntrack_allowance_available metric proactively."
  resolution: "Operator action: reduce tracked connections by configuring security group rules to avoid tracking (symmetric rules with 0.0.0.0/0 are untracked). Reduce idle connection timeout. Upgrade instance type if needed."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Search for ENA throttling
search(instanceId="i-0abc123def456", query="allowance_exceeded")
# Step 3: Get ENI diagnostics
network_diagnostics(instanceId="i-0abc123def456", sections="eni")
# Step 4: Correlate with failures
correlate(instanceId="i-0abc123def456", pivotEvent="allowance_exceeded")
```

## Output Format

```yaml
root_cause: "<allowance_type> exceeded on <instance_type>"
evidence:
  - type: search
    content: "<counter_name>=<value>"
severity: HIGH
mitigation:
  immediate: "Operator: upgrade instance type for higher network limits"
  long_term: "Monitor ENA metrics, right-size instances, use NodeLocal DNSCache"
```
