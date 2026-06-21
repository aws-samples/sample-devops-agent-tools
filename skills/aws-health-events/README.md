# AWS Health Events Skill

This skill enables the AWS DevOps Agent to retrieve and analyze AWS Health events during incident investigation, root cause analysis, and operational troubleshooting.

## Purpose

When an operational incident occurs, AWS Health events provide critical visibility into service disruptions, scheduled maintenance, and account-specific notifications that may explain or correlate with observed issues. This skill searches AWS Health events by service, time window, severity, region, and event type to surface AWS-side events as potential root causes or contributing factors.

## Key Capabilities

- Retrieve open and resolved AWS Health events via the Health API
- Search and filter events by service, severity, time range, region, and event type
- Identify affected resources impacted by Health events
- Correlate Health events with current incidents to determine root cause or contributing factors
- Generate health posture reports summarizing account health over configurable time periods

## Prerequisites

- IAM permissions for `health:Describe*` actions (`health:DescribeEvents`, `health:DescribeEventDetails`, `health:DescribeAffectedEntities`, `health:DescribeEventTypes`)
- AWS Health API access (requires AWS Business Support+, Enterprise Support, or Unified Operations support plan)

## Limitations

- The AWS Health API is only available in the `us-east-1` region for commercial accounts
- Health event data retention is limited by the AWS Health service retention period
- API rate limits apply — the skill implements exponential backoff for throttled requests

## Agent Types

This skill is used by the following agent types:

- **Chat tasks** — conversational health event lookup, reporting, and analysis
- **Incident RCA** — root cause analysis during active incidents

## Uploading to AWS DevOps Agent

You can add this skill to your Agent Space in three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/aws-health-events` directory. When prompted, select the agent types: **Chat tasks** and **Incident RCA**. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository — including this one — even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Zip the `aws-health-events/` directory (only including allowed extensions):

   ```bash
   cd skills
   zip -r aws-health-events.zip aws-health-events/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
   ```

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the `aws-health-events.zip` file (max 6 MB).
5. Select the agent types: **Chat tasks** and **Incident RCA**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

For more details, see [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) in the AWS DevOps Agent User Guide.

## How to Use This Skill

This skill is most suitable for chat and investigation. Below are sample prompts for each use-case.

### Chat

- "Create a health events report for the last 30 days, including breakdowns by service and event type."
- "Show me all open AWS Health events affecting my account."
- "Are there any scheduled maintenance events for RDS in the next 7 days?"
- "Summarize all EC2 health events from the past quarter."
- "What health events have occurred in us-west-2 this month?"
- "Give me a health posture summary for my account over the last 90 days."

### Investigation

- "Investigate the RDS connectivity alarm — are there any related AWS Health events?"
- "We're seeing elevated error rates on our ECS service. Check if there's an AWS-side issue."
- "Our Lambda functions are timing out. Is there an active Health event for Lambda in us-east-1?"
- "There's a latency spike on our ALB starting around 2am UTC — correlate with any Health events."
- "Multiple services in us-west-2 are degraded. Check for regional Health events."
- "The same EBS performance issue keeps recurring. Are there related Health events or scheduled changes?"
