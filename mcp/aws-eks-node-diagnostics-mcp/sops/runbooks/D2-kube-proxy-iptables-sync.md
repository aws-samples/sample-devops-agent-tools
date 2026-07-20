---
title: "D2 — kube-proxy iptables/IPVS Sync Issues"
description: "Diagnose service connectivity failures caused by kube-proxy not syncing iptables or IPVS rules"
status: active
severity: HIGH
triggers:
  - "Failed to list *v1.Endpoints"
  - "error syncing iptables rules"
  - "KUBE-SVC chains missing"
owner: devops-agent
objective: "Identify why kube-proxy is not syncing service rules and restore service connectivity"
context: "kube-proxy maintains iptables or IPVS rules that map Service ClusterIPs to pod endpoints. When sync fails, services become unreachable even though individual pod IPs work."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for pods with connectivity issues
  - Check kube-proxy pods: `kubectl get pods -n kube-system -l k8s-app=kube-proxy` (via EKS MCP `list_k8s_resources`) — if kube-proxy is not Running or CrashLoopBackOff, iptables rules won't sync
- **PREREQUISITE — Is kube-proxy running?** Before investigating iptables sync, verify kube-proxy is alive:
  - Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, namespace=kube-system, labelSelector=k8s-app=kube-proxy — check that kube-proxy pod on the affected node is Running.
  - If kube-proxy pod is CrashLoopBackOff, Error, or missing: that is the root cause. Report "kube-proxy not running on node — iptables/IPVS rules will not be synced" immediately.
  - ONLY if kube-proxy is confirmed running, proceed to sync investigation below.
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for kube-proxy errors
- Use `network_diagnostics` tool with instanceId and sections=iptables,kube_proxy to get iptables rules and kube-proxy status

SHOULD:
- Use `search` tool with instanceId and query=`kube-proxy.*error|Failed to list.*Endpoints|error syncing iptables` to find kube-proxy failure evidence
- Use `search` tool with query=`KUBE-SVC|KUBE-SEP` to check if service chain rules exist in iptables output

MAY:
- Use `search` tool with query=`ipvs|ipvsadm` to check IPVS rules if in IPVS mode
- Use `cluster_health` tool with clusterName to check if multiple nodes have kube-proxy issues

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

MUST:
- Review `network_diagnostics` output for iptables section — check if KUBE-SVC rules exist
- Use `search` tool with query=`kube-proxy.*API|connection refused|unauthorized` to check API server connectivity from kube-proxy
- Use `correlate` tool with instanceId and pivotEvent=`kube-proxy` to build timeline of sync failures

SHOULD:
- Review `network_diagnostics` kube_proxy section for mode (iptables vs IPVS) and error patterns
- Use `search` tool with query=`conntrack|nf_conntrack` to check if conntrack issues are contributing

MAY:
- Use `compare_nodes` tool to compare kube-proxy findings between affected and healthy nodes

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from kube-proxy-related findings
- State root cause: kube-proxy not syncing due to API connectivity, crash, or config error
- Recommend fix (operator action): restart kube-proxy or fix connectivity
- Confirm services should be reachable after fix

SHOULD:
- Include evidence of missing KUBE-SVC rules from network_diagnostics
- Include sync error evidence from search results

MAY:
- Recommend monitoring kube-proxy health

## Guardrails

anti_hallucination:
  - "CRITICAL: POD_SECURITY_GROUP_ENFORCING_MODE (podSGEnforcingMode) does NOT create a default-deny for unannotated pods. It ONLY affects pods that have the vpc.amazonaws.com/pod-eni annotation (Security Groups for Pods). In strict mode, annotated pods use their branch ENI exclusively instead of falling back to the primary ENI. Pods WITHOUT SGP annotations are COMPLETELY UNAFFECTED — they use the primary ENI and normal VPC routing. Do NOT blame podSGEnforcingMode for connectivity failures on pods without SGP annotations. If ClusterIP traffic is failing, check kube-proxy health and KUBE-SERVICES iptables chain FIRST."

escalation_conditions:
  - "kube-proxy restart does not restore service rules"
  - "API server unreachable from kube-proxy pods"
  - "All nodes missing service iptables rules"

safety_ratings:
  - "Log collection (collect), search, errors, network_diagnostics: GREEN (read-only)"
  - "Restart kube-proxy pods: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "list_k8s_resources returns kube-proxy pod in CrashLoopBackOff, Error, or missing on the affected node"
  diagnosis: "kube-proxy is not running. No iptables/IPVS rules will be synced on this node."
  resolution: "Operator action: check kube-proxy logs (kubectl logs -n kube-system -l k8s-app=kube-proxy). Common fixes: restart kube-proxy DaemonSet, check kube-proxy-config ConfigMap, verify RBAC."

- symptoms: "network_diagnostics iptables section shows no KUBE-SVC rules"
  diagnosis: "kube-proxy not running or not syncing rules"
  resolution: "Operator action: restart kube-proxy pods (kubectl delete pods -n kube-system -l k8s-app=kube-proxy)"

- symptoms: "search returns kube-proxy API connection errors"
  diagnosis: "kube-proxy cannot reach API server to get service/endpoint updates"
  resolution: "Operator action: check network connectivity, security groups, and kube-proxy service account permissions"

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Get network diagnostics
network_diagnostics(instanceId="i-0abc123def456", sections="iptables,kube_proxy")
# Step 3: Get kube-proxy findings
errors(instanceId="i-0abc123def456")
# Step 4: Search for sync errors
search(instanceId="i-0abc123def456", query="kube-proxy.*error|error syncing iptables")
```

## Output Format

```yaml
root_cause: "kube-proxy sync failure — <api_connectivity|crash|config_error>"
evidence:
  - type: network_diagnostics
    content: "<iptables/kube_proxy section showing missing rules or errors>"
severity: HIGH
mitigation:
  immediate: "Operator: restart kube-proxy pods"
  long_term: "Monitor kube-proxy health, ensure API server connectivity"
```
