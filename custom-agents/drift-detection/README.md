# Drift Detection — Custom Agent

## Purpose

This custom agent performs production drift detection — comparing the current state of AWS resources against a defined baseline of expected standards. It identifies security posture drift (encryption, network isolation, public access), compliance drift (tagging, logging, backups), and lifecycle drift (deprecated runtimes, end-of-life engine versions). Findings are reported as a structured artifact and tracked as recommendations in the improvements backlog.

Unlike a general best-practices review, this agent performs **true drift detection**: it loads a defined expected state, discovers what actually exists, and reports the delta. This makes it suitable for recurring scheduled runs where the goal is to catch configuration changes that deviate from your team's standards.

## Key Capabilities

- Compares current resource configurations against defined baseline policies
- Detects security posture drift: encryption, network isolation, public access, exposed secrets
- Detects compliance drift: missing tags, disabled logging, insufficient backups
- Detects lifecycle drift: deprecated Lambda runtimes, end-of-support RDS engines, outdated EKS versions
- Identifies AWS Config rule coverage gaps and suggests rules to codify enforcement
- Produces a persisted drift detection report artifact
- Creates or updates recommendations in the improvements backlog for each finding
- Deduplicates findings across runs to keep the backlog clean

## Prerequisites

- An AWS DevOps Agent space
- IAM permissions for read-only resource inspection (covered by `AIDevOpsAgentAccessPolicy`):
  - S3, RDS, EC2, Lambda, EKS, DynamoDB, CloudTrail, AWS Config, Resource Groups Tagging API
- The [drift-detection-baseline skill](../../skills/drift-detection-baseline/) uploaded to your Agent Space. Important note: for the skill to be used by the custom agent, choose "All agents" in the "Agent Type" field when importing the skill, even though the skill's README instructs to choose specific agent types

## Creating the Agent

1. In the DevOps Agent web app, go to the "Agents" menu (on the bottom left pane)
2. Click "Create agent" (on the right side), then on the new menu that popped up, click "Form" (the left-most option)
3. In the "Name" field, use "drift-detection"
4. Copy the content of the "SYSTEM_PROMPT.md" file from this directory, and paste it into the "System prompt" field in the custom agent creation form
5. In the "Skills" drop-down list, select the "drift-detection-baseline" skill, and click "Create agent"
6. Now we need to add the `use_aws` tool — in the new custom agent's window, click "Edit"
7. In the new popped up window, select "Chat". A new chat will start on the left side. Wait for DevOps Agent to finish thinking, and it'll ask you what would you like to change
8. Type "Add the use_aws tool to this custom agent". Once the chat is finished, verify in the custom agent's page that `use_aws` is shown under "Tools" for this custom agent

## Executing the Agent

### First Run (Recommended: On-Demand)

For the first run, execute the agent on-demand to review findings, tune the baseline, and confirm recommendations are routed correctly:

1. Navigate to the custom agent's page and click "Run"
2. Optionally provide a custom prompt to scope the review (see examples below)
3. Review the drift detection report artifact and recommendations

### Scheduled Execution (After Tuning)

After confirming the output is useful and low-noise, configure the agent to run on a schedule:

- **Daily** — before business hours, for production accounts with active deployments
- **Weekly** — before operational reviews, for stable environments

Follow the [Executing custom agents guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/custom-agents-executing-custom-agents.html) for schedule configuration.

### Custom Prompts

You can scope the review with custom prompts:

- "Focus only on S3 buckets and RDS instances"
- "Check only resources tagged with Environment: production"
- "Only report critical and high severity findings"
- "Focus on the us-east-1 region only"
- "Check for lifecycle drift only — deprecated runtimes and engines"

## Output

The agent produces:

- **Drift Detection Report** — a Markdown artifact summarizing all resources scanned, deviations found, and Config rule coverage gaps
- **Recommendations** — individual improvement items in the backlog for each drift finding, with severity, resource ARN, current vs. expected state, and remediation steps

## Related

- [drift-detection-baseline skill](../../skills/drift-detection-baseline/) — the baseline policies this agent evaluates against
- [Blog post: Build bespoke operational workflows with AWS DevOps Agent custom SRE agents](https://aws.amazon.com/blogs/devops/build-bespoke-operational-workflows-with-aws-devops-agent-custom-sre-agents/)
- [AWS DevOps Agent custom agents documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html)
