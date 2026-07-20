---
title: "K1 — Stuck / Terminating Pods"
description: "Diagnose pods stuck in Terminating state using worker node kubelet and containerd logs to identify finalizers, PreStop hook failures, and container stop failures"
status: active
severity: HIGH
triggers:
  - "pod.*Terminating"
  - "stuck.*Terminating"
  - "cannot delete pod"
  - "finalizer.*blocking"
  - "graceful deletion.*timeout"
  - "PreStop.*failed"
  - "killing pod.*timeout"
owner: devops-agent
objective: "Identify why pods are stuck in Terminating state by examining kubelet termination logs, containerd container stop logs, and finalizer state, then recommend targeted remediation"
context: >
  When a pod is deleted, Kubernetes sends SIGTERM to containers, runs PreStop hooks, waits for
  terminationGracePeriodSeconds, then sends SIGKILL. Pods can get stuck in Terminating if: (1) finalizers
  block deletion, (2) PreStop hooks hang or fail, (3) containerd cannot stop the container process,
  (4) kubelet loses contact with the API server and cannot update pod status, (5) the node is under
  resource pressure causing kubelet to stall. Worker node logs are essential because kubelet logs show
  the termination sequence, containerd logs show container stop operations, and dmesg shows if the
  container process was OOM-killed during shutdown. Namespaces can also get stuck in Terminating if
  they contain resources with finalizers or if API services report False status.
---

## Phase 1 — Triage

MUST:
- **FIRST**: Check pod and namespace state before any log collection:
  - List terminating pods: `kubectl get pods -A --field-selector=status.phase==Terminating` (via EKS MCP `list_k8s_resources` with field_selector=status.phase=Running, then filter for Terminating in results — or list all pods and check status)
  - Check pod details: `kubectl describe pod <pod>` (via EKS MCP `read_k8s_resource` kind=Pod) — look at metadata.finalizers, metadata.deletionTimestamp, spec.terminationGracePeriodSeconds, and container lastState
  - Check pod events: `kubectl describe pod <pod>` (via EKS MCP `get_k8s_events`) — look for "Killing" events with timestamps to see how long termination has been in progress
  - Identify the node: check spec.nodeName from the pod spec — all further log investigation targets this node
  - Check node conditions: `kubectl get nodes` (via EKS MCP `list_k8s_resources` kind=Node) — if the node is NotReady, kubelet cannot update pod status, causing pods to appear stuck
- **PREREQUISITE — Is kubelet running on the node?** Kubelet must be running to process pod termination:
  - Use `collect` tool with instanceId to gather logs from the affected node
  - Use `status` tool with executionId to poll until collection completes
  - Use `search` tool with instanceId and query=`Active: active \(running\)|kubelet.*started|kubelet.service.*running` and logTypes=`kubelet` — if NO matches, kubelet is stopped. That is the root cause — kubelet cannot process termination when it is not running.
  - Use `search` tool with instanceId and query=`Active: inactive|Active: failed|kubelet.service.*dead` — if matches found, report "kubelet not running — pod termination cannot proceed" as root cause.
  - ONLY if kubelet is confirmed running, proceed to termination investigation below.
- Use `errors` tool with instanceId to get pre-indexed findings — look for termination-related errors
- Use `search` tool with instanceId and query=`Killing|killing pod|SyncLoop.*DELETE|graceful.*delete|termination.*grace` and logTypes=`kubelet` to find kubelet termination sequence logs

SHOULD:
- Use `search` tool with query=`PreStop|preStop|pre-stop|lifecycle.*hook` and logTypes=`kubelet` to check for PreStop hook execution and failures
- Use `search` tool with query=`container.*stop|container.*kill|StopContainer|KillContainer` and logTypes=`containerd` to check if containerd is having trouble stopping the container
- Use `search` tool with query=`finalizer|Finalizer|metadata.*finalizers` to check for finalizer-related messages in kubelet logs

MAY:
- Use `cluster_health` tool with clusterName to check if stuck pods are widespread
- Use `search` tool with query=`orphan|orphaned pod|cleanup` to check for orphaned pod cleanup issues

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId and pivotEvent=`Killing` to build timeline around the termination attempt
- Classify the stuck reason from Phase 1 findings:
  - **Finalizers blocking**: pod has metadata.finalizers that are not being removed by their controller
  - **PreStop hook hanging**: kubelet logs show PreStop hook started but not completed within terminationGracePeriodSeconds
  - **Container stop failure**: containerd logs show errors stopping the container process (process not responding to SIGTERM/SIGKILL)
  - **Kubelet-API server disconnect**: kubelet cannot update pod status — node may be NotReady or network partitioned
  - **Resource pressure**: node under memory/disk/PID pressure causing kubelet to stall on termination operations
- Use `search` tool with query=`SIGTERM|SIGKILL|signal.*kill|signal.*term` to trace the signal delivery sequence
- Use `search` tool with query=`oom-kill|OOMKilled|Out of memory` in dmesg to check if the container process was OOM-killed during shutdown

SHOULD:
- Use `search` tool with query=`volume.*detach|volume.*unmount|PersistentVolume|pv.*finalizer` to check if volume cleanup is blocking termination
- Use `search` tool with query=`endpoint.*remove|service.*endpoint|EndpointSlice` to check if endpoint removal is stalled
- Use `search` tool with query=`api.*server.*unreachable|connection.*refused.*6443|TLS.*handshake.*timeout` to check kubelet-to-API-server connectivity

MAY:
- Use `compare_nodes` tool to check if termination issues are node-specific
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="delete" to check kube-audit logs for delete operations and any API-level errors blocking deletion
- Use EKS MCP `get_cloudwatch_logs` with clusterName, resource_type="cluster", log_type="control-plane", filter_pattern="finalizer" to check for finalizer-related API mutations

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from termination-related findings
- State root cause with specific evidence:
  - Finalizer name and which controller owns it
  - PreStop hook command and timeout evidence
  - Container stop failure with containerd error
  - Kubelet connectivity issue with API server error
- Recommend targeted fix (operator action — not available via MCP tools)

SHOULD:
- Include the termination timeline from correlate results
- Include the terminationGracePeriodSeconds value and how long the pod has been stuck

MAY:
- Recommend adjusting terminationGracePeriodSeconds if PreStop hooks need more time
- Recommend reviewing finalizer controllers for reliability

## Guardrails

escalation_conditions:
  - "Multiple pods stuck in Terminating across different nodes — possible API server issue"
  - "Namespace stuck in Terminating with NamespaceContentRemaining or NamespaceFinalizersRemaining"
  - "Kubelet not running on the node — pod termination cannot proceed"
  - "Force delete needed — risk of data loss or corruption"

safety_ratings:
  - "Log collection (collect), search, errors, correlate: GREEN (read-only)"
  - "Patch pod to remove finalizers: YELLOW — operator action, may skip cleanup"
  - "Force delete pod (--grace-period=0 --force): RED — operator action, risk of data loss"
  - "Restart kubelet: YELLOW — operator action, disrupts all pods on node"

## Common Issues

- symptoms: "read_k8s_resource shows pod has metadata.finalizers list with entries, deletionTimestamp is set"
  diagnosis: "Finalizers are blocking pod deletion. The controller responsible for the finalizer has not removed it — either the controller is not running, or it encountered an error during cleanup."
  resolution: "Operator action: identify the finalizer controller (e.g., volume controller, custom operator). If the controller is not running, restart it. If cleanup is genuinely complete, patch the pod to remove finalizers: kubectl patch pod <pod> -n <ns> -p '{\"metadata\":{\"finalizers\":null}}'"

- symptoms: "search returns PreStop hook started but no completion, pod stuck for longer than terminationGracePeriodSeconds"
  diagnosis: "PreStop hook is hanging. The hook command is not completing within the grace period. Kubelet waits for the hook before sending SIGTERM to the main container."
  resolution: "Operator action: review the PreStop hook command — ensure it completes quickly. Increase terminationGracePeriodSeconds if the hook legitimately needs more time. Force delete the pod if the hook is stuck: kubectl delete pod <pod> -n <ns> --grace-period=0 --force"

- symptoms: "search returns container stop errors in containerd logs, SIGTERM sent but process not exiting"
  diagnosis: "Container process is not responding to SIGTERM. This can happen if the process traps SIGTERM but hangs during cleanup, or if the process is a zombie."
  resolution: "Operator action: ensure the application handles SIGTERM properly. If stuck, force delete the pod. If containerd itself is stuck, restart containerd on the node (systemctl restart containerd)."

- symptoms: "search returns api server unreachable or connection refused to port 6443 in kubelet logs"
  diagnosis: "Kubelet cannot reach the API server to update pod status. The pod may have been terminated locally but the API server still shows it as Terminating."
  resolution: "Operator action: check node network connectivity to the API server endpoint. Check security groups allow outbound to port 443/6443. If the node is network-partitioned, the pod will be cleaned up once connectivity is restored."

- symptoms: "node is NotReady and all pods on it show as Terminating"
  diagnosis: "Node has lost heartbeat. Kubelet is either stopped or the node is unreachable. All pods are marked Terminating by the node controller after the pod-eviction-timeout."
  resolution: "Operator action: check if the node is reachable (SSH/SSM). If kubelet is stopped, restart it. If the node is unreachable, terminate and replace it. Pods will be rescheduled by their controllers."

- symptoms: "namespace stuck in Terminating, kubectl describe namespace shows NamespaceContentRemaining"
  diagnosis: "Resources remain in the namespace that Kubernetes cannot delete. Often caused by CRDs with finalizers or webhook failures blocking deletion."
  resolution: "Operator action: list remaining resources (kubectl api-resources --verbs=list --namespaced -o name | xargs -n 1 kubectl get --show-kind --ignore-not-found -n <ns>). Delete remaining resources. If CRDs have finalizers, patch them to remove finalizers. As last resort: kubectl patch namespace <ns> --type=json -p '[{\"op\": \"remove\", \"path\": \"/metadata/finalizers\"}]'"

- symptoms: "namespace stuck in Terminating, kubectl describe namespace shows API service with False status"
  diagnosis: "An API service (e.g., metrics-server, custom API aggregation) is reporting False status. Kubernetes cannot verify that all resources in the namespace are deleted when API services are unavailable."
  resolution: "Operator action: check API service status (kubectl get apiservices | grep False). Fix or delete the unavailable API service. The namespace deletion will proceed once all API services are healthy."

## Examples

```
# Step 1: Check pod state via EKS MCP
read_k8s_resource(clusterName="my-cluster", kind="Pod", apiVersion="v1", name="stuck-pod", namespace="default")
# Look at: metadata.finalizers, metadata.deletionTimestamp, spec.terminationGracePeriodSeconds

# Step 2: Check pod events
get_k8s_events(clusterName="my-cluster", kind="Pod", name="stuck-pod", namespace="default")

# Step 3: Collect node logs
collect(instanceId="i-0abc123def456")
status(executionId="<id-from-step-3>")

# Step 4: Check kubelet termination logs
search(instanceId="i-0abc123def456", query="Killing|killing pod|graceful.*delete", logTypes="kubelet")

# Step 5: Check PreStop hooks
search(instanceId="i-0abc123def456", query="PreStop|preStop|lifecycle.*hook", logTypes="kubelet")

# Step 6: Check containerd stop operations
search(instanceId="i-0abc123def456", query="container.*stop|StopContainer|KillContainer", logTypes="containerd")

# Step 7: Correlate timeline
correlate(instanceId="i-0abc123def456", pivotEvent="Killing", timeWindow=300)

# Step 8: Generate summary
summarize(instanceId="i-0abc123def456", finding_ids=["F-001","F-002","F-003"])
```

## Output Format

```yaml
root_cause: "<finalizer_blocking|prestop_hook_hang|container_stop_failure|kubelet_api_disconnect|resource_pressure|namespace_content_remaining> — <specific detail>"
evidence:
  - type: pod_state
    content: "<finalizers, deletionTimestamp, terminationGracePeriodSeconds from read_k8s_resource>"
  - type: kubelet_search
    content: "<termination sequence logs from search>"
  - type: containerd_search
    content: "<container stop logs from search>"
  - type: correlate
    content: "<termination timeline>"
stuck_duration: "<time since deletionTimestamp>"
severity: HIGH
mitigation:
  immediate: "Operator: <specific fix — patch finalizers, force delete, restart kubelet>"
  long_term: "Review PreStop hooks, ensure finalizer controllers are reliable, set appropriate terminationGracePeriodSeconds"
```
