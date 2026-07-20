---
title: "G1 — DiskPressure / Eviction Storms"
description: "Diagnose mass pod evictions caused by disk pressure on EKS nodes"
status: active
severity: HIGH
triggers:
  - "eviction manager: attempting to reclaim.*ephemeral-storage"
  - "DiskPressure"
  - "garbage collect.*freed 0 bytes"
  - "imagefs.available"
owner: devops-agent
objective: "Stop the eviction storm, free disk space, and prevent recurrence"
context: "When node disk usage exceeds kubelet eviction thresholds, the eviction manager aggressively kills pods to reclaim space. This can cascade into eviction storms where many pods are killed rapidly."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — look for DiskPressure condition
  - Check node details: `kubectl describe node <node>` (via EKS MCP `read_k8s_resource`) — check Conditions for DiskPressure=True and recent events for eviction activity
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for Evicted pods or pods in Terminating state
- Use `collect` tool with instanceId of the affected node to gather node-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to get pre-indexed disk pressure findings
- Use `search` tool with instanceId and query=`DiskPressure|eviction manager|ephemeral-storage|imagefs.available` to find eviction evidence in kubelet logs
- Use `storage_diagnostics` tool with instanceId and sections=kubelet,instance to check disk utilization and inode usage

SHOULD:
- Use `search` tool with query=`garbage collect|image.*prune|freed.*bytes` to check if image garbage collection is running
- Use `search` tool with query=`emptyDir|ephemeral` to identify pods using local storage

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes have DiskPressure
- Use `compare_nodes` tool with instanceIds to compare disk usage across nodes

## Phase 2 — Enrich

MUST:
- Review findings from `storage_diagnostics` — if root disk >85%: garbage collection not keeping up
- Use `search` tool with query=`eviction.*pod|evicted|Evicted` to identify which pods were evicted and how rapidly
- Use `correlate` tool with instanceId and pivotEvent=`DiskPressure` to build timeline of eviction storm
- If storage_diagnostics shows inode exhaustion: cross-reference with C3 (overlayfs inode exhaustion)

SHOULD:
- Use `search` tool with query=`containerd|image.*pull|layer` to check if large image pulls triggered the disk pressure
- Use `storage_diagnostics` to check kubelet eviction threshold configuration

MAY:
- Use `search` tool with query=`imageGCHighThreshold|imageGCLowThreshold|imageGCHighThresholdPercent|imageGCLowThresholdPercent` to check image GC configuration — default thresholds are imageGCHighThreshold=85% (start GC) and imageGCLowThreshold=80% (stop GC). Lower to 70%/60% for more aggressive cleanup.
- Use `search` tool with query=`containerd.*log|containerd.runc.log|log_file_max|log_file_max_size` to check container runtime log rotation — misconfigured containerd log rotation can fill disk independently of kubelet log management

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from disk pressure findings to generate incident summary
- State root cause: disk pressure with utilization evidence from storage_diagnostics
- List blast radius: number of evicted pods from search results
- Operator action — not available via MCP tools: prune images, clean emptyDir volumes, delete evicted pods, increase EBS root volume

SHOULD:
- Include disk utilization percentages from storage_diagnostics
- Include top disk consumers identified from findings

MAY:
- Recommend ephemeral-storage limits on all pods
- Recommend imageGCHighThresholdPercent tuning

## Guardrails

escalation_conditions:
  - "Disk at 100% and cleanup cannot free space — check via storage_diagnostics"
  - "System pods being evicted (kubelet, containerd)"
  - "Multiple nodes in eviction storm simultaneously — check via cluster_health"

safety_ratings:
  - "Log collection (collect), search, errors, storage_diagnostics, correlate: GREEN (read-only)"
  - "Prune images, clean emptyDir: YELLOW — operator action, not available via MCP tools"
  - "Increase EBS root volume: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "storage_diagnostics shows root disk >85% used, errors tool returns DiskPressure findings"
  diagnosis: "Disk full from images, logs, or emptyDir volumes. Use search with query=containerd to check image storage."
  resolution: "Operator action: run crictl rmi --prune, delete evicted pods, identify large emptyDir consumers"

- symptoms: "search for eviction returns rapid eviction of many pods within minutes"
  diagnosis: "Large deployment caused many image pulls filling disk. Use correlate to confirm timeline."
  resolution: "Operator action: pre-pull images, increase root volume, set ephemeral-storage limits"

- symptoms: "storage_diagnostics shows inode exhaustion (inodes >95%)"
  diagnosis: "Too many small files — see C3 overlayfs inode exhaustion SOP."
  resolution: "Operator action: clean up container layers, increase inode count on volume"

- symptoms: "search returns imageGCHighThreshold or image GC not reclaiming space"
  diagnosis: "Image garbage collection thresholds may be misconfigured. Default: imageGCHighThreshold=85% (start GC), imageGCLowThreshold=80% (stop GC). If thresholds are too high, GC starts too late."
  resolution: "Operator action: lower imageGCHighThresholdPercent to 70 and imageGCLowThresholdPercent to 60 in kubelet config for more aggressive image cleanup."

- symptoms: "search returns containerd.runc.log or container runtime logs consuming disk"
  diagnosis: "Container runtime log rotation is misconfigured. containerd writes to /var/log/pods/ and /var/log/containers/. The containerd config (log_file_max, log_file_max_size) controls runtime-level log rotation separately from kubelet's containerLogMaxSize."
  resolution: "Operator action: configure containerd log rotation in /etc/containerd/config.toml — set log_file_max (number of rotated files) and log_file_max_size (max size per file, e.g., 10MB). Also verify kubelet containerLogMaxSize and containerLogMaxFiles settings. Restart containerd after config changes."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get disk pressure findings
errors(instanceId="i-0abc123def456", severity="high")
# Step 4: Check disk and storage health
storage_diagnostics(instanceId="i-0abc123def456", sections="kubelet,instance")
# Step 5: Search for eviction evidence
search(instanceId="i-0abc123def456", query="DiskPressure|eviction manager|ephemeral-storage")
# Step 6: Correlate eviction timeline
correlate(instanceId="i-0abc123def456", pivotEvent="DiskPressure", timeWindow=120)
# Step 7: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003"])
```

## Output Format

```yaml
root_cause: "DiskPressure — <disk_full|inode_exhaustion|image_bloat>"
evidence:
  - type: storage_diagnostics
    content: "<disk utilization from storage_diagnostics>"
  - type: eviction_finding
    content: "<eviction manager findings from errors tool>"
severity: HIGH
mitigation:
  immediate: "Operator: prune images, clean emptyDir, delete evicted pods"
  long_term: "Increase EBS root volume, set ephemeral-storage limits, tune image GC"
```
