# S3 Resiliency Review Skill

A skill for AWS DevOps Agent that performs a structured, **read-only** resiliency,
security, and data protection review of Amazon S3 buckets and produces a rated
report with prioritized findings and remediation guidance.

## What it does

Given one or more S3 bucket names, the skill collects each bucket's configuration
using read-only control-plane API calls and evaluates it across nine dimensions:

1. **Versioning** — protection against overwrites and deletes
2. **Replication** — cross-region / cross-account redundancy (four-quadrant risk model)
3. **Object Lock** — immutability / WORM protection
4. **Bucket policy** — defensive Deny statements and transport security
5. **Block Public Access** — bucket- and account-level, cross-referenced with ACLs and policy
6. **Default encryption** — SSE-S3 / SSE-KMS / DSSE-KMS and Bucket Key
7. **Ownership controls** — ACL posture and BucketOwnerEnforced migration
8. **Server access logging** — logging or CloudTrail S3 data events for audit trail
9. **Static website hosting** — public-by-design exposure checks

Each bucket receives a **Resiliency Rating** (High / Medium / Low / Indeterminate)
with per-dimension findings. Reviews are routed automatically:

- **1 bucket** → full single-bucket report
- **2–20 buckets** → fleet report (summary matrix + details)
- **21+ buckets** → batched fleet review with a manifest for progress tracking and resume

## Prerequisites

The DevOps Agent role must have **read-only** permissions for the review to
produce complete results. These are IAM action names (which differ from the API
call names for some S3 operations):

```
s3:ListBucket
s3:ListAllMyBuckets
s3:GetBucketVersioning
s3:GetReplicationConfiguration
s3:GetBucketObjectLockConfiguration
s3:GetBucketPolicy
s3:GetBucketPublicAccessBlock
s3:GetAccountPublicAccessBlock
s3:GetEncryptionConfiguration
s3:GetBucketOwnershipControls
s3:GetBucketAcl
s3:GetBucketLogging
s3:GetBucketWebsite
s3:GetBucketCORS
s3:GetBucketLocation
cloudtrail:DescribeTrails
cloudtrail:GetEventSelectors
```

(`sts:GetCallerIdentity` is also used to resolve the account ID; it requires no
IAM permission.)

Most of these are covered by `AIDevOpsAgentAccessPolicy`. If a check lacks
permission, the skill reports it as "Unable to verify — access denied" and caps the
Resiliency Rating at Medium rather than guessing the configuration.

The skill **never** reads object data (`GetObject`) and **never** performs any
write, create, update, or delete operation.

## How to use it with DevOps Agent

Works with the **Chat** and **Investigations / Incident RCA** subagents. Describe
the task in natural language — you do not need to name the skill:

- "Run an S3 resiliency review on `my-production-bucket`."
- "Is my bucket `app-data-prod` safe? Audit its security and data protection."
- "Review these buckets for resiliency: `logs-bucket`, `assets-bucket`, `backups-bucket`."
- "What's the disaster recovery posture of `analytics-raw`?"
- "Check versioning, replication, and public access on `customer-uploads`."

The agent gathers configuration via its `use_aws` tool under the assumed role in the
target account, applies the finding logic, and returns a Markdown report artifact.

## Agent Types

This skill is used by the following agent types (selected in the Operator Web App
at upload time):

- **Chat tasks** — conversational, on-demand reviews ("run an S3 resiliency review
  on `my-bucket`", "is `app-data-prod` safe?").
- **Evaluation** — proactive, best-practices resiliency reviews of a bucket or fleet
  against the nine dimensions.
- **Incident RCA** — automated root cause analysis where an S3 bucket's
  data-protection or public-access posture may be a contributing factor.

Select **Generic** instead if you want the skill available to all agent types.

## Uploading to AWS DevOps Agent

To deploy this skill to your Agent Space, you can use any of three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/storage-s3-resiliency-expertise` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository, including this one, even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Zip the `storage-s3-resiliency-expertise/` directory (only including allowed extensions):

   ```bash
   cd skills
   zip -r storage-s3-resiliency-expertise.zip storage-s3-resiliency-expertise/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
   ```

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the `storage-s3-resiliency-expertise.zip` file (max 6 MB).
5. Select the agent types: **Chat tasks**, **Evaluation**, and **Incident RCA**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign the skill to the `CHAT`, `EVALUATION`, and `INCIDENT_RCA` agent types. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

For more details, see [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) in the AWS DevOps Agent User Guide.

## Non-production disclaimer

> ⚠️ This skill is sample code, not intended for production use without additional
> review and testing. Users should validate in a non-production environment first.
