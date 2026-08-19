---
name: eks-node-join-diagnostic
description: >-
  Use when an EKS worker node fails to join, register, or reach Ready state.
  Activate for: nodes missing from kubectl get nodes, NodeCreationFailure,
  node group Create failed/Degraded, TLS handshake timeout to API server,
  Unauthorized kubelet errors, NotReady immediately after launch, cgroup/
  containerd/IPAMD/webhook/addon-conflict node join issues, or any "why won't
  my node join?" question. Covers managed node groups, self-managed, Karpenter,
  and Auto Mode. Read-only API-side diagnosis across IAM, security groups,
  VPC/DNS, bootstrap, AMI, runtime, and CNI — ranked pass/fail checklist with
  fixes. Do NOT activate for: cluster performance/throttling without join
  failure, upgrade readiness, Karpenter setup/consolidation, pod scheduling on
  already-joined nodes, ECS, cost analysis, or general security audits.
metadata:
  author: LearningNewbie
  version: "3.1.0"
  aws-devops-agent-skills.agent-types: "Chat tasks, Incident RCA"
  aws-devops-agent-skills.aws-services: "Amazon EKS"
  aws-devops-agent-skills.technical-domains: "Containers"
---

# EKS Node Join Diagnostic

Systematically identify which of 64 known failure modes is preventing a worker
node from joining an EKS cluster, and recommend the specific fix — all without
needing SSH/SSM access to the node itself.

## Adaptive Depth

Not every diagnosis needs all 61 checks. Use this routing:

**Fast path** — If the symptom directly maps to a known root cause with high
confidence (e.g., user says "I have a pathed role ARN in aws-auth"), identify
the root cause immediately, then validate with only the relevant subset of
checks (the specific NJ ID + its prerequisites). Still produce the diagnostic
report format, but mark unrelated checks as "SKIPPED — not indicated by symptoms."

**Standard path** — For ambiguous symptoms ("nodes not joining"), run the full
decision tree from Step 2 through Step 9 (NJ1–NJ27). Stop when you find a
Critical-severity failure — that's the blocker.

**Deep path** — If all primary checks (NJ1–NJ27) pass, load
`references/extended-checks.md` and continue with NJ28–NJ64. These cover
runtime, kernel, credentials, control plane pressure, launch template edge
cases, webhooks, addon conflicts, network edge cases, instance tagging, and
control plane audit logs.

## Critical Constraints

- **Read-only.** Only `describe*`, `list*`, `get*` APIs. No mutating calls.
  All fixes are recommendations for human execution.
- **No SSM required.** This skill diagnoses from the AWS API side. For
  node-side log analysis (when SSM is available), use the
  `aws-eks-node-diagnostics-mcp` instead — the two are complementary.
- **One cluster at a time.** Ask the user for cluster name, region, and
  optionally the specific node group or instance ID.
- **Self-managed nodes need explicit auth mapping.** Unlike managed node
  groups, self-managed and Karpenter nodes do NOT get automatic aws-auth
  entries. Always flag NJ6 as high-priority for these node types.
- **Always recommend EKS Access Entries** over aws-auth for new configurations.
  Access Entries handle IAM paths correctly, are API-managed, and are the
  modern best practice (available since late 2023).

## Step 1: Gather Context

Ask the user for:
- **Cluster name** and **region**
- **What failed:** managed node group name, instance ID, or symptom
- **When:** approximate time the failure started
- **Node type:** managed node group, self-managed, Karpenter, or Auto Mode

Then collect foundation data:
```
aws eks describe-cluster --name <cluster> --region <region>
```

Extract: `status`, `version`, `endpoint`, `certificateAuthority`,
`resourcesVpcConfig` (subnetIds, securityGroupIds, clusterSecurityGroupId,
endpointPublicAccess, endpointPrivateAccess), `logging`, `encryptionConfig`.

If a node group is specified:
```
aws eks describe-nodegroup --cluster-name <cluster> --nodegroup-name <nodegroup>
```

Extract: `status`, `health.issues`, `amiType`, `instanceTypes`, `subnets`,
`remoteAccess`, `launchTemplate`, `version`, `releaseVersion`.

## Step 2: Check Cluster State (NJ1–NJ2)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ1 | Cluster status is ACTIVE | `status == "ACTIVE"` | Critical — nodes cannot join a non-ACTIVE cluster |
| NJ2 | Cluster not mid-upgrade | No pending platform version update | Medium — join may stall during CP upgrade |

If NJ1 fails, stop — the cluster itself has a problem.

## Step 3: Check Node Group Health (NJ3–NJ4)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ3 | Node group health issues | `health.issues` array is empty | Critical |
| NJ4 | Node group status | Not `CREATE_FAILED` or `DEGRADED` | Critical |

Common NJ3 issue codes and their meaning:
- `Ec2SecurityGroupNotFound` — SG was deleted → must recreate node group
- `Ec2LaunchTemplateNotFound` — LT was deleted → must recreate
- `IamInstanceProfileNotFound` — instance profile deleted → recreate
- `AutoScalingGroupNotFound` — ASG deleted → recreate node group
- `AsgInstanceLaunchFailures` — capacity unavailable → try different AZ/type
- `ClusterUnreachable` — etcd full or CP issue → check cluster health

Note: Self-managed nodes have no EKS node group object — skip NJ3/NJ4.

## Step 4: Check IAM and Authentication (NJ5–NJ8)

| ID | Check | API | Pass Criteria | Severity |
|----|-------|-----|---------------|----------|
| NJ5 | Node IAM role has required policies | `iam.ListAttachedRolePolicies` | Has AmazonEKSWorkerNodePolicy + AmazonEKS_CNI_Policy + AmazonEC2ContainerRegistryReadOnly (or PullOnly) | Critical |
| NJ6 | Node role mapped in auth config | `eks.ListAccessEntries` or `kubectl get cm aws-auth -n kube-system` | Role ARN appears as EC2_LINUX access entry OR in aws-auth mapRoles | Critical |
| NJ7 | Role ARN format correct (aws-auth) | Inspect aws-auth ConfigMap | ARN has NO path. Use `role/MyRole` not `role/path/MyRole`. Instance profile ARN must NOT be used. | Critical |
| NJ8 | Cluster IAM role valid | `iam.GetRole` on cluster role | Has AmazonEKSClusterPolicy, trust allows `eks.amazonaws.com` | Critical |

**NJ6 — auth mode detection:**
- EKS API auth mode: `aws eks list-access-entries --cluster-name <cluster>` — look for node role with type `EC2_LINUX` or `EC2_WINDOWS`.
- CONFIG_MAP mode: check aws-auth ConfigMap mapRoles for the node role with groups `system:bootstrappers` and `system:nodes`.

**NJ7 — the path gotcha:** If role ARN is `arn:aws:iam::123456789012:role/development/apps/MyNodeRole`, it MUST be entered in aws-auth as `arn:aws:iam::123456789012:role/MyNodeRole` (path stripped). This does NOT apply to EKS Access Entries which handle paths correctly. Always recommend migrating to Access Entries.

## Step 5: Check Security Groups (NJ9–NJ10)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ9 | Node SG allows outbound 443 to cluster SG | Node SG egress allows TCP 443 to cluster security group (or 0.0.0.0/0) | Critical |
| NJ10 | Cluster SG allows inbound from node SG | Cluster SG allows inbound TCP 443 from node SG AND TCP 10250 from node SG | Critical |

Required bidirectional rules:
- Node → Cluster: TCP 443 (API server)
- Cluster → Node: TCP 10250 (kubelet), TCP 53 (DNS), ephemeral ports
- Node → Node: All traffic (pod communication)

## Step 6: Check Network Connectivity (NJ11–NJ15)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ11 | VPC DNS support enabled | `enableDnsSupport = true` AND `enableDnsHostnames = true` | Critical |
| NJ12 | DHCP options have AmazonProvidedDNS | domain-name-servers includes AmazonProvidedDNS | Critical |
| NJ13 | Subnet route tables have internet/NAT/VPCe path | Private subnets → NAT GW or VPC endpoints; public → IGW | Critical |
| NJ14 | Subnet IP availability | AvailableIpAddressCount >= 5 per node subnet | High |
| NJ15 | NACLs allow required ports | Allow TCP 443, 10250, ephemeral (1025-65535) | High |

**NJ11 is critical:** If DNS hostname is disabled, nodes cannot resolve their own hostname, causing "node not found" errors. After enabling, nodes must be REPLACED (reboot is not enough).

## Step 7: Check VPC Endpoints (NJ16) — Private Clusters Only

If `endpointPublicAccess = false`:

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ16 | Required VPC endpoints exist | All required endpoints present with SG allowing inbound 443 from node subnets | Critical |

Required VPC endpoints for private clusters:
- `com.amazonaws.<region>.ec2` (interface)
- `com.amazonaws.<region>.ecr.api` (interface)
- `com.amazonaws.<region>.ecr.dkr` (interface)
- `com.amazonaws.<region>.sts` (interface)
- `com.amazonaws.<region>.s3` (gateway)
- `com.amazonaws.<region>.logs` (interface, if logging enabled)

Each interface endpoint's SG must allow inbound TCP 443 from node subnets.

## Step 8: Check Bootstrap / User-Data (NJ17–NJ20)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ17 | Launch template user-data present | Not empty (for custom LT) | High |
| NJ18 | Private cluster args present | Must contain `--apiserver-endpoint`, `--b64-cluster-ca`, `--dns-cluster-ip` | Critical |
| NJ19 | Cluster name matches | ClusterName matches exactly (case-sensitive) | Critical |
| NJ20 | AL2023 uses nodeadm format | MIME multipart with `application/node.eks.aws`, NOT bash script | Critical |

**NJ18:** For private clusters, bootstrap.sh cannot call `aws eks describe-cluster` without connectivity. Always pass args directly:
```bash
/etc/eks/bootstrap.sh <cluster-name> \
  --apiserver-endpoint https://<endpoint> \
  --b64-cluster-ca <base64-cert> \
  --dns-cluster-ip <service-cidr-dns-ip>
```

**NJ20:** AL2023 uses `nodeadm` instead of `bootstrap.sh`:
```
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="//"

--//
Content-Type: application/node.eks.aws

---
apiVersion: node.eks.aws/v1alpha1
kind: NodeConfig
spec:
  cluster:
    apiServerEndpoint: https://...
    certificateAuthority: ...
    cidr: 10.100.0.0/16
    name: my-cluster
--//--
```

## Step 9: Check AMI and STS (NJ21–NJ24)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ21 | AMI K8s version matches cluster | Within supported skew (N-3 for 1.28+) | Critical |
| NJ22 | AL2 AMI not used on 1.33+ | amiType must NOT be AL2 on 1.33+ (use AL2023 or Bottlerocket) | Critical |
| NJ23 | Custom AMI has required components | kubelet, containerd, aws-iam-authenticator, bootstrap.sh/nodeadm | High |
| NJ24 | Regional STS endpoint activated | `sts.<region>.amazonaws.com` responds. "InvalidClientTokenId" if not. | High |

## Step 10: Check Karpenter Configuration (NJ25–NJ27)

Only if Karpenter is detected in the cluster:

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ25 | EC2NodeClass subnets match cluster VPC | Subnets in same VPC with routes to API endpoint | High |
| NJ26 | EC2NodeClass SGs include cluster SG | Security groups include cluster SG or equivalent rules | Critical |
| NJ27 | EC2NodeClass AMI compatible | amiFamily not AL2 on 1.33+; amiSelectorTerms resolve to valid AMIs | High |

## Step 11: Extended Checks (NJ28–NJ64)

If all checks above PASS but nodes still aren't joining, read
`references/extended-checks.md` for the remaining 37 checks covering:

- VPC CNI scheduling and taints (NJ28)
- Join timeout and IMDS hop limit (NJ29–NJ30)
- Containerd/runtime configuration (NJ31–NJ34)
- VPC CNI IP address management (NJ35–NJ37)
- Kernel/OS compatibility (NJ38–NJ41)
- Credential/certificate lifecycle (NJ42–NJ44)
- Control plane pressure (NJ45–NJ46)
- Launch template edge cases (NJ47–NJ50)
- Admission webhooks (NJ51–NJ52)
- EKS add-on conflicts (NJ53–NJ55)
- Network edge cases (NJ56–NJ59)
- Account/credential provider (NJ60–NJ61)
- Instance tagging (NJ62)
- Control plane audit logs (NJ63–NJ64)

Route to extended checks immediately (skip primary checks) when symptoms match:
- "cgroup driver mismatch" or immediate NotReady after join → NJ32
- "cni config uninitialized" → NJ28
- IPAMD crash-looping → NJ35
- Node was stopped for weeks/months → NJ43
- Recently rotated cluster credentials → NJ44
- Intermittent NotReady (30s episodes) → NJ56
- AL2023 image pull failures → NJ61
- Self-managed/Karpenter node not discovered by cluster → NJ62
- CSR denied or aws-auth/access entry recently changed → NJ63–NJ64

## Diagnostic Report Format

Produce a report artifact named `eks-node-join-diagnostic-<cluster>-<date>.md`:

```markdown
# EKS Node Join Diagnostic — <cluster-name>
**Region:** <region> | **Date:** <date> | **K8s Version:** <version>
**Target:** <node-group-name or instance-id>
**Diagnosis:** PASS / FAIL (blocker found) / PARTIAL (warnings only)

## Root Cause
<One-line summary of the blocking issue>

## Checklist Results

| # | Check | Status | Evidence | Fix |
|---|-------|--------|----------|-----|
| NJ1 | Cluster ACTIVE | ✅ PASS | status=ACTIVE | — |
| NJ5 | Node role policies | ❌ FAIL | Missing AmazonEKS_CNI_Policy | Attach policy |
| ... | ... | SKIPPED | Not indicated by symptoms | — |

## Recommended Actions (priority order)
1. [CRITICAL] <immediate fix>
2. [HIGH] <next fix>

## What Was Not Checked
- Node-side logs (requires SSM — use aws-eks-node-diagnostics-mcp)
- <any checks skipped and why>
```

## Decision Tree

```
Node not joining
│
├─ Symptom maps to known root cause? ──→ FAST PATH: validate + report
│
├─ NJ3/NJ4: Node group health issues? ──→ Recreate if SG/LT/IAM deleted
│
├─ NJ5-NJ8: IAM/Auth failure?
│   ├─ Missing policies ──→ Attach required managed policies
│   ├─ Role not in auth config ──→ Create access entry (EC2_LINUX)
│   └─ Role has path (aws-auth) ──→ Strip path OR migrate to Access Entries
│
├─ NJ9-NJ10: Security group blocking?
│   └─ Missing 443/10250 rules ──→ Update SG rules
│
├─ NJ11-NJ15: Network connectivity?
│   ├─ DNS disabled ──→ Enable + REPLACE nodes
│   ├─ No route to API ──→ Add NAT GW or VPC endpoints
│   └─ Subnet full ──→ Add IPs or different subnets
│
├─ NJ16: Private cluster missing VPC endpoints? ──→ Create endpoints
│
├─ NJ17-NJ20: Bootstrap misconfigured?
│   ├─ Missing private cluster args ──→ Add --apiserver-endpoint, etc.
│   └─ AL2023 wrong format ──→ Convert to nodeadm MIME multipart
│
├─ NJ21-NJ24: AMI/STS issues?
│   ├─ AL2 on 1.33+ ──→ Migrate to AL2023 or Bottlerocket
│   └─ STS not activated ──→ Activate in IAM console
│
├─ NJ25-NJ27: Karpenter misconfigured? ──→ Fix EC2NodeClass spec
│
└─ All primary checks pass? ──→ Load references/extended-checks.md (NJ28-NJ64)
```

## References

- EKS Troubleshooting: https://docs.aws.amazon.com/eks/latest/userguide/troubleshooting.html
- Node join failures: https://repost.aws/knowledge-center/eks-nodes-fail-cluster-join
- Security group requirements: https://docs.aws.amazon.com/eks/latest/userguide/sec-group-reqs.html
- Private clusters: https://docs.aws.amazon.com/eks/latest/userguide/private-clusters.html
- AWSSupport-TroubleshootEKSWorkerNode runbook: https://docs.aws.amazon.com/systems-manager-automation-runbooks/latest/userguide/automation-awssupport-troubleshooteksworkernode.html
- EKS Node Diagnostics MCP server: https://github.com/aws-samples/sample-eks-node-diagnostics-mcp

## Complementing with EKS Node Diagnostics MCP Server

This skill performs API-side diagnosis and does NOT require SSM access. However,
when the `aws-eks-node-diagnostics-mcp` MCP server is deployed in the Agent Space
and the node is SSM-reachable, hand off to MCP tools for node-side confirmation:

### When to Hand Off

| After This Skill Finds... | Use MCP Tool | To Confirm |
|---|---|---|
| NJ5-NJ8: IAM/auth suspected but API checks pass | `collect` → `search` with query=`Unauthorized\|401\|Forbidden` | Actual kubelet auth error in logs |
| NJ9-NJ10: SG rules look correct but TLS timeout | `collect` → `network_diagnostics` sections=routes,eni | Actual route table and ENI config on-box |
| NJ17-NJ20: Bootstrap args suspected wrong | `collect` → `search` with query=`bootstrap.sh\|cloud-init\|ClusterName` | What args were actually passed at boot |
| NJ31-NJ34: Containerd/runtime config issue | `collect` → `search` with query=`containerd\|cgroup\|SystemdCgroup` | Actual containerd config and kubelet cgroup driver |
| NJ38-NJ41: Kernel/OS issue suspected | `collect` → `search` with query=`kernel\|modprobe\|SELinux\|AppArmor` | Actual kernel version and module state |
| NJ45: etcd pressure / heartbeat loss | `collect` → `quick_triage` on affected node | Timeline of kubelet connection failures |
| All primary checks PASS, cause unclear | `collect` → `quick_triage` for fast root cause | Let MCP's log analysis find what API checks can't see |

### Workflow Pattern

```
1. Run this skill (API-side checks NJ1–NJ64)
   ↓
2. If root cause confirmed with high confidence → report + fix
   ↓
3. If root cause unclear OR need log-level confirmation:
   → collect(instanceId="<id>", region="<region>")
   → status(executionId="<id>")   # poll until Success
   → quick_triage(instanceId="<id>")
   → Use topEvidence and recommendedSOPs from quick_triage
   ↓
4. For domain-specific deep dive:
   → network_diagnostics(instanceId="<id>", sections="iptables,cni,routes,dns,eni")
   → search(instanceId="<id>", query="<specific error pattern>")
```

### Key MCP Tools for Node Join Issues

| MCP Tool | What It Provides |
|---|---|
| `collect` + `status` | Gathers all node logs via SSM (async, 1-3 min) |
| `quick_triage` | One-shot root cause with log evidence — maps to A4 SOP |
| `search` | Regex search across collected logs for specific patterns |
| `network_diagnostics` | Structured iptables/CNI/routes/DNS/ENI/IPAMD analysis |
| `correlate` | Cross-file timeline around a pivot event |
| `compare_nodes` | Diff a failed node against a healthy node |
| `cluster_health` | Check if other nodes are also failing (cluster-wide issue) |

### Example: Full Complementary Investigation

```
# 1. API-side: This skill identifies NJ32 (cgroup mismatch suspected)
#    Evidence: AL2023 AMI + custom launch template + immediate NotReady

# 2. Confirm with MCP (if node is SSM-reachable):
collect(instanceId="i-0abc123", region="us-east-1")
status(executionId="<exec-id>")
search(instanceId="i-0abc123", query="cgroup.*driver|SystemdCgroup|cgroupfs")

# 3. MCP confirms: kubelet using systemd, containerd using cgroupfs → mismatch
# 4. Report: root cause confirmed, recommend aligning to systemd
```
