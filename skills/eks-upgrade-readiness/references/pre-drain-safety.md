# Pre-Drain Safety Checks (DRAIN-01 to DRAIN-06)

Beyond PDBs, node drains can fail or cause damage in 6 additional ways.
Check these BEFORE including drain in the upgrade plan.

## Check Summary

| ID | Check | Risk | Severity |
|----|-------|------|----------|
| DRAIN-01 | Bare pods (no ownerReferences) | Not rescheduled after eviction; kubectl drain refuses without --force | High |
| DRAIN-02 | Pods with emptyDir volumes | Data lost on drain (--delete-emptydir-data required) | Medium |
| DRAIN-03 | Custom finalizers on pods | Can hang eviction to timeout if finalizer controller is unhealthy | Medium |
| DRAIN-04 | EBS AZ-pinned PVCs | Cross-AZ reschedule strands the volume (Pending forever) | High |
| DRAIN-05 | Fail-closed webhooks on drain-target nodes | Evicting webhook pods deadlocks all further eviction cluster-wide | Critical |
| DRAIN-06 | CoreDNS SPOF | All CoreDNS replicas on same drain batch = cluster-wide DNS outage | Critical |

## DRAIN-01: Bare Pods

Pods without ownerReferences are not managed by a controller and will NOT be
rescheduled after eviction. `kubectl drain` refuses to evict them without
`--force`, which can stall automated node replacement.

```bash
kubectl get pods -A -o json | jq '.items[] | select(.metadata.ownerReferences == null) | {
  ns: .metadata.namespace,
  name: .metadata.name,
  node: .spec.nodeName
}'
```

**Remediation:** Wrap bare pods in a Deployment/Job, or acknowledge data loss
and allow `--force` drain.

## DRAIN-02: Pods with emptyDir Volumes

emptyDir volumes are ephemeral — data is lost when the pod is evicted.
`kubectl drain` requires `--delete-emptydir-data` flag to proceed.

```bash
kubectl get pods -A -o json | jq '.items[] | select(.spec.volumes[]?.emptyDir != null) | {
  ns: .metadata.namespace,
  name: .metadata.name,
  node: .spec.nodeName,
  emptyDirVolumes: [.spec.volumes[] | select(.emptyDir != null) | .name]
}'
```

**Remediation:** Ensure any important data in emptyDir is either ephemeral
(caches, temp files) or backed by external storage. Flag pods using emptyDir
for actual state (e.g., Prometheus WAL without persistent storage).

## DRAIN-03: Custom Finalizers on Pods

Pods with custom finalizers can hang eviction indefinitely if the finalizer
controller is unhealthy or slow:

```bash
kubectl get pods -A -o json | jq '.items[] | select(.metadata.finalizers != null and (.metadata.finalizers | length > 0)) | {
  ns: .metadata.namespace,
  name: .metadata.name,
  finalizers: .metadata.finalizers
}'
```

**Remediation:** Verify finalizer controllers are healthy. Consider removing
non-critical finalizers before upgrade or setting an eviction timeout.

## DRAIN-04: EBS AZ-Pinned PVCs

EBS volumes are AZ-bound. If a pod is drained to a node in a different AZ,
the PVC cannot be attached — the pod stays Pending forever.

```bash
# Find EBS PVCs with zone affinity
kubectl get pv -o json | jq '.items[] | select(.spec.csi.driver == "ebs.csi.aws.com") | {
  name: .metadata.name,
  zone: .spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[] | select(.key == "topology.ebs.csi.aws.com/zone") | .values[0],
  claim: .spec.claimRef.namespace + "/" + .spec.claimRef.name
}'

# Cross-reference with node group AZ distribution
kubectl get nodes -o json | jq '.items[] | {
  name: .metadata.name,
  zone: .metadata.labels["topology.kubernetes.io/zone"]
}'
```

**Remediation:** Ensure node groups span the same AZs as EBS volumes. For
StatefulSets with EBS, use `topologySpreadConstraints` or node affinity to
keep pods in the same AZ as their volumes. During upgrade, ensure surge
nodes are launched in every AZ that has EBS volumes.

## DRAIN-05: Fail-Closed Webhooks on Drain-Target Nodes

If ALL endpoint pods for a `failurePolicy: Fail` webhook are on nodes being
drained simultaneously, evicting those pods causes a cluster-wide deadlock —
no further evictions can proceed because the webhook rejects all API calls.

```bash
# Find fail-closed webhooks and their backing endpoints
kubectl get validatingwebhookconfigurations -o json | jq '.items[].webhooks[] | select(.failurePolicy == "Fail") | {name: .name, service: .clientConfig.service}'
kubectl get mutatingwebhookconfigurations -o json | jq '.items[].webhooks[] | select(.failurePolicy == "Fail") | {name: .name, service: .clientConfig.service}'

# For each webhook service, check endpoint pod distribution
# Example for a webhook service named "webhook-svc" in namespace "system":
kubectl get endpoints webhook-svc -n system -o json | jq '.subsets[].addresses[].nodeName'

# Cross-reference with nodes scheduled for drain
```

**Remediation:**
- Ensure webhook pods have anti-affinity to spread across nodes/AZs
- Set PDBs on webhook deployments to prevent all replicas from draining simultaneously
- Consider `failurePolicy: Ignore` for non-critical webhooks during upgrade
- Drain webhook-hosting nodes LAST

## DRAIN-06: CoreDNS Single Point of Failure

If all CoreDNS replicas end up on nodes in the same drain batch, the entire
cluster loses DNS resolution — new pods can't resolve services, health checks
fail, and cascading failures follow.

```bash
# Check CoreDNS pod distribution
kubectl get pods -n kube-system -l k8s-app=kube-dns -o wide

# Detailed node placement
kubectl get pods -n kube-system -l k8s-app=kube-dns -o json | jq '.items[] | {
  name: .metadata.name,
  node: .spec.nodeName,
  ready: .status.conditions[] | select(.type == "Ready") | .status
}'

# Check if CoreDNS has topology spread or anti-affinity
kubectl get deploy coredns -n kube-system -o json | jq '{
  replicas: .spec.replicas,
  topologySpread: .spec.template.spec.topologySpreadConstraints,
  affinity: .spec.template.spec.affinity
}'
```

**Remediation:**
- Ensure CoreDNS has at least 2 replicas (ideally 3+)
- Add `topologySpreadConstraints` to spread across AZs
- Set a PDB with `minAvailable: 2` (or appropriate for replica count)
- During node rotation, verify CoreDNS pods are rescheduled FIRST before
  proceeding with further drains
- Consider running CoreDNS on dedicated system nodes or Fargate
