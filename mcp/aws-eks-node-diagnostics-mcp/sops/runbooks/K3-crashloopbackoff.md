---
title: "K3 — CrashLoopBackOff (General)"
description: "Diagnose containers in CrashLoopBackOff using worker node kubelet and containerd logs to identify exit codes, OOM kills, runtime errors, and application failures"
status: active
severity: HIGH
triggers:
  - "CrashLoopBackOff"
  - "Back-off restarting failed container"
  - "container.*exited with.*code"
  - "exit code [1-9]"
  - "exit code 13[7-9]"
  - "OOMKilled"
owner: devops-agent
objective: "Identify why containers are crash-looping by examining kubelet container lifecycle logs, containerd exit codes, and dmesg OOM evidence, then recommend targeted remediation"
context: >
  CrashLoopBackOff occurs when a container repeatedly crashes and Kubernetes applies exponential
  backoff before restarting it (10s, 20s, 40s, ... up to 5 minutes). Worker node logs are essential
  because: (1) kubelet logs show container exit codes — 137=OOM/SIGKILL, 1=application error,
  139=SIGSEGV/segfault, 126=permission denied, 127=command not found, 143=SIGTERM, (2) dmesg shows
  kernel OOM-killer events with the killed process name and memory stats, (3) containerd logs show
  container start/stop lifecycle and runtime errors. Container-level OOM (cgroup limit exceeded) is
  different from node-level OOM (system memory exhausted) — both cause exit code 137 but have different
  remediation. This SOP focuses on diagnosing the crash reason from node-level logs when pod logs alone
  are insufficient (e.g., container exits before writing logs, OOM kills, segfaults, runtime errors).
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod state and container exit information before any log collection:
  - List pods in the affected namespace: `kubectl get pods -n <namespace> -o wide` (via EKS MCP `list_k8s_resources`) — look for pods with CrashLoopBackOff status and high RESTARTS count
  - Check pod details: `kubectl describe pod <pod>` (via EKS MCP `read_k8s_resource` kind=Pod) — examine containerStatuses:
    - lastState.terminated.reason: "OOMKilled", "Error", "Completed"
    - lastState.terminated.exitCode: the numeric exit code
    - lastState.terminated.startedAt / finishedAt: how quickly the container crashed
    - restartCount: how many times it has restarted
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) — look for "Back-off restarting failed container" and "Created container" events
  - Check pod logs if available: `kubectl logs <pod> --previous` (via EKS MCP `get_pod_logs` with previous=true) — may show application error before crash
  - Identify the node: check spec.nodeName — all further log investigation targets this node
- Use `collect` tool with instanceId to gather logs from the affected node
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId to get pre-indexed findings — look for OOM kills, container exit errors
- Use `search` tool with instanceId and query=`CrashLoopBackOff|Back-off restarting|container.*exited|exit code|ExitCode` and logTypes=`kubelet` to find container crash evidence

SHOULD:
- Use `search` tool with query=`oom-kill|OOMKilled|Out of memory|Memory cgroup out of memory|oom_score` to find OOM evidence in dmesg — this is critical for exit code 137
- Use `search` tool with query=`segfault|SIGSEGV|signal 11|core dumped|trapping fault` to find segfault evidence in dmesg — this is critical for exit code 139
- Use `search` tool with query=`container.*start|container.*create|RunPodSandbox|StartContainer` and logTypes=`containerd` to check container lifecycle in containerd logs

MAY:
- Use `cluster_health` tool with clusterName to check if CrashLoopBackOff is widespread
- Use `quick_triage` tool with instanceId for a fast overview

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`CrashLoopBackOff` to build timeline of crash events
- Classify the crash reason based on exit code from Phase 1:

### Exit Code 137 — OOMKilled / SIGKILL

MUST:
- Use `search` tool with query=`oom-kill.*<container-name>|Memory cgroup out of memory|Killed process.*<container-name>` to find the specific OOM kill event
- Determine OOM level:
  - **Container-level OOM** (cgroup limit): container exceeded its memory limit. The pod spec shows resources.limits.memory. Kubelet logs show "OOMKilled" in container status.
  - **Node-level OOM** (system memory): kernel OOM-killer chose this container. dmesg shows "Out of memory: Killed process" with the process PID and memory stats. Cross-ref G2 SOP.
- Use `search` tool with query=`memory.*limit|resources.*limits.*memory|cgroup.*memory` to check configured memory limits

SHOULD:
- Use `search` tool with query=`MemAvailable|MemFree|MemTotal` to check node memory at time of OOM
- Use `search` tool with query=`system-reserved|kube-reserved` to check if node memory reservation is configured

### Exit Code 1 — Application Error

MUST:
- Use `search` tool with query=`container.*exited.*code.*1|exit.*code.*1` and logTypes=`kubelet` to confirm application error exit
- Application logs (via EKS MCP `get_pod_logs` with previous=true) are the primary source for exit code 1 — node logs provide context but not the application error itself

SHOULD:
- Use `search` tool with query=`configmap.*not found|secret.*not found|volume.*not found|mount.*failed` and logTypes=`kubelet` to check if missing ConfigMap, Secret, or volume is causing the crash
- Use `search` tool with query=`permission denied|access denied|EACCES` to check for filesystem permission issues

### Exit Code 139 — Segfault (SIGSEGV)

MUST:
- Use `search` tool with query=`segfault|SIGSEGV|signal 11|core dumped|trapping fault` in dmesg to find the segfault event with the faulting address and instruction pointer
- Segfaults indicate a bug in the application binary or a library incompatibility

### Exit Code 126/127 — Command Not Found / Permission Denied

MUST:
- Use `search` tool with query=`exec.*not found|command not found|permission denied|exec format error` and logTypes=`containerd` to check for entrypoint/command issues
- Exit code 127 = command/binary not found in the container image
- Exit code 126 = binary found but not executable (permission issue or wrong architecture)

### Other Exit Codes

SHOULD:
- Use `search` tool with query=`exit.*code|ExitCode|terminated.*reason` and logTypes=`kubelet` to find the specific exit code
- Exit code 143 = SIGTERM (graceful shutdown — not a crash, check if something is killing the pod)
- Exit code 255 = unknown error (check containerd logs for runtime errors)

### 2E — Control Plane kube-audit Logs

MAY:
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="CrashLoopBackOff" to check for CrashLoopBackOff events in the audit log
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="OOMKilled" to check for OOM events recorded at the API level

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from crash-related findings
- State root cause with specific evidence:
  - Exit code and its meaning
  - OOM kill evidence (container-level vs node-level) with memory stats
  - Segfault evidence with faulting address
  - Missing resource (ConfigMap, Secret, volume) evidence
  - Application error (from pod logs if available)
- Recommend targeted fix (operator action — not available via MCP tools)

SHOULD:
- Include crash frequency and backoff timing from correlate results
- Include memory limit vs actual usage for OOM cases
- Differentiate between container-level OOM (increase limits) and node-level OOM (increase node size or set system-reserved)

MAY:
- Recommend VPA for automatic memory right-sizing
- Recommend startup probes for slow-starting applications that crash during initialization

## Guardrails

escalation_conditions:
  - "System pods (aws-node, kube-proxy, CoreDNS) in CrashLoopBackOff — cluster-wide impact"
  - "Node-level OOM killing kubelet or containerd — escalate to A1 SOP"
  - "Segfault in system component — possible kernel or runtime bug"
  - "CrashLoopBackOff across multiple pods on multiple nodes — possible cluster-wide issue"

safety_ratings:
  - "Log collection (collect), search, errors, correlate: GREEN (read-only)"
  - "Increase container memory limits: YELLOW — operator action"
  - "Fix application code/config: YELLOW — operator action"
  - "Set system-reserved memory: YELLOW — operator action, requires kubelet restart"

## Common Issues

- symptoms: "read_k8s_resource shows lastState.terminated.reason=OOMKilled, exitCode=137"
  diagnosis: "Container exceeded its cgroup memory limit. The container's memory usage grew beyond resources.limits.memory. This is container-level OOM, not node-level."
  resolution: "Operator action: increase resources.limits.memory in the pod spec. Check if the application has a memory leak. Use VPA to auto-right-size memory limits."

- symptoms: "search returns oom-kill in dmesg with the container process name, but pod spec has no memory limit"
  diagnosis: "Node-level OOM — kernel OOM-killer chose this container because it was the largest memory consumer and no cgroup limit was set. Without limits, the container can consume all available node memory."
  resolution: "Operator action: set resources.limits.memory on the pod. Set system-reserved and kube-reserved in kubelet config to protect system processes. See G2 SOP for node-level OOM."

- symptoms: "read_k8s_resource shows exitCode=1, pod logs show application error"
  diagnosis: "Application crashed with an error. Exit code 1 is a generic application error — the application logs contain the specific error message."
  resolution: "Operator action: review application logs (kubectl logs <pod> --previous). Common causes: missing environment variables, database connection failures, configuration errors, missing ConfigMaps or Secrets."

- symptoms: "search returns segfault in dmesg for the container process"
  diagnosis: "Application binary crashed with a segmentation fault (SIGSEGV). This is a bug in the application code or a library incompatibility (e.g., wrong architecture, glibc version mismatch)."
  resolution: "Operator action: check if the container image architecture matches the node (amd64 vs arm64). Check for library version mismatches. Enable core dumps for debugging. Rebuild the application with address sanitizer for detailed crash info."

- symptoms: "read_k8s_resource shows exitCode=127, containerd logs show exec not found"
  diagnosis: "The container's entrypoint or command binary does not exist in the container image. Common after image rebuild that changed the binary path."
  resolution: "Operator action: verify the Dockerfile ENTRYPOINT/CMD. Check if the binary exists in the image: docker run --rm <image> ls -la /path/to/binary. Fix the image or update the pod spec command."

- symptoms: "read_k8s_resource shows exitCode=126, containerd logs show permission denied or exec format error"
  diagnosis: "The binary exists but cannot be executed. Either it lacks execute permissions, or it is compiled for a different architecture (e.g., arm64 binary on amd64 node)."
  resolution: "Operator action: check binary permissions (chmod +x). Check image architecture matches node architecture. For multi-arch, use docker buildx to build for the correct platform."

- symptoms: "search returns configmap not found or secret not found in kubelet logs, container exits immediately"
  diagnosis: "Container depends on a ConfigMap or Secret that does not exist. Kubelet cannot mount the volume, causing the container to fail at startup."
  resolution: "Operator action: create the missing ConfigMap or Secret. Check if the resource was accidentally deleted. Verify the namespace is correct."

- symptoms: "CrashLoopBackOff on CloudWatch agent or EKS Pod Identity Agent pods"
  diagnosis: "System addon pods crashing due to IAM permission issues, network connectivity to AWS endpoints, or configuration errors. CloudWatch agent needs CloudWatchAgentServerPolicy. Pod Identity Agent needs AssumeRoleForPodIdentity permission."
  resolution: "Operator action: check pod logs for specific error. For CloudWatch agent: verify IAM role has CloudWatchAgentServerPolicy, check VPC endpoint for logs service, verify ConfigMap configuration. For Pod Identity Agent: verify node role has AssumeRoleForPodIdentity permission, check network access to EKS Auth endpoint."

## Examples

```
# Step 1: Check pod state and exit code via EKS MCP
read_k8s_resource(clusterName="my-cluster", kind="Pod", apiVersion="v1", name="crashing-pod", namespace="default")
# Look at: containerStatuses.lastState.terminated.exitCode, reason, restartCount

# Step 2: Check pod events
get_k8s_events(clusterName="my-cluster", kind="Pod", name="crashing-pod", namespace="default")

# Step 3: Get previous pod logs if available
get_pod_logs(clusterName="my-cluster", namespace="default", pod_name="crashing-pod", previous=true)

# Step 4: Collect node logs
collect(instanceId="i-0abc123def456")
status(executionId="<id-from-step-4>")

# Step 5: Check for OOM kills in dmesg
search(instanceId="i-0abc123def456", query="oom-kill|OOMKilled|Out of memory|Memory cgroup")

# Step 6: Check for segfaults
search(instanceId="i-0abc123def456", query="segfault|SIGSEGV|signal 11|core dumped")

# Step 7: Check kubelet container lifecycle
search(instanceId="i-0abc123def456", query="CrashLoopBackOff|Back-off restarting|exit code", logTypes="kubelet")

# Step 8: Correlate crash timeline
correlate(instanceId="i-0abc123def456", pivotEvent="CrashLoopBackOff", timeWindow=300)

# Step 9: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003"])
```

## Output Format

```yaml
root_cause: "<container_oom|node_oom|app_error|segfault|command_not_found|permission_denied|missing_config|runtime_error> — <specific detail>"
exit_code: <number>
exit_code_meaning: "<OOMKilled|ApplicationError|Segfault|CommandNotFound|PermissionDenied|SIGTERM|Unknown>"
crash_frequency: "<restarts in last N minutes from correlate>"
evidence:
  - type: pod_state
    content: "<exitCode, reason, restartCount from read_k8s_resource>"
  - type: dmesg_search
    content: "<OOM kill or segfault evidence from dmesg>"
  - type: kubelet_search
    content: "<container lifecycle logs from kubelet>"
  - type: pod_logs
    content: "<application error from get_pod_logs --previous>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix based on exit code and root cause>"
  long_term: "Set memory limits, fix application bugs, use VPA for right-sizing"
cross_reference:
  - "G2 if node-level OOM (not just container-level)"
  - "A1 if kubelet itself is OOM-killed"
  - "K2 if probe failures are causing the restarts (not application crashes)"
```
