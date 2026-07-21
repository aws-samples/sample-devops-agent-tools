# Redshift Support Specialist — Custom Agent

**Version: 1.3.0** (see [`CHANGELOG.md`](CHANGELOG.md)) | Requires skill version 1.8.0+ (see [`skills/redshift-support-specialist/`](../../skills/redshift-support-specialist/))

## Purpose

This custom agent is a lean orchestrator for the [`redshift-support-specialist`](../../skills/redshift-support-specialist/) skill. It bridges that skill's domain knowledge (query optimization, operational reviews, cost optimization) to the six tools exposed by the connected `awslabs.redshift-mcp-server` MCP server, and enforces one important behavior rule: **no scope, no run**. Custom agents execute as asynchronous invocations (there is no interactive user to answer questions mid-run), so the target cluster/workgroup and database(s) must be provided in the invocation prompt — if they're missing, the agent ends the run immediately with a "Scope required — run not started" report instead of guessing or defaulting.

## Key Capabilities

- Diagnoses slow Redshift queries live (EXPLAIN plan, disk spill, distribution/sort key issues) with concrete SQL/config fixes
- Runs a quick PASS/WARN/FAIL operational review using cluster inventory data
- Runs a full Detailed Operational Review (storage, WLM, table design, Advisor recommendations) and produces both an in-chat Markdown summary and a downloadable HTML report
- Performs cost optimization analysis, including provisioned-to-serverless RPU sizing

## Important behavior note

Custom agents in AWS DevOps Agent always execute as **asynchronous invocations** — "Run Now" on the agent page and "run the agent" in Chat both kick off a background run tracked in the **History** tab. This is platform behavior and cannot be switched to an interactive session ([Executing custom agents](https://docs.aws.amazon.com/devopsagent/latest/userguide/custom-agents-executing-custom-agents.html)).

Because the agent cannot ask questions mid-run, this agent's system prompt (Section 0) enforces **no scope, no run**: the invocation prompt must name the target cluster/workgroup, the database(s), and what to do. Example:

> "Run the custom redshift-support-specialist agent and perform a detailed operational review on cluster `my-cluster`, databases `analytics` and `sales`, with the HTML report."

If scope is missing, the run ends immediately with a "Scope required — run not started" report listing the discovered clusters/workgroups — no data is collected and nothing is assumed. For an interactive, step-by-step experience (where the agent asks scope questions and waits for your answers), use the skill from the regular DevOps Agent Chat instead of executing this custom agent.

## Prerequisites

- An AWS DevOps Agent space
- The [redshift-support-specialist skill](../../skills/redshift-support-specialist/) uploaded to your Agent Space. Important note: for the skill to be used by the custom agent, choose "All agents" in the "Agent Type" field when importing the skill, even though the skill's README instructs to choose "Chat"
- The `awslabs.redshift-mcp-server` MCP server deployed and connected as a capability provider — see the skill's [Step 1 — MCP Server Deployment](../../skills/redshift-support-specialist/README.md#step-1--mcp-server-deployment) and [Step 2 — Connect the MCP server to your Agent Space](../../skills/redshift-support-specialist/README.md#step-2--connect-the-mcp-server-to-your-agent-space) sections
- No AWS CLI or CloudWatch access is required for the agent itself; all Redshift access goes through the connected MCP server

## Creating the Agent

The MCP server must already be registered as an account-level capability provider and connected to your Agent Space before creating this agent — see [Prerequisites](#prerequisites) above and the skill's [Step 2 — Connect the MCP server to your Agent Space](../../skills/redshift-support-specialist/README.md#step-2--connect-the-mcp-server-to-your-agent-space) steps.

1. In the DevOps Agent web app, go to the "Agents" page.
2. In the "Custom Agents" section, click "Create agent".
3. In the dialog, click "Form".
4. Fill out the form:
   - **Name** — `redshift-support-specialist` (lowercase letters, numbers, hyphens only).
   - **System prompt** — copy the content of `SYSTEM_PROMPT.md` from this directory and paste it in.
   - **Skills** — select the `redshift-support-specialist` skill.
5. Click "Create agent".

### Assign the MCP tools (Chat only)

MCP tools cannot be assigned through the Form — they can only be configured through Chat, either when creating the agent via Chat instead of Form, or by editing an existing agent:

1. On the newly created agent's page, click "Edit", then select "Chat". A new chat opens.
2. Once DevOps Agent finishes loading the agent's context, type:

   ```text
   Add the list_clusters, list_databases, list_schemas, list_tables, list_columns, and execute_query tools from the awslabs.redshift-mcp-server MCP server to this custom agent.
   ```

3. Once the chat finishes, verify all six tools appear under "Tools" on the agent's page. This agent has no other way to reach Redshift — without these tools assigned, it cannot call the MCP server at all.

## Executing the Agent

You can execute the custom agent on-demand from the custom agent page (**Run Now** → dropdown → **Run with prompt** to pass scope) or through Chat. Follow the [Executing custom agents guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/custom-agents-executing-custom-agents.html) for more information. Always include the full scope in the prompt, for example:

- "Run the custom redshift-support-specialist agent and perform a detailed operational review on cluster `my-cluster`, all databases, with the HTML report."
- "Run the custom redshift-support-specialist agent and diagnose query 4823991 on cluster `my-cluster`, database `analytics`."

To see the agent's capabilities and example prompts without starting an invocation, ask in Chat: "What else can the custom redshift-support-specialist agent do?"

Track progress and results on the agent's page under the **History** tab (invocation trajectory). Runs invoked without scope end with a "Scope required — run not started" report. Because scope must be known upfront, this agent also works well with schedule triggers for recurring pre-scoped reviews.

## Related

- [redshift-support-specialist skill](../../skills/redshift-support-specialist/) — domain knowledge for Redshift query optimization, operational reviews, and cost optimization
- [AWS DevOps Agent custom agents documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html)
