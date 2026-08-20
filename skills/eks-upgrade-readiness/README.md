# EKS Upgrade Readiness Skill

A skill for AWS DevOps Agent that performs **read-only** upgrade readiness
assessments for Amazon EKS clusters, aligned with the
[AWS EKS Best Practices Guide — Cluster Upgrades](https://docs.aws.amazon.com/eks/latest/best-practices/cluster-upgrades.html).

## Purpose

EKS upgrades can fail or cause downtime when deprecated APIs, incompatible
addons, version-skewed node groups, or misconfigured PDBs are not caught
beforehand. This skill systematically checks every dimension the Best Practices
Guide calls out and produces a READY / NOT READY / READY WITH WARNINGS /
CANNOT DETERMINE verdict with a prioritized remediation plan.

## Key Capabilities

- **EKS Upgrade Insights (primary signal)** — UPGRADE_READINESS findings from
  the ListInsights/DescribeInsight APIs
- **Infrastructure prerequisites** — subnet IP availability (VPC CNI mode-aware),
  IAM role, KMS key, service quotas
- **API deprecation analysis** — maps removed APIs to replacements, scans Helm
  stored manifests (deployed revision), vendor-aware CRD checks
- **Addon compatibility (live API)** — validates managed addons via
  DescribeAddonVersions, detects self-managed addons via deployment/Helm scan
- **Full data plane inventory** — managed node groups, self-managed ASGs,
  Karpenter (Drift, expiry, EC2NodeClass, budgets), Auto Mode, Fargate profiles,
  kubelet version map
- **AL2→AL2023 migration** — launch template analysis, custom AMI detection,
  bootstrap differences, cgroup v2, IMDSv2
- **Upgrade ordering** — pre-upgrade alignment (Karpenter/CA/webhooks before CP)
  vs post-upgrade sequence
- **PDB and topology spread** — detects drain blockers and availability risks
- **StatefulSet safety** — grace period, PVC retention policy, single-replica risks
- **Pre-upgrade health baseline** — node Ready status, pending CSRs, system pod
  health, DNS/metrics baseline
- **Capacity planning** — surge calculation, ODCR/FDCR guidance, blue-green
  alternative, autoscaler pause
- **GitOps/IaC detection** — routes remediation through owning tool
  (Terraform/CDK/ArgoCD/Flux/eksctl)
- **Post-upgrade validation** — DNS, metrics, scheduling, LB health, IRSA smoke tests
- **Client/CI tooling skew** — kubectl (±1 minor), eksctl, Helm, Terraform provider checks (WARN-level)
- **Pod Identity awareness** — addon version check plus IRSA-vs-Pod-Identity blue-green migration guidance
- **Remediation playbook** — all mutations separated, require operator approval
- **Structured upgrade plan** — ordered execution with rollback gates
- **Machine-readable output** — optional JSON verdict (per-gate status, confidence,
  evidence) alongside the markdown report, for CI/CD gating

## Prerequisites

### IAM Permissions

The DevOps Agent role needs read access to EKS, EC2, IAM, and Auto Scaling:

```
eks:DescribeCluster
eks:ListClusters
eks:ListInsights
eks:DescribeInsight
eks:ListAddons
eks:DescribeAddon
eks:DescribeAddonVersions
eks:ListNodegroups
eks:DescribeNodegroup
eks:ListFargateProfiles
eks:DescribeFargateProfile
eks:ListUpdates
eks:DescribeUpdate
ec2:DescribeSubnets
ec2:DescribeInstances
ec2:DescribeLaunchTemplateVersions
ec2:DescribeImages
ec2:DescribeCapacityReservations
iam:GetRole
autoscaling:DescribeAutoScalingGroups
autoscaling:DescribeLaunchConfigurations
servicequotas:GetServiceQuota
```

### Kubernetes RBAC (if kubectl access available)

A `ClusterRole` with read-only access to nodes, pods, deployments, statefulsets,
daemonsets, PDBs, configmaps, secrets (Helm), CRDs, CSRs, Karpenter resources,
and ENIConfigs. See the "Required Permissions" section in SKILL.md for the
full `ClusterRole` manifest. `kubectl` access is optional — the assessment
still runs on AWS APIs alone at lower confidence for CRD/Helm/PDB checks.

### AWS Resources

- One or more Amazon EKS clusters (any supported version)
- Control plane logging enabled (recommended for post-upgrade debugging)

## Limitations

- **EKS clusters only.** Does not cover EKS Anywhere, Outposts, or Local Zones.
- **Read-only by design.** The skill produces recommendations; it never executes
  mutating APIs. All mutations are in the Remediation Playbook (Step 14).
- **UNKNOWN ≠ PASS.** Missing data or access denial produces UNKNOWN, never
  PASS. The overall verdict cannot be READY while any gate is UNKNOWN.
- **Addon version data may lag.** Static reference table is fallback only —
  always prefer live `describe-addon-versions` API.
- **Pagination required.** Large clusters with many node groups or addons
  require exhausting API pagination tokens.

## Agent Types

- **Chat tasks** — ask for upgrade readiness assessments
- **Evaluation** — periodic upgrade readiness scans

## Uploading to AWS DevOps Agent

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then
point to `skills/eks-upgrade-readiness`. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository, including this one, even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Zip the `eks-upgrade-readiness/` directory (only including allowed extensions):

```bash
cd skills
zip -r eks-upgrade-readiness.zip eks-upgrade-readiness/ \
  -i '*.md' '*.json' '*.yaml' '*.yml' \
  -x '*/README.md' '*/.skilleval.yaml' '*/CHANGELOG.md' '*/evals/*'
```

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the `eks-upgrade-readiness.zip` file (max 6 MB).
5. Select the agent types: **Chat tasks** and **Evaluation**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Use the DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign to `CHAT` and `EVALUATION` agent types. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

## How to Use This Skill

Describe the task in natural language — you do not need to name the skill.

### Example Prompts

```
"Is my EKS cluster prod-cluster in us-east-1 ready to upgrade to 1.31?"
"Check upgrade readiness for all my EKS clusters"
"What deprecated APIs would break if I upgrade to Kubernetes 1.32?"
"Plan the upgrade of my cluster from 1.30 to 1.31 including node groups"
"Are my addons compatible with EKS 1.31?"
"Will my PDBs block a node group upgrade?"
"I need to upgrade a 50-node cluster — what capacity do I need?"
"Compare in-place vs blue-green strategy for my 200-node cluster"
"My cluster is managed by Terraform — how should I do the upgrade?"
"We use AL2 with custom bootstrap scripts — what breaks going to 1.33?"
"We just upgraded to 1.31 — what should we validate?"
"We use Pod Identity and are planning a blue-green migration — what identity work is needed?"
"Give me the upgrade readiness result as JSON so I can gate our CI/CD pipeline"
```

### Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| Full assessment | "upgrade readiness", "ready to upgrade" | All 17 steps, scored report |
| Targeted check | "deprecated APIs", "addon compatibility", "PDB check" | One dimension, focused |
| Planning | "upgrade plan", "upgrade runbook" | Execution order with rollback gates |
| Comparison | "blue-green vs in-place" | Strategy recommendation |
| Validation | "post-upgrade check", "validate upgrade" | Smoke tests (Step 15) |

## Skill Structure

```
eks-upgrade-readiness/
├── SKILL.md                # Main skill instructions (17-step workflow)
├── README.md               # This file
├── CHANGELOG.md            # Version history
├── .skilleval.yaml         # Agent Skill Eval config
├── evals/
│   ├── evals.json          # 24 functional evaluation scenarios
│   └── eval_queries.json   # Trigger tests (positive and negative)
└── references/
    ├── safety-invariants.md         # Hard safety rules, knowledge hierarchy, operation classification
    ├── required-check-registry.yaml # All 60+ checks with IDs, categories, and severity
    ├── pre-flight-checks.yaml       # Blocking vs warning checks, timeouts, soak periods, rollback conditions
    ├── api-deprecations.md          # K8s API removal schedule by version
    ├── addon-version-matrix.md      # EKS addon compatibility (static fallback)
    ├── capacity-planning.md         # FDCR/ODCR surge capacity guidance
    ├── upgrade-troubleshooting.md   # Tools, feature removals, blue-green
    ├── karpenter-checks.md          # Full 14-check Karpenter registry (KARP-01 to KARP-14)
    ├── pre-drain-safety.md          # DRAIN-01 to DRAIN-06 detection and remediation
    ├── al2-al2023-migration.md      # AL2→AL2023 migration assessment details
    └── data-plane-inventory.md      # MNG, self-managed, Karpenter, Auto Mode, Fargate inventory commands
```

## Safety

This skill operates in **read-only** mode:

- No cluster modifications — upgrade actions are recommendations only
- No `update-*`, `delete-*`, or `create-*` API calls
- All mutations isolated in Step 14 Remediation Playbook (operator approval)
- All findings include evidence and specific remediation steps
- The operator reviews the report and decides whether to proceed
- UNKNOWN verdicts prevent false confidence (never marks missing data as PASS)

## Non-production disclaimer

> ⚠️ This skill is sample code, not intended for production use without
> additional review and testing. Validate in a non-production environment first.
> Compatibility data and version matrices are point-in-time references — always
> verify with `aws eks describe-addon-versions` for the latest data.
