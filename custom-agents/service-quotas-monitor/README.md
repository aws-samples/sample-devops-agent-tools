# Service Quotas Monitor — Custom Agent

## Purpose

This custom agent proactively monitors AWS service quotas across all active regions, identifies quotas approaching their limits (85%+ utilization), and takes automated action — requesting quota increases via the Service Quotas API or escalating through support cases when programmatic increases are not possible.

## Key Capabilities

- Discovers all enabled regions and checks quotas across the entire account footprint
- Evaluates utilization for every service quota with available usage data
- Automatically requests quota increases for adjustable quotas at 85%+ utilization
- Creates support cases or recommendations when automatic increases are not possible
- Sends notifications via integrated communication tools when quotas are flagged
- Deduplicates recommendations to avoid alert fatigue

## Prerequisites

- An AWS DevOps Agent space
- IAM permissions for Service Quotas:
  - `servicequotas:ListServices`
  - `servicequotas:ListServiceQuotas`
  - `servicequotas:GetServiceQuota`
  - `servicequotas:RequestServiceQuotaIncrease`
  - `servicequotas:CreateSupportCase`
- IAM permissions for EC2 region discovery: `ec2:DescribeRegions`
- (Optional) AWS Support API access for creating support cases: `support:CreateCase`
- (Optional) The [service-quota-check skill](../../skills/service-quota-check/) uploaded to your Agent Space for enhanced domain knowledge

## Creating the Agent

1. In the DevOps Agent web app, go to the "Agents" menu (on the bottom left pane)
2. Click "Create agent" (on the right side), then on the new menu that popped up, click "Form" (the left-most option)
3. In the "Name" field, use "service-quotas-monitor"
4. Copy the content of the "SYSTEM_PROMPT.md" file from this directory, and paste it into the "System prompt" field in the custom agent creation form
5. (Optional) In the "Skills" drop-down list, select the "service-quota-check" skill if available, and click "Create agent"
6. Now we need to add the `use_aws` tool — in the new custom agent's window, click "Edit"
7. In the new popped up window, select "Chat". A new chat will start on the left side. Wait for DevOps Agent to finish thinking, and it'll ask you what would you like to change
8. Type "Add the use_aws tool to this custom agent". Once the chat is finished, verify in the custom agent's page that `use_aws` is shown under "Tools" for this custom agent

## Executing the Agent

This agent is designed to run on a recurring schedule (e.g., daily or weekly) to catch quotas approaching their limits before they cause disruptions. You can also run it on-demand.

### Scheduled Execution (Recommended)

Follow the [Executing custom agents guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/custom-agents-executing-custom-agents.html) to set up a recurring schedule. A daily run is recommended for production accounts with active scaling.

### On-Demand Execution

Run from the custom agent page or via chat. You can provide custom prompts:

- "Check quotas only in us-east-1 and eu-west-1"
- "Check only EC2 and VPC quotas"
- "Report quotas above 70% utilization instead of 85%"

## Output

The agent produces:
- **Task journal entry** — a text summary of all findings and actions taken
- **Recommendations** — for any quotas requiring manual user intervention
- **Notifications** — sent via integrated communication tools (e.g., Slack) if quotas are flagged

## Related

- [service-quota-check skill](../../skills/service-quota-check/) — the domain knowledge skill for quota checking methodology
- [AWS DevOps Agent custom agents documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html)
