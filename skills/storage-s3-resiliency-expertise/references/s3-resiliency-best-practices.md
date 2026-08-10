# S3 Resiliency Best Practices — Operational Depth

Reference material for the storage-s3-resiliency-expertise skill. This document contains operational knowledge beyond what's in AWS public documentation — thresholds, gotchas, decision trees, and reasoning the SKILL.md finding summaries refer to.

For the canonical workflow steps and finding summary text, see SKILL.md.

## Versioning

### Why versioning is the foundation

Versioning is the single highest-leverage resiliency control on S3. Every other control either depends on it (Replication, Object Lock, MFA Delete cannot be configured without versioning) or assumes it (lifecycle rules with `NoncurrentVersionExpiration` are no-ops without versioning).

### Three states, three different problems

| State | Recovery from delete? | Recovery from overwrite? | Replication / Object Lock allowed? |
|---|---|---|---|
| Not configured | ❌ permanent | ❌ permanent | ❌ blocked |
| Suspended | ⚠️ existing noncurrent versions remain, new writes are permanent | ⚠️ same | ⚠️ remains configured but stops creating new versions |
| Enabled | ✅ via noncurrent version | ✅ via noncurrent version | ✅ |

**The Suspended trap.** Customers often "suspend" versioning thinking it's a soft delete — keeping the historical versions but stopping new ones. That's accurate, but the bucket is now in a degraded state where all new writes are unrecoverable. There is no "read-only versioning" option; you either have it or you don't.

### Object Lock vs Versioning

Object Lock requires versioning to be enabled. Object Lock can be enabled at bucket-creation time or on an existing bucket (S3 supports enabling Object Lock on existing buckets; versioning must be enabled first, and enabling it does not retroactively protect objects that already existed). New objects written after Object Lock is configured are protected per the default retention. Objects that predate the configuration are not automatically locked — set retention on them explicitly if they must be protected.

## Replication

### The four-quadrant model

Replication scenarios decompose along two axes: cross-region vs same-region, same-account vs cross-account. The four quadrants have very different risk profiles:

| | Same Account | Cross Account |
|---|---|---|
| **Cross Region** | Good for regional outage; fails on account compromise | Best protection (regional + account isolation) |
| **Same Region** | Marginal value (only protects against object-level corruption) | Account isolation but no regional DR |

The skill's SKILL.md uses this model to produce 7 distinct replication finding summaries (4 quadrants × {verified, 403} status).

### Why same-account replication has limited value

Customers often configure cross-region replication into the same account thinking they've achieved DR. They have — for regional outages and for accidental bulk deletion in many cases. But:

- A compromised IAM principal with broad S3 permissions can delete from both buckets simultaneously
- A misconfigured account-wide deny SCP affects both buckets
- A billing/account suspension affects both buckets

**Recommendation framing:** "Same-account replication addresses regional outage and accidental deletion. For protection against account-level compromise (credential theft, misconfigured SCPs, billing disputes), use cross-account replication."

### Delete Marker Replication

Default is OFF. The SKILL.md surfaces this as `<enabled/disabled>` in every replication finding because it's the most-overlooked configuration knob:

- **Off (default):** Deleting an object in source creates a delete marker in source only; destination keeps the object visible. This is "soft DR" — destination acts as a recovery point if you regret the delete.
- **On:** Delete markers replicate to destination. The destination behaves like a true mirror. Better for compliance use cases that require both copies to be in lock-step.

Most customers should leave it OFF for resiliency purposes. Most customers WHO TURN IT ON do so to satisfy audit requirements, not for resiliency benefit.

### 403 on destination HeadBucket

Surface this as ⚠️, not ❌. A 403 doesn't mean replication is broken — it means the reviewing principal can't verify destination ownership. Common causes:

1. Cross-account destination, reviewer doesn't have a role in the destination account (most common — usually intentional)
2. SCP on destination account denies HeadBucket from the source account
3. Destination bucket policy explicitly denies HeadBucket

The right framing: "Investigate permissions to verify destination ownership for a complete resiliency picture. The replication itself may be fine."

## Object Lock

### Retention period display rules — why 730 days matters

The SKILL.md uses 730 days (2 years) as the threshold for switching to year-based display. Reasoning:

- Below 2 years, customers think in days. "30-day retention" is meaningful; "0.082-year retention" isn't.
- At 2+ years, days become unreadable. "1825 days" is harder to interpret than "1825 days (5 years)".
- Exact-year multiples (730, 1095, 1460, 1825...) get a clean year display. Off-multiple values (e.g., 800 days) get an "Approx." marker so the user knows it's not a round number.

**Common retention values to memorize:**
- 30 / 60 / 90 days — typical for transient compliance
- 365 days (1 year) — annual audit cycle
- 730 days (2 years) — common SOC 2 requirement
- 1095 days (3 years) — common HIPAA / financial services
- 2555 days (7 years) — IRS / SOX
- 36500 days (~100 years) — practical "forever"

### GOVERNANCE vs COMPLIANCE mode

Surface the mode in the finding because the customer needs to understand the protection level:

- **GOVERNANCE:** Special IAM permission (`s3:BypassGovernanceRetention`) can override the lock. Useful for "tamper-evident" but not "tamper-proof." Good for internal compliance.
- **COMPLIANCE:** No one — including the root user — can override. The only way out is to wait for retention to expire or delete the entire account. This is the SEC Rule 17a-4 / FINRA-grade lock.

**Most customers should use GOVERNANCE.** COMPLIANCE is operationally dangerous if misconfigured (the bucket becomes a permanent storage cost). Recommend it only when the customer has a specific regulatory requirement and has tested their retention math carefully.

## MFA Delete

### Why "do not include a finding if not enabled" is the right default

MFA Delete sounds like security best practice but rarely is the right answer in 2026:

- Object Lock provides stronger protection (immutability vs MFA prompt)
- MFA Delete only protects the root user account workflow — most modern access is via IAM roles, where MFA Delete doesn't apply
- The MFA token requirement breaks automation (Terraform, CloudFormation, etc.)
- It's a legacy feature — AWS hasn't recommended it since the early 2010s

The SKILL.md tells the agent NOT to flag missing MFA Delete because doing so generates noise. If it IS enabled, surface it as ℹ️ (informational, not best practice) and steer toward Object Lock.

## Bucket Policy as Resiliency Control

### The "Deny only what's configured" rule

Don't recommend protecting features the bucket doesn't use. Example: if Object Lock isn't configured, don't recommend `Deny s3:BypassGovernanceRetention` — it's noise. The SKILL.md cross-references findings from other steps to keep recommendations focused on actually-configured features.

### Why a bucket policy is a resiliency control at all

Bucket policy adds a layer of defense above IAM. A principal might have `s3:DeleteBucket` IAM permission, but a bucket policy `Deny` blocks the action regardless. This protects against:

- Misconfigured IAM (overly broad policy attached to a service role)
- Compromised credentials (attacker has IAM permission but the bucket policy stops them)
- "Insider threat" with intentionally elevated IAM access

### Transport security: the cheapest universal protection

Add `Deny on aws:SecureTransport: false` to every production bucket. Costs nothing, breaks nothing in modern client libraries (all default to HTTPS), and prevents accidental plain-HTTP exposure. This is in the skill's policy check list, but worth surfacing emphatically when the bucket is missing it.

## Block Public Access (BPA)

### Bucket-level vs Account-level — when to use which

**Account-level BPA is the better default.** It applies to all current and future buckets, can't be forgotten on a new bucket, and doesn't require per-bucket configuration drift.

**Bucket-level BPA is necessary when** you have a few legitimately-public buckets (static website hosting, public dataset distribution, etc.). In that case, leave bucket-level BPA on for everything else and disable specific settings only on the buckets that need to be public.

### The 4 settings, and what they actually do

| Setting | Effect |
|---|---|
| `BlockPublicAcls` | Prevents NEW public ACLs from being applied |
| `IgnorePublicAcls` | Ignores EXISTING public ACLs (acts as if they aren't there) |
| `BlockPublicPolicy` | Prevents NEW public bucket policies from being applied |
| `RestrictPublicBuckets` | Restricts cross-account access via existing public bucket policy |

**The asymmetry:** `Block*` prevents future configuration; `Ignore*`/`Restrict*` neutralizes existing configuration. Most public-exposure incidents involve existing ACLs that pre-date BPA — `IgnorePublicAcls` is the actual mitigation, not `BlockPublicAcls`.

### Cross-referencing with ACLs and bucket policy

The SKILL.md's BPA logic combines BPA settings + actual ACL data + actual policy data to produce findings that say "this is your real exposure" rather than "you're missing a config." Example: if `BlockPublicAcls` is off but no public ACLs exist anywhere, the finding is "no public ACLs currently, but they could be written" — not "public ACL exposure." The wording matters because it changes the customer's urgency.

## Encryption

### Why SSE-KMS with CMK matters more than SSE-S3

Both encrypt at rest with AES-256. The differences are in key management, not cryptographic strength:

| Property | SSE-S3 | SSE-KMS (aws/s3) | SSE-KMS (CMK) | DSSE-KMS |
|---|---|---|---|---|
| Key rotation control | Amazon-only | Annual (auto) | Customer-controlled | Customer-controlled |
| Independent disable / revoke | ❌ | ❌ | ✅ | ✅ |
| Audit logging of key use | ❌ | ⚠️ partial | ✅ via CloudTrail | ✅ |
| Cross-account key sharing | ❌ | ❌ | ✅ via key policy | ✅ |
| Compliance requirement coverage | Most | Most | All | FIPS 140-3 dual-layer |

**Recommend SSE-KMS with CMK** for:
- Any bucket containing regulated data
- Any bucket where key access should be audit-logged
- Any bucket where you need the ability to revoke access by disabling the key

**SSE-S3 is fine for** ephemeral / non-sensitive data where simplicity matters more than control.

### Bucket Key — always recommend enabling

Bucket Key reduces KMS API calls by ~99% by caching a derived bucket-level key for short periods. For a high-traffic bucket, this is the difference between a $5/month KMS bill and a $500/month one. There is no downside — same encryption, same compliance properties, dramatically lower cost.

The only reason it's ever disabled is that it didn't exist before 2020 and old buckets were never updated. Surface ℹ️ with "consider enabling" — it's a no-brainer.

### DSSE-KMS — when do you need dual-layer?

Almost never. DSSE-KMS is FIPS 140-3 dual-layer encryption — required for some federal workloads (FedRAMP High, certain DoD compliance regimes). For commercial workloads, SSE-KMS with CMK is the right choice. If a customer has DSSE-KMS configured without a specific compliance requirement, ask why — they may be paying for compliance theater.

## Ownership Controls

### BucketOwnerEnforced is the modern default

Created post-2021 to disable ACLs entirely. All objects are owned by the bucket owner regardless of who uploaded them. This is the recommended setting for nearly all new buckets:

- Cross-account uploads work cleanly without ACL gymnastics
- Bucket policy is the sole access-control surface (simpler, easier to audit)
- ACLs can't be a vector for accidental public exposure

### Why customers stay on legacy ownership modes

Three patterns to recognize:

1. **Legacy app**: Customer's pipeline relies on the uploading-account-owns-the-object semantics (e.g., third-party log delivery). Migrating requires app-level changes.
2. **Service integration**: Some AWS services (older S3 server access logging, some CloudWatch log exports) historically relied on ACLs. Most have been updated to use bucket policy + service principals, but legacy configs persist.
3. **Cross-account access via ACL**: The customer grants access to specific other accounts via canonical user IDs in ACLs. Migration requires switching to bucket policy with account-ID principals.

**Recommendation framing:** Always recommend BucketOwnerEnforced as the destination. Acknowledge the migration effort but don't soften the recommendation — ACL-based access is a security/audit liability.

### The 8 ownership scenarios in SKILL.md

The 8-way matrix in the SKILL.md combines: ownership setting × public ACL grant × cross-account ACL grant × cross-account policy. Most customers fall into scenarios 1-3 (BOE, or non-BOE with owner-only ACLs). Scenarios 4-5 (AllUsers / AuthenticatedUsers ACL grants) are critical findings because they're effectively-public access.

## Logging

### S3 Server Access Logging vs CloudTrail Data Events

Either is acceptable for resiliency / audit purposes. The SKILL.md treats CloudTrail Data Events as a valid alternative when SAL is not configured.

| | S3 Server Access Logs | CloudTrail Data Events |
|---|---|---|
| Coverage | Per-request (best-effort delivery, may miss events) | Per-request (guaranteed delivery) |
| Delivery destination | S3 bucket | S3 bucket + optional CloudWatch Logs |
| Latency | Hours | Minutes |
| Cost | Just storage | Per-event charge ($0.10/100k events) + storage |
| Format | Apache log format | JSON |

**For a high-traffic bucket**, CloudTrail Data Events can be surprisingly expensive. SAL is usually cheaper but misses some events. Most customers should choose based on cost and downstream tooling: if you have a SIEM that ingests CloudTrail, use CloudTrail; if you're using Athena/Glue against S3, SAL is fine.

### Date-based partitioning

Always recommend date-based partitioning (`EventTime` or `DeliveryTime`). The performance and cost difference for downstream Athena queries is dramatic — querying a year of logs partitioned by date scans ~1/365th of the data vs unpartitioned. This is in the SKILL.md as ℹ️ when missing.

### Don't recommend switching from CloudTrail to SAL

If CloudTrail Data Events are configured for the bucket, that's sufficient. Don't recommend adding SAL on top — you'd be paying for two log streams. This rule is explicit in SKILL.md to prevent the agent from generating noise.

## Website Configuration

### Public-by-design vs accidentally-public

A bucket with website hosting enabled is intentionally public-facing. The skill treats the website-enabled state as informational and instead checks whether the public access is actually working (cross-referencing BPA, bucket policy Allow, and bucket policy Deny).

The interesting findings come from misconfiguration:
- Website hosting enabled but BPA blocks all public access → website doesn't work, customer probably forgot
- Website hosting + Allow + conditional Deny → website partially works, surface the conditions

### Always append the "sensitive data warning"

Every website-enabled finding gets the same trailer: "Buckets configured for static website hosting are intended for public content. Ensure sensitive or critical data is not stored in this bucket. Separate public content from sensitive data by using dedicated buckets for each purpose."

This is a hard recommendation, not a soft one. Mixing public assets with sensitive data in the same bucket is a recipe for accidental exposure (a wrong key prefix, a misconfigured policy, a forgotten lifecycle rule, etc.).

## Resiliency Assessment Rating

The SKILL.md asks the agent to produce a High/Medium/Low rating. Mental model:

| Rating | Mental Model | Typical Profile |
|---|---|---|
| **High** | "I'd be confident this bucket survives a regional outage AND a credential compromise." | Versioning + cross-region + cross-account replication, Object Lock, BPA all 4, SSE-KMS CMK + Bucket Key, BOE, logging, no public exposure |
| **Medium** | "Survives most failure modes but has at least one significant gap." | Versioning + same-account replication, BPA, encryption, but no Object Lock — OR — All resiliency features but no logging — OR — Most features but website hosting needs scoping |
| **Low** | "At least one critical exposure (no versioning, public ACL grants, no BPA, suspended versioning, etc.)." | Anything with a ❌ critical finding |

The rating is the agent's summary judgment. Don't try to make it formulaic — explain the reasoning in the report so the customer understands what would move the rating up.

## Common Anti-Patterns

| Anti-pattern | What customers do | What to recommend |
|---|---|---|
| "Versioning is too expensive" | Keep versioning off, accept data loss risk | Configure lifecycle rule with `NoncurrentVersionExpiration` (e.g., 30-90 days). Cost is bounded. |
| "Replication = backup" | Configure CRR thinking they no longer need backups | Replication is for DR, not point-in-time recovery. For accidental delete recovery, use versioning + lifecycle. |
| "Object Lock everything" | Apply COMPLIANCE mode with long retention to all buckets | Use GOVERNANCE for most cases. COMPLIANCE only for specific regulatory requirements. Long retention has permanent storage cost. |
| "BPA breaks our app" | Disable BPA without fixing the underlying issue | Almost always means the app uses public ACLs that should be replaced with bucket policy + IAM. |
| "We have backups so we don't need versioning" | External backup tools snapshot the bucket | Versioning is granular per-object recovery. External backups are coarse-grained. Use both. |
| "MFA Delete is more secure" | Enable MFA Delete on all production buckets | Object Lock is the modern equivalent. MFA Delete only protects root-user workflow. |

## Decision Tree: Where to Start

When reviewing an unfamiliar bucket, ask in this order:

```
1. Does it have versioning? 
   No → ❌ critical, fix first; everything else depends on this.
   Yes → continue.

2. Is anything publicly accessible (ACL, policy, no BPA)?
   Yes → ❌ critical, this is the first thing to fix.
   No → continue.

3. What's the data sensitivity (tags, naming convention, tribal knowledge)?
   Sensitive → continue with stricter recommendations (Object Lock, KMS CMK, cross-region replication).
   Not sensitive → continue with cost-balanced recommendations.

4. Is there cross-region/cross-account redundancy?
   No, and it's important data → recommend CRR with cross-account.
   No, and it's not important → ℹ️ note, optional.
   Yes → verify configuration.

5. Is there an audit trail?
   No → recommend SAL or CloudTrail Data Events.
   Yes → verify date-based partitioning if SAL.

6. Is the bucket policy adding defensive Denies for resiliency features?
   No → recommend adding (transport security at minimum).
   Yes → verify they cover all configured features.
```

This ordering matches the SKILL.md workflow steps but skews toward "biggest risk first" when the agent needs to triage findings into recommendations.
