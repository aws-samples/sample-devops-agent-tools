# Enrich with AWS Security Agent

This skill enables the AWS DevOps Agent to query AWS Security Agent CloudWatch logs during incident investigations, surfacing code-level security findings that may be the root cause of application outages.

## ⚠️ Important Notice

This skill is provided as sample code. If you intend to deploy it in production, start with a non-production environment first. Review IAM permissions and security configurations against your organization's policies, and validate that skill behavior meets your operational requirements before production use.

## Purpose

When an application outage or degradation occurs without a clear infrastructure cause, the root cause may be a security vulnerability — such as SSRF, injection attacks, or compromised dependencies. This skill queries AWS Security Agent findings stored in CloudWatch Logs to retrieve actionable details (file path, line number, vulnerability type, severity) that customers can directly fix.

## Key Capabilities

- Query AWS Security Agent CloudWatch log groups for security findings
- Extract actionable vulnerability details: file path, line number, vulnerability type, severity
- Correlate security findings with current incident symptoms and timeframes
- Present remediation guidance with exact code locations
- Automatically activate when application errors have no obvious infrastructure cause

## Prerequisites

- AWS Security Agent configured and writing findings to CloudWatch Logs
- Agent permissions for `logs:DescribeLogGroups`, `logs:StartQuery`, `logs:GetQueryResults` on Security Agent log groups (e.g., `/aws/securityagent/*`)

## Limitations

- Requires AWS Security Agent to be configured and actively scanning — no findings will exist if the service is not enabled
- Log retention depends on CloudWatch Logs retention settings for the Security Agent log groups
- Findings are only as current as the last Security Agent scan

## Agent Types

This skill is used by the following agent types:

- **Chat tasks** — conversational security finding lookup and analysis
- **Incident RCA** — automated root cause analysis when security issues may cause outages

## Uploading to AWS DevOps Agent

To deploy this skill to your Agent Space, you can use any of three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/enrich-with-aws-security-agent` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository — including this one — even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Zip the `enrich-with-aws-security-agent/` directory (only including allowed extensions):

   ```bash
   cd skills
   zip -r enrich-with-aws-security-agent.zip enrich-with-aws-security-agent/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
   ```

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the `enrich-with-aws-security-agent.zip` file (max 6 MB).
5. Select the agent types: **Chat tasks** and **Incident RCA**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign the skill to the `CHAT` and `INCIDENT_RCA` agent types. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

For more details, see [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) in the AWS DevOps Agent User Guide.

## How to Use This Skill

### Chat

- "Check AWS Security Agent findings for my application — are there any critical vulnerabilities?"
- "Show me security findings related to SSRF or injection attacks in the last 7 days."
- "What security vulnerabilities has AWS Security Agent found in the orders service?"
- "Are there any hardcoded secrets or credential exposure findings?"

### Investigation

- "My application is returning 500 errors but there were no recent deployments. Investigate."
- "We're seeing unexplained outages on the checkout service — could it be a security issue?"
- "The API started failing with authentication errors. Check if there are security findings related to this."
- "Our web application is behaving unexpectedly and we suspect injection attacks. Investigate."
