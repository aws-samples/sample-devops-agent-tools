---
title: "K2 — Liveness/Readiness Probe Failures"
description: "Diagnose pod restarts and not-ready state caused by liveness and readiness probe failures, using worker node kubelet logs to trace probe execution, timeouts, and VPC CNI interactions"
status: active
severity: HIGH
triggers:
  - "Liveness probe failed"
  - "Readiness probe failed"
  - "Startup probe failed"
  - "probe failed.*context deadline exceeded"
  - "probe failed.*connection refused"
  - "probe failed.*statuscode: 5"
  - "Unhealthy.*probe"
owner: devops-agent
objective: "Identify why probes are failing by examining kubelet probe execution logs, VPC CNI configuration, and application health, then recommend targeted remediation"
context: >
  Kubelet executes liveness, readiness, and startup probes to monitor pod health. Liveness probe
  failures cause container restarts (CrashLoopBackOff). Readiness probe failures remove the pod from
  Service endpoints (not ready to serve traffic). Startup probe failures prevent liveness/readiness
  probes from starting. Common causes visible in worker node logs: (1) application not listening on
  the probe port — connection refused, (2) application responding too slowly — context deadline exceeded,
  (3) VPC CNI Security Groups for Pods blocking probe traffic when DISABLE_TCP_EARLY_DEMUX is not set,
  (4) application returning HTTP 5xx — health endpoint failing, (5) exec probes timing out due to
  resource pressure on the node. Kubelet logs show every probe attempt with result and timing.
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod state and probe configuration before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`) — look for pods with high RESTARTS count (liveness failures) or 0/1 READY (readiness failures)
  - Check pod details: `kubectl describe pod <pod>` (via EKS MCP `read_k8s_resource` kind=Pod) — examine probe configuration (httpGet/exec/tcpSocket, port, path, timeoutSeconds, periodSeconds, failureThreshold, initialDelaySeconds)
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) — look for "Unhealthy" events with probe type (Liveness/Readiness/Startup) and failure message
  - Check container last state: look at containerStatuses.lastState.terminated.reason — "OOMKilled" means the container ran out of memory, not a probe issue
  - Identify the node: check spec.nodeName — all further log investigation targets this node
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for probe failure patterns
- Use `search` tool with instanceId and query=`Liveness probe failed|Readiness probe failed|Startup probe failed|Unhealthy.*probe` and logTypes=`kubelet` to find probe failure evidence with timestamps and error details

SHOULD:
- Use `search` tool with query=`context deadline exceeded|Client.Timeout|connection refused|connection timed out` and logTypes=`kubelet` to classify the probe failure type:
  - "context deadline exceeded" = probe timed out (application too slow or network issue)
  - "connection refused" = application not listening on the probe port
  - "connection timed out" = network path blocked (security groups, iptables)
  - "statuscode: 5xx" = application health endpoint returning error
- Use `search` tool with query=`DISABLE_TCP_EARLY_DEMUX|POD_SECURITY_GROUP_ENFORCING_MODE|ENABLE_POD_ENI|SecurityGroupsForPods` to check VPC CNI security group configuration — this is the most common cause of probe timeouts on EKS

MAY:
- Use `cluster_health` tool with clusterName to check if probe failures are widespread
- Use `quick_triage` tool with instanceId for a fast overview

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`probe failed` to build timeline of probe failures and correlate with other node events
- Classify the root cause:

### 2A — VPC CNI / Security Groups for Pods (Most Common on EKS)

MUST:
- Use `search` tool with query=`DISABLE_TCP_EARLY_DEMUX|POD_SECURITY_GROUP_ENFORCING_MODE` to check VPC CNI configuration:
  - If POD_SECURITY_GROUP_ENFORCING_MODE=strict AND DISABLE_TCP_EARLY_DEMUX is NOT true: this is the root cause. Kubelet probe traffic cannot reach pods with security groups in strict mode.
  - If using VPC CNI < v1.11.0 AND ENABLE_POD_ENI=true AND DISABLE_TCP_EARLY_DEMUX is NOT true: this is the root cause.
- Use `search` tool with query=`aws-node.*version|vpc-cni.*version|VPC_CNI_VERSION` to check VPC CNI version

SHOULD:
- Use `network_diagnostics` tool with instanceId and sections=cni,iptables to check for network-level probe blocking
- Use `search` tool with query=`security.*group.*pod|branch.*ENI|trunk.*ENI` to check if Security Groups for Pods is enabled

### 2B — Application Health Issues

MUST:
- Use `search` tool with query=`probe failed.*connection refused` and logTypes=`kubelet` — application not listening on the configured port
  - Check if the probe port matches the application's actual listening port
  - Check if the application has a slow startup (needs startup probe or higher initialDelaySeconds)
- Use `search` tool with query=`probe failed.*statuscode: [45]` and logTypes=`kubelet` — application health endpoint returning errors
  - HTTP 5xx = application internal error
  - HTTP 4xx = wrong path or authentication required

SHOULD:
- Use `search` tool with query=`OCI runtime exec failed|exec.*failed|resource temporarily unavailable` and logTypes=`kubelet` — exec probe failures due to runtime issues
  - "resource temporarily unavailable" = PID exhaustion or fork limit on the node
- Use `search` tool with query=`oom-kill|OOMKilled|Out of memory` to check if the container is being OOM-killed (causing probe failures as a secondary symptom)

### 2C — Node Resource Pressure

SHOULD:
- Use `search` tool with query=`PLEG.*not healthy|PLEG.*relisting` to check if PLEG issues are causing probe execution delays (cross-ref B3 SOP)
- Use `search` tool with query=`disk.*pressure|memory.*pressure|PID.*pressure` to check if node resource pressure is slowing probe execution
- Use `storage_diagnostics` tool with instanceId to check disk I/O latency — slow disk causes kubelet to stall on probe execution

### 2D — Control Plane kube-audit Logs

MAY:
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="Unhealthy" to check for probe failure events recorded in the audit log
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="SecurityGroupPolicy" to check for recent SecurityGroupPolicy changes that may have triggered probe failures

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from probe-related findings
- State root cause with specific evidence:
  - VPC CNI misconfiguration: DISABLE_TCP_EARLY_DEMUX not set with SGP
  - Application issue: not listening, slow response, health endpoint error
  - Node pressure: PLEG, disk I/O, memory pressure causing probe delays
  - Exec probe failure: runtime resource exhaustion
- Recommend targeted fix (operator action — not available via MCP tools)

SHOULD:
- Include probe configuration from pod spec (type, port, path, timeouts)
- Include failure frequency and pattern from correlate results
- Differentiate between liveness (causes restarts) and readiness (causes traffic removal) impact

MAY:
- Recommend probe tuning: increase timeoutSeconds, increase initialDelaySeconds for slow-starting apps, add startup probes
- Recommend increasing kubelet verbosity (--v=9) for detailed probe debugging

## Guardrails

escalation_conditions:
  - "Probe failures causing CrashLoopBackOff on critical system pods (aws-node, kube-proxy, CoreDNS)"
  - "VPC CNI aws-node DaemonSet itself failing probes — cluster-wide networking impact"
  - "Probe failures across multiple pods on multiple nodes — possible cluster-wide issue"
  - "PLEG unhealthy causing cascading probe failures — escalate to B3 SOP"

safety_ratings:
  - "Log collection (collect), search, errors, correlate, network_diagnostics: GREEN (read-only)"
  - "Modify probe configuration: YELLOW — operator action, affects pod lifecycle"
  - "Set DISABLE_TCP_EARLY_DEMUX: YELLOW — operator action, restarts aws-node pods"
  - "Increase kubelet verbosity: YELLOW — operator action, increases log volume"

## Common Issues

- symptoms: "search returns probe failed with context deadline exceeded, and DISABLE_TCP_EARLY_DEMUX is not set to true"
  diagnosis: "VPC CNI Security Groups for Pods is blocking kubelet probe traffic. When POD_SECURITY_GROUP_ENFORCING_MODE is strict (or ENABLE_POD_ENI=true on older CNI versions), DISABLE_TCP_EARLY_DEMUX must be set to true for probes to work."
  resolution: "Operator action: set DISABLE_TCP_EARLY_DEMUX=true on the aws-node DaemonSet init container: kubectl set env daemonset aws-node -n kube-system POD_SECURITY_GROUP_ENFORCING_MODE=standard. Or patch the init container: kubectl patch daemonset aws-node -n kube-system -p '{\"spec\":{\"template\":{\"spec\":{\"initContainers\":[{\"env\":[{\"name\":\"DISABLE_TCP_EARLY_DEMUX\",\"value\":\"true\"}],\"name\":\"aws-vpc-cni-init\"}]}}}}'"

- symptoms: "search returns probe failed with connection refused on the probe port"
  diagnosis: "Application is not listening on the configured probe port. Either the application has not started yet (needs higher initialDelaySeconds or a startup probe), or the port in the probe spec does not match the application's listening port."
  resolution: "Operator action: verify the probe port matches the application's listening port. If the app is slow to start, add a startup probe or increase initialDelaySeconds. Check application logs for startup errors."

- symptoms: "search returns probe failed with HTTP statuscode 500 or 503"
  diagnosis: "Application health endpoint is returning an error. The application is running but reporting unhealthy — could be a dependency failure (database, cache, external service)."
  resolution: "Operator action: check application logs for the health endpoint error. Review what the health check validates — if it checks external dependencies, consider making the readiness probe check dependencies and the liveness probe only check the process is alive."

- symptoms: "search returns OCI runtime exec failed or resource temporarily unavailable for exec probes"
  diagnosis: "Exec probe cannot run because the node is under PID pressure or the container runtime cannot fork a new process. This is a node-level resource issue, not an application issue."
  resolution: "Operator action: check PID usage on the node (see G3 SOP). Increase PID limits. Consider switching from exec probes to HTTP or TCP probes which don't require forking a process inside the container."

- symptoms: "search returns aws-node DaemonSet failing liveness probes with OCI runtime exec failed"
  diagnosis: "VPC CNI aws-node pod is failing its own liveness probe. The default timeoutSeconds may be too low for the node's current load. This causes aws-node restarts, disrupting pod networking."
  resolution: "Operator action: increase the liveness probe timeoutSeconds to 60 on the aws-node DaemonSet. Check node resource pressure — high CPU or memory can cause probe timeouts."

- symptoms: "search returns PLEG not healthy alongside probe failures"
  diagnosis: "PLEG (Pod Lifecycle Event Generator) is unhealthy, causing kubelet to stall on all pod operations including probe execution. Probe failures are a secondary symptom — PLEG is the root cause."
  resolution: "Operator action: investigate PLEG root cause (see B3 SOP). Common causes: containerd overload, high pod density, slow disk I/O. Probe failures will resolve once PLEG is healthy."

- symptoms: "probe failures only on pods with security groups, other pods on the same node are fine"
  diagnosis: "Security Groups for Pods is enabled and probe traffic is being blocked by the pod's security group or by the TCP early demux issue."
  resolution: "Operator action: verify the pod's security group allows traffic from the node's primary ENI IP on the probe port. Set DISABLE_TCP_EARLY_DEMUX=true if not already set."

## Examples

```
# Step 1: Check pod state and probe config via EKS MCP
read_k8s_resource(clusterName="my-cluster", kind="Pod", apiVersion="v1", name="failing-pod", namespace="default")
# Look at: spec.containers[].livenessProbe, readinessProbe, startupProbe

# Step 2: Check pod events for probe failures
get_k8s_events(clusterName="my-cluster", kind="Pod", name="failing-pod", namespace="default")

# Step 3: Collect node logs
collect(instanceId="i-0abc123def456")
status(executionId="<id-from-step-3>")

# Step 4: Search for probe failures in kubelet logs
search(instanceId="i-0abc123def456", query="Liveness probe failed|Readiness probe failed|Unhealthy.*probe", logTypes="kubelet")

# Step 5: Check VPC CNI security group config
search(instanceId="i-0abc123def456", query="DISABLE_TCP_EARLY_DEMUX|POD_SECURITY_GROUP_ENFORCING_MODE|ENABLE_POD_ENI")

# Step 6: Check for node resource pressure
search(instanceId="i-0abc123def456", query="PLEG.*not healthy|disk.*pressure|memory.*pressure")

# Step 7: Correlate probe failures with other events
correlate(instanceId="i-0abc123def456", pivotEvent="probe failed", timeWindow=300)

# Step 8: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003"])
```

## Output Format

```yaml
root_cause: "<vpc_cni_sgp|app_not_listening|app_health_error|exec_probe_resource|node_pressure_pleg|probe_timeout> — <specific detail>"
probe_type: "<liveness|readiness|startup>"
probe_config:
  type: "<httpGet|exec|tcpSocket|grpc>"
  port: "<port>"
  path: "<path if httpGet>"
  timeoutSeconds: "<value>"
  periodSeconds: "<value>"
  failureThreshold: "<value>"
failure_pattern: "<connection_refused|context_deadline_exceeded|statuscode_5xx|exec_failed>"
evidence:
  - type: kubelet_search
    content: "<probe failure logs with timestamps>"
  - type: vpc_cni_config
    content: "<DISABLE_TCP_EARLY_DEMUX, POD_SECURITY_GROUP_ENFORCING_MODE values>"
  - type: correlate
    content: "<probe failure timeline>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix based on root cause>"
  long_term: "Tune probe timeouts, add startup probes for slow apps, set DISABLE_TCP_EARLY_DEMUX if using SGP"
cross_reference:
  - "B3 if PLEG unhealthy causing probe delays"
  - "G3 if PID pressure causing exec probe failures"
  - "D9 if network connectivity blocking probe traffic"
```
