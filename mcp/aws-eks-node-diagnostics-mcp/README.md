# EKS Node Diagnostics MCP

> **⚠️ Proof of Concept (POC):** This project is a proof of concept and should be tested in non-production environments first. Validate thoroughly in a staging or development account before using with production workloads.

MCP Server for AWS DevOps Agent to collect and analyze diagnostic logs from EKS worker nodes using SSM Automation. Covers 20+ log sources including kubelet, containerd, iptables, CNI config, route tables, dmesg, IPAMD, and more — artifacts that live on the node OS and aren't accessible through the Kubernetes API or CloudWatch.

> **Want to understand the internals?** See [Architecture & Design](docs/ARCHITECTURE.md) for a deep dive into how the components work, data flows, tool design, and security model.

---

## Prerequisites

### 1. Node.js (v18.x or later)

**macOS (Homebrew):**
```bash
brew install node
```

**Linux (Ubuntu/Debian):**
```bash
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs
```

### 2. AWS CLI v2

**macOS:**
```bash
brew install awscli
```

**Linux:**
```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
```

### 3. AWS CDK CLI

```bash
npm install -g aws-cdk
```

### 4. Python 3

Most systems have it pre-installed:
```bash
python3 --version
```

### 5. AWS Credentials

You need permissions to create IAM Roles, Lambda Functions, S3 Buckets, KMS Keys, Cognito User Pools, and BedrockAgentCore Gateways.

```bash
aws configure
# Or use AWS SSO:
aws sso login --profile your-profile
export AWS_PROFILE=your-profile
```

---

## Deployment

```bash
# Clone the repository
git clone https://github.com/aws-samples/sample-eks-node-diagnostics-mcp.git
cd sample-eks-node-diagnostics-mcp

# Make the script executable
chmod +x deploy.sh

# Deploy (defaults to us-east-1)
./deploy.sh

# Or deploy to a specific region
AWS_REGION=us-west-2 ./deploy.sh
```

`deploy.sh` derives the security scope automatically from your interactive choices — there's no separate "tighten" step. After you pick clusters, the script exports:

- `ALLOWED_REGIONS` from the regions of the selected clusters.
- `ALLOWED_CLUSTER_NAMES` from the names of the selected clusters.
- `EKS_NODE_ROLE_ARNS` from the selected nodegroup roles.

These flow straight into the CDK construct, so the deployed IAM policies are tag-scoped and region-scoped without any extra flags. If you skip cluster selection (or no clusters are found), the script falls back to deploy-region-only and prompts before deploying with an unrestricted cluster scope.

### Interactive Deployment Flow

The deploy script walks you through three interactive prompts:

**Step 1 — Region selection:**
```
Which AWS regions should be scanned for EKS clusters?

  1) All enabled regions
  2) Current deploy region only (us-east-1)
  3) Enter a specific region

Select [1/2/3] (default: 1):
```

**Step 2 — Cluster selection:**
```
Found 4 EKS cluster(s):

  1) prod-cluster    (us-east-1)
  2) dev-cluster     (us-east-1)
  3) analytics        (us-west-2)
  4) eu-cluster       (eu-west-1)

  a) All clusters

Select clusters (comma-separated numbers, or 'a' for all) [default: a]:
```

**Step 3 — Node role selection:**
```
Found 3 unique node role(s):

  1) arn:aws:iam::123456789012:role/eks-prod-node-role
     └─ eks-prod-node-role  (prod-cluster / us-east-1)
  2) arn:aws:iam::123456789012:role/eks-dev-node-role
     └─ eks-dev-node-role  (dev-cluster / us-east-1)
  3) arn:aws:iam::123456789012:role/eks-eu-node-role
     └─ eks-eu-node-role  (eu-cluster / eu-west-1)

  a) All roles

Select node roles (comma-separated numbers, or 'a' for all) [default: a]:
```

**Fallback — Manual ARN entry:**

If no EKS clusters or node roles are found, the script prompts you to enter role ARNs manually:
```
WARNING: No EKS clusters found in the selected region(s).

Would you like to manually enter node role ARN(s)? [y/N]: y
Enter comma-separated role ARNs (e.g. arn:aws:iam::123456789012:role/MyNodeRole):
>
```

### Non-Interactive / CI Mode

Pre-set the env vars to skip every prompt. Recommended for repeatable deploys:

```bash
AWS_REGION=us-east-1 \
ALLOWED_REGIONS=us-east-1 \
ALLOWED_CLUSTER_NAMES=prod-cluster,staging-cluster \
EKS_NODE_ROLE_ARNS=arn:aws:iam::123456789012:role/eks-node-role \
./deploy.sh EksNodeLogMcpStack
```

If you genuinely need the wildcard scope (Lambda may target any EKS cluster in the account), opt in explicitly:

```bash
AWS_REGION=us-east-1 \
ALLOW_ANY_CLUSTER_NAME=true \
EKS_NODE_ROLE_ARNS=arn:aws:iam::123456789012:role/eks-node-role \
./deploy.sh EksNodeLogMcpStack
```

Without one of `ALLOWED_CLUSTER_NAMES` or `ALLOW_ANY_CLUSTER_NAME=true`, `cdk synth` fails with a clear error — this is intentional.

### Maximum Restriction

For production deploys, layer in the rest of the controls:

```bash
AWS_REGION=us-west-2 \
ALLOWED_REGIONS=us-west-2 \
ALLOWED_CLUSTER_NAMES=prod-cluster \
ALLOWED_SSM_DOCUMENTS=AWS-RunShellScript \
EKS_NODE_ROLE_ARNS=arn:aws:iam::123456789012:role/eks-node-role \
PRESIGNED_URL_EXPIRATION=120 \
PER_CALLER_RATE_LIMIT_PER_MINUTE=30 \
TOOL_AUTHORIZATION="collect:client-soc;batch_collect:client-emergency" \
APPROVAL_APPROVER_ARNS=arn:aws:iam::123456789012:role/OnCallOperator \
APPROVAL_NOTIFICATION_EMAILS=oncall@example.com \
MCP_VPC_ID=vpc-0123456789abcdef0 \
MCP_VPC_SUBNET_IDS=subnet-aaa,subnet-bbb \
./deploy.sh
```

> `APPROVAL_APPROVER_ARNS` defaults to the IAM principal running `deploy.sh` when unset. Approvers need `ssm:SendAutomationSignal` (plus Systems Manager console access) to click Approve/Deny.

| Env var | What it restricts | Default |
|---------|-------------------|---------|
| `ALLOWED_REGIONS` | IAM resource ARNs + Lambda region scanning | Stack region |
| `ALLOWED_CLUSTER_NAMES` | `ssm:SendCommand` tag condition on instances | (none — fail-closed) |
| `ALLOW_ANY_CLUSTER_NAME` | Explicit opt-in to any-cluster wildcard | `false` |
| `ALLOWED_SSM_DOCUMENTS` | Which SSM documents can be executed | `AWS-RunShellScript` |
| `EKS_NODE_ROLE_ARNS` | S3 PutObject + KMS Encrypt principals | Account root |
| `PRESIGNED_URL_EXPIRATION` | Log artifact presigned URL lifetime (max 900 s) | 300 s |
| `ALLOW_SELF_MANAGED_NODES` | Accept nodes with only the user-settable `kubernetes.io/cluster/*` tag (cross-checked via EKS API) | `false` |
| `REQUIRE_COLLECTION_APPROVAL` | Require human approval (native SSM `aws:approve`) before `collect`/`batch_collect` run | `true` |
| `APPROVAL_APPROVER_ARNS` | IAM users/roles allowed to approve collections (**required** when approval is on — synth fails without it) | (none — fail-closed) |
| `APPROVAL_NOTIFICATION_EMAILS` | Comma-separated emails subscribed to the approval SNS topic | Empty |
| `APPROVAL_TTL_SECONDS` | How long the `aws:approve` step waits for a decision before timing out | `900` |
| `TOOL_AUTHORIZATION` | Per-tool client-id ACL (`tool:client_a,client_b;…`) | Empty (open) |
| `PER_CALLER_RATE_LIMIT_PER_MINUTE` | Rate limit per caller (`0` disables) | 60 |
| `MCP_VPC_ID` / `MCP_VPC_SUBNET_IDS` | Run Lambda in VPC + create S3/KMS endpoints | None |

### What Gets Deployed

| Resource | Purpose |
|----------|---------|
| S3 Bucket (KMS encrypted) | Stores collected log bundles |
| S3 Bucket (SOPs) | Stores 41 runbooks, auto-deployed via CDK |
| Lambda (SSM Automation) | Handles all 19 MCP tool invocations |
| Lambda (Unzip) | Auto-extracts uploaded archives |
| Lambda (Findings Indexer) | Pre-indexes errors for fast retrieval |
| SSM Documents (approval wrappers) | `aws:approve`-gated wrappers for `collect` (single) and `batch_collect` (fan-out) |
| SNS Topic | Notifies approvers with the SSM console approval link |
| SSM Automation Role | Runs log collection on EC2 instances |
| Cognito User Pool | OAuth2 authentication for MCP Gateway |
| BedrockAgentCore Gateway | MCP protocol endpoint |
| KMS Key | Encrypts all data at rest |

---

## Security Model

All security controls are enforced by default. The construct fails synth unless you make an explicit cluster scope choice — there is no implicit wildcard.

### Defaults (no extra config)

| Control | Default | Configurable via |
|---------|---------|------------------|
| **Region restriction** | Stack region only | `ALLOWED_REGIONS` env var |
| **Cluster restriction** | **Fail-closed** — must set `ALLOWED_CLUSTER_NAMES` or `ALLOW_ANY_CLUSTER_NAME=true` | `ALLOWED_CLUSTER_NAMES`, `ALLOW_ANY_CLUSTER_NAME` |
| **SSM document restriction** | `AWS-RunShellScript` only | `ALLOWED_SSM_DOCUMENTS` env var |
| **Collection approval (human-in-the-loop)** | `collect`/`batch_collect` pause at a native SSM `aws:approve` step until a designated approver approves in the Systems Manager console | `REQUIRE_COLLECTION_APPROVAL`, `APPROVAL_APPROVER_ARNS` env vars |
| **`batch_collect` dry-run** | Defaults to dry-run; real execution needs explicit `dryRun=false` | tool parameter |
| **Cluster allowlist (Lambda)** | Enforced when `ALLOWED_CLUSTER_NAMES` is set | `ALLOWED_CLUSTER_NAMES` env var |
| **Presigned URL expiry (logs)** | 300 s, max 900 s | `PRESIGNED_URL_EXPIRATION` env var |
| **Per-tool authorization** | All authenticated callers may invoke any tool | `TOOL_AUTHORIZATION` env var |
| **Per-caller rate limit** | 60 invocations / min / caller | `PER_CALLER_RATE_LIMIT_PER_MINUTE` env var (0 disables) |
| **VPC endpoints (S3, KMS, SSM, EC2, Logs, Metrics)** | Off (Lambda runs outside a VPC) | `MCP_VPC_ID` + `MCP_VPC_SUBNET_IDS` |
| **Response redaction** | SG/ENI/subnet/VPC IDs, account IDs in ARNs, private IPs (network tools), IAM error bodies, JWT/AKIA tokens, fields named `*password*`/`*secret*`/`*token*`/`*credential*` | Always on |
| **S3 encryption** | SSE-KMS with auto-rotating key | `enableEncryption` CDK prop |
| **S3 public access** | Blocked | Always on |
| **S3 transport** | SSL enforced | Always on |
| **Authentication** | Cognito OAuth2 client credentials | Always on |
| **EKS instance validation** | EKS-managed tag required (user-settable `kubernetes.io/cluster/*` rejected unless `ALLOW_SELF_MANAGED_NODES=true`) + EKS API cross-reference | `ALLOW_SELF_MANAGED_NODES` env var |
| **Search regex safety** | Catastrophic-backtracking (ReDoS) patterns rejected | Always on |
| **Log key validation** | `read`/`artifact` restricted to log-bundle keys; path traversal blocked | Always on |
| **Idempotency writes** | S3 conditional writes (`IfNoneMatch=*`) | Always on |
| **Baseline counter writes** | Optimistic concurrency (`IfMatch=<VersionId>`, retry on `PreconditionFailed`) | Always on |
| **Log auto-deletion** | 1 day | `logRetentionDays` CDK prop |

### IAM Scoping

`ssm:SendCommand` is restricted at three levels:

1. **Resource ARNs** — instance ARNs are scoped to `ALLOWED_REGIONS` (e.g., `arn:aws:ec2:us-west-2:ACCOUNT:instance/*`). Document ARNs are scoped to specific document names (e.g., `document/AWS-RunShellScript`).
2. **Tag conditions** — instances must have the `eks:cluster-name` tag matching `ALLOWED_CLUSTER_NAMES`. With specific names, the condition uses `StringEquals` (exact match). The wildcard form (`StringLike: *`) is only emitted when `ALLOW_ANY_CLUSTER_NAME=true`.
3. **Region conditions** — all SSM, EC2, and EKS actions include `aws:RequestedRegion` conditions.

If `ALLOWED_CLUSTER_NAMES` is empty **and** `ALLOW_ANY_CLUSTER_NAME` is not `true`, `cdk synth` fails with:

```
Error: SsmAutomationGatewayV2: must set either `allowedClusterNames` (preferred)
or `allowAnyClusterName: true` to acknowledge that ssm:SendCommand should be
permitted against every EKS cluster in this account.
```

This prevents accidental deploys with an unrestricted instance scope.

### Per-Tool Authorization & Rate Limiting

Every invocation extracts the caller's Cognito `client_id` and `sub` from the JWT claims forwarded by the AgentCore Gateway. Two checks then run before dispatch:

1. **Per-tool ACL** — `TOOL_AUTHORIZATION` is a `;`-delimited list of `tool:client_a,client_b` entries. Tools listed get a non-empty allow-set (only those clients may invoke). Tools listed with an empty set are deny-all. Tools not listed remain open to all authenticated callers.
2. **Token-bucket rate limit** — best-effort, per-caller, in a single warm container. Default 60/min. Returns HTTP 429 with `retryAfterSeconds` when exceeded. Set `PER_CALLER_RATE_LIMIT_PER_MINUTE=0` to disable.

### EKS Instance Validation

Every tool that targets an instance validates that it belongs to an EKS cluster before acting. Validation trusts only the EKS-managed `eks:cluster-name` / `eks:nodegroup-name` tags, which cannot be set through the standard EC2 tag APIs. The user-settable `kubernetes.io/cluster/*` tag is **not** trusted on its own — an instance carrying only that tag is rejected unless `ALLOW_SELF_MANAGED_NODES=true`, in which case the derived cluster is cross-checked against the EKS API. When `ALLOWED_CLUSTER_NAMES` is set, the resolved cluster must also be in that list.

### Collection Approval (Human-in-the-Loop)

`collect` and `batch_collect` are the only tools that *mutate* — they start SSM Automation (the AWS-managed `AWSSupport-CollectEKSInstanceLogs` document) on nodes. To stop a compromised/poisoned agent from triggering collection on its own, these tools use SSM's **native `aws:approve` action** (on by default; disable with `REQUIRE_COLLECTION_APPROVAL=false`):

1. The agent calls `collect` (or `batch_collect` with `dryRun=false`). The Lambda starts a **wrapper Automation document** whose first step is `aws:approve` — the execution immediately pauses inside SSM. The response is `status: "pending_approval"` with an `approvalConsoleUrl` deep link.
2. A designated approver (an IAM principal listed in `APPROVAL_APPROVER_ARNS`) opens the link — the Systems Manager console execution page — reviews the request, and clicks **Approve** or **Deny**. Approvers are also notified via SNS. The decision is IAM-authenticated and CloudTrail-audited; no secret tokens or custom endpoints are involved.
3. On approval, the document proceeds to the collection step **automatically** — the agent never re-calls `collect`; it just polls `status(executionId)`, which reports the approval state (`pending` / `approved` / `denied_or_expired`) and then the collection progress.

The agent cannot approve its own request: the MCP Lambda has **no** `ssm:SendAutomationSignal` permission, and the approver list is fixed at deploy time (it is not a tool parameter). Pending requests time out after `APPROVAL_TTL_SECONDS` (default 15 min). For a batch, a single approval authorizes the whole batch — the wrapper document's fan-out step then starts one collection per sampled node. Note: because the wrapper documents are regional SSM documents deployed with the stack, approval-gated collection runs in the stack region only.

### Response Redaction

`redact_response` runs on every Lambda response before it returns to the gateway:

- Resource IDs (`sg-…`, `eni-…`, `subnet-…`, `vpc-…`, `vol-…`, `fs-…`) are masked to `<prefix>-***`.
- Account IDs in ARNs are replaced with `***`.
- AWS access keys (`AKIA…`, `ASIA…`) and JWT-shaped strings are masked.
- IAM/credential error message bodies (`AccessDenied`, `Unauthorized`, `not authorized to perform`, `ExpiredToken`, etc.) are collapsed to `<iam-error-details-redacted>`.
- For network-related tools (`network_diagnostics`, `cluster_health`, `storage_diagnostics`), RFC1918 + CGNAT private IPs are masked to `<private-ip>`.
- Fields whose key contains `password`, `secret`, `token`, `apikey`, or `credential` are replaced with `<redacted>`. `volumeHandle`/`volume_handle` is truncated to 24 chars.

### VPC Endpoints (optional)

Setting `MCP_VPC_ID` and `MCP_VPC_SUBNET_IDS` attaches the Lambda to your VPC and provisions a gateway endpoint for S3 plus interface endpoints for KMS, SSM, SSM Messages, EC2, CloudWatch Logs, and CloudWatch Metrics. SDK calls and presigned-URL traffic stay on the AWS network instead of the public internet.

---

## Post-Deployment: EKS Node IAM Setup

### What's Automatic

If you selected node roles during the interactive deploy flow (or passed them via `EKS_NODE_ROLE_ARNS`), the CDK stack automatically grants:

- S3 bucket policy: `s3:PutObject`, `s3:GetBucketPolicyStatus`, `s3:GetBucketAcl` on the logs bucket
- KMS key policy: `kms:GenerateDataKey`, `kms:Encrypt`, `kms:Decrypt` on the encryption key (`kms:Decrypt` is required for S3 multipart uploads of files larger than ~8 MiB)

No manual S3 or KMS setup is needed for those roles.

If no node roles were provided during deployment, the stack falls back to an account-scoped policy (any principal in the account can upload). This is less restrictive but still functional.

### What You May Still Need

The only thing the CDK stack does not attach is the SSM Agent managed policy. EKS-optimized AMIs include SSM Agent by default, but the IAM role needs the policy:

```bash
# Only needed if not already attached
aws iam attach-role-policy \
  --role-name <YOUR-NODE-ROLE-NAME> \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
```

### Adding Node Roles After Deployment

If you add new EKS clusters later, re-run the deploy script — it will detect the new node roles and update the S3 bucket and KMS key policies automatically.

Alternatively, pass the new roles directly:

```bash
EKS_NODE_ROLE_ARNS="arn:aws:iam::123456789012:role/ExistingRole,arn:aws:iam::123456789012:role/NewRole" ./deploy.sh
```

### Checklist Per Cluster

- [ ] Node role was selected during deployment (or added via re-deploy)
- [ ] Node role has `AmazonSSMManagedInstanceCore` managed policy (for SSM Agent)
- [ ] SSM Agent is running on the nodes (default on EKS-optimized AMIs)
- [ ] `AWSSupport-CollectEKSInstanceLogs` SSM document exists in the target region

---

## Configuration in DevOps Agent

After deployment, the script outputs all values needed for the MCP Server configuration:

| Setting | Value |
|---------|-------|
| MCP Server URL | `https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp` |
| OAuth Client ID | Cognito Client ID from output |
| OAuth Client Secret | Cognito Client Secret from output |
| Token URL | `https://<stack-name>-<account>.auth.<region>.amazoncognito.com/oauth2/token` |
| Scope | `ssm-automation-gateway-id/gateway:read` |

Values are also saved to `mcp-config.txt` for reference.

---

## How It Works

The server gives MCP-compatible agents the ability to collect full diagnostic bundles from EKS worker nodes, pre-index errors with severity classification, stream multi-GB log files without truncation, correlate events across log sources, compare nodes, and follow structured runbooks — all through 19 MCP tools organized in 4 tiers. The two mutating collection tools (`collect`, `batch_collect`) require human-in-the-loop approval before they run (see [Security Model](#security-model)); the other 17 read-only tools run directly.

For a detailed walkthrough of the architecture, data flows, tool design, cross-region mechanics, security model, and anti-hallucination design, see:

**[Architecture & Design →](docs/ARCHITECTURE.md)**

### MCP Tools (Quick Reference)

| Tier | Tools | Purpose |
|------|-------|---------|
| 1 — Core | `collect`†, `status`, `validate`, `errors`, `read` | Log collection, findings, streaming |
| 2 — Analysis | `search`, `correlate`, `artifact`, `summarize`, `quick_triage`, `history` | Deep investigation, correlation, summaries |
| 3 — Cluster | `cluster_health`, `compare_nodes`, `batch_collect`†, `batch_status`, `network_diagnostics`, `storage_diagnostics` | Multi-node operations |
| 4 — SOPs | `list_sops`, `get_sop` | 41 structured runbooks |

† `collect` and `batch_collect` are **mutating** (they start SSM Automation on nodes). By default they require **human-in-the-loop approval** via SSM's native `aws:approve` action: the call returns `status: "pending_approval"` with an `approvalConsoleUrl`, a designated approver clicks Approve in the Systems Manager console, and collection proceeds automatically — the agent just keeps polling `status`. See [Security Model](#security-model).

### Agent Workflow

```
collect → (human approves in SSM console) → status (poll) → validate → errors → search → correlate → read → summarize
```

> Set `REQUIRE_COLLECTION_APPROVAL=false` for a fully supervised/test deployment to skip the approval step.

### Runbook Library (41 SOPs)

| Category | Coverage |
|----------|----------|
| A — Node Lifecycle | OOM/NotReady, certificates, bootstrap, clock skew, join failures |
| B — Kubelet | Config errors, eviction, PLEG |
| C — Container Runtime | Image pull, sandbox creation, OverlayFS/inode |
| D — Networking | VPC CNI, kube-proxy, conntrack, MTU, DNS, ENA, pod-to-pod |
| E — Storage | EBS CSI, EFS mount |
| F — Scheduling | CPU/memory, max pods, taints/tolerations |
| G — Resource Pressure | Disk pressure, OOMKill, PID pressure |
| H — IAM/Security | Node role, IRSA/Pod Identity, IMDS |
| I — Upgrades | Version skew |
| J — Infrastructure | ENA/instance limits, EBS transient, AZ outage |
| K — Workload Issues | Stuck terminating pods, probe failures, CrashLoopBackOff, containerd failures, CSI plugin |
| Z — Catch-All | General troubleshooting |

---

## Usage Examples

### Basic Investigation
```
Node i-0abc123def in us-west-2 went NotReady around 3am. Collect its logs
and correlate what happened in the 5 minutes before it went down.
```

### Cluster-Wide Triage
```
We have a 200-node cluster and something is off. Do a dry run batch collection
first — show me which nodes you'd sample. Then collect from the unhealthy ones.
```

### SOP-Guided
```
I don't know what's wrong — just investigate. List the available SOPs, run a
general triage, and follow whichever runbook matches.
```

---

## CloudFormation Outputs

| Output | Description |
|--------|-------------|
| `GatewayId` | AgentCore Gateway ID |
| `GatewayUrl` | MCP Server URL |
| `CognitoUserPoolId` | Cognito User Pool ID |
| `CognitoClientId` | OAuth Client ID |
| `OAuthExchangeUrl` | OAuth Token URL |
| `OAuthScope` | OAuth Scope |
| `LogsBucketName` | S3 bucket for logs |
| `SOPBucketName` | S3 bucket for runbooks |
| `SSMAutomationRoleArn` | SSM Automation role ARN |
| `EncryptionKeyArn` | KMS key ARN |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `cdk synth` fails with "must set either `allowedClusterNames` …" | Cluster scope wasn't chosen | Set `ALLOWED_CLUSTER_NAMES=…` (preferred) or `ALLOW_ANY_CLUSTER_NAME=true` and re-run `./deploy.sh` |
| `cdk synth` fails with "`approvalApproverArns` is empty" | Approval is on but no approvers were designated | Set `APPROVAL_APPROVER_ARNS=…` (deploy.sh defaults it to the deploying principal) or `REQUIRE_COLLECTION_APPROVAL=false` for test deployments |
| `collect` stuck in `pending_approval` | No approver has acted in the SSM console | Open the `approvalConsoleUrl` from the response as a designated approver and click Approve; the request times out after `APPROVAL_TTL_SECONDS` |
| Approve button fails in the console | The signed-in principal isn't in `APPROVAL_APPROVER_ARNS` or lacks `ssm:SendAutomationSignal` | Sign in as a designated approver, or add the principal and redeploy |
| `status` shows `humanApproval: denied_or_expired` | Approver denied the request, or it timed out | Re-call `collect` to request a fresh approval if still needed |
| Tool returns 403 "Caller is not permitted to invoke '…'" | Per-tool ACL doesn't include this client | Add the client to the matching `TOOL_AUTHORIZATION` entry |
| Tool returns 429 "Rate limit exceeded" | Caller exceeded `PER_CALLER_RATE_LIMIT_PER_MINUTE` | Wait the `retryAfterSeconds` in the response, or raise the limit |
| `collect` returns "document not found" | SSM document not in target region | Use a supported region or pass `region` explicitly |
| `collect` fails at `CheckS3BucketPublicStatus` | SSM automation role missing `s3:GetBucketPublicAccessBlock` / `s3:GetAccountPublicAccessBlock` | Already granted by the current construct — redeploy if your stack predates the fix |
| Upload step fails | Node role missing S3/KMS permissions | Pass the node role via `EKS_NODE_ROLE_ARNS` and redeploy |
| `status` returns wrong region | Region metadata not persisted | Pass `region` explicitly |
| Auto-detection times out | Instance in uncommon region | Add the region to `ALLOWED_REGIONS` and pass `region` explicitly |
| `errors` returns empty | Findings indexer hasn't run yet | Wait a few seconds after `validate`, or use `search` |
| Response missing IDs that should be there (e.g. `sg-…`) | Redaction layer is masking them | Expected — `redact_response` masks SG/ENI/subnet/VPC IDs and account IDs by design |

---

## Cleanup

```bash
cdk destroy
```

> The logs and SOP buckets are configured with `removalPolicy: DESTROY` and `autoDeleteObjects: true`, so `cdk destroy` will delete the buckets and all their contents. Download anything you need from `eksnodelogmcpstack-logs-<account>` first.

---
