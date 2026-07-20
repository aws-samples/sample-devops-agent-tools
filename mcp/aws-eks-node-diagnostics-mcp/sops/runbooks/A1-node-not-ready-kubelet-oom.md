---
title: "A1 — Node NotReady Due to Kubelet OOM"
description: "Diagnose and remediate EKS worker node transitioning to NotReady caused by kubelet OOM-kill"
status: active
severity: CRITICAL
triggers:
  - "invoked oom-killer.*kubelet"
  - "Memory cgroup out of memory"
  - "node.*NotReady"
owner: devops-agent
objective: "Confirm kubelet OOM as root cause, collect memory evidence, and restore node to Ready state"
context: "Kubelet process killed by kernel OOM-killer causes node to lose heartbeat and transition to NotReady. Pods on the node become orphaned until the node recovers or is replaced."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — confirm the node is NotReady
  - Check node details: `kubectl describe node <node>` (via EKS MCP `read_k8s_resource`) — look at Conditions (MemoryPressure, DiskPressure, PIDPressure) and allocatable resources
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for pods in CrashLoopBackOff, OOMKilled, or Evicted state
  - Check kubelet pod status on the node — if kubelet is OOM-killed, all pods on the node will be affected
- **PREREQUISITE — Is kubelet running?** Before investigating OOM, verify the kubelet process is alive:
  - Use `collect` tool with instanceId to gather logs from the affected node
  - Use `status` tool with executionId to poll until collection completes
  - Use `search` tool with instanceId and query=`Active: active \(running\)|kubelet.*started|kubelet.service.*running` and logTypes=`kubelet` — if NO matches, kubelet is stopped/dead. That is the root cause, not necessarily OOM.
  - Use `search` tool with instanceId and query=`Active: inactive|Active: failed|kubelet.service.*dead|kubelet.service.*failed` — if matches found, kubelet is stopped. Check dmesg for OOM evidence before concluding.
  - If kubelet is stopped but NO OOM evidence found in dmesg, report "kubelet service not running — cause unknown, not OOM" and investigate further (check B1 SOP for config issues).
  - ONLY if kubelet is confirmed stopped AND OOM evidence exists, OR kubelet is running but under memory pressure, proceed to OOM investigation below.
- Use `collect` tool with instanceId to start log collection from the affected node (skip if already collected in prerequisite)
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to get pre-indexed OOM findings
- Use `search` tool with instanceId and query=`oom-killer|OOMKilled|out of memory|Memory cgroup` to find OOM evidence in dmesg and system logs

SHOULD:
- Use `search` tool with query=`MemoryPressure|memory pressure|MemAvailable` to find memory pressure signals
- Use `search` tool with query=`kubelet.*restart|kubelet.*start` to detect kubelet restart events

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes are affected
- Use `compare_nodes` tool with instanceIds of affected + healthy node to diff findings

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`oom-killer` to build timeline around the OOM event
- Confirm the killed process is kubelet by reviewing findings from `errors` tool (look for finding with message containing kubelet PID)
- Use `validate` tool with instanceId to confirm log bundle has dmesg and kubelet logs

SHOULD:
- Use `search` tool with query=`system-reserved|kube-reserved` in kubelet config logs to check memory reservation settings
- Use `errors` tool with severity=all to check for recurring OOM events (multiple findings with oom-killer pattern)

MAY:
- Use `compare_nodes` tool to compare memory-related findings between affected node and a healthy peer

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the OOM-related findings to generate incident summary
- State root cause: kubelet OOM-killed by kernel, with PID and timestamp evidence from findings
- List blast radius: affected node, number of orphaned pods (from triage output)
- Recommend immediate mitigation: cordon node, drain if needed (operator action)
- Recommend long-term fix: increase instance type or set system-reserved memory

SHOULD:
- Include dmesg excerpt from `search` results showing OOM kill line
- Include memory stats from findings
- Provide estimated time to recovery

MAY:
- Suggest Karpenter or cluster autoscaler configuration for dynamic scaling
- Recommend monitoring alert for node memory > 85%

## Guardrails

escalation_conditions:
  - "Multiple nodes in NotReady state simultaneously (check via cluster_health)"
  - "OOM kills recurring within 30 minutes of kubelet restart"
  - "Node does not recover after kubelet restart + 5 minutes"
  - "System-reserved memory already at recommended values and OOM persists"

safety_ratings:
  - "Log collection (collect), search, errors, correlate: GREEN (read-only)"
  - "Cordon/drain node: YELLOW — operator action, not available via MCP tools"
  - "Restart kubelet: YELLOW — operator action, not available via MCP tools"
  - "Terminate and replace node: RED — operator action, requires approval"

## Common Issues

- symptoms: "search for kubelet service status returns Active: inactive or Active: failed, but no OOM evidence in dmesg"
  diagnosis: "Kubelet is stopped but not due to OOM. Could be config error, manual stop, or other failure."
  resolution: "Investigate kubelet config (see B1 SOP). Operator action: check journalctl -u kubelet for startup errors, then restart kubelet."

- symptoms: "errors tool returns findings with oom-killer targeting kubelet PID"
  diagnosis: "Kernel killed kubelet due to memory exhaustion. Use search tool with query=system-reserved to check if memory reservation is configured."
  resolution: "Operator action: restart kubelet, then set --system-reserved=memory=1Gi and --kube-reserved=memory=512Mi in kubelet config"

- symptoms: "search for MemAvailable shows < 100MB but no OOM kill found in errors"
  diagnosis: "Memory pressure without OOM. Kubelet may recover on its own."
  resolution: "Monitor for 5 minutes. If node stays NotReady, operator should drain and investigate memory consumers."

- symptoms: "cluster_health shows multiple nodes NotReady simultaneously"
  diagnosis: "Cluster-wide issue. Possible DaemonSet memory leak or undersized node group."
  resolution: "Escalate immediately. Use compare_nodes to check DaemonSet resource usage across all nodes."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get OOM findings
errors(instanceId="i-0abc123def456", severity="critical")
# Step 4: Deep search for OOM evidence
search(instanceId="i-0abc123def456", query="oom-killer|OOMKilled|out of memory")
# Step 5: Correlate timeline
correlate(instanceId="i-0abc123def456", pivotEvent="oom-killer", timeWindow=120)
# Step 6: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003"])
```

## Output Format

```yaml
root_cause: "Kubelet OOM-killed by kernel"
evidence:
  - type: dmesg_finding
    content: "<OOM kill finding from errors tool with PID and timestamp>"
  - type: memory_search
    content: "<MemAvailable value from search results>"
blast_radius: "node (<node-name>), <N> pods affected"
severity: CRITICAL
mitigation:
  immediate: "Operator: cordon node, drain if pods need rescheduling"
  short_term: "Operator: restart kubelet after memory pressure resolves"
  long_term: "Increase instance type or set system-reserved memory"
```
