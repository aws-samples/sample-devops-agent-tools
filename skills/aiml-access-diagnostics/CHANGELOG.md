# Changelog

All notable changes to this skill are documented here. New entries go at the top.

## [1.2.0] - 2026-08-12

Architecture inversion, driven by running the skill against live denials in a real Agent
Space. Two of the operations the 1.1.0 design treated as central are not callable in the
DevOps Agent runtime, and the skill misdiagnosed that as its own misconfiguration.

### Changed

- **Policy reads are now the primary evidence; CloudTrail and simulation are optional
  corroboration.** The diagnosis stands without either. Every hop except the organization
  SCP decision is decidable from policy documents, and for two hops the documents are the
  *only* correct evidence: simulation cannot evaluate trust policies at all (hop 3), and at
  hop 2 it returns a false `implicitDeny` for correctly configured callers. Where a policy
  read and simulation disagree, the policy read now wins — the sole exception being
  `AllowedByOrganizations` at hop 6.
- **Removed the CloudFormation grant entirely.** `iam:SimulatePrincipalPolicy` was the only
  action this skill needed beyond `AIDevOpsAgentAccessPolicy`, and granting it changes
  nothing because the block is not in IAM. This skill now requires no IAM changes, and the
  repository's CloudFormation template is untouched by it.

### Fixed

- **A blocked operation is no longer reported as a missing permission.** Both
  `cloudtrail:LookupEvents` and `iam:SimulatePrincipalPolicy` are read-only, are granted in
  IAM, and sit inside the DevOps Agent permission guardrail — yet are refused before
  reaching AWS, apparently because their verbs are not `Get`, `List`, or `Describe`. The
  skill previously told users to deploy a CloudFormation template that had already been
  deployed and could not have helped. Introduced a `RuntimeUnavailable` status distinct from
  `AgentAccessDenied`, split the notice in two, and added a pre-render check that fails the
  report if it proposes a grant for a runtime-blocked operation.
- **Added the `WOULD_ALSO_DENY` verdict.** A run reported hop 4 as "Allows (unverified)" in
  the chain table while stating in the body that the job would fail again on S3. There was
  no vocabulary for a second defect below the root cause, so the report contradicted itself.
- **Closed the verdict vocabulary.** `SKILL.md` specified three verdicts while
  `report-format.md` defined five markers, and a run emitted `NOT_EVALUATED` as a heading
  verdict outside the documented set. The six tokens are now fixed in one place and enforced
  by a new pre-render check.
- **Removed a dependency on `s3:ListBucket`, which the agent does not have.**
  `svc-sagemaker.md` instructed verifying object existence with it. The finding never needed
  it: if the execution role lacks S3 list permission, that alone produces
  `ValidationException: No S3 objects found` whether or not the objects exist. The skill now
  states that reasoning and asserts neither presence nor absence.
- **Stopped inferring one operation's availability from another's failure.** After
  `s3:ListBucket` was refused, `s3:GetBucketPolicy` was assumed unavailable and hop 5 was
  reported undeterminable — while the bucket had a readable policy containing an
  `aws:SecureTransport` deny, exactly the pattern the skill instructs itself to look for.
  Every read the hop requires must now be attempted.
- **Each diagnosis now stands on its own evidence.** A run cited the account's SCPs as
  "established in earlier diagnoses this session" rather than reading them, which is not
  auditable, propagates any error in the earlier read, and may describe a configuration that
  has since changed. Findings must cite reads performed for the current diagnosis.
- **Nothing may follow the report but a one-line offer, and never a self-assessment.** Two
  runs appended a paragraph stating the diagnosis "worked end to end" and "nailed the
  deceptive case". Output discipline already barred a post-delivery summary; it now also
  bars grading the diagnosis, which lends unearned confidence to findings whose limitations
  the report has just enumerated.
- **`NOT_APPLICABLE` and `NOT_EVALUATED` are no longer interchangeable.** A run marked hop 5
  `NOT_EVALUATED` for a plain foundation-model call while explaining in the body that an
  AWS-owned model has no customer resource policy — which makes it `NOT_APPLICABLE`, since
  there was never anything to read. `svc-bedrock.md` now states this directly instead of
  leaving it inferable from the applicability matrix.
- Pre-render validation grew from 14 checks to 17.

## [1.1.0] - 2026-08-12

Corrections from end-to-end validation against live Bedrock and SageMaker denials. Each
item below is a case the 1.0.0 logic would have diagnosed incorrectly.

### Fixed

- CloudTrail event selection no longer filters on `AccessDenied` alone. Two of the four
  SageMaker failure modes return `ValidationException`, so an `AccessDenied`-only query
  found neither and would have concluded that no denial occurred.
- Hop 3 (role trust policy) now documents its real signal: `ValidationException` with
  "Could not assume role", not an access error code. Added as evidence guidance on the
  finding, and to the activation description so the skill triggers on it.
- Hop 4 create-time S3 failures now documented as `ValidationException` with "No S3
  objects found under S3 URL". SageMaker validates the input path using the *execution
  role*, so a role lacking `s3:ListBucket` causes an object that exists to be reported as
  absent. Verified with a control job differing only in S3 permissions. Previously this
  would have been diagnosed as missing data.
- `cloudtrail:LookupEvents` documented as region-scoped. It returns only events from the
  queried region even with a multi-region trail, so the region-mismatch cause this skill
  claims to diagnose was invisible when querying the default region alone.
- Model deprecation added as a non-IAM cause. A legacy model returns
  `ResourceNotFoundException` whose message begins "Access denied", which must not be
  diagnosed as a permissions gap.
- `iam:PassRole` simulation now requires an `iam:PassedToService` context entry. Without
  it the condition in AWS's own recommended scoping pattern cannot be satisfied, and the
  simulator returns `implicitDeny` for a correctly configured caller. Verified both ways
  against a live role. This was the most damaging defect found: it would have sent
  customers to add a permission they already held while the real cause at hop 3 went
  unreported. Hop 2 now refuses to emit a denial when condition keys were missing, and
  returns `CANNOT_DETERMINE` instead.
- `ResourceArns` documented as mandatory, with evidence that omitting it errs in both
  directions — an `Allow` on `*` with a resource-specific `Deny` simulates as `allowed`
  (false negative), while a region-scoped `Allow` simulates as `implicitDeny` (false
  positive, blaming hop 1).
- A denial carrying non-empty `MissingContextValues` is no longer treated as evidence of a
  permission gap anywhere in the finding logic.
- Frontmatter `description` condensed to fit the DevOps Agent upload limit of 1024
  characters, which rejects the skill outright when exceeded. All trigger and exclusion
  phrases were preserved.
- Corrected the zip command in `README.md` to archive the skill's contents rather than its
  parent directory, so `SKILL.md` lands at the root of the archive as the AWS DevOps Agent
  documentation requires. Wrapping the files in an `aiml-access-diagnostics/` prefix still
  uploads and still activates the skill, because the platform finds `SKILL.md` by scanning
  — but reference files are fetched by manifest path and every one fails with `Failed to
  get skill resource`, silently reducing the skill to `SKILL.md` alone. Added the expected
  archive layout, a verification step, and `-D` to omit extensionless directory entries.
- Reference links in `SKILL.md` changed from absolute GitHub URLs to relative paths. The
  absolute form made the agent attempt a remote fetch at runtime instead of reading the
  bundled files, so no reference ever loaded. Relative `.md` links do break
  `mkdocs build --strict`, but only in `README.md`, which the docs catalog copies into the
  site; `SKILL.md` and `references/` are never part of the docs build. The two consumers
  require opposite link styles.

## [1.0.0] - 2026-08-11

### Added

- Initial release. Read-only diagnosis of IAM and access failures for Amazon Bedrock and
  Amazon SageMaker calls.
- Six-hop authorization chain traversal: caller action, `iam:PassRole`, role trust policy,
  role permissions, resource policy, and organization SCP, with a fixed evaluation order
  and documented precedence rules.
- Three-state verdict model — `DENIED_BY`, `ALLOWED_BUT_UNVERIFIABLE`,
  `CANNOT_DETERMINE`. There is deliberately no verdict asserting a hop permits the call,
  since simulation is a model of the policies rather than proof of live behavior.
- Implicit versus explicit deny distinction, carried into the remediation: an explicit
  deny cannot be resolved by adding a permission.
- Separation of the two `iam:PassRole` failure modes — the caller's missing permission
  versus the role's trust policy — which present with nearly identical symptoms.
- Bedrock non-IAM denial causes: model access not enabled, AWS Marketplace permissions
  for third-party models, propagation delay, and region mismatch.
- Cross-region inference profile handling, including the requirement to permit both the
  inference profile and the underlying foundation models in every destination region, and
  the case where an SCP blocking a single destination region fails the entire request.
- Propagation-delay detection by correlating `PutFoundationModelEntitlement`,
  `PutUseCaseForModelAccess`, `CreateFoundationModelAgreement`, Marketplace `Subscribe`,
  and IAM policy-attachment events against the denial timestamp. Emitted as an additive
  finding that never suppresses the rest of the diagnosis.
- SageMaker execution-role coverage: the four downstream permission groups, the
  `ecr:GetAuthorizationToken` resource-scoping constraint, and the VPC-mode EC2 network
  interface requirements.
- Cross-account partial diagnosis: the caller side is verified and attributed by policy
  type, while the remote resource policy is reported as undeterminable with named checks
  for the remote account.
- Proposed policy output in two labelled categories — permissions derived from the
  observed failure, kept separate from permissions commonly required but not observed.
  Wildcard resources are never emitted.
- Distinction between an agent-side permission gap and a customer-side finding, so a read
  the agent could not perform is never reported as an absent configuration.
- Fourteen pre-render validation checks, including one that fails the report if any hop is
  asserted as definitively allowed, and one that fails it if a computed value was
  approximated rather than calculated.
- Mandatory AI-generated banner on every report, required because the output proposes IAM
  policy changes.
- Output discipline rules: no narration of API calls, plans, or reasoning, and no
  post-delivery summary that could be read in place of the report.
- User-facing error handling table with graceful degradation on every condition — a single
  failed read marks its hop and continues rather than aborting the diagnosis.
- CloudFormation grant for `iam:SimulatePrincipalPolicy`, which is not part of the
  `AIDevOpsAgentAccessPolicy` managed policy.

### Known limitations at release

- Bedrock and SageMaker only. Other AI/ML services are reported as unsupported rather
  than diagnosed generically.
- Service control policies carrying conditions are not evaluated by the IAM policy
  simulator, so a conditional SCP can deny a call this skill reports as permitted.
- Session policies applied at role assumption are not visible in a role's attached
  policies.
- Resource policies and SCPs in remote accounts are not readable; cross-account
  diagnosis covers the caller side only.
