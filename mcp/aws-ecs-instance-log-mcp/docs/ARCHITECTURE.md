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

Hub-and-spoke: one central deployment serves container instances across all AWS regions.

---

## Component Deep Dive

### MCP Gateway (Bedrock AgentCore)

Entry point for all MCP tool calls. Handles MCP protocol (JSON-RPC over HTTP), OAuth2 token validation via Cognito, and request routing to Lambda. Tool names are kept short (e.g., `collect`, `errors`, `read`) to stay under the 64-character limit.

### Lambda Function (Tool Router)

A single Python Lambda (~4400 lines) implementing all 19 MCP tools. Key design:
- **Single Lambda**: All tools share one function to avoid cold start multiplication
- **Regional clients**: `get_regional_client()` creates boto3 clients per-region with caching
- **Auto-detection**: `detect_instance_region()` tries default region first, then scans common regions
- **ECS instance validation**: `validate_ecs_instance()` verifies the target belongs to an ECS cluster via `aws:ecs:clusterName` tag or ECS API fallback
- **Region allow-list**: `ALLOWED_REGIONS` env var restricts which regions the tools can operate in

### SSM Automation

Log collection uses `AWSSupport-CollectECSInstanceLogs`, which:
- Runs on the target EC2 instance via SSM Agent
- Collects ECS agent logs, Docker/containerd, container logs, system logs, dmesg, networking, cgroups, metadata, GPU info
- Packages into a tar.gz archive and uploads to the central S3 bucket

### S3 Log Storage

Two S3 buckets:

1. **Logs bucket** (KMS-encrypted):
   ```
   ecs_{instance-id}/
   ├── {timestamp}.tar.gz          # Raw bundle from SSM
   ├── extracted/                   # Extracted files (by Unzip Lambda)
   │   ├── var/log/ecs/ecs-agent.log
   │   ├── var/log/docker
   │   ├── iptables-rules.txt
   │   └── manifest.json
   ├── findings_index.json          # Pre-indexed errors
   └── baselines/{cluster}/         # Baseline noise profiles
   
   idempotency/{instance-id}/      # Dedup mappings
   execution-regions/               # Region metadata
   ```

2. **SOPs bucket**: 36 runbook markdown files, auto-deployed via CDK.

### Findings Indexer

Separate Lambda triggered by S3 events on `manifest.json`. Scans extracted files for ECS-specific error patterns (agent disconnects, image pull failures, OOM kills, etc.), assigns severity and stable finding IDs (F-001), writes `findings_index.json`.

### Unzip Lambda

Triggered by `.tar.gz` uploads. Extracts files, generates `manifest.json`, which triggers the Findings Indexer.

### KMS Encryption

Customer-managed key encrypts all S3 objects. S3 client uses SigV4 explicitly for presigned URL compatibility. Key policy scopes to specific instance roles when provided, falls back to account-wide access otherwise.

---

## Data Flow

### Log Collection Flow

```
Agent calls collect(instanceId, region?)
  → Lambda validates region (ALLOWED_REGIONS) and instance (ECS cluster tag)
  → Lambda calls SSM StartAutomationExecution in target region
  → SSM Agent collects logs, packages tar.gz, uploads to central S3
  → Unzip Lambda extracts → manifest.json triggers Findings Indexer
  → Agent polls status(executionId) until complete
  → Agent calls errors(instanceId) → pre-indexed findings
```

### Live Packet Capture Flow

```
Agent calls tcpdump_capture(instanceId, taskId?, filter?)
  → Lambda calls SSM SendCommand (RunShellScript)
  → If taskId: resolves container PID via ECS agent introspection + nsenter
  → Captures for durationSeconds, uploads pcap + decoded text to S3
  → Agent polls tcpdump_capture(commandId) until complete
  → Agent calls tcpdump_analyze(instanceId, commandId) → stats + anomalies
```

---

## Cross-Region Design

```
Central Region (us-east-1)
  MCP Gateway → Lambda → S3 Bucket (KMS)
                  │ SSM StartAutomation
                  ├──→ us-west-2 (ECS Instance)
                  ├──→ eu-west-1 (ECS Instance)
                  └──→ ap-southeast-1 (ECS Instance)
```

- Region resolution: explicit param > auto-detect > Lambda's region
- Region metadata persisted in S3 for subsequent calls
- S3 writes are cross-region (S3 is global)
- IAM scoped to `ALLOWED_REGIONS` via `aws:RequestedRegion` conditions

---

## Tool Architecture

| Tier | Tools | Purpose |
|------|-------|---------|
| 1 — Core | `collect`, `status`, `validate`, `errors`, `read` | Log collection, findings, streaming |
| 2 — Analysis | `search`, `correlate`, `artifact`, `summarize`, `history` | Deep investigation, correlation |
| 3 — Cluster | `cluster_health`, `compare_instances`, `batch_collect`, `batch_status`, `network_diagnostics` | Multi-instance ops |
| 4 — Capture | `tcpdump_capture`, `tcpdump_analyze` | Live packet capture |
| 5 — SOPs | `list_sops`, `get_sop` | 36 structured runbooks |

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

36 runbooks covering ECS-specific failure categories (A-K, Z). Each follows a 3-phase structure:
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
| IAM | Least-privilege with region restrictions via `aws:RequestedRegion` |
| Instance scoping | S3 bucket policy and KMS key policy scoped to specific instance role ARNs (when provided) |
| Presigned URLs | Configurable expiration (default 900s) with SigV4 |
| Instance validation | ECS cluster membership verified before SSM execution |
| Region validation | `ALLOWED_REGIONS` enforced on all tools |
| Idempotency | Instance-scoped token mapping |
| Audit | CloudWatch logs for all Lambda invocations |

---

## CDK Construct Design

Single CDK construct (`EcsLogGatewayConstructV2`) provisions everything. Accepts optional props:
- `ecsInstanceRoleArns`: Scopes S3 bucket policy and KMS key policy to specific roles
- `allowedRegions`: Restricts IAM policies and Lambda env var
- `presignedUrlExpirationSeconds`: Controls URL lifetime
- `ssmDefaultHostRoleArn`: Grants SSM Default Host Management role access

When `ecsInstanceRoleArns` is provided, the construct creates tight S3/KMS policies. When omitted, it falls back to account-scoped access for backward compatibility.

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

Automation mode: `ECS_INSTANCE_ROLE_ARNS="arn:..." ./deploy.sh`
