# EOS Cost Analysis — Custom Agent

## Purpose

A dedicated custom agent that discovers AWS resources approaching or past End of Standard Support and calculates the Extended Support cost impact. Use this when you want automated, scheduled EOS posture reports without manual prompting.

> **Note:** For ad-hoc queries, you don't need this custom agent. Just upload the [eos-cost-analysis skill](../../skills/eos-cost-analysis/) and ask questions in regular Chat. This custom agent is for scheduled/automated runs.

## Key Capabilities

- Runs on demand or on a weekly/monthly schedule
- Discovers resources across all associated accounts automatically
- Produces a persisted artifact (report) each run for tracking over time
- Covers EKS, RDS/Aurora, Lambda, ElastiCache, and OpenSearch
- Calculates per-resource Extended Support cost with Year 1/2/3 pricing tiers

## Prerequisites

- An AWS DevOps Agent space
- The [eos-cost-analysis skill](../../skills/eos-cost-analysis/) uploaded to your Agent Space (choose "All agents" for Agent Type)
- `use_aws` and `verify_aws_claim` tools available in the Agent Space
- IAM permissions for resource discovery:
  - `eks:ListClusters`, `eks:DescribeCluster`
  - `rds:DescribeDBInstances`, `rds:DescribeDBClusters`
  - `lambda:ListFunctions`
  - `elasticache:DescribeCacheClusters`
  - `opensearch:ListDomainNames`, `opensearch:DescribeDomains`
- For multi-account analysis: associate secondary accounts in DevOps Agent settings

## Creating the Agent

1. In the DevOps Agent web app, go to the "Agents" menu (on the bottom left pane)
2. Click "Create agent" (on the right side), then click "Form" (the left-most option)
3. In the "Name" field, use "eos-cost-analysis"
4. Copy the content of the `SYSTEM_PROMPT.md` file from this directory, and paste it into the "System prompt" field
5. In the "Skills" drop-down list, select the "eos-cost-analysis" skill, and click "Create agent"
6. Add the `use_aws` tool — in the custom agent's window, click "Edit", select "Chat", and type "Add the use_aws tool to this custom agent"
7. Add the `verify_aws_claim` tool — type "Also add the verify_aws_claim tool"
8. Verify both tools appear under "Tools" for this custom agent

## Executing the Agent

You can execute the custom agent on-demand, on schedule, or using chat. Follow the [Executing custom agents guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/custom-agents-executing-custom-agents.html) for more information.

**On-demand prompts:**
- "Run this agent now" (uses default — full EOS analysis across all services and accounts)
- "Analyze EKS EOS only in us-east-1 and eu-west-1"
- "Check RDS extended support costs for the last quarter"

**Scheduled runs:**
- Configure weekly or monthly execution under the agent's schedule settings
- Each run produces a new artifact version, allowing week-over-week comparison

Once finished, the artifact is persisted on the **Artifacts** page in the DevOps Agent web app.

## Multi-Account Setup

For organization-wide analysis across multiple AWS accounts:

1. In DevOps Agent, go to Settings → Cloud Sources
2. Click "Add secondary cloud source"
3. Follow the prompts to create a cross-account role in each secondary account
4. After association, the agent automatically discovers resources across all connected accounts

No additional configuration needed in the skill or agent — DevOps Agent handles cross-account access transparently.

## Related

- [eos-cost-analysis skill](../../skills/eos-cost-analysis/) — the domain knowledge skill this agent uses (also works standalone in Chat)
- [aws-health-events skill](../../skills/aws-health-events/) — for correlating with Health Dashboard deprecation events
- [AWS DevOps Agent custom agents documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html)
