# Changelog

## 1.0.0 (2026-06-18)

- Initial release
- 5-phase workflow: Scope → Discover → Classify → Calculate → Report
- Service support: EKS, RDS/Aurora, Lambda, ElastiCache, OpenSearch
- EOS status classification: IN_EXTENDED_SUPPORT, APPROACHING_EOS, PAST_EXTENDED_SUPPORT
- Cost calculation with per-vCPU (RDS), per-cluster (EKS), per-node (ElastiCache/OpenSearch) formulas
- Multi-AZ cost doubling for RDS
- Year 1/2/3 pricing escalation
- Cross-account discovery via sts:AssumeRole
- Documentation validation via aws-knowledge-mcp-server
- CSV artifact output with upgrade recommendations
