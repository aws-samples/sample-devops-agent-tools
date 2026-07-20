---
title: "H3 — IMDS (Instance Metadata Service) Issues"
description: "Diagnose credential and metadata failures caused by IMDS accessibility issues"
status: active
severity: HIGH
triggers:
  - "Unable to retrieve.*metadata"
  - "169.254.169.254.*timed out"
  - "EC2MetadataError"
  - "failed to get credentials.*IMDS"
owner: devops-agent
objective: "Identify why IMDS is unreachable and restore metadata/credential access"
context: "IMDS provides instance metadata and temporary credentials. Pods cannot reach IMDS when hop limit is 1 (extra network hop from container), when network policies block 169.254.169.254, or when IMDS is disabled."
---

## Phase 1 — Triage

FIRST — Check node and pod state before collecting logs:
- Use `list_k8s_resources` with clusterName, kind=Node, apiVersion=v1 to list all nodes — check if the affected node is Ready and has valid conditions
- Use `read_k8s_resource` with clusterName, kind=Node, apiVersion=v1, name=<node-name> to get detailed node status — check providerID and node conditions for credential-related issues
- Use `list_k8s_resources` with clusterName, kind=Pod, apiVersion=v1, fieldSelector=spec.nodeName=<node-name> to list pods on the node — check for pods in CrashLoopBackOff or Error state due to IMDS/credential failures
- Use `get_k8s_events` with clusterName, kind=Node, name=<node-name> to check for credential or metadata-related warning events

MUST:
- Use `collect` tool with instanceId of the affected node to gather node-level logs
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to get pre-indexed IMDS/metadata findings
- Use `search` tool with instanceId and query=`169.254.169.254.*timed out|EC2MetadataError|Unable to retrieve.*metadata|failed to get credentials.*IMDS` to find IMDS errors

SHOULD:
- Use `search` tool with query=`HttpPutResponseHopLimit|http-put-response-hop-limit|metadata-options` to check IMDS hop limit configuration
- Use `network_diagnostics` tool with instanceId and sections=iptables to check for iptables rules blocking 169.254.169.254

MAY:
- Use `cluster_health` tool with clusterName to check if multiple nodes have IMDS issues
- Use `search` tool with query=`network policy|NetworkPolicy|169.254` to check for network policies blocking metadata endpoint

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`metadata` to build timeline of IMDS failures
- Review findings from `errors` tool and `network_diagnostics` to classify the failure:
  - If hop limit == 1: containers cannot reach IMDS (extra network hop) — needs increase to 2
  - If IMDS endpoint disabled: IMDS completely disabled — enable or use alternative credentials
  - If iptables shows DROP for 169.254.169.254: explicit block — review security policy
  - If IMDSv2 required but SDK too old: SDK does not support token flow
- Use `search` tool with query=`IMDSv2|http-tokens|HttpTokens` to check if IMDSv2 is required

SHOULD:
- Use `search` tool with query=`IRSA|pod-identity|service-account` to check if IRSA/Pod Identity is available as alternative
- Use `search` tool with query=`launch template|user-data|metadata` to check launch template IMDS settings

MAY:
- Use `compare_nodes` tool to check if IMDS issue affects all nodes or specific ones
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="NetworkPolicy" to check for recent NetworkPolicy changes that may be blocking traffic to 169.254.169.254 (IMDS endpoint)

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from IMDS-related findings to generate incident summary
- State root cause: specific IMDS accessibility issue with evidence from findings and network_diagnostics
- Recommend fix based on root cause classification
- Operator action — not available via MCP tools: increase hop limit, enable IMDS, remove iptables block, or configure IRSA

SHOULD:
- Include MetadataOptions showing hop limit and endpoint status from findings

MAY:
- Recommend IRSA/Pod Identity as preferred alternative to IMDS for pod credentials

## Guardrails

escalation_conditions:
  - "IMDS disabled by security policy and cannot be re-enabled"
  - "Hop limit change requires launch template update across all node groups"
  - "iptables IMDS block is intentional security control"

safety_ratings:
  - "Log collection (collect), search, errors, correlate, network_diagnostics: GREEN (read-only)"
  - "Increase hop limit: YELLOW — operator action, not available via MCP tools"
  - "Enable IMDS: YELLOW — operator action, not available via MCP tools"
  - "Remove iptables block: RED — operator action, may violate security policy"

## Common Issues

- symptoms: "search returns IMDS timeout from pods, errors tool shows metadata failures"
  diagnosis: "HttpPutResponseHopLimit is 1, containers need hop limit 2. Use search with query=HopLimit to confirm."
  resolution: "Operator action: aws ec2 modify-instance-metadata-options --instance-id <id> --http-put-response-hop-limit 2 --http-tokens required"

- symptoms: "search returns IMDS completely unreachable from node (not just pods)"
  diagnosis: "IMDS disabled on instance. Use search with query=HttpEndpoint to confirm."
  resolution: "Operator action: enable IMDS or use IRSA/Pod Identity for credentials"

- symptoms: "network_diagnostics shows iptables DROP rule for 169.254.169.254"
  diagnosis: "Explicit iptables rule blocking metadata endpoint."
  resolution: "Operator action: review security policy, remove rule if unintended"

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Poll status
status(executionId="<id-from-step-1>")
# Step 3: Get IMDS findings
errors(instanceId="i-0abc123def456", severity="high")
# Step 4: Search for IMDS errors
search(instanceId="i-0abc123def456", query="169.254.169.254.*timed out|EC2MetadataError|IMDS")
# Step 5: Check iptables for IMDS blocking
network_diagnostics(instanceId="i-0abc123def456", sections="iptables")
# Step 6: Check hop limit config
search(instanceId="i-0abc123def456", query="HttpPutResponseHopLimit|http-put-response-hop-limit")
# Step 7: Correlate IMDS failure timeline
correlate(instanceId="i-0abc123def456", pivotEvent="metadata", timeWindow=120)
# Step 8: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002"])
```

## Output Format

```yaml
root_cause: "<hop_limit|disabled|blocked|sdk_version> — <detail>"
evidence:
  - type: imds_finding
    content: "<IMDS failure finding from errors tool>"
  - type: network_diagnostics
    content: "iptables rules for 169.254.169.254 from network_diagnostics"
severity: HIGH
mitigation:
  immediate: "Operator: increase hop limit to 2 or remove iptables block"
  long_term: "Use IRSA/Pod Identity instead of IMDS, set hop limit 2 in launch templates"
```