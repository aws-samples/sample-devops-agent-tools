# EKS Upgrade Troubleshooting

Common failures during EKS upgrades and their resolutions.

## Control Plane Upgrade Failures

### Upgrade stuck in "Updating" for > 60 minutes

**Causes:**
- Webhook configurations blocking API server startup
- Custom admission controllers not compatible with new version
- Insufficient IAM permissions for EKS service role

**Resolution:**
1. Check EKS update status: `aws eks describe-update --name <cluster> --update-id <id>`
2. Review CloudTrail for EKS API errors
3. If webhook is blocking: the control plane will eventually recover by
   skipping the webhook; no user action needed but it causes delays
4. If IAM: verify `AmazonEKSClusterPolicy` is attached to cluster role

### Control plane upgrade succeeded but kubectl fails

**Causes:**
- kubeconfig pointing to old endpoint
- aws-auth ConfigMap missing after upgrade (rare)
- Client version too old for new API server

**Resolution:**
1. Update kubeconfig: `aws eks update-kubeconfig --name <cluster>`
2. Verify: `kubectl version` — client should be within one minor of server
3. Check aws-auth: `kubectl get configmap aws-auth -n kube-system`

## Addon Upgrade Failures

### Addon update returns "ConfigurationConflict"

**Cause:** Addon was manually modified outside of EKS addon management.

**Resolution:** This is an operator-approved mutation. Inspect and back up the
existing managed-addon configuration first, then choose deliberately:

```bash
# Preserve intentional customer configuration when it is target-compatible.
aws eks update-addon --cluster-name <cluster> \
  --addon-name <addon> --addon-version <version> \
  --resolve-conflicts PRESERVE

# OVERWRITE replaces conflicting customer configuration with EKS defaults.
# Use only after reviewing the diff, recording configurationValues, and
# confirming the rollback plan.
aws eks update-addon --cluster-name <cluster> \
  --addon-name <addon> --addon-version <version> \
  --resolve-conflicts OVERWRITE
```

### CoreDNS not running after upgrade

**Causes:**
- Corefile incompatible with new version
- Pod scheduling issues (taints, resource limits)

**Resolution:**
1. Check pods: `kubectl get pods -n kube-system -l k8s-app=kube-dns`
2. Check events: `kubectl describe pod <coredns-pod> -n kube-system`
3. If Corefile issue: check `kubectl get configmap coredns -n kube-system -o yaml`

## Node Group Upgrade Failures

### Nodes not draining (upgrade stuck)

**Causes:**
- PDB with `maxUnavailable: 0` blocking eviction
- Pod with no controller (standalone pod without owner)
- Local storage preventing eviction (emptyDir with data)
- Finalizers blocking pod deletion

**Resolution:**
1. Check PDBs: `kubectl get pdb --all-namespaces`
2. Identify blocking pods from node group update events:
   ```
   aws eks describe-update --name <cluster> --update-id <id> --nodegroup-name <ng>
   ```
3. Temporarily adjust PDB: `kubectl patch pdb <name> -p '{"spec":{"maxUnavailable":1}}'`
4. For standalone pods: delete manually or add controller

### New nodes joining but pods not scheduling

**Causes:**
- Taints on new nodes not tolerated by workloads
- Node labels changed between AMI versions
- Resource requests exceed new node capacity

**Resolution:**
1. Check node taints: `kubectl describe node <new-node> | grep Taint`
2. Check pending pods: `kubectl get pods --field-selector=status.phase=Pending`
3. Check events: `kubectl describe pod <pending-pod>`

### InsufficientInstanceCapacity during node group upgrade

**Cause:** EC2 cannot launch the required instance type in the AZ.

**Resolution:**
1. Check which AZ is constrained from the update error
2. Options:
   - Wait and retry (capacity may free up)
   - Use Capacity Reservations (see `capacity-planning.md`)
   - Add alternative instance types to the node group
   - Reduce `maxUnavailablePercentage` to lower simultaneous surge

### Launch template version mismatch

**Cause:** Custom launch template AMI doesn't match target EKS version.

**Resolution:**
1. Check LT: `aws ec2 describe-launch-template-versions --launch-template-id <lt-id>`
2. Update AMI to match target version:
   ```
   aws ssm get-parameter --name /aws/service/eks/optimized-ami/<version>/amazon-linux-2023/x86_64/standard/recommended/image_id
   ```
3. Create new LT version with correct AMI
4. Update node group to use new LT version

## Karpenter-Specific Issues

### Karpenter not launching nodes with new AMI after upgrade

**Cause:** `amiSelectorTerms` in EC2NodeClass pinned to old version.

**Resolution:**
1. Check EC2NodeClass: `kubectl get ec2nodeclass -o yaml`
2. Update `amiSelectorTerms` to include new version or use `amiFamily` for auto-discovery
3. Roll nodes: `kubectl delete nodes -l karpenter.sh/nodepool=<pool>`

### Karpenter version incompatible with new EKS version

**Cause:** Old Karpenter release doesn't support new K8s API version.

**Resolution:**
1. Check Karpenter compatibility matrix in release notes
2. Upgrade Karpenter BEFORE or alongside control plane upgrade
3. For Karpenter v1.x, check minimum EKS version in docs

## Rollback Scenarios

Control-plane rollback is **conditional**, not one-way: EKS makes it available
only to an eligible cluster for seven days after a successful upgrade. During
that window, query `ListInsights` with `ROLLBACK_READINESS` and resolve every
`ERROR` or `UNKNOWN` before the operator attempts rollback. Outside the window,
or when eligibility conditions are not met, the control plane must be fixed
forward.

| Component | Rollback Possible? | How |
|-----------|-------------------|-----|
| Control plane | CONDITIONAL (7 days after eligible upgrade) | Operator performs rollback only after `ROLLBACK_READINESS` insights PASS; otherwise fix forward or follow the documented forced-rollback procedure |
| Addons | YES | `aws eks update-addon --addon-version <old-version>` |
| Managed node groups | PARTIAL | Can halt; completed nodes stay at new version |
| Self-managed nodes | YES | Revert launch template, terminate new nodes |
| Karpenter nodes | YES | Revert EC2NodeClass AMI, delete nodes |

## Prevention Checklist

- [ ] Run EKS Upgrade Insights before starting
- [ ] Test upgrade in a non-production cluster first
- [ ] Enable cluster audit logging before upgrade
- [ ] Take Velero backup of critical resources
- [ ] Verify all webhooks are compatible with target version
- [ ] Confirm no pending node group health issues
- [ ] Schedule upgrade during low-traffic window
- [ ] Have rollback plan for addons and node groups

## Feature-Specific Removal Guidance

### Dockershim Removal (EKS 1.25)

EKS Optimized AMI for 1.25+ no longer includes Dockershim. If workloads mount
the Docker socket (`/var/run/docker.sock`), they will break.

**Detection:**
```bash
# Install and run Detector for Docker Socket (DDS)
kubectl krew install dds
kubectl dds
```

**Resolution:** Remove Docker socket dependencies. Use containerd-compatible
alternatives or CRI APIs directly.

### PodSecurityPolicy Removal (EKS 1.25)

PSP was removed in Kubernetes 1.25. Clusters using PSP must migrate before
upgrading.

**Detection:**
```bash
kubectl get psp
# If any PSPs exist, migration is required
```

**Migration options:**
1. Pod Security Standards (PSS) with Pod Security Admission (PSA) — built-in
2. Policy-as-code: OPA Gatekeeper or Kyverno

See [AWS PSP removal FAQ](https://docs.aws.amazon.com/eks/latest/userguide/pod-security-policy-removal-faq.html).

### In-Tree Storage Driver Deprecation (EKS 1.23)

The `kubernetes.io/aws-ebs` in-tree provisioner is deprecated. Must use the
EBS CSI driver (`ebs.csi.aws.com`) before upgrading to 1.23+.

**Detection:**
```bash
kubectl get sc -o jsonpath='{range .items[*]}{.metadata.name}: {.provisioner}{"\n"}{end}'
# Look for kubernetes.io/aws-ebs
kubectl get pv -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.csi.driver // "in-tree"}{"\n"}{end}'
```

**Resolution:** Install the [Amazon EBS CSI driver](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html) and create new StorageClasses
using `ebs.csi.aws.com`. Existing PVs will be handled by CSI migration (automatic).

## Useful Upgrade Tools

| Tool | Purpose | Link |
|------|---------|------|
| kubent | Scan cluster for deprecated APIs | https://github.com/doitintl/kube-no-trouble |
| pluto | Detect deprecated APIs in cluster and Helm charts | https://pluto.docs.fairwinds.com |
| kubectl-convert | Convert manifests between API versions | https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/#install-kubectl-convert-plugin |
| eksup (ClowdHaus) | EKS upgrade guidance CLI | https://clowdhaus.github.io/eksup |
| GoNoGo | Determine upgrade confidence for add-ons | https://github.com/FairwindsOps/GoNoGo |
| DDS | Detect Docker socket dependencies | https://github.com/aws-containers/kubectl-detector-for-docker-socket |
| Velero | Cluster backup before upgrade | https://velero.io |
| AWS Backup | Managed backup for EKS | https://docs.aws.amazon.com/eks/latest/userguide/integration-backup.html |

## Blue-Green Cluster Strategy

For very large clusters or when skipping multiple minor versions is required:

**Benefits:**
- Can jump multiple EKS versions at once
- Able to switch back to old cluster if issues arise
- Creates a fresh cluster with latest configurations

**Downsides:**
- API endpoint and OIDC change (requires updating all consumers: kubectl, CI/CD, IRSA)
- Two clusters running in parallel (cost, capacity limits)
- Load balancers and external DNS cannot easily span clusters
- Stateful workload migration requires careful planning (data backup + restore)
- More coordination needed if workloads depend on each other

**When to consider:**
- Cluster is 3+ minor versions behind
- In-place sequential upgrades would take too long or be too risky
- Cluster was created with legacy tooling and needs to be rebuilt with modern IaC
- Compliance requires a clean-state cluster

### Identity Migration Considerations (IRSA vs. Pod Identity)

Both mechanisms need work on a new cluster, but the work is different — don't
assume "no IAM changes" means "no identity work":

| Mechanism | What Must Happen on the New Cluster | Effort |
|-----------|--------------------------------------|--------|
| **IRSA** | Each new cluster has its own OIDC provider ARN. Existing IAM role trust policies must be updated to also trust the new cluster's OIDC provider (a trust policy can list multiple issuers, but is capped at 4096 characters — roles shared across many clusters can hit this limit). | Edit IAM role trust policies |
| **Pod Identity** | The IAM role's trust policy does not change (it trusts the cluster-agnostic `pods.eks.amazonaws.com` service principal). However, associations (service account ↔ role mappings) are stored as an EKS resource scoped to one cluster — each association must be explicitly recreated with `aws eks create-pod-identity-association` on the new cluster. Nothing carries over automatically. | Recreate every association (no IAM edits) |

Before a blue-green cutover, inventory both:
```bash
# IRSA: service accounts with role-arn annotations
kubectl get sa -A -o json | jq '.items[] | select(.metadata.annotations["eks.amazonaws.com/role-arn"] != null) | {ns: .metadata.namespace, sa: .metadata.name, role: .metadata.annotations["eks.amazonaws.com/role-arn"]}'

# Pod Identity: existing associations on the source cluster
aws eks list-pod-identity-associations --cluster-name <source-cluster>
```
Every association returned by `list-pod-identity-associations` needs an
equivalent `create-pod-identity-association` call against the new cluster
before cutting workloads over — this is a mutation and belongs in the
Remediation Playbook (Step 14 of SKILL.md), not something the assessment
executes automatically.
