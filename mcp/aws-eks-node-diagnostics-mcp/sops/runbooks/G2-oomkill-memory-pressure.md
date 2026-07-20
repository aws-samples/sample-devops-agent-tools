---
title: "G2 — OOMKill / MemoryPressure"
description: "Diagnose containers killed by OOM and nodes with MemoryPressure condition"
status: active
severity: CRITICAL
triggers:
  - "oom-kill:"
  - "Out of memory: Killed process"
  - "Memory cgroup out of memory"
  - "OOMKilled"
  - "memory.available.*eviction"
owner: devops-agent
objective: "Identify OOM kill target and cause, then right-size memory limits"
context: "OOM kills occur at two levels: container-level (cgroup limit exceeded) and node-level (system memory exhausted). Container OOM kills restart the specific container. Node-level OOM kills can affect kubelet and system stability."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — look for MemoryPressure condition
  - Check node details: `kubectl describe node <node>` (via EKS MCP `read_k8s_resource`) — check Conditions for MemoryPressure=True
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for OOMKilled pods (look at RESTARTS count and last state), CrashLoopBackOff, or Evicted pods
  - Check pod details for OOMKilled: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) — look for "OOMKilled" in last termination reason
- Use `collect` tool with instanceId of the affected node to gather node-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to get pre-indexed OOM findings
- Use `search` tool with instanceId and query=`oom-kill|OOMKilled|Out of memory|Memory cgroup out of memory` to find OOM evidence in dmesg and system logs

SHOULD:
- Use `search` tool with query=`MemoryPressure|memory.available|memory pressure` to find memory pressure signals
- Use `search` tool with query=`MemAvailable|MemFree|MemTotal` to check memory utilization at time of kill

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes have MemoryPressure
- Use `compare_nodes` tool with instanceIds of affected + healthy node to diff memory findings

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`oom-kill` to build timeline around the OOM event
- Review findings from `errors` tool — if OOM target is a container process: container exceeded its cgroup memory limit
- If OOM target is kubelet or system process: node-level memory exhaustion — cross-reference with A1 (kubelet OOM)
- Use `search` tool with query=`Killed process.*pid|oom_score_adj` to identify the killed process name and PID

SHOULD:
- Use `search` tool with query=`system-reserved|kube-reserved` to check if memory reservation is configured
- Use `errors` tool with severity=all to check for recurring OOM events (multiple findings)

MAY:
- Use `compare_nodes` tool to compare memory-related findings between affected and healthy nodes

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from OOM-related findings to generate incident summary
- State root cause: container OOM or node-level OOM with process name and PID from findings
- Recommend memory limit adjustment based on root cause
- Operator action — not available via MCP tools: increase container memory limits, set system-reserved, restart affected pods

SHOULD:
- Include dmesg OOM kill line from search results
- Include memory utilization at time of kill from findings

MAY:
- Recommend VPA for automatic right-sizing
- Recommend system-reserved memory settings for node-level protection

## Guardrails

escalation_conditions:
  - "Kubelet process OOM-killed — escalate to A1 SOP"
  - "OOM kills recurring despite memory limit increase"
  - "Node-level memory exhaustion affecting system stability — check via cluster_health"

safety_ratings:
  - "Log collection (collect), search, errors, correlate, compare_nodes: GREEN (read-only)"
  - "Increase container memory limits: YELLOW — operator action, not available via MCP tools"
  - "Set system-reserved in kubelet config: YELLOW — operator action, not available via MCP tools"
  - "Terminate and replace node: RED — operator action, requires approval"

## Common Issues

- symptoms: "errors tool returns findings with OOMKilled for container process"
  diagnosis: "Container memory limit too low for workload. Use search with query=memory limit to check configured limits."
  resolution: "Operator action: increase container memory limit in pod spec"

- symptoms: "search for oom-kill returns system-level OOM targeting kubelet or containerd"
  diagnosis: "Node memory exhausted, system-reserved not configured. Escalate to A1 SOP."
  resolution: "Operator action: set system-reserved in kubelet config, consider larger instance type"

- symptoms: "correlate shows recurring OOM kills every few minutes"
  diagnosis: "Application memory leak — OOM kills recur after container restart."
  resolution: "Operator action: investigate application memory leak, increase limits as temporary measure"

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get OOM findings
errors(instanceId="i-0abc123def456", severity="critical")
# Step 4: Search for OOM evidence
search(instanceId="i-0abc123def456", query="oom-kill|OOMKilled|Out of memory")
# Step 5: Correlate OOM timeline
correlate(instanceId="i-0abc123def456", pivotEvent="oom-kill", timeWindow=120)
# Step 6: Check memory reservation config
search(instanceId="i-0abc123def456", query="system-reserved|kube-reserved")
# Step 7: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003"])
```

## Output Format

```yaml
root_cause: "<container_oom|node_oom> — process <name> PID <pid>"
evidence:
  - type: dmesg_finding
    content: "<OOM kill finding from errors tool>"
  - type: memory_search
    content: "<memory utilization from search results>"
severity: CRITICAL
mitigation:
  immediate: "Operator: increase memory limits for affected container"
  long_term: "Set system-reserved, deploy VPA, right-size instances"
```
