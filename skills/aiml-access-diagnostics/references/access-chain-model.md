# Authorization Chain Model

The six-hop chain an AI/ML service call traverses, the order to evaluate it in, and
the precedence rules that decide the outcome. This document defines *what to check and
in what order*. It does not define how to collect the data (see
`data-collection.md`) or how to word findings (see `finding-logic.md`).

## Why a chain model is needed

An AI/ML `AccessDenied` surfaces at the caller, but the denial frequently originates
one hop away. A SageMaker `CreateTrainingJob` failure has at least four distinct
causes that produce nearly identical symptoms:

1. The caller lacks `sagemaker:CreateTrainingJob`
2. The caller lacks `iam:PassRole` for the execution role
3. The execution role's trust policy does not allow `sagemaker.amazonaws.com`
4. The execution role itself cannot read the input S3 prefix

Only the first is "the caller's permissions." The customer sees the same error class
for all four. Naming the hop is the diagnosis.

## The six hops

| Hop | Name | Question | Whose policy |
|---|---|---|---|
| 1 | Caller action | May the caller invoke this API at all? | Caller identity-based policy |
| 2 | PassRole | May the caller hand this role to the service? | Caller identity-based policy |
| 3 | Trust | Will the role accept this service as a principal? | Target role trust policy |
| 4 | Role permissions | Can the role reach its downstream dependencies? | Target role identity-based policy |
| 5 | Resource policy | Does the target resource permit this principal? | Resource-based policy |
| 6 | Organization | Does an SCP deny the action? | SCP |

Hops 2, 3, and 4 exist only when the call passes a role to a service. A Bedrock
`InvokeModel` call typically has no role-passing step, so hops 2–4 are marked **not
applicable** rather than passed.

## Traversal order

Evaluate in the order 1 → 6. Rationale:

- Hop 1 is cheapest to check and most commonly assumed to be the problem, so ruling it
  in or out early orients the rest of the diagnosis.
- Hops 2–4 are where most real failures live and require hop-1 context (which role is
  being passed) to evaluate.
- Hops 5 and 6 are the least visible and most often produce `CANNOT_DETERMINE`, so
  they come last where their absence does the least damage to the rest of the report.

**Do not stop at the first pass.** A passing hop 1 is the single most common reason a
diagnosis goes wrong — the caller's permissions look fine, so the investigation ends,
while the execution role is the actual problem. Continue through all applicable hops.

**Do stop descending on a definitive deny.** Once a hop yields `DENIED_BY` with an
explicit deny statement, later hops cannot change the outcome. Still report the
remaining hops as context if the data is already collected, marked
`NOT_EVALUATED — denied earlier at hop N`.

## Precedence rules

AWS policy evaluation, in the order that determines the result:

1. **Explicit deny wins, always.** An explicit `Deny` in any policy type overrides
   every `Allow`. When simulation returns matched statements and one is a deny, that
   deny is the only entry returned — treat its presence as conclusive.
2. **SCP must allow.** If an SCP does not permit the action, no identity or resource
   policy can grant it. Simulation surfaces this via
   `OrganizationsDecisionDetail.AllowedByOrganizations`.
3. **Permissions boundary must allow.** A boundary caps what identity policies can
   grant. It never grants on its own.
4. **Identity or resource policy must allow.** Within the same account, an allow in
   either is sufficient for most services. Across accounts, **both** the caller's
   identity policy and the resource policy must allow.
5. **Default is deny.** Absence of an allow is a denial, and it is an *implicit* deny.
   Distinguishing implicit from explicit matters: implicit means "add a permission,"
   explicit means "find and remove a deny," which are very different remediations.

## Implicit versus explicit deny

This distinction drives the recommendation and must appear in the report.

| | Implicit deny | Explicit deny |
|---|---|---|
| Cause | No statement allows the action | A statement denies it |
| CloudTrail wording | "is not authorized to perform" with no qualifier | "with an explicit deny in a(n) <policy type>" |
| Simulation | `implicitDeny` | `explicitDeny` with the deny statement in matched statements |
| Remediation | Add a scoped allow | Locate and amend the denying statement — adding an allow will not help |

When CloudTrail's `errorMessage` contains "with an explicit deny in", the message
names the policy type responsible. Quote it verbatim in the finding; it is the single
most useful string in the whole diagnosis.

## Cross-account handling

When the resource is in a different account from the caller:

| Element | Verifiable from here | Verdict |
|---|---|---|
| Caller's identity policy allows the remote resource ARN | Yes — simulation is in-account | `DENIED_BY` or verified allow |
| Which policy type produced the decision | Yes — `EvalDecisionDetails` returns per-type decisions for cross-account simulations | Attributable |
| SCP applicability in the caller's organization | Yes — `OrganizationsDecisionDetail` | Attributable |
| The remote resource policy's contents | **No** — not readable without credentials in the remote account | `CANNOT_DETERMINE` |
| SCPs in the remote account's organization | **No** | `CANNOT_DETERMINE` |

Diagnose the caller side definitively and state precisely what must be checked in the
remote account. Do not exclude cross-account requests, and do not report a whole-chain
verdict when half the chain is invisible.

## Applicability matrix

Which hops apply to which call shapes:

| Call shape | Hops 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| Bedrock `InvokeModel` / `Converse` | ✓ | n/a | n/a | n/a | ✓ if custom model or cross-account | ✓ |
| Bedrock agent or knowledge-base creation | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| SageMaker `CreateTrainingJob` / `CreateEndpoint` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| SageMaker execution role reaching S3, ECR, KMS | n/a | n/a | n/a | ✓ | ✓ | ✓ |

Mark inapplicable hops **not applicable** with the reason. Do not mark them as
passed — a hop that never ran did not pass, and conflating the two overstates
coverage.

## What this model cannot see

Carry these into the report's limitations section every time:

- **SCPs carrying conditions.** The policy simulator does not evaluate them, so an
  SCP with a condition can deny a call that simulation reports as allowed.
- **Resource policies in other accounts.** Not readable without credentials there.
- **Session policies and assumed-role session scoping.** A session policy passed at
  `AssumeRole` time narrows permissions and is not visible in the role's attached
  policies.
- **Service-specific authorization outside IAM.** Model subscriptions, marketplace
  entitlements, and per-service settings that are not IAM policies. These are covered
  per service in `svc-*.md` and are a common cause of "IAM looks fine but the call
  still fails."
- **Propagation timing.** A grant made moments ago may not be in effect yet.
