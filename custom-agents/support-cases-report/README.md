# Support Cases Report — Custom Agent

## Purpose

This custom agent generates a weekly summary report of all AWS Support cases opened, updated, or resolved during a specified time period. It provides operations teams with a consolidated view of support activity, recurring patterns, and items requiring follow-up.

## Key Capabilities

- Retrieves all AWS Support cases (open and resolved) for a configurable reporting period
- Groups cases by service, severity, and status
- Identifies recurring patterns and escalation trends
- Highlights open cases requiring attention (critical/urgent, long-running)
- Produces a structured Markdown report as a persisted artifact

## Prerequisites

- An AWS DevOps Agent space with the AWS account associated
- AWS Support API access (Business, Enterprise On-Ramp, or Enterprise support plan)
- IAM permissions for `support:DescribeCases` and `support:DescribeCommunications`
- The **support-cases** skill uploaded to your Agent Space

## Configuration

### Creating the Agent

1. In the DevOps Agent console, go to **Custom Agents** and choose **Create agent**
2. Configure the agent with:

| Setting | Value |
|---------|-------|
| **Name** | `Support Cases Weekly Report` |
| **System prompt** | Copy contents of [`SYSTEM_PROMPT.md`](./SYSTEM_PROMPT.md) |
| **Tools** | AWS Support (must include `describe-cases` capability) |
| **Skills** | `support-cases` |

### Scheduling (Recommended)

Set up a weekly schedule to run this agent automatically:

- **Frequency**: Weekly (e.g., every Monday at 09:00 UTC)
- **Run prompt** (optional): Use a custom prompt to override defaults, e.g.:
  - `"Generate the report for the past 14 days"` — for a biweekly cadence
  - `"Focus only on critical and urgent cases"` — for an executive summary

### On-Demand Execution

Run manually anytime from the Custom Agents page or via Chat:

> "Run my Support Cases Weekly Report agent"

You can also pass a custom prompt to override the time window:

> "Run my Support Cases Weekly Report agent for the past 30 days"

## Output

The agent produces a Markdown artifact with:

- Executive summary
- Cases by severity (opened, resolved, still open)
- Cases by service with most common issues
- Open cases requiring attention
- Resolved cases with resolution times
- Recurring patterns and trends
- Actionable recommendations

The artifact is persisted on the **Artifacts** page in the DevOps Agent console.

## Customization

You can tailor this agent by editing the system prompt:

- **Change the default time window**: Modify "past 7 days" to any period
- **Add team-specific sections**: Include account IDs, service filters, or team ownership mapping
- **Adjust verbosity**: For large environments, configure the agent to summarize normal/low cases and detail only critical/urgent ones
- **Add integrations**: Extend the prompt to post the report to Slack, create a Jira ticket, or send via email (requires additional tools)

## Related

- [support-cases skill](../../skills/support-cases/) — the domain knowledge skill this agent uses
- [AWS DevOps Agent custom agents documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html)
