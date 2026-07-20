---
title: "B2 — Eviction Manager Issues"
description: "Diagnose unexpected pod evictions caused by DiskPressure, MemoryPressure, or PIDPressure"
status: active
severity: HIGH
triggers:
  - "eviction manager: attempting to reclaim"
  - "eviction_signal.*memory.available"
  - "node has conditions.*DiskPressure"
  - "failed to garbage collect required amount of images"
owner: devops-agent
objective: "Identify the eviction trigger (disk, memory, or PID pressure), stop the eviction storm, and restore node stability"
context: "Kubelet eviction manager kills pods when node resources drop below configured thresholds. This can cascade into eviction storms where many pods are killed rapidly, causing service disruption."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — look for MemoryPressure, DiskPressure conditions that trigger evictions
  - Check node details: `kubectl describe node <node>` (via EKS MCP `read_k8s_resource`) — look at Conditions and recent events for eviction activity
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for Evicted pods, pods in Terminating state, or pods being rescheduled
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `quick_triage` tool with instanceId to get combined validate + errors + triage in one call
- Use `search` tool with instanceId and query=`eviction manager|attempting to reclaim|DiskPressure|MemoryPressure|PIDPressure` to find eviction evidence

SHOULD:
- Use `storage_diagnostics` tool with instanceId to check disk and inode usage from collected logs
- Use `search` tool with query=`garbage collect|image.*prune|crictl` to check image GC status

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes are in eviction state
- Use `compare_nodes` tool to compare resource pressure across nodes

## Phase 2 — Enrich

MUST:
- Use `errors` tool with instanceId and severity=critical to identify which eviction signal triggered: DiskPressure, MemoryPressure, or PIDPressure
- For DiskPressure: use `storage_diagnostics` tool with instanceId to get disk utilization details
- For MemoryPressure: use `search` tool with query=`oom-killer|OOMKilled|out of memory` to check for OOM kills
- For PIDPressure: use `search` tool with query=`pid_max|too many processes|fork.*failed` to check PID exhaustion

SHOULD:
- Use `correlate` tool with instanceId and pivotEvent=`eviction` to build timeline of eviction events
- Use `search` tool with query=`eviction.*threshold|hard-eviction|soft-eviction` to check kubelet eviction threshold configuration

MAY:
- Use `compare_nodes` tool to compare resource usage between affected and healthy nodes

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from eviction-related findings
- State root cause: specific pressure type with resource utilization evidence
- Recommend immediate action to stop eviction storm (operator action)
- Recommend long-term fix to prevent recurrence

SHOULD:
- List evicted pods identified from findings
- Include disk/memory/PID utilization evidence from diagnostics tools

MAY:
- Recommend ephemeral-storage limits, image GC thresholds, or PID limits

## Guardrails

escalation_conditions:
  - "Eviction storm affecting critical system pods (kube-proxy, aws-node)"
  - "Multiple nodes in eviction state simultaneously (check via cluster_health)"
  - "Disk usage at 100% and garbage collection unable to free space"

safety_ratings:
  - "Log collection (collect), search, errors, storage_diagnostics: GREEN (read-only)"
  - "Prune images, delete pods: YELLOW — operator action, not available via MCP tools"
  - "Modify eviction thresholds: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "errors tool returns DiskPressure findings, storage_diagnostics shows >85% disk used"
  diagnosis: "Disk full from container images, logs, or emptyDir volumes"
  resolution: "Operator action: prune unused images (crictl rmi --prune), delete evicted pods, identify large emptyDir consumers"

- symptoms: "errors tool returns MemoryPressure findings"
  diagnosis: "Node memory exhausted by pod workloads"
  resolution: "Operator action: identify memory-heavy pods, set system-reserved memory"

- symptoms: "errors tool returns PIDPressure findings"
  diagnosis: "Too many processes/threads on the node"
  resolution: "Operator action: identify runaway process, set PID limits on containers"

- symptoms: "errors tool returns DiskPressure findings, search shows 'Disk usage on image filesystem is over the high threshold, trying to free bytes'"
  diagnosis: "Kubelet image garbage collection is failing to free enough space. The image-gc-high-threshold has been exceeded."
  resolution: "Operator action: 1) Manually prune images: crictl rmi --prune. 2) Lower GC thresholds: set --image-gc-high-threshold=70 --image-gc-low-threshold=60 in kubelet config. 3) Increase EBS volume size or provision new nodes with larger root volumes."

- symptoms: "storage_diagnostics shows root filesystem >85% used, search shows container log files consuming significant space"
  diagnosis: "Container runtime log files (stdout/stderr) are not being rotated properly, consuming disk space."
  resolution: "Operator action: configure containerd log rotation in /etc/containerd/config.toml — set log_file_max (max rotated files) and log_file_max_size (max size per file). Consider using a logging agent to ship logs externally."

- symptoms: "errors tool returns DiskPressure findings, search shows pods without ephemeral-storage limits"
  diagnosis: "Pods without ephemeral-storage limits can consume unlimited disk space on the node, triggering DiskPressure evictions for other pods."
  resolution: "Operator action: set ephemeral-storage requests and limits on all pods. Example: resources.requests.ephemeral-storage=1Mi, resources.limits.ephemeral-storage=2Mi. Use LimitRange to enforce defaults."

## Examples

```
# Step 1: One-shot triage
quick_triage(instanceId="i-0abc123def456")
# Step 2: Search for eviction events
search(instanceId="i-0abc123def456", query="eviction manager|attempting to reclaim")
# Step 3: Check storage if DiskPressure
storage_diagnostics(instanceId="i-0abc123def456")
# Step 4: Correlate eviction timeline
correlate(instanceId="i-0abc123def456", pivotEvent="eviction", timeWindow=300)
```

## Output Format

```yaml
root_cause: "<DiskPressure|MemoryPressure|PIDPressure> — <specific detail>"
evidence:
  - type: finding
    content: "<eviction finding from errors tool>"
  - type: diagnostics
    content: "<resource utilization from storage_diagnostics>"
blast_radius: "node (<node-name>), <N> pods evicted"
severity: HIGH
mitigation:
  immediate: "Operator: <clean up action>"
  long_term: "Set resource limits, monitoring, scaling"
```
