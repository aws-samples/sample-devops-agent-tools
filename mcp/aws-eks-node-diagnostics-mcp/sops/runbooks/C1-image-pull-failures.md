---
title: "C1 — Image Pull Failures (ImagePullBackOff)"
description: "Diagnose pods stuck in ImagePullBackOff or ErrImagePull"
status: active
severity: HIGH
triggers:
  - "failed to pull and unpack image"
  - "401 Unauthorized"
  - "403 Forbidden"
  - "failed to resolve reference"
  - "no space left on device"
owner: devops-agent
objective: "Identify why container images cannot be pulled and restore pod scheduling"
context: "Image pull failures prevent pods from starting. Causes range from auth issues (ECR token expired, missing imagePullSecrets) to network problems (DNS, SG blocking registry) to disk space exhaustion."
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and node state before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`)
  - Check pod status — ImagePullBackOff or ErrImagePull confirms this is the right SOP
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) for the exact image pull error message (auth failure, not found, timeout)
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — verify the node is Ready
- **PREREQUISITE — Is containerd running?** Before investigating image pull failures, verify the container runtime is alive:
  - Use `collect` tool with instanceId to gather logs from the affected node
  - Use `status` tool with executionId to poll until collection completes
  - Use `search` tool with instanceId and query=`Active: active \(running\)|containerd.*started|containerd.service.*running` and logTypes=`containerd` — if NO matches, containerd is stopped/dead. That is the root cause, not an image pull issue.
  - Use `search` tool with instanceId and query=`Active: inactive|Active: failed|containerd.service.*dead|containerd.service.*failed` — if matches found, containerd is stopped. Report "containerd service not running — image pulls impossible" as root cause.
  - ONLY if containerd is confirmed running, proceed to image pull investigation below.
- **PREREQUISITE — Does the image exist?** Before investigating auth or network, check if the image reference is valid:
  - Use `search` tool with instanceId and query=`manifest unknown|not found.*manifest|404.*manifest|repository.*not found|name unknown` — if matches found, the image name or tag does not exist in the registry. That is the root cause. Report "image not found in registry — verify image name and tag" immediately.
  - ONLY if no "not found" errors, proceed to auth/network investigation below.
- Use `errors` tool with instanceId to get pre-indexed findings — look for image pull errors
- Use `search` tool with instanceId and query=`failed to pull|401 Unauthorized|403 Forbidden|failed to resolve reference|no space left` to find image pull failure evidence

SHOULD:
- Use `search` tool with query=`ecr.*credential|imagePullSecret|registry.*auth` to check auth configuration
- Use `storage_diagnostics` tool with instanceId to check disk space from collected logs
- Use `search` tool with query=`toomanyrequests|rate limit|429|Too Many Requests` to check for Docker Hub or registry rate limiting

MAY:
- Use `network_diagnostics` tool with instanceId and sections=dns to check DNS resolution for registry endpoints
- Use `search` tool with query=`IMDS|hop-limit|169.254.169.254` to check IMDS accessibility for ECR credential helper
- Use `search` tool with query=`vpce-|vpc.*endpoint|ecr.api|ecr.dkr` to check for VPC endpoints (required for private clusters pulling from ECR)

## Phase 2 — Enrich

MUST:
- Use `search` tool with query=`401|403` — auth issue (IRSA, node role, imagePullSecrets)
- Use `search` tool with query=`i/o timeout|dial tcp.*timeout` — DNS or network connectivity issue
- Use `search` tool with query=`no space left on device` — disk full (cross-ref with storage_diagnostics)
- Use `search` tool with query=`failed to resolve reference|not found|manifest unknown` — invalid image name/tag

SHOULD:
- Use `correlate` tool with instanceId and pivotEvent=`failed to pull` to build timeline of pull failures
- Use `search` tool with query=`x509.*certificate` to check for proxy/firewall TLS interception issues

MAY:
- Use `compare_nodes` tool to check if image pull failures are node-specific or cluster-wide

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from image-pull-related findings
- State root cause: specific pull failure reason with evidence
- Recommend targeted fix based on failure type (operator action)
- Confirm pods should transition to Running after fix

SHOULD:
- Include the specific error message from findings
- Provide exact remediation steps for the operator

MAY:
- Recommend IRSA for ECR access instead of node role
- Recommend proper IMDS hop limits for containerized workloads

## Guardrails

escalation_conditions:
  - "All pods on node failing to pull images (node-wide issue)"
  - "ECR service endpoint unreachable (potential AWS service issue)"
  - "x509 certificate errors for ECR (potential proxy/firewall issue)"

safety_ratings:
  - "Log collection (collect), search, errors, storage_diagnostics, network_diagnostics: GREEN (read-only)"
  - "Modify IAM roles, imagePullSecrets: YELLOW — operator action, not available via MCP tools"

## Common Issues

- symptoms: "search for containerd service status returns Active: inactive or Active: failed"
  diagnosis: "Containerd is stopped. No images can be pulled without the container runtime."
  resolution: "Operator action: check journalctl -u containerd for startup errors, then restart containerd (systemctl restart containerd)."

- symptoms: "search returns manifest unknown or repository not found"
  diagnosis: "Image name or tag does not exist in the registry. Typo in image reference or tag was never pushed."
  resolution: "Operator action: verify image name and tag. Check ECR repository exists and the tag was pushed. Use 'aws ecr describe-images' to list available tags."

- symptoms: "errors tool returns findings with 401 Unauthorized or 403 Forbidden"
  diagnosis: "ECR auth failure. Node role missing registry permissions or ECR token expired."
  resolution: "Operator action: verify node role has AmazonEC2ContainerRegistryReadOnly. For cross-account: configure ECR repository policy."

- symptoms: "search for i/o timeout returns matches"
  diagnosis: "Network cannot reach container registry. DNS or SG issue."
  resolution: "Operator action: check DNS resolution, verify SG allows outbound 443 to registry endpoint."

- symptoms: "storage_diagnostics shows disk full, search confirms no space left on device"
  diagnosis: "Root volume full, cannot store pulled image layers."
  resolution: "Operator action: prune unused images (crictl rmi --prune), increase root volume size."

- symptoms: "search for IMDS hop-limit shows value of 1"
  diagnosis: "IMDS hop limit is 1, pods cannot get ECR credentials via IMDS."
  resolution: "Operator action: increase IMDS hop limit to 2 or use IRSA for ECR access."

- symptoms: "search returns toomanyrequests, rate limit, or 429 Too Many Requests"
  diagnosis: "Docker Hub rate limit exceeded. Anonymous pulls are limited to 100 pulls/6h, authenticated to 200 pulls/6h."
  resolution: "Operator action: use ECR Public or a pull-through cache to avoid Docker Hub rate limits. Alternatively, authenticate Docker Hub pulls with imagePullSecrets containing a Docker Hub token."

- symptoms: "search returns ECR repository policy AccessDenied or cross-account pull failure"
  diagnosis: "ECR repository policy does not allow the node role or cross-account role to pull images. The repository-level policy is separate from the IAM policy on the node role."
  resolution: "Operator action: update the ECR repository policy to allow ecr:GetDownloadUrlForLayer, ecr:BatchGetImage, ecr:BatchCheckLayerAvailability for the node role or cross-account principal. Use 'aws ecr set-repository-policy'."

- symptoms: "search returns vpce- or VPC endpoint errors, or private cluster cannot reach ECR"
  diagnosis: "Private cluster missing VPC endpoints for ECR. Private clusters (no internet access) require VPC endpoints for ecr.api, ecr.dkr, and an S3 gateway endpoint to pull images from ECR."
  resolution: "Operator action: create VPC endpoints for com.amazonaws.<region>.ecr.api, com.amazonaws.<region>.ecr.dkr (interface endpoints), and com.amazonaws.<region>.s3 (gateway endpoint). Attach security groups allowing HTTPS (443) from the node subnets."

## Examples

```
# Step 1: Collect logs
collect(instanceId="i-0abc123def456")
# Step 2: Get image pull findings
errors(instanceId="i-0abc123def456")
# Step 3: Search for pull failures
search(instanceId="i-0abc123def456", query="failed to pull|401 Unauthorized|403 Forbidden")
# Step 4: Check disk space
storage_diagnostics(instanceId="i-0abc123def456")
# Step 5: Check DNS
network_diagnostics(instanceId="i-0abc123def456", sections="dns")
```

## Output Format

```yaml
root_cause: "<auth|network|disk|config> — <specific detail>"
evidence:
  - type: finding
    content: "<pull failure finding from errors tool>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix>"
  long_term: "Use IRSA for ECR, set proper IMDS hop limits"
```
