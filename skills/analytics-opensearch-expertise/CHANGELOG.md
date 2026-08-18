## [2.6.0] - 2026-07-24
### Changed
- Remediation Reference moved to `references/remediation-reference.md`, loaded on demand via
  `read_skill_resource` (mandatory Execution Flow step) — keeps SKILL.md compact so late-file
  instructions are never truncated at skill ingestion.
### Added
- API compatibility guidance: the DevOps Agent tooling exposes the legacy
  Elasticsearch-compatible client; legacy operation names listed first with modern fallbacks.
- CloudWatch fallback for DescribeDomainHealth (Shards.active / Shards.unassigned /
  ClusterStatus.*) so shard checks are never skipped; namespace and dimensions stated
  explicitly (AWS/ES, DomainName + ClientId).
- Category roll-up rule for the executive summary (worst finding wins).
- Instance RAM reference table for heap estimation.
- Explicit SKIPPED paths for 2.4 (metric unavailable) and 5.3 (AccessDenied); 3.7
  currently-yellow reporting clarified.

# Changelog

All notable changes to the `analytics-opensearch-expertise` skill are documented here.

## [2.5.0] - 2026-07-24
### Added
- Severity-override prohibition: verdict tiers come only from the matching Logic line, never
  from general judgment; inline "do not escalate" guards on checks 4.1, 4.2, and 4.6.
- Check-ID fidelity rule: canonical 24-entry ID-to-name list embedded in the coverage rule;
  renumbering, splitting, merging, or inventing checks is prohibited.

## [2.4.0] - 2026-07-23
### Changed
- Security severity rebalance: 4.1 (encryption at rest), 4.2 (node-to-node encryption),
  4.3 (HTTPS), 4.5 (access policy), and 4.6 (FGAC) are Warnings when a control is disabled
  in isolation; 4.4 compound exposure (public + HTTPS off + FGAC off) is the only security
  Critical. Criticals are reserved for compounded exposure and imminent operational risk.
- 3.3/3.4 latency checks demoted to informational-only: the millisecond markers are
  heuristics with no AWS-documented threshold; latency is workload-dependent.
- 3.7 renamed "Cluster Status Duration" to "Cluster Status History".
- 1.4 version counting rule: "releases behind" is the count of later same-major versions
  returned by ListVersions, never the numeric delta between version numbers.
- 1.4 fallback provenance note now lives inside the finding only, phrased for the reader;
  API error names never appear in the report.

## [2.3.0] - 2026-07-21
### Added
- Remediation Reference: a 24-entry verbatim dictionary (why-it-matters, resolution steps,
  and verified official AWS documentation links). Every non-passing finding pulls its entry
  verbatim; documentation links come only from the dictionary, eliminating URL hallucination.
### Changed
- 4.4 Network Exposure escalation logic changed from OR to AND: Critical only when public
  endpoint, HTTPS off, and FGAC off are all true; a single weak control remains a Warning
  at 4.4 (it is still flagged by its own check).
- 1.4 upgrade candidates render as a set of direct single-hop targets, never a sequential
  chain; upgrade recommendations propose a single hop to the latest same-major version.

## [2.2.0] - 2026-07-21
### Fixed
- 2.4 free-storage formula: the FreeStorageSpace Minimum statistic is per-node; the
  percentage is computed against one node's volume (the previous formula understated free
  space by the node count).
- 3.1 pinned to the Maximum statistic; Average is reported as context only.
### Added
- 1.4 fallback: derive upgrade candidates via ListVersions when GetCompatibleVersions is
  unavailable; skip only if both APIs fail.
- Deterministic tier boundaries for 2.2 (shard density bands) and 3.6 (tier determined
  only by 5xx count).
- Target Resolution rules: exact match proceeds; no target triggers discovery with options;
  a near-miss target stops for explicit confirmation and is never silently substituted.
- Coverage rule hardening: no tier rounding by judgment; CloudWatch rows name the statistic
  used; mandatory 24-row matrix self-count.

## [2.1.0] - 2026-07-14
### Changed
- 2.2 shard density computes the best-practice limit from the actual instance heap
  (heap = min(32, RAM/2) GiB x 25 shards) instead of a fixed assumption; Critical tier
  added at 4x the limit.
- 5.1 right-sizing rule made explicitly conjunctive (CPU < 20% AND JVM < 50%); suppressed
  when JVM pressure is at or above 50%.

## [1.0.0] - 2026-07-11
### Added
- Initial release: 24 read-only control-plane checks across cluster health, storage and
  shard strategy, performance, security posture, and cost optimization, with a structured
  findings report, coverage matrix, and prioritized recommendations.
