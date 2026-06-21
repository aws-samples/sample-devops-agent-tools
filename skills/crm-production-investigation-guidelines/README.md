# CRM Production Investigation Guidelines Skill (Sample)

This is a **sample skill** demonstrating how to write production investigation guidelines for the AWS DevOps Agent **Incident Triage** agent type. It shows how to encode application-specific troubleshooting knowledge — architecture details, incident isolation rules, and investigation procedures — into a skill that guides the agent during production incidents.

> **Note:** This skill is specific to a fictional CRM application and cannot be reused as-is. Use it as a template for writing similar investigation guideline skills for your own applications.

## Purpose

When a production incident occurs, investigation agents might need application-specific context that goes beyond generic AWS troubleshooting: what services make up the application, what the common failure modes are, how to isolate unrelated incidents, and what observability tools to use. This sample skill demonstrates how to encode that knowledge so the Incident Triage agent can conduct thorough, structured investigations without repeated human guidance.

## Key Capabilities (Demonstrated)

- Defining investigation isolation rules to prevent conflating unrelated incidents
- Specifying known incident titles and their corresponding failure domains
- Providing a structured investigation approach (symptoms → logs → audit trail → correlation → root cause → remediation)
- Documenting application architecture so the agent understands service relationships
- Listing common root cause patterns with specific AWS API calls to check

## Prerequisites

- AWS DevOps Agent space
- The target application's observability stack (CloudWatch Logs, CloudWatch Metrics, CloudTrail)
- IAM permissions for the agent to access CloudWatch, CloudTrail, and application-specific resources

## Limitations

- This skill is a sample for a fictional CRM application — it must be adapted to your own architecture before use
- Investigation guidance is only as effective as the observability data available to the agent
- The incident title matching logic assumes a fixed set of known alert titles

## Agent Types

This skill is designed for:

- **Incident Triage** — initial incident assessment and investigation

## Uploading to AWS DevOps Agent

To deploy this skill (or your adapted version) to your Agent Space, you can use any of three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/crm-production-investigation-guidelines` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository — including this one — even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Zip the skill directory (only including allowed extensions):

   ```bash
   cd skills
   zip -r crm-production-investigation-guidelines.zip crm-production-investigation-guidelines/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
   ```

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the zip file (max 6 MB).
5. Select the agent type: **Incident Triage**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign the skill to the `INCIDENT_TRIAGE` agent type. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

For more details, see [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) in the AWS DevOps Agent User Guide.

## How to Use This Skill

### As a Template

1. Copy the `SKILL.md` file as a starting point for your own application
2. Replace the CRM architecture section with your application's architecture
3. Replace the incident titles with your actual alert titles
4. Update the investigation approach with your organization's runbook procedures
5. Add common root cause patterns specific to your application

### Sample Incident Triage Prompts (for this demo application)

- "Investigate the SQS Message Backlog Spike alert"
- "We received an alert for High Lambda Error Rate, all invocations failing"
- "Triage the RDS Read Latency/CPU Utilization High incident"
- "What changed in the CRM Lambda function in the last hour?"
- "Check CloudTrail for IAM permission changes affecting the CRM application"
