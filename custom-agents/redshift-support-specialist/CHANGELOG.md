# Changelog

## 1.3.0

- Section 0 rewritten for the platform's actual execution model: custom agents always run as asynchronous invocations (confirmed against the Executing custom agents documentation), so "run in active chat" was unenforceable — asking Chat to use the agent kicked off a background run that then proceeded without scope confirmation. The new rule is "no scope, no run": the invocation prompt must name the cluster/workgroup, database(s), and capability; if anything is missing the agent calls `list_clusters` for discovery only and ends the run with a "Scope required — run not started" report instead of guessing
- Section 2/3/4 updated to match: rule 2a's "send a message and wait for the reply" (impossible mid-invocation) replaced with validate-scope-from-prompt-or-stop
- README rewritten: documents the async execution model, "Run with prompt" examples with explicit scope, and directs users to the skill in regular Chat for interactive step-by-step work

## 1.2.0

- Removed disaster recovery and incident detection from the capability mapping, README purpose, and key capabilities (aligns with skill v1.8.0, which removed Capabilities 4 and 5 pending a live CloudWatch MCP implementation). Section 3 now instructs the agent to state these aren't currently supported if asked.
- Removed references to the deleted `references/incident-response-playbooks.md` and `references/cloudwatch-metrics.md` files.

## 1.1.0

- Section 0 relaxed from "never background under any circumstances" to interactive-by-default: background execution is now honored only when the user explicitly asks for it, and only after cluster/workgroup and database scope have been confirmed (aligns with skill v1.7.0 Core Rule 11)
- Added rule 10a: an empty result set from a tool call is not a failure — report it with a friendly, positive message (e.g. "✅ No queries with disk spill found — nothing to fix here") and mark the check as PASS, even when the chat UI shows a "failed" badge

## 1.0.0

- Initial version
- Lean orchestrator system prompt bridging the `redshift-support-specialist` skill to the six `awslabs.redshift-mcp-server` MCP tools
- Enforces active-chat-only execution (never background), overriding the skill's own background-mode option for the Detailed Operational Review
- Hard-stop scope confirmation (cluster/workgroup + database) before any data collection
- Read-only enforcement and per-capability tool mapping (query optimization, operational reviews, disaster recovery, incident detection, cost optimization)
