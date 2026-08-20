# Kubernetes API Deprecations and Removals by EKS Version

> Static fallback reference. Last verified: 2026-08-17. Confirm the target
> version against live EKS Upgrade Insights and the Kubernetes release notes;
> this table is not a compatibility authority.

This reference maps deprecated and removed Kubernetes APIs to EKS versions.
Use this to identify workloads that must be updated before upgrading.

## How to Read This Table

- **Deprecated**: API still works but emits warnings in audit logs
- **Removed**: API returns 404 — workloads using it will break

## Removals by Target Version

### EKS 1.32 (Kubernetes 1.32)

| API | Replacement | Resources Affected |
|-----|-------------|-------------------|
| `flowcontrol.apiserver.k8s.io/v1beta3` | `flowcontrol.apiserver.k8s.io/v1` | FlowSchema, PriorityLevelConfiguration |

### EKS 1.29 (Kubernetes 1.29)

| API | Replacement | Resources Affected |
|-----|-------------|-------------------|
| `flowcontrol.apiserver.k8s.io/v1beta2` | `flowcontrol.apiserver.k8s.io/v1` | FlowSchema, PriorityLevelConfiguration |

### EKS 1.27 (Kubernetes 1.27)

| API | Replacement | Resources Affected |
|-----|-------------|-------------------|
| `storage.k8s.io/v1beta1` (CSIStorageCapacity) | `storage.k8s.io/v1` | CSIStorageCapacity |

### EKS 1.26 (Kubernetes 1.26)

| API | Replacement | Resources Affected |
|-----|-------------|-------------------|
| `flowcontrol.apiserver.k8s.io/v1beta1` | `flowcontrol.apiserver.k8s.io/v1beta3` | FlowSchema, PriorityLevelConfiguration |
| `autoscaling/v2beta2` | `autoscaling/v2` | HorizontalPodAutoscaler |

### EKS 1.25 (Kubernetes 1.25)

| API | Replacement | Resources Affected |
|-----|-------------|-------------------|
| `policy/v1beta1` | `policy/v1` | PodDisruptionBudget, PodSecurityPolicy (removed entirely) |
| `batch/v1beta1` | `batch/v1` | CronJob |
| `discovery.k8s.io/v1beta1` | `discovery.k8s.io/v1` | EndpointSlice |
| `events.k8s.io/v1beta1` | `events.k8s.io/v1` | Event |
| `autoscaling/v2beta1` | `autoscaling/v2` | HorizontalPodAutoscaler |
| `node.k8s.io/v1beta1` | `node.k8s.io/v1` | RuntimeClass |

### EKS 1.22 (Kubernetes 1.22)

| API | Replacement | Resources Affected |
|-----|-------------|-------------------|
| `networking.k8s.io/v1beta1` | `networking.k8s.io/v1` | Ingress, IngressClass |
| `rbac.authorization.k8s.io/v1beta1` | `rbac.authorization.k8s.io/v1` | ClusterRole, ClusterRoleBinding, Role, RoleBinding |
| `admissionregistration.k8s.io/v1beta1` | `admissionregistration.k8s.io/v1` | MutatingWebhookConfiguration, ValidatingWebhookConfiguration |
| `apiextensions.k8s.io/v1beta1` | `apiextensions.k8s.io/v1` | CustomResourceDefinition |

## Detection Methods

### Via EKS Upgrade Insights (recommended)
```
aws eks list-insights --cluster-name <cluster> \
  --filter '{"categories":["UPGRADE_READINESS"]}'
```

### Via Kubernetes audit logs (if enabled)
Search for `k8s.io/deprecated=true` annotation in API server audit logs:
```
fields @timestamp, objectRef.resource, objectRef.apiVersion, user.username
| filter annotations.`k8s.io/deprecated` = "true"
| stats count() by objectRef.apiVersion, objectRef.resource
```

### Via kubectl (requires cluster access)
```bash
# Check for deprecated APIs using kubectl
kubectl get --raw /metrics | grep apiserver_requested_deprecated_apis
```

## Remediation Pattern

For each deprecated API usage:
1. Identify the controller/workload using it (from insight or audit log)
2. Update the manifest `apiVersion` field to the replacement
3. Check if the resource spec changed between versions (some fields moved)
4. Apply the updated manifest
5. Verify the workload is healthy
6. Confirm no more deprecation warnings in audit logs
