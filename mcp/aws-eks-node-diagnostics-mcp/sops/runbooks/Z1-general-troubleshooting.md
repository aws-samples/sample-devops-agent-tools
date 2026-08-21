---
title: "Z1 — General Troubleshooting (Catch-All)"
description: "Fallback SOP for issues that do not match any specific runbook. Provides a structured investigation flow using all available MCP tools to classify the failure domain and match to an existing SOP."
status: active
severity: MEDIUM
triggers:
  - ".*"
owner: devops-agent
objective: "Systematically investigate an unknown node issue using MCP tools, classify the failure domain, and either match to an existing SOP or escalate with full evidence"
context: "This SOP is invoked when the reported symptoms do not match any of the 41 specific runbooks (A1–J3, D7–D9, K1–K5). It provides a broad, methodical investigation that narrows the failure domain step by step. If the issue remains unclassified after enrichment, escalate to the operator with all collected evidence."
---

## Phase 1 — Triage

FIRST — Check node and pod state before any log collection:
- Use `list_k8s_resources` with clusterName, kind=Node, apiVersion=v1 to list all nodes — check Ready/NotReady status, conditions (DiskPressure, MemoryPressure, PIDPressure, NetworkUnavailable), and identify the affected node
- Use `read_k8s_resource` with clusterName, kind=Node, apiVersion=v1, name=<node-name> to get detailed node conditions, capacity, allocatable resources, and node info (kubelet version, OS, container runtime)
- Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, fieldSelector=spec.nodeName=<node-name> to list all pods on the affected node — check for pods in CrashLoopBackOff, Error, ImagePullBackOff, Pending, or ContainerCreating state
- Use `get_k8s_events` with clusterName, kind=Node, name=<node-name> to check for recent warning events that may indicate the failure domain

MUST:
- Use `quick_triage` tool with instanceId to get a one-shot validate + errors + summarize overview of the node
- Review the quick_triage output to check if any findings match a known SOP trigger pattern:
  - OOM/memory → A1, G2
  - NotReady → A1, A2
  - Certificate → A2
  - Clock/NTP → A3
  - Kubelet config → B1
  - Eviction → B2, G1
  - PLEG → B3
  - Image pull → C1
  - Sandbox → C2
  - Inode → C3
  - VPC CNI/IP → D1
  - kube-proxy/iptables → D2
  - Conntrack → D3
  - MTU → D4
  - DNS → D5
  - ENA throttling → D6, J1
  - Scheduling/capacity → F1, F2, F3
  - Disk pressure → G1
  - PID pressure → G3
  - IAM/AccessDenied → H1, H2
  - IMDS → H3
  - Version skew → I1
  - EBS attach → J2
  - Stuck Terminating pods/finalizers → K1
  - Probe failures (liveness/readiness/startup) → K2
  - CrashLoopBackOff → K3
  - Containerd/runtime failures → K4
  - CSI node plugin not running/registration → K5
- If a match is found: switch to that specific SOP immediately
- If no match: continue to broad collection below

SHOULD:
- Use `collect` tool with instanceId to start full log collection
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=all to get the complete findings list (not just critical/high)

MAY:
- Use `cluster_health` tool with clusterName to check if the issue is node-specific or cluster-wide

## Phase 2 — Enrich

MUST:
- **PREREQUISITE HEALTH CHECKS — Verify basic service health before rationalizing symptoms**:
  - Is kubelet running? Use `search` with query=`Active: active.*kubelet|kubelet.service.*running` — if no matches, kubelet is stopped. That is the root cause.
  - Is kubelet stopped/failed? Use `search` with query=`Active: inactive|Active: failed|kubelet.service.*dead` — if matches, report kubelet down before investigating further.
  - Are critical add-ons installed? Use `list_k8s_resources` with kind=DaemonSet, namespace=kube-system to verify CoreDNS, kube-proxy, VPC CNI, and EBS CSI driver are present. Missing add-ons are root causes, not symptoms.
  - Are there firewall rules blocking traffic? Use `search` with query=`DROP|REJECT` in iptables output — firewall blocks explain connectivity failures more directly than application-level errors.
  - Is there memory/resource exhaustion? Use `search` with query=`OOM|oom-killer|out of memory|stress|memory.*exhaust` — resource exhaustion causes cascading failures. If found, it is likely the root cause, not the downstream symptoms.
  - **RULE: Always verify the simplest explanation first. A stopped service, missing driver, or firewall block is more likely the root cause than a complex chain of transient conditions.**
- Use `search` tool with instanceId and query=`error|Error|ERROR|fail|Fail|FAIL|fatal|Fatal|FATAL` to cast a wide net for error signals
- Use `search` tool with query=`warning|Warning|WARNING` to find warning-level signals that may indicate the root cause
- Classify the failure domain from search results:
  - Kernel/OS level: dmesg errors, kernel panics, hardware errors
  - Kubelet level: kubelet crashes, config errors, API server connectivity
  - Container runtime: containerd errors, sandbox failures, image issues
  - Networking: CNI, DNS, kube-proxy, ENA, connectivity
  - Storage: EBS, EFS, disk, inode
  - IAM/Security: AccessDenied, credentials, IMDS, IRSA
  - Scheduling: capacity, taints, affinity, version skew
- Use `correlate` tool with instanceId and pivotEvent set to the most prominent error pattern found above

SHOULD:
- Use `network_diagnostics` tool with instanceId and sections=iptables,cni,routes,dns,eni to rule out networking issues
- Use `storage_diagnostics` tool with instanceId and sections=kubelet,ebs_csi,efs_csi,instance to rule out storage issues
- Use `search` tool with query=`restart|crash|segfault|panic|backtrace` to check for process crashes

MAY:
- Use `compare_nodes` tool with instanceIds of affected + healthy node to find what differs
- Use `tcpdump_capture` tool if networking is suspected but network_diagnostics is inconclusive
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="error" to check kube-audit logs for recent API errors, denied requests, or failed mutations that may correlate with the issue
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="Forbidden" to check for RBAC denials in the audit log that may indicate permission issues

## Phase 3 — Classify or Escalate

MUST:
- If Phase 2 narrowed the failure domain to a known category: re-check SOP trigger patterns and switch to the matching SOP
- Use `list_sops` tool to review all available SOPs and find the closest match based on failure domain
- Use `get_sop` tool with the matched SOP name to retrieve the full procedure and follow it
- If the issue remains unclassified after Phase 2: escalate to the operator with all collected evidence
- Use `summarize` tool with instanceId and finding_ids from all relevant findings to generate the final incident summary

SHOULD:
- Include all evidence collected across Phases 1–2 in the summary
- Clearly state whether the issue was resolved via an existing SOP or requires operator escalation

MAY:
- Recommend creating a new permanent SOP if this issue type is likely to recur

## Phase 4 — Report

MUST:
- State the investigation path taken: quick_triage → broad search → domain classification → (SOP match OR generated runbook)
- State root cause if identified, or state "unclassified" with the best hypothesis and supporting evidence
- List all MCP tools used and key findings from each
- Recommend next steps:
  - If root cause found: specific remediation (operator action — not available via MCP tools)
  - If unclassified: escalate to human operator with the full evidence bundle

SHOULD:
- Include the failure domain classification (kernel, kubelet, runtime, network, storage, IAM, scheduling, or unknown)
- Include timeline from correlate tool

MAY:
- Recommend adding a new SOP for this issue type if it represents a gap in coverage
- Recommend additional monitoring or alerting for the identified failure pattern

## Guardrails

escalation_conditions:
  - "Issue remains unclassified after full Phase 2 investigation"
  - "Multiple failure domains involved simultaneously"
  - "Node is completely unresponsive (collect tool fails)"
  - "Issue affects multiple nodes — check via cluster_health"

safety_ratings:
  - "All MCP tools used in this SOP: GREEN (read-only)"
  - "Any remediation actions: YELLOW/RED — operator action, not available via MCP tools"

## Common Issues

- symptoms: "quick_triage returns no critical or high findings"
  diagnosis: "Issue may be intermittent or already resolved. Use search with broad error patterns to find historical evidence."
  resolution: "Widen time window in correlate, check for warning-level findings in errors tool with severity=all"

- symptoms: "collect tool fails or times out"
  diagnosis: "Node may be unreachable via SSM. Check SSM agent status and network connectivity."
  resolution: "Escalate to operator — node may need direct SSH access or replacement"

- symptoms: "search returns errors across multiple domains (network + storage + kubelet)"
  diagnosis: "Cascading failure — one root cause triggering multiple symptoms. Use correlate to find the earliest event."
  resolution: "Focus on the earliest error in the timeline as the likely root cause, then follow the matching domain SOP"

- symptoms: "No existing SOP matches the failure domain after full investigation"
  diagnosis: "Issue is outside the scope of node-level log collection (e.g., control plane, AWS service issue) or represents a novel failure mode."
  resolution: "Escalate to operator with all evidence collected so far. Recommend creating a new SOP for this issue type."

## Examples

```
# Step 1: Quick triage for fast SOP matching
quick_triage(instanceId="i-0abc123def456")
# Step 2: No SOP match — broad collection
collect(instanceId="i-0abc123def456")
status(executionId="<id-from-step-2>")
# Step 3: Get all findings
errors(instanceId="i-0abc123def456", severity="all")
# Step 4: Broad error search
search(instanceId="i-0abc123def456", query="error|Error|ERROR|fail|Fail|FAIL")
# Step 5: Check networking
network_diagnostics(instanceId="i-0abc123def456", sections="iptables,cni,routes,dns,eni")
# Step 6: Check storage
storage_diagnostics(instanceId="i-0abc123def456", sections="kubelet,ebs_csi,efs_csi,instance")
# Step 7: Correlate around earliest error
correlate(instanceId="i-0abc123def456", pivotEvent="<earliest-error-pattern>", timeWindow=300)
# Step 8: Still unclassified — check available SOPs for closest match
list_sops()
# Returns: all 36 SOPs with titles, triggers, severity
get_sop(sopName="<closest-match-sop>")
# Step 9: Final summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003","F-004"])
```

## Output Format

```yaml
root_cause: "<identified_cause OR unclassified>"
failure_domain: "<kernel|kubelet|runtime|network|storage|iam|scheduling|unknown>"
investigation_path: "quick_triage → broad_search → <domain_classification> → <sop_match|generated_runbook|escalation>"
evidence:
  - type: quick_triage
    content: "<summary from quick_triage>"
  - type: error_search
    content: "<key error patterns found>"
  - type: domain_diagnostics
    content: "<network_diagnostics or storage_diagnostics results>"
  - type: correlation
    content: "<timeline from correlate>"
severity: MEDIUM
mitigation:
  immediate: "Operator: <specific action if root cause found, or escalate with evidence>"
  long_term: "Create new SOP for this issue type if recurring"
```