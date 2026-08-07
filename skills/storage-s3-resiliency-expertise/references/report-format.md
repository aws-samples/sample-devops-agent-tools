# Report Format

The rendered markdown report has the following structure. Readability is a hard requirement — emoji severity markers always have a space after them, headings are descriptive (no step numbers), and the finding text below each heading does not duplicate the leading emoji.

## Required sections (in order)

```
# S3 Resiliency Review — <bucket-name>

## Bucket Overview
- Name, region, account ID, age, data source
- Resiliency Rating: <emoji> <High|Medium|Low|Indeterminate> — <one-line justification>
- Dimensions table

## ⚠️ Permissions Notice
<INCLUDE ONLY IF permissions_limited == true.>

## ⚠️ Tooling Availability Notice
<INCLUDE ONLY IF tooling_limited == true.>

## Findings & Recommendations
<one subsection per check>

## References
<AWS docs relevant to specific findings>
```

## Bucket Overview format

The Bucket Overview contains three elements in order:

**1. Metadata block:**
```
- **Name:** <bucket-name>
- **Region:** <region>
- **Account ID:** <account-id>
- **Created:** <date> (~<N> years old)
- **Data source:** AWS control-plane APIs
```

**2. Resiliency Rating line:**
```
Resiliency Rating: <emoji> <High|Medium|Low|Indeterminate> — <one-line justification>
```

**3. Dimensions table:**

A summary table showing each check's outcome at a glance.

```
| Dimension | Status |
|---|---|
| Versioning | <emoji> <short status> |
| Replication | <emoji> <short status> |
| Object Lock | <emoji> <short status> |
| Bucket policy | <emoji> <short status> |
| Block Public Access | <emoji> <short status> |
| Default encryption | <emoji> <short status> |
| Ownership controls | <emoji> <short status> |
| Server access logging | <emoji> <short status> |
| Website hosting | <emoji> <short status> |
```

**Dimension status values:**

- Skipped checks: `— Skipped (<reason>)`
- AccessDenied checks: `⚠️ Unable to verify (access denied)`
- ToolingFailure checks: `⚠️ Unable to verify (tooling unavailable)`
- MFA Delete: omit row entirely if not enabled
- Website hosting "not configured": `✅ Not configured`

**Short status examples:**
- `❌ Not configured`
- `✅ Enabled`
- `✅ Cross-region, cross-account`
- `⚠️ Same-region only`
- `ℹ️ SSE-S3 (implicit)`
- `ℹ️ Not Set (owner-only ACLs)`
- `⚠️ Partial protections`
- `✅ Enabled (date-based partitioning)`

## Findings & Recommendations format

Each check that produced a finding gets one `###` subsection. Contains the finding AND recommendation together.

**Heading format:**
```
### <emoji-for-severity> <check name>
```

**Severity-to-emoji mapping:** success=✅, info=ℹ️, warning=⚠️, critical=❌. Always one space between emoji and check name.

**Body format:**
- Render the finding `body` field verbatim. Do NOT prepend any emoji.
- Substitute placeholder values with actual values.
- For multi-fragment findings, concatenate in order, separated by blank lines.
- The recommendation is embedded in the finding body. Do NOT add a separate recommendation line.

**Multi-fragment findings:** The heading takes the worst severity that any fragment fired. Appendix fragments do NOT introduce their own subheadings.

**Check names:**
- Versioning
- Replication
- Object Lock
- MFA Delete (omit if not enabled)
- Bucket policy
- Block Public Access
- Default encryption
- Ownership controls
- Server access logging
- Website hosting (omit if not configured)

**AccessDenied handling:**
```
### ⚠️ <check name>

Unable to verify — access denied. The caller does not have the required permission for this check. The bucket's actual configuration for this dimension is unknown. Re-run with broader access for a complete assessment.
```

**ToolingFailure handling:**
```
### ⚠️ <check name>

Unable to verify — tooling unavailable. The data collection infrastructure was unavailable when this check was attempted. This is NOT an indication that the feature is missing or misconfigured. Re-run when tooling is restored.
```

## Resiliency Rating

**Rating-to-emoji mapping:**
- High → ✅
- Medium → ⚠️
- Low → ❌
- Indeterminate → ⚠️

**Rating criteria:**

**CRITICAL RULE: AccessDenied / ToolingFailure checks do NOT affect the resiliency score.**

The rating is computed from **verified checks only** (OK or NotConfigured):
- **High:** Bucket survives both a regional outage and a credential compromise. **Cannot be assigned when `permissions_limited == true` or `tooling_limited == true`.**
- **Medium:** Survives most failure modes but has at least one significant gap. **Maximum rating when limited flags are set.**
- **Low:** Has at least one critical exposure — anything that produced a `❌` finding from a verified check.
- **Indeterminate:** More than half of checks returned AccessDenied or ToolingFailure.

## Permissions Notice format

Include ONLY when `permissions_limited == true`. Render BEFORE `## Findings & Recommendations`.

```
## ⚠️ Permissions Notice

The caller was missing read access for **<N>** checks. These render as `⚠️ Unable to verify` below and are excluded from the Resiliency Rating (they do not penalize the score, but cap the maximum rating at Medium).
```

## Tooling Availability Notice format

Include ONLY when `tooling_limited == true`. Render BEFORE `## Findings & Recommendations` (after Permissions Notice if both present).

```
## ⚠️ Tooling Availability Notice

The data collection infrastructure was unavailable for **<N>** checks. These render as `⚠️ Unable to verify — tooling unavailable` below. This is NOT an indication that features are missing or misconfigured. The Resiliency Rating is capped at Medium.
```

## References

A `## References` heading followed by a bulleted list of AWS documentation links. Only include references directly relevant to findings. Group by topic if more than 5 references.

### Canonical AWS documentation URLs

- Versioning: https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html
- Replication: https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication.html
- Replication (delete markers): https://docs.aws.amazon.com/AmazonS3/latest/userguide/delete-marker-replication.html
- Replication (cross-account): https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-walkthrough-2.html
- Object Lock: https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html
- Object Lock (retention modes): https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock-overview.html
- MFA Delete: https://docs.aws.amazon.com/AmazonS3/latest/userguide/MultiFactorAuthenticationDelete.html
- Bucket policy: https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucket-policies.html
- Security best practices (SecureTransport): https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html
- Block Public Access: https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html
- Block Public Access (account-level): https://docs.aws.amazon.com/AmazonS3/latest/userguide/configuring-block-public-access-account.html
- Default encryption: https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucket-encryption.html
- SSE-KMS: https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingKMSEncryption.html
- Bucket Key: https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucket-key.html
- DSSE-KMS: https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingDSSEncryption.html
- Ownership controls: https://docs.aws.amazon.com/AmazonS3/latest/userguide/about-object-ownership.html
- Disabling ACLs: https://docs.aws.amazon.com/AmazonS3/latest/userguide/ensure-object-ownership.html
- ACLs overview: https://docs.aws.amazon.com/AmazonS3/latest/userguide/acl-overview.html
- Server access logging: https://docs.aws.amazon.com/AmazonS3/latest/userguide/ServerLogs.html
- Logging (date-based partitioning): https://docs.aws.amazon.com/AmazonS3/latest/userguide/ServerLogs.html#server-log-keyname-format
- CloudTrail S3 data events: https://docs.aws.amazon.com/AmazonS3/latest/userguide/cloudtrail-logging-s3-info.html
- Static website hosting: https://docs.aws.amazon.com/AmazonS3/latest/userguide/WebsiteHosting.html
- CORS: https://docs.aws.amazon.com/AmazonS3/latest/userguide/cors.html

## Pre-render validation

Run these 13 checks before delivering the report (see the Final Delivery Contract in SKILL.md). Do NOT output validation results to the user.

### Structure (4 checks)

1. **Required sections present.** Correct order, no missing, no extras.
2. **No empty required sections.** Each `##` section has content.
3. **Permissions Notice consistency.** Present iff `permissions_limited == true`.
4. **Findings = checks that produced findings.** Exact match, no extras, no missing.

### Severity coherence (3 checks)

5. **Severity-emoji mapping is correct.**
6. **Multi-fragment severity = worst severity.**
7. **Resiliency Rating is consistent with findings.** Low requires ❌. High requires no ⚠️/❌ and no limited flags.

### Substitution (2 checks)

8. **No unsubstituted placeholders.** No `<...>` tokens remain (code fences exempt).
9. **No emoji-prefixed body text.** Emoji belongs on heading only.

### Internal consistency (2 checks)

10. **References trace to findings.**
11. **No duplicate or contradictory findings.**

### Delivery consistency (1 check)

12. **Artifact and final response match.** If a report artifact is produced, its content is identical to the report returned in the final response (per the Final Delivery Contract).

### Dimensions table (1 check)

13. **Dimensions table consistency.** Every row matches its finding's severity. Skipped = `—`. No missing dimensions.

## Artifact title

When the runtime supports persisted artifacts, name the report artifact:

```
s3-resiliency-review-<bucket-name>-<YYYY-MM-DD>.md
```

For a fleet review, use `s3-fleet-resiliency-review-<YYYY-MM-DD>.md`. See the Final Delivery Contract in SKILL.md for full delivery requirements.
