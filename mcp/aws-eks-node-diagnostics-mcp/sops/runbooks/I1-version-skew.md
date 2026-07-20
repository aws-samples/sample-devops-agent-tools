---
title: "I1 — Kubernetes Version Skew"
description: "Diagnose API errors and node registration failures caused by Kubernetes version skew"
status: active
severity: HIGH
triggers:
  - "the server could not find the requested resource"
  - "no matches for kind.*in version"
  - "is deprecated.*removed in"
  - "kubelet version.*skew"
owner: devops-agent
objective: "Identify version skew between control plane, nodes, and addons, then plan upgrade path"
context: "Kubernetes supports N-2 minor version skew between control plane and nodes. Exceeding this causes registration failures, API errors, and feature incompatibilities. Addon versions must also be compatible."
---

## Phase 1 — Triage

FIRST — Check node state and version info before collecting logs:
- Use `list_k8s_resources` with clusterName, kind=Node, apiVersion=v1 to list all nodes — check kubelet version in node status and identify nodes with different versions
- Use `read_k8s_resource` with clusterName, kind=Node, apiVersion=v1, name=<node-name> to get detailed node info — check status.nodeInfo.kubeletVersion, status.nodeInfo.kubeProxyVersion, and node conditions (Ready/NotReady)
- Use `get_k8s_events` with clusterName, kind=Node, name=<node-name> to check for version-related registration failures or API incompatibility events
- Use `describe_eks_resource` with resourceType=cluster, clusterName to get the control plane Kubernetes version for skew comparison

MUST:
- Use `collect` tool with instanceId of the affected node to gather node-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to get pre-indexed version skew findings
- Use `search` tool with instanceId and query=`server could not find the requested resource|no matches for kind|deprecated.*removed|kubelet version.*skew` to find version-related errors

SHOULD:
- Use `cluster_health` tool with clusterName to get cluster version and node version overview
- Use `search` tool with query=`kubeletVersion|kubeProxyVersion|server version` to find version strings in logs
- Use `compare_nodes` tool with instanceIds of multiple nodes to identify version inconsistencies across the fleet

MAY:
- Use `search` tool with query=`deprecated|removed in|apiVersion` to find deprecated API usage in kubelet logs

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`version` to build timeline of version-related failures
- Review findings from `errors` tool and `cluster_health` to calculate version skew:
  - If skew > 2 minor versions: unsupported — nodes must be upgraded
  - If kubelet > API server: unsupported configuration — upgrade control plane first
  - If kube-proxy or CoreDNS incompatible: addon version mismatch
- Use `search` tool with query=`kube-proxy|coredns|aws-node|vpc-cni` to check addon version strings

SHOULD:
- Use `compare_nodes` tool to identify which nodes are on which versions — find the outliers
- Use `search` tool with query=`registration.*fail|register.*error|certificate` to check if version skew is causing registration failures
- Verify upgrade order was followed: control plane → nodes → addons. If nodes were upgraded before control plane, that is the root cause.
- Check addon compatibility: use `search` tool with query=`vpc-cni|coredns|kube-proxy|ebs-csi|efs-csi|aws-load-balancer` to identify all addon versions, then compare against compatible versions for the cluster K8s version

MAY:
- Use `search` tool with query=`apiserver_requested_deprecated_apis|deprecated API` to find deprecated API usage
- Use `search` tool with query=`extended support|extended-support` to check if cluster is on EKS extended support (additional cost, should plan upgrade)
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="deprecated" to check kube-audit logs for deprecated API usage warnings — these indicate workloads using APIs that will be removed in future versions
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="removed" to check for API calls to already-removed endpoints

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from version-related findings to generate incident summary
- State root cause: version skew with specific versions from cluster_health and compare_nodes
- Recommend upgrade path: control plane first, then nodes, then addons
- Operator action — not available via MCP tools: upgrade control plane, update node groups, update addons

SHOULD:
- Include version comparison table from cluster_health and compare_nodes results
- List any deprecated APIs found in search results that need updating before upgrade

MAY:
- Recommend upgrade runbook with pre-flight checks
- Recommend running pluto or kubent to detect deprecated APIs before upgrade

## Guardrails

escalation_conditions:
  - "Version skew > 3 minor versions (requires multi-step upgrade)"
  - "Deprecated APIs used by critical workloads — found via search"
  - "Addon upgrade fails due to compatibility issues"

safety_ratings:
  - "Log collection (collect), search, errors, correlate, cluster_health, compare_nodes: GREEN (read-only)"
  - "Upgrade control plane: YELLOW — operator action, not available via MCP tools"
  - "Update node groups: YELLOW — operator action, not available via MCP tools"
  - "Update addons: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "search returns the server could not find the requested resource"
  diagnosis: "Workload using API version removed in current control plane version. Use search with query=apiVersion to identify."
  resolution: "Operator action: update workload manifests to use current API versions before upgrading"

- symptoms: "errors tool returns findings with kubelet version skew or registration failure"
  diagnosis: "Node kubelet version too old for control plane (>N-2). Use cluster_health to confirm versions."
  resolution: "Operator action: update node group — aws eks update-nodegroup-version --cluster-name <cluster> --nodegroup-name <ng>"

- symptoms: "compare_nodes shows mixed kubelet versions across fleet"
  diagnosis: "Rolling upgrade incomplete — some nodes on old version."
  resolution: "Operator action: complete rolling upgrade of remaining node groups"

- symptoms: "search returns addon incompatible after control plane upgrade"
  diagnosis: "kube-proxy, CoreDNS, or VPC CNI version not compatible with new K8s version."
  resolution: "Operator action: update addons — aws eks update-addon --cluster-name <cluster> --addon-name <addon> --addon-version <version>"

- symptoms: "search returns API errors or node registration failures after control plane upgrade"
  diagnosis: "Upgrade order violated — nodes or addons were not updated after control plane upgrade. Correct order: control plane first, then nodes, then addons (VPC CNI, CoreDNS, kube-proxy, EBS/EFS CSI drivers, AWS Load Balancer Controller)."
  resolution: "Operator action: follow upgrade order — 1) control plane (aws eks update-cluster-version), 2) node groups (aws eks update-nodegroup-version), 3) addons (aws eks update-addon for each). Check addon compatibility first: aws eks describe-addon-versions --addon-name <addon> --kubernetes-version <version>."

- symptoms: "search returns kubelet version higher than control plane version (e.g., kubelet v1.29 on control plane v1.28)"
  diagnosis: "Kubelet version cannot be newer than the control plane. This is an unsupported configuration that causes unpredictable behavior."
  resolution: "Operator action: upgrade control plane first to match or exceed kubelet version. EKS does not support downgrading the control plane."

- symptoms: "cluster_health shows control plane and nodes more than 2 minor versions apart (e.g., control plane v1.30, nodes v1.27)"
  diagnosis: "Version skew exceeds the supported N-2 limit. Nodes on v1.27 cannot communicate reliably with a v1.30 control plane."
  resolution: "Operator action: upgrade nodes incrementally — cannot skip versions. Upgrade node groups to N-2 first, then N-1, then N. Each step requires a rolling update."

- symptoms: "search returns errors after upgrading addons, or addon pods are CrashLoopBackOff after cluster upgrade"
  diagnosis: "Addon version incompatible with the new Kubernetes version. Key addons to check: VPC CNI (aws-node), CoreDNS, kube-proxy, EBS CSI driver, EFS CSI driver, AWS Load Balancer Controller."
  resolution: "Operator action: check compatible versions — aws eks describe-addon-versions --addon-name <addon> --kubernetes-version <version>. Update each addon to a compatible version. For self-managed addons (e.g., AWS Load Balancer Controller), check the compatibility matrix in the addon documentation."

- symptoms: "cluster is on extended support (v1.23 or older) and upgrade is needed"
  diagnosis: "EKS extended support keeps older versions running but at additional cost. Clusters on extended support should be upgraded to standard support versions."
  resolution: "Operator action: plan multi-step upgrade path. Cannot skip minor versions — must upgrade one version at a time (e.g., 1.23 → 1.24 → 1.25 → ... → target). Test each step in a staging cluster first. Check for deprecated APIs at each version boundary using pluto or kubent."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get version skew findings
errors(instanceId="i-0abc123def456", severity="high")
# Step 4: Check cluster version overview
cluster_health(clusterName="my-cluster")
# Step 5: Compare node versions
compare_nodes(instanceIds=["i-0abc123def456","i-0xyz789ghi012"])
# Step 6: Search for version errors
search(instanceId="i-0abc123def456", query="server could not find|no matches for kind|deprecated.*removed")
# Step 7: Correlate version failure timeline
correlate(instanceId="i-0abc123def456", pivotEvent="version", timeWindow=120)
# Step 8: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002"])
```

## Output Format

```yaml
root_cause: "Version skew — control plane <v1> vs nodes <v2>"
evidence:
  - type: cluster_health
    content: "<cluster version and node versions from cluster_health>"
  - type: compare_nodes
    content: "<version differences from compare_nodes>"
severity: HIGH
mitigation:
  immediate: "Operator: update node groups to within N-2 of control plane"
  long_term: "Implement upgrade runbook, use managed node groups for auto AMI updates"
```