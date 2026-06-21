# AWS Health Report — Custom Agent

## Purpose

This custom agent generates a report of AWS Health events (service issues, scheduled changes, and account notifications) during a specified time period (last 30 days by default). It provides operations teams with visibility into AWS-side events affecting their environment.

## Key Capabilities

- Retrieves all AWS Health events for a configurable reporting period
- Groups events by AWS service and event category
- Highlights upcoming and recent scheduled changes with affected resources
- Produces a structured report as a persisted artifact

## Prerequisites

- An AWS DevOps Agent space
- AWS Health API access (Business Support+, Enterprise Support, or Unified Operations plan)
- IAM permissions for `health:DescribeEvents`, `health:DescribeEventDetails`, and `health:DescribeAffectedEntities`
- The [aws-health-events skill](../../skills/aws-health-events/) uploaded to your Agent Space. Important note: for the skill to be used by the custom agent, choose "All agents" in the "Agent Type" field when importing the skill, even that the skill's README file instructs to choose specific agent types

## Creating the Agent

1. In the DevOps Agent web app, go to the "Agents" menu (on the bottom left pane)
2. Click "Create agent" (on the right side), then on the new menu that popped up, click "Form" (the left-most option)
3. In the "Name" field, use "aws-health-report"
4. Copy the content of the "SYSTEM_PROMPT.md" file from this directory, and paste it into the "System prompt" field in the custom agent creation form
5. In the "Skills" drop-down list, select the "aws-health-events" skill, and click "Create agent"
6. Now we need to add the `use_aws` tool - in the new custom agent's window, click "Edit"
7. In the new popped up window, select "Chat". A new chat will start on the left side. Wait for DevOps Agent to finish thinking, and it'll ask you what would you like to change
8. Type "Add the use_aws tool to this custom agent". Once the chat is finished, verify in the custom agent's page that `use_aws` is shown under "Tools" for this custom agent

## Executing the Agent

You can execute the custom agent on-demand from the custom agent page, on schedule, or using chat. Follow the [Executing custom agents guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/custom-agents-executing-custom-agents.html) for more information. You can also run it using custom prompt (for example, ask it to produce a report for the last 90 days instead of the default 30 days).  
Once finished, the artifact is persisted on the **Artifacts** page in the DevOps Agent web app.

## Related

- [aws-health-events skill](../../skills/aws-health-events/) — the domain knowledge skill this agent uses
- [AWS DevOps Agent custom agents documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html)
