---
title: "C2 — Sandbox Creation Failures"
description: "Diagnose pods stuck in ContainerCreating due to sandbox creation failures"
status: active
severity: HIGH
triggers:
  - "failed to create sandbox"
  - "failed to setup network for sandbox"
  - "add cmd: failed to assign an IP address"
  - "failed to create containerd task"
owner: devops-agent
objective: "Identify why container sandboxes cannot be created and restore pod scheduling"
context: "Sandbox creation is the first step in starting a container. Failures here block all pod creation on the node. Root causes include CNI/IP exhaustion, containerd runtime errors, disk/inode exhaustion, or runtime socket unavailability."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Check pod status — pods stuck in ContainerCreating with sandbox errors confirms this SOP
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) for sandbox creation failure details
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- **PREREQUISITE — Is containerd running?** Sandbox creation requires the container runtime:
  - Use `search` tool with instanceId and query=`Active: active \(running\)|containerd.*started|containerd.service.*running` and logTypes=`containerd` — if NO matches, containerd is stopped/dead. That is the root cause, not a sandbox-specific issue.
  - Use `search` tool with instanceId and query=`Active: inactive|Active: failed|containerd.service.*dead|containerd.service.*failed` — if matches found, containerd is stopped. Report "containerd service not running — sandbox creation impossible" as root cause.
  - ONLY if containerd is confirmed running, proceed to sandbox investigation below.
- **PREREQUISITE — Is aws-node (VPC CNI) running?** Most sandbox failures are CNI-related:
  - Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, namespace=kube-system, labelSelector=k8s-app=aws-node — check that aws-node pod on the affected node is Running. If CrashLoopBackOff or missing, that is the root cause. Report "aws-node (VPC CNI) not running on node — sandbox network setup will fail" immediately.
  - ONLY if aws-node is confirmed running, proceed to sandbox investigation below.
- Use `errors` tool with instanceId to get pre-indexed findings — look for sandbox creation errors
- Use `search` tool with instanceId and query=`failed to create sandbox|failed to setup network|failed to assign an IP|failed to create containerd task` to find sandbox failure evidence

SHOULD:
- Use `network_diagnostics` tool with instanceId and sections=cni,ipamd to check CNI/IP allocation status
- Use `storage_diagnostics` tool with instanceId to check disk and inode usage
- Use `search` tool with query=`Resource temporarily unavailable|cannot allocate memory|fork.*failed|too many open files` to check for PID/file limit exhaustion or zombie processes

MAY:
- Use `search` tool with query=`containerd.*socket|runtime not ready` to check containerd health
- Use `cluster_health` tool with clusterName to check if multiple nodes are affected

## Phase 2 — Enrich

MUST:
- Use `search` tool with query=`failed to assign an IP` — CNI/IP exhaustion (cross-ref with D1 SOP)
- Use `search` tool with query=`failed to create containerd task` — runtime or disk issue
- Use `storage_diagnostics` tool to check inode usage — 100% means inode exhaustion (cross-ref with C3 SOP)
- Use `search` tool with query=`runtime not ready|containerd.*unavailable` — containerd socket issue

SHOULD:
- Use `network_diagnostics` tool with sections=cni,ipamd to get IP allocation details
- Use `correlate` tool with instanceId and pivotEvent=`failed to create sandbox` to build failure timeline
- Use `search` tool with query=`DISABLE_TCP_EARLY_DEMUX|POD_SECURITY_GROUP_ENFORCING_MODE|strict.*mode|security.*groups.*pods` to check for Security Groups for Pods strict mode issues — DISABLE_TCP_EARLY_DEMUX must be set to true when POD_SECURITY_GROUP_ENFORCING_MODE is strict
- Use `search` tool with query=`aws-node.*serviceaccount|overwrite|managed.*plugin|Pod Identity|EKS Pod Identity` to check if the aws-node service account was overwritten by a managed CNI plugin update, or if Pod Identity is configured for aws-node (Pod Identity is NOT supported for aws-node — must use IRSA or node role)

MAY:
- Use `compare_nodes` tool to check if sandbox failures are node-specific

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from sandbox-related findings
- State root cause: specific sandbox failure reason with evidence
- Recommend targeted fix (operator action)
- Cross-reference with related SOPs (D1 for IP exhaustion, C3 for inode exhaustion)

SHOULD:
- Include evidence from network_diagnostics or storage_diagnostics

MAY:
- Recommend prefix delegation for IP density
- Recommend larger root volumes

## Guardrails

escalation_conditions:
  - "Containerd socket unresponsive after restart"
  - "All pods on node stuck in ContainerCreating"
  - "IP exhaustion across multiple nodes (check via cluster_health)"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics, storage_diagnostics: GREEN (read-only)"
  - "Restart containerd, clean containers: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "search for containerd service status returns Active: inactive or Active: failed"
  diagnosis: "Containerd is stopped. No containers can be created without the runtime."
  resolution: "Operator action: check journalctl -u containerd for startup errors, then restart containerd (systemctl restart containerd)."

- symptoms: "list_k8s_resources returns aws-node pod in CrashLoopBackOff or missing on the affected node"
  diagnosis: "VPC CNI DaemonSet not running on the node. Sandbox network setup will fail for all new pods."
  resolution: "Operator action: check aws-node logs (kubectl logs -n kube-system -l k8s-app=aws-node). Common fixes: update VPC CNI addon, check IAM permissions, verify subnet has available IPs."

- symptoms: "search returns failed to assign an IP address, network_diagnostics shows IP exhaustion"
  diagnosis: "VPC CNI IP exhaustion. See SOP D1."
  resolution: "Operator action: check subnet IPs, ENI limits. Enable prefix delegation."

- symptoms: "search returns failed to create containerd task, storage_diagnostics shows disk/inode issues"
  diagnosis: "Containerd runtime error, often disk or inode related"
  resolution: "Operator action: clean stopped containers and unused images. Increase root volume."

- symptoms: "storage_diagnostics shows inodes at 100%"
  diagnosis: "Too many small files from container layers or log rotation failures"
  resolution: "Operator action: clean stopped containers and unused images. See SOP C3."

- symptoms: "search returns Resource temporarily unavailable, fork failed, or too many open files"
  diagnosis: "PID limit or file descriptor limit exhausted, or zombie processes consuming PIDs. The node cannot create new processes for sandbox setup."
  resolution: "Operator action: check 'ps aux | wc -l' for process count, check /proc/sys/kernel/pid_max and /proc/sys/fs/file-max. Kill zombie processes. Increase PID limits if needed. Identify the pod consuming excessive PIDs."

- symptoms: "search returns DISABLE_TCP_EARLY_DEMUX error or Security Groups for Pods strict mode failure"
  diagnosis: "When POD_SECURITY_GROUP_ENFORCING_MODE is set to strict, DISABLE_TCP_EARLY_DEMUX must be set to true on the aws-node DaemonSet. Without this, sandbox network setup fails for pods using security groups."
  resolution: "Operator action: set DISABLE_TCP_EARLY_DEMUX=true in the aws-node DaemonSet environment variables. Alternatively, use standard mode instead of strict mode."

- symptoms: "search returns aws-node service account overwrite or Pod Identity errors for aws-node"
  diagnosis: "The aws-node service account annotation was overwritten by a managed CNI addon update, or Pod Identity was configured for aws-node. EKS Pod Identity is NOT supported for the aws-node DaemonSet — it must use IRSA or the node IAM role."
  resolution: "Operator action: if using IRSA, re-annotate the aws-node service account with eks.amazonaws.com/role-arn. If Pod Identity was configured, remove it and switch to IRSA. When updating the VPC CNI addon, use --resolve-conflicts PRESERVE to avoid overwriting the service account annotation."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Get sandbox failure findings
errors(instanceId="i-0abc123def456")
# Step 3: Check CNI/IP status
network_diagnostics(instanceId="i-0abc123def456", sections="cni,ipamd")
# Step 4: Check disk/inodes
storage_diagnostics(instanceId="i-0abc123def456")
```

## Output Format

```yaml
root_cause: "<CNI|runtime|disk|inode> — <specific detail>"
evidence:
  - type: finding
    content: "<sandbox failure finding>"
  - type: diagnostics
    content: "<network or storage diagnostics detail>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix>"
  long_term: "Enable prefix delegation, increase root volume"
```
