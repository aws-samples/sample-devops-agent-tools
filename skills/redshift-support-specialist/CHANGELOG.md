# Changelog

## [1.8.0] - 2026-07-20

### Removed

- Capability 4 (Disaster Recovery Recommendations) and Capability 5 (Incident Detection & Response) — both were reference-guidance-only since the connected Redshift MCP tools provide no AWS CLI or CloudWatch access. Removed pending a proper live implementation (planned: CloudWatch MCP server integration for alarm/metric analysis). The skill now has four capabilities: Query Optimization, High-Level Operational Review, Detailed Operational Review, and Cost Optimization (renumbered from 6 to 4).
- `references/incident-response-playbooks.md` and `references/cloudwatch-metrics.md` — orphaned by the capability removal; preserved in the `v1.7.0` tag / `backup/v1` branch for when the capabilities return.
- All remaining alarm/DR references outside the removed capabilities: Capability 2's alarm-coverage skip item and "Not Available" row, `references/health-checklist.md` alarm checks (4.14, 4.15, 6.6) and `describe-alarms` API references, and `references/best-practices.md` section 11.6 (Disaster Recovery & High Availability, sections renumbered) plus the CloudWatch-alarm monitoring bullet. Query-execution alerts (`STL_ALERT_EVENT_LOG`, used by Query Optimization) and backup/snapshot guidance (§11.5, used by the operational review's snapshot checks) are unrelated and retained.

## [1.7.0] - 2026-07-20

### Changed

- Execution mode is now interactive-by-default and no longer offered as a choice: Core Rule 10's confirmation question covers scope only (cluster/workgroup + database(s)), and the new Core Rule 11 requires all data collection to run turn by turn in the active chat session. Background execution is allowed only when the user explicitly asks for it, and only after the full scope has been confirmed. Previously the agent asked "background or step-by-step?" as part of the confirmation message, which led platforms to default into background runs. Capability 3's combined confirmation message updated accordingly (scope + HTML-report preference; no execution-mode question).

### Added

- Core Rule 9 now includes an explicit empty-result rule: a query that succeeds but returns zero rows is a normal, often healthy outcome (no disk spill, no queue waits, no stale tables) and must be reported with a friendly, positive message (e.g. "✅ No queries with disk spill found — nothing to fix here") and marked ✅ PASS / "no findings" — never as failed, even when the chat UI shows a generic "failed" badge on the tool call. Failure language is reserved for actual errors with error text.

## [1.6.2] - 2026-07-16

### Fixed

- `references/best-practices.md` had significant content corruption: two top-level section headers ("1. Table Design" and "2. Data Loading & Ingestion") were missing entirely even though their `1.1`–`1.4` and `2.1`–`2.4` subsections existed, several subsections (`1.4`, `2.2`, `2.3`, `3.2`, `3.3`) were duplicated with different/garbled content, and multiple sentences were fused together mid-word (e.g. "Incremental Loading Patternslerance", "the initial load (when..."). Rebuilt the file section-by-section; it now has clean, sequential headers (0, 1.1–1.4, 2.1–2.4, 3.1–3.4, 4.1–4.3, 5.1–5.3, 6.1–6.4, 7, 8, 9, 10, 11.1–11.8) with no duplicates or corrupted text.
- `references/serverless-sizing-guide.md` Q2 query had a corrupted `TIMESuserP` token where `TIMESTAMP` was intended, which would have caused the query to fail if run as-is. Fixed.
- `references/operational-review-signals.md`: corrected two inaccurate counts — the section-count summary said "10 data collection sections" when the table below it lists 12; the recommendation catalog header said "30 unique recommendations" when the catalog contains 31. Also linked the "AI-Driven Scaling is disabled" manual observation to its matching catalog entry (`#39`), which existed but wasn't cross-referenced.
- Verified via automated cross-check that every `#N` recommendation ID cited by a signal in `operational-review-signals.md` and `assets/queries/operational-review-collection.md` resolves to an entry in the recommendation catalog, and that all 31 catalog IDs are unique.

## [1.6.1] - 2026-07-16

### Changed

- Removed remaining internal-only content carried over from the source AWS Support Specialist skill, to bring the skill in line with public-repo contribution requirements:
  - Removed the `references/health-checklist.md` "Pre-Assessment: Customer & Account Context" section, which called internal tools (`get_kcr_kci_risk_on_customer_sentiment`, `caseapi_fetch_cases`) that have no equivalent through the connected MCP tools. Replaced with a minimal "Pre-Assessment: Account Context" section using only `aws sts get-caller-identity` and the public AWS Health Dashboard.
  - Replaced internal "SA-validated" / "SA to Validate" terminology in `references/operational-review-signals.md` with "Require Human Review," and replaced two internal hash-style recommendation IDs (`0c976c00`, `f15ca331`) with sequential IDs (`#39`, `#40`) consistent with the rest of the catalog. Updated the corresponding reference in `assets/queries/operational-review-collection.md`.
  - Replaced citations of internal, non-public training material ("Amazon Redshift Best Practices Q1 2026 training deck," "Amazon Redshift Operations Q1 2026 training deck," "Internal Redshift Serverless Sizing Usage Guide") in `references/best-practices.md` and `references/serverless-sizing-guide.md` with links to public Amazon Redshift documentation.
  - Reworded "customer"-centric, AWS-Support-style phrasing (e.g. "Inform customer," `--profile customer-profile`, "Request customer to run," "Share both outputs with customer") across `references/health-checklist.md`, `references/incident-response-playbooks.md`, `references/cloudwatch-metrics.md`, and `references/serverless-sizing-guide.md` to reflect a self-service DevOps Agent user rather than an AWS Support engineer assisting a customer.
  - Fixed a leftover instruction in `assets/queries/diagnostic-bundle.md` telling the user to "export as CSV or copy ALL rows and share" — contradicted the skill's core no-CSV rule; now instructs running the query via `execute_query` and analyzing results directly.
- Bumped `SKILL.md` `metadata.version` to `1.6.1` to match this changelog.

## [1.6.0] - 2026-07-15

### Changed

- Rebuilt `assets/templates/detailed-operational-review.html` and its companion `.md` to match a more detailed report structure: gradient header banner, executive summary with critical findings/opportunities/areas-of-concern call-outs, tabbed "All Findings" view, full WLM queue/memory/QMR breakdown, workload distribution and queue performance tables, top-queries grouped by issue with per-query time/spill breakdowns, tabbed table-design issue types, Spectrum/external and data-sharing tables, an upgrade/cost-savings callout, and a numbered prioritized-recommendations list. Every value remains a `{{placeholder}}` token or a single `<!-- REPEAT -->` example row — no real or sample customer data was introduced. Section order and content still exactly mirror the queries in `assets/queries/operational-review-collection.md` and the skill's Core Rules (Cluster Level Review stays "Not Available via MCP tools").

## [1.5.0] - 2026-07-15

### Changed

- The downloadable HTML report for the Detailed Operational Review is no longer generated unconditionally. The combined scope/mode confirmation message (Capability 3, step 2) now also asks whether the user wants a downloadable HTML file, or just the in-chat Markdown summary. The Markdown report remains mandatory and always produced; the HTML file is now opt-in.
- Updated Core Rule 12 and the `assets/templates/detailed-operational-review.html` asset description to reflect that the HTML artifact is conditional on the user's answer, not automatically generated alongside the Markdown report.

## [1.4.0] - 2026-07-15

### Changed

- Strengthened Core Rule 10 into an explicit hard-stop: the agent must send ONE combined message covering scope (cluster/workgroup + database) AND execution mode (background vs. step-by-step) together, then wait for the user's reply before calling any data-collecting tool (`execute_query`, `list_databases` follow-ups, etc.) or starting a background task. Previously the scope and background-mode questions were separate steps, which some sessions were skipping past without actually waiting for a reply.
- Updated the Query Optimization, High-Level Operational Review, and Detailed Operational Review workflows to reflect the single combined confirmation gate instead of two sequential questions.
- Renumbered former Core Rule 11 (background-mode handling once chosen) to reflect the merge with Rule 10.

## [1.3.0] - 2026-07-15

### Added

- Core Rule 10: the agent must always explicitly confirm scope (cluster/workgroup AND database) before collecting data for any capability that targets one — even when there is only one candidate, it must be stated back to the user rather than silently assumed.
- Core Rule 11: the agent must ask whether to run in the background or step-by-step before starting any multi-step or long-running workflow (in particular the Detailed Operational Review), and respect the answer.
- Core Rule 12: a review is not complete until every section defined in the workflow/template has been attempted and written into the output — permission errors or unavailable views must be reported and the review continued, never used as a reason to truncate or summarize instead of producing the full report.
- Explicit scope-confirmation and background-mode-question steps added to the Query Optimization, High-Level Operational Review, and Detailed Operational Review workflows.

## [1.2.0] - 2026-07-14

### Added

- Adapted to the [Agent Skills specification](https://agentskills.io/specification) for AWS DevOps Agent: `metadata.version`, `metadata.author`, and `compatibility` frontmatter fields; `README.md` with non-production disclaimer; this `CHANGELOG.md`.
- Database-scope question for the Detailed Operational Review capability — the agent now asks which database(s) to focus the review on (all, or a specific subset) instead of assuming a single default database.
- Dual output format for the Detailed Operational Review: an HTML report (downloadable artifact with a built-in self-download button) and a companion Markdown report (rendered directly in chat), both generated from the same live-collected data.
- Error-transparency rule: the agent now quotes the actual tool error text back to the user for any failed diagnostic query instead of reporting a generic "failed" status, and continues with the remaining sections rather than aborting the whole review.

### Changed

- Rewrote all six capabilities to be driven entirely by the `awslabs.redshift-mcp-server` MCP server's six tools (`list_clusters`, `list_databases`, `list_schemas`, `list_tables`, `list_columns`, `execute_query`). The skill no longer asks users for an AWS CLI profile, a cluster identifier from memory, or a CSV export from an external extraction tool — all diagnostics are collected live by the agent.
- Clarified, capability by capability, exactly which checks require AWS CLI/CloudWatch access the MCP server does not provide, and instructed the agent to report those as "Not Available" rather than guessing or fabricating values.

## [1.0.0] - Initial version

### Added

- Initial release covering query optimization, high-level operational review, detailed operational review, disaster recovery recommendations, incident detection/response guidance, and cost optimization for Amazon Redshift provisioned clusters and Serverless workgroups.
