# EKS Operation Review — AWS DevOps Agent Skill

A comprehensive Amazon EKS operational review skill for [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html). Conducts best-practices assessments aligned with the [EKS Best Practices Guide](https://docs.aws.amazon.com/eks/latest/best-practices/introduction.html) and generates a shareable report artifact per cluster.

## What It Does

When activated via Chat, this skill instructs the DevOps Agent to:

1. Discover EKS clusters in the configured account/regions.
2. Collect cluster configuration, K8s resources, node groups, add-ons, networking, security, and workloads. **K8s API is preferred** when reachable; AWS APIs are used as fallback.
3. Collect 7-day historical CloudWatch metrics, control-plane logs, and CloudTrail events.
4. Analyze against 12 EKS best-practices sections (Security, Reliability, Networking, Scalability, Cost Optimization, Karpenter, Cluster Upgrades, etc.).
5. Generate a shareable report artifact per cluster, named `eks-review-<cluster-name>-<YYYY-MM-DD>.md`.

## Agent Types

This skill is intended for the following agent types (selected in the Operator Web App at upload time):

- **On-demand** — conversational invocation in Chat ("review my EKS cluster", "EKS health check").
- **Evaluation** — proactive operational improvement recommendations.

Select **Generic** instead if you want the skill available to all agent types.

## Prerequisites

### 1. An AWS DevOps Agent Space with the target AWS account

You need an existing [Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html) with the target AWS account configured as a cloud source.

### 2. Integrate the DevOps Agent with each EKS cluster

This grants the Agent Space's IAM role read-only Kubernetes API access via an EKS access entry. Repeat **for each cluster** you want to review.

> Reference: [AWS EKS access setup](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-capabilities-for-aws-devops-agent-aws-eks-access-setup.html)

**a. Get the Agent Space IAM role ARN**

In the AWS DevOps Agent console, open your Agent Space → **Capabilities** → **Cloud** → **Primary Source** → **Edit**. Copy the **IAM role ARN**.

**b. Verify cluster authentication mode**

In the [Amazon EKS console](https://console.aws.amazon.com/eks), open the cluster → **Access** tab. The **Authentication mode** must include **EKS API**. If it doesn't, switch to a mode that does (note: this change cannot be reverted).

**c. Create the access entry**

On the cluster's **Access** tab:

1. Click **Create access entry**.
2. **IAM principal**: paste the Agent Space IAM role ARN from step (a).
3. Click **Next**.
4. **Access policy**: select the AWS managed policy **`AmazonAIOpsAssistantPolicy`**.
5. **Access scope**: choose **Cluster** (or specific Kubernetes namespaces if you want to limit visibility).
6. Click **Add Policy** → **Next** → **Create**.

**d. Verify**

In the Operator Web App Chat, ask: *"list all pods in the default namespace on cluster `<name>`"*. If pods are returned, access is configured.

If the agent can't reach the cluster, check that the access entry uses the exact IAM role ARN shown in the Agent Space dialog and that `AmazonAIOpsAssistantPolicy` is attached.

### 3. (Conditional) Private connectivity for clusters with a private API endpoint

If the cluster's API server endpoint access is **private only**, the AWS DevOps Agent service can't reach the K8s API over the public network. You have two options:

**Option A — enable public + private endpoint access**

Easiest path. In the EKS console → cluster → **Networking** → **Manage networking** → enable **Public and private** API server endpoint access. Restrict the public endpoint with **public access CIDRs** to lock it down. This is the supported, simplest setup.

**Option B — create a private connection from the Agent Space to your VPC**

Use the AWS DevOps Agent **private connection** mechanism, which sets up an Amazon VPC Lattice resource gateway with ENIs in your VPC, so the agent can reach private host addresses without exposing them to the internet.

> Reference: [Connecting to privately hosted tools](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-capabilities-for-aws-devops-agent-connecting-to-privately-hosted-tools.html)

> **Note on EKS coverage.** The capability providers that currently bind to a private connection are **GitHub, GitLab, MCP Server, and Grafana**. EKS isn't a listed capability provider in this list, so the most reliable option for fully private API endpoints today remains **Option A**. The steps below describe the generic private-connection mechanism for completeness — verify against the current docs that EKS is supported as a target before relying on it in production.

**Prerequisites for the private connection:**

- An active Agent Space.
- A target service (here, the EKS private API endpoint) reachable at a private DNS name or IP from the chosen VPC, serving HTTPS with TLS 1.2+ on a known port (EKS API: TCP 443).
- 1–20 subnets in the VPC where the resource-gateway ENIs will live (multi-AZ recommended; one subnet per AZ).
- (Optional) Up to 5 security groups to attach to the ENIs. If omitted, a default SG scoped to the chosen ports is created.
- The cluster's API endpoint security group must allow inbound TCP 443 from the resource-gateway ENI security group (or the VPC CIDR).
- Verify the chosen subnets are not in any of the [VPC Lattice unsupported AZs](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-capabilities-for-aws-devops-agent-connecting-to-privately-hosted-tools.html#create-a-private-connection).

**Console steps:**

1. Open the AWS DevOps Agent console.
2. **Capability providers** → **Private connections** → **Create a new connection**.
3. **Name**: e.g. `eks-private-api`.
4. **VPC**: the VPC routable to the cluster's private endpoint.
5. **Subnets**: 1–20 subnets, multi-AZ.
6. **IP address type**: `IPv4` (typical for EKS).
7. (Optional) **Security groups**: SGs that allow egress to TCP 443 of the cluster API endpoint.
8. **Port ranges**: `443`.
9. **Host address**: the cluster's private API endpoint DNS name (from EKS console → cluster → **Overview** → **API server endpoint**, e.g. `<id>.gr7.<region>.eks.amazonaws.com`). Must be resolvable from the VPC.
10. (Optional) **Certificate public key**: only needed if the endpoint uses a private CA — EKS uses public AWS-issued certs, so this is normally not required.
11. **Create connection**. Status `CREATE_IN_PROGRESS` → `ACTIVE` (up to ~10 min).

**CLI equivalent:**

```bash
aws devops-agent create-private-connection \
  --name eks-private-api \
  --mode '{
    "serviceManaged": {
      "hostAddress": "<cluster-id>.gr7.<region>.eks.amazonaws.com",
      "vpcId": "vpc-xxxxxxxxxxxxxxxxx",
      "subnetIds": ["subnet-aaa", "subnet-bbb"],
      "securityGroupIds": ["sg-xxxxxxxxxxxxxxxxx"],
      "portRanges": ["443"]
    }
  }'

aws devops-agent describe-private-connection --name eks-private-api
```

**Troubleshooting:**

- ENI security group must allow outbound TCP 443; the cluster's endpoint SG must allow inbound TCP 443 from the ENI SG (or VPC CIDR).
- Subnet route tables must reach the cluster's endpoint network.
- If `CREATE_FAILED`: check VPC Lattice quotas, subnet IP availability, and any SCPs blocking the service-linked role.
- If using a hub-and-spoke VPC Lattice topology you already manage, use the `selfManaged` mode with an existing `resourceConfigurationId` — see [Advanced setup](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-capabilities-for-aws-devops-agent-connecting-to-privately-hosted-tools.html#advanced-setup-using-existing-vpc-lattice-resources).

## Uploading to AWS DevOps Agent

You can add this skill to your Agent Space in three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/eks-operation-review` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository — including this one — even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Package the skill from the `skills/` directory in this repo:

   ```bash
   cd skills
   zip -r eks-operation-review.zip eks-operation-review/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
   ```

   Constraints (enforced at upload time):

   - Total zip size ≤ **6 MB**.
   - `SKILL.md` is required and must include `name` and `description` frontmatter.
   - A `scripts/` directory is **not** allowed — uploads containing scripts are rejected.

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop `eks-operation-review.zip` (or browse to it, max 6 MB).
5. Select agent types: **On-demand** and **Evaluation** (or leave **Generic** to make it available to all agent types).
6. Review the validation results.
7. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign the skill to the `CHAT` and `PREVENTION` agent types. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

For more details, see [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) in the AWS DevOps Agent User Guide.

### (Optional) Connect additional observability sources

For richer analysis, connect your observability tools to the Agent Space:

| Tool | Setup Guide |
|------|-------------|
| CloudWatch | Built-in (no setup needed) |
| Datadog | [Connecting Datadog](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-telemetry-sources-connecting-datadog.html) |
| Dynatrace | [Connecting Dynatrace](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-telemetry-sources-connecting-dynatrace.html) |
| New Relic | [Connecting New Relic](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-telemetry-sources-connecting-new-relic.html) |
| Splunk | [Connecting Splunk](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-telemetry-sources-connecting-splunk.html) |
| Grafana | [Connecting Grafana](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-telemetry-sources-connecting-grafana.html) |
| Custom MCP | [Connecting MCP Servers](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-capabilities-for-aws-devops-agent-connecting-mcp-servers.html) |

## Usage

In the DevOps Agent Chat, use natural language:

- *"Run an EKS operational review for all clusters."*
- *"Review my EKS cluster `prod` in `us-east-1` for best practices."*
- *"Audit EKS security and cost optimization."*
- *"Generate an EKS best-practices report for cluster `genai-workshop`."*

The agent will:

- Collect all data automatically (no prompts for confirmation).
- Use the K8s API first when reachable; fall back to AWS APIs.
- Generate a report artifact per cluster, named `eks-review-<cluster-name>-<YYYY-MM-DD>.md`.

## Skill Contents

```
eks-operation-review/
├── SKILL.md                           # main skill instructions (with frontmatter)
├── README.md                          # this file
├── references/
│   ├── best-practices-checklist.md    # checklist mapped to EKS Best Practices Guide
│   └── metrics-thresholds.md          # CloudWatch metric thresholds & severity rules
└── evals/                             # evaluation data (not included in upload zip)
```

`evals/` is for skill evaluation tracking and is **not** required at the cluster — keep it out of the upload zip if you want to minimize size (the `zip -r eks-operation-review.zip eks-operation-review/` command above includes it; if you need to slim down, exclude it explicitly: `zip -r eks-operation-review.zip eks-operation-review/ -x 'eks-operation-review/evals/*'`).

## Best-Practices Sections Covered

| # | Section | Reference |
|---|---------|-----------|
| 1 | Security (IAM, Pod Security, Network, Encryption, etc.) | [security.html](https://docs.aws.amazon.com/eks/latest/best-practices/security.html) |
| 2 | Reliability (Applications, Control Plane, Data Plane) | [reliability.html](https://docs.aws.amazon.com/eks/latest/best-practices/reliability.html) |
| 3 | Karpenter | [karpenter.html](https://docs.aws.amazon.com/eks/latest/best-practices/karpenter.html) |
| 4 | Cluster Autoscaler | [cas.html](https://docs.aws.amazon.com/eks/latest/best-practices/cas.html) |
| 5 | EKS Auto Mode | [automode.html](https://docs.aws.amazon.com/eks/latest/best-practices/automode.html) |
| 6 | Networking | [networking.html](https://docs.aws.amazon.com/eks/latest/best-practices/networking.html) |
| 7 | Scalability + Data Plane Scaling | [scalability.html](https://docs.aws.amazon.com/eks/latest/best-practices/scalability.html), [scale-data-plane.html](https://docs.aws.amazon.com/eks/latest/best-practices/scale-data-plane.html) |
| 8 | Cluster Upgrades | [cluster-upgrades.html](https://docs.aws.amazon.com/eks/latest/best-practices/cluster-upgrades.html) |
| 9 | Cost Optimization | [cost-opt.html](https://docs.aws.amazon.com/eks/latest/best-practices/cost-opt.html) |
| 10–12 | Windows / Hybrid / AI-ML (conditional) | — |

## Severity Definitions

| Severity | Definition | SLA |
|----------|------------|-----|
| CRITICAL | Immediate risk to availability, security, or data integrity | 24–48 hours |
| HIGH | Significant gap that could lead to incidents | 1 week |
| MEDIUM | Notable improvement opportunity | 30 days |
| LOW | Minor optimization or hardening | When convenient |
| INFO | Observation, no action required | N/A |

## License

Internal use.
