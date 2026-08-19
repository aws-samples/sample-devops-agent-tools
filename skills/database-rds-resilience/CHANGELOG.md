```markdown
# Changelog

## 1.0.0

- Initial release: 66-blocker catalog across 7 categories (Failover Timing, Snapshot
  Restore, Encryption, KMS API Throttling, Cross-Region DR, Application-Layer
  Resilience Gaps, Account-Level Service Quotas)
- Topology-aware RTO/RPO calculation with 4-dimension scoring (Regional HA, Data
  Protection, Cross-Region DR, Application Resilience) summing to 100
- Read-only, AWS CLI + Service Quotas API only — no MCP, no database connection
- QT-07 (concurrent cross-region snapshot copies) corrected to the documented AWS
  default of 5 (was previously stated as 20 and inconsistent with the detection rule)
- Deduplicated detection rules that were previously defined twice in two separate
  YAML blocks (Quota Detection Rules and Detection Rules) into a single rule set
- Moved the 66-blocker catalog and remediation playbooks to `references/` to keep
  `SKILL.md` under the repository's ~500-line guideline
- Relabeled the Multi-AZ conversion remediation from "Zero Downtime" to "Deferred —
  Applies at Next Maintenance Window" with an explicit warning about the performance
  impact of initial standby synchronization and the failover risk of `--apply-immediately`
