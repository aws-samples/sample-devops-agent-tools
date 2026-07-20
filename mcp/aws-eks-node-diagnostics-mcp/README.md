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

### 6. crictl on Worker Nodes (for pod-level tcpdump)

Required only for `tcpdump_capture` with `podName`/`podNamespace`. EKS-optimized AMIs include it by default.

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
PCAP_PRESIGNED_URL_EXPIRATION=30 \
PER_CALLER_RATE_LIMIT_PER_MINUTE=30 \
TOOL_AUTHORIZATION="collect:client-soc;tcpdump_capture:client-emergency" \
MCP_VPC_ID=vpc-0123456789abcdef0 \
MCP_VPC_SUBNET_IDS=subnet-aaa,subnet-bbb \
MAX_PCAP_BYTES=104857600 \
./deploy.sh
```

| Env var | What it restricts | Default |
|---------|-------------------|---------|
| `ALLOWED_REGIONS` | IAM resource ARNs + Lambda region scanning | Stack region |
| `ALLOWED_CLUSTER_NAMES` | `ssm:SendCommand` tag condition on instances | (none — fail-closed) |
| `ALLOW_ANY_CLUSTER_NAME` | Explicit opt-in to any-cluster wildcard | `false` |
| `ALLOWED_SSM_DOCUMENTS` | Which SSM documents can be executed | `AWS-RunShellScript` |
| `EKS_NODE_ROLE_ARNS` | S3 PutObject + KMS Encrypt principals | Account root |
| `PRESIGNED_URL_EXPIRATION` | Log artifact presigned URL lifetime (max 900 s) | 300 s |
| `PCAP_PRESIGNED_URL_EXPIRATION` | Pcap presigned URL lifetime (max 300 s) | 60 s |
| `ENABLED_RESTRICTED_TOOLS` | tcpdump tools availability | Empty (not available) |
| `TOOL_AUTHORIZATION` | Per-tool client-id ACL (`tool:client_a,client_b;…`) | Empty (open) |
| `PER_CALLER_RATE_LIMIT_PER_MINUTE` | Rate limit per caller (`0` disables) | 60 |
| `MCP_VPC_ID` / `MCP_VPC_SUBNET_IDS` | Run Lambda in VPC + create S3/KMS endpoints | None |
| `MAX_PCAP_BYTES` | Pcap upload size cap (warning only) | 200 MiB |

### What Gets Deployed

| Resource | Purpose |
|----------|---------|
| S3 Bucket (KMS encrypted) | Stores collected log bundles |
| S3 Bucket (SOPs) | Stores 41 runbooks, auto-deployed via CDK |
| Lambda (SSM Automation) | Handles all 21 MCP tool invocations (19 always-on + 2 restricted tcpdump) |
| Lambda (Unzip) | Auto-extracts uploaded archives |
| Lambda (Findings Indexer) | Pre-indexes errors for fast retrieval |
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
| **tcpdump tools** | Removed from routing table | `ENABLED_RESTRICTED_TOOLS` env var |
| **Presigned URL expiry (logs)** | 300 s, max 900 s | `PRESIGNED_URL_EXPIRATION` env var |
| **Presigned URL expiry (pcap)** | 60 s, max 300 s | `PCAP_PRESIGNED_URL_EXPIRATION` env var |
| **Per-tool authorization** | All authenticated callers may invoke any non-restricted tool | `TOOL_AUTHORIZATION` env var |
| **Per-caller rate limit** | 60 invocations / min / caller | `PER_CALLER_RATE_LIMIT_PER_MINUTE` env var (0 disables) |
| **VPC endpoints (S3, KMS, SSM, EC2, Logs, Metrics)** | Off (Lambda runs outside a VPC) | `MCP_VPC_ID` + `MCP_VPC_SUBNET_IDS` |
| **Pcap upload bound** | 200 MiB (warns when exceeded) | `MAX_PCAP_BYTES` env var |
| **Response redaction** | SG/ENI/subnet/VPC IDs, account IDs in ARNs, private IPs (network tools), IAM error bodies, JWT/AKIA tokens, fields named `*password*`/`*secret*`/`*token*`/`*credential*` | Always on |
| **S3 encryption** | SSE-KMS with auto-rotating key | `enableEncryption` CDK prop |
| **S3 public access** | Blocked | Always on |
| **S3 transport** | SSL enforced | Always on |
| **Authentication** | Cognito OAuth2 client credentials | Always on |
| **EKS instance validation** | Tag-based + EKS API cross-reference | Always on |
| **BPF filter validation** | Allowlist-based (not denylist) | Always on |
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

### tcpdump Tools

`tcpdump_capture` and `tcpdump_analyze` are **not available by default**. They are completely removed from the Lambda's tool routing table — they don't appear in `available_tools` and cannot be invoked. To enable them, set `ENABLED_RESTRICTED_TOOLS=tcpdump_capture,tcpdump_analyze` before deploying. Even when enabled:

- Each capture requires `confirmCapture=true`.
- tcpdump will not be auto-installed on nodes (the script bails with manual install instructions).
- Pcap presigned URLs use the shorter `PCAP_PRESIGNED_URL_EXPIRATION` window (default 60 s).
- Captures larger than `MAX_PCAP_BYTES` (default 200 MiB) surface a warning in the response.

### Response Redaction

`redact_response` runs on every Lambda response before it returns to the gateway:

- Resource IDs (`sg-…`, `eni-…`, `subnet-…`, `vpc-…`, `vol-…`, `fs-…`) are masked to `<prefix>-***`.
- Account IDs in ARNs are replaced with `***`.
- AWS access keys (`AKIA…`, `ASIA…`) and JWT-shaped strings are masked.
- IAM/credential error message bodies (`AccessDenied`, `Unauthorized`, `not authorized to perform`, `ExpiredToken`, etc.) are collapsed to `<iam-error-details-redacted>`.
- For network-related tools (`network_diagnostics`, `tcpdump_*`, `cluster_health`, `storage_diagnostics`), RFC1918 + CGNAT private IPs are masked to `<private-ip>`.
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

## Agent Skills

A pre-built [Agent Skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html) is included in `skills/` to teach AWS DevOps Agent how to use this MCP server effectively. The skill loads investigation workflows, anti-hallucination guardrails, and all 41 runbook procedures into the agent's context — complementing the runtime `list_sops`/`get_sop` tools.

| Skill Zip | Size | Contents |
|-----------|------|----------|
| [`eks-node-diagnostics.zip`](skills/eks-node-diagnostics.zip) | 188K | 21-tool workflow, 41 runbooks, VPC CNI anti-hallucination rules, storage guardrails |

### Uploading the Skill

1. Navigate to the **Skills** page in your [Agent Space Operator Web App](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html)
2. Click **Add skill** → **Upload skill**
3. Upload `skills/eks-node-diagnostics.zip`
4. Set Agent Type to **Generic** (all agent types)
5. Click **Upload**

For more details, see the [AWS DevOps Agent Skills documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html).

> **Skills vs SOPs**: The skill loads at investigation start (upfront methodology and gotchas). SOPs are fetched on-demand via `get_sop` (detailed step-by-step procedures). Use both together for best results.

---

## How It Works

The server gives MCP-compatible agents the ability to collect full diagnostic bundles from EKS worker nodes, pre-index errors with severity classification, stream multi-GB log files without truncation, correlate events across log sources, run live tcpdump captures, compare nodes, and follow structured runbooks — all through 21 MCP tools (19 always-on + 2 restricted tcpdump tools, opt-in) organized in 5 tiers.

For a detailed walkthrough of the architecture, data flows, tool design, cross-region mechanics, security model, and anti-hallucination design, see:

**[Architecture & Design →](docs/ARCHITECTURE.md)**

### MCP Tools (Quick Reference)

| Tier | Tools | Purpose |
|------|-------|---------|
| 1 — Core | `collect`, `status`, `validate`, `errors`, `read` | Log collection, findings, streaming |
| 2 — Analysis | `search`, `correlate`, `artifact`, `summarize`, `quick_triage`, `history` | Deep investigation, correlation, summaries |
| 3 — Cluster | `cluster_health`, `compare_nodes`, `batch_collect`, `batch_status`, `network_diagnostics`, `storage_diagnostics` | Multi-node operations |
| 4 — Capture | `tcpdump_capture`, `tcpdump_analyze` | Live packet capture (**disabled by default**) |
| 5 — SOPs | `list_sops`, `get_sop` | 41 structured runbooks |

> **Note:** Tier 4 tools are removed from the routing table by default. They don't appear in `available_tools` and cannot be invoked unless `ENABLED_RESTRICTED_TOOLS` includes them. See [Security Model](#security-model).

### Agent Workflow

```
collect → status (poll) → validate → errors → search → correlate → read → summarize
```

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

### Live Packet Capture

> **Requires:** `ENABLED_RESTRICTED_TOOLS=tcpdump_capture,tcpdump_analyze` set at deploy time. tcpdump must be pre-installed on the node AMI. Each capture requires `confirmCapture=true`.

```
Pods on node i-0abc123def can't reach the API server. Run a 2-minute tcpdump
filtered on port 443, then analyze — show me RST counts and retransmissions.
```

### Pod-Level Capture

> **Requires:** Same as above, plus `crictl` on the node (default on EKS-optimized AMIs).

```
DNS lookups are timing out. CoreDNS pod coredns-5d78c9869d-abc12 is on node
i-0abc123def in kube-system. Capture UDP port 53 from inside the pod for 60s.
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
| Tool returns 403 "Tool '…' is restricted and not enabled" | Caller hit `tcpdump_capture` / `tcpdump_analyze` without opt-in | Set `ENABLED_RESTRICTED_TOOLS=tcpdump_capture,tcpdump_analyze` and redeploy |
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

## License

This project is licensed under the MIT No Attribution (MIT-0) License. See the [LICENSE](LICENSE) file.
