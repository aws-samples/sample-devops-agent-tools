---
title: "A4 — Worker Node Fails to Join EKS Cluster"
description: "Comprehensive SOP for diagnosing all known reasons an EC2 worker node fails to register with an EKS cluster, covering IAM, networking, bootstrap, DNS, STS, AMI, security groups, tags, and VPC endpoint issues"
status: active
severity: CRITICAL
triggers:
  - "Instances failed to join the kubernetes cluster"
  - "NodeCreationFailure"
  - "Unable to register node"
  - "Unauthorized"
  - "connect: connection refused"
  - "TLS handshake timeout"
  - "node.*not found"
  - "InvalidClientTokenId"
  - "AccessDenied.*sts"
  - "Create failed"
owner: devops-agent
objective: "Systematically identify which of the 12+ known failure modes is preventing the worker node from joining the EKS cluster and recommend the specific fix"
context: >
  A worker node that never appears in 'kubectl get nodes' after launch. This is one of the most common EKS issues
  with many possible root causes spanning IAM (aws-auth/access entries, node role permissions), networking
  (security groups, NACLs, route tables, VPC endpoints, NAT gateway), bootstrap configuration (user data,
  cluster name, API endpoint args), DNS (VPC DNS settings, DHCP options), STS (regional endpoint activation),
  AMI (missing required components), and tagging (kubernetes.io/cluster tag). This SOP covers all failure modes
  documented in the AWS EKS troubleshooting guide and re:Post knowledge center. Cross-references A2 (bootstrap
  registration) for overlap but provides deeper coverage of networking, IAM, and VPC endpoint scenarios.
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — check if the node appears in the node list (failed join = not listed)
  - If node IS listed: check its status and conditions for join-related errors
  - If node is NOT listed: confirms join failure — proceed with log collection via SSM
- Use `collect` tool with instanceId to gather logs from the node that failed to join
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=critical to get pre-indexed findings
- Use `search` tool with instanceId and query=`Unauthorized|Unable to register|connection refused|TLS handshake timeout|InvalidClientTokenId|not found|AccessDenied|Create failed|NodeCreationFailure` to cast a wide net for join failure evidence

SHOULD:
- Use `search` tool with query=`cloud-init|bootstrap|/etc/eks/bootstrap` to check if bootstrap script ran and what arguments were passed
- Use `search` tool with query=`kubelet.*error|kubelet.*fatal|kubelet.*failed` to check kubelet startup errors
- Use `network_diagnostics` tool with instanceId and sections=routes,dns,eni to check network path to API server
- Use `cluster_health` tool with clusterName to verify the cluster itself is healthy and accepting registrations

MAY:
- Use `quick_triage` tool with instanceId for a fast overview
- Use `compare_nodes` tool with instanceIds of failed node + a healthy registered node to diff findings

## Phase 2 — Enrich

MUST check each failure domain systematically. Work through these in order — the first match is likely the root cause:

### 2A — IAM / Authentication (most common)

MUST:
- Use `search` tool with query=`Unauthorized|401|Forbidden|403` to check for authentication failures
  - If found: aws-auth ConfigMap or EKS access entry is missing the node IAM role ARN
  - Common mistake: using instance profile ARN instead of role ARN
  - Common mistake: role ARN contains a path (e.g., /development/apps/my-role) — path must be removed
  - Recommended: use EKS Access Entries (EC2 Linux or EC2 Windows type) instead of aws-auth ConfigMap for new clusters
- Use `search` tool with query=`AccessDenied|not authorized|AmazonEKSWorkerNodePolicy|AmazonEKS_CNI_Policy|AmazonEC2ContainerRegistryReadOnly|AmazonEC2ContainerRegistryPullOnly` to check node role permissions
  - Node role must have: AmazonEKSWorkerNodePolicy, AmazonEKS_CNI_Policy, and AmazonEC2ContainerRegistryReadOnly (or AmazonEC2ContainerRegistryPullOnly for newer setups)
  - Also verify the cluster IAM role has AmazonEKSClusterPolicy and the correct trust policy for eks.amazonaws.com
- Use `search` tool with query=`InvalidClientTokenId|SignatureDoesNotMatch|security token.*expired` to check STS/credential issues
  - If found: regional STS endpoint may not be activated for this region

SHOULD:
- Use `search` tool with query=`sts.*endpoint|sts.*region|regional.*sts` to check if regional STS is being used
- Use `search` tool with query=`access.entry|iamidentitymapping|aws-auth` to check for auth configuration clues

### 2B — Network Connectivity to API Server

MUST:
- Use `network_diagnostics` tool with instanceId and sections=routes,eni to check routing
- Use `search` tool with query=`TLS handshake timeout|connection timed out|connection refused|no route to host|dial tcp.*443` to find API server connectivity failures
  - TLS handshake timeout: security group or NACL blocking port 443 to API server endpoint
  - Connection refused: API server endpoint unreachable or private endpoint not enabled
  - No route to host: routing issue — check route tables, NAT gateway, internet gateway
- Use `search` tool with query=`private.*endpoint|public.*endpoint|endpoint.*access` to check cluster endpoint configuration

SHOULD:
- Use `search` tool with query=`NAT|nat-|igw-|internet gateway` to check NAT/IGW configuration in route tables
- Use `search` tool with query=`vpc.*endpoint|vpce-|PrivateLink` to check VPC endpoint configuration for private clusters
  - Private clusters need VPC endpoints for: ec2, ecr.api, ecr.dkr, sts, s3 (gateway)
- Use `network_diagnostics` iptables section to check for firewall rules blocking outbound 443

### 2C — DNS Configuration

MUST:
- Use `network_diagnostics` tool with instanceId and sections=dns to check DNS resolution
- Use `search` tool with query=`node.*not found|hostname.*not found|NXDOMAIN|resolve.*failed` to find DNS failures
  - "node not found" error: VPC missing DHCP options for domain-name and domain-name-servers
  - DNS resolution failure: VPC DNS support or DNS hostnames not enabled
- Use `search` tool with query=`DHCP|domain-name|AmazonProvidedDNS` to check DHCP options

### 2D — Bootstrap / User Data Configuration

MUST:
- Use `search` tool with query=`bootstrap\.sh|/etc/eks/bootstrap|cloud-init.*error|cloud-init.*fatal` to check bootstrap execution
  - Bootstrap script not found: AMI may not be EKS-optimized
  - Bootstrap args wrong: ClusterName mismatch, missing --apiserver-endpoint for private clusters
- Use `search` tool with query=`ClusterName|cluster-name|--b64-cluster-ca|--apiserver-endpoint|--dns-cluster-ip` to verify bootstrap arguments
  - For private clusters: --apiserver-endpoint, --b64-cluster-ca, and --dns-cluster-ip MUST be passed explicitly
  - ClusterName must exactly match the EKS cluster name (case-sensitive)
- Use `search` tool with query=`nodeadm|NodeConfig|node.eks.aws` to check AL2023 nodeadm configuration (if applicable)

SHOULD:
- Use `search` tool with query=`Too Many Requests|throttl|rate limit|DescribeCluster` to check for API throttling during bootstrap
  - If found: pass --apiserver-endpoint, --b64-cluster-ca, --dns-cluster-ip directly to avoid DescribeCluster API call

### 2E — Security Groups and NACLs

MUST:
- Use `search` tool with query=`security group|sg-|inbound|outbound|egress|ingress` to find security group references
  - Control plane SG must allow inbound 443 from worker node SG
  - Worker node SG must allow outbound 443 to control plane SG and 10250 from control plane SG
  - Worker node SG must allow outbound 443 to 0.0.0.0/0 (for ECR, STS, etc.) or to VPC endpoints
- Use `search` tool with query=`NACL|network ACL|acl-` to check for NACL restrictions
  - NACLs must allow ports 80, 443, and 1025-65535 inbound and outbound

### 2F — Tagging

SHOULD:
- Use `search` tool with query=`kubernetes.io/cluster|tag.*owned|tag.*shared` to check instance tags
  - Node must have tag: kubernetes.io/cluster/<cluster-name> = owned

### 2G — AMI Issues

SHOULD:
- Use `search` tool with query=`ami-|AMI|image.*id|kubelet.*not found|containerd.*not found` to check AMI
  - Custom AMIs must include kubelet, containerd/docker, aws-iam-authenticator, and bootstrap.sh
  - AMI must match the cluster's Kubernetes version
- Use `search` tool with query=`Not authorized for images|private.*AMI|Windows.*AMI` to check for AMI access issues

### 2H — Subnet IP Exhaustion

MAY:
- Use `search` tool with query=`InsufficientFreeAddresses|no available IP|subnet.*full` to check for IP exhaustion
  - If subnet has no available IPs, node gets an IP but pods cannot — or node may fail to launch entirely
  - Resolution: add secondary CIDR to VPC or use different subnets

### 2I — Control Plane kube-audit Logs

SHOULD:
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="certificatesigningrequests" to check for node CSR requests and whether they were approved or denied
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="aws-auth" to check for recent aws-auth ConfigMap changes that may have removed the node role mapping
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="access" to check for access entry create/update/delete events
- Correlate timestamps of auth configuration changes with the node join failure — a recently removed mapping is a common root cause

### Timeline Correlation

MUST:
- Use `correlate` tool with instanceId and pivotEvent set to the first error found above to build a timeline of the join failure sequence

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from all relevant findings
- State root cause with the specific failure domain identified:
  - IAM: missing aws-auth entry, wrong ARN format, missing node role policies, STS endpoint not activated
  - Network: SG blocking 443, missing NAT/IGW, missing VPC endpoints for private cluster, route table misconfiguration
  - DNS: VPC DNS disabled, missing DHCP options, node hostname resolution failure
  - Bootstrap: wrong cluster name, missing private cluster args, cloud-init failure, API throttling
  - Security rules: NACL blocking required ports
  - Tags: missing kubernetes.io/cluster tag
  - AMI: missing required components, version mismatch, private AMI access denied
  - IP exhaustion: subnet out of IPs
- Recommend specific fix (operator action — not available via MCP tools)
- Cross-reference: if the issue is specifically Unauthorized + kubelet restart, also see A2 SOP

SHOULD:
- Include the specific log evidence from search results
- Include the failure domain and sub-category
- Provide the exact operator remediation command or configuration change needed

MAY:
- Recommend using managed node groups to avoid manual aws-auth management
- Recommend the AWSSupport-TroubleshootEKSWorkerNode SSM runbook for additional automated diagnostics
- Recommend passing bootstrap args explicitly to avoid DescribeCluster API throttling

## Guardrails

escalation_conditions:
  - "Node role is correctly mapped and network is open but node still cannot join — possible control plane issue"
  - "API server endpoint completely unreachable from VPC — possible VPC endpoint or peering misconfiguration"
  - "Multiple nodes failing to join simultaneously — check via cluster_health for cluster-level issues"
  - "Managed node group stuck in Create failed for > 15 minutes"
  - "Private cluster with no VPC endpoints configured — requires infrastructure changes"
  - "AMI missing required EKS components — requires new AMI build"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics, correlate, compare_nodes, cluster_health: GREEN (read-only)"
  - "Update aws-auth ConfigMap or create access entry: YELLOW — operator action, not available via MCP tools"
  - "Modify security groups or NACLs: YELLOW — operator action, not available via MCP tools"
  - "Create VPC endpoints: YELLOW — operator action, not available via MCP tools"
  - "Modify bootstrap user data / launch template: YELLOW — operator action, not available via MCP tools"
  - "Replace node or node group: RED — operator action, requires approval"

## Common Issues

- symptoms: "search returns Unauthorized or 401 in kubelet logs"
  diagnosis: "aws-auth ConfigMap or EKS access entry missing node IAM role. Common mistake: using instance profile ARN instead of role ARN, or role ARN contains a path."
  resolution: "Operator action: add node role ARN to aws-auth via 'eksctl create iamidentitymapping --cluster <name> --arn <role-arn> --group system:bootstrappers --group system:nodes' or create an EC2_linux access entry."

- symptoms: "search returns TLS handshake timeout on port 443"
  diagnosis: "Security group or NACL blocking outbound 443 from worker node to EKS API server endpoint."
  resolution: "Operator action: update worker node security group to allow outbound TCP 443 to the cluster security group and 0.0.0.0/0. Check NACLs allow ports 443 and 1025-65535."

- symptoms: "search returns connection refused or connection timed out to API endpoint"
  diagnosis: "Cluster private endpoint not enabled, or node is in private subnet without NAT gateway, or VPC endpoints missing for private cluster."
  resolution: "Operator action: enable private endpoint access on cluster, or add NAT gateway to private subnet route table, or create VPC endpoints (ec2, ecr.api, ecr.dkr, sts, s3)."

- symptoms: "search returns 'node not found' error in kubelet logs"
  diagnosis: "VPC missing DHCP options for domain-name and domain-name-servers. Node cannot resolve its own hostname."
  resolution: "Operator action: create DHCP options set with domain-name=<region>.compute.internal and domain-name-servers=AmazonProvidedDNS, associate with VPC."

- symptoms: "search returns InvalidClientTokenId"
  diagnosis: "Regional STS endpoint not activated for this AWS region."
  resolution: "Operator action: activate the regional STS endpoint in IAM console under Account Settings > STS > Endpoints."

- symptoms: "search returns Too Many Requests or DescribeCluster throttling"
  diagnosis: "API throttling during bootstrap when many nodes launch simultaneously. bootstrap.sh calls DescribeCluster API."
  resolution: "Operator action: pass --apiserver-endpoint, --b64-cluster-ca, --dns-cluster-ip directly in user data to skip DescribeCluster call."

- symptoms: "search returns bootstrap.sh not found or cloud-init error"
  diagnosis: "AMI is not EKS-optimized or is missing required components (kubelet, containerd, bootstrap.sh)."
  resolution: "Operator action: use an official EKS-optimized AMI, or ensure custom AMI includes all required components and matches cluster Kubernetes version."

- symptoms: "search returns ClusterName mismatch or wrong cluster name in kubeconfig"
  diagnosis: "Bootstrap user data has incorrect ClusterName parameter. Case-sensitive exact match required."
  resolution: "Operator action: fix ClusterName in launch template user data to exactly match the EKS cluster name."

- symptoms: "search returns kubernetes.io/cluster tag missing"
  diagnosis: "Node not tagged as owned by the cluster. Required for node discovery."
  resolution: "Operator action: add tag kubernetes.io/cluster/<cluster-name>=owned to the EC2 instance or launch template."

- symptoms: "search returns AccessDenied for AmazonEKSWorkerNodePolicy or AmazonEKS_CNI_Policy"
  diagnosis: "Node IAM role missing required managed policies, or SCP/permissions boundary blocking the policies."
  resolution: "Operator action: attach AmazonEKSWorkerNodePolicy, AmazonEKS_CNI_Policy, and AmazonEC2ContainerRegistryReadOnly (or AmazonEC2ContainerRegistryPullOnly for newer setups) to the node IAM role. Also verify the cluster IAM role has AmazonEKSClusterPolicy and the correct trust policy for eks.amazonaws.com."

- symptoms: "errors tool returns no findings, node simply never appears"
  diagnosis: "Bootstrap script may not have run at all. Check cloud-init logs for user data execution."
  resolution: "Operator action: verify launch template user data is correctly formatted (MIME multipart for AL2023, bash script for AL2). Check cloud-init output log."

- symptoms: "search returns AutoScalingGroupNotFound or managed node group shows Degraded"
  diagnosis: "The Auto Scaling Group referenced by the managed node group was deleted or cannot be found."
  resolution: "Operator action: delete the degraded node group and create a new one. The ASG cannot be recovered once deleted."

- symptoms: "search returns ClusterUnreachable or etcd database size exceeded"
  diagnosis: "EKS control plane etcd database has exceeded 8GB, making the cluster unreachable. Nodes cannot join because the API server is unavailable."
  resolution: "Operator action: reduce the number of objects in the cluster (delete unused ConfigMaps, Secrets, Events). Contact AWS Support if the cluster remains unreachable."

- symptoms: "search returns AutoScalingGroupInvalidConfiguration or subnet mismatch"
  diagnosis: "Node group subnets do not match the cluster subnets, or the specified subnets no longer exist."
  resolution: "Operator action: delete the node group and recreate with correct subnets that match the cluster VPC configuration."

- symptoms: "search returns Ec2SecurityGroupNotFound"
  diagnosis: "The security group referenced by the node group was deleted. Managed node groups cannot recover from a deleted SG."
  resolution: "Operator action: delete the node group and create a new one with a valid security group. The deleted SG cannot be re-associated."

- symptoms: "search returns Ec2LaunchTemplateNotFound or LaunchTemplateVersionMismatch"
  diagnosis: "The launch template or the specified version was deleted or does not exist."
  resolution: "Operator action: update the node group to use a valid launch template version, or delete and recreate the node group."

- symptoms: "search returns AsgInstanceLaunchFailures or InsufficientInstanceCapacity for Spot"
  diagnosis: "Spot instance capacity unavailable for the requested instance type in the specified AZ."
  resolution: "Operator action: use mixed instance types in the node group to increase Spot availability. Configure multiple instance types across multiple AZs."

- symptoms: "search returns IamInstanceProfileNotFound or IamNodeRoleNotFound"
  diagnosis: "The IAM instance profile or node role referenced by the node group was deleted."
  resolution: "Operator action: delete the degraded node group and create a new one with a valid IAM role and instance profile."

## Examples

```
# Step 1: Collect logs from the node that failed to join
collect(instanceId="i-0abc123def456")
status(executionId="<id-from-step-1>")

# Step 2: Get all findings
errors(instanceId="i-0abc123def456", severity="critical")

# Step 3: Wide search for join failure patterns
search(instanceId="i-0abc123def456", query="Unauthorized|TLS handshake|connection refused|InvalidClientTokenId|not found")

# Step 4: Check bootstrap execution
search(instanceId="i-0abc123def456", query="cloud-init|bootstrap.sh|/etc/eks/bootstrap")

# Step 5: Check network path to API server
network_diagnostics(instanceId="i-0abc123def456", sections="routes,dns,eni")

# Step 6: Check IAM / auth errors
search(instanceId="i-0abc123def456", query="Unauthorized|AccessDenied|aws-auth|iamidentitymapping")

# Step 7: Check security group / NACL issues
search(instanceId="i-0abc123def456", query="security group|NACL|DROP|REJECT")

# Step 8: Check STS endpoint
search(instanceId="i-0abc123def456", query="InvalidClientTokenId|sts.*endpoint|regional.*sts")

# Step 9: Check DNS
search(instanceId="i-0abc123def456", query="node.*not found|DHCP|domain-name|NXDOMAIN")

# Step 10: Check VPC endpoints (private clusters)
search(instanceId="i-0abc123def456", query="vpc.*endpoint|vpce-|PrivateLink|private.*endpoint")

# Step 11: Correlate timeline
correlate(instanceId="i-0abc123def456", pivotEvent="<first-error-pattern>", timeWindow=300)

# Step 12: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003"])
```

## Output Format

```yaml
root_cause: "<iam_auth|network_connectivity|dns_config|bootstrap_config|security_rules|tagging|ami_issue|ip_exhaustion|sts_endpoint> — <specific detail>"
failure_domain: "<iam|network|dns|bootstrap|security|tags|ami|ip>"
evidence:
  - type: search
    content: "<specific error message from logs>"
  - type: network_diagnostics
    content: "<route table, DNS, ENI findings>"
  - type: correlate
    content: "<timeline of join failure sequence>"
blast_radius: "node (<instance-id>) or node group (<group-name>)"
severity: CRITICAL
mitigation:
  immediate: "Operator: <specific fix based on root cause>"
  long_term: "Use managed node groups, pass bootstrap args explicitly, monitor node group health"
cross_reference:
  - "A2 if specifically Unauthorized + kubelet restart loop"
  - "H1 if node role missing IAM permissions for other AWS services"
  - "D5 if DNS resolution failures extend beyond node join"
```
