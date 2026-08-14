# ECS Lambda Rewrite Spec

## Goal
Rewrite `src/lambda/ecs-log-automation.py` from line 451 onwards to match EKS gold standard patterns.
Keep lines 1-449 (ECS_ERROR_PATTERNS, ECS_TRIAGE_CATEGORIES, ECS_LOG_TYPE_PATTERNS) exactly as-is.

## Tasks (in order)

### Task 1: Foundation Layer (lines 451-800)
- Compiled error patterns (pre-compile ECS_ERROR_PATTERNS regexes)
- Response helpers: success_response (5.5MB guard), error_response
- S3 safe helpers: safe_s3_read, safe_s3_head, safe_s3_list
- Utility helpers: get_regional_client, detect_instance_region, resolve_region
- Severity class, normalize_severity_filter, assign_finding_id
- format_bytes, parse_failure_reason (RunCommand), estimate_progress (RunCommand)
- Idempotency: store_execution_region, get_execution_region, find_execution_by_idempotency_token, store_idempotency_mapping

### Task 2: Scan & Analysis Layer (lines 800-1200)
- Baseline helpers: load_baselines, update_baselines, annotate_findings_with_baselines
- Scan helpers: find_findings_index, scan_and_index_errors, scan_file_for_errors (false positive suppression, multi-signal)
- Read/search: read_by_lines, search_file_for_pattern (chunked), get_line_context, extract_timestamp
- categorize_log_source (ECS-specific), find_correlations (ECS-specific), generate_recommendations (ECS-specific)
- Triage: perform_ecs_triage (using ECS_TRIAGE_CATEGORIES)
- Temporal: _build_temporal_clusters, _build_root_cause_chain (ECS causal patterns)

### Task 3: Core Tool Handlers (lines 1200-1800)
- lambda_handler with routing map (15 short names matching construct)
- collect (start_log_collection) - idempotency, cross-region, embedded bash script (PRESERVE existing)
- status (get_collection_status) - RunCommand APIs, progress estimation
- validate (validate_bundle_completeness) - manifest.json support
- errors (get_error_summary) - pre-indexed findings, pagination, baseline subtraction
- read (read_log_chunk) - byte-range + line-based, line-aligned
- search (search_logs_deep) - regex with S-NNN finding_ids, chunked
- correlate (correlate_events) - temporal clusters, root cause chains

### Task 4: Advanced Tool Handlers (lines 1800-2400)
- artifact (get_artifact_reference) - presigned URLs
- summarize (generate_incident_summary) - grounded in finding_ids, triage
- history (list_collection_history) - cross-region S3 listing
- cluster_health - ECS cluster overview via ecs:ListContainerInstances + DescribeContainerInstances
- compare_instances - diff findings between instances
- batch_collect - smart batch with sampling, filter unhealthy/disconnected
- batch_status - poll multiple executions
- network_diagnostics - ECS-specific sections

## Key ECS Differences from EKS
- SSM: start_automation_execution with AWSSupport-CollectECSInstanceLogs (same pattern as EKS)
- Parameters: ECSInstanceId, LogDestination, AutomationAssumeRole
- S3 prefix: ecs_{instance_id}
- Log types: ecs-agent, docker, containerd, system, kernel, networking, cgroups, metadata
- cluster_health uses ecs:ListContainerInstances + DescribeContainerInstances
- batch_collect filters: unhealthy/disconnected (not notready)
- network_diagnostics sections: iptables,docker,routes,dns,eni,security-groups
- Tool routing uses short names: collect, status, validate, errors, read, search, correlate, artifact, summarize, history, cluster_health, compare_instances, batch_collect, batch_status, network_diagnostics
