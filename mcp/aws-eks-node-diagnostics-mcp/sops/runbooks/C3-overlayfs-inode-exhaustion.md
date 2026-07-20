---
title: "C3 — OverlayFS / Inode Exhaustion"
description: "Diagnose no space left on device errors caused by inode exhaustion"
status: active
severity: HIGH
triggers:
  - "no space left on device"
  - "DiskPressure"
owner: devops-agent
objective: "Confirm inode exhaustion and free inodes to restore pod operations"
context: "Inode exhaustion occurs when the filesystem runs out of inode entries even though disk space is available. Common with many small files from container layers or log rotation failures."
---

## Phase 1 — Triage

FIRST — Check node and pod state before collecting logs:
- Use `list_k8s_resources` with clusterName, kind=Node, apiVersion=v1 to list all nodes and identify the affected node's status (Ready/NotReady) and conditions (DiskPressure, MemoryPressure, PIDPressure)
- Use `read_k8s_resource` with clusterName, kind=Node, apiVersion=v1, name=<node-name> to get detailed node conditions — look for DiskPressure=True which indicates inode or disk exhaustion
- Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, fieldSelector=spec.nodeName=<node-name> to list all pods on the affected node — check for pods in CrashLoopBackOff, Error, or ContainerCreating state (stuck creating due to no space)
- Use `get_k8s_events` with clusterName, kind=Node, name=<node-name> to check for DiskPressure, EvictionThresholdMet, or FreeDiskSpaceFailed events

MUST:
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for disk/inode errors
- Use `storage_diagnostics` tool with instanceId to get disk and inode usage from collected logs

SHOULD:
- Use `search` tool with instanceId and query=`no space left on device|DiskPressure|inode` to find inode exhaustion evidence
- Use `search` tool with query=`garbage collect|image.*prune` to check image GC status

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes have disk pressure

## Phase 2 — Enrich

MUST:
- Review `storage_diagnostics` output for inode utilization — confirm IUse% at or near 100%
- Identify which filesystem is inode-exhausted from storage_diagnostics results
- Use `search` tool with query=`containerd|image.*layer|overlay` to identify inode consumers

SHOULD:
- Use `search` tool with query=`image garbage collection|imageGCHighThreshold` to check if image GC is working
- Use `correlate` tool with instanceId and pivotEvent=`no space left` to build timeline

MAY:
- Use `compare_nodes` tool to compare disk/inode usage across nodes

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from disk-related findings
- State root cause: inode exhaustion with IUse% evidence from storage_diagnostics
- Recommend immediate cleanup actions (operator action)
- Recommend long-term prevention

SHOULD:
- Identify top inode consumers from storage_diagnostics

MAY:
- Recommend XFS filesystem for better inode handling

## Guardrails

escalation_conditions:
  - "Inode cleanup does not free sufficient inodes"
  - "Root filesystem requires resize"
  - "System pods affected by inode exhaustion"

safety_ratings:
  - "Log collection (collect), search, errors, storage_diagnostics: GREEN (read-only)"
  - "Clean containers, prune images: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "storage_diagnostics shows IUse% near 100% but disk space available"
  diagnosis: "Inode exhaustion from too many small files (container layers, logs)"
  resolution: "Operator action: clean stopped containers and prune unused images. Increase root volume."

- symptoms: "storage_diagnostics shows both disk usage >85% AND IUse% near 100%"
  diagnosis: "Both disk space and inodes are exhausted. Container images, logs, and emptyDir volumes are consuming both resources."
  resolution: "Operator action: 1) Prune unused images: crictl rmi --prune. 2) Clean stopped containers. 3) Increase EBS root volume size. 4) Set ephemeral-storage limits on pods to prevent unbounded disk usage."

- symptoms: "search returns 'failed to garbage collect required amount of images' alongside DiskPressure"
  diagnosis: "Kubelet image garbage collection cannot free enough space. All images may be in use by running containers."
  resolution: "Operator action: 1) Lower GC thresholds: --image-gc-high-threshold=70 --image-gc-low-threshold=60. 2) Reduce number of unique images on the node. 3) Use smaller base images. 4) Provision new nodes with larger root volumes."

- symptoms: "search returns 'no space left on device' during container creation but df -h shows disk space available"
  diagnosis: "Inode exhaustion confirmed — filesystem has space but no free inodes. Common with ext4 filesystems that have many small container layer files."
  resolution: "Operator action: clean up container layers and unused images. For long-term fix, consider XFS filesystem (dynamic inode allocation) or increase root volume size (more inodes allocated at mkfs time)."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Check storage
storage_diagnostics(instanceId="i-0abc123def456")
# Step 3: Get disk-related findings
errors(instanceId="i-0abc123def456")
# Step 4: Search for inode evidence
search(instanceId="i-0abc123def456", query="no space left on device|inode")
```

## Output Format

```yaml
root_cause: "Inode exhaustion on <filesystem>"
evidence:
  - type: storage_diagnostics
    content: "IUse% = <value>"
severity: HIGH
mitigation:
  immediate: "Operator: clean stopped containers and unused images"
  long_term: "Increase root volume, configure image GC thresholds"
```
