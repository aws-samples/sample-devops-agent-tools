# EKS Best Practices Checklist

## Security
- [ ] Authentication mode set to API (not CONFIG_MAP)
- [ ] Cluster creator admin permissions removed
- [ ] Access Entries used (not aws-auth ConfigMap)
- [ ] No system:masters mappings in aws-auth
- [ ] EKS Pod Identity configured for workloads needing AWS access
- [ ] IRSA roles follow least-privilege
- [ ] Cluster/node IAM roles least-privilege
- [ ] No ClusterRoleBinding to system:anonymous
- [ ] Regional STS endpoint used
- [ ] Private endpoint enabled
- [ ] Public endpoint restricted (not 0.0.0.0/0)
- [ ] IMDSv2 enforced (httpTokens=required)
- [ ] KMS envelope encryption for secrets
- [ ] All 5 control plane log types enabled
- [ ] Pod Security Standards enforced
- [ ] NetworkPolicies present
- [ ] ECR scan-on-push enabled
- [ ] VPC endpoints for ECR, S3, STS

## Reliability
- [ ] K8s version within N-2 of latest
- [ ] Liveness, readiness, startup probes on production workloads
- [ ] PodDisruptionBudgets for production workloads
- [ ] TopologySpreadConstraints for HA
- [ ] Resource requests AND limits set
- [ ] Nodes across ≥3 AZs
- [ ] Auto-scaling configured (Karpenter or CAS)
- [ ] Graceful shutdown (preStop hooks, terminationGracePeriodSeconds)

## Networking
- [ ] VPC CNI version current
- [ ] Subnet IP availability >20% free
- [ ] VPC CIDR sized for growth (/16 recommended)
- [ ] CoreDNS replicas scaled for cluster size
- [ ] CoreDNS on dedicated nodepool
- [ ] NodeLocalDNS deployed (for large clusters)
- [ ] NAT gateway multi-AZ redundancy

## Cost Optimization
- [ ] Node CPU utilization 30-70% average
- [ ] Pod resource requests match actual usage
- [ ] Spot instances for stateless workloads
- [ ] Graviton instances where possible
- [ ] gp3 StorageClass (not gp2)
- [ ] Karpenter consolidation enabled (WhenEmptyOrUnderutilized)
- [ ] Cost allocation tags on clusters and node groups
- [ ] No idle resources (0-replica Deployments, orphaned PVCs)

## Karpenter
- [ ] Consolidation policy: WhenEmptyOrUnderutilized
- [ ] Disruption budgets set
- [ ] Instance type diversity (≥3 families)
- [ ] Spot + On-Demand capacity types
- [ ] Resource limits (cpu, memory) on NodePools
- [ ] EC2NodeClass: IMDSv2 enforced
- [ ] EC2NodeClass: amiFamily set (AL2023/Bottlerocket)
- [ ] EC2NodeClass: blockDeviceMappings with encrypted EBS

## Cluster Upgrades
- [ ] EKS upgrade insights reviewed (no FAILING status)
- [ ] Addon compatibility verified for target version
- [ ] PDB coverage for safe node drains
- [ ] No deprecated API usage
