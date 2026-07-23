# Changelog

All notable changes to the `acm-certificate-ops-review` skill are documented
in this file. The format is based on Keep a Changelog, and this project follows
semantic versioning.

## [1.0.0] - 2026-07-12

Authors: Tejas Majamudar (majamuda), Manoj Gaddam (vmgaddam)

### Added
- Initial release of the ACM Certificate Operations Review skill.
- Phase 1 operational investigation runbook: certificate inventory across
  accounts/regions, issue detection (expiry, renewal health, validation
  failures, imported-in-use, weak keys, unused certs, missing DaysToExpiry
  monitoring, stale endpoints, ACM Private CA), risk classification, and a
  prioritized findings report.
- Reference files: `acm-thresholds.md`, `report-format.md`, and
  `cab-forum-readiness.md` (Phase 2 CA/Browser Forum readiness).
- Read-only IAM permission set documented in the README.
