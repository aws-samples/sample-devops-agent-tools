---
title: "J2 — EBS Transient Attach/Detach Failures"
description: "Diagnose pods stuck due to EBS volume attach timeouts, stale attachments, or multi-attach errors"
status: active
severity: HIGH
triggers:
  - "AttachVolume.*timed out"
  - "Multi-Attach error"
  - "volume is already.*attached"
  - "FailedAttachVolume"
  - "WaitForAttach.*timeout"
owner: devops-agent
objective: "Identify the EBS attach failure reason and restore volume access"
context: "EBS volumes can get stuck in attaching state, remain attached to terminated nodes, or fail due to AZ mismatch. Multi-attach errors occur when a volume is still attached to a previous node."
---

## Phase 1 — Triage

FIRST — Check pod and node state before collecting logs:
- Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, namespace=<namespace> to list pods — check for pods stuck in ContainerCreating or Pending state (indicates volume attach failure)
- Use `read_k8s_resource` with clusterName, kind=Pod, apiVersion=v1, namespace=<namespace>, name=<pod-name> to get detailed pod status — check conditions for PodScheduled, volumes section for PVC references, and container status
- Use `get_k8s_events` with clusterName, kind=Pod, namespace=<namespace>, name=<pod-name> to check for FailedAttachVolume, FailedMount, Multi-Attach, or WaitForAttach timeout events
- Use `read_k8s_resource` with clusterName, kind=PersistentVolumeClaim, apiVersion=v1, namespace=<namespace>, name=<pvc-name> to check PVC status (Bound/Pending) and the associated PV
- Use `list_k8s_resources` with clusterName, kind=Node, apiVersion=v1 to check which node the pod is scheduled on and its AZ label (topology.kubernetes.io/zone)

MUST:
- **PREREQUISITE — Is EBS CSI driver installed?** Before investigating attach failures, verify the driver exists:
  - Use `list_k8s_resources` with clusterName, kind=DaemonSet, apiVersion=apps/v1, namespace=kube-system, labelSelector=app.kubernetes.io/name=aws-ebs-csi-driver — if NO DaemonSet found, the EBS CSI driver is NOT installed. That is the root cause. Report "EBS CSI driver not installed" immediately.
  - Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, namespace=kube-system, labelSelector=app.kubernetes.io/name=aws-ebs-csi-driver — check that ebs-csi-controller and ebs-csi-node pods exist and are Running. If pods are missing or CrashLooping, that is the root cause.
  - Use `describe_eks_resource` with clusterName, resourceType=addon, resourceName=aws-ebs-csi-driver — if addon not found, the driver was never installed as an EKS addon. Report this before investigating IAM or volume state.
  - ONLY if CSI driver is confirmed installed and running, proceed to attach failure investigation below.
- **PREREQUISITE — Does the EBS volume exist?** Before investigating attach mechanics, verify the volume is real:
  - Use `search` tool with instanceId and query=`InvalidVolume.NotFound|volume not found|vol-.*does not exist|VolumeNotFound` — if matches found, the EBS volume referenced by the PV/PVC has been deleted or never existed. That is the root cause. Report "EBS volume does not exist" immediately.
  - Use `get_k8s_events` with clusterName, kind=PersistentVolumeClaim, namespace=<namespace>, name=<pvc-name> — check for events containing "volume not found" or "InvalidVolume".
  - ONLY if volume existence is confirmed (no NotFound errors), proceed to attach failure investigation below.
- Use `collect` tool with instanceId of the affected node to gather node-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to get pre-indexed EBS attach/detach findings
- Use `search` tool with instanceId and query=`AttachVolume.*timed out|Multi-Attach error|volume is already.*attached|FailedAttachVolume|WaitForAttach.*timeout` to find EBS attach failure evidence
- Use `storage_diagnostics` tool with instanceId and sections=ebs_csi,pv_pvc to check EBS CSI driver status and PV/PVC state

SHOULD:
- Use `search` tool with query=`ebs-csi|csi-driver|csi-node` to check CSI driver pod health
- Use `search` tool with query=`VolumeAttachment|volume attachment|attach.*vol-` to find volume attachment details
- Use `search` tool with query=`ebs-csi.*version|csi-driver.*image|ebs-csi-controller` to check EBS CSI driver version — version must be compatible with the cluster Kubernetes version
- Use `search` tool with query=`eks.amazonaws.com/role-arn|serviceaccount.*annotation|IRSA|oidc` to check if the CSI controller service account has the IAM role annotation for IRSA — missing annotation means the CSI controller cannot call EC2 APIs

MAY:
- Use `cluster_health` tool with clusterName to check if EBS CSI driver is healthy cluster-wide
- Use `search` tool with query=`throttl|API.*rate|TooManyRequests` to check for AWS API throttling on attach/detach calls
- Use `search` tool with query=`DeadlineExceeded|context deadline exceeded|timeout.*provision|timeout.*attach` to check if CSI controller cannot reach EC2 API — DeadlineExceeded on provisioning means the controller pod has no network path to the EC2 API endpoint

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`AttachVolume` to build timeline of attach failures
- Review findings from `errors` tool and `storage_diagnostics` to classify the failure:
  - If volume "in-use" but attached to different node: stale attachment from previous pod
  - If volume AZ != node AZ: AZ mismatch — volume cannot cross AZs
  - If max volumes per instance reached: instance volume limit hit
  - If ebs-csi-node pod not running: CSI driver issue
- Use `search` tool with query=`vol-.*state|volume.*status|in-use|available|attaching` to find volume state details

SHOULD:
- Use `search` tool with query=`terminated|terminating|previous pod|graceful` to check if previous pod fully terminated
- Use `search` tool with query=`WaitForFirstConsumer|volumeBindingMode|StorageClass` to check StorageClass configuration
- Use `search` tool with query=`ebs-plugin|csi-provisioner|csi-attacher` to check CSI sidecar container logs for provisioning/attach errors
- Use `search` tool with query=`volume node affinity conflict|FailedScheduling.*affinity|topology.*zone` to check for AZ mismatch between pod scheduling and PV node affinity — StatefulSets with EBS volumes must use volumeBindingMode: WaitForFirstConsumer to ensure the volume is provisioned in the same AZ as the pod

MAY:
- Use `compare_nodes` tool to check if EBS attach issues affect specific nodes or are cluster-wide

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from EBS-related findings to generate incident summary
- State root cause: specific attach failure with volume and node details from findings and storage_diagnostics
- Recommend fix based on root cause classification
- Operator action — not available via MCP tools: force detach volume (with data corruption warning), reschedule pod, fix CSI driver

SHOULD:
- Include volume ID, state, and attachment details from findings
- Warn about data corruption risk for force detach operations

MAY:
- Recommend WaitForFirstConsumer binding mode for topology-aware provisioning

## Guardrails

escalation_conditions:
  - "Force detach needed on actively-written volume (data corruption risk)"
  - "Volume stuck in attaching state for >10 minutes — check via storage_diagnostics"
  - "CSI driver pods not running on any node — check via cluster_health"

safety_ratings:
  - "Log collection (collect), search, errors, storage_diagnostics, correlate: GREEN (read-only)"
  - "Force detach volume: RED — operator action, data corruption risk, requires approval"
  - "Reschedule pod to correct AZ: YELLOW — operator action, not available via MCP tools"
  - "Restart CSI driver pods: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "list_k8s_resources returns no DaemonSet or pods for aws-ebs-csi-driver in kube-system"
  diagnosis: "EBS CSI driver is not installed. Without the driver, no EBS volumes can be attached to pods."
  resolution: "Operator action: install EBS CSI driver as EKS addon — aws eks create-addon --cluster-name <cluster> --addon-name aws-ebs-csi-driver --service-account-role-arn <role-arn>"

- symptoms: "search returns InvalidVolume.NotFound or volume not found errors"
  diagnosis: "The EBS volume referenced by the PersistentVolume has been deleted or never existed."
  resolution: "Operator action: delete the PV and PVC, then create a new PVC to provision a fresh volume. If data recovery is needed, check EBS snapshots."

- symptoms: "errors tool returns findings with AttachVolume timed out, storage_diagnostics shows volume attached to different node"
  diagnosis: "Stale attachment from previous pod that did not fully terminate. Use search with query=terminated to check."
  resolution: "Operator action: wait for GC, or force detach — aws ec2 detach-volume --volume-id <vol-id> --force (data corruption risk)"

- symptoms: "search returns volume AZ does not match node AZ"
  diagnosis: "EBS volumes cannot cross AZs. Use storage_diagnostics to confirm AZ mismatch."
  resolution: "Operator action: reschedule pod to correct AZ or create new volume in target AZ"

- symptoms: "errors tool returns FailedAttachVolume with max volumes reached"
  diagnosis: "Instance volume attachment limit hit. Use storage_diagnostics to confirm volume count."
  resolution: "Operator action: move pods to nodes with available volume slots"

- symptoms: "storage_diagnostics shows ebs-csi-node pod not running"
  diagnosis: "CSI driver issue — ebs-csi-node DaemonSet pod not healthy."
  resolution: "Operator action: restart ebs-csi-node pod, check CSI driver DaemonSet status"

- symptoms: "search returns CSI controller service account missing eks.amazonaws.com/role-arn annotation"
  diagnosis: "The ebs-csi-controller-sa service account does not have the IAM role annotation for IRSA. Without this, the CSI controller cannot call EC2 APIs to attach/detach/provision volumes."
  resolution: "Operator action: annotate the service account — kubectl annotate serviceaccount ebs-csi-controller-sa -n kube-system eks.amazonaws.com/role-arn=<role-arn>. Verify the OIDC provider is configured for the cluster. Restart the ebs-csi-controller pods after annotation."

- symptoms: "search returns DeadlineExceeded or context deadline exceeded on volume provisioning"
  diagnosis: "CSI controller pod cannot reach the EC2 API endpoint. This happens when the controller pod is in a private subnet without NAT gateway or VPC endpoints for EC2."
  resolution: "Operator action: ensure the CSI controller pods can reach the EC2 API — add NAT gateway to the subnet route table, or create a VPC endpoint for com.amazonaws.<region>.ec2. Check security groups allow outbound HTTPS (443)."

- symptoms: "search returns FailedScheduling volume node affinity conflict"
  diagnosis: "The PersistentVolume has a node affinity constraint for a specific AZ, but the pod is being scheduled in a different AZ. EBS volumes cannot cross AZs. This commonly happens with StatefulSets when volumeBindingMode is Immediate instead of WaitForFirstConsumer."
  resolution: "Operator action: use StorageClass with volumeBindingMode: WaitForFirstConsumer to ensure volumes are provisioned in the same AZ as the pod. For existing volumes, either reschedule the pod to the correct AZ or create a new volume from a snapshot in the target AZ."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get EBS attach findings
errors(instanceId="i-0abc123def456", severity="high")
# Step 4: Check EBS CSI and PV/PVC status
storage_diagnostics(instanceId="i-0abc123def456", sections="ebs_csi,pv_pvc")
# Step 5: Search for attach failure evidence
search(instanceId="i-0abc123def456", query="AttachVolume.*timed out|Multi-Attach error|FailedAttachVolume")
# Step 6: Correlate attach failure timeline
correlate(instanceId="i-0abc123def456", pivotEvent="AttachVolume", timeWindow=120)
# Step 7: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002"])
```

## Output Format

```yaml
root_cause: "<stale_attach|az_mismatch|volume_limit|csi_driver> — <detail>"
evidence:
  - type: storage_diagnostics
    content: "<EBS volume state and attachment from storage_diagnostics>"
  - type: attach_finding
    content: "<attach failure finding from errors tool>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix based on root cause>"
  long_term: "Use WaitForFirstConsumer, topology-aware provisioning"
```