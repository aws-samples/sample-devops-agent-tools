---
title: "D8 — kube-proxy Failures and Service Connectivity Issues"
description: "Comprehensive SOP for diagnosing kube-proxy crashes, IPVS/iptables mode issues, stale endpoints, service unreachable, and ClusterIP/NodePort/headless service failures"
status: active
severity: HIGH
triggers:
  - "kube-proxy.*CrashLoopBackOff"
  - "kube-proxy.*error"
  - "Failed to list.*Endpoints"
  - "error syncing iptables rules"
  - "KUBE-SVC chains missing"
  - "connection refused.*ClusterIP"
  - "no endpoints available"
  - "service.*unreachable"
  - "ipvs.*error"
  - "kube-proxy.*OOMKilled"
owner: devops-agent
objective: "Identify why kube-proxy is failing or service connectivity is broken, covering crash loops, mode misconfiguration, stale rules, version skew, and endpoint issues"
context: >
  kube-proxy runs as a DaemonSet on every node and maintains iptables or IPVS rules that map
  Service ClusterIPs/NodePorts to pod endpoints. When kube-proxy fails, services become unreachable
  even though individual pod IPs may work fine. This SOP covers the full range of kube-proxy issues:
  crash loops (OOM, config errors, API server connectivity), iptables mode problems (rule sync failures,
  stale chains, rule explosion in large clusters), IPVS mode problems (missing kernel modules, scheduler
  misconfiguration), stale endpoints (pods deleted but rules remain), version skew between kube-proxy
  and cluster, and service-level connectivity failures (ClusterIP, NodePort, headless, ExternalName).
  Cross-references D2 for basic iptables sync, D3 for conntrack exhaustion, D5 for DNS/CoreDNS issues.
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Verify the client pod is Running and Ready — if the pod is not running, connectivity failure is expected
  - Check the Service and its endpoints: `kubectl get svc <svc>` and `kubectl get endpoints <svc>` (via EKS MCP `read_k8s_resource`) — if endpoints list is empty, no backend pods are selected
  - Check backend pods are Running and Ready — if backends are down, the Service has no healthy endpoints
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — NotReady nodes break kube-proxy rule sync
- **PREREQUISITE — Does the Service have healthy endpoints?** Before investigating kube-proxy, verify the Service has backends:
  - Use `read_k8s_resource` with clusterName, kind=Endpoints, apiVersion=v1, namespace=<namespace>, name=<service-name> — if the subsets array is empty or has no addresses, there are no healthy backend pods. That is the root cause, not a kube-proxy issue.
  - If endpoints are empty: check if backend pods exist and are passing readiness probes. Report "Service has no healthy endpoints — backend pods are not Ready" immediately.
  - ONLY if the Service has healthy endpoints, proceed to kube-proxy investigation below.
- **PREREQUISITE — Is kube-proxy running?** Before investigating iptables/IPVS rules, verify kube-proxy is alive:
  - Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, namespace=kube-system, labelSelector=k8s-app=kube-proxy — check that kube-proxy pod on the affected node is Running.
  - If kube-proxy pod is CrashLoopBackOff, Error, or missing: that is the root cause. Report "kube-proxy not running on node — service rules will not be synced" immediately.
  - ONLY if kube-proxy is confirmed running, proceed to service connectivity investigation below.
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for kube-proxy errors
- Use `network_diagnostics` tool with instanceId and sections=iptables,kube_proxy to get current iptables/IPVS rules and kube-proxy status

SHOULD:
- Use `search` tool with instanceId and query=`kube-proxy.*error|kube-proxy.*fatal|kube-proxy.*crash|kube-proxy.*OOM` to find kube-proxy failure evidence
- Use `search` tool with query=`KUBE-SVC|KUBE-SEP|KUBE-MARK|KUBE-POSTROUTING` to check if service iptables chains exist
- Use `search` tool with query=`kube-proxy.*mode|mode.*iptables|mode.*ipvs` to determine which proxy mode is configured

MAY:
- Use `quick_triage` tool with instanceId for a fast overview
- Use `cluster_health` tool with clusterName to check if kube-proxy issues affect multiple nodes
- Use `compare_nodes` tool with instanceIds of affected + healthy node to diff kube-proxy state

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

Work through these failure domains in order:

### 2A — kube-proxy Pod Health

MUST:
- Use `search` tool with query=`kube-proxy.*CrashLoopBackOff|kube-proxy.*restart|kube-proxy.*OOMKilled|kube-proxy.*Error` to check pod status
  - CrashLoopBackOff: check for config errors, missing RBAC, or OOM
  - OOMKilled: kube-proxy running out of memory (common in large clusters with many services/iptables rules)
- Use `search` tool with query=`kube-proxy.*version|kube-proxy.*v1\.|image.*kube-proxy` to check kube-proxy version
  - Version must match cluster Kubernetes version (minor version skew of +/- 1 allowed)
  - Outdated kube-proxy addon can cause compatibility issues

SHOULD:
- Use `search` tool with query=`kube-proxy.*config|kube-proxy-config|ConfigMap.*kube-proxy` to check for configuration errors
- Use `search` tool with query=`kube-proxy.*serviceaccount|kube-proxy.*RBAC|kube-proxy.*forbidden` to check RBAC issues

### 2B — API Server Connectivity from kube-proxy

MUST:
- Use `search` tool with query=`kube-proxy.*connection refused|kube-proxy.*timeout|kube-proxy.*unauthorized|kube-proxy.*API` to check if kube-proxy can reach the API server
  - Connection refused/timeout: network issue between node and API server (see A4 SOP)
  - Unauthorized: service account token expired or RBAC misconfigured
- Use `search` tool with query=`Failed to list.*Service|Failed to list.*Endpoints|Failed to watch` to check if kube-proxy can list/watch services and endpoints
  - These failures mean kube-proxy cannot get updates, so rules become stale

### 2C — iptables Mode Issues

MUST:
- Review `network_diagnostics` iptables section for KUBE-SVC and KUBE-SEP chains
  - No KUBE-SVC chains at all: kube-proxy never synced or is not running
  - KUBE-SVC chains exist but point to wrong endpoints: stale rules from deleted pods
  - Very large number of rules (>10,000): iptables mode performance degradation in large clusters
- Use `search` tool with query=`error syncing iptables|iptables.*failed|iptables-restore|iptables.*lock` to find iptables sync errors
  - "iptables-restore: unable to initialize table": iptables binary issue or kernel module missing
  - "Another app is currently holding the xtables lock": contention with other iptables users (CNI, calico, etc.)

SHOULD:
- Use `search` tool with query=`syncProxyRules|SyncProxyRulesLatency|sync.*duration` to check sync performance
  - Sync taking > 1 second: too many rules, consider IPVS mode
- Use `search` tool with query=`masquerade|MASQUERADE|SNAT` to check masquerade rules for NodePort/LoadBalancer services

### 2D — IPVS Mode Issues

MUST (if IPVS mode detected):
- Use `search` tool with query=`ipvs.*error|ipvsadm|ip_vs.*module|IPVS` to find IPVS-specific errors
  - "can't load module ip_vs": IPVS kernel modules not loaded on the node
  - Required modules: ip_vs, ip_vs_rr, ip_vs_wrr, ip_vs_sh, nf_conntrack
- Use `search` tool with query=`ipvs.*scheduler|scheduler.*rr|scheduler.*lc` to check IPVS scheduler configuration
- Use `search` tool with query=`ipvsadm -L|ipvs.*TCP|ipvs.*UDP` to verify IPVS entries exist for services — after switching to IPVS, `ipvsadm -L` should show TCP/UDP entries for each Service ClusterIP

SHOULD:
- Use `search` tool with query=`ipvsadm -L|ip_vs_` to check if IPVS entries exist for services
- Use `search` tool with query=`kube-ipvs0|dummy.*interface` to check if the kube-ipvs0 dummy interface exists
  - Missing kube-ipvs0: IPVS mode not properly initialized
- Use `search` tool with query=`KUBE-SVC.*iptables|iptables.*KUBE-SVC` to check if stale iptables rules remain after switching to IPVS — old KUBE-SVC chains should be cleaned up after IPVS is confirmed working

### 2E — Stale Endpoints and Service Connectivity

MUST:
- Use `search` tool with query=`no endpoints available|endpoints.*not found|connection refused.*10\.|connection refused.*ClusterIP` to find service connectivity failures
  - "no endpoints available": service has no ready pods, or kube-proxy has stale endpoint list
- Use `search` tool with query=`stale.*endpoint|endpoint.*slice|EndpointSlice` to check for stale endpoint issues
- Use `network_diagnostics` iptables section — look for KUBE-SEP entries pointing to IPs of pods that no longer exist

SHOULD:
- Use `search` tool with query=`NodePort|nodePort|externalTrafficPolicy|healthCheckNodePort` to check NodePort service configuration
  - externalTrafficPolicy=Local with no local pods: service returns connection refused on that node
- Use `search` tool with query=`headless|clusterIP.*None` to check headless service issues
  - Headless services rely on DNS, not kube-proxy — redirect to D5 DNS SOP if headless service fails

### 2F — Conntrack Interaction

SHOULD:
- Use `search` tool with query=`nf_conntrack|conntrack.*table|conntrack.*drop` to check if conntrack issues are contributing to service failures
  - Conntrack table full causes new connections to be dropped even if kube-proxy rules are correct
  - If found: cross-reference D3 conntrack exhaustion SOP

### 2G — Control Plane kube-audit Logs

SHOULD:
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="Service" to check for recent Service create/update/delete events that may have changed ClusterIP, ports, or selectors
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="Endpoints" to check for Endpoints/EndpointSlice mutations that could cause stale endpoint issues
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="kube-proxy" to check for kube-proxy ConfigMap changes (mode switch, config updates)
- Correlate timestamps of Service/Endpoints mutations with the onset of connectivity failures

### Timeline Correlation

MUST:
- Use `correlate` tool with instanceId and pivotEvent set to the most prominent kube-proxy error to build a timeline

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from all kube-proxy and service connectivity findings
- State root cause with specific failure domain:
  - Pod health: CrashLoopBackOff, OOM, version skew
  - API connectivity: cannot list/watch services and endpoints
  - iptables: sync failure, lock contention, rule explosion
  - IPVS: missing kernel modules, scheduler misconfiguration, missing kube-ipvs0 interface
  - Stale endpoints: rules pointing to deleted pods
  - Service config: externalTrafficPolicy=Local with no local pods, headless service DNS issue
  - Conntrack: table full causing connection drops (cross-ref D3)
- Recommend specific fix (operator action — not available via MCP tools)

SHOULD:
- Include kube-proxy version and mode from search results
- Include evidence of missing/stale rules from network_diagnostics
- Include sync latency if available

MAY:
- Recommend IPVS mode for clusters with > 1,000 services to avoid iptables performance issues
- Recommend updating kube-proxy addon to match cluster version
- Recommend NodeLocal DNSCache for headless service DNS performance

## Guardrails

anti_hallucination:
  - "CRITICAL: POD_SECURITY_GROUP_ENFORCING_MODE (podSGEnforcingMode) does NOT create a default-deny for unannotated pods. It ONLY affects pods that have the vpc.amazonaws.com/pod-eni annotation (Security Groups for Pods). In strict mode, annotated pods use their branch ENI exclusively instead of falling back to the primary ENI. Pods WITHOUT SGP annotations are COMPLETELY UNAFFECTED — they use the primary ENI and normal VPC routing. Do NOT blame podSGEnforcingMode for service connectivity failures on pods without SGP annotations. If ClusterIP traffic is failing, check kube-proxy health and KUBE-SERVICES iptables chain FIRST."

escalation_conditions:
  - "kube-proxy CrashLoopBackOff on all nodes — cluster-wide service outage"
  - "kube-proxy cannot reach API server — check cluster endpoint and network"
  - "iptables-restore failing due to kernel module issue — node OS problem"
  - "IPVS mode switch caused service disruption — rollback to iptables mode"
  - "Stale endpoints persisting after kube-proxy restart — possible API server or etcd issue"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics, correlate, compare_nodes: GREEN (read-only)"
  - "Restart kube-proxy DaemonSet: YELLOW — operator action, not available via MCP tools"
  - "Switch kube-proxy mode (iptables to IPVS): RED — disruptive, operator action, requires off-hours"
  - "Update kube-proxy addon version: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "read_k8s_resource for Endpoints shows empty subsets or no addresses"
  diagnosis: "Service has no healthy backend pods. This is not a kube-proxy issue — the Service selector does not match any Running/Ready pods."
  resolution: "Operator action: verify pods matching the Service selector exist and are passing readiness probes. Check 'kubectl get pods -l <selector>' and 'kubectl describe pod' for readiness probe failures."

- symptoms: "list_k8s_resources returns kube-proxy pod in CrashLoopBackOff, Error, or missing on the affected node"
  diagnosis: "kube-proxy is not running. Service iptables/IPVS rules will not be synced on this node."
  resolution: "Operator action: check kube-proxy logs, restart kube-proxy DaemonSet, check kube-proxy-config ConfigMap."

- symptoms: "search returns kube-proxy CrashLoopBackOff or OOMKilled"
  diagnosis: "kube-proxy pod crashing. OOM common in large clusters (>1000 services) with iptables mode. Check kube-proxy logs for config errors."
  resolution: "Operator action: if OOM, increase kube-proxy memory limits or switch to IPVS mode. If config error, fix kube-proxy-config ConfigMap."

- symptoms: "search returns Failed to list Services or Failed to watch Endpoints"
  diagnosis: "kube-proxy cannot reach API server or RBAC is misconfigured. Rules become stale."
  resolution: "Operator action: check network connectivity to API server, verify kube-proxy service account has correct ClusterRole binding."

- symptoms: "network_diagnostics shows no KUBE-SVC chains in iptables"
  diagnosis: "kube-proxy is not running or never synced rules on this node."
  resolution: "Operator action: check kube-proxy DaemonSet status, restart kube-proxy pods (kubectl -n kube-system rollout restart ds kube-proxy)."

- symptoms: "search returns iptables-restore error or xtables lock contention"
  diagnosis: "iptables binary issue or lock contention with CNI (calico, cilium) or other iptables users."
  resolution: "Operator action: check for concurrent iptables users, increase iptables lock timeout, or switch to IPVS mode."

- symptoms: "search returns syncProxyRules taking > 1 second"
  diagnosis: "Too many iptables rules. iptables mode has O(n) performance — degrades with many services."
  resolution: "Operator action: switch to IPVS mode for O(1) lookup performance. See AWS docs for IPVS setup: install ipvsadm, load kernel modules, update kube-proxy addon config."

- symptoms: "IPVS mode configured but search returns 'can't load module ip_vs'"
  diagnosis: "IPVS kernel modules not loaded on the worker node. Required modules: ip_vs, ip_vs_rr, ip_vs_wrr, ip_vs_sh, nf_conntrack."
  resolution: "Operator action: install ipvsadm package, load kernel modules (modprobe ip_vs ip_vs_rr ip_vs_wrr ip_vs_sh nf_conntrack), persist in /etc/modules-load.d/ipvs.conf. For worker node bootstrap, add modprobe commands to user data."

- symptoms: "IPVS mode configured but search returns missing kube-ipvs0 dummy interface"
  diagnosis: "IPVS mode not properly initialized. The kube-ipvs0 dummy interface is required for IPVS to bind Service ClusterIPs."
  resolution: "Operator action: verify IPVS kernel modules are loaded, restart kube-proxy. The kube-ipvs0 interface is created automatically when kube-proxy starts in IPVS mode with all required modules."

- symptoms: "after switching to IPVS mode, search returns stale KUBE-SVC iptables entries"
  diagnosis: "After switching from iptables to IPVS mode, old iptables KUBE-SVC chains were not cleaned up. This can cause routing conflicts."
  resolution: "Operator action: after confirming IPVS is working (ipvsadm -L shows TCP/UDP entries for services), flush stale iptables rules. Restart kube-proxy pods to ensure clean state."

- symptoms: "search returns kube-proxy IPVS configuration via managed addon"
  diagnosis: "IPVS mode can be configured via the EKS managed kube-proxy addon using configuration values."
  resolution: "Operator action: update kube-proxy addon with IPVS config — aws eks update-addon --cluster-name <name> --addon-name kube-proxy --configuration-values '{\"ipvs\": {\"scheduler\": \"rr\"}, \"mode\": \"ipvs\"}'. Ensure worker nodes have ipvsadm installed and IPVS kernel modules loaded before switching."

- symptoms: "search returns 'no endpoints available' for a service"
  diagnosis: "Service has no ready pods, or kube-proxy has stale endpoint list. Check if pods are running and ready."
  resolution: "Operator action: verify pods are running and passing readiness probes. If pods are ready but endpoints missing, restart kube-proxy."

- symptoms: "NodePort service returns connection refused on some nodes but works on others"
  diagnosis: "externalTrafficPolicy=Local set on the service, and the node has no local pods for that service."
  resolution: "Operator action: change to externalTrafficPolicy=Cluster (adds extra hop but works on all nodes), or ensure pods are scheduled on all nodes via DaemonSet."

- symptoms: "kube-proxy version does not match cluster Kubernetes version"
  diagnosis: "Version skew between kube-proxy addon and cluster. Can cause compatibility issues with API changes."
  resolution: "Operator action: update kube-proxy addon to match cluster version via 'aws eks update-addon --cluster-name <name> --addon-name kube-proxy --addon-version <version>'."

- symptoms: "tcpdump shows the same source connecting to both a ClusterIP (10.x.x.x) and a different PodIP on the same destination port — appears as duplicate or phantom traffic"
  diagnosis: "THIS IS NORMAL BEHAVIOR. kube-proxy performs DNAT (Destination NAT) on ClusterIP Service traffic, rewriting the destination from the ClusterIP to a backend PodIP. tcpdump on the node captures BOTH the pre-DNAT packet (to ClusterIP) and the post-DNAT packet (to PodIP), making it look like the same source is talking to two different destinations. This is standard iptables/IPVS Service routing."
  resolution: "No action required — this is working as designed. If you need to trace a specific flow, filter tcpdump by the pod IP rather than the ClusterIP to see only the actual backend traffic."

- symptoms: "tcpdump shows TCP RST packets on ports 10250, 10256, 8080, or other health-check ports after very short-lived connections"
  diagnosis: "THIS IS NORMAL BEHAVIOR. Kubernetes liveness and readiness probes open a TCP connection to verify the port is listening, then immediately close it. This produces TCP RST packets. kubelet probes on port 10250, kube-proxy health on 10256, and application probes on 8080/443 all exhibit this pattern. See https://docs.aws.amazon.com/prescriptive-guidance/latest/ha-resiliency-amazon-eks-apps/probes-checks.html"
  resolution: "No action required — these RSTs are expected probe behavior, not connection failures. A high RST rate from probes alone is not a concern."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
status(executionId="<id-from-step-1>")

# Step 2: Get kube-proxy findings
errors(instanceId="i-0abc123def456")

# Step 3: Full network diagnostics including iptables and kube-proxy
network_diagnostics(instanceId="i-0abc123def456", sections="iptables,kube_proxy")

# Step 4: Check kube-proxy pod health
search(instanceId="i-0abc123def456", query="kube-proxy.*CrashLoopBackOff|kube-proxy.*OOMKilled|kube-proxy.*error")

# Step 5: Check kube-proxy mode and version
search(instanceId="i-0abc123def456", query="kube-proxy.*mode|mode.*iptables|mode.*ipvs|kube-proxy.*version")

# Step 6: Check API server connectivity
search(instanceId="i-0abc123def456", query="kube-proxy.*connection refused|Failed to list.*Service|Failed to watch")

# Step 7: Check iptables sync
search(instanceId="i-0abc123def456", query="error syncing iptables|iptables.*lock|syncProxyRules")

# Step 8: Check IPVS (if applicable)
search(instanceId="i-0abc123def456", query="ipvs.*error|ip_vs.*module|ipvsadm|kube-ipvs0")

# Step 9: Check stale endpoints
search(instanceId="i-0abc123def456", query="no endpoints available|stale.*endpoint|connection refused.*ClusterIP")

# Step 10: Correlate timeline
correlate(instanceId="i-0abc123def456", pivotEvent="kube-proxy", timeWindow=300)

# Step 11: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003"])
```

## Output Format

```yaml
root_cause: "<pod_crash|api_connectivity|iptables_sync|ipvs_modules|stale_endpoints|version_skew|service_config> — <specific detail>"
kube_proxy_mode: "<iptables|ipvs>"
kube_proxy_version: "<version from search>"
evidence:
  - type: network_diagnostics
    content: "<iptables rules / kube_proxy status>"
  - type: search
    content: "<kube-proxy error messages, sync latency, missing modules>"
  - type: correlate
    content: "<timeline of kube-proxy failures and service impact>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix based on root cause>"
  long_term: "Keep kube-proxy addon updated, consider IPVS for large clusters, monitor sync latency"
cross_reference:
  - "D2 for basic iptables sync issues"
  - "D3 if conntrack exhaustion contributing to service failures"
  - "D5 if headless service DNS resolution failing"
  - "I1 if kube-proxy version skew detected"
```
