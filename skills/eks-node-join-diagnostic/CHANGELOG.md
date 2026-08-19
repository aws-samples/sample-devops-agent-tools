# Changelog

## 3.1.0

- Added 3 checks identified by cross-referencing the companion
  `aws-eks-node-diagnostics-mcp` skill's A4 worker-node-join-failure SOP:
  - **Instance Tagging (NJ62):** `kubernetes.io/cluster/<name>` tag required
    for self-managed and Karpenter nodes (managed node groups set it
    automatically)
  - **Control Plane Audit Logs (NJ63–NJ64):** CSR denial detection via
    control-plane audit logs, and detection of recent aws-auth/access entry
    removal as a root cause for sudden fleet-wide join failures
- Added a "Complementing with EKS Node Diagnostics MCP Server" section with a
  handoff table mapping primary/extended checks to specific MCP tool calls
  (`collect`, `quick_triage`, `search`, `network_diagnostics`) for node-side
  log confirmation when the MCP server is available
- Fixed SKILL.md frontmatter `description` exceeding the 1024-character
  DevOps Agent upload limit; trimmed while preserving trigger keywords and
  negative-boundary exclusions
- Corrected check-count and failure-domain totals across SKILL.md, README.md,
  and references/extended-checks.md (58→64 checks, 24→25 domains, NJ1–NJ64)
- Confirmed via live testing against a real EKS Auto Mode cluster: primary
  checks (NJ1, NJ3, NJ5–NJ8) correctly adapted for Auto Mode (no managed node
  groups is expected) and correctly diagnosed a dual `API_AND_CONFIG_MAP`
  auth-mode conflict

## 3.0.0

- Restructured for progressive disclosure: SKILL.md reduced from 673 to 331 lines
- Moved extended checks (NJ28–NJ61) to `references/extended-checks.md`
- Added **Adaptive Depth** routing:
  - Fast path: immediate root cause identification for clear-cut symptoms
  - Standard path: full NJ1–NJ27 for ambiguous symptoms
  - Deep path: loads extended checks only when primary checks pass
- Added symptom-to-check routing table for direct jump to extended checks
- Strengthened differentiators vs baseline model knowledge:
  - Explicit "self-managed needs auth mapping" constraint
  - Always recommend EKS Access Entries over aws-auth
  - Structured report with coverage gaps ("What Was Not Checked")
- Optimized description for trigger accuracy with explicit negative boundaries
- Audit score improved: 66/100 → 74/100

## 2.0.0

- Expanded from 28 to 58 diagnostic checks across 24 failure domains
- New failure domains added from analysis of 100 real-world support cases:
  - **Containerd / Runtime Configuration (NJ31–NJ34):** registry mirror validation,
    cgroup driver alignment, snapshotter compatibility, instance store mounting
  - **VPC CNI / IP Address Management (NJ35–NJ37):** IPAMD warm pool vs subnet
    capacity, CNI migration chicken-and-egg detection, CIDR overlap with
    on-premises networks
  - **Kernel / OS-Level Compatibility (NJ38–NJ41):** kernel version requirements
    per EKS version, SELinux/AppArmor blocking detection, required kernel modules
  - **Credential / Certificate Lifecycle (NJ42–NJ44):** SSM hybrid activation
    expiry, kubelet cert expiration after hibernation, cluster credential rotation
  - **Control Plane Pressure (NJ45–NJ46):** etcd database size correlation,
    API throttling during mass scaling events
  - **Launch Template Edge Cases (NJ47–NJ50):** user-data 16KB size limit,
    blocking package updates in runtime, network interface + subnet conflicts,
    stale cloud-init cache detection
  - **Admission Webhooks (NJ51–NJ52):** webhook failurePolicy:Fail blocking
    node registration, PDB blocking node group updates
  - **EKS Add-on Conflicts (NJ53–NJ55):** kube-proxy version compatibility,
    GPU Operator + EKS device plugin conflict, root volume sizing
  - **Network Edge Cases (NJ56–NJ59):** NAT gateway idle timeout, ENI detach
    by third-party tools, conntrack table exhaustion, S3 endpoint policy
    blocking ECR
  - **Account / Organization Changes (NJ60–NJ61):** account migration between
    Organizations, kubelet credential provider path validation for AL2023
- Updated decision tree with all new failure domains
- Expanded trigger keywords for better skill activation
- Updated "When to Use" section with new symptom patterns

## 1.0.0

- Initial release
- 30 diagnostic checks across 14 failure domains:
  - Cluster state (NJ1–NJ2)
  - Node group health (NJ3–NJ4)
  - IAM/authentication mapping (NJ5–NJ8)
  - Security groups (NJ9–NJ10)
  - Network connectivity and DNS (NJ11–NJ15)
  - VPC endpoints for private clusters (NJ16)
  - Bootstrap/user-data validation (NJ17–NJ20)
  - AMI compatibility (NJ21–NJ23)
  - Regional STS endpoint (NJ24)
  - Karpenter configuration (NJ25–NJ27)
  - VPC CNI scheduling (NJ28)
  - Join timeout / burstable instance delay (NJ29)
  - IMDS reachability / hop limit / host firewall (NJ30)
- Decision tree for quick root-cause identification
- Covers managed node groups, self-managed, Karpenter, and Auto Mode
- No SSM/SSH required — pure API-side diagnosis
- Diagnostic report artifact with ranked remediation
