# EKS Addon Version Compatibility Matrix

> Static fallback reference. Last verified: 2026-08-17. `DescribeAddonVersions`
> for the current cluster and target Kubernetes version is the compatibility
> authority. Do not select a version, or mark a blocker, from this table alone.

This reference shows historically recommended addon version families per EKS
Kubernetes version. Use it only when live API data is unavailable, and report
that the relevant compatibility gate as `UNKNOWN` rather than `PASS`.

## Core Addons

| EKS Version | kube-proxy | vpc-cni | coredns | aws-ebs-csi-driver |
|-------------|------------|---------|---------|-------------------|
| 1.32 | v1.32.x | v1.19+ | v1.12+ | v1.38+ |
| 1.31 | v1.31.x | v1.18+ | v1.11+ | v1.35+ |
| 1.30 | v1.30.x | v1.18+ | v1.11+ | v1.33+ |
| 1.29 | v1.29.x | v1.16+ | v1.11+ | v1.28+ |
| 1.28 | v1.28.x | v1.15+ | v1.10+ | v1.25+ |

## Addon Upgrade Rules

### kube-proxy
- Treat matching the target control-plane minor as the normal **post-upgrade
  alignment recommendation**, not a hard-coded compatibility rule.
- Use `DescribeAddonVersions` to decide whether the installed version blocks
  the target upgrade. The live API result overrides this static reference.
- Upgrade promptly after the control plane when the live API identifies a
  target-compatible version. During the transition, respect the Kubernetes
  kube-proxy skew policy rather than assuming an exact minor match is the only
  valid state.

### vpc-cni (amazon-vpc-cni-k8s)
- Generally backward compatible across 2-3 minor versions
- New features (prefix delegation, Security Groups for Pods, network policy)
  may require specific minimum versions
- Safe to run a newer vpc-cni on an older control plane

### coredns
- Backward compatible across multiple minor versions
- New EKS versions may require minimum coredns for new features
- Check `coredns:coredns/corefile-migration` for Corefile compatibility

### aws-ebs-csi-driver
- Version constraints driven by CSI spec version and sidecar compatibility
- Newer versions add volume snapshot, resize, and topology awareness features
- Check for deprecation of `kubernetes.io/aws-ebs` in-tree provisioner

## How to Check Compatibility

```bash
# List available versions for an addon on target EKS version
aws eks describe-addon-versions \
  --addon-name vpc-cni \
  --kubernetes-version 1.31 \
  --query 'addons[0].addonVersions[*].{version:addonVersion,default:compatibilities[0].defaultVersion}' \
  --output table

# Check current addon versions on a cluster
aws eks list-addons --cluster-name <cluster> --output text
for addon in $(aws eks list-addons --cluster-name <cluster> --output text --query 'addons[]'); do
  echo "$addon: $(aws eks describe-addon --cluster-name <cluster> --addon-name $addon --query 'addon.addonVersion' --output text)"
done
```

## Self-Managed and Custom-Configured Addons

First compare `aws eks list-addons` with the in-cluster inventory. A core
component that exists in `kube-system` but is absent from `ListAddons` is
self-managed (or replaced) and must not be assumed compatible from EKS managed
addon APIs alone.

```bash
# Core components that may be self-managed: images, args, and configuration
kubectl -n kube-system get daemonset aws-node kube-proxy -o json | \
  jq '.items[] | {name: .metadata.name, images: [.spec.template.spec.containers[].image], args: [.spec.template.spec.containers[].args]}'
kubectl -n kube-system get deployment coredns -o json | \
  jq '.items[] | {name: .metadata.name, images: [.spec.template.spec.containers[].image], args: [.spec.template.spec.containers[].args]}'
kubectl -n kube-system get configmap coredns aws-node -o yaml
```

- **VPC CNI (`aws-node`)** — inspect image, environment variables, and the
  `aws-node` ConfigMap. A custom image or configuration needs its own
  compatibility validation; use the mode-aware capacity gate in SKILL.md Step 2.
- **CoreDNS** — inspect image and Corefile. A custom Corefile requires the
  CoreDNS migration check for the target release; do not overwrite it without
  a reviewed backup and migration plan.
- **kube-proxy** — inspect image, mode/configuration, and DaemonSet arguments.
  Use the upstream component documentation plus target-version testing when it
  is not EKS managed.
- For other self-managed addons, check the addon's release notes for Kubernetes
  version support: Karpenter, AWS Load Balancer Controller, ExternalDNS,
  cert-manager, ingress-nginx, Argo CD, and Flux.

## Upgrade Order

1. Pre-control-plane compatibility remediation where needed (Karpenter,
   Cluster Autoscaler, webhooks, and controllers that must span source/target).
2. Control plane.
3. kube-proxy, VPC CNI, CoreDNS, CSI drivers, other managed addons, then
   self-managed addons — use target-compatible versions returned by live APIs
   or each self-managed addon's support matrix.

### Configuration-Conflict Strategy (operator approval required)

Before updating a managed addon, save `DescribeAddon` output and any
`configurationValues`. Select a conflict mode deliberately:

- `PRESERVE` retains customer configuration values. Use it first when custom
  configuration is intentional and compatible with the target addon.
- `OVERWRITE` replaces conflicting customer configuration with EKS defaults.
  It can discard custom behavior; use it only after review, backup, and a
  documented rollback plan.

Neither option is part of the read-only assessment. They belong in the
operator-approved remediation playbook.
