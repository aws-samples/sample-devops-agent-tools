---
title: "D1 — VPC CNI IP Allocation Failures"
description: "Diagnose pods stuck in ContainerCreating due to VPC CNI IP address exhaustion"
status: active
severity: CRITICAL
triggers:
  - "failed to assign an IP address to container"
  - "no available IP addresses"
  - "failed to allocate ENI"
  - "InsufficientFreeAddresses"
  - "ipamd.*failed to increase IP pool"
owner: devops-agent
objective: "Identify the IP allocation bottleneck and restore pod networking"
context: "VPC CNI assigns real VPC IP addresses to pods. When IPs are exhausted (subnet depletion, ENI limits, or ipamd issues), new pods cannot get network interfaces and remain stuck in ContainerCreating."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Check pod status — pods stuck in ContainerCreating without an IP address confirms IP allocation failure
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) for "failed to assign an IP address" or CNI errors
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready
  - Check aws-node (VPC CNI) pods: `kubectl get pods -n kube-system -l k8s-app=aws-node` — if CNI is not Running, IP allocation is broken
- **PREREQUISITE — Is aws-node (VPC CNI) running?** Before investigating IP allocation, verify the CNI DaemonSet is alive:
  - Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, namespace=kube-system, labelSelector=k8s-app=aws-node — check that aws-node pod on the affected node is Running.
  - If aws-node pod is CrashLoopBackOff, Error, or missing: that is the root cause. Report "aws-node (VPC CNI) not running on node — IP allocation impossible" immediately.
  - Use `describe_eks_resource` with clusterName, resourceType=addon, resourceName=vpc-cni — if addon not found or degraded, report this.
  - ONLY if aws-node is confirmed running, proceed to IP allocation investigation below.
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for IP allocation errors
- Use `network_diagnostics` tool with instanceId and sections=cni,ipamd,eni to get CNI/ipamd/ENI status from collected logs

SHOULD:
- Use `search` tool with instanceId and query=`failed to assign an IP|no available IP|InsufficientFreeAddresses|failed to allocate ENI` to find IP allocation failure evidence
- Use `search` tool with query=`WARM_IP_TARGET|MINIMUM_IP_TARGET|ENABLE_PREFIX_DELEGATION|WARM_PREFIX_TARGET` to check ipamd environment settings
- Use `search` tool with query=`vpc-cni.*version|VPC_CNI_VERSION|aws-node.*image|602401143452.*amazon-k8s-cni` to check VPC CNI version — version must be compatible with the cluster Kubernetes version. Incompatible versions cause ipamd failures.

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes have IP exhaustion
- Use `compare_nodes` tool to compare IP allocation findings across nodes

## Phase 2 — Enrich

MUST:
- Review `network_diagnostics` cni/ipamd sections for ENI and IP utilization
- If all ENIs at max IPs (from network_diagnostics): instance ENI/IP limit reached — need larger instance or prefix delegation
- If ipamd shows many IPs in cooldown: high churn + low warm targets — increase WARM_IP_TARGET
- Use `search` tool with query=`ipamd.*not running|aws-node.*CrashLoop` to check if ipamd pod is healthy
- Use `search` tool with query=`kube-proxy.*not running|kube-proxy.*CrashLoop|kube-proxy.*error` to check kube-proxy health — kube-proxy must be running for aws-node to reach Ready state. If kube-proxy is down, aws-node cannot allocate IPs.

SHOULD:
- Use `correlate` tool with instanceId and pivotEvent=`failed to assign` to build timeline of IP allocation failures
- Calculate IP utilization from network_diagnostics: assigned IPs / max IPs for instance type
- Use `search` tool with query=`InsufficientCidrBlocks|InsufficientCIDR|prefix.*delegation.*failed|/28.*failed` to check for prefix delegation failures due to fragmented subnets — subnets need contiguous /28 blocks for prefix delegation
- Use `search` tool with query=`ENABLE_SUBNET_DISCOVERY|subnet.*discovery|secondary.*subnet` to check if enhanced subnet discovery is enabled — this allows the CNI to use additional subnets tagged with kubernetes.io/role/cni
- Use `search` tool with query=`configurationConflicts|addon.*conflict|resolve.*conflicts` to check for VPC CNI addon configuration conflicts — use --resolve-conflicts OVERWRITE when updating the addon if custom config was applied

MAY:
- Use `search` tool with query=`prefix delegation|ENABLE_PREFIX_DELEGATION` to check if prefix delegation is already enabled

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from IP-allocation-related findings
- State root cause: specific IP allocation bottleneck with evidence from network_diagnostics
- Recommend targeted fix based on bottleneck type (operator action)
- Confirm new pods should get IPs after fix

SHOULD:
- Include ENI/IP utilization numbers from network_diagnostics
- Note subnet available IP count if visible in findings

MAY:
- Recommend prefix delegation for 16x IP density
- Recommend cni-metrics-helper for monitoring

## Guardrails

escalation_conditions:
  - "Subnet completely exhausted (0 available IPs)"
  - "ipamd pod CrashLooping on multiple nodes"
  - "IP allocation failures across all nodes in cluster (check via cluster_health)"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics: GREEN (read-only)"
  - "Modify CNI settings, scale subnets: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "list_k8s_resources returns aws-node pod in CrashLoopBackOff, Error, or missing on the affected node"
  diagnosis: "VPC CNI DaemonSet not running. No IP allocation can occur without aws-node."
  resolution: "Operator action: check aws-node logs (kubectl logs -n kube-system -l k8s-app=aws-node). Common fixes: update VPC CNI addon, check IAM permissions (AmazonEKS_CNI_Policy), verify subnet has available IPs."

- symptoms: "network_diagnostics shows all ENIs at max IPs"
  diagnosis: "Instance type ENI/IP limit reached"
  resolution: "Operator action: enable prefix delegation (ENABLE_PREFIX_DELEGATION=true on aws-node DaemonSet) for 16x density, or scale to larger instance type."

- symptoms: "network_diagnostics shows many IPs in cooldown"
  diagnosis: "High pod churn with low warm IP targets"
  resolution: "Operator action: increase WARM_IP_TARGET or MINIMUM_IP_TARGET in aws-node environment."

- symptoms: "search returns ipamd not running or aws-node CrashLooping"
  diagnosis: "aws-node DaemonSet issue or missing AmazonEKS_CNI_Policy"
  resolution: "Operator action: check aws-node DaemonSet status, verify node IAM role has AmazonEKS_CNI_Policy."

- symptoms: "search returns VPC CNI version incompatible or aws-node image version mismatch"
  diagnosis: "VPC CNI version is not compatible with the cluster Kubernetes version. Each CNI version supports specific K8s versions."
  resolution: "Operator action: update VPC CNI addon to a version compatible with the cluster version — aws eks update-addon --cluster-name <name> --addon-name vpc-cni --addon-version <compatible-version>. Check compatibility matrix in AWS docs."

- symptoms: "search returns kube-proxy not running or kube-proxy CrashLoopBackOff"
  diagnosis: "kube-proxy must be running for aws-node to reach Ready state. Without kube-proxy, aws-node cannot communicate with the API server properly and IP allocation fails."
  resolution: "Operator action: fix kube-proxy first (see D8 SOP), then aws-node will recover. Check kube-proxy DaemonSet status and logs."

- symptoms: "search returns InsufficientCidrBlocks with prefix delegation enabled"
  diagnosis: "Subnet is fragmented and does not have contiguous /28 CIDR blocks available for prefix delegation. Even if individual IPs are available, prefix delegation requires contiguous /28 blocks."
  resolution: "Operator action: create a subnet CIDR reservation for prefix delegation using 'aws ec2 create-subnet-cidr-reservation'. Alternatively, use a new subnet with sufficient contiguous space. Consider disabling prefix delegation and using secondary IP mode if the subnet is heavily fragmented."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Get network diagnostics
network_diagnostics(instanceId="i-0abc123def456", sections="cni,ipamd,eni")
# Step 3: Get IP allocation findings
errors(instanceId="i-0abc123def456")
# Step 4: Search for IP failures
search(instanceId="i-0abc123def456", query="failed to assign an IP|InsufficientFreeAddresses")
```

## Output Format

```yaml
root_cause: "<subnet_exhaustion|eni_limit|ipamd_failure|warm_target> — <detail>"
evidence:
  - type: network_diagnostics
    content: "<ENI/IP utilization from cni/ipamd sections>"
severity: CRITICAL
mitigation:
  immediate: "Operator: <specific fix>"
  long_term: "Enable prefix delegation, monitor ipamd metrics"
```
