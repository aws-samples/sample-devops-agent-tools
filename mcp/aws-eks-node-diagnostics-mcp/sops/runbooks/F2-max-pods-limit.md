---
title: "F2 — Max Pods Limit Reached"
description: "Diagnose pods stuck in Pending due to instance max-pods limit"
status: active
severity: HIGH
triggers:
  - "Too many pods"
  - "max pods.*reached"
  - "cannot allocate.*pod"
owner: devops-agent
objective: "Identify max-pods bottleneck and increase pod density or scale nodes"
context: "Each EC2 instance type has a max-pods limit based on ENI count and IPs-per-ENI. When reached, no new pods can schedule on that node. Prefix delegation can increase this limit 16x."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Check pod status — pods in Pending state with "Too many pods" event confirms this SOP
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) for "Too many pods" or max-pods scheduling failures
  - Check node details: `kubectl describe node <node>` (via EKS MCP `read_k8s_resource`) — compare current pod count vs allocatable pods
  - Check all nodes: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — check if all nodes are at max pods
- Use `collect` tool with instanceId of the affected node to gather node-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to get pre-indexed findings related to pod limits
- Use `search` tool with instanceId and query=`Too many pods|max pods|cannot allocate pod|maxPods` to find max-pods evidence in kubelet logs

SHOULD:
- Use `search` tool with query=`ENABLE_PREFIX_DELEGATION|prefix delegation|warm-prefix` to check if prefix delegation is enabled
- Use `network_diagnostics` tool with instanceId and sections=cni,eni,ipamd to check ENI allocation and IP capacity

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes are hitting pod limits
- Use `compare_nodes` tool with instanceIds to compare pod density across nodes

## Phase 2 — Enrich

MUST:
- Review findings from `errors` tool — if findings show max-pods reached: ENI-based limit hit
- Use `search` tool with query=`kubelet-config|maxPods|--max-pods` to check if max-pods is explicitly overridden below ENI limit
- Use `network_diagnostics` tool to confirm ENI count and IPs-per-ENI for the instance type
- Use `search` tool with query=`DaemonSet|daemonset` in kubelet logs to estimate DaemonSet pod overhead

SHOULD:
- Use `search` tool with query=`prefix delegation|ENABLE_PREFIX_DELEGATION=true` to determine if prefix delegation is already enabled
- Calculate pod density from findings: running pods / max pods

MAY:
- Use `search` tool with query=`karpenter.*maxPods` to check Karpenter override settings

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from pod-limit findings to generate incident summary
- State root cause: max-pods limit with current count vs limit from findings
- Recommend fix based on root cause
- Operator action — not available via MCP tools: enable prefix delegation, update kubelet config, or change instance type

SHOULD:
- Include pod count, max-pods value, and instance type from findings
- Include ENI/IP details from network_diagnostics

MAY:
- Recommend Karpenter with maxPods override for flexible density

## Guardrails

escalation_conditions:
  - "Prefix delegation cannot be enabled (CNI version too old)"
  - "All nodes in cluster at max-pods — check via cluster_health"
  - "DaemonSet count cannot be reduced"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics, cluster_health: GREEN (read-only)"
  - "Enable prefix delegation, update kubelet config: YELLOW — operator action, not available via MCP tools"
  - "Change instance type: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "errors tool returns findings with Too many pods on specific nodes"
  diagnosis: "Instance type ENI/IP limit reached. Use network_diagnostics to confirm ENI count."
  resolution: "Operator action: enable prefix delegation (ENABLE_PREFIX_DELEGATION=true on aws-node) for 16x density"

- symptoms: "search for maxPods shows value set below ENI limit"
  diagnosis: "Manual --max-pods override too restrictive in kubelet config."
  resolution: "Operator action: update kubelet config or launch template user data to remove override"

- symptoms: "search for DaemonSet shows >30% of pod slots consumed by DaemonSets"
  diagnosis: "DaemonSet overhead too high, reducing available pod slots for workloads."
  resolution: "Operator action: consolidate DaemonSets or increase max-pods via prefix delegation"

- symptoms: "search returns 'Too many pods' and network_diagnostics shows all ENI slots consumed"
  diagnosis: "Instance ENI/IP limit reached. Each instance type has a fixed max ENI count and IPs-per-ENI. Use aws ec2 describe-instance-types --instance-types <type> --query 'InstanceTypes[].NetworkInfo.{MaxENI:MaximumNetworkInterfaces,IPv4PerENI:Ipv4AddressesPerInterface}' to check limits."
  resolution: "Operator action: enable prefix delegation (ENABLE_PREFIX_DELEGATION=true) for up to 110 pods on most Nitro instances, or upgrade to instance type with more ENIs"

- symptoms: "errors tool returns 'Too many pods' but max-pods value appears lower than expected for instance type"
  diagnosis: "Max-pods may be calculated incorrectly or overridden. Default formula: (MaxENI * (IPv4PerENI - 1)) + 2. With prefix delegation: (MaxENI * ((IPv4PerENI - 1) * 16)) + 2."
  resolution: "Operator action: verify max-pods calculation matches instance type. Check if --max-pods is overridden in kubelet args or launch template user data."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get pod limit findings
errors(instanceId="i-0abc123def456", severity="high")
# Step 4: Search for max-pods evidence
search(instanceId="i-0abc123def456", query="Too many pods|max pods|maxPods")
# Step 5: Check ENI/IP allocation
network_diagnostics(instanceId="i-0abc123def456", sections="cni,eni,ipamd")
# Step 6: Check prefix delegation status
search(instanceId="i-0abc123def456", query="ENABLE_PREFIX_DELEGATION|prefix delegation")
# Step 7: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002"])
```

## Output Format

```yaml
root_cause: "Max pods limit reached — <count>/<limit> on <instance_type>"
evidence:
  - type: pod_limit_finding
    content: "<finding from errors tool showing pod limit>"
  - type: network_diagnostics
    content: "<ENI count and IP allocation from network_diagnostics>"
severity: HIGH
mitigation:
  immediate: "Operator: enable prefix delegation or scale to larger instance"
  long_term: "Use Karpenter with maxPods override, right-size pods"
```
