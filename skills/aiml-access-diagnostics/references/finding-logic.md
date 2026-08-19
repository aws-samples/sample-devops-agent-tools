# Finding Logic

Verdict assignment and finding text for each hop. Applied against the structured object
from `data-collection.md`. No API calls happen here — all evidence is pre-collected.

Use the body templates verbatim, substituting only the bracketed placeholders.

## The verdict vocabulary

**This list is closed.** Every hop in the chain table and every finding heading uses one
of these six tokens and no others. Inventing a token — or writing a verdict as prose in
place of one — fails pre-render validation.

Four verdicts for hops that were evaluated:

| Verdict | Emoji | Assign when |
|---|---|---|
| `DENIED_BY` | ❌ | Evidence shows this hop denied the call |
| `WOULD_ALSO_DENY` | ❌ | This hop would deny too, but an earlier hop is the operative cause |
| `ALLOWED_BUT_UNVERIFIABLE` | ⚠️ | Evidence indicates allow, but something outside our view could still deny |
| `CANNOT_DETERMINE` | ❓ | Required evidence was unavailable — always names what was missing |

Two markers for hops that were not evaluated:

| Marker | Assign when |
|---|---|
| `NOT_APPLICABLE` | The call shape does not include this hop (e.g. PassRole on `InvokeModel`) |
| `NOT_EVALUATED` | A prior hop denied and this hop's evidence was not collected |

### `WOULD_ALSO_DENY` — why it exists

A denial at hop 3 does not make hops 4 through 6 irrelevant. Their evidence is often
already in hand, and a second defect there means the user's call still fails after
fixing the first. Reporting that is valuable.

Without this verdict the report has no honest way to say it. Marking such a hop
`ALLOWED_BUT_UNVERIFIABLE` while the body text explains that it will fail is a direct
self-contradiction, and it has occurred: a run reported hop 4 as "Allows (unverified)"
in the chain table while stating in the body that the job would fail again on S3.

Assign `WOULD_ALSO_DENY` when the evidence for a hop below the denying hop independently
shows a denial. Keep the root cause on the **earliest** denying hop, and say plainly in
the finding that this one is a subsequent blocker rather than the current cause. If a
hop below the denial was simply not investigated, it is `NOT_EVALUATED`, not this.

### Why there is no plain "ALLOWED"

There is no verdict asserting the hop permits the call. The strongest available evidence
is that the policy documents we could read indicate an allow — which cannot account for
session policies, conditional SCPs, or service-side gates outside IAM. That is
`ALLOWED_BUT_UNVERIFIABLE`.

Collapsing it into "allowed" is the primary way this skill produces a wrong answer:
reporting the caller's permissions as correct when an SCP with conditions, a session
policy, or a service-specific gate is the real cause.

### Verdict assignment from policy reads

Policy documents are the primary evidence. Assign from what the documents say, evaluated
against the action, the resource ARN including its account and region fields, and every
condition key the failing call actually supplied.

| Policy-read outcome | Verdict |
|---|---|
| A matching `Deny` statement applies | `DENIED_BY` (explicit) |
| No statement grants the action on that resource | `DENIED_BY` (implicit) |
| A grant exists but a condition key is unmet | `DENIED_BY`, naming the condition |
| A grant exists, matches the resource, conditions satisfied | `ALLOWED_BUT_UNVERIFIABLE` |
| A grant exists but whether a condition holds cannot be established | `CANNOT_DETERMINE`, naming the key |
| The policy could not be read (`AgentAccessDenied`) | `CANNOT_DETERMINE`, naming the operation |
| The read is not callable in this environment (`RuntimeUnavailable`) | `CANNOT_DETERMINE`, naming the operation and stating no grant fixes it |

### Corroboration from simulation, when available

Simulation is optional and frequently unavailable. When it did run, use it to corroborate
a policy read — never to overturn one.

| `EvalDecision` | Additional signal | Effect |
|---|---|---|
| `explicitDeny` | — | Corroborates `DENIED_BY` (explicit) |
| `implicitDeny` | `missing_context_values` empty | Corroborates `DENIED_BY` (implicit) |
| `implicitDeny` | `missing_context_values` non-empty | **Discard.** Not evidence of anything. |
| `allowed` | `allowed_by_organizations` is `false` | `DENIED_BY` (SCP) at hop 6 — the one thing only simulation shows |
| `allowed` | clean | Corroborates `ALLOWED_BUT_UNVERIFIABLE` |

**Where simulation and a policy read disagree, the policy read wins**, with one exception:
`allowed_by_organizations` at hop 6, which policy reading cannot compute.

This ordering is not a preference. For hop 2 simulation is measurably wrong on correctly
configured callers unless `iam:PassedToService` is supplied, and for hop 3 it cannot
evaluate trust policies at all. See `data-collection.md`.

If CloudTrail was available and shows a denial while the policies read as an allow, the
hop is `CANNOT_DETERMINE` with the divergence stated explicitly — never
`ALLOWED_BUT_UNVERIFIABLE`. That divergence is itself a valuable finding, because it means
the cause lies outside what the readable policies model.

## Hop 1 — Caller action

**Input:** `hops.caller_action`, `simulation.results` for the failed action,
`cloudtrail.denials[].deny_kind`.

**Explicit deny:**
- verdict: `DENIED_BY`
- body: "The caller `[principal ARN]` is explicitly denied `[action]` on `[resource]`. The denial comes from a `[policy type]` policy — CloudTrail reports: `[verbatim errorMessage]`. An explicit deny overrides every allow, so **adding a permission will not resolve this**. Locate and amend the denying statement. Matched statement: `[statement id or index]` in `[policy ARN or inline policy name]`."

**Implicit deny:**
- verdict: `DENIED_BY`
- body: "The caller `[principal ARN]` has no policy allowing `[action]` on `[resource]`. This is an implicit denial — nothing forbids the action, but nothing permits it either. Adding a scoped allow resolves this. See the proposed policy below."

**Allowed, clean:**
- verdict: `ALLOWED_BUT_UNVERIFIABLE`
- body: "The caller `[principal ARN]` is permitted `[action]` on `[resource]` by `[policy ARN or inline policy name]`. This hop is not the cause. Continue to the hops below — when the caller's own permissions are correct, the denial usually originates in the role the call passes to the service, or in a non-IAM gate."

**Allowed, but missing context values:**
- verdict: `CANNOT_DETERMINE`
- body: "Simulation reports the caller is permitted `[action]`, but the evaluation was incomplete: the policies reference condition keys that were not supplied — `[missing context values]`. The live result depends on the values of those keys at call time. Treat this hop as undetermined."

**Simulation says allowed but CloudTrail shows a denial:**
- verdict: `CANNOT_DETERMINE`
- body: "Simulation indicates the caller is permitted `[action]`, yet CloudTrail records an `AccessDenied` at `[event time]`. The cause therefore lies outside what the policy simulator evaluates — candidates are an SCP carrying conditions, a session policy applied at role assumption, or a service-specific gate outside IAM. See the service-specific findings and limitations below."

## Hop 2 — PassRole

Applies only when the call passes a role to a service.

**Before emitting any hop-2 denial:** confirm the simulation supplied an
`iam:PassedToService` context entry naming the service that receives the role, and that
`MissingContextValues` came back empty. A `PassRole` simulation run without that key
returns `implicitDeny` for correctly configured callers, because AWS's recommended
scoping pattern puts a condition on exactly that key. Re-simulate with it before
concluding anything. If the receiving service cannot be determined, this hop is
`CANNOT_DETERMINE`.

Emitting a false hop-2 denial is the most damaging error this skill can make: it sends
the customer to add a permission they already hold, and the real cause — usually the
trust policy at hop 3 — is never reported.

**Explicit or implicit deny (with context keys supplied and no missing values):**
- verdict: `DENIED_BY`
- body: "The caller `[principal ARN]` is not permitted `iam:PassRole` for `[role ARN]`. The caller has permission to invoke `[action]`, but handing a role to a service is a separate permission, and it is missing. This is one of two distinct PassRole failures — this one is on the **caller**. The other is the role's trust policy, evaluated at hop 3."

**Denied, but condition keys were missing from the simulation:**
- verdict: `CANNOT_DETERMINE`
- body: "The simulation returned a denial for `iam:PassRole` on `[role ARN]`, but it reported missing condition keys: `[MissingContextValues]`. The caller's policy scopes `PassRole` with a condition, and the simulation could not evaluate it, so this denial is not reliable. Confirm the value of `[key]` for this call before treating hop 2 as the cause."

**Allowed:**
- verdict: `ALLOWED_BUT_UNVERIFIABLE`
- body: "The caller is permitted `iam:PassRole` for `[role ARN]`, scoped by `[policy ARN or inline policy name]`. Note that `PassRole` succeeding does not mean the role will accept the service — that is hop 3."

**Not applicable:**
- marker: `NOT_APPLICABLE`
- body: "`[action]` does not pass a role to a service, so no `iam:PassRole` permission is required."

## Hop 3 — Trust policy

**Service principal not permitted:**
- verdict: `DENIED_BY`
- body: "The role `[role ARN]` does not trust `[service principal]`. Its trust policy permits: `[list of principals found]`. Even with `iam:PassRole` granted on the caller, the service cannot assume this role. Add `[service principal]` to the role's trust policy. This is the second of the two PassRole failure modes and is frequently mistaken for the first — the symptoms are nearly identical."
- evidence note: this hop does **not** produce an `AccessDenied`. It surfaces as `ValidationException` with "Could not assume role `[role ARN]`". Treat that message as direct evidence for this finding and cite it. Its absence from an `AccessDenied`-filtered CloudTrail query is not evidence that this hop passed.

**Service principal permitted:**
- verdict: `ALLOWED_BUT_UNVERIFIABLE`
- body: "The role `[role ARN]` trusts `[service principal]`. Trust policy conditions present: `[conditions or 'none']`. If conditions are present, verify their values hold for this call — a trust policy with an unmet `sts:ExternalId` or `aws:SourceArn` condition denies assumption while appearing correctly configured."

**Trust policy unreadable:**
- verdict: `CANNOT_DETERMINE`
- body: "The trust policy for `[role ARN]` could not be read: `[status]`. Whether the role accepts `[service principal]` is unknown. Re-run with `iam:GetRole` permission on that role for a complete diagnosis."

## Hop 4 — Role permissions

The role's ability to reach its downstream dependencies. This hop is where most
correctly-configured callers still fail.

**Downstream action denied:**
- verdict: `DENIED_BY`
- body: "The role `[role ARN]` cannot perform `[action]` on `[resource]`. The caller and role-passing configuration are correct, but the role itself lacks a permission it needs at runtime. Denied actions: `[list]`. This failure surfaces to the caller as a generic `AccessDenied`, which is why it is commonly misdiagnosed as a caller-permission problem."

**All checked downstream actions permitted:**
- verdict: `ALLOWED_BUT_UNVERIFIABLE`
- body: "The role `[role ARN]` is permitted the downstream actions checked: `[list]`. Note that this list is the set commonly required for `[operation]`, not an exhaustive inventory of what your specific workload needs. A dependency outside this list would not appear here."

**Role's policies unreadable:**
- verdict: `CANNOT_DETERMINE`
- body: "The policies attached to `[role ARN]` could not be enumerated: `[status]`. The role's downstream permissions are unknown, and this hop is the most common source of AI/ML access failures — so an undetermined result here materially limits the diagnosis."

## Hop 5 — Resource policy

**Resource policy denies:**
- verdict: `DENIED_BY`
- body: "The resource policy on `[resource ARN]` denies `[principal ARN]`. Statement: `[sid or index]`. Resource-policy denials are independent of the caller's identity permissions — the caller can be fully permitted and still be refused here."

**No resource policy exists:**
- verdict: `ALLOWED_BUT_UNVERIFIABLE`
- body: "No resource policy is attached to `[resource ARN]`, so none denies this call. Within a single account an identity-based allow is sufficient."

**Resource policy exists and permits:**
- verdict: `ALLOWED_BUT_UNVERIFIABLE`
- body: "The resource policy on `[resource ARN]` permits `[principal ARN]` via statement `[sid or index]`."

**Resource policy unreadable:**
- verdict: `CANNOT_DETERMINE`
- body: "The resource policy on `[resource ARN]` could not be read: `[status]`. Whether it permits or denies `[principal ARN]` is unknown. This is **not** the same as no policy being present — an unreadable policy may contain a deny."

## Hop 6 — Organization SCP

**SCP denies:**
- verdict: `DENIED_BY`
- body: "An organization service control policy denies `[action]` for this account. Simulation reports `AllowedByOrganizations: false`. No identity-based or resource-based policy can override an SCP denial — the change must be made in the organization's policy, typically by an administrator in the management account. Policies applied to this account: `[list]`."

**SCP permits:**
- verdict: `ALLOWED_BUT_UNVERIFIABLE`
- body: "Simulation reports `AllowedByOrganizations: true` for `[action]`. Caveat: the policy simulator does not evaluate SCPs that carry conditions, so a conditional SCP could still deny this call without appearing here."

**Not in an organization:**
- marker: `NOT_APPLICABLE`
- body: "This account is not a member of an AWS Organization, so no SCP applies."

**SCP data unreadable:**
- verdict: `CANNOT_DETERMINE`
- body: "Organization policies could not be read: `[status]`. SCP involvement is undetermined."

## Additive finding — propagation delay

Emitted **in addition to** all hop findings, never instead of them. A possible
propagation delay does not excuse skipping the diagnosis.

**Trigger:** a grant event in `cloudtrail.grant_events` occurring within 600 seconds
before the earliest denial, targeting the same principal, role, or model.

- severity: informational
- body: "⏳ **Possible propagation delay.** `[grant event name]` for `[target]` was recorded at `[grant time]`, `[N]` seconds before the denial at `[denial time]`. Access grants can take up to approximately 2 minutes to take effect, and Bedrock model subscriptions may continue returning `AccessDeniedException` during that window. Retry the call before treating this as a configuration gap. If it still fails after 2 minutes, the findings below apply as written."

**Window rationale:** the trigger uses 600 seconds rather than 120 because CloudTrail
delivery latency can reach ~15 minutes, so the recorded timestamps of the grant and
the denial can be skewed relative to each other. A tight window would miss real cases.

**Do not** suppress, soften, or defer other findings because this fired. Report both.

## Cross-account

When `request.cross_account` is true, hops 1, 2, and 6 remain fully diagnosable —
simulation is in-account and `EvalDecisionDetails` returns a decision per policy type
for cross-account simulations. Hops 3, 4, and 5 concerning constructs in the remote
account are not readable.

Render as a paired finding:

- body: "**Caller side — verified.** `[principal ARN]` [is permitted / is denied] `[action]` on `[resource ARN]` by `[policy type]`. `AllowedByOrganizations: [value]`.

  **Remote side — cannot be determined.** `[resource ARN]` resides in account `[account id]`. Its resource policy and any SCP in its organization are not readable from account `[caller account]`. Verify in `[account id]`: (1) the resource policy grants `[principal ARN]` the `[action]` action, (2) no SCP in that organization denies it, and (3) for a role-based flow, the role's trust policy permits the calling principal.

  Cross-account calls require **both** an identity-based allow here and a resource-based allow there. A verified caller side is necessary but not sufficient."

## Incomplete-diagnosis notice

Two different things can limit a diagnosis, they have different remedies, and conflating
them produces a false remediation. Render whichever applies as a notice above the
findings, and never let either masquerade as a customer-side result.

### Runtime restriction — `RuntimeUnavailable`

The environment refused or deferred the operation before it reached AWS. **No permission
grant fixes this.** Do not recommend one.

- body: "⚠️ **Partial diagnosis — operations unavailable in this environment.** The following read-only operations were not callable here: `[list of operations]`. This is a characteristic of the runtime, **not** a permission gap and **not** a finding about your configuration. Granting these actions to the agent role would not change the outcome, and no CloudFormation template or policy change enables them. `[Affected hops]` were evaluated from policy documents only, which is the primary evidence path for every hop except the organization SCP decision."

Applies in particular to:

| Operation | What is lost |
|---|---|
| `cloudtrail:LookupEvents` | Independent corroboration of the failure event, `requestParameters` (the passed `RoleArn`, any `VpcConfig`), and propagation-delay detection |
| `iam:SimulatePrincipalPolicy` | `AllowedByOrganizations` at hop 6 only. Hops 1 through 5 are decided by policy reads regardless. |

State what was lost in those terms. Do not imply the diagnosis is unreliable — for hops 1
through 5 the policy documents are the stronger evidence, and for hop 2 and hop 3 they are
the only correct evidence.

### Agent IAM gap — `AgentAccessDenied`

An AWS API returned `AccessDenied` on the agent's own read. This one **is** a permission
gap and a grant would fix it.

- body: "⚠️ **Incomplete diagnosis — the agent lacks a required permission.** These reads returned `AccessDenied` for the agent itself: `[list of operations]`. Affected hops are reported as `CANNOT_DETERMINE`. This is about the agent's own permissions, not a finding about your environment. Granting the agent role these read actions and re-running would complete those hops."

Never merge the two notices, and never attribute a `RuntimeUnavailable` operation to a
missing grant. Both `cloudtrail:LookupEvents` and `iam:SimulatePrincipalPolicy` sit inside
the agent's permission guardrail and can be granted in IAM while remaining uncallable —
so a report claiming they are "not granted" is wrong on the facts.

## Root cause selection

The report names one root cause. Select it as follows:

1. The **first** hop in traversal order with verdict `DENIED_BY`.
2. If several hops deny, the earliest is the root cause. Re-mark every later denying hop
   as `WOULD_ALSO_DENY` so the chain table stays consistent with the finding bodies, and
   state in each that it is a subsequent blocker rather than the current cause — fixing
   only a later hop will not resolve the call, and fixing only the root cause will not
   either.
3. If no hop is `DENIED_BY` but a service-specific non-IAM cause was found, that is the
   root cause.
4. If no hop is `DENIED_BY` and no non-IAM cause was found, but CloudTrail shows a
   denial, the root cause is **undetermined** — state that plainly and list what could
   not be evaluated. Do not manufacture a cause from the strongest-looking hop.
5. If a propagation-delay finding fired and no hop is `DENIED_BY`, the likely cause is
   timing. Say "likely," not "confirmed."

Case 4 is the honest outcome when simulation and CloudTrail disagree. Reporting it as
undetermined with a precise list of blind spots is more useful than a confident guess,
and it is the behaviour this skill is designed to produce.

## Proposed policy construction

Two categories, always rendered separately and never merged into one block.

### Category A — derived from the observed failure

Built from the CloudTrail event only. The action is the `eventName` mapped to its IAM
action; the resource is the ARN from `requestParameters` or the error message.

- heading: "Derived from the observed failure"
- preamble: "These permissions correspond directly to the call that failed. The action and resource are taken from the CloudTrail event, not inferred."

### Category B — commonly required, not observed

Taken from the curated per-service minimums in the matching `svc-*.md`. These are
permissions the role typically needs and whose absence would produce a similar failure,
but which were **not** observed failing in this incident.

- heading: "Commonly required — not observed"
- preamble: "These are the permissions `[operation]` usually requires. They were not observed failing here, and this list is not exhaustive for your workload. Review each against what your job actually accesses, and narrow the resource ARNs before applying."

### Rules

- Scope every `Resource` to a specific ARN. Never emit `"Resource": "*"`. If the exact
  ARN is unknown, emit a clearly marked placeholder such as
  `arn:aws:s3:::REPLACE_WITH_YOUR_BUCKET/*` rather than a wildcard.
- Never propose a policy when the root cause is an **explicit** deny — adding an allow
  cannot help. Instead state which statement must be amended.
- Never propose a policy when the root cause is an SCP — direct the user to the
  organization administrator.
- The simulator does not generate policies. Do not present simulator output as a
  suggested policy.
- Always precede both categories with the review banner from `report-format.md`.
