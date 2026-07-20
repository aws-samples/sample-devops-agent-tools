---
title: "D5 — DNS Failures from Pods"
description: "Diagnose DNS resolution failures affecting pod workloads"
status: active
severity: HIGH
triggers:
  - "UnknownHostException"
  - "Could not resolve host"
  - "SERVFAIL"
  - "linklocal_allowance_exceeded"
owner: devops-agent
objective: "Identify the DNS failure root cause and restore name resolution"
context: "Pod DNS failures can stem from CoreDNS overload, VPC DNS throttling (1024 PPS per ENI), ENA linklocal allowance exceeded, incorrect resolv.conf, ndots:5 amplification, or network policies blocking UDP 53."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Verify the affected pod is Running and Ready — DNS failures in a non-running pod are a symptom, not the cause
  - Check CoreDNS pods: `kubectl get pods -n kube-system -l k8s-app=kube-dns` (via EKS MCP `list_k8s_resources`) — if CoreDNS pods are not Running, that is the root cause
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — NotReady nodes cannot reach CoreDNS
- **PREREQUISITE — Check firewall rules blocking DNS (port 53)**: Before investigating DNS config or CoreDNS health, rule out firewall blocks:
  - Use `collect` tool with instanceId to gather logs from the affected node
  - Use `status` tool with executionId to poll until collection completes
  - Use `network_diagnostics` tool with instanceId and sections=iptables to get iptables rules
  - Use `search` tool with instanceId and query=`DROP.*53|REJECT.*53|DROP.*dns|REJECT.*dns|DROP.*coredns|iptables.*53.*DROP|iptables.*53.*REJECT` — if matches found, firewall rules are blocking DNS traffic. That is the root cause.
  - Use `search` tool with instanceId and query=`NetworkPolicy|network.*policy|calico.*deny|cilium.*deny` — check for Kubernetes NetworkPolicy or CNI policy rules that may block UDP/TCP 53
  - ONLY if no firewall blocks found, proceed to DNS config and CoreDNS investigation below.
- Use `errors` tool with instanceId to get pre-indexed findings — look for DNS errors
- Use `network_diagnostics` tool with instanceId and sections=dns to get DNS configuration from collected logs

SHOULD:
- Use `search` tool with instanceId and query=`UnknownHostException|Could not resolve|SERVFAIL|linklocal_allowance_exceeded` to find DNS failure evidence
- Use `search` tool with query=`resolv.conf|nameserver|ndots` to check DNS configuration
- Use `search` tool with query=`security group|sg-|port 53|UDP.*53|TCP.*53` to check if security groups allow DNS traffic (TCP/UDP port 53) from the pod CIDR range to CoreDNS pods

MAY:
- Use `search` tool with query=`ethtool.*linklocal|linklocal_allowance` to check ENA linklocal counters
- Use `cluster_health` tool with clusterName to check if DNS failures are cluster-wide

## Phase 2 — Enrich

MUST:
- Review `network_diagnostics` dns section for resolv.conf configuration and DNS issues
- Use `search` tool with query=`linklocal_allowance_exceeded` — if found, PPS throttling to VPC DNS (recommend NodeLocal DNSCache)
- Use `search` tool with query=`nameserver` in resolv.conf — if not kube-dns ClusterIP, bootstrap misconfiguration
- Use `search` tool with query=`CoreDNS|coredns.*error|coredns.*CrashLoop` to check CoreDNS health
- Use `search` tool with query=`OOM|oom-killer|out of memory|stress|memory.*exhaust` — check for memory exhaustion causing cascading failures including DNS. If found, DNS failure is a symptom, not the root cause — switch to A1 (OOM) SOP.

SHOULD:
- Use `correlate` tool with instanceId and pivotEvent=`DNS|resolve` to correlate DNS failures with other events
- Use `search` tool with query=`ndots` to check ndots setting (ndots:5 causes 4x query amplification)
- Use `search` tool with query=`NodeLocalDNS|node-local-dns|nodelocaldns|169.254.20.10` to check if NodeLocalDNS is installed and running — if installed but CrashLoopBackOff, check for port 53 conflicts
- Use `search` tool with query=`ipvs|IPVS|kube-proxy.*mode.*ipvs` to check if kube-proxy is in IPVS mode — IPVS mode requires manual pod DNS configuration to use NodeLocalDNS link-local address (169.254.20.10)

MAY:
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="coredns" to check for recent CoreDNS ConfigMap changes
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="NetworkPolicy" to check for NetworkPolicy changes blocking UDP 53
- When log-level DNS evidence is inconclusive (no clear iptables blocks, CoreDNS healthy, resolv.conf correct, but DNS still failing), suggest operator run tcpdump to capture live DNS traffic: `tcpdump -i any -nn port 53 -c 50` on the affected node. This can reveal packet drops, timeouts, or unexpected responses not visible in logs. Recommend capturing both UDP and TCP on port 53.

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from DNS-related findings
- State root cause: specific DNS failure mechanism with evidence
- Recommend targeted fix (operator action)
- Confirm DNS resolution should be restored after fix

SHOULD:
- Include linklocal counter values or CoreDNS error evidence from search results
- Recommend NodeLocal DNSCache if PPS throttling detected

MAY:
- Recommend reducing ndots in pod spec for external-heavy workloads

## Guardrails

escalation_conditions:
  - "CoreDNS pods CrashLooping and cannot be restarted"
  - "VPC DNS throttling affecting all nodes"
  - "DNS failures causing cascading application failures"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics: GREEN (read-only)"
  - "Deploy NodeLocal DNSCache, modify CoreDNS: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "search returns DROP.*53 or REJECT.*53 in iptables output"
  diagnosis: "Firewall rules (iptables/nftables) are blocking DNS traffic on port 53"
  resolution: "Operator action: remove the offending iptables rule — iptables -D <chain> <rule-spec>. Check for NetworkPolicy or security tooling that injected the rule."

- symptoms: "search returns NetworkPolicy deny rules affecting kube-dns or CoreDNS pods"
  diagnosis: "Kubernetes NetworkPolicy blocking UDP/TCP 53 to CoreDNS"
  resolution: "Operator action: update NetworkPolicy to allow egress to kube-dns on UDP/TCP 53"

- symptoms: "search returns linklocal_allowance_exceeded > 0"
  diagnosis: "PPS throttling to VPC DNS (169.254.169.253)"
  resolution: "Operator action: deploy NodeLocal DNSCache to reduce VPC DNS queries"

- symptoms: "network_diagnostics dns section shows wrong nameserver IP in resolv.conf"
  diagnosis: "Bootstrap misconfiguration — dns-cluster-ip wrong"
  resolution: "Operator action: fix --dns-cluster-ip in kubelet config to match kube-dns ClusterIP"

- symptoms: "search returns CoreDNS CrashLooping"
  diagnosis: "CoreDNS resource exhaustion or configuration error"
  resolution: "Operator action: scale CoreDNS replicas, increase memory limits, check Corefile"

- symptoms: "search returns NodeLocalDNS CrashLoopBackOff on EKS Auto Mode nodes or port 53 conflict"
  diagnosis: "NodeLocalDNS cannot bind to port 53 because another process (e.g., systemd-resolved on EKS Auto Mode nodes) is already listening on that port."
  resolution: "Operator action: configure NodeLocalDNS to use a different port or disable systemd-resolved on the node. On EKS Auto Mode nodes, NodeLocalDNS may not be compatible — use CoreDNS scaling instead."

- symptoms: "search returns IPVS mode and pods not using NodeLocalDNS link-local address"
  diagnosis: "When kube-proxy runs in IPVS mode, pods must be manually configured to use the NodeLocalDNS link-local address (169.254.20.10) because IPVS does not intercept traffic to the kube-dns ClusterIP the same way iptables does."
  resolution: "Operator action: update pod DNS config to use 169.254.20.10 as the nameserver, or configure NodeLocalDNS to listen on the kube-dns ClusterIP (requires disabling kube-dns service). See AWS docs for IPVS + NodeLocalDNS setup."

- symptoms: "search returns security group blocking TCP/UDP port 53 from pod CIDR"
  diagnosis: "Security group rules are blocking DNS traffic (TCP/UDP port 53) from the pod CIDR range to CoreDNS pods."
  resolution: "Operator action: update security groups to allow TCP and UDP port 53 from the pod CIDR range to the CoreDNS pod IPs or the node security group."

- symptoms: "tcpdump or DNS logs show queries with doubled/repeated domain suffixes such as 'myservice.mynamespace.svc.cluster.local.mynamespace.svc.cluster.local' — these queries return NXDomain but the final correct query succeeds"
  diagnosis: "THIS IS NORMAL BEHAVIOR — NOT A MISCONFIGURATION. With the default ndots:5 setting, the glibc resolver treats any name with fewer than 5 dots as 'not fully qualified' and appends each search domain from /etc/resolv.conf before trying the name as-is. A standard Kubernetes resolv.conf has search domains like 'mynamespace.svc.cluster.local svc.cluster.local cluster.local ec2.internal'. When a pod resolves 'myservice.mynamespace.svc.cluster.local' (4 dots, which is < ndots:5), the resolver first tries appending the first search domain, producing 'myservice.mynamespace.svc.cluster.local.mynamespace.svc.cluster.local'. This gets NXDomain, then the next search domain is tried, and so on until the correct resolution succeeds. This generates 3-5 extra NXDomain queries per lookup but is functionally correct and expected."
  resolution: "No action required — this is working as designed. If the extra DNS queries are causing performance concerns (CoreDNS load, latency), the operator can: (1) add a trailing dot to FQDNs in application config (e.g., 'myservice.mynamespace.svc.cluster.local.') to bypass search domain expansion entirely, (2) lower ndots to 2 in the pod spec dnsConfig, or (3) use short service names (e.g., 'myservice' or 'myservice.mynamespace') which resolve correctly on the first search domain attempt. See https://docs.aws.amazon.com/eks/latest/best-practices/scale-cluster-services.html"

- symptoms: "tcpdump shows a small number of DNS SERVFAIL responses, but DNS resolution generally works"
  diagnosis: "THIS IS NORMAL BEHAVIOR during CoreDNS scaling events. When CoreDNS pods scale down, there is a propagation delay for kube-proxy to update iptables rules. During this brief window, DNS queries may be routed to a terminating CoreDNS pod and receive SERVFAIL. The CoreDNS lameduck plugin mitigates this by delaying shutdown. A few SERVFAILs are transient and self-resolving."
  resolution: "No action required if the count is small (<10) and transient. If persistent, check CoreDNS health and ensure the lameduck plugin is configured in the Corefile. See https://docs.aws.amazon.com/eks/latest/best-practices/scale-cluster-services.html"

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Get DNS diagnostics
network_diagnostics(instanceId="i-0abc123def456", sections="dns")
# Step 3: Get DNS-related findings
errors(instanceId="i-0abc123def456")
# Step 4: Search for DNS failures
search(instanceId="i-0abc123def456", query="linklocal_allowance_exceeded|Could not resolve|SERVFAIL")
```

## Output Format

```yaml
root_cause: "<pps_throttling|coredns_overload|config_error|network_policy> — <detail>"
evidence:
  - type: network_diagnostics
    content: "<DNS configuration from dns section>"
  - type: finding
    content: "<DNS error finding>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix>"
  long_term: "Deploy NodeLocal DNSCache, reduce ndots, monitor DNS metrics"
```
