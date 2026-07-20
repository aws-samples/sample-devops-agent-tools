---
title: "G3 — PIDPressure"
description: "Diagnose PIDPressure condition caused by process/thread exhaustion"
status: active
severity: HIGH
triggers:
  - "PIDPressure"
  - "pid.available"
  - "unable to create new.*thread"
owner: devops-agent
objective: "Identify the runaway process consuming PIDs and restore PID availability"
context: "PIDPressure occurs when the node runs out of process IDs. This prevents new containers from starting and can affect system stability. Common causes include thread-leaking applications or kernel.pid_max set too low."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — look for PIDPressure condition
  - Check node details: `kubectl describe node <node>` (via EKS MCP `read_k8s_resource`) — check Conditions for PIDPressure=True
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for pods in CrashLoopBackOff or Error state (PID exhaustion causes fork failures)
- Use `collect` tool with instanceId of the affected node to gather node-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to get pre-indexed PID pressure findings
- Use `search` tool with instanceId and query=`PIDPressure|pid.available|unable to create.*thread|cannot allocate memory` to find PID exhaustion evidence

SHOULD:
- Use `search` tool with query=`kernel.pid_max|kernel.threads-max|pid_max` to check kernel PID limits
- Use `search` tool with query=`kubelet.*PIDPressure|node condition` to check if PIDPressure condition is set on the node

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes have PIDPressure
- Use `compare_nodes` tool with instanceIds to compare PID-related findings across nodes

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`PIDPressure` to build timeline of PID exhaustion
- Review findings from `errors` tool — identify the process with the most threads from findings
- Use `search` tool with query=`threads|nlwp|NLWP|clone|fork` to find thread creation patterns
- Compare current PID count against kernel.pid_max from search results

SHOULD:
- Use `search` tool with query=`java|python|node|go` combined with `thread` to identify if a known application type is leaking threads
- Use `errors` tool with severity=all to check if PID pressure is recurring

MAY:
- Use `search` tool with query=`pids.max|pids.current` to check container-level PID limits

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from PID-related findings to generate incident summary
- State root cause: PID exhaustion with offending process name and thread count from findings
- Recommend killing runaway process and setting PID limits
- Operator action — not available via MCP tools: kill runaway process, set container PID limits, increase kernel.pid_max

SHOULD:
- Include process name and thread count from findings
- Include kernel.pid_max value from search results

MAY:
- Recommend container PID limits via kubelet config (--pod-max-pids)

## Guardrails

escalation_conditions:
  - "Runaway process is a critical system component (kubelet, containerd)"
  - "PID exhaustion affecting kubelet or containerd — check via errors tool"
  - "kernel.pid_max already at maximum safe value"

safety_ratings:
  - "Log collection (collect), search, errors, correlate, cluster_health: GREEN (read-only)"
  - "Kill runaway process: YELLOW — operator action, not available via MCP tools"
  - "Increase kernel.pid_max: YELLOW — operator action, not available via MCP tools"
  - "Set container PID limits: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "errors tool returns PIDPressure findings, search shows thousands of threads from one process"
  diagnosis: "Application thread leak consuming all PIDs. Use search with query=threads to identify the process."
  resolution: "Operator action: kill runaway process, set PID limits on containers, fix application thread leak"

- symptoms: "search for pid_max shows low value (e.g., 32768) with high workload density"
  diagnosis: "kernel.pid_max set too low for workload density."
  resolution: "Operator action: increase kernel.pid_max via sysctl. Set PID limits per container."

- symptoms: "correlate shows PIDPressure recurring after process restart"
  diagnosis: "Application has a persistent thread leak — killing the process is only a temporary fix."
  resolution: "Operator action: fix application thread leak, set container PID limits as safety net"

- symptoms: "search for pids.max shows container-level PID limit set but process still exhausts node PIDs"
  diagnosis: "Container PID limit (pids.max in cgroup) may be set too high or not set at all. Without container-level limits, a single pod can exhaust all node PIDs."
  resolution: "Operator action: set --pod-max-pids in kubelet config to limit PIDs per pod. Default is -1 (unlimited). Recommended: set to 1024-4096 depending on workload."

- symptoms: "search returns 'fork: retry: Resource temporarily unavailable' or 'cannot allocate memory' alongside PIDPressure"
  diagnosis: "PID exhaustion is preventing new process creation. This affects all containers on the node including system pods."
  resolution: "Operator action: 1) Identify the runaway process (check /proc/*/status for highest Threads count). 2) Kill the process. 3) Set kernel.pid_max higher if needed (sysctl -w kernel.pid_max=65536). 4) Set --pod-max-pids in kubelet config."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get PID pressure findings
errors(instanceId="i-0abc123def456", severity="high")
# Step 4: Search for PID exhaustion evidence
search(instanceId="i-0abc123def456", query="PIDPressure|pid.available|unable to create.*thread")
# Step 5: Check kernel PID limits
search(instanceId="i-0abc123def456", query="kernel.pid_max|kernel.threads-max")
# Step 6: Correlate PID pressure timeline
correlate(instanceId="i-0abc123def456", pivotEvent="PIDPressure", timeWindow=120)
# Step 7: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002"])
```

## Output Format

```yaml
root_cause: "PID exhaustion — <process_name> using <N> threads"
evidence:
  - type: pid_finding
    content: "<PID pressure finding from errors tool>"
  - type: kernel_config
    content: "kernel.pid_max=<value> from search results"
severity: HIGH
mitigation:
  immediate: "Operator: kill runaway process, increase kernel.pid_max"
  long_term: "Set PID limits on containers, fix application thread leaks"
```
