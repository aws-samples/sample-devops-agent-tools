# EKS CloudWatch Metrics Thresholds Reference

## Container Insights Metrics (Namespace: ContainerInsights)

| Metric | Normal | Warning | Critical | Finding |
|--------|--------|---------|----------|---------|
| node_cpu_utilization | <70% | >70% | >90% | Right-size or add capacity |
| node_memory_utilization | <80% | >80% | >95% | OOM risk, increase capacity |
| node_filesystem_utilization | <70% | >70% | >85% | Disk exhaustion risk |
| pod_cpu_utilization | 10-60% | <10% or >60% | >80% | Over/under-provisioned |
| pod_memory_utilization | 20-70% | <20% or >70% | >85% | Over/under-provisioned |
| cluster_failed_node_count | 0 | >0 | >1 | Node failures detected |
| pod_number_of_container_restarts | <50/7d | >50/7d | >200/7d | Unstable pods |

## EKS Control Plane Metrics (Namespace: AWS/EKS)

| Metric | Normal | Warning | Critical | Finding |
|--------|--------|---------|----------|---------|
| apiserver_request_duration_seconds | <500ms | >500ms | >1s | API server latency |
| apiserver_admission_webhook_rejection_count | 0 | >0 | >10 | Webhook config issues |
| scheduler_pending_pods | 0 | >0 sustained | >10 sustained | Scheduling failures |

## EC2 Node Metrics (Namespace: AWS/EC2)

| Metric | Normal | Warning | Critical | Finding |
|--------|--------|---------|----------|---------|
| CPUUtilization | <70% | >80% | >95% | CPU saturation |
| StatusCheckFailed | 0 | — | >0 | Hardware/system failure |

## CloudWatch Log Patterns

| Pattern | Severity if Found | Action |
|---------|------------------|--------|
| ERROR (>100/7d) | MEDIUM | Investigate root cause |
| 429 (throttling) | HIGH | Reduce API call rate |
| OOMKilled | HIGH | Increase memory limits |
| FailedScheduling | MEDIUM | Check capacity/constraints |
| Evicted | HIGH | Check node resource pressure |

## CloudTrail Event Analysis

| Event Pattern | Severity | Action |
|--------------|----------|--------|
| AccessDenied errors | HIGH | Investigate unauthorized access |
| CreateAccessEntry | MEDIUM | Verify authorized |
| UpdateClusterConfig | INFO | Audit trail |
| DeleteCluster | INFO | Verify intentional |
| High write volume from single principal | MEDIUM | Unusual activity |
