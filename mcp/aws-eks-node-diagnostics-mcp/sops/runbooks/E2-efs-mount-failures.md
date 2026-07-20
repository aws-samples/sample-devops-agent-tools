---
title: "E2 — EFS Mount Failures"
description: "Diagnose EFS mount failures including access denied, timeouts, and CSI driver issues"
status: active
severity: HIGH
triggers:
  - "access denied by server while mounting"
  - "Connection timed out"
  - "efs.csi.aws.com not found"
  - "mount.nfs4: No such device"
owner: devops-agent
objective: "Identify the EFS mount failure reason and restore volume access"
context: "EFS mounts fail due to missing IAM mount options, security group blocking NFS (TCP 2049), missing mount targets in the node AZ, or EFS CSI driver not installed."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Check pod status — pods stuck in ContainerCreating with mount errors confirms this SOP
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) for EFS/NFS mount failure details
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for EFS/NFS mount errors
- Use `storage_diagnostics` tool with instanceId and sections=efs_csi to get EFS CSI driver status

SHOULD:
- Use `search` tool with instanceId and query=`access denied.*mounting|Connection timed out.*nfs|efs.csi.aws.com not found|mount.nfs4` to find EFS mount failure evidence
- Use `network_diagnostics` tool with instanceId and sections=iptables to check for rules blocking NFS (TCP 2049)

MAY:
- Use `search` tool with query=`efs-csi|efs.*driver|efs.*DaemonSet` to check EFS CSI driver status
- Use `cluster_health` tool with clusterName to check if EFS failures are cluster-wide

## Phase 2 — Enrich

MUST:
- Use `search` tool with query=`access denied` — EFS policy uses IAM conditions, need "iam" mount option in PV
- Use `search` tool with query=`not found.*registered CSI drivers` — EFS CSI DaemonSet not running on node
- Use `search` tool with query=`timed out.*nfs|Connection timed out` — SG blocking TCP 2049 or no mount target in AZ

SHOULD:
- Use `correlate` tool with instanceId and pivotEvent=`mount` to build timeline of mount failures
- Use `search` tool with query=`mountOptions|iam.*tls` to check PV mount options

MAY:
- Use `search` tool with query=`Bottlerocket|nfs.*module` to check for Bottlerocket NFS compatibility

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from EFS-related findings
- State root cause: specific mount failure with evidence
- Recommend targeted fix (operator action)
- Confirm mount should succeed after fix

SHOULD:
- Include the specific error from findings

MAY:
- Recommend EFS CSI driver over kernel NFS for Bottlerocket

## Guardrails

escalation_conditions:
  - "EFS file system unreachable from all nodes"
  - "EFS CSI driver cannot be installed (node compatibility issue)"
  - "Security group changes require approval"

safety_ratings:
  - "Log collection (collect), search, errors, storage_diagnostics, network_diagnostics: GREEN (read-only)"
  - "Modify PV spec, security groups: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "search returns access denied by server while mounting 127.0.0.1"
  diagnosis: "EFS file system policy requires IAM auth but PV missing iam mount option"
  resolution: "Operator action: add mountOptions: [iam, tls] to PV spec"

- symptoms: "search returns efs.csi.aws.com not found in registered CSI drivers"
  diagnosis: "EFS CSI DaemonSet not running on the node"
  resolution: "Operator action: install or restart EFS CSI driver"

- symptoms: "search returns Connection timed out for NFS"
  diagnosis: "Security group blocking TCP 2049 or no mount target in node AZ"
  resolution: "Operator action: update SG to allow TCP 2049, verify mount target exists in AZ"

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Get storage diagnostics
storage_diagnostics(instanceId="i-0abc123def456", sections="efs_csi")
# Step 3: Get EFS-related findings
errors(instanceId="i-0abc123def456")
# Step 4: Search for mount failures
search(instanceId="i-0abc123def456", query="access denied.*mounting|efs.csi.aws.com not found|timed out.*nfs")
```

## Output Format

```yaml
root_cause: "<iam_auth|csi_driver|network|mount_target> — <detail>"
evidence:
  - type: finding
    content: "<mount failure finding>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix>"
  long_term: "Use EFS CSI driver with IAM mount options"
```
