---
title: "A3 — Clock Skew"
description: "Diagnose TLS/certificate failures caused by system clock drift on EKS worker nodes"
status: active
severity: HIGH
triggers:
  - "x509:.*not yet valid"
  - "clock skew detected"
  - "time is out of sync"
owner: devops-agent
objective: "Confirm clock skew as root cause of TLS failures and restore time synchronization"
context: "System clock drift causes TLS certificate validation to fail because certificates appear not-yet-valid or expired. This breaks kubelet-to-API-server communication and can affect all TLS-dependent services on the node."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — confirm the node is NotReady or showing clock-related issues
  - Check node details: `kubectl describe node <node>` (via EKS MCP `read_k8s_resource`) — look for x509 certificate errors or time-related conditions
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for pods failing with TLS or certificate errors
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- **PREREQUISITE — Is chronyd/ntpd running?** Before investigating clock drift values, verify the NTP service is alive:
  - Use `search` tool with instanceId and query=`Active: active \(running\)|chronyd.*started|chronyd.service.*running|ntpd.*running` and logTypes=`system` — if NO matches for any NTP service, the time sync daemon is stopped. That is the root cause.
  - Use `search` tool with instanceId and query=`chronyd.*inactive|chronyd.*failed|ntpd.*inactive|ntpd.*failed` — if matches found, NTP service is stopped. Report "NTP service (chronyd/ntpd) not running — clock will drift" as root cause.
  - ONLY if NTP service is confirmed running but clock is still drifting, proceed to clock skew investigation below.
- Use `errors` tool with instanceId to get pre-indexed findings — look for x509 or time-related errors
- Use `search` tool with instanceId and query=`x509.*not yet valid|clock skew|time.*out of sync` to find clock-related TLS failures

SHOULD:
- Use `search` tool with query=`chronyd|ntpd|timedatectl|time sync` to check NTP service status in collected logs
- Use `search` tool with query=`chrony.*offset|ntp.*offset` to find time offset values

MAY:
- Use `network_diagnostics` tool with instanceId and sections=dns to check if 169.254.169.123 (Amazon Time Sync) is reachable
- Use `cluster_health` tool with clusterName to check if multiple nodes have clock skew

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`x509` to build timeline around TLS failures
- Confirm clock drift by reviewing findings — look for "not yet valid" (clock behind) vs "expired" (clock ahead)
- Use `search` tool with query=`chronyd|ntpd` and logTypes=`system` to check NTP daemon status

SHOULD:
- Use `search` tool with query=`linklocal_allowance_exceeded` to check if ENA throttling is blocking NTP access to 169.254.169.123
- Use `errors` tool with severity=all to check for other time-dependent failures (token expiry, lease renewal)

MAY:
- Use `compare_nodes` tool to compare time-related findings between affected and healthy nodes

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from clock/TLS-related findings
- State root cause: clock skew causing TLS validation failure, with evidence from findings
- Recommend immediate fix: operator should force time sync (chronyc makestep)
- Confirm node should return to Ready after sync

SHOULD:
- Include time offset evidence from search results
- Include kubelet error showing TLS failure from findings

MAY:
- Recommend CloudWatch alarm for NTP sync status

## Guardrails

escalation_conditions:
  - "chronyd restart does not restore time sync"
  - "linklocal_allowance_exceeded preventing NTP access (see D6-ena-throttling SOP)"
  - "Multiple nodes with clock skew simultaneously"

safety_ratings:
  - "Log collection (collect), search, errors, correlate: GREEN (read-only)"
  - "Force time sync (chronyc makestep): YELLOW — operator action, not available via MCP tools"
  - "Restart chronyd: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "search for chronyd/ntpd service status returns Active: inactive or Active: failed, or no NTP service found"
  diagnosis: "NTP service is not running. Without time synchronization, the system clock will drift and cause TLS failures."
  resolution: "Operator action: systemctl enable --now chronyd. Verify Amazon Time Sync (169.254.169.123) is configured in /etc/chrony.conf."

- symptoms: "errors tool returns findings with x509: certificate not yet valid"
  diagnosis: "Node clock is behind actual time. Certificates appear to be in the future."
  resolution: "Operator action: chronyc makestep to force immediate sync, then systemctl restart chronyd"

- symptoms: "search for chronyd returns no matches or shows chronyd not running"
  diagnosis: "NTP service stopped or failed to start"
  resolution: "Operator action: systemctl enable --now chronyd and verify Amazon Time Sync is configured"

- symptoms: "search for chrony offset shows large offset values (>1 second) despite chronyd running"
  diagnosis: "Chrony is running but not syncing properly. Source may be unreachable or misconfigured."
  resolution: "Operator action: check chronyc sources -v for source status. Ensure Amazon Time Sync (169.254.169.123) is configured with 'prefer iburst minpoll 4 maxpoll 4' in chrony config. On AL2023 check /run/chrony.d/ and /etc/chrony.d/. On AL2 check /etc/chrony.d/."

- symptoms: "search returns ntpd references instead of chronyd"
  diagnosis: "Node is using ntpd instead of chrony. Best practice is chrony for faster sync and better accuracy."
  resolution: "Operator action: migrate from ntpd to chrony — systemctl disable --now ntpd && systemctl enable --now chronyd. Configure Amazon Time Sync as primary source."

- symptoms: "search for linklocal_allowance_exceeded shows nonzero values alongside clock skew"
  diagnosis: "ENA linklocal throttling is blocking NTP access to Amazon Time Sync (169.254.169.123). See D6-ena-throttling SOP."
  resolution: "Operator action: reduce linklocal traffic (deploy NodeLocal DNSCache, use IRSA instead of IMDS) or upgrade instance type. Add public fallback: 'pool time.aws.com iburst' in chrony config."

- symptoms: "search for IPv6 NTP or fd00:ec2::123 shows connection failures on Nitro instances"
  diagnosis: "IPv6 Amazon Time Sync endpoint (fd00:ec2::123) not reachable. May need IPv4 fallback."
  resolution: "Operator action: ensure 169.254.169.123 (IPv4) is configured as primary NTP source. IPv6 fd00:ec2::123 is available on Nitro instances as alternative."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Get time-related findings
errors(instanceId="i-0abc123def456")
# Step 3: Search for clock skew evidence
search(instanceId="i-0abc123def456", query="x509.*not yet valid|clock skew|time.*out of sync")
# Step 4: Check NTP status
search(instanceId="i-0abc123def456", query="chronyd|ntpd|timedatectl")
```

## Output Format

```yaml
root_cause: "Clock skew causing TLS validation failure"
evidence:
  - type: finding
    content: "<x509 not yet valid finding>"
  - type: ntp_search
    content: "<NTP status from search results>"
severity: HIGH
mitigation:
  immediate: "Operator: chronyc makestep && systemctl restart chronyd"
  long_term: "Ensure Amazon Time Sync (169.254.169.123) is configured in chrony.conf"
```
