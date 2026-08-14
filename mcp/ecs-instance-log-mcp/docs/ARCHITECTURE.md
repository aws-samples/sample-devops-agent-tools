# ECS Instance Log MCP — Architecture & Design

This document explains the internal design of the ECS Instance Log MCP server: how the components fit together, how data flows from an ECS container instance to an AI agent's context window, and the design decisions behind each layer.

## Table of Contents

- [System Overview](#system-overview)
- [Component Deep Dive](#component-deep-dive)
- [Data Flow](#data-flow)
- [Cross-Region Design](#cross-region-design)
- [Tool Architecture](#tool-architecture)
- [Time-Bounded Analysis](#time-bounded-analysis)
- [Anti-Hallucination Design](#anti-hallucination-design)
- [SOP Runbook System](#sop-runbook-system)
- [Security Model](#security-model)
- [CDK Construct Design](#cdk-construct-design)
- [Deploy Script Design](#deploy-script-design)

---

## System Overview

The server bridges the gap between AI agents and the OS-level diagnostic data on ECS container instances. The ECS API and CloudWatch don't expose Docker daemon config, iptables rules, cgroup memory events, container runtime state, or ECS agent internal logs — but these are exactly what's needed to diagnose task startup failures, agent disconnects, OOM kills, and networking issues.

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  MCP Client     │────▶│  MCP Gateway     │────▶│  Lambda         │
│  (DevOps Agent) │◀────│  (AgentCore)     │◀────│  (19 tools)     │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                              │ OAuth2                    │
                              ▼                           │
                        ┌──────────┐          ┌───────────┼───────────┐
                        │ Cognito  │          │           │           │
                        │ User Pool│          ▼           ▼           ▼
                        └──────────┘   ┌──────────┐ ┌──────────┐ ┌──────────┐
                                       │ SSM      │ │ S3       │ │ S3       │
                                       │ Automati-│ │ Logs     │ │ SOPs     │
                                       │ on       │ │ (KMS)    │ │ Bucket   │
                                       └────┬─────┘ └──────────┘ └──────────┘
                                            │
                                  ┌─────────┼─────────┐
                                  ▼         ▼         ▼
                             ┌────────┐┌────────┐┌────────┐
                             │ECS Inst││ECS Inst││ECS Inst│
                             │Region A││Region B││Region C│
                             └────────┘└────────┘└────────┘
```

A deployment serves the regions allowed by IAM for read-only diagnostics. Native approval wrappers are account-owned regional SSM documents, so approval-gated collection and capture target the stack region. Deploy one stack per required region.

---

## Component Deep Dive

### MCP Gateway (Bedrock AgentCore)

Entry point for all MCP tool calls. Handles MCP protocol (JSON-RPC over HTTP), OAuth2 token validation via Cognito, and request routing to Lambda. Tool names are kept short (e.g., `collect`, `errors`, `read`) to stay under the 64-character limit.

### Lambda Function (Tool Router)

A single Python Lambda implements 17 default tools and two opt-in packet-capture tools. Key design:
- **Single Lambda**: All tools share one function to avoid cold start multiplication
- **Regional clients**: `get_regional_client()` creates boto3 clients per region with caching
- **Auto-detection**: `detect_instance_region()` tries the default region, then allowed candidate regions
- **Exact ECS membership**: `validate_ecs_instance()` enumerates only configured `ALLOWED_CLUSTER_NAMES`, paginates their container instances, and requires an exact ACTIVE `ec2InstanceId` match; names and tags are not trusted as membership proof
- **Cluster and region allowlists**: empty cluster configuration fails closed, and `ALLOWED_REGIONS` restricts every live regional client path
- **Approval separation**: Lambda has no `ssm:SendAutomationSignal` and no direct `ssm:SendCommand` while approval is enabled

### SSM Automation

Approval is enabled by default. Three account-owned Automation documents pause at native `aws:approve` steps for single collection, one-approved batch fan-out, and task-scoped tcpdump. The Automation role, approvers, and SNS topic are embedded at deployment rather than supplied by callers.

After approval, log collection invokes `AWSSupport-CollectECSInstanceLogs`, which:
- Runs on the target EC2 instance via SSM Agent
- Collects ECS agent logs, Docker/containerd, container logs, system logs, dmesg, networking, cgroups, metadata, and GPU info
- Packages an archive (`.tgz` on current Linux runbook versions; `.tar.gz` and `.zip` are also supported) and uploads it to the central S3 bucket

The managed runbook writes Linux bundles at the bucket root using `ecs_<instance-id>_<execution-id>.tgz`. The unzip Lambda validates that deployment-owned layout and maps extracted content into the canonical instance namespace used by analysis tools.

The tcpdump wrapper invokes `AWS-RunShellScript` only after approval. The script never installs packages and rejects Fargate, host-wide, host-network, ambiguous-container, stale-PID, and changed-namespace targets.

### S3 Log Storage

Two S3 buckets:

1. **Logs bucket** (KMS-encrypted):
   ```
   ecs_{instance-id}_{execution-id}.tgz     # Raw bundle from the managed SSM runbook
   ecs_{instance-id}/{execution-id}/
   └── extracted/                            # Canonical analysis namespace
       ├── var/log/ecs/ecs-agent.log
       ├── var/log/docker
       ├── iptables-rules.txt
       ├── manifest.json
       └── findings_index.json

   idempotency/{instance-id}/      # Dedup mappings
   _metadata/                      # Execution and batch provenance
   ```

2. **SOPs bucket**: 36 runbook markdown files, auto-deployed via CDK.

### Findings Indexer

The unzip Lambda invokes the Findings Indexer asynchronously after extraction and manifest generation. The indexer scans extracted files for ECS-specific error patterns (agent disconnects, image pull failures, OOM kills, etc.), assigns severity and stable finding IDs (F-001), and writes `findings_index.json` into the same canonical extraction prefix.

### Unzip Lambda

Triggered by S3 ObjectCreated notifications for `.zip`, `.tar.gz`, and `.tgz`. It URL-decodes the S3 event key, accepts only validated managed-runbook or canonical archive layouts, maps root-level managed bundles into `ecs_<instance-id>/<execution-id>/extracted/`, generates `manifest.json`, and directly invokes the Findings Indexer. The deploy script verifies all three notification suffixes after deployment.

### KMS Encryption

Customer-managed key encrypts all S3 objects. S3 client uses SigV4 explicitly for presigned URL compatibility. Key and bucket policies require explicit ECS instance-role ARNs; synthesis fails instead of creating an account-scoped compatibility fallback.

---

## Data Flow

### Log Collection Flow

```
Agent calls collect(instanceId)
  → Lambda validates allowed region and exact ACTIVE ECS membership
  → Lambda starts the stack-region collection approval wrapper
  → Native aws:approve waits; Lambda cannot approve its own request
  → Approved wrapper invokes AWSSupport-CollectECSInstanceLogs
  → SSM Agent collects and uploads the managed-runbook archive
  → .zip/.tar.gz/.tgz ObjectCreated notification invokes Unzip Lambda
  → Unzip Lambda maps the bundle into the canonical instance namespace and extracts it
  → Unzip Lambda writes manifest.json and directly invokes Findings Indexer
  → Agent polls status(executionId) until the child execution completes
```

### Batch Collection Flow

```
Agent calls batch_collect(clusterName) → dry-run plan by default
  → Agent explicitly calls batch_collect(..., dryRun=false)
  → One native approval covers at most 15 sampled instances
  → Wrapper starts child collection automations and records per-instance errors
  → Agent polls batch_status(batchId) for complete, failed, or partial_failure
```

### Live Packet Capture Flow

```
Agent calls tcpdump_capture(instanceId, taskId, containerName?, confirmCapture=true)
  → Restricted-tool and same-region approval checks fail closed
  → Native aws:approve waits with task/container scope in the request
  → Approved wrapper dispatches the fixed Run Command path
  → Script exactly resolves task and RUNNING application container
  → Docker/containerd resolves PID; PID start time and net namespace are recorded
  → Script rejects host namespace and re-resolves PID immediately before nsenter
  → nsenter runs preinstalled tcpdump and uploads pcap/text/stats
  → Agent polls executionId, then commandId
  → Agent calls tcpdump_analyze(instanceId, commandId)
```

---

## Cross-Region Design

The S3 data plane and read-only diagnostic APIs can cover configured `ALLOWED_REGIONS`. Approval wrappers are regional SSM documents and this implementation deliberately requires approval-gated operations to target the stack region. For multiple operational regions, deploy the stack separately in each region so every collection or capture retains native approval; do not disable approval as a regional workaround.

- Region resolution: explicit parameter, then instance detection, then Lambda region
- Execution-region metadata is persisted for polling
- IAM resources and unsupported resource-level actions are constrained to allowed regions
- New approved collection, batch fan-out, and tcpdump requests are rejected when the target differs from the stack region

---

## Tool Architecture

| Tier | Tools | Purpose |
|------|-------|---------|
| 1 — Core | `collect`, `status`, `validate`, `errors`, `read` | Log collection, findings, streaming |
| 2 — Analysis | `search`, `correlate`, `artifact`, `summarize`, `history` | Deep investigation, correlation |
| 3 — Cluster | `cluster_health`, `compare_instances`, `batch_collect`, `batch_status`, `network_diagnostics` | Multi-instance ops |
| 4 — Restricted capture | `tcpdump_capture`, `tcpdump_analyze` | Opt-in, human-approved task packet capture |
| 5 — SOPs | `list_sops`, `get_sop` | Structured runbooks, including approved capture operations |

---

## Time-Bounded Analysis

`TimeWindowResolver` enforces time windows on all analysis:
1. Explicit `start_time` + `end_time` → used as-is
2. `incident_time` → ± 5 minutes
3. Nothing → last 10 minutes
4. Max: 24 hours (safety cap)

---

## Anti-Hallucination Design

1. **Finding IDs**: Every error gets a stable ID (F-001). `summarize` requires finding IDs — unresolved IDs are flagged.
2. **ECS instance validation**: `collect` verifies the target belongs to an ECS cluster before running SSM.
3. **Region allow-list**: Tools reject requests to disallowed regions.
4. **Baseline subtraction**: Known noise is annotated, not removed.
5. **Confidence scores**: `correlate` reports confidence and data gaps.
6. **Network diagnostics guardrails**: `network_diagnostics` returns an `ecsContext` block with domain-specific guardrails (Docker bridge vs awsvpc, ECS Agent vs container networking, SG per network mode, DNS per network mode, conntrack attribution, ENI limits, Service Connect vs Discovery, container vs ELB health checks).
7. **False positive suppression**: Error scanning filters out `error_count=0`, conditional error handling, etc.
8. **Configurable presigned URLs**: `PRESIGNED_URL_EXPIRATION_SECONDS` env var controls URL lifetime.
9. **S3 SigV4**: Explicit SigV4 signing for KMS-encrypted bucket compatibility.

---

## SOP Runbook System

Runbooks cover ECS-specific failure categories (A-K, Z) plus human-approved task packet capture. Each follows a 3-phase structure:
- Phase 1 — Triage (MUST): Check cluster/task state, collect logs, get findings
- Phase 2 — Enrich (SHOULD): Deep search, correlate, domain-specific diagnostics
- Phase 3 — Report (MUST): Grounded summary with finding IDs, root cause, remediation

Auto-matched via `recommendedSOPs` in `errors`, `correlate`, and `summarize` responses.

---

## Security Model

| Layer | Mechanism |
|-------|-----------|
| Authentication | Cognito OAuth2 client credentials grant |
| Encryption at rest | KMS customer-managed key |
| Encryption in transit | HTTPS enforced on S3 |
| Public access | S3 Block Public Access |
| Native approval | Default-on `aws:approve`; synthesis fails without approvers |
| Approval ownership | Automation role, approvers, and SNS topic are embedded in documents |
| Approval separation | Lambda has no `ssm:SendAutomationSignal` and no direct `SendCommand` in approval mode |
| IAM | StartAutomation and SendCommand scoped to required documents and allowed regions |
| Cluster scope | Exact deployment-owned cluster allowlist; empty configuration fails closed |
| Restricted tools | Packet-capture schemas and runtime routes are absent unless explicitly enabled |
| Instance validation | Exact paginated ACTIVE ECS container-instance membership within allowed clusters |
| Generic artifacts | `instanceId`-bound canonical keys; cross-instance, metadata, traversal, and pcap access rejected |
| Regex search | Conservative unsafe-pattern rejection plus interruptible per-file wall-clock timeout |
| Execution polling | Gateway provenance binds execution ID to tool, document, region, and instance; batch requires `batchId` |
| Capture scope | Exact task/container; ambiguous, changed-PID, host-network, host-wide, and Fargate targets rejected |
| Packet artifacts | Short pcap URL lifetime; analysis requires the exact command UUID |
| Batch safety | Dry-run default, one approval, 15-child cap, explicit partial-failure reporting |
| Instance uploads | Explicit instance-role principals receive bucket-level `ListBucket`/policy/ACL reads for the support document's `HeadBucket` preflight and object-level `PutObject`; the deploy script validates the live policy after deployment |
| Region validation | `ALLOWED_REGIONS` plus same-region approval-wrapper enforcement |
| Idempotency | Instance-scoped token mapping |
| Audit | SSM Automation/Run Command history and CloudWatch Lambda logs |

### AppSec control mapping

| IDs | Architecture evidence |
|---|---|
| M1–M4 | OAuth authentication; mandatory KMS/TLS; explicit least-privilege principals; native human approval with approver separation. |
| E1–E3 | Exact cluster/region boundaries; exact ACTIVE ECS membership; canonical instance-bound S3 object access. |
| E4–E6 | Interruptible regex limits; provenance-bound polling; restricted task-scoped tcpdump and dry-run/capped batch collection. |

Executable evidence is in `tests/test_security_parity.py`, `tests/test_instance_validation.py`, `tests/test_collection_approval.py`, `tests/test_batch_approval.py`, and the tcpdump security/approval suites.

---

## CDK Construct Design

Single CDK construct (`EcsLogGatewayConstructV2`) provisions everything. Relevant properties include:
- `allowedClusterNames`: mandatory exact ECS cluster allowlist
- `ecsInstanceRoleArns`: mandatory explicit principals for S3/KMS upload access; no account fallback
- `allowedRegions`: constrains regional IAM and Lambda validation
- `enableKmsEncryption`: retained for compatibility but must be `true`; synthesis rejects disabled encryption
- `requireCollectionApproval`: defaults to `true`; disabling it is an explicit supervised/test choice
- `approvalApproverArns`, `approvalNotificationEmails`, `approvalTtlSeconds`: configure native approval
- `enableRestrictedTools`: opt-in exposure of `tcpdump_capture` and/or `tcpdump_analyze`
- `pcapPresignedUrlExpirationSeconds`, `maxPcapBytes`: limit packet-artifact exposure
- `presignedUrlExpirationSeconds`, `ssmDefaultHostRoleArn`: configure general artifacts and SSM host uploads

When approval is enabled, the construct creates all three wrappers and grants Lambda StartAutomation only on those account-owned documents. When disabled, wrappers are omitted and direct tcpdump SendCommand is granted only if capture is explicitly enabled. Explicit instance-role principals remain mandatory in both modes.

---

## Deploy Script Design

Interactive 5-step flow matching the EKS deploy pattern:

```
Step 1: Region Selection
  ├── 1) All enabled regions
  ├── 2) Current deploy region only
  └── 3) Enter a specific region

Step 2: ECS Cluster Discovery
  └── Lists all ECS clusters across selected regions

Step 3: Cluster Selection
  ├── a) All clusters
  └── 1,2,5) Comma-separated picks

Step 4: Instance Role Detection
  └── Discovers IAM roles from container instance profiles

Step 5: Role Selection
  ├── a) All roles
  └── 1,3) Comma-separated picks
```

Automation mode requires all three deployment boundaries:

```bash
ALLOWED_CLUSTER_NAMES="prod-cluster" \
ALLOWED_REGIONS="us-east-1" \
ECS_INSTANCE_ROLE_ARNS="arn:aws:iam::123456789012:role/ecsInstanceRole" \
./deploy.sh
```

The script never retrieves, prints, or writes the OAuth client secret. The ignored `mcp-config.txt` contains non-secret connection metadata only.
