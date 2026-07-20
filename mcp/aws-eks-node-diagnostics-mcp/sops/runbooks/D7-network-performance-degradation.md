---
title: "D7 — Network Performance Degradation"
description: "Diagnose high latency, packet loss, TCP retransmissions, and intermittent timeouts not explained by ENA throttling, MTU, or DNS issues"
status: active
severity: HIGH
triggers:
  - "retransmit"
  - "TCP.*reset"
  - "connection timed out"
  - "no route to host"
  - "rx_errors"
  - "tx_errors"
  - "packet loss"
  - "latency"
owner: devops-agent
objective: "Identify the layer causing network performance degradation (NIC errors, TCP stack, security rules, routing, or upstream) and recommend targeted remediation"
context: "Network performance issues that are not ENA throttling (D6/J1), MTU (D4), DNS (D5), or conntrack (D3) fall here. Common causes include TCP retransmissions from packet loss, interface errors, security group or NACL drops, route blackholes, IRQ imbalance, and ring buffer overflows. These manifest as high latency, intermittent timeouts, or degraded throughput without a clear ENA allowance_exceeded signal."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for pods experiencing slow responses, timeouts, or packet loss
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for network error patterns
- Use `network_diagnostics` tool with instanceId and sections=eni,routes,iptables,cni,dns to get a full network picture

SHOULD:
- Use `search` tool with instanceId and query=`retransmit|retrans|RST|reset|timeout|timed out|no route to host` to find TCP-level failure evidence
- Use `search` tool with query=`rx_errors|tx_errors|rx_dropped|tx_dropped|rx_crc|collisions|carrier` to find NIC-level errors
- Rule out other D-series SOPs first:
  - Check for `allowance_exceeded` → D6/J1
  - Check for `Frag needed` → D4
  - Check for `nf_conntrack.*table full` → D3
  - Check for DNS failures → D5

MAY:
- Use `quick_triage` tool with instanceId for a fast overview before deep-diving
- Use `cluster_health` tool with clusterName to check if degradation is node-specific or cluster-wide

## Phase 2 — Enrich

MUST:
- Classify the failure layer from Phase 1 findings:

  Layer 1 — NIC/Driver errors:
  - Use `search` tool with query=`rx_errors|tx_errors|rx_crc_errors|rx_missed_errors|carrier_errors` to find interface error counters
  - Use `search` tool with query=`ena.*version|modinfo ena` to check ENA driver version (< 2.8 may have bugs)
  - Use `search` tool with query=`ring buffer|rx_queue.*drop|tx_queue.*drop` to check ring buffer overflows

  Layer 2 — IRQ / CPU affinity:
  - Use `search` tool with query=`irqbalance|smp_affinity|RPS|XPS` to check IRQ distribution
  - Use `search` tool with query=`softirq.*NET_RX|ksoftirqd|cpu.*100` to check for softirq saturation on a single CPU

  Layer 3 — TCP stack:
  - Use `search` tool with query=`retransmit|TCPRetrans|TCPLoss|TCPTimeouts|TCPAbort` to find TCP retransmission stats from /proc/net/snmp or netstat
  - Use `search` tool with query=`tcp_rmem|tcp_wmem|somaxconn|backlog` to check TCP tuning parameters
  - Use `search` tool with query=`SYN.*drop|SYN.*overflow|listen.*overflow` to check for SYN queue overflow

  Layer 4 — Security rules / routing:
  - Use `network_diagnostics` iptables section to check for DROP/REJECT rules that may silently discard traffic
  - Use `network_diagnostics` routes section to check for blackhole routes or missing routes
  - Use `search` tool with query=`REJECT|DROP.*INPUT|DROP.*FORWARD|nflog` to find firewall drops
  - Use `search` tool with query=`NetworkPolicy|calico|cilium` to check for Kubernetes NetworkPolicy enforcement

- Use `correlate` tool with instanceId and pivotEvent set to the most prominent error pattern (e.g., `retransmit` or `rx_errors`) to build a timeline

SHOULD:
- Use `tcpdump_capture` tool with instanceId to capture live traffic if the issue is intermittent and log evidence is insufficient
  - For latency: capture on the affected pod interface or eth0
  - For packet loss: capture with a filter matching the affected traffic flow
- Use `tcpdump_analyze` tool to analyze the capture for retransmissions, resets, and latency patterns
- Use `search` tool with query=`nf_conntrack_count|nf_conntrack_max` to rule out conntrack pressure (even if not full, high utilization can cause slowness)

MAY:
- Use `compare_nodes` tool with instanceIds of affected + healthy node to find NIC/TCP differences
- Use `search` tool with query=`placement group|cluster placement` to check if instances are in a placement group (affects inter-node latency)

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from all network performance findings
- State root cause with the specific layer identified:
  - NIC errors → driver issue or hardware degradation
  - IRQ imbalance → softirq saturation on single CPU
  - TCP retransmissions → packet loss in path, security rule drops, or upstream issue
  - Security rule drops → iptables/NetworkPolicy blocking traffic
  - Route issue → missing or blackhole route
- Recommend targeted remediation (operator action — not available via MCP tools)
- Cross-reference: if ENA throttling found during investigation, redirect to D6/J1

SHOULD:
- Include specific counter values from search results (rx_errors, retransmit counts, drop counts)
- Include timeline from correlate showing when degradation started
- Include tcpdump analysis summary if capture was performed

MAY:
- Recommend TCP tuning parameters if TCP stack is the bottleneck
- Recommend placement group for latency-sensitive workloads
- Recommend ENA driver update if version is outdated

## Guardrails

escalation_conditions:
  - "NIC hardware errors (rx_crc_errors) increasing — possible hardware failure, request instance replacement"
  - "Packet loss on multiple nodes simultaneously — possible upstream network issue"
  - "Security group or NACL changes needed that affect other workloads"
  - "Issue persists after all node-level checks — may be VPC, TGW, or peering issue"
  - "tcpdump shows retransmissions but no local drops — loss is in the network path, not on the node"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics, correlate: GREEN (read-only)"
  - "tcpdump_capture, tcpdump_analyze: GREEN (read-only packet capture)"
  - "Modify TCP sysctl parameters: YELLOW — operator action, not available via MCP tools"
  - "Modify security groups / NACLs: YELLOW — operator action, not available via MCP tools"
  - "Replace instance (hardware errors): RED — operator action, requires approval"

## Common Issues

- symptoms: "search returns rx_errors or tx_errors > 0 and increasing"
  diagnosis: "NIC-level errors. Check ENA driver version and instance health. Use search with query=ena.*version."
  resolution: "Operator action: update ENA driver to latest. If errors persist, replace instance (possible hardware issue)."

- symptoms: "search returns high TCPRetransSegs or TCPTimeouts from /proc/net/snmp"
  diagnosis: "TCP retransmissions indicate packet loss in the network path. Use tcpdump_capture to identify where loss occurs."
  resolution: "If loss is on-node: check iptables DROP rules via network_diagnostics. If loss is off-node: escalate as VPC/upstream issue."

- symptoms: "network_diagnostics iptables section shows DROP rules on FORWARD chain"
  diagnosis: "Kubernetes NetworkPolicy or custom iptables rules dropping inter-pod traffic."
  resolution: "Operator action: review and adjust NetworkPolicy rules. Use search with query=NetworkPolicy to find applied policies."

- symptoms: "search returns ksoftirqd or NET_RX consuming 100% of one CPU"
  diagnosis: "IRQ affinity imbalance — all network interrupts handled by one CPU core."
  resolution: "Operator action: enable irqbalance service or configure RPS/XPS for multi-queue distribution."

- symptoms: "search returns SYN overflow or listen backlog drops"
  diagnosis: "TCP SYN queue overflow — too many incoming connections for the backlog size."
  resolution: "Operator action: increase net.core.somaxconn and net.ipv4.tcp_max_syn_backlog via sysctl."

- symptoms: "network_diagnostics routes section shows blackhole or missing route for pod CIDR"
  diagnosis: "Routing issue — traffic to certain pod CIDRs has no valid next hop."
  resolution: "Operator action: check VPC route tables and CNI routing. May need to restart aws-node DaemonSet."

- symptoms: "tcpdump_analyze shows retransmissions only for traffic leaving the VPC (cross-AZ or internet)"
  diagnosis: "Loss in the upstream path, not on the node. Node-level fixes will not help."
  resolution: "Escalate: check VPC peering, TGW, NAT gateway, or internet gateway health."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
status(executionId="<id-from-step-1>")

# Step 2: Rule out other D-series SOPs
search(instanceId="i-0abc123def456", query="allowance_exceeded")
# If nonzero → switch to D6/J1
search(instanceId="i-0abc123def456", query="Frag needed|message too long")
# If found → switch to D4
search(instanceId="i-0abc123def456", query="nf_conntrack.*table full")
# If found → switch to D3

# Step 3: Full network diagnostics
network_diagnostics(instanceId="i-0abc123def456", sections="eni,routes,iptables,cni,dns")

# Step 4: Check NIC errors
search(instanceId="i-0abc123def456", query="rx_errors|tx_errors|rx_dropped|tx_dropped|rx_crc")

# Step 5: Check TCP retransmissions
search(instanceId="i-0abc123def456", query="retransmit|TCPRetrans|TCPLoss|TCPTimeouts")

# Step 6: Check for firewall drops
search(instanceId="i-0abc123def456", query="DROP.*INPUT|DROP.*FORWARD|REJECT")

# Step 7: Check IRQ distribution
search(instanceId="i-0abc123def456", query="softirq.*NET_RX|ksoftirqd|irqbalance")

# Step 8: Capture traffic if still inconclusive
tcpdump_capture(instanceId="i-0abc123def456", interface="eth0", duration=30, filter="tcp")
tcpdump_analyze(instanceId="i-0abc123def456", captureId="<id-from-step-8>")

# Step 9: Correlate timeline
correlate(instanceId="i-0abc123def456", pivotEvent="retransmit", timeWindow=300)

# Step 10: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003","F-004"])
```

## Output Format

```yaml
root_cause: "<nic_errors|irq_imbalance|tcp_retransmissions|security_rule_drops|route_issue|upstream_loss>"
failure_layer: "<nic_driver|irq_cpu|tcp_stack|iptables_netpolicy|routing|upstream>"
evidence:
  - type: network_diagnostics
    content: "<interface stats, route table, iptables rules>"
  - type: search
    content: "<specific counter values — rx_errors, TCPRetransSegs, DROP rules>"
  - type: tcpdump_analyze
    content: "<retransmission count, reset count, latency distribution>"
  - type: correlate
    content: "<timeline showing onset of degradation>"
severity: HIGH
mitigation:
  immediate: "Operator: <layer-specific fix>"
  long_term: "Monitor NIC errors and TCP retransmissions, tune TCP stack, review NetworkPolicies"
cross_reference:
  - "D6/J1 if ENA allowance_exceeded found"
  - "D4 if MTU/fragmentation found"
  - "D3 if conntrack exhaustion found"
  - "D5 if DNS-specific failures found"
```
