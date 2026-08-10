# Changelog

All notable changes to this skill are documented here. New entries go at the top.

## [1.0.1] - 2026-08-10

### Added
- README: "Agent Types" and "Uploading to AWS DevOps Agent" sections (GitHub import,
  zip upload, and Asset API deployment paths), aligned with sibling skills.

### Changed
- `metadata.agent-types` now includes **Incident RCA** alongside Chat tasks and
  Evaluation, matching the README.

### Fixed
- Data collection: `GetBucketVersioning` and `GetBucketLogging` return a successful
  but empty response (no `Status` / no `LoggingEnabled`) when the feature was never
  configured, rather than raising a `NoSuch*` error. The error classification now
  maps an empty success to `NotConfigured` for these two calls instead of `OK`, so a
  never-versioned or never-logged bucket is classified and reported correctly.

## [1.0.0] - 2026-07-29

### Added
- Initial release for AWS DevOps Agent, adapted from the AWS Support Specialist
  `storage-s3-resiliency-expertise` skill.
- Read-only resiliency, security, and data protection review of Amazon S3 buckets
  across nine dimensions: versioning, replication, object lock, bucket policy,
  block public access, default encryption, ownership controls, server access
  logging, and static website hosting.
- Automatic single-bucket vs multi-bucket (fleet) routing by input count, including
  batched review with manifest tracking and resume for 21+ buckets.
- Resiliency Rating (High / Medium / Low / Indeterminate) with per-dimension
  findings and remediation guidance.
- Self-contained data collection via read-only control-plane API calls
  (`use_aws`); no AWS profile or credentials requested from the user.
- Pre-flight permissions and tooling-availability handling that reports unverifiable
  checks instead of inferring configuration state.
- Final Delivery Contract: the report is emitted as a persisted artifact (when the
  runtime supports it) and returned verbatim in the final response, preventing the
  host agent from summarizing or reformatting the output. Runtime-neutral so it also
  applies when the skill is ported to other agents. The contract also mandates the
  full standard report regardless of how the request is phrased ("is it safe",
  "audit", "DR posture", etc.) — no condensed or "focused view" variants.
- Object Lock finding body states that Object Lock can be enabled on an existing
  versioned bucket (corrects the outdated "creation-time only" assumption).

### Changed (from the source skill)
- Replaced the prior data-acquisition layer and separate configuration-collector
  dependency with a self-contained `use_aws` control-plane collection reference.
- Removed the AWS profile prompt and per-run profile caching (DevOps Agent operates
  under an assumed role in the target account).
- Corrected the Object Lock guidance to reflect that Object Lock can be enabled on
  existing versioned buckets.
- Removed all internal Amazon references.
