---
title: "E1 — EBS CSI Attach/Mount Timeout"
description: "Diagnose pods stuck in ContainerCreating due to EBS volume attach or mount failures"
status: active
severity: HIGH
triggers:
  - "AttachVolume.Attach failed"
  - "Unable to attach or mount volumes: timed out"
  - "FailedMount"
  - "Multi-Attach error"
owner: devops-agent
objective: "Identify the EBS attach/mount failure reason and restore volume access"
context: "EBS volumes must be attached to the correct instance in the correct AZ before pods can mount them. Failures occur due to AZ mismatch, stale attachments, instance volume limits, or CSI driver issues."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Check pod status — pods stuck in ContainerCreating with volume mount errors confirms this SOP
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) for volume attach/mount timeout details
  - Check PersistentVolumeClaim status: `kubectl get pvc -n <namespace>` (via EKS MCP `list_k8s_resources` kind=PersistentVolumeClaim) — Pending PVC = volume not provisioned
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for volume attach/mount errors
- Use `storage_diagnostics` tool with instanceId to get storage/volume status from collected logs

SHOULD:
- Use `search` tool with instanceId and query=`AttachVolume.*failed|timed out.*volumes|FailedMount|Multi-Attach` to find volume failure evidence
- Use `search` tool with query=`ebs-csi|csi.*controller|csi.*error` to check EBS CSI driver logs

MAY:
- Use `search` tool with query=`lsblk|block device` to check attached block devices
- Use `cluster_health` tool with clusterName to check if volume issues are cluster-wide

## Phase 2 — Enrich

MUST:
- Use `search` tool with query=`timed out.*different.*AZ|availability zone` — AZ mismatch between volume and node
- Use `search` tool with query=`403|not authorized` in CSI logs — IAM permissions issue
- Review `storage_diagnostics` for device count — attachment limit reached
- Use `search` tool with query=`Multi-Attach` — volume still attached to old node

SHOULD:
- Use `correlate` tool with instanceId and pivotEvent=`AttachVolume` to build timeline of attach failures
- Use `search` tool with query=`StorageClass|volumeBindingMode|WaitForFirstConsumer` to check storage class config

MAY:
- Use `compare_nodes` tool to check if volume issues are node-specific

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from volume-related findings
- State root cause: specific attach/mount failure with evidence
- Recommend targeted fix (operator action)
- Confirm pod should transition to Running after fix

SHOULD:
- Include volume ID, AZ, and attachment state from findings
- Warn about data corruption risk for force detach

MAY:
- Recommend WaitForFirstConsumer binding mode
- Recommend Gen7+ instances for higher volume limits

## Guardrails

escalation_conditions:
  - "Volume stuck in attaching state for >10 minutes"
  - "Force detach needed on actively-written volume (data corruption risk)"
  - "CSI driver pods not running on any node"

safety_ratings:
  - "Log collection (collect), search, errors, storage_diagnostics: GREEN (read-only)"
  - "Force detach volume: RED — operator action, data corruption risk, requires approval"

## Common Issues

- symptoms: "search returns AttachVolume timed out, findings show AZ mismatch"
  diagnosis: "Volume in different AZ than target node, or stale attachment"
  resolution: "Operator action: check volume AZ vs node AZ. If stale: force detach (with caution) or wait for GC."

- symptoms: "search returns 403 not authorized in CSI controller findings"
  diagnosis: "EBS CSI controller IAM role missing permissions"
  resolution: "Operator action: add required EBS permissions to CSI controller IAM role"

- symptoms: "search returns Multi-Attach error"
  diagnosis: "Volume still attached to previous node"
  resolution: "Operator action: wait for previous pod termination, or force detach if safe"

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Get storage diagnostics
storage_diagnostics(instanceId="i-0abc123def456")
# Step 3: Get volume-related findings
errors(instanceId="i-0abc123def456")
# Step 4: Search for attach failures
search(instanceId="i-0abc123def456", query="AttachVolume.*failed|Multi-Attach|FailedMount")
```

## Output Format

```yaml
root_cause: "<az_mismatch|stale_attach|iam|volume_limit> — <detail>"
evidence:
  - type: finding
    content: "<attach/mount error finding>"
  - type: storage_diagnostics
    content: "<volume/device status>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix>"
  long_term: "Use WaitForFirstConsumer, Gen7+ instances"
```
