---
title: "K5 — CSI Node Plugin Failures"
description: "Diagnose volume staging/publishing failures caused by CSI node plugin not running, registration failures, or driver crashes visible in worker node kubelet and CSI driver logs"
status: active
severity: HIGH
triggers:
  - "not found in the list of registered CSI drivers"
  - "CSI.*driver.*not.*found"
  - "csi.*node.*not.*running"
  - "NodeStageVolume.*error"
  - "NodePublishVolume.*error"
  - "volume.*staging.*failed"
  - "volume.*publish.*failed"
  - "csi.*registration.*failed"
  - "ebs-csi-node.*CrashLoopBackOff"
  - "efs-csi-node.*CrashLoopBackOff"
owner: devops-agent
objective: "Identify why CSI node plugins are not running or failing on a worker node, causing volume mount failures for pods, and recommend targeted recovery steps"
context: >
  CSI (Container Storage Interface) node plugins run as DaemonSet pods on every worker node. They handle
  the node-local volume operations: staging (attaching the volume device to a global mount point) and
  publishing (bind-mounting into the pod's container). On EKS, the two primary CSI node plugins are:
  (1) ebs-csi-node — part of the EBS CSI driver, handles EBS volume format/mount on the node,
  (2) efs-csi-node — part of the EFS CSI driver, handles NFS mount for EFS volumes.
  When a CSI node plugin is not running on a node, kubelet cannot complete volume operations and pods
  get stuck in ContainerCreating with "not found in the list of registered CSI drivers" errors.
  CSI node plugins register with kubelet via a Unix socket in /var/lib/kubelet/plugins_registry/.
  Registration failures, plugin crashes, or missing DaemonSet pods all cause the same symptom.
  Worker node kubelet logs are essential because they show the CSI registration handshake, volume
  staging/publishing calls, and timeout errors. The CSI node plugin pod logs (collected as container
  logs) show driver-side errors. E1 (EBS attach/mount) and E2 (EFS mount) SOPs cover the volume-level
  failures — this SOP covers the node plugin infrastructure failures that prevent ANY volume operation.
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and CSI driver state before any log collection:
  - Check the stuck pod: `kubectl describe pod <pod>` (via EKS MCP `read_k8s_resource` kind=Pod) — look for "not found in the list of registered CSI drivers" or "NodeStageVolume" / "NodePublishVolume" errors in status
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) — look for FailedMount, FailedAttachVolume events with CSI driver errors
  - Identify the node: check spec.nodeName from the pod spec — all further investigation targets this node
  - Check CSI DaemonSet pods: `kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver` (via EKS MCP `list_k8s_resources` kind=Pod, namespace=kube-system, label_selector="app.kubernetes.io/name=aws-ebs-csi-driver") — check if ebs-csi-node pod is Running on the affected node
  - Check EFS CSI DaemonSet pods: `kubectl get pods -n kube-system -l app=efs-csi-node` (via EKS MCP `list_k8s_resources` kind=Pod, namespace=kube-system, label_selector="app=efs-csi-node") — check if efs-csi-node pod is Running on the affected node
  - Check CSI node pod logs if the pod exists but is not Ready: `kubectl logs <csi-node-pod> -n kube-system -c ebs-plugin` (via EKS MCP `get_pod_logs`)
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready (CSI failures can occur on Ready nodes)
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- **PREREQUISITE — Is the CSI node plugin pod running on this node?**
  - If EKS MCP shows no ebs-csi-node or efs-csi-node pod on the affected node: the DaemonSet is not scheduling to this node. Check taints, tolerations, and node selectors on the DaemonSet. That IS the root cause — no plugin means no volume operations.
  - If the CSI node pod exists but is in CrashLoopBackOff or Error: the plugin is crashing. Check pod logs via EKS MCP `get_pod_logs` for the crash reason. Proceed to Phase 2 section 2B.
  - ONLY if the CSI node pod is Running but volumes still fail, proceed to registration/staging investigation below.
- Use `errors` tool with instanceId to get pre-indexed findings — look for CSI registration and volume staging/publishing errors
- Use `search` tool with instanceId and query=`not found.*registered CSI drivers|CSI.*driver.*not found|csi.*registration` and logTypes=`kubelet` to find CSI registration failures
- Use `storage_diagnostics` tool with instanceId to get storage/volume status including CSI driver state

SHOULD:
- Use `search` tool with query=`NodeStageVolume|NodePublishVolume|NodeUnstageVolume|NodeUnpublishVolume` and logTypes=`kubelet` to find volume operation failures
- Use `search` tool with query=`ebs-csi|efs-csi|csi.*plugin|csi.*socket` to find CSI plugin activity in logs

MAY:
- Use `cluster_health` tool with clusterName to check if CSI failures affect multiple nodes
- Use `search` tool with query=`plugins_registry|registration.*socket|kubelet.*plugin` to check kubelet plugin registration directory status

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`CSI` to build timeline of CSI failures
- Classify the failure type:

### 2A — CSI Node Plugin Not Registered

MUST:
- Use `search` tool with query=`not found in the list of registered CSI drivers|driver name.*not found` and logTypes=`kubelet` to confirm registration failure
  - "ebs.csi.aws.com not found" = EBS CSI node plugin not registered
  - "efs.csi.aws.com not found" = EFS CSI node plugin not registered
- Use `search` tool with query=`registration.*ebs.csi|registration.*efs.csi|RegisterPlugin|plugin.*registered` and logTypes=`kubelet` to check if registration was attempted
  - If no registration attempt: the CSI node pod is not running or the registration socket is missing
  - If registration attempted but failed: check for socket permission errors or version incompatibility

SHOULD:
- Use `search` tool with query=`/var/lib/kubelet/plugins_registry|/var/lib/kubelet/plugins/|csi.sock` to check for socket file references
- Use `search` tool with query=`node-driver-registrar|csi-driver-registrar` to check the sidecar registrar container status

### 2B — CSI Node Plugin Crashing

MUST:
- Check CSI node pod logs via EKS MCP `get_pod_logs` with container_name="ebs-plugin" or "efs-plugin" for crash evidence
- Use `search` tool with query=`ebs-csi-node.*CrashLoopBackOff|efs-csi-node.*CrashLoopBackOff|ebs-csi-node.*Error|efs-csi-node.*Error` to find crash patterns
- Use `search` tool with query=`csi.*panic|csi.*fatal|csi.*segfault|csi.*OOMKilled` to find crash signatures in node logs

SHOULD:
- Use `search` tool with query=`403|AccessDenied|not authorized|UnauthorizedAccess` in CSI logs to check for IAM permission failures
  - EBS CSI controller needs ec2:CreateVolume, ec2:AttachVolume, ec2:DetachVolume permissions
  - EBS CSI node needs no special IAM permissions (it uses the node's instance profile for device operations)
- Use `search` tool with query=`csi.*version|driver.*version|ebs-csi.*version` to check driver version compatibility with the Kubernetes version

### 2C — Volume Staging/Publishing Failures

MUST:
- Use `search` tool with query=`NodeStageVolume.*failed|NodeStageVolume.*error|staging.*target.*path` and logTypes=`kubelet` to find staging failures
  - Staging = attaching the block device and formatting/mounting to a global mount point
  - Common causes: device not found (EBS not attached yet), filesystem corruption, mount point busy
- Use `search` tool with query=`NodePublishVolume.*failed|NodePublishVolume.*error|publish.*target.*path` and logTypes=`kubelet` to find publishing failures
  - Publishing = bind-mounting from the global mount point into the pod's volume directory
  - Common causes: staging not complete, mount propagation issues, SELinux/AppArmor blocking

SHOULD:
- Use `search` tool with query=`mkfs|format.*volume|filesystem.*error|fsck` to check for filesystem formatting errors during staging
- Use `search` tool with query=`mount.*busy|mount.*already|target.*not empty` to check for stale mount points from previous pod

### 2D — Control Plane kube-audit Logs

MAY:
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="VolumeAttachment" to check for VolumeAttachment API objects that may be stuck or failing
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="csi" to check for CSI-related API events

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from CSI-related findings
- State root cause with specific evidence:
  - Plugin not scheduled: DaemonSet not targeting this node (taints/tolerations)
  - Plugin crashing: crash reason from pod logs (OOM, permission, config)
  - Registration failure: socket missing or registration handshake error
  - Staging/publishing failure: specific volume operation error
- Recommend recovery steps (operator action — not available via MCP tools)
- Cross-reference E1 (EBS attach/mount) if the issue is volume-level, not plugin-level

SHOULD:
- Include CSI driver version and Kubernetes version compatibility note
- Include the specific error message from kubelet logs
- Distinguish between controller-side failures (E1/E2 SOPs) and node-side failures (this SOP)

MAY:
- Recommend upgrading CSI driver if version incompatibility detected
- Recommend checking EKS managed add-on status if using managed CSI driver

## Guardrails

escalation_conditions:
  - "CSI node plugin not running on ANY node — DaemonSet completely broken"
  - "CSI node plugin crashes immediately after restart — possible driver bug or incompatibility"
  - "Volume staging fails with filesystem corruption — data integrity risk"
  - "Multiple nodes affected — check via cluster_health"

safety_ratings:
  - "Log collection (collect), search, errors, correlate, storage_diagnostics: GREEN (read-only)"
  - "Restart CSI node pod: YELLOW — operator action, briefly disrupts volume operations on node"
  - "Delete and recreate CSI DaemonSet: YELLOW — operator action, disrupts all volume operations cluster-wide"
  - "Force unmount stale mount points: RED — operator action, data corruption risk"

## Common Issues

- symptoms: "get_k8s_events returns 'not found in the list of registered CSI drivers' for ebs.csi.aws.com"
  diagnosis: "EBS CSI node plugin is not registered with kubelet on this node. Either the ebs-csi-node DaemonSet pod is not running on this node, or the registration sidecar failed."
  resolution: "Operator action: check if ebs-csi-node pod exists on the node (kubectl get pods -n kube-system -o wide | grep ebs-csi-node). If missing, check DaemonSet tolerations match node taints. If present but not Ready, check pod logs. Restart the pod: kubectl delete pod <ebs-csi-node-pod> -n kube-system"

- symptoms: "EBS CSI node pod is in CrashLoopBackOff, pod logs show OOMKilled"
  diagnosis: "CSI node plugin is being OOM-killed. The default memory limit may be too low for nodes with many volumes."
  resolution: "Operator action: increase memory limits on the ebs-csi-node DaemonSet containers. If using EKS managed add-on, update the add-on configuration with higher resource limits."

- symptoms: "search returns 403 or AccessDenied in ebs-csi-controller logs, but ebs-csi-node is running"
  diagnosis: "This is a controller-side IAM issue, not a node plugin issue. The CSI controller cannot call EC2 APIs to create/attach volumes. See E1 SOP for volume-level troubleshooting."
  resolution: "Operator action: verify the ebs-csi-controller-sa service account IAM role has ec2:CreateVolume, ec2:AttachVolume, ec2:DetachVolume permissions. Check OIDC provider configuration."

- symptoms: "search returns NodeStageVolume failed with 'device not found' or 'no such file or directory'"
  diagnosis: "The EBS volume device has not appeared on the node yet. The volume may still be attaching at the EC2 level, or the attach operation failed. This is a volume-level issue (see E1 SOP), not a plugin issue."
  resolution: "Operator action: check EC2 volume attachment state. Wait for attachment to complete. If stuck, see E1 SOP for attach timeout troubleshooting."

- symptoms: "search returns NodePublishVolume failed with 'mount point busy' or 'target not empty'"
  diagnosis: "Stale mount point from a previous pod that was not properly cleaned up. The CSI node plugin cannot bind-mount to a directory that already has content."
  resolution: "Operator action: manually unmount the stale mount point on the node (umount <path>). If the mount is busy, check for processes using the mount (lsof +D <path>). Delete and recreate the pod."

- symptoms: "efs-csi-node pod not running, search returns 'efs.csi.aws.com not found in registered CSI drivers'"
  diagnosis: "EFS CSI driver is not installed or the DaemonSet is not scheduling to this node. EFS CSI driver is not installed by default — it must be added as an EKS add-on or Helm chart."
  resolution: "Operator action: install the EFS CSI driver add-on (aws eks create-addon --cluster-name <cluster> --addon-name aws-efs-csi-driver). If already installed, check DaemonSet tolerations."

- symptoms: "CSI node plugin running but registration fails, search returns 'RegisterPlugin failed' in kubelet logs"
  diagnosis: "Kubelet plugin registration handshake failed. Could be socket permission issue, kubelet plugins directory misconfigured, or CSI driver version incompatible with kubelet version."
  resolution: "Operator action: check kubelet --root-dir configuration matches CSI driver socket path. Verify CSI driver version compatibility with the Kubernetes version. Restart the CSI node pod."

- symptoms: "search returns mkfs or format errors during NodeStageVolume"
  diagnosis: "Volume formatting failed during staging. Could be a corrupted volume, unsupported filesystem type, or device busy from a previous mount."
  resolution: "Operator action: check if the volume was previously formatted with a different filesystem. If corrupted, create a new volume from snapshot. If device busy, check for stale attachments."

## Examples

```
# Step 1: Check stuck pod state via EKS MCP
read_k8s_resource(clusterName="my-cluster", kind="Pod", apiVersion="v1", name="stuck-pod", namespace="default")
get_k8s_events(clusterName="my-cluster", kind="Pod", name="stuck-pod", namespace="default")

# Step 2: Check CSI DaemonSet pods on the affected node
list_k8s_resources(clusterName="my-cluster", kind="Pod", apiVersion="v1", namespace="kube-system", label_selector="app.kubernetes.io/name=aws-ebs-csi-driver")

# Step 3: If CSI node pod exists but not Ready, check its logs
get_pod_logs(clusterName="my-cluster", namespace="kube-system", pod_name="ebs-csi-node-xxxxx", container_name="ebs-plugin")

# Step 4: Collect worker node logs
collect(instanceId="i-0abc123def456")
status(executionId="<id-from-step-4>")

# Step 5: Check CSI registration in kubelet logs
search(instanceId="i-0abc123def456", query="not found.*registered CSI drivers|registration.*ebs.csi|RegisterPlugin", logTypes="kubelet")

# Step 6: Check volume staging/publishing errors
search(instanceId="i-0abc123def456", query="NodeStageVolume|NodePublishVolume|staging.*failed|publish.*failed", logTypes="kubelet")

# Step 7: Check storage diagnostics
storage_diagnostics(instanceId="i-0abc123def456")

# Step 8: Correlate timeline
correlate(instanceId="i-0abc123def456", pivotEvent="CSI", timeWindow=300)

# Step 9: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003"])
```

## Output Format

```yaml
root_cause: "<plugin_not_scheduled|plugin_crashing|registration_failure|staging_failure|publishing_failure|iam_permission> — <specific detail>"
csi_driver: "<ebs.csi.aws.com|efs.csi.aws.com>"
plugin_status: "<running|not_running|crashloopbackoff|not_scheduled>"
evidence:
  - type: pod_state
    content: "<CSI node pod status from EKS MCP>"
  - type: kubelet_logs
    content: "<CSI registration or volume operation errors from search>"
  - type: csi_pod_logs
    content: "<crash or error evidence from CSI node pod logs>"
  - type: storage_diagnostics
    content: "<volume/device status from storage_diagnostics>"
  - type: correlate
    content: "<failure timeline>"
severity: HIGH
mitigation:
  immediate: "Operator: <restart CSI node pod, fix DaemonSet tolerations, or fix IAM permissions>"
  long_term: "Ensure CSI driver version compatible with Kubernetes version, monitor CSI node pod health, use EKS managed add-on for automatic updates"
cross_reference:
  - "E1 if volume attach/mount timeout (controller-side issue)"
  - "E2 if EFS mount failure (controller-side or network issue)"
  - "K4 if containerd crash prevents CSI node pod from running"
```
