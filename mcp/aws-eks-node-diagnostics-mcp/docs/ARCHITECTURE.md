# EKS Node Log MCP — Architecture & Design

This document explains the internal design of the EKS Node Log MCP server: how the components fit together, how data flows from an EKS worker node to an AI agent's context window, and the design decisions behind each layer.

## Table of Contents

- [System Overview](#system-overview)
- [Component Deep Dive](#component-deep-dive)
  - [MCP Gateway (Bedrock AgentCore)](#mcp-gateway-bedrock-agentcore)
  - [Lambda Function (Tool Router)](#lambda-function-tool-router)
  - [SSM Automation](#ssm-automation)
  - [S3 Log Storage](#s3-log-storage)
  - [Findings Indexer](#findings-indexer)
  - [Unzip Lambda](#unzip-lambda)
  - [Cognito (OAuth2)](#cognito-oauth2)
  - [KMS Encryption](#kms-encryption)
- [Data Flow](#data-flow)
  - [Log Collection Flow](#log-collection-flow)
  - [Analysis Flow](#analysis-flow)
  - [Live Packet Capture Flow](#live-packet-capture-flow)
- [Cross-Region Design](#cross-region-design)
- [Tool Architecture](#tool-architecture)
  - [Tier 1 — Core Operations](#tier-1--core-operations)
  - [Tier 2 — Advanced Analysis](#tier-2--advanced-analysis)
  - [Tier 3 — Cluster-Level Intelligence](#tier-3--cluster-level-intelligence)
  - [Tier 4 — Live Packet Capture](#tier-4--live-packet-capture)
  - [Tier 5 — SOP Management](#tier-5--sop-management)
- [Time-Bounded Analysis](#time-bounded-analysis)
- [Anti-Hallucination Design](#anti-hallucination-design)
- [SOP Runbook System](#sop-runbook-system)
- [Security Model](#security-model)
- [CDK Construct Design](#cdk-construct-design)
- [Deploy Script Design](#deploy-script-design)

---

## System Overview

The server bridges the gap between AI agents (DevOps Agent or any MCP-compatible client) and the OS-level diagnostic data on EKS worker nodes. The Kubernetes API and CloudWatch don't expose iptables rules, CNI config, route tables, dmesg, IPAMD logs, or conntrack state — but these are exactly what's needed to diagnose ~40-50% of EKS production issues.

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
                             │EKS Node││EKS Node││EKS Node│
                             │Region A││Region B││Region C│
                             └────────┘└────────┘└────────┘
```

The design is intentionally hub-and-spoke: one central deployment (Lambda + S3 + Gateway) serves nodes across all AWS regions. Logs always flow back to the central S3 bucket regardless of which region the node lives in.

---

## Component Deep Dive

### MCP Gateway (Bedrock AgentCore)

The entry point for all MCP tool calls. AgentCore provides:
- MCP protocol handling (JSON-RPC over HTTP)
- OAuth2 token validation via Cognito
- Request routing to the Lambda function
- Tool schema advertisement to MCP clients

The gateway is configured with tool definitions that include parameter schemas, descriptions, and required fields. Tool names are kept short (e.g., `collect`, `errors`, `read`) to stay under the 64-character limit when prefixed with the MCP server name.

### Lambda Function (Tool Router)

A single Python Lambda (~9700 lines) that implements all 19 MCP tools. It acts as a router:

1. Receives the tool name and arguments from the gateway
2. Dispatches to the appropriate handler function
3. Returns structured JSON responses

Key design decisions:
- **Single Lambda**: All tools share the same function to avoid cold start multiplication and simplify IAM. The function has cross-region permissions for EC2, SSM, and S3.
- **Regional clients**: `get_regional_client()` creates boto3 clients for any target region on demand, cached per invocation.
- **Auto-detection**: `detect_instance_region()` tries the default region first, then scans 16 common regions with a 20-second timeout. Explicit `region` parameter always wins.

### SSM Automation

Log collection uses the AWS-managed `AWSSupport-CollectEKSInstanceLogs` SSM document. This document:
- Runs on the target EC2 instance via SSM Agent
- Collects 20+ log sources (kubelet, containerd, CNI, iptables, routes, dmesg, sysctl, ENI metadata, IPAMD, etc.)
- Packages everything into a tar.gz archive
- Uploads directly to the central S3 bucket

The Lambda calls `ssm:StartAutomationExecution` in the target region, passing the S3 bucket and KMS key as parameters. The SSM Automation role is created by CDK with a trust policy for `ssm.amazonaws.com`.

### S3 Log Storage

Two S3 buckets:

1. **Logs bucket** (KMS-encrypted): Stores collected log bundles, findings indexes, baselines, execution metadata, and tcpdump captures. Structure:
   ```
   eks-logs/{instance-id}/
   ├── {timestamp}.tar.gz          # Raw bundle from SSM
   ├── {timestamp}/                # Extracted files (by Unzip Lambda)
   │   ├── var/log/kubelet.log
   │   ├── var/log/containers/...
   │   ├── iptables-rules.txt
   │   └── manifest.json
   ├── findings-v2.json            # Pre-indexed errors
   └── baselines/{cluster}/        # Baseline noise profiles
   
   execution-regions/              # Region metadata for cross-region routing
   idempotency-tokens/             # Dedup mappings
   tcpdump/{instance-id}/          # Packet captures
   ```

2. **SOPs bucket**: Stores 41 runbook markdown files, auto-deployed via CDK `BucketDeployment` from `sops/runbooks/`.

### Findings Indexer

A separate Lambda triggered by S3 `ObjectCreated` events on `manifest.json` files. When a log bundle is extracted:

1. Scans all extracted log files for error patterns
2. Assigns severity levels (CRITICAL, HIGH, WARNING, INFO)
3. Assigns stable finding IDs (F-001, F-002, ...)
4. Writes a `findings-v2.json` index to S3

This pre-indexing means the `errors` tool returns results instantly without re-scanning files. The agent gets structured findings with IDs it can reference in follow-up calls.

### Unzip Lambda

Triggered by S3 `ObjectCreated` events on `.tar.gz` files. It:
1. Downloads the archive
2. Extracts all files to the same S3 prefix
3. Generates a `manifest.json` listing all files with sizes
4. The manifest creation triggers the Findings Indexer

### Cognito (OAuth2)

Provides machine-to-machine authentication:
- A User Pool with a resource server defining the `gateway:read` scope
- A client credentials grant flow
- The MCP Gateway validates tokens on every request

### KMS Encryption

A customer-managed KMS key encrypts:
- All S3 objects at rest (SSE-KMS)
- The key policy grants the Lambda role, SSM Automation role, and EKS node roles encrypt/decrypt permissions
- Node roles need `kms:GenerateDataKey` and `kms:Encrypt` to upload logs

---

## Data Flow

### Log Collection Flow

```
Agent calls collect(instanceId, region?)
        │
        ▼
Lambda resolves region (explicit > auto-detect > default)
        │
        ▼
Lambda calls SSM StartAutomationExecution in target region
  - Passes: instanceId, S3 bucket, KMS key ARN
  - Stores: execution-region mapping in S3
  - Returns: executionId + task envelope (async)
        │
        ▼
SSM Agent on the node runs the collection document
  - Collects 20+ log sources
  - Packages into tar.gz
  - Uploads to central S3 bucket (cross-region write)
        │
        ▼
S3 ObjectCreated triggers Unzip Lambda
  - Extracts archive
  - Writes manifest.json
        │
        ▼
manifest.json triggers Findings Indexer Lambda
  - Scans all files for errors
  - Writes findings-v2.json
        │
        ▼
Agent polls status(executionId) until complete
Agent calls errors(instanceId) → gets pre-indexed findings
```

### Analysis Flow

Once logs are in S3, all analysis is local to the central region:

- `errors` → reads `findings-v2.json` (or live-scans if index missing)
- `search` → regex search across extracted files with byte-range reads
- `correlate` → builds cross-file timeline, temporal clusters, root cause chains
- `read` → line-aligned byte-range streaming for specific files
- `summarize` → grounded summary using finding IDs from `errors`/`search`
- `network_diagnostics` → structured analysis of iptables, CNI, routes, DNS, ENI, IPAMD
- `compare_nodes` → diffs findings across multiple nodes

### Live Packet Capture Flow

```
Agent calls tcpdump_capture(instanceId, filter?, podName?, podNamespace?)
        │
        ▼
Lambda calls SSM SendCommand (RunShellScript) in target region
  - If podName provided: resolves to container PID via crictl
  - Runs tcpdump in the appropriate network namespace
  - Captures for durationSeconds (default 120s)
  - Uploads pcap + decoded text + stats to S3
  - Returns: commandId (async)
        │
        ▼
Agent polls tcpdump_capture(commandId, instanceId) until complete
        │
        ▼
Agent calls tcpdump_analyze(instanceId, commandId)
  - Reads decoded packet text from S3
  - Runs 15 analysis modules:
    DNS analysis, TCP RST patterns, kube-proxy DNAT, VPC CNI SNAT,
    TCP keepalives, ICMP analysis, CoreDNS transients, SYN flood,
    TCP window zero, retransmissions, connection refused, traffic bursts,
    top talkers, MTU fragmentation, conntrack pressure
  - Returns structured results with anomaly flags
```

---

## Cross-Region Design

The stack deploys to one region but operates across all regions:

```
                    ┌─────────────────────────────────────────────┐
                    │           Central Region (us-east-1)        │
                    │                                             │
                    │  MCP Gateway → Lambda → S3 Bucket (KMS)    │
                    │                  │                          │
                    │                  │ SSM StartAutomation      │
                    └──────────────────┼──────────────────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
              ▼                        ▼                        ▼
     ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
     │   us-west-2     │    │   eu-west-1     │    │  ap-southeast-1 │
     │  EKS Node       │    │  EKS Node       │    │  EKS Node       │
     │  ↓ SSM Agent    │    │  ↓ SSM Agent    │    │  ↓ SSM Agent    │
     │  ↓ Upload to S3 │    │  ↓ Upload to S3 │    │  ↓ Upload to S3 │
     └─────────────────┘    └─────────────────┘    └─────────────────┘
```

Key mechanisms:
- **Region resolution priority**: explicit parameter > auto-detect via `ec2:DescribeInstances` > Lambda's own region
- **Region metadata persistence**: After `collect`, the Lambda stores `execution-regions/{executionId}` in S3 so `status`/`validate` auto-route to the correct region
- **Cross-region S3 writes**: The node's IAM role writes directly to the central bucket — S3 is global, so this works regardless of the node's region
- **Lambda IAM**: CDK grants `ec2:DescribeInstances`, `ssm:*` across `*` resources so the Lambda can operate in any region

---

## Tool Architecture

### Tier 1 — Core Operations

| Tool | Purpose | Key Design |
|------|---------|------------|
| `collect` | Start log collection | Idempotency via token mapping in S3. Cross-region SSM dispatch. Returns async task envelope. |
| `status` | Poll execution progress | Reads region metadata from S3. Parses SSM failure reasons. Estimates progress percentage. |
| `validate` | Verify bundle completeness | Reads `manifest.json`. Reports file count, total size, missing expected files. |
| `errors` | Pre-indexed findings | Reads `findings-v2.json` first (fast path). Falls back to live scan. Supports severity filter, pagination, baseline subtraction. |
| `read` | Byte-range streaming | Line-aligned reads. Supports `startByte/endByte` or `startLine/lineCount`. No truncation — handles multi-GB files. |

### Tier 2 — Advanced Analysis

| Tool | Purpose | Key Design |
|------|---------|------------|
| `search` | Regex search across logs | Searches all extracted files. Returns matches with finding IDs (S-NNN), line numbers, context lines. |
| `correlate` | Cross-file timeline | Extracts timestamps from all files. Builds temporal clusters. Generates root cause chains with confidence scores. Reports data gaps. |
| `artifact` | Presigned URLs | 15-minute expiration. For downloading large files outside MCP. |
| `summarize` | Incident summary | Grounded in finding IDs — agent must pass IDs from `errors`/`search`. Unresolved IDs are flagged. Prevents hallucinated evidence. |
| `history` | Audit trail | Lists past collections by instance. Supports cross-region. |

### Tier 3 — Cluster-Level Intelligence

| Tool | Purpose | Key Design |
|------|---------|------------|
| `cluster_health` | Cluster overview | Enumerates nodes via EKS + EC2 APIs. Checks SSM Agent status. Reports unhealthy nodes. |
| `compare_nodes` | Diff findings | Collects findings from 2+ nodes. Separates common vs unique errors. Generates comparison insight. |
| `batch_collect` | Smart batch collection | Statistical sampling for large clusters. Dry-run mode. Prioritizes unhealthy nodes. |
| `batch_status` | Batch polling | Polls multiple executions. Reports overall completion percentage. |
| `network_diagnostics` | Networking analysis | Structured parsing of iptables, CNI config, routes, DNS, ENI metadata, IPAMD logs. Issues severity assessment. |

### Tier 4 — Live Packet Capture

| Tool | Purpose | Key Design |
|------|---------|------------|
| `tcpdump_capture` | Run tcpdump via SSM | Async via SSM RunCommand. Supports pod-level capture (resolves PID via crictl). Uploads pcap + decoded text to S3. |
| `tcpdump_analyze` | Analyze capture | 15 analysis modules covering DNS, RST, DNAT, SNAT, keepalives, ICMP, SYN floods, retransmissions, top talkers, MTU, conntrack. |

### Tier 5 — SOP Management

| Tool | Purpose | Key Design |
|------|---------|------------|
| `list_sops` | Browse runbooks | Lists all 41 SOPs with title, description, severity, trigger patterns. |
| `get_sop` | Retrieve full SOP | Returns complete 3-phase procedure. Agent follows it step by step. |

---

## Time-Bounded Analysis

All analysis tools enforce time windows to prevent historical noise from polluting active investigations.

Resolution order:
1. Explicit `start_time` + `end_time` → used as-is
2. `incident_time` → window = incident_time ± 5 minutes
3. Nothing provided → last 10 minutes from current UTC
4. Maximum window: 24 hours (safety cap)

Every response includes `window_start_utc`, `window_end_utc`, `resolution_reason`, and counts of findings excluded outside the window.

---

## Anti-Hallucination Design

The server is designed to prevent AI agents from fabricating evidence:

1. **Finding IDs**: Every error gets a stable ID (F-001, S-001). The `summarize` tool requires the agent to pass specific finding IDs. Any ID that doesn't resolve to a real finding is flagged in the response.

2. **Baseline subtraction**: Known cluster noise is annotated (not removed). The agent sees `"is_baseline": true` but the finding is still present — the user retains full visibility.

3. **Confidence scores**: `correlate` reports confidence levels and data quality gaps. If timestamps are missing or log coverage is incomplete, the response says so.

4. **Grounded summaries**: `summarize` only includes evidence from findings the agent explicitly references. No inferred or assumed evidence.

---

## SOP Runbook System

41 runbooks covering the most common EKS node-level failure categories. Each follows a consistent structure:

```
Phase 1 — Triage (MUST)
  → Check pod/node state via EKS MCP tools first
  → Collect logs, get pre-indexed findings

Phase 2 — Enrich (SHOULD)
  → Deep search, correlate, domain-specific diagnostics

Phase 3 — Report (MUST)
  → Grounded incident summary with finding IDs
  → Root cause, evidence chain, remediation steps
```

SOPs are stored as markdown in `sops/runbooks/`, deployed to S3 via CDK `BucketDeployment`, and retrieved at runtime by the agent using `list_sops` and `get_sop`.

---

## Security Model

| Layer | Mechanism |
|-------|-----------|
| Authentication | Cognito OAuth2 client credentials grant |
| Encryption at rest | KMS customer-managed key for all S3 objects |
| Encryption in transit | HTTPS enforced on S3 (deny HTTP policy) |
| Public access | S3 Block Public Access enabled |
| IAM | Least-privilege: Lambda role scoped to required actions, node roles scoped to PutObject on the logs bucket |
| Presigned URLs | 15-minute expiration for artifact downloads |
| Idempotency | Token-based dedup prevents duplicate SSM executions |
| Audit | CloudWatch logs for all Lambda invocations |

---

## CDK Construct Design

The infrastructure is a single CDK construct (`SsmAutomationGatewayV2Construct`) that provisions everything:

- S3 buckets (logs + SOPs) with KMS encryption and lifecycle policies
- Lambda functions (main handler, unzip, findings indexer, client secret retriever)
- SSM Automation role with trust policy
- Cognito User Pool, resource server, and client
- Bedrock AgentCore Gateway with tool schema definitions
- IAM policies with cross-region permissions
- S3 event notifications wiring (tar.gz → unzip, manifest.json → indexer)
- BucketDeployment for SOP runbooks

The construct accepts optional props for customization (gateway name, log retention, encryption toggle, node role ARNs) but works with zero configuration.

---

## Deploy Script Design

The `deploy.sh` script handles the full deployment lifecycle with interactive prompts:

### Interactive Flow

```
Step 1: Region Selection
  ├── 1) All enabled regions
  ├── 2) Current deploy region only
  └── 3) Enter a specific region

Step 2: Cluster Selection
  ├── Lists all discovered EKS clusters with region
  ├── a) All clusters
  └── 1,2,5) Comma-separated picks

Step 3: Node Role Selection
  ├── Lists all unique node role ARNs with source cluster
  ├── a) All roles
  └── 1,3) Comma-separated picks

Fallback: Manual ARN Entry
  └── If no clusters/roles found, prompts for manual ARN input
```

### What It Does

1. Installs npm dependencies and builds TypeScript
2. Bootstraps CDK if needed
3. Detects or creates the SSM Default Host Management role
4. Interactively discovers EKS clusters and node roles (or accepts manual input)
5. Deploys the CDK stack
6. Retrieves all configuration values (Gateway URL, Cognito credentials, etc.)
7. Saves configuration to `mcp-config.txt`

### Automation Mode

Skip all interactive prompts by providing role ARNs directly:

```bash
EKS_NODE_ROLE_ARNS="arn:aws:iam::123456789012:role/MyNodeRole" ./deploy.sh
```

Or as a positional argument:

```bash
./deploy.sh EksNodeLogMcpStack arn:aws:iam::123456789012:role/MyNodeRole
```
