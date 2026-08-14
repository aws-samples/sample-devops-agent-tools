---
title: "K14 — Task Latency & Performance Degradation"
description: "Diagnose and remediate ECS task latency, EBS throttling, network interface throttling, and slow DNS"
status: active
severity: HIGH
triggers:
  - "latency"
  - "slow"
  - "performance"
  - "response time"
  - "TargetResponseTime"
  - "TTFB"
  - "throttl"
  - "EBS"
  - "network.*throughput"
  - "DNS.*slow"
  - "timeout"
owner: devops-agent
objective: "Identify performance bottlenecks and restore acceptable task latency"
context: "ECS task latency can stem from multiple layers: application code, container resource limits (CPU/memory), EBS volume throttling (IOPS/throughput), network interface throttling (bandwidth/PPS limits), DNS resolution delays, load balancer configuration, or external dependency latency. For EC2 launch type, instance-level metrics (CPU, memory, network, EBS) are critical. For Fargate, container-level CloudWatch Container Insights metrics and network sidecar diagnostics help isolate the bottleneck."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=warning to find performance-related warnings
- Use `search` tool with instanceId and query=`latency|slow|timeout|performance|throttl|response.*time|TTFB` to find evidence

SHOULD:
- Use `search` tool with query=`CPU.*utilization|memory.*utilization|cpu.*throttl|oom` to check resource saturation
- Use `search` tool with query=`EBS.*throttl|VolumeReadOps|VolumeWriteOps|BurstBalance` to check EBS throttling
- Use `network_diagnostics` tool with instanceId to check network performance

MAY:
- Use `search` tool with query=`DNS.*resolution|resolve.*time|nslookup|dig` to check DNS latency
- Use `search` tool with query=`TargetResponseTime|HealthyHostCount|RequestCount` to check ALB metrics

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of performance degradation
- Determine bottleneck layer: CPU, memory, EBS, network, DNS, or application
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`instance.*type|vCPU|network.*bandwidth|baseline` to check instance capabilities
- Use `compare_instances` tool to compare performance across healthy vs degraded instances

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the performance findings
- State root cause: which layer is the bottleneck and specific metric evidence
- Recommend specific remediation to restore performance

SHOULD:
- Include before/after metric comparison if baseline data available
- Recommend right-sizing based on observed resource utilization

## Guardrails

escalation_conditions:
  - "P99 latency exceeding SLA thresholds"
  - "EBS volume consistently throttled"
  - "Network bandwidth saturated on instance"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Task definition resource changes: YELLOW — operator action"
  - "Instance type change: YELLOW — requires rolling replacement"

## Common Issues

- symptoms: "High response times, TargetResponseTime spikes in ALB metrics"
  diagnosis: "Application CPU or memory saturation causing slow request processing"
  resolution: "Increase task CPU/memory limits. Enable Application Auto Scaling. Profile application for hot paths."

- symptoms: "Intermittent latency spikes on EC2 launch type"
  diagnosis: "EBS volume IOPS or throughput throttling — burst credits exhausted"
  resolution: "Upgrade to gp3 volume with provisioned IOPS. Monitor BurstBalance metric. Reduce disk I/O or use instance store."

- symptoms: "Network throughput degradation on EC2 instances"
  diagnosis: "Instance network bandwidth limit reached or network interface PPS throttling"
  resolution: "Use larger instance type with higher network bandwidth. Enable enhanced networking (ENA). Distribute traffic across more instances."

- symptoms: "Slow DNS resolution causing connection timeouts"
  diagnosis: "VPC DNS resolver throttled or DNS cache not configured"
  resolution: "Enable DNS caching in application or use a local DNS cache sidecar. Check Route 53 Resolver query limits. Reduce DNS TTL churn."

- symptoms: "Fargate task latency with no obvious resource saturation"
  diagnosis: "Network interface throttling on Fargate — each task gets a single ENI with bandwidth limits"
  resolution: "Use larger Fargate task size (higher vCPU = higher network bandwidth). Optimize payload sizes. Use connection pooling."
