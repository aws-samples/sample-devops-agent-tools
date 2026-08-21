# Changelog

## 1.0.0 (2026-08-20)

Initial release.

- System prompt for a dedicated/scheduled EOS Cost Analysis agent (pairs with the `eos-cost-analysis` skill)
- Supports 5 services: EKS, RDS/Aurora, Lambda, ElastiCache, OpenSearch
- Extended Support cost calculation with Year 1/2/3 tiered pricing and Multi-AZ doubling for RDS
- Single-account and organization-wide (cross-account) analysis via agent associations
- Never guesses EOS dates or pricing: EOS dates verified from AWS documentation per exact engine + version; pricing retrieved live from the AWS Pricing API
- Produces persisted CSV artifacts for week-over-week tracking
- Lambda deprecated-runtime flagging (security risk, no ES cost)
