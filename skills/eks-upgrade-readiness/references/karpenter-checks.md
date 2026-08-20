# Karpenter Upgrade Readiness Checks (KARP-01 to KARP-14)

This document contains the full Karpenter check registry and detection commands
for EKS upgrade readiness assessments.

## Discovery Commands

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

## Check Registry

| ID | Check | Pass Criteria | Severity |
|----|-------|---------------|----------|
| KARP-01 | Version compatibility | Karpenter release supports target K8s version | Critical |
| KARP-02 | Drift enabled | Feature gate on (default since v0.33) | High |
| KARP-03 | expireAfter set | Not `Never` on any NodePool | High |
| KARP-04 | Disruption budgets | At least 1 node can be disrupted (not `nodes: "0"`) | High |
| KARP-05 | AMI not pinned | amiSelectorTerms not pinned to specific AMI ID | High |
| KARP-06 | AMI family valid | amiFamily not AL2 when target >= 1.33 | Critical |
| KARP-07 | Not self-hosted | Controller pods NOT on Karpenter-managed nodes | Critical |
| KARP-08 | NodeClassRef valid | Every NodePool's nodeClassRef points to existing EC2NodeClass | High |
| KARP-09 | Consolidation interference | Short consolidateAfter + active drift = race condition | Medium |
| KARP-10 | Schedule conflict | Disruption budget schedule doesn't block upgrade window | Medium |
| KARP-11 | ExpireAfter timing | NodeClaim expiry won't trigger during upgrade window | Medium |
| KARP-12 | Drift throughput | Estimated time to replace all nodes vs acceptable window | Low |
| KARP-13 | Controller health | All replicas ready, no crash-looping | Critical |
| KARP-14 | v1alpha5 orphans | No leftover Provisioner CRD from incomplete migration | Medium |

## KARP-07: Self-Hosted Detection (Critical)

If Karpenter runs on nodes it manages, it may evict itself during drift-based
replacement, halting all further node rotation.

```bash
# Check if karpenter pods run on karpenter-managed nodes
KARP_NODES=$(kubectl get pods -n kube-system -l app.kubernetes.io/name=karpenter -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}')
for node in $KARP_NODES; do
  kubectl get node $node -o jsonpath='{.metadata.labels}' | grep -q "karpenter.sh/nodepool" && echo "FAIL: Karpenter self-hosted on $node"
done
```

## KARP-08: Dangling NodeClassRef Detection

Dangling nodeClassRef prevents Karpenter from launching replacement nodes after
drift fires:

```bash
# Verify all NodePool nodeClassRefs resolve
NODECLASSES=$(kubectl get ec2nodeclasses -o jsonpath='{.items[*].metadata.name}')
kubectl get nodepools -o json | jq --arg ncs "$NODECLASSES" '.items[] | select(.spec.template.spec.nodeClassRef.name as $ref | ($ncs | split(" ") | index($ref)) == null) | {pool: .metadata.name, danglingRef: .spec.template.spec.nodeClassRef.name}'
```

## KARP-09: Consolidation Interference

Short `consolidateAfter` combined with active drift creates a race condition
where consolidation may terminate nodes that drift is trying to replace.

Check:
```bash
kubectl get nodepools -o json | jq '.items[] | select(.spec.disruption.consolidateAfter != null and .spec.disruption.consolidateAfter != "Never") | {name: .metadata.name, consolidateAfter: .spec.disruption.consolidateAfter}'
```

If consolidateAfter is < 30m and drift is active, flag as WARN.

## KARP-10: Schedule Conflict

Disruption budget schedules that overlap with the planned upgrade window can
block Karpenter from replacing nodes:

```bash
kubectl get nodepools -o json | jq '.items[] | select(.spec.disruption.budgets[]?.schedule != null) | {name: .metadata.name, budgets: .spec.disruption.budgets}'
```

## KARP-11: ExpireAfter Timing

If NodeClaims are close to their expiry time, they may trigger replacement
during the upgrade window causing unexpected churn:

```bash
kubectl get nodeclaims -o json | jq '.items[] | {name: .metadata.name, created: .metadata.creationTimestamp, expireAfter: .spec.expireAfter}'
```

## KARP-13: Controller Health

```bash
kubectl get deploy karpenter -n kube-system -o json | jq '{
  replicas: .spec.replicas,
  ready: .status.readyReplicas,
  available: .status.availableReplicas,
  conditions: .status.conditions
}'

# Check for crash loops
kubectl get pods -n kube-system -l app.kubernetes.io/name=karpenter -o json | jq '.items[] | {
  name: .metadata.name,
  ready: .status.containerStatuses[0].ready,
  restarts: .status.containerStatuses[0].restartCount,
  state: .status.containerStatuses[0].state
}'
```

## KARP-14: v1alpha5 Orphan Detection

After migration from Karpenter < 0.33, leftover Provisioner CRDs may remain:

```bash
# Check for old Provisioner CRD
kubectl get crd provisioners.karpenter.sh 2>/dev/null && echo "WARN: Legacy Provisioner CRD still exists"

# Check for leftover Provisioner resources
kubectl get provisioners 2>/dev/null && echo "WARN: Legacy Provisioner resources found"

# Check for old AWSNodeTemplate CRD
kubectl get crd awsnodetemplates.karpenter.k8s.aws 2>/dev/null && echo "WARN: Legacy AWSNodeTemplate CRD still exists"
```
