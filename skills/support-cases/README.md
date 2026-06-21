# Support Cases Skill

This skill enables the AWS DevOps Agent to retrieve and analyze AWS Support cases during incident investigation, root cause analysis, and operational troubleshooting.

## Purpose

When an operational incident occurs, historical support cases often contain valuable context — prior root causes, proven remediations, and recurring patterns. This skill searches past AWS Support cases by service, time window, severity, and error keywords to surface relevant history that informs the current investigation.

## Key Capabilities

- Retrieve open and resolved AWS Support cases via the Support API
- Search and filter cases by service, severity, time range, and keywords
- Review case communications for root cause statements and remediation steps
- Correlate historical findings with current incident symptoms
- Identify recurring patterns that may require permanent fixes

## Prerequisites

- AWS account with Business Support+, Enterprise Support, or Unified Operations plan
- Agent permissions for `support:DescribeCases` and `support:DescribeCommunications`

## Limitations

- Support case data is only available for up to 24 months after creation

## Agent Types

This skill is used by the following agent types:

- **Chat tasks** — conversational support case lookup and analysis
- **Incident RCA** — root cause analysis during active incidents

## Uploading to AWS DevOps Agent

To deploy this skill to your Agent Space, you can use any of three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/support-cases` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository — including this one — even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Zip the `support-cases/` directory (only including allowed extensions):

   ```bash
   cd skills
   zip -r support-cases.zip support-cases/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
   ```

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the `support-cases.zip` file (max 6 MB).
5. Select the agent types: **Chat tasks** and **Incident RCA**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign the skill to the `CHAT` and `INCIDENT_RCA` agent types. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

For more details, see [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) in the AWS DevOps Agent User Guide.

## How to Use This Skill

This skill is most suitable for chat and investigation. Below are sample prompts for each use-case

### Chat

- "Create a support cases report for all support cases in the last 12 months, including breakdowns by service and severity"
- "Show me all support cases opened in the last 30 days for RDS."
- "What was the resolution for case-123456789010-muen-2024?"
- "Are there any open critical-severity cases in this account?"
- "Summarize the communications on our most recent EC2 support case."
- "Have we filed any support cases related to Lambda throttling this quarter?"

### Investigation

- "Investigate the Lambda-Errors alarm"
- "We're seeing 5xx errors on our ALB — can you investigate?"
- "RDS connections are maxing out and queries are timing out. What's going on?"
- "There's a latency spike on our ECS service starting around 3am UTC."
- "Our last deployment triggered elevated error rates across multiple endpoints."
- "The same DynamoDB throttling alarm fired again. Help me figure out why this keeps happening."
