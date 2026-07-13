# Service Quota Check

This skill enables the AWS DevOps Agent to check AWS service quota utilization during incident investigations, capacity planning, and before provisioning new resources.

## Purpose

When operational issues arise from hitting AWS service limits, or when recommendations involve provisioning additional resources, this skill checks current quota values and utilization. It proactively identifies quotas at risk (85%+ utilization), requests increases via the Service Quotas API when possible, and guides users to open support cases when programmatic increases are not available.

## Key Capabilities

- Retrieve current quota values and applied limits for any AWS service
- Calculate real-time utilization using CloudWatch metrics or resource counts
- Flag quotas at 85%+ utilization with risk-level assessment
- Submit quota increase requests via the Service Quotas API
- Check for existing pending increase requests to avoid duplicates
- Recommend support case creation for non-adjustable quotas
- Perform bulk quota assessments across all quotas for a service

## Prerequisites

- Agent permissions for Service Quotas APIs: `servicequotas:ListServices`, `servicequotas:ListServiceQuotas`, `servicequotas:GetServiceQuota`, `servicequotas:RequestServiceQuotaIncrease`, `servicequotas:ListRequestedServiceQuotaChangeHistory`, `servicequotas:CreateSupportCase`
- Agent permissions for CloudWatch: `cloudwatch:GetMetricData`, `cloudwatch:GetMetricStatistics`
- For resource counting fallback: read-only permissions on target services (e.g., `ec2:DescribeInstances`, `rds:DescribeDBInstances`)

## Limitations

- Service Quotas is a regional service; quotas must be checked in the correct region
- Not all quotas have a `UsageMetric` in CloudWatch; some require manual resource counting
- Some quotas are not adjustable via the API and require support cases
- Quota increase requests may take minutes (auto-approved) to days (manual review)
- Rate-based quotas (requests per second) require different monitoring than resource-count quotas

## Agent Types

This skill is used by the following agent types:

- **Chat tasks** — conversational quota lookup, capacity planning, and proactive checks
- **Incident RCA** — quota exhaustion as root cause during active incidents

## Uploading to AWS DevOps Agent

To deploy this skill to your Agent Space, you can use any of three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings -> Add Skill -> Import from repository, then point to the `skills/service-quota-check` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository — including this one — even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Zip the `service-quota-check/` directory (only including allowed extensions):

   ```bash
   cd skills
   zip -r service-quota-check.zip service-quota-check/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
   ```

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** -> **Upload skill**.
4. Drag and drop the `service-quota-check.zip` file (max 6 MB).
5. Select the agent types: **Chat tasks** and **Incident RCA**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign the skill to the `CHAT` and `INCIDENT_RCA` agent types. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

For more details, see [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) in the AWS DevOps Agent User Guide.

## How to Use This Skill

This skill is most suitable for chat and investigation. Below are sample prompts for each use-case.

### Chat

- "Check the EC2 vCPU quota utilization in us-east-1."
- "What's my current VPC quota and how many VPCs am I using?"
- "Show me all quotas for Lambda that are above 70% utilization."
- "Can I launch 50 more t3.large instances without hitting the quota?"
- "List all service quotas that are near their limits across EC2, VPC, and RDS."
- "Request an increase for my NAT Gateway quota in eu-west-1."

### Investigation

- "I'm getting LimitExceededException when creating a new VPC. Check quotas."
- "Lambda function invocations are being throttled. Check if we're hitting concurrent execution limits."
- "EC2 instance launch failed with InsufficientInstanceCapacity. Is this a quota issue?"
- "We need to scale our ECS cluster but tasks are failing to start. Check Fargate quotas."
- "The recommendation is to add more read replicas for RDS. Check if the quota allows it."
- "CloudFormation stack creation failed — investigate if we hit the stack count limit."
