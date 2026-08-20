# AL2 → AL2023 Migration Assessment

This document covers the full AL2 to AL2023 migration checks for EKS upgrade
readiness assessments. If any node group or EC2NodeClass uses AL2, and the target
version is 1.33+, this is a **CRITICAL** blocker because EKS stopped releasing
AL2 AMIs after 1.32. For targets < 1.33, flag AL2 usage as a **WARNING**:
upstream Amazon Linux 2 reaches end of life on June 30, 2026.

## Detection Commands

```bash
# Check MNG AMI types
aws eks list-nodegroups --cluster-name <cluster> --query 'nodegroups[]' --output text | \
  xargs -I {} aws eks describe-nodegroup --cluster-name <cluster> --nodegroup-name {} \
  --query 'nodegroup.amiType' --output text

# Check Karpenter EC2NodeClass amiFamily
kubectl get ec2nodeclasses -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.amiFamily}{"\n"}{end}'
```

## Launch Template Analysis

```bash
# Get launch template user data for bootstrap method detection
aws ec2 describe-launch-template-versions --launch-template-id <lt-id> \
  --versions <version> --query 'LaunchTemplateVersions[0].LaunchTemplateData.UserData' | \
  base64 -d
```

## Bootstrap Differences

| Aspect | AL2 (bootstrap.sh) | AL2023 (nodeadm) |
|--------|--------------------|--------------------|
| Bootstrap script | `/etc/eks/bootstrap.sh` | `nodeadm` with YAML NodeConfig |
| Config format | CLI flags | `/etc/nodeadm/nodeconfig.yaml` |
| Cgroup driver | cgroup v1 | cgroup v2 (unified) |
| IMDS | v1 enabled by default | v2 only (IMDSv2) |
| Container runtime | containerd (since 1.24) | containerd |
| Kernel | 5.10 | 6.1 |

## Custom AMI Detection

If a node group uses a custom AMI (not EKS-optimized):

```bash
# Check if AMI is EKS-optimized or custom
AMI_ID=$(aws ec2 describe-launch-template-versions --launch-template-id <lt-id> \
  --versions <version> --query 'LaunchTemplateVersions[0].LaunchTemplateData.ImageId' --output text)
aws ec2 describe-images --image-ids $AMI_ID --query 'Images[0].Name' --output text
# EKS-optimized pattern: amazon-eks-node-<version>-*
# Custom: anything else
```

If custom AMI is detected:
- Flag that automated AMI updates won't work
- User must rebuild their AMI pipeline for AL2023 base
- Check if user data scripts are AL2-specific (yum vs dnf, systemd units, etc.)

## User Data / Bootstrap Compatibility

Check user data for AL2-specific patterns that break on AL2023:

- `--kubelet-extra-args` in bootstrap.sh → must convert to NodeConfig YAML
- `/etc/docker/daemon.json` → irrelevant on AL2023 (containerd only)
- `yum install` → must change to `dnf install`
- `/etc/sysctl.d/` settings → verify cgroup v2 compatibility
- IMDSv1 assumptions → AL2023 defaults to IMDSv2 only

### Example: Converting bootstrap.sh to nodeadm NodeConfig

**AL2 (bootstrap.sh):**
```bash
/etc/eks/bootstrap.sh my-cluster \
  --kubelet-extra-args '--max-pods=110 --node-labels=workload=compute'
```

**AL2023 (nodeadm NodeConfig):**
```yaml
apiVersion: node.eks.aws/v1alpha1
kind: NodeConfig
spec:
  cluster:
    name: my-cluster
    apiServerEndpoint: https://...
    certificateAuthority: ...
  kubelet:
    config:
      maxPods: 110
    flags:
      - --node-labels=workload=compute
```

## Cgroup v2 Compatibility

AL2023 uses cgroup v2 (unified hierarchy). Check for workloads that assume cgroup v1:

- **Java apps with `-XX:+UseContainerSupport`:** Works on both, but check JDK version.
  JDK 15+ has full cgroup v2 support. JDK 8u372+ and 11.0.16+ have partial support.
- **Monitoring agents reading `/sys/fs/cgroup/memory/`:** This is the v1 path; v2 uses
  `/sys/fs/cgroup/memory.max` etc. Agents that hardcode v1 paths will break.
- **Custom init containers manipulating cgroup files directly:** Any direct cgroup
  filesystem manipulation needs updating.
- **Resource monitoring tools:** cAdvisor < 0.43 has limited cgroup v2 support.

### Detection

```bash
# Find pods that mount cgroup filesystem directly
kubectl get pods -A -o json | jq '.items[] | select(.spec.volumes[]?.hostPath.path | test("/sys/fs/cgroup")) | {
  ns: .metadata.namespace,
  name: .metadata.name,
  mounts: [.spec.volumes[] | select(.hostPath.path | test("/sys/fs/cgroup")) | .hostPath.path]
}'

# Check Java version in common images (requires exec access)
# kubectl exec <pod> -- java -version 2>&1 | head -1
```

## IMDSv2 Compatibility

AL2023 defaults to IMDSv2 only (requires token-based requests). Check for
workloads that use IMDSv1 (simple HTTP GET without token):

### Common IMDSv1 Patterns That Break

- AWS SDK versions before credential provider chain update (SDK v1 < 1.11.x)
- Custom scripts using `curl http://169.254.169.254/latest/meta-data/`
  without first obtaining a session token
- Legacy EC2 metadata queries without `X-aws-ec2-metadata-token` header

### Detection

```bash
# Check launch template IMDS settings
aws ec2 describe-launch-template-versions --launch-template-id <lt-id> \
  --versions <version> --query 'LaunchTemplateVersions[0].LaunchTemplateData.MetadataOptions'

# Check node group IMDS configuration
aws eks describe-nodegroup --cluster-name <cluster> --nodegroup-name <ng> \
  --query 'nodegroup.launchTemplate'
```

### Remediation

- Update AWS SDK to latest version (all modern SDKs support IMDSv2)
- Replace `curl http://169.254.169.254/...` with token-based access:
  ```bash
  TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
  curl -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/
  ```
- Use IRSA or Pod Identity instead of IMDS for AWS credentials (preferred)

## Migration Strategy Summary

1. **Inventory:** Identify all AL2 node groups and Karpenter EC2NodeClasses
2. **Bootstrap:** Convert all bootstrap.sh args to nodeadm NodeConfig YAML
3. **Custom AMIs:** Rebuild AMI pipelines with AL2023 base
4. **User Data:** Update package managers (yum→dnf), systemd units, scripts
5. **Cgroup v2:** Validate workloads with cgroup v2 compatibility
6. **IMDSv2:** Ensure all metadata access uses tokens or IRSA/Pod Identity
7. **Test:** Deploy AL2023 node group in parallel, migrate workloads gradually
8. **Cutover:** Drain AL2 nodes after validation
