# AI/ML Access Diagnostics Skill

A skill for AWS DevOps Agent that diagnoses **why** an AI/ML service call was denied.
It walks the authorization chain hop by hop, names the hop that denied the call, and
proposes a scoped IAM policy for human review. Strictly **read-only**.

## Purpose

An AI/ML `AccessDenied` surfaces at the caller, but the denial usually originates one hop
away. A SageMaker `CreateTrainingJob` failure has at least four causes that look
identical to the customer:

- the caller lacks `sagemaker:CreateTrainingJob`
- the caller lacks `iam:PassRole` for the execution role
- the execution role's trust policy does not allow `sagemaker.amazonaws.com`
- the execution role itself cannot read the input S3 prefix

Only the first is "the caller's permissions." Bedrock adds a further complication:
several of its most common denials are not IAM gaps at all — model access not enabled,
AWS Marketplace permissions missing for a third-party model, or a grant that has not
propagated yet.

Debugging this blind tends to end in over-granting permissions until something works.
This skill names the specific hop and the specific missing action instead.

## Key Capabilities

- **Six-hop chain traversal** — caller action, `iam:PassRole`, role trust policy, role
  permissions, resource policy, and organization SCP, evaluated in a fixed order
- **Distinguishes implicit from explicit deny** — the remediations are entirely different,
  and adding a permission cannot resolve an explicit deny
- **Separates the two PassRole failure modes** — the caller's missing `iam:PassRole` and
  the role's trust policy are different problems with the same symptom
- **Rules out non-IAM causes explicitly** — Bedrock model access, Marketplace
  subscription, propagation timing, and region mismatch
- **Cross-region inference profile handling** — including the requirement to permit both
  the profile and the underlying foundation models, and the case where an SCP blocking a
  single destination region fails the whole request
- **Propagation-delay detection** — correlates recent grant events in CloudTrail against
  the denial timestamp
- **Three-state verdicts** — `DENIED_BY`, `ALLOWED_BUT_UNVERIFIABLE`, `CANNOT_DETERMINE`,
  so an unreadable policy is never reported as an absent one
- **Proposed policy in two labelled categories** — permissions derived from the observed
  failure, kept separate from permissions that are commonly required but were not observed

## Prerequisites

### IAM Permissions

**No IAM changes are required.** Everything this skill depends on is already granted by the
[`AIDevOpsAgentAccessPolicy`](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AIDevOpsAgentAccessPolicy.html)
managed policy: the IAM read actions, `organizations:Describe*` and `List*`,
`bedrock:Get*`/`List*`, `sagemaker:Describe*`/`List*`, `kms:GetKeyPolicy`,
`s3:GetBucketPolicy`, and `ecr:GetRepositoryPolicy`. `sts:GetCallerIdentity` needs no
permission at all.

There is no CloudFormation template to deploy for this skill.

### Runtime constraints you may observe

Two read-only operations the skill would like to use are not callable in the DevOps Agent
runtime. Both are permitted by IAM and sit inside the agent's
[permission guardrail](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-limiting-agent-access-in-an-aws-account.html),
but are refused before the call reaches AWS — the observed pattern is that operations whose
verb is not `Get`, `List`, or `Describe` are treated as potentially mutating.

| Operation | Behaviour | What is lost |
|---|---|---|
| `cloudtrail:LookupEvents` | Requires operator approval per call | Independent confirmation of the failure event, the passed `RoleArn` and any `VpcConfig` from `requestParameters`, and propagation-delay detection |
| `iam:SimulatePrincipalPolicy` | Refused | `AllowedByOrganizations` at hop 6 only |

**Granting these actions does not enable them**, so the skill never asks you to. It reports
them as an environment characteristic and continues on policy reads, which decide hops 1
through 5 regardless — and which are the *only* correct evidence for the trust policy at
hop 3, since simulation cannot evaluate trust policies, and for `iam:PassRole` at hop 2,
where simulation returns a false denial for correctly configured callers.

If your environment does permit them, the skill uses them as corroboration automatically.

### AWS Resources

- An actual failure to diagnose — an error message, or a principal plus the API call that
  failed. Pasting the error verbatim gives the best result.
- CloudTrail is optional. When available it adds corroboration; when not, the diagnosis
  proceeds from the error text and the policy documents.

## Limitations

- **Two services only.** Amazon Bedrock and Amazon SageMaker. Other AI/ML services are
  reported as unsupported rather than diagnosed generically — the value is in the
  service-specific knowledge, and without it the output would be a guess.
- **No verdict asserts success.** The strongest available verdict is
  `ALLOWED_BUT_UNVERIFIABLE`. Reading a policy that permits an action cannot account for
  session policies, SCPs carrying conditions, or service-side gates outside IAM.
- **Hop 6 is weaker without simulation.** The SCP documents are read and evaluated by hand,
  but the authoritative `AllowedByOrganizations` decision requires
  `iam:SimulatePrincipalPolicy`, which this runtime refuses. A conditional SCP can deny a
  call the skill reports as permitted.
- **SCPs carrying conditions are not evaluated** by the simulator, so a conditional SCP
  can deny a call this skill reports as permitted.
- **Session policies are invisible.** A policy passed at `AssumeRole` time narrows
  permissions and does not appear in the role's attached policies.
- **Cross-account is diagnosed on one side only.** The caller side is verifiable; a
  resource policy or SCP in the remote account is not readable. The skill names precisely
  what must be checked there.
- **CloudTrail delivery can lag** up to approximately 15 minutes, so a very recent call
  may not appear yet.
- **Reactive, not proactive.** This diagnoses failures. It is not a least-privilege audit
  and will decline a request with no failure to explain.
- **Read-only.** It proposes a policy; it never applies one. Proposed policies are not
  validated against your workload and need their resource scoping narrowed before use.
- **Diagnostic output contains identifiers.** Principal ARNs, account IDs, role names,
  resource ARNs, and CloudTrail error messages appear in the report. That is metadata
  rather than customer data, but treat the output with the same sensitivity as your IAM
  configuration.

## Agent Types

This skill is used by the following agent types (selected in the Operator Web App at
upload time):

- **Chat tasks** — interactive diagnosis of a specific access failure
- **Incident RCA** — automated root cause analysis where an AI/ML permission failure may
  be a contributing factor

Select **Generic** instead if you want the skill available to all agent types.

## Uploading to AWS DevOps Agent

To deploy this skill to your Agent Space, you can use any of three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/aiml-access-diagnostics` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository, including this one, even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Zip the skill's **contents**, so that `SKILL.md` sits at the root of the archive:

   ```bash
   cd skills/aiml-access-diagnostics
   zip -rD ../../aiml-access-diagnostics.zip . \
     -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' \
     -x './README.md' './CHANGELOG.md' './.skilleval.yaml' './.skilleval.yml' './evals/*' './.claude/*' './scripts/*'
   ```

   The resulting archive must look like this, with `SKILL.md` at the top level:

   ```text
   aiml-access-diagnostics.zip
   ├── SKILL.md
   └── references/
       ├── access-chain-model.md
       ├── data-collection.md
       ├── finding-logic.md
       ├── report-format.md
       ├── svc-bedrock.md
       └── svc-sagemaker.md
   ```

   Verify before uploading:

   ```bash
   unzip -l ../../aiml-access-diagnostics.zip
   ```

   > **Do not zip the parent directory.** Running `zip -r skill.zip aiml-access-diagnostics/`
   > from `skills/` wraps every file in an `aiml-access-diagnostics/` prefix. The upload
   > still succeeds and the skill still activates, because the platform locates `SKILL.md`
   > by scanning the archive — but reference files are retrieved by their manifest path
   > (`references/access-chain-model.md`), which no longer matches the stored path. Every
   > reference then fails with `Failed to get skill resource`, and the skill runs on
   > `SKILL.md` alone with no error surfaced at upload time. The `-D` flag omits directory
   > entries, which carry no file extension and can trip the extension validator. See
   > [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills)
   > for the required structure.

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the `aiml-access-diagnostics.zip` file (max 6 MB).
5. Select the agent types: **Chat tasks** and **Incident RCA**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign the skill to the `CHAT` and `INCIDENT_RCA` agent types. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

For more details, see [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) in the AWS DevOps Agent User Guide.

## How to Use This Skill

Describe the failure in natural language. You do not need to name the skill. Pasting the
error message verbatim gives the best result, because the error string carries the
principal, action, and resource.

### Chat

```
"Bedrock InvokeModel is returning AccessDeniedException for claude-3-5-sonnet in us-east-1"

"User: arn:aws:sts::111122223333:assumed-role/app-role/session is not authorized to
 perform: bedrock:InvokeModel on resource: arn:aws:bedrock:us-east-1::foundation-model/
 anthropic.claude-3-5-sonnet-20241022-v2:0"

"My SageMaker training job fails with AccessDenied — why?"

"is not authorized to perform: iam:PassRole on resource: arn:aws:iam::111122223333:role/
 sagemaker-execution-role"

"Why can't my SageMaker execution role read from the training data bucket?"
```

### Incident RCA

```
"The inference service started failing at 14:20 with AccessDenied — is this a permissions change?"

"Correlate these Bedrock AccessDeniedException errors with any recent IAM changes"
```

### What you get back

A report naming the root-cause hop, a verdict for each of the six hops, the distinction
between implicit and explicit deny, any non-IAM causes found, a proposed policy in two
clearly separated categories, and an explicit statement of what the diagnosis could not
determine.

## Non-production disclaimer

> ⚠️ This skill is sample code, not intended for production use without additional review
> and testing. Validate in a non-production environment first. Proposed IAM policies are
> suggestions derived from observed evidence — review and narrow them before applying, and
> never apply an IAM change you have not read.
