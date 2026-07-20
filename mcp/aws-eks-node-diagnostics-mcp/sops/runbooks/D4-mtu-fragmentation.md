---
title: "D4 — MTU / Fragmentation Issues"
description: "Diagnose large packet drops and TLS failures caused by MTU mismatch"
status: active
severity: MEDIUM
triggers:
  - "Frag needed"
  - "message too long"
owner: devops-agent
objective: "Identify MTU mismatch between interfaces and restore large packet delivery"
context: "MTU mismatches between host interfaces (9001 jumbo) and pod interfaces can cause large packets to be dropped. TLS handshakes and large HTTP responses fail while small packets work fine."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for pods experiencing packet loss or connection issues
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for fragmentation errors
- Use `network_diagnostics` tool with instanceId and sections=routes,eni to get interface MTU values and route table from collected logs

SHOULD:
- Use `search` tool with instanceId and query=`Frag needed|message too long|PMTU|mtu` to find MTU-related errors
- Use `search` tool with query=`AWS_VPC_MTU_OVERRIDE|MTU` to check CNI MTU configuration

MAY:
- Use `tcpdump_capture` tool with instanceId to capture fragmentation events (if needed for deeper analysis)
- Use `tcpdump_analyze` tool to analyze captured packets for MTU issues

## Phase 2 — Enrich

MUST:
- Review `network_diagnostics` routes section for interface MTU values — compare pod interface MTU against expected (9001 jumbo or 1500 standard)
- Use `search` tool with query=`AWS_VPC_MTU_OVERRIDE` to check if MTU override is set on aws-node DaemonSet
- Use `search` tool with query=`ICMP.*Frag needed|icmp.*type 3` to verify ICMP is not blocked

SHOULD:
- Use `network_diagnostics` eni section to check ENI configuration
- Use `correlate` tool with instanceId and pivotEvent=`Frag needed` to correlate MTU issues with TLS failures

MAY:
- Use `compare_nodes` tool to compare MTU settings across nodes

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from MTU-related findings
- State root cause: MTU mismatch with specific interface values from network_diagnostics
- Recommend MTU fix (operator action)
- Confirm large packets should be delivered after fix

SHOULD:
- Include interface MTU values from network_diagnostics

MAY:
- Recommend consistent MTU policy across VPC

## Guardrails

escalation_conditions:
  - "MTU mismatch caused by VPC peering or Transit Gateway configuration"
  - "ICMP blocked by network policy that cannot be changed"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics, tcpdump_capture: GREEN (read-only)"
  - "Modify CNI MTU config: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "network_diagnostics shows pod interface MTU != host interface MTU, search returns TLS failures"
  diagnosis: "MTU mismatch causing large packet drops. PMTUD may be blocked."
  resolution: "Operator action: set AWS_VPC_MTU_OVERRIDE on aws-node DaemonSet. Ensure ICMP is not blocked."

- symptoms: "search returns 'Destination Unreachable: Fragmentation Needed' (ICMP Type 3, Code 4) in dmesg or tcpdump"
  diagnosis: "Path MTU Discovery is working — a device along the path has a smaller MTU than the packet size. The ICMP message instructs the sender to reduce packet size. If these messages are blocked by NACLs or security groups, PMTUD fails silently."
  resolution: "Operator action: ensure network ACLs allow ICMP Type 3 Code 4 (Fragmentation Needed) inbound and outbound. For IPv6, ensure ICMPv6 Type 2 (Packet Too Big) is allowed. See AWS docs on Path MTU Discovery."

- symptoms: "search returns TLS handshake failures or large HTTP response timeouts, but small requests (ping, DNS) work fine"
  diagnosis: "Classic MTU black hole — large packets are silently dropped because ICMP PMTUD messages are blocked. Small packets under the path MTU work fine."
  resolution: "Operator action: 1) Check NACLs allow ICMP Type 3 Code 4. 2) Set AWS_VPC_MTU_OVERRIDE=1500 on aws-node if using VPC peering or Transit Gateway (which may have lower MTU). 3) For cross-region or VPN traffic, MTU may be as low as 1300-1400."

- symptoms: "network_diagnostics shows host interface MTU=9001 (jumbo) but traffic crosses VPC peering, Transit Gateway, or VPN"
  diagnosis: "VPC peering, Transit Gateway, and VPN connections may not support jumbo frames (MTU 9001). Traffic crossing these boundaries gets fragmented or dropped."
  resolution: "Operator action: set AWS_VPC_MTU_OVERRIDE=1500 on aws-node DaemonSet for pods that communicate across VPC boundaries. Alternatively, set MTU at the pod level."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Get network diagnostics
network_diagnostics(instanceId="i-0abc123def456", sections="routes,eni")
# Step 3: Search for MTU issues
search(instanceId="i-0abc123def456", query="Frag needed|message too long|mtu")
# Step 4: Check CNI MTU config
search(instanceId="i-0abc123def456", query="AWS_VPC_MTU_OVERRIDE")
```

## Output Format

```yaml
root_cause: "MTU mismatch — <interface details from network_diagnostics>"
evidence:
  - type: network_diagnostics
    content: "<MTU values per interface>"
severity: MEDIUM
mitigation:
  immediate: "Operator: fix CNI MTU config via AWS_VPC_MTU_OVERRIDE"
  long_term: "Ensure consistent MTU across VPC, do not block ICMP"
```
