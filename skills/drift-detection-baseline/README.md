# Drift Detection Baseline

## Purpose

This skill provides AWS DevOps Agent with a defined baseline of expected production infrastructure state for drift detection workflows. It enables the agent to compare current resource configurations against organizational standards and identify deviations (drift) across security posture, compliance, and lifecycle dimensions.

Unlike a general best-practices audit, drift detection requires a **reference baseline** — a declared "expected state" to compare against. This skill serves as that baseline, providing the standards, verification methods, and severity framework the agent needs to systematically identify when production infrastructure has drifted from its intended configuration.

## ⚠️ Important Notice

This skill is provided as sample code. If you intend to deploy it in production, start with a non-production environment first, and before deploying to production:

- Review and customize the baseline policies to match your organization's standards
- Test thoroughly in a non-production environment
- Validate that the severity classifications align with your operational priorities
- Extend with organization-specific policies as needed

## Key Capabilities

- Defines expected state for encryption, network isolation, public access, tagging, logging, and backups
- Provides specific AWS API calls and fields to verify each policy
- Classifies drift findings by severity (critical, high, medium, low) based on security exposure and blast radius
- Includes AWS Config rule coverage gap analysis to identify policies lacking automated enforcement
- Designed to be extended with organization-specific policies

## Prerequisites

- An AWS DevOps Agent space
- IAM permissions for read-only access to inspect resources:
  - S3: `s3:GetEncryptionConfiguration`, `s3:GetBucketPublicAccessBlock`, `s3:GetBucketPolicy`, `s3:GetBucketTagging`
  - RDS: `rds:DescribeDBInstances`, `rds:DescribeDBClusters`, `rds:ListTagsForResource`
  - EC2: `ec2:DescribeVolumes`, `ec2:DescribeSecurityGroups`, `ec2:DescribeInstances`
  - Lambda: `lambda:ListFunctions`, `lambda:GetFunctionConfiguration`
  - EKS: `eks:DescribeCluster`, `eks:ListClusters`
  - DynamoDB: `dynamodb:DescribeTable`, `dynamodb:DescribeContinuousBackups`
  - Config: `config:DescribeConfigRules`, `config:DescribeConfigurationRecorders`
  - CloudTrail: `cloudtrail:DescribeTrails`, `cloudtrail:GetTrailStatus`
  - Resource Groups: `tag:GetResources`

## Limitations

- This skill defines a **sample baseline** — organizations should customize the policies, required tags, and severity thresholds to match their own standards
- Lifecycle drift detection (deprecated runtimes, EOL engine versions) requires knowledge of current AWS deprecation schedules, which change over time
- The skill covers foundational infrastructure policies but does not include application-level drift (latency thresholds, scaling policies, replication lag)
- AWS Config rule gap analysis relies on the agent's knowledge of available managed rules

## Agent Types

This skill is designed for use with:

- **All agents** — select "All agents" when importing to enable use by custom agents and built-in agents alike
- Primarily consumed by the [drift-detection custom agent](../../custom-agents/drift-detection/)
- Also useful for Chat tasks when asking about infrastructure compliance

## Uploading to AWS DevOps Agent

1. From the `skills/` directory, create a zip of the skill:
   ```bash
   cd skills
   zip -r drift-detection-baseline.zip drift-detection-baseline/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' -x '*/README.md' '*/.skilleval.yaml' '*/CHANGELOG.md' '*/evals/*'
   ```
2. In the DevOps Agent web app, navigate to **Skills** and click **Import skill**
3. Upload the zip file
4. Set **Agent Type** to "All agents" (required for custom agent usage)
5. Click **Import**

## How to Use This Skill

### With the Drift Detection Custom Agent (recommended)

This skill is designed to be loaded by the [drift-detection custom agent](../../custom-agents/drift-detection/). See that agent's README for setup and execution instructions.

### With Chat Tasks

You can reference this skill directly in chat:

- "Check my production resources for drift against the baseline policies"
- "Are there any S3 buckets that violate the encryption at rest policy?"
- "Which resources are missing required tags?"
- "Identify any resources running deprecated or end-of-life versions"
- "What AWS Config rules should I add to enforce the baseline policies?"

### Extending the Baseline

Create additional skills with your organization's specific policies:

- Regulatory requirements (HIPAA, PCI-DSS, SOC2)
- Application-specific thresholds (latency, replication lag, cost limits)
- Team conventions (naming standards, environment separation rules)

Load them alongside this baseline for comprehensive drift coverage.
