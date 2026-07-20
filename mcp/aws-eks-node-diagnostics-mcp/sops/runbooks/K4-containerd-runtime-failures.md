---
title: "K4 — Containerd / Container Runtime Failures"
description: "Diagnose pod failures caused by containerd crashes, shim errors, and container runtime issues visible in worker node containerd and kubelet logs"
status: active
severity: CRITICAL
triggers:
  - "containerd.*error"
  - "containerd.*not running"
  - "containerd.*dead"
  - "shim.*error"
  - "shim.*died"
  - "runtime.*not ready"
  - "container runtime.*down"
  - "RunPodSandbox.*error"
  - "rpc error.*containerd"
owner: devops-agent
objective: "Identify containerd runtime failures from worker node logs, determine if the runtime is crashed/overloaded/misconfigured, and recommend recovery steps"
context: >
  Containerd is the container runtime on EKS worker nodes (since EKS 1.24+, Docker/dockershim was removed).
  Kubelet communicates with containerd via CRI (Container Runtime Interface) over a Unix socket. When
  containerd fails, ALL container operations on the node stop — no new pods can start, running pods cannot
  be stopped, and PLEG becomes unhealthy (causing NotReady). Containerd failures manifest as: (1) containerd
  service crash/restart — visible in systemd logs, (2) shim process errors — each container has a
  containerd-shim-runc-v2 process that can crash independently, (3) CRI socket unavailable — kubelet
  cannot reach containerd, (4) containerd overload — too many concurrent operations causing timeouts,
  (5) containerd config errors — invalid config.toml or registry mirror issues. Worker node logs are
  the ONLY way to diagnose these issues — they are not visible from kubectl or the Kubernetes API.
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check node and pod state before any log collection:
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — containerd failures cause NotReady state. Check node status.conditions for "ContainerRuntimeNotReady" or "KubeletNotReady"
  - Check node details: `kubectl describe node <node>` (via EKS MCP `read_k8s_resource` kind=Node) — look at Conditions and node info (containerRuntimeVersion should show containerd://X.Y.Z)
  - List pods on the affected node: `kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>` (via EKS MCP `list_k8s_resources` with field_selector) — check for pods in ContainerCreating, Unknown, or Error state
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) for runtime-related errors
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- **PREREQUISITE — Is containerd running?** This is the fundamental check:
  - Use `search` tool with instanceId and query=`Active: active \(running\)|containerd.*started|containerd.service.*running` and logTypes=`containerd` — if NO matches, containerd is stopped/dead. That IS the root cause.
  - Use `search` tool with instanceId and query=`Active: inactive|Active: failed|containerd.service.*dead|containerd.service.*failed` — if matches found, containerd is stopped. Check WHY it stopped.
  - Use `search` tool with query=`containerd.*panic|containerd.*fatal|containerd.*segfault|containerd.*signal` in dmesg and containerd logs to find crash evidence
  - ONLY if containerd is confirmed running, proceed to investigate runtime errors below.
- Use `errors` tool with instanceId to get pre-indexed findings — look for containerd/runtime errors
- Use `search` tool with instanceId and query=`containerd.*error|containerd.*fail|rpc error.*containerd|runtime.*not ready` and logTypes=`containerd,kubelet` to find runtime failure evidence

SHOULD:
- Use `search` tool with query=`shim.*error|shim.*died|shim.*exit|containerd-shim-runc-v2.*error` to find shim-level errors
- Use `search` tool with query=`PLEG.*not healthy|PLEG.*relisting` and logTypes=`kubelet` to check if PLEG is unhealthy due to containerd issues (cross-ref B3 SOP)

MAY:
- Use `cluster_health` tool with clusterName to check if runtime failures affect multiple nodes
- Use `search` tool with query=`containerd.*version|runc.*version` to check runtime versions

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`containerd` to build timeline of runtime failures
- Classify the failure type:

### 2A — Containerd Service Crash

MUST:
- Use `search` tool with query=`containerd.service.*failed|containerd.*exit.*status|containerd.*restart|systemd.*containerd.*stop` to find service lifecycle events
- Use `search` tool with query=`containerd.*panic|containerd.*fatal|containerd.*SIGSEGV|containerd.*SIGABRT` to find crash signatures
- Use `search` tool with query=`oom-kill.*containerd|Out of memory.*containerd` in dmesg to check if containerd was OOM-killed
  - If containerd was OOM-killed: node-level memory exhaustion is the root cause (cross-ref A1/G2 SOP)

SHOULD:
- Use `search` tool with query=`containerd.*core dump|containerd.*backtrace` to find crash dump evidence
- Use `search` tool with query=`containerd.*config|config.toml|registry.*mirror|sandbox_image` to check for configuration issues that may cause crashes

### 2B — Shim Process Errors

MUST:
- Use `search` tool with query=`shim.*died|shim.*error|shim.*exit|shim.*not running` to find shim failures
  - Each container has its own shim process (containerd-shim-runc-v2)
  - Shim death kills the specific container but does not affect other containers
- Use `search` tool with query=`runc.*error|runc.*failed|runc.*exec` to check for runc (low-level runtime) errors

SHOULD:
- Use `search` tool with query=`cgroup.*error|cgroup.*failed|cgroup.*denied` to check for cgroup-related errors
  - cgroup v2 migration issues can cause shim failures on newer AMIs
- Use `search` tool with query=`seccomp.*error|apparmor.*error|selinux.*error` to check for security profile issues blocking container operations

### 2C — CRI Socket / Communication Errors

MUST:
- Use `search` tool with query=`containerd.sock.*error|containerd.sock.*refused|dial.*containerd|CRI.*error|CRI.*unavailable` and logTypes=`kubelet` to find CRI communication failures
  - "connection refused" = containerd is not running or socket is not created
  - "context deadline exceeded" = containerd is overloaded and not responding in time
- Use `search` tool with query=`runtime.*not ready|container runtime.*not running|RuntimeReady.*false` and logTypes=`kubelet` to check kubelet's view of runtime readiness

### 2D — Containerd Overload

SHOULD:
- Use `search` tool with query=`containerd.*timeout|containerd.*deadline|containerd.*slow|too many` to check for overload symptoms
- Use `search` tool with query=`container.*count|running.*containers|pod.*count` to estimate container density
  - High container density (>100 containers) can overwhelm containerd
- Use `storage_diagnostics` tool with instanceId to check disk I/O — slow disk causes containerd to stall on image layer operations

### 2E — Control Plane kube-audit Logs

MAY:
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="NotReady" to check for node NotReady events in the audit log that correlate with containerd failures
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="runtime" to check for runtime-related API events

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from runtime-related findings
- State root cause with specific evidence:
  - Containerd crash: crash signature, OOM evidence, or config error
  - Shim failure: specific container affected, runc error
  - CRI socket: communication failure type
  - Overload: container density, disk I/O evidence
- Recommend recovery steps (operator action — not available via MCP tools)
- Note blast radius: containerd crash affects ALL pods on the node

SHOULD:
- Include containerd version and configuration from search results
- Include PLEG status if affected (cross-ref B3)
- Include node conditions showing NotReady

MAY:
- Recommend containerd version upgrade if known buggy version
- Recommend monitoring containerd health via node-level metrics

## Guardrails

escalation_conditions:
  - "Containerd crashes repeatedly after restart — possible kernel or hardware issue"
  - "Containerd OOM-killed — node-level memory exhaustion (escalate to A1/G2)"
  - "Multiple nodes with containerd failures — possible AMI or cluster-wide issue"
  - "Containerd config.toml corrupted — requires node replacement or manual fix"

safety_ratings:
  - "Log collection (collect), search, errors, correlate, storage_diagnostics: GREEN (read-only)"
  - "Restart containerd: YELLOW — operator action, briefly disrupts all containers on node"
  - "Drain node: YELLOW — operator action, moves all pods to other nodes"
  - "Terminate and replace node: RED — operator action, requires approval"

## Common Issues

- symptoms: "search returns containerd.service failed or Active: inactive, no OOM evidence in dmesg"
  diagnosis: "Containerd service stopped without OOM. Could be a crash, config error, or manual stop. Check containerd logs for panic/fatal messages."
  resolution: "Operator action: check journalctl -u containerd for the crash reason. Fix any config issues in /etc/containerd/config.toml. Restart containerd: systemctl restart containerd. If it crashes again, check for kernel bugs or hardware issues."

- symptoms: "search returns oom-kill containerd in dmesg"
  diagnosis: "Containerd was OOM-killed by the kernel. Node-level memory exhaustion is the root cause. All containers on the node are affected."
  resolution: "Operator action: restart containerd after memory pressure resolves. Set system-reserved memory in kubelet config to protect system processes. Consider larger instance type. See A1/G2 SOP for memory investigation."

- symptoms: "search returns shim died or shim error for specific containers"
  diagnosis: "Container shim process crashed. Only the specific container is affected, not the entire runtime. Often caused by runc errors, cgroup issues, or resource limits."
  resolution: "Operator action: delete and recreate the affected pod. If persistent, check runc version compatibility. Check for cgroup v2 issues on newer AMIs."

- symptoms: "search returns containerd.sock connection refused in kubelet logs"
  diagnosis: "Kubelet cannot reach containerd via the CRI socket. Containerd is either not running or the socket file does not exist."
  resolution: "Operator action: check if containerd is running (systemctl status containerd). If stopped, restart it. If the socket is missing, check containerd config for the socket path."

- symptoms: "search returns containerd timeout or deadline exceeded, high container count on node"
  diagnosis: "Containerd is overloaded by high container density. Too many concurrent container operations are causing timeouts."
  resolution: "Operator action: reduce pod count on the node (drain some pods). Check disk I/O — slow EBS volumes cause containerd to stall. Consider larger instance type with faster storage."

- symptoms: "search returns PLEG not healthy alongside containerd errors"
  diagnosis: "PLEG is unhealthy because containerd is slow or unresponsive. PLEG relists containers via containerd — if containerd is slow, PLEG times out. Containerd is the root cause, not PLEG."
  resolution: "Operator action: fix the containerd issue first (restart, reduce load, fix config). PLEG will recover once containerd is responsive. See B3 SOP for PLEG-specific investigation."

- symptoms: "search returns containerd config error or registry mirror error"
  diagnosis: "Containerd configuration in /etc/containerd/config.toml is invalid. This can happen after AMI updates, manual edits, or bootstrap script errors."
  resolution: "Operator action: validate config.toml syntax. Check registry mirror configuration. Restore default config if needed. Restart containerd after fixing config."

- symptoms: "search returns runc error or exec format error in containerd logs"
  diagnosis: "Low-level runtime (runc) error. Could be architecture mismatch (arm64 image on amd64 node), corrupted binary, or kernel incompatibility."
  resolution: "Operator action: check node architecture vs container image architecture. Update runc if outdated. Check kernel version compatibility with the containerd/runc version."

## Examples

```
# Step 1: Check node state via EKS MCP
list_k8s_resources(clusterName="my-cluster", kind="Node", apiVersion="v1")
read_k8s_resource(clusterName="my-cluster", kind="Node", apiVersion="v1", name="affected-node")

# Step 2: Collect node logs
collect(instanceId="i-0abc123def456")
status(executionId="<id-from-step-2>")

# Step 3: Check containerd service status
search(instanceId="i-0abc123def456", query="Active: active.*containerd|Active: inactive.*containerd|containerd.service.*failed", logTypes="containerd")

# Step 4: Check for containerd crashes
search(instanceId="i-0abc123def456", query="containerd.*panic|containerd.*fatal|containerd.*segfault|oom-kill.*containerd")

# Step 5: Check for shim errors
search(instanceId="i-0abc123def456", query="shim.*died|shim.*error|runc.*error")

# Step 6: Check CRI communication
search(instanceId="i-0abc123def456", query="containerd.sock.*error|CRI.*error|runtime.*not ready", logTypes="kubelet")

# Step 7: Check PLEG impact
search(instanceId="i-0abc123def456", query="PLEG.*not healthy|PLEG.*relisting", logTypes="kubelet")

# Step 8: Correlate timeline
correlate(instanceId="i-0abc123def456", pivotEvent="containerd", timeWindow=300)

# Step 9: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003"])
```

## Output Format

```yaml
root_cause: "<containerd_crash|containerd_oom|shim_failure|cri_socket|containerd_overload|config_error|runc_error> — <specific detail>"
containerd_status: "<running|stopped|crashed|overloaded>"
evidence:
  - type: containerd_logs
    content: "<crash/error evidence from containerd logs>"
  - type: dmesg
    content: "<OOM kill or panic evidence from dmesg>"
  - type: kubelet_logs
    content: "<CRI communication errors from kubelet>"
  - type: correlate
    content: "<failure timeline>"
blast_radius: "All pods on node <node-name> affected (containerd crash) OR specific container only (shim failure)"
severity: CRITICAL
mitigation:
  immediate: "Operator: restart containerd (systemctl restart containerd), or drain and replace node"
  long_term: "Set system-reserved memory, monitor containerd health, upgrade containerd version"
cross_reference:
  - "B3 if PLEG unhealthy as secondary symptom"
  - "A1/G2 if containerd OOM-killed (memory root cause)"
  - "C2 if sandbox creation fails after containerd recovery"
```
