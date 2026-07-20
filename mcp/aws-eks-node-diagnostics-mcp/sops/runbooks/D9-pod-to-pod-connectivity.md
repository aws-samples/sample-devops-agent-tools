---
title: "D9 — Pod-to-Pod Connectivity Failures"
description: "Diagnose inter-pod communication failures where pods have IPs but cannot reach each other, using tcpdump on pod veth interfaces and worker node eth0 to trace packet flow through the VPC CNI data path"
status: active
severity: HIGH
triggers:
  - "connection refused.*pod"
  - "connection timed out.*pod"
  - "no route to host.*10\\.\\d+"
  - "network unreachable.*pod"
  - "i/o timeout.*pod"
  - "NetworkPolicy.*deny"
  - "DENY.*verdict"
  - "policyendpoint"
  - "pod.*cannot reach.*pod"
  - "inter-pod.*fail"
owner: devops-agent
objective: "Trace the packet path from source pod through veth pair, node routing, and CNI data path to destination pod, identifying where packets are dropped or rejected"
context: >
  This SOP covers scenarios where pods have valid IP addresses (not stuck in ContainerCreating — that is D1)
  and services are not involved (that is D8), but direct pod-to-pod communication fails. The VPC CNI assigns
  real VPC IPs to pods via ENIs. Traffic between pods on the same node traverses the veth pair and Linux bridge/
  routing table. Traffic between pods on different nodes goes through the node's eth0, VPC routing, and the
  destination node's ENI. Failures can occur at any layer: veth misconfiguration, iptables/eBPF NetworkPolicy
  enforcement dropping traffic, missing routes for pod CIDRs, CNI plugin bugs, security group rules blocking
  inter-node traffic, or NACL restrictions. This SOP uses tcpdump_capture on both the pod's veth interface
  and the node's eth0 to pinpoint exactly where packets are lost. Cross-references D1 (IP allocation), D3
  (conntrack), D5 (DNS), D7 (general network perf), D8 (kube-proxy/service connectivity).
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Verify source and destination pods are Running and Ready — if pods are Pending, CrashLoopBackOff, or Terminating, that is the root cause, not a network issue
  - Check which nodes the pods are on — same node vs different nodes changes the investigation path entirely
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — if a node is NotReady, network will fail for all pods on it
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) for scheduling failures, OOM kills, or image pull errors
- Use `collect` tool with instanceId of the SOURCE node (where the calling pod runs) to gather logs
- Use `status` tool with executionId to poll until collection completes
- **PREREQUISITE — Is aws-node (VPC CNI) running on the source node?** Pod networking requires the CNI:
  - Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, namespace=kube-system, labelSelector=k8s-app=aws-node — check that aws-node pod on the SOURCE node is Running.
  - If aws-node pod is CrashLoopBackOff, Error, or missing: that is the root cause. Report "aws-node (VPC CNI) not running on source node — pod networking is broken at the CNI level" immediately.
  - ONLY if aws-node is confirmed running on the source node, proceed to connectivity investigation below.
- Use `errors` tool with instanceId to get pre-indexed findings — look for network/CNI errors
- Use `network_diagnostics` tool with instanceId and sections=eni,routes,iptables,cni to get the full network picture on the source node

SHOULD:
- Use `search` tool with instanceId and query=`connection refused|connection timed out|no route to host|network unreachable|i/o timeout` to find pod-level connectivity errors
- Use `search` tool with query=`NetworkPolicy|policyendpoint|DENY|verdict|network-policy-agent` to check if NetworkPolicy enforcement is blocking traffic
- Use `search` tool with query=`aws-node|ipamd|eni.*error|veth|cni.*error` to check VPC CNI health
- Determine if the issue is same-node (pods on same worker) or cross-node (pods on different workers):
  - Same-node: packets traverse veth → Linux routing → veth (never hits eth0)
  - Cross-node: packets traverse veth → Linux routing → eth0 → VPC → dest eth0 → dest veth

MAY:
- Use `quick_triage` tool with instanceId for a fast overview
- Use `cluster_health` tool with clusterName to check if connectivity failure is widespread
- If destination pod is on a different node, also use `collect` tool with the DESTINATION node instanceId

## Phase 2 — Enrich

### ⚠️ MANDATORY PRE-CHECK: Read CRITICAL_WARNINGS First

Before investigating ANY VPC CNI configuration, you MUST:
1. Check the `network_diagnostics` response for `CRITICAL_WARNINGS` and `rootCauseRanking` fields
2. If `CRITICAL_WARNINGS` exists, the root cause is ALREADY IDENTIFIED — do not investigate further
3. If `rootCauseRanking` shows kube-proxy as rank 1, the issue is kube-proxy NOT VPC CNI
4. Do NOT form hypotheses about podSGEnforcingMode, SNAT, or any CNI config until you have ruled out kube-proxy

### ⚠️ MANDATORY: kube-proxy vs CNI Ownership Check

If service connectivity (ClusterIP, NodePort) is failing:
- KUBE-SERVICES chain empty → kube-proxy issue, NOT CNI. Stop investigating CNI.
- KUBE-SERVICES chain populated → kube-proxy is fine, investigate CNI/routing/SG.

The VPC CNI NEVER creates, modifies, or reads KUBE-SERVICES chains. The string "KUBE-SERVICES" does not appear anywhere in the VPC CNI codebase. If KUBE-SERVICES is empty, no amount of CNI config changes will fix it.

MUST work through these layers in order to isolate where packets are dropped:

### 2A — VPC CNI Health and Pod IP Assignment

MUST:
- Use `search` tool with query=`ipamd.*error|ipamd.*failed|aws-node.*error|aws-node.*restart|aws-node.*CrashLoopBackOff` to check CNI DaemonSet health
  - If aws-node is crashing: pod networking is broken at the CNI level — no further packet tracing needed
- Use `search` tool with query=`eni.*attached|eni.*allocated|secondary.*IP|warm.*pool|WARM_IP_TARGET|WARM_ENI_TARGET` to verify ENI/IP allocation is healthy
- Use `search` tool with query=`veth.*error|veth.*not found|link.*not found|device.*not found` to check if pod veth interfaces exist
  - Missing veth: CNI failed to set up the pod network namespace

SHOULD:
- Use `search` tool with query=`aws-node.*version|vpc-cni.*version|VPC_CNI_VERSION` to check VPC CNI version
  - Known buggy version: VPC CNI v1.20.4 has issues
- Use `search` tool with query=`ENABLE_NETWORK_POLICY|enableNetworkPolicy|network-policy-agent` to check if NetworkPolicy enforcement is enabled
- Use `search` tool with query=`kube-proxy.*not running|kube-proxy.*CrashLoop` to check kube-proxy health — kube-proxy must be running for aws-node to reach Ready state. If kube-proxy is down, pod networking is broken at a lower level.

### 2B — NetworkPolicy Enforcement (Most Common Cause)

MUST:
- Use `search` tool with query=`NetworkPolicy|network-policy|policyendpoint|DENY.*verdict|ACCEPT.*verdict` to check if NetworkPolicy is blocking traffic
  - VPC CNI NetworkPolicy uses eBPF to enforce — check `/var/log/aws-routed-eni/network-policy-agent.log`
  - DENY verdicts in flow logs confirm NetworkPolicy is dropping packets
- Use `search` tool with query=`calico|cilium|Calico.*policy|CiliumNetworkPolicy` to check for third-party NetworkPolicy engines
  - After migrating from Calico to VPC CNI NetworkPolicy: port limit is 24 unique port combinations per ingress/egress selector — use port ranges instead
  - Calico and VPC CNI NetworkPolicy cannot run simultaneously

SHOULD:
- Use `search` tool with query=`default.*deny|deny.*all|ingress.*deny|egress.*deny` to check for default-deny policies
  - Default-deny with no matching allow policy = all pod traffic blocked
- Use `search` tool with query=`policyendpoints.*not found|policyendpoints.*error|Kyverno.*block` to check if policyendpoint CRD creation is blocked
  - If policyendpoints are not created: NetworkPolicy controller cannot push rules to the agent

### 2C — iptables / eBPF Rules on the Node

MUST:
- Review `network_diagnostics` iptables section for DROP or REJECT rules on FORWARD chain
  - Custom iptables rules or security tools can silently drop inter-pod traffic
  - Check for rules matching pod CIDR ranges
- Use `search` tool with query=`DROP.*FORWARD|REJECT.*FORWARD|iptables.*FORWARD.*policy` to find FORWARD chain drops
  - CRITICAL: custom AMIs may set iptables FORWARD policy to DROP — must be ACCEPT for pod networking
- Use `search` tool with query=`FORWARD.*ACCEPT|FORWARD.*policy.*ACCEPT` to verify FORWARD chain default is ACCEPT

SHOULD:
- Use `search` tool with query=`nf_conntrack.*table full|conntrack.*drop` to check if conntrack exhaustion is causing drops
  - If found: cross-reference D3 conntrack exhaustion SOP
- Use `search` tool with query=`ebpf.*error|bpf.*error|tc.*filter|cls_bpf` to check for eBPF program errors (VPC CNI NetworkPolicy uses eBPF)

### 2D — Routing (Same-Node vs Cross-Node)

MUST:
- Review `network_diagnostics` routes section for pod CIDR routing
  - Same-node: route for pod IP should point to the veth interface
  - Cross-node: route for remote pod CIDR should go via eth0 (VPC routing handles it)
- Use `search` tool with query=`blackhole|unreachable|no route|missing.*route|ip route` to find routing issues
  - Blackhole route for pod CIDR: CNI routing table corruption
  - Missing route for remote pod CIDR: VPC route table issue or CNI custom networking misconfiguration

SHOULD:
- Use `search` tool with query=`ENIConfig|custom.*networking|CUSTOM_NETWORKING|AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG` to check if custom networking is enabled
  - Custom networking uses separate subnets for pods — routing must account for this
- Use `search` tool with query=`prefix.*delegation|ENABLE_PREFIX_DELEGATION|/28` to check if prefix delegation is enabled
  - Prefix delegation assigns /28 blocks — routing is different from secondary IP mode

### 2E — Security Groups (Cross-Node Traffic)

MUST (for cross-node pod communication):
- Use `search` tool with query=`security group|sg-|SecurityGroupIds` to find security group configuration
  - Worker node security groups MUST allow all traffic between nodes in the cluster
  - Specifically: allow all TCP/UDP from the cluster security group to itself
- Use `network_diagnostics` eni section to check which security groups are attached to the node's ENIs

SHOULD:
- Use `search` tool with query=`NACL|network ACL|acl-` to check for NACL restrictions
  - NACLs are stateless — must allow both request and response traffic for pod CIDRs
- Use `search` tool with query=`SecurityGroupsForPods|ENABLE_POD_ENI|trunk.*ENI` to check if Security Groups for Pods is enabled
  - SGP uses branch ENIs with separate security groups per pod — can cause inter-pod isolation if SGs don't allow each other

### 2F — Packet Capture (tcpdump)

MUST (if the issue is not identified from log analysis above):
- Use `tcpdump_capture` tool on the SOURCE node to capture traffic on the pod's veth interface:
  - Filter for traffic to/from the destination pod IP
  - Duration: 30 seconds while reproducing the connectivity failure
  - This shows if packets LEAVE the source pod
- Use `tcpdump_capture` tool on the SOURCE node to capture on eth0:
  - Same filter for destination pod IP
  - This shows if packets reach the node's outbound interface (cross-node) or are dropped before
- Use `tcpdump_analyze` tool to analyze both captures:
  - Packets on veth but NOT on eth0: dropped by iptables/eBPF/routing on the source node
  - Packets on eth0 of source but not arriving at destination: dropped in VPC (SG, NACL, routing)
  - Packets arriving at destination eth0 but not on destination veth: dropped on destination node

SHOULD:
- If cross-node: also use `tcpdump_capture` on the DESTINATION node's eth0 and the destination pod's veth
  - This gives the full 4-point trace: src-veth → src-eth0 → dst-eth0 → dst-veth
- Use `tcpdump_analyze` to check for:
  - TCP RST (connection refused — something is actively rejecting)
  - TCP SYN with no SYN-ACK (packets silently dropped)
  - ICMP unreachable messages (routing or firewall rejection)

### 2G — Control Plane kube-audit Logs

SHOULD:
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="NetworkPolicy" to check for recent NetworkPolicy create/update/delete events that may be blocking traffic
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="policyendpoints" to check for policyendpoint CRD mutations
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="securitygroups" to check for SecurityGroupPolicy changes (if SGP is in use)
- Correlate timestamps of NetworkPolicy mutations with the onset of connectivity failures — a recently applied deny-all policy is a common root cause

### Timeline Correlation

MUST:
- Use `correlate` tool with instanceId and pivotEvent set to the connectivity error pattern to build a timeline

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from all pod connectivity findings
- State root cause with the specific layer identified:
  - CNI health: aws-node crash, veth not created, IP not assigned
  - NetworkPolicy: DENY verdict blocking inter-pod traffic, default-deny with no allow rule, Calico migration port limit
  - iptables/eBPF: FORWARD chain DROP policy, custom iptables rules, eBPF program error
  - Routing: missing route for pod CIDR, blackhole route, custom networking misconfiguration
  - Security groups: node SGs not allowing inter-node traffic, SGP branch ENI isolation
  - NACLs: stateless rules blocking pod CIDR traffic
  - VPC routing: missing route for pod CIDR in VPC route table
- Include tcpdump evidence showing exactly where packets were lost in the path
- Recommend specific fix (operator action — not available via MCP tools)

SHOULD:
- Include the 4-point packet trace results if captured (src-veth, src-eth0, dst-eth0, dst-veth)
- Include NetworkPolicy flow log evidence if applicable
- Include VPC CNI version and configuration

MAY:
- Recommend enabling VPC CNI NetworkPolicy flow logs for ongoing visibility
- Recommend VPC Flow Logs for cross-node packet loss investigation
- Recommend using `kubectl exec` to test connectivity from within pods (operator action)

## Guardrails

anti_hallucination:
  - "CRITICAL: POD_SECURITY_GROUP_ENFORCING_MODE (podSGEnforcingMode) does NOT create a default-deny for unannotated pods. It ONLY affects pods that have the vpc.amazonaws.com/pod-eni annotation (Security Groups for Pods). In strict mode, annotated pods use their branch ENI exclusively instead of falling back to the primary ENI. Pods WITHOUT SGP annotations are COMPLETELY UNAFFECTED — they use the primary ENI and normal VPC routing. Do NOT blame podSGEnforcingMode for pod-to-pod connectivity failures on pods without SGP annotations. If pod-to-pod traffic is failing, check kube-proxy health, KUBE-SERVICES iptables chain, NetworkPolicy enforcement, and security groups FIRST."

escalation_conditions:
  - "aws-node DaemonSet crashing on multiple nodes — cluster-wide CNI failure"
  - "iptables FORWARD policy is DROP on custom AMI — requires AMI rebuild or node-level fix"
  - "VPC route table missing pod CIDR routes — requires VPC-level changes"
  - "Security Groups for Pods causing isolation — requires SG rule changes across multiple SGs"
  - "tcpdump shows packets leaving source node but never arriving at destination — VPC-level issue"
  - "NetworkPolicy migration from Calico hitting port limit — requires policy redesign"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics, correlate, compare_nodes: GREEN (read-only)"
  - "tcpdump_capture, tcpdump_analyze: GREEN (read-only packet capture on node)"
  - "Modify NetworkPolicy: YELLOW — operator action, not available via MCP tools"
  - "Modify iptables FORWARD policy: YELLOW — operator action, affects all pod traffic on node"
  - "Modify security groups: YELLOW — operator action, affects network access"
  - "Restart aws-node DaemonSet: YELLOW — operator action, brief pod networking disruption"
  - "Modify VPC route tables: RED — operator action, VPC-wide impact"

## Common Issues

- symptoms: "list_k8s_resources returns aws-node pod in CrashLoopBackOff, Error, or missing on the source or destination node"
  diagnosis: "VPC CNI DaemonSet not running. Pod networking is broken at the CNI level — no further packet tracing needed."
  resolution: "Operator action: check aws-node logs (kubectl logs -n kube-system -l k8s-app=aws-node). Common fixes: update VPC CNI addon, check IAM permissions (AmazonEKS_CNI_Policy), verify subnet has available IPs."

- symptoms: "search returns DENY verdict in network-policy-agent.log for the pod IPs"
  diagnosis: "NetworkPolicy is blocking inter-pod traffic. Check if a default-deny policy exists without a matching allow rule."
  resolution: "Operator action: review NetworkPolicy rules. Add an allow rule for the required traffic, or remove the overly restrictive policy. Use 'kubectl get networkpolicy -A' to list all policies."

- symptoms: "search returns iptables FORWARD chain policy is DROP"
  diagnosis: "Custom AMI has iptables FORWARD policy set to DROP. This blocks all inter-pod traffic that traverses the FORWARD chain."
  resolution: "Operator action: set iptables FORWARD policy to ACCEPT. Add 'iptables -P FORWARD ACCEPT' to kubelet.service or node bootstrap. Rebuild AMI for permanent fix."

- symptoms: "tcpdump shows packets on source veth but NOT on source eth0 (cross-node)"
  diagnosis: "Packets are being dropped on the source node between the veth and eth0. Check iptables FORWARD chain, eBPF programs, and routing table."
  resolution: "Operator action: check 'iptables -L FORWARD -n -v' for DROP rules matching pod CIDRs. Check for eBPF programs with 'tc filter show dev <veth> egress'."

- symptoms: "tcpdump shows packets on source eth0 but NOT on destination eth0"
  diagnosis: "Packets are lost in the VPC. Security group, NACL, or VPC route table is dropping the traffic."
  resolution: "Operator action: verify node security groups allow all traffic from the cluster security group. Check NACLs allow pod CIDR ranges. Check VPC route tables have routes for all pod CIDRs."

- symptoms: "tcpdump shows packets on destination eth0 but NOT on destination veth"
  diagnosis: "Packets arrive at the destination node but are dropped before reaching the pod. Check iptables/eBPF on the destination node and verify the destination pod's veth exists."
  resolution: "Operator action: run the same iptables and NetworkPolicy checks on the destination node. Verify the destination pod is Running and its veth interface exists."

- symptoms: "search returns aws-node CrashLoopBackOff or ipamd errors"
  diagnosis: "VPC CNI DaemonSet is unhealthy. Pod networking is broken at the CNI level."
  resolution: "Operator action: check aws-node logs with 'kubectl logs -n kube-system -l k8s-app=aws-node'. Common fixes: update VPC CNI addon, check IAM permissions for the CNI role, verify subnet has available IPs."

- symptoms: "search returns veth not found or device not found for a pod"
  diagnosis: "Pod's veth interface was not created by the CNI. Pod has an IP but no network path."
  resolution: "Operator action: delete and recreate the pod. If persistent, restart aws-node on the affected node. Check CNI logs for setup errors."

- symptoms: "search returns policyendpoints not found or Kyverno blocking policyendpoint creation"
  diagnosis: "NetworkPolicy controller cannot create policyendpoint CRDs. Policies are defined but not enforced — or enforced incorrectly."
  resolution: "Operator action: check ClusterRole permissions for aws-node and eks:network-policy-controller. If Kyverno is installed, ensure it allows policyendpoint creation."

- symptoms: "cross-node pod communication fails but same-node works fine"
  diagnosis: "VPC-level issue: security groups, NACLs, or route tables blocking inter-node traffic on pod CIDRs."
  resolution: "Operator action: verify node security groups allow all traffic from cluster SG. Check VPC route tables. Enable VPC Flow Logs to see rejected packets."

- symptoms: "search returns Calico migration errors or port limit exceeded"
  diagnosis: "After migrating from Calico to VPC CNI NetworkPolicy, the 24 unique port combination limit per selector is exceeded."
  resolution: "Operator action: consolidate NetworkPolicy port specifications into port ranges (e.g., 8000-8100 instead of listing 25+ individual ports)."

- symptoms: "SecurityGroupsForPods enabled and pods in different SGs cannot communicate"
  diagnosis: "Branch ENIs have separate security groups. Pod A's SG does not allow traffic from Pod B's SG and vice versa."
  resolution: "Operator action: update the security groups assigned to pods to allow traffic from each other's SGs."

- symptoms: "tcpdump shows outbound packets to external (non-RFC1918) IPs with the node IP as source instead of the pod IP"
  diagnosis: "THIS IS NORMAL BEHAVIOR. By default, VPC CNI performs SNAT (Source NAT) on pod traffic destined outside the VPC, translating the pod source IP to the node's primary ENI IP. This is controlled by AWS_VPC_K8S_CNI_EXTERNALSNAT (default: false, meaning SNAT is enabled). See https://docs.aws.amazon.com/eks/latest/userguide/external-snat.html"
  resolution: "No action required — this is default VPC CNI behavior. If pods need to preserve their source IP for external traffic (e.g., for VPN or Direct Connect peers), set AWS_VPC_K8S_CNI_EXTERNALSNAT=true on the aws-node DaemonSet."

- symptoms: "tcpdump shows a small number of ICMP port-unreachable or host-unreachable messages during or shortly after a rolling update or pod deletion"
  diagnosis: "THIS IS NORMAL BEHAVIOR. When a pod is deleted, VPC CNI places the pod's IP in a 30-second cooldown cache before recycling it. During this window, kube-proxy iptables rules on other nodes may still route traffic to the old pod IP, resulting in ICMP unreachable responses. This is a transient condition that resolves once all nodes update their iptables rules. See https://docs.aws.amazon.com/eks/latest/best-practices/vpc-cni.html"
  resolution: "No action required if the count is small and occurs during deployments. If persistent, check that kube-proxy is running and syncing rules on all nodes. Ensure pods have proper preStop hooks to allow graceful connection draining."

- symptoms: "tcpdump shows zero-length ACK packets (TCP keepalives) on long-lived connections like gRPC, database pools, or websockets"
  diagnosis: "THIS IS NORMAL BEHAVIOR. Applications and kernels send TCP keepalive probes to prevent idle connection timeout by NAT Gateway (350s idle timeout), NLB, or conntrack table eviction. These appear as small ACK-only packets with length 0 on established connections. See https://aws.amazon.com/blogs/networking-and-content-delivery/implementing-long-running-tcp-connections-within-vpc-networking/"
  resolution: "No action required — keepalives are essential for maintaining long-lived connections through stateful network devices. If connections are still dropping, check that the keepalive interval is shorter than the NAT Gateway/NLB idle timeout (350s)."

- symptoms: "tcpdump shows ICMP 'fragmentation needed' or 'packet too big' messages"
  diagnosis: "THIS IS NORMAL BEHAVIOR. These are Path MTU Discovery (PMTUD) messages indicating the network is telling the sender to reduce packet size. Common when traffic crosses MTU boundaries (e.g., 9001 jumbo frames on EC2 to 1500 standard MTU on VPN/internet)."
  resolution: "No action required — PMTUD is working correctly. If application performance is affected, ensure the application respects the DF (Don't Fragment) bit and adjusts MSS accordingly. Check MTU settings on all interfaces in the path."

## Examples

```
# Step 1: Collect logs from the SOURCE node
collect(instanceId="i-0abc123def456")
status(executionId="<id-from-step-1>")

# Step 2: Get findings
errors(instanceId="i-0abc123def456")

# Step 3: Full network diagnostics on source node
network_diagnostics(instanceId="i-0abc123def456", sections="eni,routes,iptables,cni")

# Step 4: Check VPC CNI health
search(instanceId="i-0abc123def456", query="aws-node|ipamd|veth.*error|cni.*error")

# Step 5: Check NetworkPolicy enforcement
search(instanceId="i-0abc123def456", query="NetworkPolicy|DENY.*verdict|policyendpoint|network-policy-agent")

# Step 6: Check iptables FORWARD chain
search(instanceId="i-0abc123def456", query="FORWARD.*DROP|FORWARD.*REJECT|FORWARD.*policy")

# Step 7: Check routing for pod CIDRs
search(instanceId="i-0abc123def456", query="blackhole|no route|missing.*route|pod.*CIDR")

# Step 8: Check security groups (cross-node)
search(instanceId="i-0abc123def456", query="security group|sg-|SecurityGroupIds")

# Step 9: Capture on source pod veth (while reproducing the issue)
tcpdump_capture(instanceId="i-0abc123def456", interface="<pod-veth>", duration=30, filter="host <dest-pod-ip>")
tcpdump_analyze(instanceId="i-0abc123def456", captureId="<id-from-step-9>")

# Step 10: Capture on source node eth0
tcpdump_capture(instanceId="i-0abc123def456", interface="eth0", duration=30, filter="host <dest-pod-ip>")
tcpdump_analyze(instanceId="i-0abc123def456", captureId="<id-from-step-10>")

# Step 11: If cross-node — capture on DESTINATION node eth0 and veth
collect(instanceId="i-0dest789ghi012")
tcpdump_capture(instanceId="i-0dest789ghi012", interface="eth0", duration=30, filter="host <src-pod-ip>")
tcpdump_analyze(instanceId="i-0dest789ghi012", captureId="<id-from-step-11>")
tcpdump_capture(instanceId="i-0dest789ghi012", interface="<dest-pod-veth>", duration=30, filter="host <src-pod-ip>")
tcpdump_analyze(instanceId="i-0dest789ghi012", captureId="<id-from-step-11b>")

# Step 12: Correlate timeline
correlate(instanceId="i-0abc123def456", pivotEvent="connection refused|timed out|DENY", timeWindow=300)

# Step 13: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003","F-004"])
```

## Output Format

```yaml
root_cause: "<cni_health|network_policy|iptables_forward|routing|security_groups|nacls|vpc_routing|sgp_isolation> — <specific detail>"
traffic_path: "<same_node|cross_node>"
packet_trace:
  src_veth: "<packets seen: yes/no>"
  src_eth0: "<packets seen: yes/no>"
  dst_eth0: "<packets seen: yes/no (cross-node only)>"
  dst_veth: "<packets seen: yes/no>"
  drop_point: "<src_node_forward_chain|src_node_routing|vpc_sg_nacl|dst_node_forward_chain|dst_node_routing>"
evidence:
  - type: network_diagnostics
    content: "<iptables rules, routes, ENI config, CNI status>"
  - type: search
    content: "<NetworkPolicy DENY verdicts, iptables DROP rules, CNI errors>"
  - type: tcpdump_analyze
    content: "<packet capture showing where traffic stops>"
  - type: correlate
    content: "<timeline of connectivity failure>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix based on drop point>"
  long_term: "Enable NetworkPolicy flow logs, enable VPC Flow Logs, monitor aws-node health"
cross_reference:
  - "D1 if pods stuck in ContainerCreating (IP allocation)"
  - "D3 if conntrack exhaustion contributing to drops"
  - "D5 if DNS resolution failing (not direct pod IP)"
  - "D7 if general network performance degradation"
  - "D8 if Service/ClusterIP connectivity (not direct pod-to-pod)"
```
