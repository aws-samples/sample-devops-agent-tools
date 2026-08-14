# ECS Instance Log MCP

> **⚠️ Proof of Concept (POC):** This project is a proof of concept and should be tested in non-production environments first. Validate thoroughly in a staging or development account before using with production workloads.

MCP Server for AWS DevOps Agent to collect and analyze diagnostic logs from ECS container instances using SSM Automation. Covers ECS agent, Docker/containerd, container logs, system logs, dmesg, networking, cgroups, instance metadata, and GPU diagnostics — artifacts that live on the instance OS and aren't accessible through the ECS API or CloudWatch.

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
git clone <repo-url>
cd ecs-instance-log-mcp

# Make the script executable
chmod +x deploy.sh

# Deploy (defaults to us-east-1 and stack EcsInstanceLogMcpStack)
./deploy.sh

# Update a specifically named existing stack
./deploy.sh EcsLogGatewayStack

# Or deploy to a specific region
AWS_REGION=us-west-2 ./deploy.sh
```

### Interactive Deployment Flow

The deploy script walks you through three interactive prompts:

**Step 1 — Region selection:**
```
Which AWS regions should be scanned for ECS clusters?

  1) All enabled regions
  2) Current deploy region only (us-east-1)
  3) Enter a specific region

Select [1/2/3] (default: 1):
```

**Step 2 — Cluster selection:**
```
Found 4 ECS cluster(s):

  1) prod-cluster    (us-east-1)
  2) dev-cluster     (us-east-1)
  3) analytics        (us-west-2)
  4) eu-cluster       (eu-west-1)

  a) All clusters

Select clusters (comma-separated numbers, or 'a' for all) [default: a]:
```

**Step 3 — Instance role selection:**
```
Found 3 unique container instance role(s):

  1) arn:aws:iam::123456789012:role/ecsInstanceRole
     └─ ecsInstanceRole  (prod-cluster / us-east-1)
  2) arn:aws:iam::123456789012:role/ecs-dev-role
     └─ ecs-dev-role  (dev-cluster / us-east-1)
  3) arn:aws:iam::123456789012:role/ecs-eu-role
     └─ ecs-eu-role  (eu-cluster / eu-west-1)

  a) All roles

Select instance roles (comma-separated numbers, or 'a' for all) [default: a]:
```

**Fail-closed discovery behavior:**

If no ECS clusters are found, deployment stops because a nonempty exact cluster allowlist is mandatory. If selected clusters contain no EC2 container instances (for example, they are Fargate-only), the script permits manual entry of explicit ECS instance-role ARNs; it never grants account-wide upload access.

### Non-Interactive / CI Mode

Skip all prompts by providing the mandatory cluster, region, and role allowlists:

```bash
export ALLOWED_CLUSTER_NAMES="prod-cluster"
export ALLOWED_REGIONS="us-east-1"
export ECS_INSTANCE_ROLE_ARNS="arn:aws:iam::123456789012:role/ecsInstanceRole"
./deploy.sh

# Multiple values are comma-separated.
ALLOWED_CLUSTER_NAMES="prod-cluster,dev-cluster" \
ALLOWED_REGIONS="us-east-1,us-west-2" \
ECS_INSTANCE_ROLE_ARNS="arn:aws:iam::123456789012:role/Role1,arn:aws:iam::123456789012:role/Role2" \
./deploy.sh
```

Deployment fails closed when any allowlist is empty. There is no account-scoped upload compatibility fallback.

### Human approval and restricted tools

Native Systems Manager approval is enabled by default. CDK synthesis fails closed unless `APPROVAL_APPROVER_ARNS` contains at least one IAM user or role ARN. The Automation documents embed the deployment-owned role, approvers, and SNS topic; callers cannot replace those values. Approvers need `ssm:SendAutomationSignal`. Email subscribers must confirm the SNS subscription before notifications are delivered.

```bash
export APPROVAL_APPROVER_ARNS="arn:aws:iam::123456789012:role/EcsDiagnosticsApprover"
export APPROVAL_NOTIFICATION_EMAILS="oncall@example.com"
export APPROVAL_TTL_SECONDS=900

# tcpdump tools are hidden unless explicitly enabled.
export ENABLED_RESTRICTED_TOOLS="tcpdump_capture,tcpdump_analyze"
export PCAP_PRESIGNED_URL_EXPIRATION=60
export MAX_PCAP_BYTES=209715200

./deploy.sh
```

`collect`, `batch_collect(dryRun=false)`, and new `tcpdump_capture` requests pause at a native `aws:approve` step. The Lambda has no `ssm:SendAutomationSignal` permission and, while approval is enabled, no direct `ssm:SendCommand` permission. `batch_collect` defaults to `dryRun=true`; one approval fans out to at most 15 sampled child collections, and `batch_status` reports partial fan-out failures. Approval wrapper documents exist only in the stack region, so approval-gated operations must target that region. Deploy a stack in each required region rather than disabling approval.

Set `REQUIRE_COLLECTION_APPROVAL=false` only for an explicitly supervised test deployment. Direct tcpdump Run Command permission is added only when approval is disabled **and** `tcpdump_capture` is enabled.

Packet capture is opt-in and limited to ECS tasks on EC2. A new capture requires `instanceId`, an exact task ID or ARN, and `confirmCapture=true`. `containerName` must match one RUNNING application container; it may be omitted only when exactly one eligible container exists. The node re-resolves the container PID immediately before `nsenter`, rejects PID/namespace changes and the host network namespace, and never installs tcpdump. Fargate and host-wide captures are unsupported. `tcpdump_analyze` requires the exact `commandId`; there is no latest-capture fallback. Packet data may contain sensitive payloads, so minimize filters/duration and use the short-lived pcap URL.

### AppSec parity evidence

| ID | Control | Enforcement and test evidence |
|---|---|---|
| M1 | Authenticated tool surface | Cognito OAuth2 protects AgentCore; restricted schemas/routes are absent unless explicitly enabled (`test_tcpdump_security.py`). |
| M2 | Mandatory encryption | KMS customer-managed key, TLS-only S3, public-access block; synthesis rejects disabled KMS (`test_security_parity.py`). |
| M3 | Least-privilege identities | Explicit ECS instance-role principals are mandatory; no `AnyPrincipal` or account fallback (`test_security_parity.py`). |
| M4 | Human authorization | Default-on native SSM `aws:approve`; Lambda cannot approve and has no direct Run Command permission in approval mode (`test_collection_approval.py`, `test_tcpdump_approval.py`). |
| E1 | Deployment scope | Exact cluster and region allowlists fail closed and are applied to all live API paths (`test_instance_validation.py`, `test_security_parity.py`). |
| E2 | Target identity | Exact EC2 ID plus ACTIVE ECS container-instance membership; tags and names are not trusted (`test_instance_validation.py`). |
| E3 | Artifact isolation | Generic reads require `instanceId` and canonical `ecs_{instanceId}/...` keys; metadata, cross-instance paths, traversal, and pcaps are rejected (`test_security_parity.py`). |
| E4 | Search resource bounds | Unsafe regex structures are rejected and every scanned file has an interruptible hard timeout (`test_security_parity.py`). |
| E5 | Polling provenance | Status and batch polling accept only gateway-created IDs bound to expected document, region, and instance; batch polling requires opaque `batchId` (`test_collection_approval.py`, `test_batch_approval.py`). |
| E6 | Invasive-operation containment | Tcpdump requires explicit opt-in, confirmation, approval, exact task/container/PID/netns, safe BPF, and short-lived artifacts; batch is dry-run by default and capped at 15 (`test_tcpdump_security.py`, `test_batch_approval.py`). |

### What Gets Deployed

| Resource | Purpose |
|----------|---------|
| S3 Bucket (KMS encrypted) | Stores collected log bundles |
| S3 Bucket (SOPs) | Stores 36 runbooks, auto-deployed via CDK |
| Lambda (ECS Log Automation) | Handles MCP tool invocations (17 by default; up to 19 with restricted tools enabled) |
| Lambda (Unzip) | Auto-extracts uploaded archives |
| Lambda (Findings Indexer) | Pre-indexes errors for fast retrieval |
| SSM Automation Role | Runs approval wrappers, log collection, approved task-scoped capture, and batch child automations |
| SNS Approval Topic | Notifies configured approvers of pending SSM Automation requests |
| SSM Automation Documents | Native approval wrappers for single collection, batch collection, and task-scoped tcpdump |
| Cognito User Pool | OAuth2 authentication for MCP Gateway |
| BedrockAgentCore Gateway | MCP protocol endpoint |
| KMS Key | Encrypts all data at rest |

---

## Post-Deployment: ECS Instance IAM Setup

### What's Automatic

The CDK stack requires explicit `ECS_INSTANCE_ROLE_ARNS` and grants only those roles:

- S3 bucket policy: bucket-level `s3:ListBucket`, `s3:GetBucketPolicyStatus`, and `s3:GetBucketAcl` for the support document's `HeadBucket` preflight; object-level `s3:PutObject` for uploads
- KMS key policy: `kms:GenerateDataKey`, `kms:Encrypt` on the encryption key

After deployment, `deploy.sh` reads the live bucket policy and fails with an actionable error unless every configured ECS instance role has all required preflight and upload actions. This prevents a successful stack deployment from surfacing later as an SSM `HeadBucket` 403 during collection.

Synthesis fails when no explicit instance role is configured; there is no account-scoped principal fallback.

### What You May Still Need

The only thing the CDK stack does not attach is the SSM Agent managed policy. ECS-optimized AMIs include SSM Agent by default, but the IAM role needs the policy:

```bash
# Only needed if not already attached
aws iam attach-role-policy \
  --role-name <YOUR-INSTANCE-ROLE-NAME> \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
```

### Adding Instance Roles After Deployment

If you add new ECS clusters later, re-run the deploy script — it will detect the new instance roles and update the S3 bucket and KMS key policies automatically.

Alternatively, pass the new roles directly:

```bash
ECS_INSTANCE_ROLE_ARNS="arn:aws:iam::123456789012:role/ExistingRole,arn:aws:iam::123456789012:role/NewRole" ./deploy.sh
```

### Checklist Per Cluster

- [ ] Instance role was selected during deployment (or added via re-deploy)
- [ ] Instance role has `AmazonSSMManagedInstanceCore` managed policy (for SSM Agent)
- [ ] SSM Agent is running on the instances (default on ECS-optimized AMIs)
- [ ] `AWSSupport-CollectECSInstanceLogs` SSM document exists in the target region

---

## Configuration in DevOps Agent

After deployment, the script outputs the non-secret values needed for MCP Server configuration:

| Setting | Value |
|---------|-------|
| MCP Server URL | `https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp` |
| OAuth Client ID | Cognito Client ID from output |
| Token URL | `https://<stack-name>-<account>.auth.<region>.amazoncognito.com/oauth2/token` |
| Scope | `ecs-log-gateway-id/gateway:read` |

Non-secret values are saved to ignored `mcp-config.txt`. The OAuth client secret is deliberately not retrieved, printed, or written by `deploy.sh`; retrieve it only at the point of secure MCP client registration.

---

## How It Works

The server gives MCP-compatible agents the ability to collect full diagnostic bundles from ECS container instances, pre-index errors with severity classification, stream multi-GB log files without truncation, correlate events across log sources, run opt-in task-scoped tcpdump captures, compare instances, and follow structured runbooks — through 17 tools by default (up to 19 when the two restricted tcpdump tools are enabled), organized in 5 tiers.

For a detailed walkthrough of the architecture, data flows, tool design, cross-region mechanics, security model, and anti-hallucination design, see:

**[Architecture & Design →](docs/ARCHITECTURE.md)**

### MCP Tools (Quick Reference)

| Tier | Tools | Purpose |
|------|-------|---------|
| 1 — Core | `collect`, `status`, `validate`, `errors`, `read` | Log collection, findings, streaming |
| 2 — Analysis | `search`, `correlate`, `artifact`, `summarize`, `history` | Deep investigation, correlation, summaries |
| 3 — Cluster | `cluster_health`, `compare_instances`, `batch_collect`, `batch_status`, `network_diagnostics` | Multi-instance operations |
| 4 — Capture (opt-in) | `tcpdump_capture`, `tcpdump_analyze` | Human-approved, task-scoped packet capture on ECS EC2 only |
| 5 — SOPs | `list_sops`, `get_sop` | 36 structured runbooks |

### Agent Workflow

```
collect → status (poll) → validate → errors → search → correlate → read → summarize
```

### Runbook Library (36 SOPs)

| Category | Coverage |
|----------|----------|
| A — Task Startup | Resource initialization, container runtime failures |
| B — Image Pull | ECR auth, image not found, Docker Hub rate limits |
| C — IAM/Secrets | Task execution role, Secrets Manager retrieval |
| D — Resource Exhaustion | OOM kills, disk space, CPU throttling |
| E — Networking | ENI allocation, DNS resolution, connection timeouts |
| F — Health Checks | Container health checks, ELB target health |
| G — ECS Agent | Agent disconnected, instance registration failure |
| H — Logging | CloudWatch log driver configuration |
| I — Deployment | Circuit breaker triggers, rollbacks |
| J — Container Runtime | OCI runtime, entrypoint issues, architecture mismatch |
| K — Extended | Spot interruption, task placement, steady state, auto scaling, ECS Exec, Service Connect, volume mounts, stuck pending, Fargate, API throttling, Windows, performance, Docker daemon |
| Z — Catch-All | General troubleshooting |

---

## Usage Examples

### Basic Investigation
```
Container instance i-0abc123def in us-west-2 has tasks failing to start.
Collect its logs and correlate what happened in the last 10 minutes.
```

### Cluster-Wide Triage
```
We have a 50-instance ECS cluster and something is off. Do a dry run batch
collection first — show me which instances you'd sample. Then collect from
the unhealthy ones.
```

### Live Packet Capture
```
Tasks on instance i-0abc123def can't reach the backend service. Run a 2-minute
tcpdump filtered on port 8080, then analyze — show me RST counts and retransmissions.
```

### Task-Level Capture
```
Task abc123 on instance i-0abc123def has connection timeouts. Capture traffic
scoped to that task's network namespace for 60 seconds on port 443.
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
| `collect` returns "document not found" | SSM document not in target region | Use a supported region or pass `region` explicitly |
| Upload step fails | Instance role missing S3/KMS permissions | Re-run deploy with the instance role selected |
| `status` returns wrong region | Region metadata not persisted | Pass `region` explicitly |
| Auto-detection times out | Instance in uncommon region | Pass `region` explicitly |
| `errors` returns empty | Findings indexer hasn't run yet | Wait a few seconds after `validate`, or use `search` |
| Collection succeeds but no extracted bundle appears | `.tgz` S3 notification or canonical extraction mapping is missing | Re-deploy the current stack; `deploy.sh` validates `.zip`, `.tar.gz`, and `.tgz` notifications |
| `tcpdump_capture` uploads fail | Instance role missing S3 PutObject | Re-run deploy with the instance role selected |

---

## Cleanup

```bash
cdk destroy
```

> The S3 bucket has `removalPolicy: DESTROY` with `autoDeleteObjects: true`, so it will be cleaned up with the stack.

---
