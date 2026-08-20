# Data Plane Inventory

A complete picture of the data plane is required for upgrade readiness assessment.
Missing any node population means the upgrade plan has blind spots.

## Managed Node Groups (MNG)

```bash
# List all node groups (paginate!)
NODEGROUPS=$(aws eks list-nodegroups --cluster-name <cluster> --query 'nodegroups[]' --output text)

# For each node group, get full details
for ng in $NODEGROUPS; do
  aws eks describe-nodegroup --cluster-name <cluster> --nodegroup-name "$ng" \
    --query '{name:nodegroup.nodegroupName, version:nodegroup.version,
      amiType:nodegroup.amiType, instanceTypes:nodegroup.instanceTypes,
      desiredSize:nodegroup.scalingConfig.desiredSize,
      maxSize:nodegroup.scalingConfig.maxSize,
      updateConfig:nodegroup.updateConfig,
      launchTemplate:nodegroup.launchTemplate,
      health:nodegroup.health.issues}'
done
```

For each MNG, record:
- Current K8s version vs target (version skew check)
- AMI type (AL2, AL2023, BOTTLEROCKET, WINDOWS_CORE, CUSTOM)
- Update strategy (`maxUnavailable` or `maxUnavailablePercentage`)
- Launch template ID and version (for custom AMI detection)
- Health issues (any existing problems block upgrade)

### MNG Update Algorithm

When you initiate a node group update, EKS:
1. Creates new nodes with the updated config (up to `maxUnavailable` count)
2. Cordons old nodes
3. Drains old nodes (respects PDBs — will wait/retry for up to 15 min)
4. If drain fails after timeout, ForceEviction applies (pods deleted)
5. Old nodes are terminated
6. Repeats until all nodes are updated

Understanding this is critical for capacity planning — at peak, you have
`existing_nodes + maxUnavailable` nodes running simultaneously.

## Self-Managed Node Groups (ASGs)

Self-managed nodes are EC2 instances in ASGs that joined the cluster via
bootstrap script but aren't tracked by EKS node group APIs.

### Detection

```bash
# Find ASGs with EKS cluster tag
aws autoscaling describe-auto-scaling-groups \
  --query "AutoScalingGroups[?Tags[?Key=='kubernetes.io/cluster/<cluster>' || Key=='eks:cluster-name']].[AutoScalingGroupName,LaunchTemplate.LaunchTemplateId,LaunchTemplate.Version,DesiredCapacity]" \
  --output table

# Get launch template details for AMI ID
aws ec2 describe-launch-template-versions --launch-template-id <lt-id> \
  --versions <version> --query 'LaunchTemplateVersions[0].LaunchTemplateData.ImageId'

# Resolve AMI to K8s version
aws ec2 describe-images --image-ids <ami-id> --query 'Images[0].[Name,Description]'
```

### Kubelet Version from Self-Managed Nodes

```bash
# Extract kubelet version from node labels (if kubectl available)
kubectl get nodes -l eks.amazonaws.com/nodegroup!=<any-mng> \
  -o jsonpath='{range .items[*]}{.metadata.name}: {.status.nodeInfo.kubeletVersion}{"\n"}{end}'
```

For self-managed nodes:
- Check if AMI is custom or EKS-optimized (from AMI name pattern)
- Identify bootstrap method (see `al2-al2023-migration.md`)
- Note: self-managed nodes require manual launch template updates

## Karpenter Managed Nodes

```bash
# Check Karpenter version
kubectl get deploy karpenter -n kube-system -o jsonpath='{.spec.template.spec.containers[0].image}'

# List NodePools and their config
kubectl get nodepools -o json | jq '.items[] | {
  name: .metadata.name,
  expireAfter: .spec.disruption.expireAfter,
  consolidateAfter: .spec.disruption.consolidateAfter,
  budgets: .spec.disruption.budgets
}'

# List EC2NodeClasses (AMI config)
kubectl get ec2nodeclasses -o json | jq '.items[] | {
  name: .metadata.name,
  amiFamily: .spec.amiFamily,
  amiSelectorTerms: .spec.amiSelectorTerms
}'

# Check feature gates (Drift)
kubectl get deploy karpenter -n kube-system -o json | jq '.spec.template.spec.containers[0].env[] | select(.name=="FEATURE_GATES")'
```

See `karpenter-checks.md` for the full 14-check registry (KARP-01 to KARP-14).

## EKS Auto Mode

If `cluster.computeConfig.enabled` is `true`:
- Data plane upgrades happen automatically after control plane upgrade
- Monitor with: `aws eks describe-cluster --name <cluster> --query 'cluster.computeConfig'`
- Verify PDBs won't block automatic rotation

### Detection

```bash
aws eks describe-cluster --name <cluster> --query 'cluster.computeConfig'
```

If Auto Mode is enabled, the node rotation happens without operator action
after the control plane upgrade completes. The key checks become:
- PDBs must allow disruption
- Workloads must tolerate rolling replacement
- No bare pods or emptyDir-dependent workloads on Auto Mode nodes

## Fargate Profiles

```bash
# List Fargate profiles
aws eks list-fargate-profiles --cluster-name <cluster>

# Describe each profile
aws eks describe-fargate-profile --cluster-name <cluster> --fargate-profile-name <name> \
  --query '{name:fargateProfile.fargateProfileName, selectors:fargateProfile.selectors, subnets:fargateProfile.subnets}'
```

Fargate pods:
- Are automatically upgraded when redeployed after control plane upgrade
- Support the same version skew as managed node groups (N-3 for 1.28+)
- Require explicit restart after CP upgrade (see Remediation Playbook)

## Kubelet Version Inventory

Regardless of node management method, confirm actual kubelet versions running:

```bash
# Full kubelet version map (requires kubectl)
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}: kubelet={.status.nodeInfo.kubeletVersion}, os={.status.nodeInfo.osImage}, arch={.metadata.labels.kubernetes\.io/arch}{"\n"}{end}'

# Summary: versions that violate skew policy
kubectl get nodes -o json | jq --arg target "<target-version>" '.items[] | select(.status.nodeInfo.kubeletVersion | test("v1\\.(\\d+)") | not) | {name: .metadata.name, version: .status.nodeInfo.kubeletVersion}'
```

### Version Skew Check

Evaluate the current control-plane version and target version separately:

1. **Current-state upper bound:** kubelet must never be newer than the current
   control plane (`kubelet_minor <= current_control_plane_minor`). A node at
   1.32 with a current control plane at 1.31 is already invalid, even if 1.32
   is the intended target.
2. **Target lower bound:** for target version 1.X, kubelet must be >= 1.(X-3)
   when X >= 28 (N-3), or >= 1.(X-2) when X < 28 (N-2).

Any node violating either predicate is a **FAIL** — correct it before the
control-plane upgrade proceeds.

## Inventory Summary Template

After running all discovery commands, produce a summary:

```
### Data Plane Inventory
- Managed Node Groups: <count> (versions: <list>)
  - <ng-name>: <version>, <ami-type>, <instance-types>, <desired>/<max> nodes
- Self-Managed ASGs: <count> (versions: <list>)
  - <asg-name>: <ami-id>, <instance-type>, <count> nodes
- Karpenter NodePools: <count> (Karpenter version: <ver>)
  - <pool-name>: <amiFamily>, expireAfter=<val>, budgets=<val>
- EKS Auto Mode: <enabled|disabled>
- Fargate Profiles: <count>
  - <profile-name>: selectors=<namespaces>
- Total Nodes: <count>
- Kubelet Versions: <distinct versions found>
- Version Skew Violations: <count> nodes outside allowed window
```
