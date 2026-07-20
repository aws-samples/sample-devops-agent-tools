---
title: "F1 — Insufficient CPU/Memory for Scheduling"
description: "Diagnose pods stuck in Pending due to insufficient cluster capacity"
status: active
severity: HIGH
triggers:
  - "Insufficient cpu"
  - "Insufficient memory"
  - "0/\\d+ nodes are available"
  - "pod didn't trigger scale-up"
owner: devops-agent
objective: "Identify the scheduling bottleneck and restore pod scheduling"
context: "Pods remain Pending when no node has enough allocatable CPU or memory to satisfy resource requests. Causes include cluster capacity exhaustion, over-provisioned requests, restrictive affinity rules, or autoscaler failures."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Check pod status — pods in Pending state with "Insufficient cpu" or "Insufficient memory" events confirms this SOP
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) for scheduling failure details (FailedScheduling)
  - Check node conditions and allocatable resources: `kubectl describe node <node>` (via EKS MCP `read_k8s_resource`) — compare Allocatable vs Allocated resources
  - Check all nodes: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify which nodes are Ready and available for scheduling
- Use `collect` tool with instanceId of a node where pods cannot schedule to gather node-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to get pre-indexed scheduling-related findings
- Use `search` tool with instanceId and query=`Insufficient cpu|Insufficient memory|FailedScheduling|0/.*nodes are available` to find scheduling evidence in kubelet logs

SHOULD:
- Use `cluster_health` tool with clusterName to get cluster-wide overview including node capacity status
- Use `search` tool with query=`cluster-autoscaler|scale-up|pod didn't trigger` to find autoscaler-related messages

MAY:
- Use `compare_nodes` tool with instanceIds of multiple nodes to identify which nodes are at capacity vs which have headroom
- Use `search` tool with query=`karpenter|provisioner|machine` to check Karpenter activity if used

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`Insufficient` to build timeline of scheduling failures
- Review findings from `errors` tool — if all nodes show allocatable CPU/memory below pending pod requests: cluster capacity exhaustion
- Use `search` tool with query=`node affinity|nodeSelector|didn't match` to check if affinity rules are eliminating nodes
- If autoscaler messages found: use `search` tool with query=`pod didn't trigger scale-up|max size reached|launch.*fail` to determine autoscaler failure reason

SHOULD:
- Use `compare_nodes` tool to diff resource utilization across nodes — identify if specific node groups are full while others have capacity
- Use `search` tool with query=`requests.*cpu|requests.*memory|resource quota` to check if resource requests are over-provisioned

MAY:
- Use `search` tool with query=`PriorityClass|preemption` to check if priority-based scheduling is configured
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="Deployment" to check for recent Deployment scale-up events (replicas increased) that may have exhausted cluster capacity
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="ResourceQuota" to check for ResourceQuota create/update events that may be limiting scheduling

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from scheduling-related findings to generate incident summary
- State root cause: specific scheduling bottleneck (capacity exhaustion, affinity mismatch, autoscaler failure, or over-provisioned requests) with evidence
- Recommend targeted fix based on root cause
- Operator action — not available via MCP tools: scale node group, adjust affinity rules, increase ASG max size, or right-size resource requests

SHOULD:
- Include node capacity vs request numbers from findings
- Include autoscaler status from search results

MAY:
- Recommend VPA for right-sizing resource requests
- Recommend Karpenter for flexible instance selection

## Guardrails

escalation_conditions:
  - "ASG max size reached and cannot be increased"
  - "Instance launch failures (capacity unavailable in AZ)"
  - "Critical workloads stuck in Pending — check via cluster_health"

safety_ratings:
  - "Log collection (collect), search, errors, correlate, cluster_health, compare_nodes: GREEN (read-only)"
  - "Scale node group, adjust ASG max: YELLOW — operator action, not available via MCP tools"
  - "Modify resource requests/limits: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "errors tool returns findings with Insufficient cpu/memory across all nodes"
  diagnosis: "Cluster capacity exhaustion. Use cluster_health to confirm all node groups at capacity."
  resolution: "Operator action: scale node group or add Karpenter provisioner for auto-scaling"

- symptoms: "search for node affinity returns didn't match Pod's node affinity/selector"
  diagnosis: "Scheduling constraints too restrictive — affinity rules eliminate all available nodes."
  resolution: "Operator action: review and relax nodeSelector/affinity rules in pod spec"

- symptoms: "search for autoscaler returns pod didn't trigger scale-up"
  diagnosis: "ASG max size reached or launch template failures. Use search with query=max size reached to confirm."
  resolution: "Operator action: increase ASG max size, check launch template and instance availability"

- symptoms: "compare_nodes shows resource requests 3x+ higher than actual usage"
  diagnosis: "Over-provisioned resource requests wasting capacity."
  resolution: "Operator action: right-size using VPA recommendations or manual adjustment"

- symptoms: "search for node affinity returns 'node(s) had untolerated taint'"
  diagnosis: "Taint/toleration mismatch — nodes have taints that the pod does not tolerate. Use search with query=taint|toleration to identify specific taints."
  resolution: "Operator action: add matching tolerations to pod spec, or remove unnecessary taints from nodes"

- symptoms: "search returns 'Insufficient vpc.amazonaws.com/pod-eni' in scheduling events"
  diagnosis: "Pod security groups require trunk ENI interface, which is only available on Nitro-based instances. Non-Nitro instances cannot allocate pod-eni resources."
  resolution: "Operator action: ensure node group uses Nitro-based instance types (m5, c5, r5, etc.). Enable trunk interface via ENABLE_POD_ENI=true on aws-node DaemonSet."

- symptoms: "search returns 'didn't have free ports for requested pod ports' in scheduling events"
  diagnosis: "Pod uses hostNetwork:true and the requested hostPort is already in use on all available nodes."
  resolution: "Operator action: remove hostNetwork:true if not required, or use different hostPort values. Consider using a Service with NodePort instead."

- symptoms: "search returns 'node(s) didn't match Pod's node affinity/selector' but nodes appear to have capacity"
  diagnosis: "Pod uses requiredDuringSchedulingIgnoredDuringExecution affinity that eliminates all available nodes. Consider using preferredDuringScheduling for soft constraints."
  resolution: "Operator action: change requiredDuringScheduling to preferredDuringScheduling for non-critical placement preferences, or add nodes matching the required labels"

- symptoms: "search returns volume zone mismatch or 'no available volume zone' in scheduling events"
  diagnosis: "PVC is bound to an EBS volume in a different AZ than available nodes. Pod cannot schedule because the volume cannot be attached cross-AZ."
  resolution: "Operator action: create a new PVC in the correct AZ, or add nodes in the AZ where the volume exists. Use volumeBindingMode: WaitForFirstConsumer in StorageClass to prevent this."

## Examples

```
# Step 1: Collect logs from affected node
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get scheduling findings
errors(instanceId="i-0abc123def456", severity="high")
# Step 4: Search for scheduling evidence
search(instanceId="i-0abc123def456", query="Insufficient cpu|Insufficient memory|FailedScheduling")
# Step 5: Check cluster-wide health
cluster_health(clusterName="my-cluster")
# Step 6: Compare nodes for capacity differences
compare_nodes(instanceIds=["i-0abc123def456","i-0xyz789ghi012"])
# Step 7: Correlate timeline
correlate(instanceId="i-0abc123def456", pivotEvent="Insufficient", timeWindow=120)
# Step 8: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002"])
```

## Output Format

```yaml
root_cause: "<capacity_exhaustion|affinity|autoscaler|over_provisioned> — <detail>"
evidence:
  - type: scheduling_finding
    content: "<finding from errors tool showing scheduling failure>"
  - type: cluster_health
    content: "<cluster_health output showing node capacity>"
severity: HIGH
mitigation:
  immediate: "Operator: <scale or adjust based on root cause>"
  long_term: "Deploy VPA, configure Karpenter, implement PriorityClasses"
```
