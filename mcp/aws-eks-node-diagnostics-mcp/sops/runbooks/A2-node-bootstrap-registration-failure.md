---
title: "A2 — Node Bootstrap / Registration Failure"
description: "Diagnose EKS worker node that never appears in kubectl get nodes after launch"
status: active
severity: HIGH
triggers:
  - "Unauthorized"
  - "Unable to register node"
  - "connect: connection refused"
  - "TLS handshake timeout"
owner: devops-agent
objective: "Identify why the node cannot register with the EKS API server and restore registration"
context: "Node instance is running in EC2 but never joins the cluster. Common causes include aws-auth misconfiguration, network connectivity issues to the API server, or bootstrap script failures."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — check if the node appears in the node list at all (unregistered nodes won't appear)
  - If node IS listed: check its status — it may be NotReady with registration errors
  - If node is NOT listed: confirms bootstrap/registration failure — proceed with log collection
- Use `collect` tool with instanceId to gather logs from the unregistered node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for Unauthorized, TLS, or bootstrap errors
- Use `search` tool with instanceId and query=`Unauthorized|Unable to register|connection refused|TLS handshake timeout` to find registration failure evidence

SHOULD:
- Use `search` tool with query=`cloud-init|bootstrap` to check bootstrap script output
- Use `cluster_health` tool with clusterName to verify cluster is healthy and accepting registrations

MAY:
- Use `network_diagnostics` tool with instanceId and sections=routes,dns to check network path to API server
- Use `compare_nodes` tool with instanceIds of failed node + a healthy registered node to diff findings

## Phase 2 — Enrich

MUST:
- Use `search` tool with query=`Unauthorized` — indicates aws-auth or access entry missing node role
- Use `search` tool with query=`TLS handshake timeout` — indicates SG/NACL blocking 443 to API server
- Use `search` tool with query=`Too Many Requests` — indicates API throttling during bootstrap
- Use `search` tool with query=`no route to host` — indicates subnet routing issue

SHOULD:
- Use `correlate` tool with instanceId to build timeline of bootstrap events and identify first failure point
- Use `search` tool with query=`aws-auth|iamidentitymapping` to check for auth configuration clues in logs

MAY:
- Use `network_diagnostics` tool with sections=dns to verify DNS resolution of API server endpoint
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="certificatesigningrequests" to check for node CSR requests and whether they were approved or denied — unapproved CSRs prevent node registration
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="aws-auth" to check for recent aws-auth ConfigMap changes that may have removed the node role mapping
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="access" to check for access entry mutations

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from registration-related findings
- State root cause with specific evidence (auth failure, network block, or bootstrap error)
- Recommend specific fix (operator action: update aws-auth, fix SG rules, or pass bootstrap args)
- List blast radius: single node or node group affected

SHOULD:
- Include relevant log excerpts from search results showing the failure
- Provide exact remediation steps for the operator

MAY:
- Recommend managed node groups for automatic aws-auth management
- Suggest passing --apiserver-endpoint, --b64-cluster-ca, --dns-cluster-ip to bootstrap script

## Guardrails

escalation_conditions:
  - "Node role is correctly mapped but still Unauthorized"
  - "API server endpoint unreachable from VPC (potential VPC endpoint issue)"
  - "Multiple nodes failing to register simultaneously (check via cluster_health)"

safety_ratings:
  - "Log collection (collect), search, errors, correlate: GREEN (read-only)"
  - "Update aws-auth ConfigMap: YELLOW — operator action, not available via MCP tools"
  - "Modify security groups: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "errors tool returns findings with Unauthorized in kubelet logs"
  diagnosis: "aws-auth ConfigMap or EKS access entry missing node IAM role ARN"
  resolution: "Operator action: add node role to aws-auth via eksctl create iamidentitymapping"

- symptoms: "search for TLS handshake timeout returns matches in kubelet logs"
  diagnosis: "Security group or NACL blocking port 443 to EKS API endpoint"
  resolution: "Operator action: update security group to allow outbound 443 to EKS API server CIDR"

- symptoms: "search for Too Many Requests returns matches in cloud-init logs"
  diagnosis: "API throttling during bootstrap DescribeCluster call"
  resolution: "Operator action: pass --apiserver-endpoint, --b64-cluster-ca, --dns-cluster-ip directly to bootstrap.sh"

## Examples

```
# Step 1: Collect logs from unregistered node
collect(instanceId="i-0abc123def456")
# Step 2: Check for registration errors
errors(instanceId="i-0abc123def456", severity="critical")
# Step 3: Search for specific failure patterns
search(instanceId="i-0abc123def456", query="Unauthorized|TLS handshake|connection refused")
# Step 4: Check network path
network_diagnostics(instanceId="i-0abc123def456", sections="routes,dns")
```

## Output Format

```yaml
root_cause: "<auth_failure|network_block|bootstrap_error> — <detail>"
evidence:
  - type: finding
    content: "<relevant error finding from errors tool>"
  - type: search_match
    content: "<relevant log line from search tool>"
blast_radius: "node (<instance-id>)"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix>"
  long_term: "Use managed node groups or pass bootstrap args to avoid API calls"
```
