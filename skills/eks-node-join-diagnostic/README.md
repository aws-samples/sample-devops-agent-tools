# EKS Node Join Diagnostic — AWS DevOps Agent Skill

A focused Amazon EKS node registration diagnostic skill for [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html). Identifies why worker nodes fail to join an EKS cluster by running 64 read-only API-side configuration checks across 25 failure domains, returning a ranked pass/fail checklist with specific remediation for each failure.

## What It Does

When activated via Chat or during Incident RCA, this skill instructs the DevOps Agent to:

1. Gather cluster and node group context via AWS APIs.
2. Route to the appropriate diagnostic depth using **Adaptive Depth**:
   - **Fast path** — symptom maps directly to known root cause → validate subset, skip unrelated checks
   - **Standard path** — ambiguous symptoms → run primary checks (NJ1–NJ27), stop at first Critical failure
   - **Deep path** — primary checks pass → load extended checks (NJ28–NJ64) for runtime, kernel, credential, network edge cases, instance tagging, and control plane audit logs
3. Identify the specific failure mode from 64 known root causes.
4. Generate a diagnostic report with a ranked remediation plan.

## Key Design Decisions

- **No SSM required.** Diagnoses entirely from the AWS API side. Complements the `aws-eks-node-diagnostics-mcp` (which requires SSM access to the node for log-based diagnosis).
- **Covers all node types:** Managed node groups, self-managed nodes, Karpenter-provisioned nodes, and EKS Auto Mode.
- **64 checks across 25 failure domains** — based on analysis of 100+ real-world EKS support cases and cross-referenced against the companion `aws-eks-node-diagnostics-mcp` skill's A4 worker-node-join-failure SOP for coverage gaps.
- **Progressive disclosure** — primary checks inline in SKILL.md (~400 lines), extended checks in `references/extended-checks.md`. Keeps context lean for common cases.
- **Always recommends EKS Access Entries** over aws-auth for new configurations.

## Agent Types

- **Chat tasks** — interactive "why won't my node join?" investigation
- **Incident RCA** — when node failures contribute to a broader incident

## Prerequisites

### IAM Permissions

The DevOps Agent role needs read-only access (most covered by `AIDevOpsAgentAccessPolicy`):

```
eks:DescribeCluster
eks:DescribeNodegroup
eks:ListAccessEntries
eks:DescribeAccessEntry
ec2:DescribeSecurityGroups
ec2:DescribeSubnets
ec2:DescribeVpcs
ec2:DescribeVpcEndpoints
ec2:DescribeRouteTables
ec2:DescribeNetworkAcls
ec2:DescribeDhcpOptions
ec2:DescribeLaunchTemplateVersions
ec2:DescribeImages
iam:ListAttachedRolePolicies
iam:GetRole
sts:GetCallerIdentity
```

### Optional (for deeper checks)

- **kubectl access** — for checking aws-auth ConfigMap, Karpenter resources, VPC CNI DaemonSet, admission webhooks, PDBs, addon versions
- **EKS cluster access entry** — DevOps Agent role configured with cluster access
- **CloudWatch access** — `cloudwatch:GetMetricData` for etcd metrics (NJ45) and API throttling (NJ46)
- **CloudTrail access** — `cloudtrail:LookupEvents` for credential rotation detection (NJ44), ENI detach events (NJ57), account migration (NJ60)

## Uploading to AWS DevOps Agent

### Package the skill

```bash
cd skills
zip -r eks-node-join-diagnostic.zip eks-node-join-diagnostic/ \
  -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' \
  -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/CHANGELOG.md' '*/evals/*'
```

### Upload via the Operator Web App

1. Navigate to the Skills page in your Agent Space.
2. Click **Add skill** → **Upload skill**.
3. Drag and drop `eks-node-join-diagnostic.zip`.
4. Select agent types: **Chat tasks** and **Incident RCA**.
5. Click **Upload**.

## Usage

In the DevOps Agent Chat:

- "My managed node group `prod-nodes` in cluster `prod-eks` shows Create failed. Diagnose why."
- "Nodes are not joining my private EKS cluster `staging` in `us-west-2`."
- "I launched self-managed nodes but they don't appear in kubectl get nodes."
- "Karpenter is provisioning nodes but they stay NotReady."
- "I'm getting TLS handshake timeout when nodes try to join."
- "After upgrading to 1.33, my new node group can't register."
- "Nodes joining but immediately NotReady with cgroup driver mismatch errors."
- "VPC CNI IPAMD is crash-looping on new nodes — warm pool exhaustion."
- "Nodes intermittently going NotReady behind NAT gateway."
- "Our custom AMI with kernel 5.4 stopped working after upgrading to EKS 1.31."
- "Admission webhook is timing out and blocking new nodes from registering."
- "After cluster credential rotation, existing nodes can't communicate."
- "Cloud-init seems stuck — nodes never start kubelet."
- "GPU nodes failing after installing both GPU Operator and EKS nvidia plugin."

## Skill Contents

```
eks-node-join-diagnostic/
├── SKILL.md                    # Primary checks NJ1–NJ27 + adaptive routing (~400 lines)
├── README.md                   # This file
├── CHANGELOG.md                # Version history
├── references/
│   └── extended-checks.md      # Extended checks NJ28–NJ64 (loaded on demand)
└── evals/
    ├── evals.json              # Functional test scenarios
    ├── eval_queries.json       # Trigger accuracy tests
    └── benchmark.json          # Eval benchmark results
```

## Failure Domains Covered

| Domain | Checks | Common Symptom |
|--------|--------|----------------|
| Cluster State | NJ1–NJ2 | Cluster not ACTIVE or mid-upgrade |
| Node Group Health | NJ3–NJ4 | health.issues present, Create failed |
| IAM / Authentication | NJ5–NJ8 | Unauthorized, missing policies, role path issue |
| Security Groups | NJ9–NJ10 | TLS handshake timeout, connection refused |
| Network / DNS | NJ11–NJ15 | Node not found, no route to host, IP exhaustion |
| VPC Endpoints | NJ16 | Private cluster can't reach ECR/STS/API |
| Bootstrap / User-Data | NJ17–NJ20 | Cloud-init failure, wrong cluster name, AL2023 format |
| AMI Compatibility | NJ21–NJ23 | Version mismatch, AL2 on 1.33+ |
| STS Endpoint | NJ24 | InvalidClientTokenId |
| Karpenter | NJ25–NJ27 | Wrong subnets/SGs/AMI in EC2NodeClass |
| VPC CNI | NJ28 | "network plugin not ready: cni config uninitialized" |
| Join Timeout | NJ29 | Burstable instance slow to boot, MNG 20-min timeout |
| IMDS | NJ30 | Hop limit too low, host firewall blocking 169.254.169.254 |
| Containerd / Runtime | NJ31–NJ34 | Mirror misconfiguration, cgroup mismatch, snapshotter incompatibility |
| VPC CNI / IP Management | NJ35–NJ37 | IPAMD crash-loop, CNI migration deadlock, CIDR overlap |
| Kernel / OS Compatibility | NJ38–NJ41 | Kernel too old, SELinux/AppArmor blocking, missing modules |
| Credential Lifecycle | NJ42–NJ44 | SSM activation expired, cert expired after hibernation, CA rotated |
| Control Plane Pressure | NJ45–NJ46 | etcd compaction causing heartbeat loss, API throttling at scale |
| Launch Template Edge Cases | NJ47–NJ50 | User-data >16KB, package updates hang, NIC+subnet conflict, stale cache |
| Admission Webhooks | NJ51–NJ52 | Webhook Fail policy blocking nodes, PDB blocking drain |
| EKS Addon Conflicts | NJ53–NJ55 | kube-proxy version mismatch, GPU operator conflict, disk pressure |
| Network Edge Cases | NJ56–NJ59 | NAT idle timeout, ENI detach, conntrack full, S3 endpoint blocks ECR |
| Account / Org Changes | NJ60–NJ61 | Account moved between Orgs, credential provider path wrong |
| Instance Tagging | NJ62 | Self-managed/Karpenter node missing kubernetes.io/cluster tag |
| Control Plane Audit Logs | NJ63–NJ64 | CSR denied, aws-auth/access entry recently removed |

## Relationship to Other Tools

| Tool | What it does | When to use |
|------|-------------|-------------|
| **This skill** | API-side config validation (no node access needed) | Node never joins OR you can't SSM into it. Start here. |
| `aws-eks-node-diagnostics-mcp` | SSM-based log collection and analysis from the node | Node is SSM-reachable — use to confirm findings from this skill with actual log evidence |
| `AWSSupport-TroubleshootEKSWorkerNode` | SSM Automation runbook | Automated node-side checks (requires SSM) |
| `eks-operation-review` | Full cluster best-practices audit | Proactive review, not incident response |

### Complementary Workflow

This skill is designed to work standalone OR complement the EKS Node Diagnostics MCP server:

1. **Always start with this skill** — it works without SSM and identifies the likely root cause via API checks
2. **If MCP server is available and root cause needs confirmation** — hand off to MCP tools (`collect` → `quick_triage` → `search`) for log-level evidence
3. **If all API checks pass but node still won't join** — the MCP's `quick_triage` tool with its A4 SOP can find issues only visible in node-side logs (e.g., kubelet crashloop, cloud-init failures, containerd errors)

The SKILL.md includes a detailed handoff table showing which MCP tools to use after each NJ check identifies a suspected root cause.

## Source Material

- [EKS Troubleshooting docs](https://docs.aws.amazon.com/eks/latest/userguide/troubleshooting.html)
- [re:Post — Nodes fail to join cluster](https://repost.aws/knowledge-center/eks-nodes-fail-cluster-join)
- [re:Post — Worker nodes cluster](https://aws.amazon.com/premiumsupport/knowledge-center/eks-worker-nodes-cluster/)
- [EKS Security Group Requirements](https://docs.aws.amazon.com/eks/latest/userguide/sec-group-reqs.html)
- [Private Clusters](https://docs.aws.amazon.com/eks/latest/userguide/private-clusters.html)
- Project Nebula case pattern analysis (#1 Node Registration Failures, #7 Node Group Creation Failures — 2,770 cases/yr)
- Internal EKS Support Playbook (node registration checklist, known issues)
- Analysis of 100 real-world EKS node join failure support cases (Apr–Aug 2025) covering containerd, CNI, kernel, credential, webhook, addon, and network edge case patterns

## License

Apache-2.0. See [LICENSE](../../LICENSE).
