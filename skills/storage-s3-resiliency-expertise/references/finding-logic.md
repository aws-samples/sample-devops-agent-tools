# Finding Logic

This document defines the finding rules for each resiliency check. The analyzer applies these rules against the structured configuration data produced during data collection (see `data-collection.md`). No API calls are made during finding generation — all data is pre-collected.

The order of sections below is the order findings should appear in the rendered report.

If a check's status is `AccessDenied` or `ToolingFailure`, that check's finding is replaced by the "Unable to verify" template defined in the report format. Skip the check's normal finding-summary lookup and do NOT infer the configuration's state.

## Versioning

**Input:** `checks.versioning.status` + `checks.versioning.value`

**Conditional logic:**
- If versioning is **Enabled** or **Suspended** → evaluate sub-checks **Replication**, **Object Lock**, **MFA Delete**
- If versioning is **NotConfigured** → skip all sub-checks

**Finding summaries:**

**Not configured:**
- severity: critical
- body: "Versioning is not enabled. Without versioning, overwrites and deletes are permanent — there is no way to recover previous object data. Additionally, versioning is a prerequisite for S3 Replication and Object Lock. These features cannot be configured until versioning is enabled, leaving the bucket without cross-region redundancy and immutability protection."

**Suspended:**
- severity: critical
- body: "Versioning is suspended. New writes will not create noncurrent versions, meaning overwrites and deletes of new objects are permanent. Existing noncurrent versions from before suspension are preserved, but the bucket is in a degraded protection state. Re-enable versioning to restore data protection."

**Suspended — replication appendix (appended to Suspended body when replication is configured):**
- severity: matches parent (does not introduce a new heading)
- body: "S3 Replication is configured on this bucket but will not function while versioning is suspended. Re-enable versioning to allow replication to resume."

**Enabled:**
- severity: success
- body: "Versioning is enabled. The bucket retains noncurrent versions of objects, providing protection against accidental overwrites and deletes."

### Replication

**Input:** `checks.replication.status` + `checks.replication.value`

**Destination classification:** Use `value.destination_region` vs source `region` for cross-region determination. Use `value.destination_account` vs source `account_id` for cross-account determination. If `value.destination_lookup == "lookup_failed"` → use the 403 scenario.

**Finding summaries (7 scenarios):**

**1. Not configured:**
- severity: critical
- body: "S3 Replication is not configured. Without replication, the bucket has no cross-region or cross-account redundancy. A regional outage or accidental bulk deletion would have no secondary copy to recover from. Configure S3 Replication to a bucket in a separate region to improve resiliency."

**2. Cross-region, same account:**
- severity: success
- body: "S3 Replication is configured. Objects are replicated to `<destination bucket>` in `<destination region>` (cross-region). Both source and destination are in the same account. Note: same-account replication does not provide isolation against account-level compromise — a principal with sufficient privileges can access both buckets. For stronger isolation, consider cross-account replication. Delete marker replication is `<enabled/disabled>`."

**3. Cross-region, cross account:**
- severity: success
- body: "S3 Replication is configured. Objects are replicated to `<destination bucket>` in `<destination region>` (cross-region, cross-account). Delete marker replication is `<enabled/disabled>`."

**4. Cross-region, lookup failed:**
- severity: warning
- body: "S3 Replication is configured. Objects are replicated to `<destination bucket>` in `<destination region>` (cross-region). Unable to verify destination account ownership — HeadBucket returned 403 (destination may be owned by a different account or insufficient permissions). Investigate permissions to ensure the reviewing principal has `s3:HeadBucket` access to the destination bucket for a complete resiliency assessment. Delete marker replication is `<enabled/disabled>`."

**5. Same-region, same account:**
- severity: warning
- body: "S3 Replication is configured to `<destination bucket>`, but both source and destination are in `<region>`. Same-region replication does not provide protection against a regional outage. Both buckets are in the same account, which does not provide isolation against account-level compromise. For disaster recovery and stronger isolation, consider cross-region, cross-account replication. Delete marker replication is `<enabled/disabled>`."

**6. Same-region, cross account:**
- severity: warning
- body: "S3 Replication is configured to `<destination bucket>` (cross-account), but both source and destination are in `<region>`. Same-region replication does not provide protection against a regional outage. For disaster recovery, configure cross-region replication. Delete marker replication is `<enabled/disabled>`."

**7. Same-region, lookup failed:**
- severity: warning
- body: "S3 Replication is configured to `<destination bucket>`, but both source and destination are in `<region>`. Unable to verify destination account ownership — HeadBucket returned 403 (destination may be owned by a different account or insufficient permissions). Investigate permissions to ensure the reviewing principal has `s3:HeadBucket` access to the destination bucket for a complete resiliency assessment. Same-region replication does not provide protection against a regional outage. For disaster recovery, configure cross-region replication. Delete marker replication is `<enabled/disabled>`."

### Object Lock

**Input:** `checks.object_lock.status` + `checks.object_lock.value`

**Retention display rules:**
- Under 730 days (2 years): show days only (e.g., "30 days")
- 730 days or over, exactly divisible by 365: show days and years (e.g., "730 days (2 years)")
- 730 days or over, not exactly divisible by 365: show days and approximate years (e.g., "800 days (Approx. 2 years)")
- Pluralize "day" and "year" appropriately (1 day, 2 days, 1 year, 2 years)

**Finding summaries:**

**Configured:**
- severity: success
- body: "Object Lock is enabled with `<GOVERNANCE/COMPLIANCE>` mode and `<retention display>` retention. Objects cannot be deleted or overwritten during the retention period."

**Not configured:**
- severity: warning
- body: "Object Lock is not configured. Without Object Lock, versioned objects can still be permanently deleted. For data that requires immutability guarantees (compliance, ransomware protection), consider enabling Object Lock. Object Lock can be enabled on an existing bucket that has versioning enabled — it does not require recreating the bucket (note: objects written before Object Lock is configured are not retroactively protected)."

### MFA Delete

**Input:** `checks.versioning.mfa_delete`

**Finding summaries:**

**Enabled:**
- severity: info
- body: "MFA Delete is enabled. Permanently deleting object versions and changing the versioning state requires multi-factor authentication. MFA Delete is not the recommended approach for protecting against object deletions. Consider using Object Lock for immutability protection instead."

**Not enabled:**
- Do not include a finding for MFA Delete in the report if it is not enabled.

## Bucket policy

**Input:** `checks.bucket_policy.status` + `checks.bucket_policy.value`

**Resiliency-focused checks:**
For each resiliency-relevant feature, check whether the policy contains a Deny statement protecting it:
- Object/bucket deletion (`s3:DeleteObject`, `s3:DeleteObjectVersion`, `s3:DeleteBucket`)
- Versioning modification (`s3:PutBucketVersioning`)
- Replication modification (`s3:PutReplicationConfiguration`, `s3:DeleteReplicationConfiguration`)
- Bucket policy modification (`s3:PutBucketPolicy`, `s3:DeleteBucketPolicy`)
- Lifecycle modification (`s3:PutLifecycleConfiguration`, `s3:DeleteLifecycleConfiguration`)
- Encryption modification (`s3:PutEncryptionConfiguration`, `s3:DeleteEncryptionConfiguration`)
- Public Access Block modification (`s3:PutBucketPublicAccessBlock`, `s3:DeleteBucketPublicAccessBlock`)
- Logging modification (`s3:PutBucketLogging`)
- Ownership controls modification (`s3:PutBucketOwnershipControls`)
- Object Lock bypass (`s3:BypassGovernanceRetention`, `s3:PutObjectRetention`, `s3:PutObjectLegalHold`)
- Transport security (Deny on `aws:SecureTransport: false`)

**Combination logic:**
Only recommend adding Deny protections for features that are actually configured (based on findings from other checks). For features not yet configured, the earlier checks already flag them.

**Finding summaries:**

**1. No bucket policy:**
- severity: warning
- body: "No bucket policy is configured. Without policy-level protections, resiliency features (versioning, replication, encryption, etc.) can be modified or disabled by any principal with sufficient IAM permissions. Consider adding a bucket policy with Deny statements protecting configured resiliency features from unauthorized modification."

**2. Bucket policy exists + no resiliency protections detected:**
- severity: warning
- body: "Bucket policy is configured but does not contain Deny statements protecting resiliency features. Consider adding protective Deny statements for the following actions, scoped to the currently configured features: `<list based on combination with other checks>`."

**3. Bucket policy exists + partial resiliency protections:**
- severity: info
- body: "Bucket policy contains Deny statements protecting the following actions: `<list of protected actions>`. The following actions related to currently configured features are not protected: `<list of missing actions based on combination with other checks>`. Consider adding Deny statements for these to prevent unauthorized modifications."

**4. Bucket policy exists + full resiliency protections for all configured features:**
- severity: success
- body: "Bucket policy contains Deny statements protecting all currently configured resiliency features from unauthorized modification."

**Transport security (appended to any of the above):**
- severity: matches parent (does not introduce a new heading)
- If `value.has_secure_transport_deny == true`, append: "HTTPS is enforced via a Deny on non-secure transport."
- If `value.has_secure_transport_deny == false`, append: "HTTPS is not enforced. Consider adding a Deny on `aws:SecureTransport: false` to block non-HTTPS requests."

## Block Public Access (BPA)

**Input:** `checks.bpa_bucket` + `checks.bpa_account` + `checks.acl` + `checks.bucket_policy`

**Conditional logic:**
- Evaluate BPA at bucket level first. If NotConfigured, check account level.
- If all 4 settings are enabled at either level, report as fully protected.
- If partially enabled, cross-reference ACL and bucket policy data.

**Finding summaries:**

**1. All 4 enabled at bucket level:**
- severity: success
- body: "Block Public Access is fully enabled at the bucket level. All 4 settings are active."

**2. Partially enabled (bucket or account level) — combined finding:**
- severity: warning (or critical if cross-referenced sub-findings reveal active public exposure)
- base body: "Block Public Access is partially enabled at the `<bucket/account>` level."
- Then append, in this order, only the fragments that fired:

**ACL exposure fragment (IgnorePublicAcls + BlockPublicAcls):**
- If IgnorePublicAcls is disabled, check ACLs for AllUsers or AuthenticatedUsers grants:
  - If public ACLs exist → severity escalates to **critical**: "Public ACLs are present on this bucket and are in effect because IgnorePublicAcls is disabled."
  - If no public ACLs exist but BlockPublicAcls is also disabled → severity stays **warning**: "No public ACLs are currently present, but BlockPublicAcls is disabled — public ACLs could be written to this bucket."
  - If no public ACLs exist and BlockPublicAcls is enabled → no ACL fragment appended.

**Policy exposure fragment (BlockPublicPolicy + RestrictPublicBuckets):**
- If BlockPublicPolicy is disabled, check bucket policy for public access grants (`Principal: "*"` in Allow statements):
  - If public policy exists → severity escalates to **critical**: "The bucket policy grants public access and BlockPublicPolicy is disabled, allowing this policy to remain in effect."
  - If no public policy exists → severity stays **warning**: "No public bucket policy is currently configured, but BlockPublicPolicy is disabled — a public policy could be applied to this bucket."
- If RestrictPublicBuckets is disabled and a public policy exists, also append: "RestrictPublicBuckets is disabled — cross-account access via the public bucket policy is not restricted."

**Closing line (always appended):** "Enable all 4 Block Public Access settings unless public access is intentionally required."

**3. Not configured at bucket level + all 4 enabled at account level:**
- severity: success
- body: "Block Public Access is not configured at the bucket level. Account-level Block Public Access is fully enabled, providing protection across all buckets in the account without requiring per-bucket configuration."

**4. Not configured at bucket level + partially enabled at account level:**
- severity: warning (or critical if cross-referenced sub-findings reveal active public exposure)
- Use the same combined finding logic as scenario 2, but the base body says "at the account level" instead of "at the bucket level".

**5. Not configured at bucket level + not configured at account level:**
- severity: critical
- body: "Block Public Access is not configured at either the bucket or account level. The bucket has no BPA protection against public access. Enable all 4 Block Public Access settings at the bucket level."

**6. Not configured at bucket level + unable to check account level (AccessDenied/ToolingFailure):**
- severity: warning
- body: "Block Public Access is not configured at the bucket level. Unable to verify account-level Block Public Access — insufficient permissions. Investigate permissions and verify BPA is enabled at the bucket or account level."

## Default encryption

**Input:** `checks.encryption.status` + `checks.encryption.value`

**Finding summaries:**

**SSE-KMS with customer-managed key (CMK), Bucket Key enabled:**
- severity: success
- body: "Default encryption is SSE-KMS using a customer-managed key (`<key ARN>`). Bucket Key is enabled, reducing KMS costs and allowing for better API performance."

**SSE-KMS with customer-managed key (CMK), Bucket Key disabled:**
- severity: info
- body: "Default encryption is SSE-KMS using a customer-managed key (`<key ARN>`). Bucket Key is not enabled — consider enabling it to reduce KMS costs and potentially improve API performance."

**SSE-KMS with AWS-managed key (aws/s3):**
- severity: info
- body: "Default encryption is SSE-KMS using the AWS-managed key (`aws/s3`). This provides KMS-level encryption but without independent key rotation control or the ability to revoke access by disabling the key. For stronger key management, consider using a customer-managed KMS key."

**SSE-S3 (AES256):**
- severity: info
- body: "Default encryption is SSE-S3 (AES256). Encryption is Amazon-managed with no customer control over key lifecycle. For workloads requiring key management, audit logging of key usage, or the ability to revoke access via key policy, consider SSE-KMS with a customer-managed key."

**DSSE-KMS (dual-layer):**
- severity: success
- body: "Default encryption is DSSE-KMS (dual-layer server-side encryption) using key `<key ARN>`. This provides two layers of encryption for compliance requirements."

## Ownership controls

**Input:** `checks.ownership` + `checks.acl` + `checks.bucket_policy`

**Conditional logic:**
- If BucketOwnerEnforced: no sub-checks needed
- If BucketOwnerPreferred, ObjectWriter, or Not Set: analyze ACLs and cross-account policy

**Ownership-specific warnings (append to non-BucketOwnerEnforced findings):**
- **ObjectWriter or Not Set:** "Objects not owned by the bucket owner will cause permissions issues for the bucket owner."
- **BucketOwnerPreferred:** "Objects uploaded without the bucket-owner-full-control ACL will cause permissions issues for the bucket owner."

**Combined finding summaries (8 scenarios):**

**1. BucketOwnerEnforced:**
- severity: success
- body: "Bucket ownership is set to BucketOwnerEnforced. ACLs are disabled. All objects are owned by the bucket owner regardless of who uploaded them."

**2. Non-BucketOwnerEnforced + owner-only ACL + no cross-account policy:**
- severity: info
- body: "Bucket ownership is set to `<ObjectWriter/BucketOwnerPreferred/Not Set>`. ACLs are active but only grant FULL_CONTROL to the bucket owner. No cross-account access found in bucket policy. `<ownership-specific warning>` Consider migrating to BucketOwnerEnforced to disable ACLs and simplify access management."

**3. Non-BucketOwnerEnforced + owner-only ACL + cross-account policy principals:**
- severity: warning
- body: "Bucket ownership is set to `<ObjectWriter/BucketOwnerPreferred/Not Set>`. ACLs are active but only grant FULL_CONTROL to the bucket owner. However, bucket policy grants access to cross-account principal(s) `<principal ARN(s)>`. Objects uploaded by cross-account principals may be owned by the uploading account, limiting the bucket owner's access to those objects. `<ownership-specific warning>` Migrate to BucketOwnerEnforced to ensure the bucket owner retains ownership of all objects, or use cross-account IAM roles to avoid ownership issues."

**4. Non-BucketOwnerEnforced + AllUsers ACL grant:**
- severity: critical
- body: "Bucket ownership is set to `<ObjectWriter/BucketOwnerPreferred/Not Set>`. Bucket ACL grants `<permission>` to AllUsers. This grants unauthenticated access to perform the granted actions. Write ACLs grant actors the ability to write objects into the bucket. `<ownership-specific warning>` Remove public ACL grants and migrate to BucketOwnerEnforced to disable ACLs. Manage access through cross-account IAM roles or bucket policies with scoped conditions."

**5. Non-BucketOwnerEnforced + AuthenticatedUsers ACL grant:**
- severity: critical
- body: "Bucket ownership is set to `<ObjectWriter/BucketOwnerPreferred/Not Set>`. Bucket ACL grants `<permission>` to AuthenticatedUsers. This grants permission to all AWS authenticated accounts. Write ACLs grant actors the ability to write objects into the bucket. `<ownership-specific warning>` Remove AuthenticatedUsers ACL grants and migrate to BucketOwnerEnforced to disable ACLs. Manage access through cross-account IAM roles or bucket policies with scoped conditions."

**6. Non-BucketOwnerEnforced + LogDelivery group ACL only + no cross-account policy:**
- severity: info
- body: "Bucket ownership is set to `<ObjectWriter/BucketOwnerPreferred/Not Set>`. Bucket ACL grants `<permission>` to the S3 LogDelivery group. This is a legacy pattern for S3 server access log delivery. No cross-account access found in bucket policy. `<ownership-specific warning>` Consider migrating to BucketOwnerEnforced to disable ACLs. S3 server access log can use bucket policy grants to the `logging.s3.amazonaws.com` service principal instead."

**7. Non-BucketOwnerEnforced + cross-account CanonicalUser ACL grant + no cross-account policy:**
- severity: warning
- body: "Bucket ownership is set to `<ObjectWriter/BucketOwnerPreferred/Not Set>`. Bucket ACL grants `<permission>` to account `<canonical user ID>`. Cross-account access is configured through ACLs. `<ownership-specific warning>` Consolidate cross-account access by migrating to BucketOwnerEnforced to disable ACLs and manage access through cross-account IAM roles or bucket policies with scoped conditions."

**8. Non-BucketOwnerEnforced + cross-account CanonicalUser ACL grant + cross-account policy principals:**
- severity: warning
- body: "Bucket ownership is set to `<ObjectWriter/BucketOwnerPreferred/Not Set>`. Bucket ACL grants `<permission>` to account `<canonical user ID>`, and bucket policy also grants access to cross-account principal(s) `<principal ARN(s)>`. Cross-account access is configured through both ACLs and bucket policy. `<ownership-specific warning>` Consolidate cross-account access by migrating to BucketOwnerEnforced to disable ACLs and manage access through cross-account IAM roles or bucket policies with scoped conditions."

## Server access logging

**Input:** `checks.logging` + `checks.cloudtrail`

**Conditional logic:**
- If logging is configured: check the log key format (date-based partitioning vs default)
- If logging is NOT configured: evaluate CloudTrail data from the collector

**Finding summaries:**

**1. Logging enabled + date-based partitioning:**
- severity: success
- body: "S3 server access logging is enabled. Logs are delivered to `<target bucket>` with prefix `<prefix>` using date-based partitioning (`<EventTime/DeliveryTime>`)."

**2. Logging enabled + no date-based partitioning (simple prefix):**
- severity: info
- body: "S3 server access logging is enabled. Logs are delivered to `<target bucket>` with prefix `<prefix>` using the default key format. Consider enabling date-based partitioning to improve query performance and reduce the amount of data scanned."

**3. Not configured + CloudTrail S3 data events found:**
- severity: success
- body: "S3 server access logging is not configured. CloudTrail trail `<trail name>` has S3 data events enabled covering this bucket, providing API-level audit logging."

**4. Not configured + no CloudTrail S3 data events:**
- severity: warning
- body: "S3 server access logging is not configured and no CloudTrail S3 data events were found for this bucket. There is no request-level audit trail for this bucket. Enable S3 server access logging or CloudTrail S3 data events to maintain an audit record of access to this bucket."

**5. Not configured + unable to verify CloudTrail (AccessDenied/ToolingFailure):**
- severity: warning
- body: "S3 server access logging is not configured. Unable to verify CloudTrail S3 data events — insufficient permissions to describe trails or get event selectors. Investigate permissions and verify that request-level logging is enabled through either S3 server access logging or CloudTrail."

## Website hosting

**Input:** `checks.website` + `checks.cors` + `checks.bpa_bucket` + `checks.bpa_account` + `checks.bucket_policy`

**Conditional logic:**
- If website hosting is NOT configured: report and skip sub-checks
- If website hosting IS enabled: analyze CORS and anonymous access using collected data

**Finding summaries:**

**1. Not configured:**
- severity: success
- body: "S3 static website hosting is not configured."

**2. Website enabled + anonymous GetObject access is working (BPA not blocking + Allow present + no overriding Deny):**
- severity: info
- body: "S3 static website hosting is enabled. Anonymous read access is granted and not blocked. CORS `<is configured with N rules / is not configured>`. Buckets configured for static website hosting are intended for public content. Ensure sensitive or critical data is not stored in this bucket. Separate public content from sensitive data by using dedicated buckets for each purpose."

**3. Website enabled + anonymous GetObject access is blocked:**
- severity: warning
- body: "S3 static website hosting is enabled, but anonymous `s3:GetObject` access is blocked by: `<list of blockers>`. Website requests will be blocked. Resolve by either disabling website hosting if it is not needed, or addressing the access blocks if public website access is intended. CORS `<is configured with N rules / is not configured>`. Buckets configured for static website hosting are intended for public content. Ensure sensitive or critical data is not stored in this bucket. Separate public content from sensitive data by using dedicated buckets for each purpose."

Where `<list of blockers>` includes whichever apply:
- Block Public Access enabled at the `<bucket/account>` level
- Bucket policy does not grant `s3:GetObject` to `Principal: "*"`
- Bucket policy contains an explicit Deny on `s3:GetObject` for anonymous access `<unconditionally / with conditions: condition keys>`
- No bucket policy configured

**4. Website enabled + anonymous GetObject access is partially blocked (Allow present + conditional Deny, no BPA):**
- severity: warning
- body: "S3 static website hosting is enabled. Bucket policy grants anonymous read access (`s3:GetObject` to `Principal: \"*\"`), but also contains an explicit Deny on `s3:GetObject` for anonymous access with conditions (`<condition keys>`). Website requests matching those conditions will be blocked. Verify this is intentional. CORS `<is configured with N rules / is not configured>`. Buckets configured for static website hosting are intended for public content. Ensure sensitive or critical data is not stored in this bucket. Separate public content from sensitive data by using dedicated buckets for each purpose."
