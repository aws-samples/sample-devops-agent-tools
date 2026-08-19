# Service Specifics — Amazon Bedrock

Load only when the failing call is a Bedrock operation.

Bedrock is the service where "your IAM is correct" is most often the wrong answer. At
least four distinct causes produce `AccessDeniedException`, and only one of them is an
IAM policy gap. Rule out the others explicitly before concluding.

## Applicable hops

| Call | Hop 1 | Hop 2 | Hop 3 | Hop 4 | Hop 5 | Hop 6 |
|---|---|---|---|---|---|---|
| `InvokeModel`, `InvokeModelWithResponseStream` | ✓ | n/a | n/a | n/a | ✓ if custom model or cross-account | ✓ |
| `Converse`, `ConverseStream` | ✓ | n/a | n/a | n/a | ✓ if custom model or cross-account | ✓ |
| `ApplyGuardrail` | ✓ | n/a | n/a | n/a | ✓ | ✓ |
| Agent / knowledge-base creation | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Plain model invocation passes no role, so hops 2–4 are `NOT_APPLICABLE`, not passed.
Agents and knowledge bases do pass a service role and use the full chain.

**Hop 5 on a plain foundation-model call is `NOT_APPLICABLE`, not `NOT_EVALUATED`.** A
foundation model is AWS-owned and carries no customer resource policy, so there is nothing
that could have been read. Reserve `NOT_EVALUATED` for a hop whose evidence exists but was
not collected because an earlier hop denied. Hop 5 becomes applicable only when the request
involves a custom model, a provisioned-throughput resource, a guardrail, a customer-managed
KMS key, or a cross-account resource — each of which does have a policy worth reading.

## Actions and resource ARNs

| Operation | IAM action | Resource ARN shape |
|---|---|---|
| Invoke a foundation model | `bedrock:InvokeModel` | `arn:aws:bedrock:<region>::foundation-model/<model-id>` |
| Streamed invocation | `bedrock:InvokeModelWithResponseStream` | same as above |
| Converse API | `bedrock:Converse`, `bedrock:ConverseStream` | same as above |
| Invoke via inference profile | `bedrock:InvokeModel` **and** profile access | `arn:aws:bedrock:<region>:<account>:inference-profile/<id>` |
| Custom model | `bedrock:InvokeModel` | `arn:aws:bedrock:<region>:<account>:custom-model/<id>` |
| Provisioned throughput | `bedrock:InvokeModel` | `arn:aws:bedrock:<region>:<account>:provisioned-model/<id>` |
| Apply a guardrail | `bedrock:ApplyGuardrail` | `arn:aws:bedrock:<region>:<account>:guardrail/<id>` |

Note the foundation-model ARN has an **empty account field** — foundation models are
AWS-owned. A policy written with the caller's account ID in that position will not
match. This is a common authoring error worth checking when hop 1 shows an implicit
deny against a foundation model.

## Non-IAM cause 1 — Model access not enabled

The most common Bedrock denial that is not an IAM gap.

Model access is granted per model, per region, and per account. Without it,
`InvokeModel` returns `AccessDeniedException` even when the caller's IAM policy is
correct. AWS documents that if prerequisites are missing, the subscription attempt fails
and subsequent API calls return `AccessDeniedException`.

**How to check**
- `bedrock:GetFoundationModel` for the model ID and inspect availability.
- `bedrock:ListFoundationModels` for the region, and confirm the target model is present
  and usable.
- Look for the grant events listed below in CloudTrail.

**Finding body**
- verdict: `DENIED_BY` (service-specific)
- body: "Model access for `[model id]` is not enabled in `[region]`. Bedrock model access is granted per model, per region, per account, and is separate from IAM permissions — the caller's IAM policy can be entirely correct while the call still returns `AccessDeniedException`. Enable access for this model in `[region]`, then retry. Note that access enabled in one region does not apply to another."

## Non-IAM cause 2 — AWS Marketplace permissions

Applies to third-party models (Anthropic, Meta, Mistral, Cohere, AI21, and similar).

On first invocation of a third-party model, Bedrock automatically initiates an AWS
Marketplace subscription. If the calling role lacks Marketplace permissions, that
subscription fails, and the resulting error is an `AccessDeniedException` that looks
like a Bedrock permissions problem.

This is the highest-value finding in this file, because a diagnosis that checks only
`bedrock:*` actions will report the IAM configuration as correct and be wrong.

**How to check**
- Does the caller's policy include `aws-marketplace:Subscribe` and
  `aws-marketplace:ViewSubscriptions`?
- Is there an `aws-marketplace.amazonaws.com` `Subscribe` event in CloudTrail near the
  denial, and did it fail?
- Is the target model first-party (Amazon Titan, Nova) or third-party? First-party
  models do not use the Marketplace path.

**Finding body**
- verdict: `DENIED_BY` (service-specific)
- body: "`[model id]` is a third-party model. On first invocation Bedrock initiates an AWS Marketplace subscription automatically, and the calling principal `[principal ARN]` lacks the Marketplace permissions that requires — `[missing actions]`. The subscription fails and the invocation surfaces as `AccessDeniedException`, which is easily mistaken for a Bedrock IAM gap. Either grant the Marketplace permissions to the calling role, or have an administrator subscribe to the model once, after which the calling role no longer needs them."

## Non-IAM cause 3 — Propagation delay

Grants take effect asynchronously. AWS documents that after granting permissions it may
take up to approximately 2 minutes for a subscription to complete, and during that
window API calls may continue returning `AccessDeniedException`.

**Grant events to look for in CloudTrail**

| Event | Source | Meaning |
|---|---|---|
| `PutFoundationModelEntitlement` | `bedrock.amazonaws.com` | Model entitlement granted |
| `PutUseCaseForModelAccess` | `bedrock.amazonaws.com` | Use-case form submitted (required for Anthropic models) |
| `CreateFoundationModelAgreement` | `bedrock.amazonaws.com` | EULA accepted |
| `Subscribe` | `aws-marketplace.amazonaws.com` | Marketplace subscription created |

Emit the additive propagation finding from `finding-logic.md` when any of these appears
within 600 seconds before the denial. Do not suppress other findings.

## Non-IAM cause 4 — Region mismatch

Model access and IAM permissions are both region-scoped. A policy granting
`bedrock:InvokeModel` on a `us-east-1` foundation-model ARN does not authorize the same
model in `eu-west-1`.

**How to check** — compare the `awsRegion` on the CloudTrail denial against the region in
the resource ARN in the caller's policy.

**Finding body**
- verdict: `DENIED_BY` (service-specific)
- body: "The call was made in `[call region]`, but the caller's permission for `[model id]` is scoped to `[policy region]`. Bedrock permissions and model access are both per-region. Grant `[action]` on `arn:aws:bedrock:[call region]::foundation-model/[model id]` and enable model access in `[call region]`."

## Cross-region inference profiles

A cross-region inference profile routes a request to one of several regions. This
creates two requirements that are easy to miss.

**1. Both resource types must be permitted.** AWS documents that restricting a role to
specific inference profiles means listing both the inference profiles *and* the
foundation models in the `Resource` list. Permission on the profile alone is not enough
— the underlying foundation models must also be permitted, in every destination region
the profile can route to.

**2. An SCP blocking any destination region fails the whole request.** Per AWS: if any
destination region in a cross-region inference profile is blocked by an SCP, the request
fails even if the other regions remain allowed. This produces an intermittent-looking
denial that is actually deterministic per routing decision.

**How to check**
- `bedrock:GetInferenceProfile` for the profile and enumerate its destination regions.
- Confirm the caller's policy covers both the profile ARN and the foundation-model ARNs
  in every destination region.
- Check SCPs for region conditions such as `aws:RequestedRegion`.

**Finding body**
- verdict: `DENIED_BY` (service-specific)
- body: "The call used cross-region inference profile `[profile id]`, which can route to `[destination regions]`. `[Specific gap: the caller's policy covers the profile but not the foundation model in <region> | an SCP blocks <region>]`. A cross-region inference profile requires permission on both the profile ARN and the underlying foundation-model ARN in every destination region, and an SCP blocking any single destination region fails the request even when the others are permitted."

## Guardrails and KMS

- A call with a guardrail attached needs `bedrock:ApplyGuardrail` on the guardrail ARN
  in addition to the invocation permission.
- Custom models encrypted with a customer-managed key require `kms:Decrypt` on that key,
  and the key policy must permit the principal. If hop 5 shows a KMS key policy that
  omits the caller, that is the cause.
- Model invocation logging to S3 or CloudWatch Logs is configured with a service role;
  a failure there affects logging, not invocation, and should not be reported as the
  cause of an invocation denial.

## Curated hop-2 permissions — Bedrock agents and knowledge bases

For **Category B** of the proposed policy (commonly required, not observed). Applies to
the Bedrock service role for agents and knowledge bases, not to plain invocation.

| Purpose | Actions |
|---|---|
| Invoke the underlying model | `bedrock:InvokeModel` on the foundation-model ARN |
| Read knowledge-base source data | `s3:GetObject`, `s3:ListBucket` on the source prefix |
| Vector store access | The relevant OpenSearch Serverless or Aurora actions for the configured store |
| Decrypt encrypted sources | `kms:Decrypt` on the key protecting the source data |

Label these "commonly required — not observed" and narrow the resource ARNs before
proposing them.

## Diagnostic order for Bedrock

1. Hop 1 — caller's `bedrock:InvokeModel` on the correct region-scoped ARN
2. Hop 6 — SCP, including region conditions
3. Model access enabled for that model in that region
4. Marketplace permissions, if the model is third-party
5. Propagation, if a grant event precedes the denial
6. Region mismatch between the call and the policy
7. Inference-profile dual-resource and per-region requirements
8. Guardrail and KMS, if either is in the request

If hop 1 and hop 6 both indicate allow and none of items 3–8 applies, the root cause is
**undetermined**. Report it that way. Do not select the most plausible-looking hop and
present it as the answer.

## References

- Model access: https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html
- Model access permissions: https://docs.aws.amazon.com/bedrock/latest/userguide/model-access-permissions.html
- Bedrock IAM: https://docs.aws.amazon.com/bedrock/latest/userguide/security-iam.html
- Inference profile prerequisites: https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-prereq.html
- Inference profile regions and SCPs: https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
- Resolve AccessDeniedException: https://repost.aws/knowledge-center/bedrock-access-denied-exception
- Resolve Marketplace permission errors: https://repost.aws/knowledge-center/bedrock-resolve-marketplace-permission
- Simplified model access: https://aws.amazon.com/blogs/security/simplified-amazon-bedrock-model-access
