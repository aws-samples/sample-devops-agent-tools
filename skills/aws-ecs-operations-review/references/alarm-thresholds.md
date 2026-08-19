# Recommended CloudWatch Alarm Thresholds

Recommend these alarms as part of every ECS operations review. Sourced from:
- AWS CloudWatch Recommended Alarms: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ECS
- ECS Monitoring Guide: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs_monitoring.html

## Standard ECS Alarms (AWS/ECS Namespace)

These alarms use the standard AWS/ECS namespace. Recommend all rows unless a footnote condition excludes them.

| Alarm | Metric | Namespace | Dimensions | Statistic | Threshold | Period | Datapoints / Eval | Operator | doc_url |
|-------|--------|-----------|------------|-----------|-----------|--------|-------------------|----------|---------|
| Service CPU High | CPUUtilization | AWS/ECS | ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ECS |
| Service Memory High | MemoryUtilization | AWS/ECS | ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ECS |
| EBS Filesystem High¹ | EBSFilesystemUtilization | AWS/ECS | ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ECS |
| Cluster CPU Reservation² | CPUReservation | AWS/ECS | ClusterName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ECS |
| Cluster Memory Reservation² | MemoryReservation | AWS/ECS | ClusterName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ECS |

¹ EBSFilesystemUtilization is only emitted when EBS volumes are attached to the task; recommend only when EBS volumes are configured in the task definition.

² CPUReservation and MemoryReservation are NOT recommended for Fargate clusters with capacity providers.

## Capacity Provider Alarms (EC2 ASG capacity providers with managed scaling only)

Recommend when the service runs on an EC2 Auto Scaling group capacity provider with managed scaling ENABLED. Not applicable to Fargate or Managed Instances (AWS manages that scaling).

| Alarm | Metric | Namespace | Dimensions | Statistic | Threshold | Period | Datapoints / Eval | Operator | doc_url |
|-------|--------|-----------|------------|-----------|-----------|--------|-------------------|----------|---------|
| Capacity Provider Saturated | CapacityProviderReservation | AWS/ECS/ManagedScaling | CapacityProviderName | Maximum | >= 100 (tasks waiting on instance capacity; tune relative to configured targetCapacity) | 60s | 5 / 5 | >= threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cluster-auto-scaling.html |

## Container Insights Alarms

These alarms require Container Insights to be enabled on the cluster. MUST be recommended when Container Insights is active.

| Alarm | Metric | Namespace | Dimensions | Statistic | Threshold | Period | Datapoints / Eval | Operator | doc_url |
|-------|--------|-----------|------------|-----------|-----------|--------|-------------------|----------|---------|
| Running Task Count | RunningTaskCount | ECS/ContainerInsights | ClusterName, ServiceName | Average | < desiredCount (derived from service config) | 60s | 5 / 5 | < threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Task CPU (Cluster) | TaskCpuUtilization | ECS/ContainerInsights | ClusterName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Task CPU (Service) | TaskCpuUtilization | ECS/ContainerInsights | ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Task Memory (Cluster) | TaskMemoryUtilization | ECS/ContainerInsights | ClusterName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Task Memory (Service) | TaskMemoryUtilization | ECS/ContainerInsights | ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Container CPU (Cluster) | ContainerCpuUtilization | ECS/ContainerInsights | ClusterName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Container CPU (Service) | ContainerCpuUtilization | ECS/ContainerInsights | ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Container Memory (Cluster) | ContainerMemoryUtilization | ECS/ContainerInsights | ClusterName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Container Memory (Service) | ContainerMemoryUtilization | ECS/ContainerInsights | ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Task Ephemeral Storage (Cluster) | TaskEphemeralStorageUtilization | ECS/ContainerInsights | ClusterName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Task Ephemeral Storage (Service) | TaskEphemeralStorageUtilization | ECS/ContainerInsights | ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |
| Filesystem Utilization (EC2)* | instance_filesystem_utilization | ECS/ContainerInsights | InstanceId, ContainerInstanceId, ClusterName | Average | 90% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-container-insights.html |

\* Filesystem Utilization applies only to EC2 launch type clusters; NOT applicable to Fargate.

## Enhanced Observability Alarms

These alarms require Container Insights with Enhanced Observability. MUST be recommended when enhanced observability is enabled.

| Alarm | Metric | Namespace | Dimensions | Statistic | Threshold | Period | Datapoints / Eval | Operator | doc_url |
|-------|--------|-----------|------------|-----------|-----------|--------|-------------------|----------|---------|
| Container CPU | ContainerCpuUtilization | ECS/ContainerInsights | ContainerName, ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-metrics.html#enhanced-container-insights |
| Container Memory | ContainerMemoryUtilization | ECS/ContainerInsights | ContainerName, ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-metrics.html#enhanced-container-insights |
| Task EBS Filesystem³ | TaskEBSFilesystemUtilization | ECS/ContainerInsights | ClusterName, ServiceName | Average | 80% | 60s | 5 / 5 | > threshold | https://docs.aws.amazon.com/AmazonECS/latest/developerguide/cloudwatch-metrics.html#enhanced-container-insights |

³ TaskEBSFilesystemUtilization is only emitted when EBS volumes are attached to the task; recommend only when EBS volumes are configured in the task definition.

## Relative Alarms

These require cross-referencing collected config data to compute thresholds.

| Alarm | Metric | Namespace | Statistic | Relative To | Warning | Critical | Period | Eval | doc_url |
|-------|--------|-----------|-----------|-------------|---------|----------|--------|------|---------|
| CPU Spike | CPUUtilization | AWS/ECS | Average | 7-day baseline average | > 150% baseline | > 200% baseline | 5 min | 3/5 | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ECS |
| Memory Spike | MemoryUtilization | AWS/ECS | Average | 7-day baseline average | > 150% baseline | > 200% baseline | 5 min | 3/5 | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ECS |

## Load Balancer Target Group Alarms (if load balancer configured)

**ALB (Application Load Balancer):**

| Alarm | Metric | Namespace | Dimensions | Statistic | Warning | Critical | Period | Eval | doc_url |
|-------|--------|-----------|------------|-----------|---------|----------|--------|------|---------|
| Unhealthy Targets | UnHealthyHostCount | AWS/ApplicationELB | TargetGroup, LoadBalancer | Maximum | > 0 | > 1 | 1 min | 2/3 | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ALB |
| Target Response Time | TargetResponseTime | AWS/ApplicationELB | TargetGroup, LoadBalancer | p99 | > 1s | > 3s | 5 min | 3/5 | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ALB |
| HTTP 5xx Errors | HTTPCode_Target_5XX_Count | AWS/ApplicationELB | TargetGroup, LoadBalancer | Sum | > 10 | > 50 | 5 min | 3/5 | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#ALB |

**NLB (Network Load Balancer):**

NLB operates at Layer 4 — `TargetResponseTime` and `HTTPCode_Target_5XX_Count` are NOT available.

| Alarm | Metric | Namespace | Dimensions | Statistic | Warning | Critical | Period | Eval | doc_url |
|-------|--------|-----------|------------|-----------|---------|----------|--------|------|---------|
| Unhealthy Targets | UnHealthyHostCount | AWS/NetworkELB | TargetGroup, LoadBalancer | Maximum | > 0 | > 1 | 1 min | 2/3 | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#NLB |
| TCP Target Resets | TCP_Target_Reset_Count | AWS/NetworkELB | TargetGroup, LoadBalancer | Sum | > 100 | > 500 | 5 min | 3/5 | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Best_Practice_Recommended_Alarms_AWS_Services.html#NLB |

## Implementation Priority

When recommending alarms in the operations review report, use this priority order:

| Priority | Alarms | Rationale |
|----------|--------|-----------|
| P0 (Immediate) | RunningTaskCount < desiredCount | Service availability — partial or complete outage detection |
| P1 (High) | CPUUtilization > 80%, MemoryUtilization > 80% | Resource bottleneck detection |
| P1 (High) | HTTPCode_Target_5XX_Count | Application error detection |
| P1 (High) | UnHealthyHostCount > 0 | LB-attached service health — targets failing health checks |
| P2 (Medium) | TargetResponseTime | Latency monitoring |
| P2 (Medium) | TaskEphemeralStorageUtilization > 80% | Disk space monitoring |
| P2 (Medium) | EBSFilesystemUtilization > 80% | EBS volume monitoring |
| P3 (Low) | ContainerCpuUtilization, ContainerMemoryUtilization | Per-container granularity |
| P3 (Low) | CPU/Memory Spike (relative) | Anomaly detection |

## Alarm Actions

- All alarms: SNS notification to operations team
- P0 RunningTaskCount (critical): Page on-call for immediate investigation
- P1 High CPU/Memory (critical): Trigger auto scaling if not already configured
- P1 HTTP 5XX (critical): Investigate application logs and container health
- ALB Unhealthy Targets (critical): Investigate container health and startup time

## Notes

- ECS CPU/memory metrics are percentages of the task-level reservation, not the host
- Container Insights must be enabled on the cluster for RunningTaskCount metric
- Enhanced Observability provides per-container metrics (ContainerName dimension)
- For Fargate, CPU and memory are hard limits — tasks are killed when exceeded
- For EC2 launch type, memory is a soft limit unless hard limit is also set
- ALB/NLB alarms should include both `TargetGroup` and `LoadBalancer` dimensions, scoped to the specific target group
- Target response time and other absolute-count thresholds (UnHealthyHostCount, HTTPCode_Target_5XX_Count, TCP_Target_Reset_Count) are starting points — adjust based on SLA and baseline traffic patterns
- CPUReservation/MemoryReservation are NOT recommended for Fargate with capacity providers

## Baseline Metrics

Pull with `cloudwatch.getMetricStatistics`, Period 3600, StartTime 7 days ago.

| Metric | Namespace | Statistics | Dimensions |
|--------|-----------|------------|------------|
| CPUUtilization | AWS/ECS | Average, Maximum | ClusterName, ServiceName |
| MemoryUtilization | AWS/ECS | Average, Maximum | ClusterName, ServiceName |
| RunningTaskCount | ECS/ContainerInsights | Average, Minimum | ClusterName, ServiceName |
| CapacityProviderReservation* | AWS/ECS/ManagedScaling | Average, Maximum | CapacityProviderName |

\* Only for EC2 ASG capacity providers with managed scaling enabled — feeds PERF10 (baseline vs targetCapacity analysis). Not emitted for Fargate or Managed Instances.

## ECS Service Limits Reference

| Limit | Value |
|-------|-------|
| Max tasks per service | 5,000 |
| Max services per cluster | 5,000 |
| Max container instances per cluster | 5,000 |
| Fargate task CPU range | 256 (.25 vCPU) – 16384 (16 vCPU) |
| Fargate task memory range | 512 MiB – 120 GB |
| Max containers per task definition | 10 |
| Max target groups per service | 5 |
