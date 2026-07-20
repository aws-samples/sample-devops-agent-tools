---
title: "D3 — Conntrack Exhaustion"
description: "Diagnose connection failures caused by conntrack table full"
status: active
severity: CRITICAL
triggers:
  - "nf_conntrack: table full, dropping packet"
  - "conntrack_allowance_exceeded"
owner: devops-agent
objective: "Identify conntrack exhaustion level (kernel vs AWS instance) and restore connectivity"
context: "Connection tracking (conntrack) maintains state for NAT and stateful firewall rules. When the table fills up, new connections are dropped silently, causing intermittent failures and DNS timeouts."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for pods with connection timeouts or refused connections (symptoms of conntrack exhaustion)
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for conntrack errors
- Use `search` tool with instanceId and query=`nf_conntrack.*table full|conntrack_allowance_exceeded` to find conntrack exhaustion evidence

SHOULD:
- Use `network_diagnostics` tool with instanceId and sections=kube_proxy to get conntrack stats from collected logs
- Use `search` tool with query=`nf_conntrack_max|nf_conntrack_count` to find sysctl conntrack limits

MAY:
- Use `search` tool with query=`ethtool.*conntrack|ena.*conntrack` to check ENA-level conntrack counters
- Use `cluster_health` tool with clusterName to check if multiple nodes are affected

## Phase 2 — Enrich

MUST:
- Use `search` tool with query=`table full` — if found, kernel conntrack limit hit (can increase via sysctl)
- Use `search` tool with query=`conntrack_allowance_exceeded` — if found, AWS instance-level limit (need bigger instance, sysctl will not help)
- Determine which limit is the bottleneck from findings

SHOULD:
- Use `correlate` tool with instanceId and pivotEvent=`conntrack` to correlate conntrack exhaustion with connection failures
- Use `search` tool with query=`nf_conntrack_count|nf_conntrack_max` to calculate utilization percentage

MAY:
- Use `compare_nodes` tool to compare conntrack findings across nodes

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from conntrack-related findings
- State root cause: kernel conntrack limit or AWS instance limit, with evidence
- Recommend specific fix based on which limit is hit (operator action)
- Confirm connections should be restored after fix

SHOULD:
- Include conntrack count vs max values from search results
- Include ENA allowance counter values if available

MAY:
- Recommend conntrack monitoring via CloudWatch agent

## Guardrails

escalation_conditions:
  - "AWS instance-level conntrack limit hit (cannot fix with sysctl)"
  - "Conntrack exhaustion causing DNS failures cluster-wide"
  - "Conntrack table full on multiple nodes simultaneously"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics: GREEN (read-only)"
  - "Modify sysctl settings: YELLOW — operator action, not available via MCP tools"
  - "Upsize instance: RED — operator action, requires approval"

## Common Issues

- symptoms: "search returns nf_conntrack: table full in dmesg findings"
  diagnosis: "Kernel conntrack table limit reached"
  resolution: "Operator action: sysctl -w net.netfilter.nf_conntrack_max=<higher_value>. Make persistent in sysctl.d."

- symptoms: "search returns conntrack_allowance_exceeded > 0 in ethtool findings"
  diagnosis: "AWS instance-level conntrack limit. Kernel sysctl cannot fix this."
  resolution: "Operator action: upgrade to instance type with higher connection tracking allowance."

- symptoms: "search for nf_conntrack_max shows default value (e.g., 131072) with high connection workloads"
  diagnosis: "Kernel conntrack limit too low for workload. Can be increased via kube-proxy ConfigMap or sysctl."
  resolution: "Operator action: increase via kube-proxy ConfigMap — set conntrack.min and conntrack.maxPerCore. Formula: max(min, maxPerCore * number_of_CPU_cores). Then restart kube-proxy DaemonSet: kubectl rollout restart ds/kube-proxy -n kube-system. Each conntrack entry uses ~300 bytes of memory."

- symptoms: "search returns both nf_conntrack table full AND conntrack_allowance_exceeded > 0"
  diagnosis: "Both kernel and AWS instance-level limits are being hit. Kernel limit should be raised first, but AWS limit is the hard ceiling."
  resolution: "Operator action: first increase kernel limit via sysctl or kube-proxy ConfigMap. If conntrack_allowance_exceeded persists, upgrade instance type. Monitor memory impact (~300 bytes per entry)."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Get conntrack findings
errors(instanceId="i-0abc123def456")
# Step 3: Search for conntrack evidence
search(instanceId="i-0abc123def456", query="nf_conntrack.*table full|conntrack_allowance_exceeded")
# Step 4: Get network diagnostics
network_diagnostics(instanceId="i-0abc123def456", sections="kube_proxy")
```

## Output Format

```yaml
root_cause: "<kernel_conntrack|aws_instance_limit> exhaustion"
evidence:
  - type: finding
    content: "<conntrack finding from errors tool>"
  - type: search
    content: "count=<N> max=<M>"
severity: CRITICAL
mitigation:
  immediate: "Operator: increase sysctl limit or upsize instance"
  long_term: "Monitor conntrack metrics, right-size instances"
```
