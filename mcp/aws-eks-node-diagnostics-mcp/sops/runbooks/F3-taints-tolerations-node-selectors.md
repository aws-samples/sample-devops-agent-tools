---
title: "F3 — Taints, Tolerations, and Node Selector Mismatches"
description: "Diagnose pods stuck in Pending due to taint/toleration or node selector mismatches"
status: active
severity: MEDIUM
triggers:
  - "had untolerated taint"
  - "didn't match Pod's node affinity"
  - "didn't match Pod's node selector"
owner: devops-agent
objective: "Identify the scheduling constraint mismatch and restore pod scheduling"
context: "Pods remain Pending when taints, tolerations, nodeSelectors, or affinity rules prevent scheduling. Common causes include maintenance taints not removed, missing labels, or overly restrictive affinity rules."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Check pod status — pods in Pending state with taint/toleration or nodeSelector mismatch confirms this SOP
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) for "didn't match Pod's node affinity/selector" or taint-related scheduling failures
  - Check node taints and labels: `kubectl describe node <node>` (via EKS MCP `read_k8s_resource`) — look at Taints and Labels sections
  - Check all nodes: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify which nodes are available
- Use `collect` tool with instanceId of a node where pods are expected to schedule to gather node-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings including taint/scheduling issues
- Use `search` tool with instanceId and query=`untolerated taint|didn't match.*node affinity|didn't match.*node selector|NoSchedule|NoExecute` to find scheduling constraint evidence

SHOULD:
- Use `cluster_health` tool with clusterName to check overall node status and taint distribution
- Use `search` tool with query=`taint|toleration|nodeSelector|affinity` in kubelet logs to find scheduling-related configuration

MAY:
- Use `compare_nodes` tool with instanceIds of tainted vs untainted nodes to diff configurations
- Use `search` tool with query=`maintenance|cordon|drain` to check if taints are from maintenance operations

## Phase 2 — Enrich

MUST:
- Review findings from `errors` tool — if findings show "untolerated taint": identify the taint key/value and whether it is intentional
- Use `search` tool with query=`NoSchedule|NoExecute|PreferNoSchedule` to identify all active taints on the node
- If findings show "didn't match node selector": use `search` tool with query=`nodeSelector|label` to identify the missing label
- If all nodes appear tainted: use `cluster_health` to confirm no schedulable nodes exist

SHOULD:
- Use `search` tool with query=`gpu|dedicated|special-purpose` to determine if taints are for GPU/special-purpose nodes blocking general workloads
- Use `correlate` tool with instanceId and pivotEvent=`taint` to check when taints were applied

MAY:
- Use `compare_nodes` tool to compare taint configurations across node groups
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="taint" to check for recent taint mutations on nodes (e.g., maintenance taints applied but not removed)
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="label" to check for recent label changes on nodes that may have broken nodeSelector matching

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from scheduling-related findings to generate incident summary
- State root cause: specific scheduling constraint mismatch with taint key/value or missing label
- Recommend targeted fix based on root cause
- Operator action — not available via MCP tools: add toleration to pod spec, remove taint from node, or add label to node

SHOULD:
- Include the specific taint or label causing the mismatch from findings

MAY:
- Recommend OPA/Gatekeeper policies for scheduling constraint governance

## Guardrails

escalation_conditions:
  - "All nodes tainted with NoSchedule and no untainted nodes available — check via cluster_health"
  - "Taint removal requires approval (production node group)"
  - "Affinity rules set by platform team and cannot be changed"

safety_ratings:
  - "Log collection (collect), search, errors, cluster_health, compare_nodes: GREEN (read-only)"
  - "Add toleration to pod spec: YELLOW — operator action, not available via MCP tools"
  - "Remove taint from node: YELLOW — operator action, not available via MCP tools"
  - "Add label to node: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "errors tool returns findings with untolerated taint {key}={value}:NoSchedule"
  diagnosis: "Node tainted but pod lacks matching toleration. Use search with query=NoSchedule to identify the taint."
  resolution: "Operator action: add toleration to pod spec OR remove taint from node"

- symptoms: "search returns didn't match Pod's node selector"
  diagnosis: "Pod nodeSelector references label not present on any node. Use cluster_health to check node labels."
  resolution: "Operator action: add label to nodes or update pod nodeSelector"

- symptoms: "cluster_health shows all nodes tainted with NoSchedule"
  diagnosis: "No schedulable nodes exist — possibly maintenance taints not removed."
  resolution: "Operator action: remove maintenance taints from recovered nodes"

## Examples

```
# Step 1: Collect logs from affected node
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get scheduling findings
errors(instanceId="i-0abc123def456")
# Step 4: Search for taint/selector evidence
search(instanceId="i-0abc123def456", query="untolerated taint|didn't match.*node affinity|NoSchedule")
# Step 5: Check cluster-wide taint status
cluster_health(clusterName="my-cluster")
# Step 6: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002"])
```

## Output Format

```yaml
root_cause: "<taint_mismatch|label_missing|affinity_restrictive> — <detail>"
evidence:
  - type: scheduling_finding
    content: "<finding from errors tool showing constraint failure>"
  - type: taint_search
    content: "<search results showing active taints>"
severity: MEDIUM
mitigation:
  immediate: "Operator: add toleration/label or remove taint"
  long_term: "Document taint/toleration strategy, use admission webhooks"
```
