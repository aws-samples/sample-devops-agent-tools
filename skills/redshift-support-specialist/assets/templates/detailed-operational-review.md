<!--
  Detailed Operational Review — Markdown report template
  ========================================================
  STRUCTURAL template only. Contains NO customer data. It mirrors
  assets/templates/detailed-operational-review.html section-for-section so the
  two outputs always agree. Replace every {{token}} with data collected live via
  the awslabs.redshift-mcp-server MCP tools (list_clusters, list_databases,
  execute_query) and the queries in assets/queries/operational-review-collection.md.
  NEVER copy sample/example values into a real report — this file is reference
  structure only.

  Use this Markdown version as the PRIMARY chat-rendered output (raw HTML dropped
  into a chat renders as inert code, not an interactive page). Pair it with the
  HTML file (detailed-operational-review.html) as the downloadable artifact.

  Rows/blocks marked <!-- REPEAT --> are single-row examples: repeat that
  row/block once per returned data row, then delete the comment. Add or remove
  ### subsections (finding categories, issue types, query issue groups) to match
  whatever the live data collection actually returns — don't force every example
  category to appear if there's no data for it.

  The "Cluster Level Review (Power-2)" section requires CloudWatch/AWS CLI data
  the MCP tools cannot fetch — keep it framed as "Not Available via MCP tools"
  unless the user supplies that data manually.
-->

# Redshift Detailed Operational Review

**{{cluster_or_workgroup}}** · {{account_or_customer_label}} · Generated {{report_date}}
🔧 Cluster: {{node_type}} × {{node_count}} nodes | Slices: {{slice_count}}

📥 **[Download HTML Report]({{html_download_link}})** — same content, interactive tabs, printable layout.

---

## Executive Summary

**Overall Health: {{overall_health_emoji_and_label}}**

{{executive_summary_narrative}}

| Pass | Warn | Fail | Info |
|---|---|---|---|
| {{pass_count}} | {{warn_count}} | {{fail_count}} | {{info_count}} |

A total of **{{total_findings}}** checks were evaluated: **{{fail_count}} critical (FAIL)**, **{{warn_count}} warnings**, **{{pass_count}} passing**, and {{info_count}} informational.

### Critical Findings Requiring Immediate Attention

- **{{critical_finding_category}} — {{critical_finding_title}}:** {{critical_finding_detail}}
<!-- REPEAT per FAIL-severity finding -->

### Top Optimization Opportunities

- **{{opportunity_category}} — {{opportunity_title}}:** {{opportunity_detail}}
<!-- REPEAT per top WARN-severity opportunity -->

### Areas of Concern

- **{{area_category}}** — {{area_fail_count}} critical, {{area_warn_count}} warnings
<!-- REPEAT per finding category -->

The full breakdown is in [All Findings](#all-findings), and prioritized remediation steps are in [Recommendations](#recommendations).

---

## 1. Cluster Overview

| Metric | Value |
|---|---|
| Node Type | {{node_type}} |
| Node Count | {{node_count}} |
| Total Slices | {{slice_count}} |
| Storage Used | {{storage_used_gb}} GB / {{storage_capacity_gb}} GB ({{storage_utilization_pct}}% avg) |
| Storage Skew (max/min) | {{storage_skew_ratio}}x |
| WLM Mode | {{wlm_mode}} |
| User Queues | {{user_queue_count}} |
| SQA Enabled | {{sqa_enabled}} |
| Databases Reviewed | {{database_list}} |

---

## 1b. Cluster Level Review (Power-2)

> ℹ️ **Not Available via MCP tools.** This section covers CloudWatch metrics, support case
> history, and AWS CLI-sourced configuration checks (SSL enforcement, audit logging,
> Enhanced VPC Routing, Multi-AZ, cross-region snapshot copy, maintenance window,
> parameter groups). The connected `awslabs.redshift-mcp-server` MCP tools only provide
> `list_clusters`, `list_databases`, `list_schemas`, `list_tables`, `list_columns`, and
> `execute_query` — none of which expose CloudWatch or AWS CLI data. Populate the tables
> below only if the user supplies this data manually; otherwise leave every row as
> "Not Available."

**Account:** {{account_id}} | **Region:** {{region}} | **Cluster:** {{cluster_identifier}} | **Date:** {{report_date}}

| Category | Check | Status | Detail | Recommendation |
|---|---|---|---|---|
| Security | Encryption at rest | ℹ️ N/A | Requires AWS CLI | — |
| Security | SSL enforced (require_ssl) | ℹ️ N/A | Requires AWS CLI | — |
| Security | Audit logging | ℹ️ N/A | Requires AWS CLI | — |
| Security | Enhanced VPC Routing | ℹ️ N/A | Requires AWS CLI | — |
| Resilience | Multi-AZ | ℹ️ N/A | Requires AWS CLI | — |
| Backup | Automated snapshots / cross-region copy | ℹ️ N/A | Requires AWS CLI | — |
<!-- REPEAT: add rows only if user manually supplies data -->

### CloudWatch Metrics — 30-Day Trend

| Metric | Min | Avg | Max | Threshold | Status |
|---|---|---|---|---|---|
| CPU Utilization (Avg %) | Requires CloudWatch access | | | | ℹ️ N/A |
| Disk Space Used (Avg %) | Requires CloudWatch access | | | | ℹ️ N/A |
| Database Connections (Max) | Requires CloudWatch access | | | | ℹ️ N/A |
| Health Status (Min) | Requires CloudWatch access | | | | ℹ️ N/A |

### Active Redshift Service Events

> Requires AWS CLI (describe-events) access — not available via MCP tools.

### Open Redshift Support Cases

> Requires AWS Support API access — not available via MCP tools.

---

## 2. All Findings

| Pass | Warn | Fail | Info |
|---|---|---|---|
| {{pass_count}} | {{warn_count}} | {{fail_count}} | {{info_count}} |

### {{findings_category_1}} ({{findings_category_1_count}})

| Check | Status | Detail | Recommendation |
|---|---|---|---|
| {{check_name}} | ❌ FAIL | {{check_detail}} | {{check_recommendation}} |
<!-- REPEAT per finding row in this category -->

### {{findings_category_2}} ({{findings_category_2_count}})

| Check | Status | Detail | Recommendation |
|---|---|---|---|
| {{check_name}} | ⚠️ WARN | {{check_detail}} | {{check_recommendation}} |
<!-- REPEAT -->

<!-- REPEAT one ### subsection per finding category actually populated -->

---

## 3. WLM Configuration

| WLM Mode | Total Slots | User Queues | SQA | Cluster Memory |
|---|---|---|---|---|
| {{wlm_mode}} | {{total_wlm_slots}} | {{user_queue_count}} | {{sqa_enabled}} | {{cluster_memory_gb}} GB |

### Queue Configuration

| Queue | Service Class | Slots | Memory % | Memory/Slot | Concurrency Scaling | QMR Rules | Evictable | Routing |
|---|---|---|---|---|---|---|---|---|
| {{queue_name}} | {{service_class}} | {{queue_slots}} | {{queue_memory_pct}}% | {{queue_memory_per_slot}} | {{queue_concurrency_scaling}} | {{queue_qmr_rule_count}} | {{queue_evictable}} | {{queue_routing}} |
<!-- REPEAT per WLM queue -->

### Memory Allocation

Total cluster memory: {{cluster_memory_gb}} GB ({{cluster_memory_mb}} MB). {{wlm_mode}} WLM divides memory across queues based on percentage allocation.

| Queue | Slots | Memory % | Memory per Slot | Total Queue Memory |
|---|---|---|---|---|
| {{queue_name}} ({{service_class}}) | {{queue_slots}} | {{queue_memory_pct}}% | {{queue_memory_per_slot}} | {{queue_total_memory}} |
<!-- REPEAT per WLM queue -->

### QMR Rules (Query Monitoring Rules)

| Queue | Rule Name | Metric & Threshold | Action |
|---|---|---|---|
| {{queue_name}} | {{qmr_rule_name}} | {{qmr_metric_threshold}} | {{qmr_action}} |
<!-- REPEAT per QMR rule -->

**Key Settings:** Auto WLM = {{auto_wlm_enabled}} | SQA = {{sqa_enabled}} | Statement Timeout = {{statement_timeout}}

---

## 4. Workload Analysis

### Workload Distribution

| Type | % Total Workload | % Day Active | Query Count | Avg Exec (s) | Max Exec (s) | Avg Scan (MB) |
|---|---|---|---|---|---|---|
| {{workload_type}} | {{workload_pct_total}} | {{workload_pct_day_active}} | {{workload_query_count}} | {{workload_avg_exec_sec}} | {{workload_max_exec_sec}} | {{workload_avg_scan_mb}} |
<!-- REPEAT per workload_type (small/medium/large) -->

### Queue Performance Summary

| Queue | Queries | Elapsed (s) | Queue Time (s) | Queue % | Disk Spill | Errors | Small Inserts |
|---|---|---|---|---|---|---|---|
| {{queue_name}} | {{queue_query_count}} | {{queue_elapsed_sec}} | {{queue_wait_sec}} | {{queue_wait_pct}}% | {{queue_disk_spill}} | {{queue_error_count}} | {{queue_small_insert_count}} |
<!-- REPEAT per queue -->

---

## 5. Top Queries by Runtime

Queries grouped by primary issue. Use the Query ID to locate the query in your cluster.

### {{issue_group_title}} ({{issue_group_count}})

{{issue_group_description}}

<details>
<summary><strong>#{{rank}}</strong> Query ID: <code>{{query_id}}</code> — {{elapsed_sec}}s — ×{{run_count}} runs — {{query_type}} — {{queue_name}}</summary>

**What's happening:** {{issue_explanation}}
**Performance impact:** {{performance_impact}}
**Root cause:** {{root_cause}}
**Fix:** {{fix_recommendation}}

| Metric | Value | % of Elapsed | Flag |
|---|---|---|---|
| Execution Time | {{exec_sec}}s | {{exec_pct}}% | |
| Queue Wait | {{queue_wait_sec}}s | {{queue_wait_pct}}% | |
| Compile Time | {{compile_sec}}s | {{compile_pct}}% | |
| Planning Time | {{planning_sec}}s | | |
| Lock Wait | {{lock_wait_sec}}s | | |
| Disk Spill (Total) | {{spill_total_mb}} MB | | {{spill_flag}} |
| Disk Spill (Local) | {{spill_local_mb}} MB | | |
| Disk Spill (Remote/S3) | {{spill_remote_mb}} MB | | {{remote_spill_flag}} |
| Range-Restricted Scan | {{range_restricted}} | | {{range_restricted_flag}} |

**Query ID:** `{{query_id}}` | **User:** {{query_user}} | **Priority:** {{query_priority}} | **Compute:** {{compute_type}}
**Tables:** {{referenced_tables}}
</details>
<!-- REPEAT per query within this issue group -->

<!-- REPEAT one ### subsection per issue group (e.g. Heavy Disk Spill, Long Running Query, etc.) -->

### Top 20 Summary

Flags: 💾 Spill >100MB | ☁️ Remote spill (S3) | ⏳ Queue >10% | 🔨 High compile | 🔄 Nested loop | 📡 Broadcast | 📊 Missing stats | 🔀 Unsorted | ❌ Error

| # | Query ID | Exec (s) | Elapsed (s) | Queue % | Count | Type | Queue | Spill (MB) | Flags |
|---|---|---|---|---|---|---|---|---|---|
| {{rank}} | {{query_id}} | {{exec_sec}} | {{elapsed_sec}} | {{queue_wait_pct}}% | {{run_count}} | {{query_type}} | {{queue_name}} | {{spill_mb}} | {{flags}} |
<!-- REPEAT for up to 20 rows -->

---

## 6. Table Design

| Total Issues | Fail | Warn | Info | Issue Types |
|---|---|---|---|---|
| {{table_design_total_issues}} | {{table_design_fail_count}} | {{table_design_warn_count}} | {{table_design_info_count}} | {{table_design_issue_type_count}} |

### {{issue_type_1}} ({{issue_type_1_count}})

**Recommendation:** {{issue_type_1_recommendation}}

| Table | Rows | Severity | Detail | Recommendation |
|---|---|---|---|---|
| {{schema_dot_table}} | {{row_count}} | FAIL | {{issue_detail}} | {{issue_recommendation}} |
<!-- REPEAT per affected table -->

<!-- REPEAT one ### subsection per table-design issue type actually populated -->

---

## 7. Spectrum / External Queries

{{spectrum_table_count}} external tables queried. **{{spectrum_poor_pruning_count}} partitioned tables with poor partition pruning (<95%).**

| Table | Format | Partitions | Queries | Pruning % | Avg Elapsed (s) |
|---|---|---|---|---|---|
| {{external_table_name}} | {{external_file_format}} | {{partition_count}} | {{external_query_count}} | {{partition_pruning_pct}}% | {{external_avg_elapsed_sec}} |
<!-- REPEAT per external table, or replace with "No Spectrum usage detected." if none found -->

---

## 8. Data Sharing

| Share Name | Objects |
|---|---|
| {{share_name}} | {{share_object_count}} |
<!-- REPEAT per data share, or replace with "No data sharing configured." if none found -->

---

## Upgrade / Cost Savings Opportunity

> 💡 **{{upgrade_recommendation_title}}**
>
> {{upgrade_recommendation_summary}}

| Aspect | Current Configuration | Recommended |
|---|---|---|
| Instance Type | {{current_node_type}} | {{recommended_node_type}} |
| Node Count | {{current_node_count}} | {{recommended_node_count}} |
| On-Demand Price (per node/hr) | {{current_price_per_node_hr}} | {{recommended_price_per_node_hr}} |
| Monthly Compute (On-Demand) | {{current_monthly_cost}} | {{recommended_monthly_cost}} |
| **Estimated Monthly Savings** | **{{estimated_monthly_savings}} ({{estimated_savings_pct}}%)** | |

**Migration method:** {{migration_method}}

> ℹ️ Precise current-cost figures require AWS CLI/Cost Explorer access not available
> through the MCP tools. Treat savings estimates as directional unless the user
> supplies actual billing data.

---

## 9. Prioritized Recommendations

1. **[FAIL]** {{recommendation_1_title}} — {{recommendation_1_detail}}
   💡 {{recommendation_1_fix}}
<!-- REPEAT one numbered item per recommendation, ordered by severity (FAIL > WARN > INFO) -->
