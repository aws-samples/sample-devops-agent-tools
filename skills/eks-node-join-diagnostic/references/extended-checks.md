# Extended Diagnostic Checks (NJ28–NJ64)

Load this reference when the primary checks (NJ1–NJ27) pass but the node still
isn't joining, OR when symptoms point to runtime, kernel, credential, or
network edge cases.

## Table of Contents

- [VPC CNI and Taints (NJ28)](#vpc-cni-and-taints-nj28)
- [Join Timeout and IMDS (NJ29–NJ30)](#join-timeout-and-imds-nj29nj30)
- [Containerd / Runtime (NJ31–NJ34)](#containerd--runtime-nj31nj34)
- [VPC CNI / IP Management (NJ35–NJ37)](#vpc-cni--ip-management-nj35nj37)
- [Kernel / OS Compatibility (NJ38–NJ41)](#kernel--os-compatibility-nj38nj41)
- [Credential / Certificate Lifecycle (NJ42–NJ44)](#credential--certificate-lifecycle-nj42nj44)
- [Control Plane Pressure (NJ45–NJ46)](#control-plane-pressure-nj45nj46)
- [Launch Template Edge Cases (NJ47–NJ50)](#launch-template-edge-cases-nj47nj50)
- [Admission Webhooks (NJ51–NJ52)](#admission-webhooks-nj51nj52)
- [EKS Add-on Conflicts (NJ53–NJ55)](#eks-add-on-conflicts-nj53nj55)
- [Network Edge Cases (NJ56–NJ59)](#network-edge-cases-nj56nj59)
- [Account / Credential Provider (NJ60–NJ61)](#account--credential-provider-nj60nj61)
- [Instance Tagging (NJ62)](#instance-tagging-nj62)
- [Control Plane Audit Logs (NJ63–NJ64)](#control-plane-audit-logs-nj63nj64)

---

## VPC CNI and Taints (NJ28)

| ID | Check | Source | Pass Criteria | Severity |
|----|-------|--------|---------------|----------|
| NJ28 | aws-node DaemonSet can schedule on node | `kubectl get ds aws-node -n kube-system` + node taints | If nodes have custom taints, verify aws-node has matching tolerations (default since VPC CNI 1.7+, but custom manifests may be missing them) | High |

Symptom: "Container runtime network not ready: NetworkReady=false
reason:NetworkPluginNotReady message: network plugin is not ready: cni config
uninitialized" — means the VPC CNI pod couldn't schedule on the node.

---

## Join Timeout and IMDS (NJ29–NJ30)

| ID | Check | Source | Pass Criteria | Severity |
|----|-------|--------|---------------|----------|
| NJ29 | Instance not stuck in EC2 Pending state | `ec2.DescribeInstances` | Instance reached `running` within 5 minutes. MNG timeout is ~20 minutes. | Medium |
| NJ30 | IMDS reachable from node (hop limit) | `ec2.DescribeInstances` (MetadataOptions) | `HttpPutResponseHopLimit` >= 2 if containers need IMDS. Hop limit 1 blocks IMDS from containerized kubelet/bootstrap. | High |

NJ30 detail: IMDS (169.254.169.254) is required for instance identity, region info, and VPC CNI ENI attachment. If `HttpPutResponseHopLimit = 1` and kubelet runs inside a container (Bottlerocket host containers, CIS hardening), the request crosses a network hop and is blocked. Set hop limit to 2. Host firewalls (firewalld, nftables) can also block IMDS.

---

## Containerd / Runtime (NJ31–NJ34)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ31 | Containerd registry mirror config valid | Mirror endpoint format matches ECR path structure | High |
| NJ32 | Cgroup driver aligned (kubelet + containerd) | Both use `systemd` (EKS 1.24+ default). Mismatch → immediate NotReady. | Critical |
| NJ33 | Root filesystem supports snapshotter | XFS with `ftype=1` or ext4 for overlayfs | High |
| NJ34 | Instance store mounted before containerd | User-data mounts volume BEFORE containerd starts | High |

NJ32 symptoms: Node joins then immediately NotReady. kubelet: `"Failed to start ContainerManager"`. Fix: `cgroupDriver: systemd` in kubelet + `SystemdCgroup = true` in containerd.

---

## VPC CNI / IP Management (NJ35–NJ37)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ35 | IPAMD warm pool feasible for subnet | `WARM_IP_TARGET` doesn't exceed subnet's available IPs. IPAMD crash-loops if target > available. | Critical |
| NJ36 | Non-default CNI images pre-loaded | If non-AWS CNI (Cilium/Calico): images on AMI or fallback CNI exists | High |
| NJ37 | VPC CIDR no overlap with connected networks | Node subnet doesn't overlap on-premises via DX/TGW | High |

NJ35 fix: Reduce `WARM_IP_TARGET` to 2-5, set `MINIMUM_IP_TARGET=10`, enable `ENABLE_PREFIX_DELEGATION`.

---

## Kernel / OS Compatibility (NJ38–NJ41)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ38 | Kernel version meets EKS requirements | EKS 1.31+: kernel >= 5.10. EKS 1.24-1.30: >= 4.15. | Critical |
| NJ39 | SELinux not blocking kubelet (custom AMI) | kubelet can access `/var/lib/kubelet`, `/var/run/containerd`, `/opt/cni/bin` | High |
| NJ40 | AppArmor not blocking containerd (Ubuntu) | containerd socket access allowed | High |
| NJ41 | Required kernel modules present | `vxlan`, `ip_tables`, `br_netfilter`, `overlay` | High |

---

## Credential / Certificate Lifecycle (NJ42–NJ44)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ42 | SSM hybrid activation not expired | EKS Hybrid Nodes: activation ExpirationDate in future (default 30 days) | Critical |
| NJ43 | Kubelet certificate not expired | Instance stopped >90 days: cert may have expired (no auto-rotation while stopped) | High |
| NJ44 | No recent cluster credential rotation | If rotated within last hour: all nodes need rolling restart for new CA | High |

---

## Control Plane Pressure (NJ45–NJ46)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ45 | etcd size healthy | etcd < 6GB. Large etcd triggers compaction → slow heartbeats → NotReady | High |
| NJ46 | No API throttling during bootstrap | If 100+ nodes scaling: check for 429s on DescribeCluster/GetToken | Medium |

NJ46 fix: Stagger scaling, pass bootstrap args directly in user-data to avoid runtime API calls.

---

## Launch Template Edge Cases (NJ47–NJ50)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ47 | User-data size <= 16KB compressed | EC2 silently drops oversized user-data. No error in events. | Critical |
| NJ48 | No blocking package updates in user-data | `apt-get update`/`yum update` at boot can hang cloud-init indefinitely | High |
| NJ49 | No NetworkInterfaces + subnet conflict | LT must NOT specify both NetworkInterfaces section AND node group subnets | Critical |
| NJ50 | AMI not from running instance with stale cloud-init | `/var/lib/cloud/` from source cluster causes wrong-cluster join | High |

NJ47: Move config to S3, user-data only downloads. NJ48: Move ALL package updates to AMI build time.

---

## Admission Webhooks (NJ51–NJ52)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ51 | No webhooks blocking node registration | No webhook with `failurePolicy: Fail` targeting nodes that is timing out | High |
| NJ52 | PDBs not blocking node rotation | No PDB with `maxUnavailable: 0` when node group is updating | Medium |

NJ51 fix: Change `failurePolicy` to `Ignore` for node resources, OR ensure webhook runs on dedicated infra nodes.

---

## EKS Add-on Conflicts (NJ53–NJ55)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ53 | kube-proxy version matches cluster | Minor version must match. Never newer than cluster. | High |
| NJ54 | No GPU Operator + EKS device plugin conflict | Use ONE: either EKS addon OR GPU Operator | High |
| NJ55 | Root volume >= 30GB | Undersized root → DiskPressure → evicts system pods → NotReady cycling | High |

---

## Network Edge Cases (NJ56–NJ59)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ56 | NAT GW idle timeout compatible | TCP keepalive < 350s on nodes. NAT drops idle connections after 350s. | Medium |
| NJ57 | No ENI detach events | Third-party tools detaching primary ENI → momentary network loss | High |
| NJ58 | conntrack table adequate | `nf_conntrack_max` >= 256K for high-traffic nodes | Medium |
| NJ59 | S3 endpoint allows ECR buckets | Policy must allow `prod-<region>-starport-layer-bucket` | Critical |

NJ59 S3 endpoint policy must include:
- `arn:aws:s3:::prod-<region>-starport-layer-bucket/*`
- `arn:aws:s3:::amazon-eks/*`

---

## Account / Credential Provider (NJ60–NJ61)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ60 | Account not migrated between Orgs | IAM trust policies with `aws:PrincipalOrgID` fail silently after migration | High |
| NJ61 | AL2023 credential provider binary exists | `--image-credential-provider-config` path must match actual binary location | Critical |

NJ61 paths:
- Config: `/etc/kubernetes/credential-provider/config.yaml`
- Binary: `/etc/eks/image-credential-provider/ecr-credential-provider`

---

## Instance Tagging (NJ62)

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| NJ62 | Instance has kubernetes.io/cluster tag | `ec2.DescribeInstances` — instance must have tag `kubernetes.io/cluster/<cluster-name>` with value `owned` or `shared` | High |

This is required for **self-managed nodes** and **Karpenter nodes**. Managed node
groups set this tag automatically. Without it, the cluster may not discover the
node even if it successfully registers.

Note: If using EKS Auto Mode or Karpenter with `EC2NodeClass`, the tag is applied
automatically via the EC2NodeClass `tags` field. Check the EC2NodeClass spec if
missing.

---

## Control Plane Audit Logs (NJ63–NJ64)

| ID | Check | Source | Pass Criteria | Severity |
|----|-------|--------|---------------|----------|
| NJ63 | Node CSR not denied | CloudWatch Logs (`/aws/eks/<cluster>/cluster`) — filter `certificatesigningrequests` | No DENY entries for the node's CSR during join window. A denied CSR means the kubelet's certificate request was rejected — node cannot authenticate. | High |
| NJ64 | aws-auth / access entry not recently removed | CloudWatch Logs (`/aws/eks/<cluster>/cluster`) — filter `aws-auth` or CloudTrail `DeleteAccessEntry` | No recent deletion of the node role mapping within the failure window. Accidental removal is a common cause of sudden join failures across all nodes. | High |

**NJ63 detail:** When a node joins, kubelet sends a CSR to the API server. The
CSR must be auto-approved by the `eks:node-bootstrapper` ClusterRoleBinding. If:
- The node role is not in `system:bootstrappers` group → CSR is never approved
- A custom admission webhook denies CSRs → CSR is explicitly denied
- Check: `aws logs filter-log-events --log-group-name /aws/eks/<cluster>/cluster
  --filter-pattern "certificatesigningrequests"`

**NJ64 detail:** Common scenario: another team accidentally removes entries from
aws-auth ConfigMap or deletes an access entry. All new nodes (and existing nodes
after token refresh) immediately fail with Unauthorized. Check:
- `aws logs filter-log-events --log-group-name /aws/eks/<cluster>/cluster
  --filter-pattern "aws-auth"` (for ConfigMap changes)
- CloudTrail: `DeleteAccessEntry` or `UpdateAccessEntry` events
