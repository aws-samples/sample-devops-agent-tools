# Repo-Anchored Claim Enforcement for VPC CNI

## Source of Truth
All VPC CNI behavior claims MUST be verified against the `amazon-vpc-cni-k8s` repository (github.com/aws/amazon-vpc-cni-k8s).

## Enforcement Process

### Step 1: Identify the Claim
When the agent mentions any VPC CNI mechanism (SNAT, SGP, prefix delegation, custom networking, iptables chain, connmark, etc.), extract the specific behavioral claim being made.

### Step 2: Locate Repo Evidence
For each claim, the agent MUST:
1. Identify the relevant source file in the repo
2. Cite the function name that implements the behavior
3. Include the line range if available
4. Extract the preconditions for that behavior

### Step 3: Verify Preconditions Against Evidence
Compare the repo-defined preconditions against the actual incident evidence:
- Are the required env vars set?
- Are the required annotations present?
- Are the required CRDs installed?
- Is the feature actually enabled?

### Step 4: Verdict
- **Preconditions MET** → Claim is valid, can be used in diagnosis
- **Preconditions NOT MET** → Claim MUST be blocked or downgraded to LOW confidence hypothesis
- **Cannot verify** → Claim must be labeled as UNVERIFIED

## Key Repo Anchors

### SGP / Pod-ENI
| Claim | Repo File | Function | Gate |
|-------|-----------|----------|------|
| SGP affects pod X | `pkg/ipamd/rpc_handler.go` | AddNetwork | PodVlanId != 0 |
| Strict mode blocks traffic | `pkg/sgpp/utils.go` | BuildHostVethNamePrefix | Only changes veth prefix |
| SGP default is strict | `pkg/sgpp/constants.go` | DefaultEnforcingMode | = EnforcingModeStrict |

### SNAT
| Claim | Repo File | Function | Gate |
|-------|-----------|----------|------|
| CNI manages SNAT | `pkg/networkutils/network.go` | buildIptablesSNATRules | AWS-SNAT-CHAIN-0 only |
| externalSNAT disables NAT | `pkg/networkutils/network.go` | useExternalSNAT | Only CNI SNAT, not kube-proxy |
| IPv6 has no SNAT | `pkg/networkutils/network.go` | updateHostIptablesRules | v6Enabled → return nil |

### iptables Ownership
| Chain | Owner | Repo Evidence |
|-------|-------|---------------|
| AWS-SNAT-CHAIN-0 | CNI | `pkg/networkutils/network.go` buildIptablesSNATRules() |
| AWS-CONNMARK-CHAIN-0 | CNI | `pkg/networkutils/network.go` buildIptablesConnmarkRules() |
| KUBE-SERVICES | kube-proxy | NOT in CNI repo (zero grep results) |
| KUBE-SVC-* | kube-proxy | NOT in CNI repo |
| KUBE-SEP-* | kube-proxy | NOT in CNI repo |
| KUBE-POSTROUTING | kube-proxy | NOT in CNI repo |

### Mark Space
| Component | Mark Value | Source |
|-----------|-----------|--------|
| VPC CNI | 0x80 | `pkg/networkutils/network.go` defaultConnmark |
| kube-proxy | 0x0000c000 | `pkg/networkutils/network.go` comment line 121 |
| Calico | 0xffff0000 | `pkg/networkutils/network.go` comment line 122 |

### Egress Plugin
| Claim | Repo File | Function | Gate |
|-------|-----------|----------|------|
| V6 egress on IPv4 cluster | `cmd/aws-vpc-cni/main.go` | conflist generation | ENABLE_V6_EGRESS=true + IPv4 mode |
| V4 egress on IPv6 cluster | `cmd/aws-vpc-cni/main.go` | conflist generation | ENABLE_V4_EGRESS=true + IPv6 mode |
| Egress creates per-pod chains | `cmd/egress-cni-plugin/snat/snat.go` | Add() | CNI-E6-<containerID> or CNI-E4-<containerID> |

### IMDS-Only Mode
| Claim | Repo File | Function | Gate |
|-------|-----------|----------|------|
| IMDS-only skips EC2 API | `pkg/awsutils/awsutils.go` | DescribeAllENIs | ENABLE_IMDS_ONLY_MODE=true |
| IMDS-only disables ENI provisioning | `pkg/ipamd/ipamd.go` | disableENIProvisioning() | enableImdsOnlyMode() returns true |

### Removed/Deprecated Env Vars
| Env Var | Status | Replacement |
|---------|--------|-------------|
| ENABLE_NFTABLES | REMOVED from codebase | Auto-detection from kubelet (v1.13.1+) |

## Anti-Hallucination Rules

1. **NEVER claim SGP affects unannotated pods** — The PodVlanId gate in rpc_handler.go proves this is impossible.
2. **NEVER claim CNI manages KUBE-SERVICES** — The string "KUBE-SERVICES" does not appear in the CNI codebase.
3. **NEVER claim externalSNAT breaks service routing** — Service masquerade is kube-proxy's KUBE-POSTROUTING, independent of CNI SNAT.
4. **NEVER claim prefix delegation changes routing** — PD only changes IP allocation strategy.
5. **NEVER claim IPv6 mode has SNAT rules** — updateHostIptablesRules() returns nil for v6.
6. **NEVER claim CNI config is cluster-wide** — All config is node-local via os.Getenv.
7. **NEVER confuse NETWORK_POLICY_ENFORCING_MODE with POD_SECURITY_GROUP_ENFORCING_MODE** — They are completely different subsystems (eBPF network policy vs SGP veth naming).
8. **NEVER claim DISABLE_NETWORK_RESOURCE_PROVISIONING breaks networking** — It only disables ENI provisioning, not existing networking.
9. **NEVER claim WARM_IP_TARGET limits max pods** — It controls warm pool pre-allocation, not pod capacity.
10. **NEVER confuse AWS_VPC_ENI_MTU with POD_MTU** — ENI MTU is host-level, POD_MTU is pod veth-level.
11. **NEVER flag empty main route table as broken on multi-ENI nodes** — Per-ENI policy routing replaces the main table.
12. **NEVER flag missing KUBE-SVC chains as broken in IPVS mode** — IPVS uses kernel hash tables, not iptables chains.
13. **NEVER blame IPAMD for transient "IP not in datastore" during cooldown** — 30s cooldown after pod deletion is normal.
14. **NEVER blame VPC CNI or kube-proxy for conntrack table exhaustion** — It's a kernel resource limit, fix via nf_conntrack_max.
15. **NEVER claim nm-cloud-setup is compatible with VPC CNI** — It overwrites per-ENI ip rules, breaking pod networking.
16. **NEVER restart kube-proxy during API server outages** — Static stability keeps existing rules working.
17. **NEVER claim ENABLE_IMDS_ONLY_MODE breaks networking** — It only changes ENI discovery from EC2 API to IMDS. Existing pod networking is unaffected.
18. **NEVER confuse ENABLE_V4_EGRESS with ENABLE_V6_EGRESS** — V4 egress is for IPv6 clusters (enables IPv4 outbound). V6 egress is for IPv4 clusters (enables IPv6 outbound). The naming is counterintuitive.
19. **NEVER reference ENABLE_NFTABLES as a current env var** — It has been REMOVED from the VPC CNI codebase. Auto-detection replaced it in v1.13.1+.
