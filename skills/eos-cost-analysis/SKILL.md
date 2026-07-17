---
name: eos-cost-analysis
description: Discovers AWS resources approaching or past End of Standard Support and
  calculates Extended Support cost impact. Use when a user asks about end of support,
  extended support costs, version deprecation, EOS analysis, or upgrade planning for
  EKS, RDS, Lambda, ElastiCache, or OpenSearch. This skill enumerates resources via
  AWS APIs, classifies their EOS status, validates dates and pricing against AWS
  documentation, and produces a cost-impact report with per-resource breakdown.
metadata:
  author: rajatgoy
  version: "1.0.0"
  aws-devops-agent-skills.agent-types: "Chat tasks, Custom agents"
  aws-devops-agent-skills.aws-services: "Amazon EKS, Amazon RDS, AWS Lambda, Amazon ElastiCache, Amazon OpenSearch Service, AWS Health"
  aws-devops-agent-skills.technical-domains: "Cost Optimization, Operations"
---

# EOS Cost Analysis

Discover AWS resources approaching End of Standard Support, calculate Extended
Support charges, and generate an actionable cost-impact report.


## When to Use This Skill

- User asks about End of Support, Extended Support costs, or version deprecation
- User wants to know which resources are incurring or will incur Extended Support charges
- User asks for upgrade planning or EOS posture assessment
- User wants a cost-impact report for deprecated service versions
- Proactive scheduled checks for EOS posture across the organization

## Supported Services (Phase 1 — Extended Support Charges)

| Service | Billing Unit | Discovery API |
|---------|-------------|---------------|
| Amazon EKS | Per cluster/hour | `eks:ListClusters`, `eks:DescribeCluster` |
| Amazon RDS/Aurora | Per vCPU/hour | `rds:DescribeDBInstances`, `rds:DescribeDBClusters` |
| AWS Lambda | No direct ES charge (security risk only) | `lambda:ListFunctions` |
| Amazon ElastiCache | Per node/hour | `elasticache:DescribeCacheClusters` |
| Amazon OpenSearch | Per instance/hour | `opensearch:ListDomainNames`, `opensearch:DescribeDomains` |

## Extended Support Pricing Tiers

Charges escalate based on how long a resource has been past End of Standard Support:

- **Year 1** (0–12 months past EOS): Base rate
- **Year 2** (12–24 months past EOS): 2× base rate
- **Year 3** (24–36 months past EOS): 3× base rate

After Year 3, AWS may force-upgrade the resource.

## Prerequisites

- IAM permissions for service discovery APIs (see list per service above)
- For organization-wide analysis: `organizations:ListAccounts` and `sts:AssumeRole` to a read-only role in member accounts
- For EOS date and pricing validation: `aws-knowledge-mcp-server` connected to the Agent Space
- AWS Health API access (Business Support+ plan) for `health:DescribeEvents`

## Constraints

- NEVER guess or estimate EOS dates — always verify from AWS documentation
- NEVER fabricate pricing rates — use only values confirmed via documentation search
- If a version is not found in documentation, mark it as "UNVERIFIED — check AWS docs"
- All output is AI-generated and must be independently verified before taking action
- Read-only operations only — do not modify any resources

---

## Phase 1: Scope Definition

1. **Identify target service(s)** from the user request:
   - If user specifies a service (e.g., "EKS EOS") → analyze that service only
   - If user says "all services" or "full EOS analysis" → analyze all 5 supported services
   - If unclear, ask: "Which service would you like me to analyze? (EKS, RDS, Lambda, ElastiCache, OpenSearch, or all)"

2. **Determine account scope:**
   - **Default: ALL associated accounts** — Scan resources in ALL AWS accounts connected to this Agent Space (primary + all secondary cloud sources)
   - The agent has access to multiple accounts through its cloud source associations — discover resources in EACH account, not just the primary
   - To identify available accounts: attempt resource discovery in all accounts the agent has access to. The Agent Space configuration determines which accounts are available.
   - If user says "only this account" or specifies an account ID → limit to that account

3. **Determine region scope:**
   - If user specifies regions → use those
   - Otherwise → scan all commercially available regions
   - Optimization: first check which regions have resources for the target service using a quick `List` call in each region

4. **Confirm scope** with the user:
   - "I'll analyze [service(s)] across [N accounts] in [regions]. Shall I proceed?"
   - In auto/scheduled mode: proceed without confirmation
   - Default behavior: all services, all associated accounts, all regions — unless user narrows it

---

## Phase 2: Resource Discovery

For each account and region in scope, call the appropriate service APIs.

### EKS Discovery

```
use_aws: eks:ListClusters (region: <region>)
→ For each cluster name:
  use_aws: eks:DescribeCluster (name: <cluster_name>, region: <region>)
  → Extract: clusterName, version, arn, createdAt
```

**Record:** account_id, region, cluster_name, cluster_arn, kubernetes_version

### RDS Discovery

```
use_aws: rds:DescribeDBInstances (region: <region>)
→ For each instance:
  → Extract: DBInstanceIdentifier, Engine, EngineVersion, DBInstanceClass,
             MultiAZ, DBInstanceArn, DBClusterIdentifier
```

**Filter:** Only include instances where `Engine` is `postgres`, `mysql`, or `aurora-*`.
**Record:** account_id, region, instance_id, engine, engine_version, instance_class, multi_az, arn

**Instance class to vCPU mapping** (required for cost calculation):
- Use `aws-knowledge-mcp-server` to search: "[instance_class] vCPU count"
- Common mappings: db.t3.micro=2, db.t3.small=2, db.t3.medium=2, db.r5.large=2, db.r5.xlarge=4, db.r5.2xlarge=8, db.r5.4xlarge=16, db.r6g.large=2, db.r6g.xlarge=4, db.r6g.2xlarge=8

### Lambda Discovery

```
use_aws: lambda:ListFunctions (region: <region>)
→ For each function:
  → Extract: FunctionName, Runtime, FunctionArn, LastModified
```

**Filter:** Only include functions with deprecated runtimes (python3.8, python3.7, nodejs16.x, nodejs14.x, dotnet6, java8, etc.)
**Record:** account_id, region, function_name, runtime, arn, last_modified

### ElastiCache Discovery

```
use_aws: elasticache:DescribeCacheClusters (region: <region>, ShowCacheNodeInfo: true)
→ For each cluster:
  → Extract: CacheClusterId, Engine, EngineVersion, CacheNodeType, NumCacheNodes, ARN
```

**Filter:** Only include clusters where `Engine` = `redis` and version is deprecated.
**Record:** account_id, region, cluster_id, engine_version, node_type, num_nodes, arn

### OpenSearch Discovery

```
use_aws: opensearch:ListDomainNames (region: <region>)
→ For each domain name:
  use_aws: opensearch:DescribeDomain (DomainName: <name>, region: <region>)
  → Extract: DomainName, EngineVersion, ClusterConfig.InstanceType,
             ClusterConfig.InstanceCount, ARN
```

**Record:** account_id, region, domain_name, engine_version, instance_type, instance_count, arn

### Discovery Summary

After discovery completes, present:
- Total resources found per service
- Unique versions detected (with counts)
- Accounts and regions with resources

---

## Phase 3: EOS Classification and Pricing Validation

### Step 3.1 — Validate EOS Dates

For each unique version discovered, verify its End of Standard Support date:

1. Search AWS documentation: "[service] [version] end of standard support date"
2. Search AWS documentation: "[service] version lifecycle calendar"
3. Extract: `eos_date` (end of standard support), `es_end_date` (end of extended support)

If documentation search returns no result for a version:
- Mark as `UNVERIFIED`
- Note in the report: "EOS date could not be confirmed from AWS documentation"

### Step 3.2 — Validate Extended Support Pricing

For each service with affected resources:

1. Search `aws-knowledge-mcp-server`: "[service] extended support pricing"
2. Extract: rate, billing unit, year-over-year escalation
3. Known pricing (validate against docs):
   - **EKS**: $0.60 per cluster per hour (Year 1)
   - **RDS**: $0.10 per vCPU per hour (Year 1)
   - **ElastiCache**: Varies by node type — must look up
   - **OpenSearch**: Varies by instance type — must look up
   - **Lambda**: No Extended Support charge (deprecated runtimes lose security patches only)

### Step 3.3 — Classify Each Resource

Compare each resource's version against the validated EOS dates:

| Status | Condition | Include in Report? |
|--------|-----------|-------------------|
| `IN_EXTENDED_SUPPORT` | EOS date is past, ES end date is future | YES — currently incurring charges |
| `APPROACHING_EOS` | EOS date is within 6 months from today | YES — will soon incur charges |
| `PAST_EXTENDED_SUPPORT` | Both EOS and ES end dates are past | YES — critical, may be force-upgraded |
| `SUPPORTED` | EOS date is more than 6 months away | NO — exclude from cost report |

### Step 3.4 — Search Upgrade Recommendations

For each affected version:
1. Search `aws-knowledge-mcp-server`: "[service] upgrade from [version] to latest"
2. Extract: recommended target version, key breaking changes, migration guide URL

---

## Phase 4: Cost Calculation

### Step 4.1 — Determine pricing year

For each resource classified as `IN_EXTENDED_SUPPORT` or `PAST_EXTENDED_SUPPORT`:
- Calculate months since EOS: `(today - eos_date).months`
- Year 1: 0–12 months → multiplier = 1
- Year 2: 12–24 months → multiplier = 2
- Year 3: 24–36 months → multiplier = 3

### Step 4.2 — Calculate per-resource monthly cost

| Service | Formula |
|---------|---------|
| EKS | `monthly_cost = $0.60 × 730 × year_multiplier` |
| RDS | `monthly_cost = vCPUs × $0.10 × 730 × year_multiplier × (2 if MultiAZ else 1)` |
| ElastiCache | `monthly_cost = num_nodes × node_rate × 730 × year_multiplier` |
| OpenSearch | `monthly_cost = instance_count × instance_rate × 730 × year_multiplier` |
| Lambda | `monthly_cost = $0` (flag as security risk only) |

### Step 4.3 — Assign urgency

| Urgency | Condition |
|---------|-----------|
| `CRITICAL` | `IN_EXTENDED_SUPPORT` or `PAST_EXTENDED_SUPPORT` (already incurring charges) |
| `HIGH` | `APPROACHING_EOS` within 3 months |
| `MEDIUM` | `APPROACHING_EOS` within 3–6 months |

### Step 4.4 — Aggregate totals

Calculate:
- **Total monthly ES cost** across all resources
- **Total annual ES cost** (monthly × 12)
- **Top 10 most impacted accounts** (by monthly cost)
- **Cost by service** breakdown
- **Cost by urgency** breakdown

---

## Phase 5: Report Generation

### Step 5.1 — Present Summary

```
## EOS Cost Impact Summary

| Metric | Value |
|--------|-------|
| Total Monthly Extended Support Cost | $X,XXX |
| Total Annual Extended Support Cost | $XX,XXX |
| Affected Resources | N |
| Affected Accounts | N |
| Services Analyzed | [list] |
| Regions Scanned | N |

### By Urgency
- CRITICAL: N resources — $X,XXX/month (currently incurring charges)
- HIGH: N resources — $X,XXX/month (entering ES within 3 months)
- MEDIUM: N resources — $X,XXX/month (entering ES within 3-6 months)

### By Service
- EKS: N clusters — $X,XXX/month
- RDS: N instances — $X,XXX/month
- ElastiCache: N nodes — $X,XXX/month
- OpenSearch: N instances — $X,XXX/month
- Lambda: N functions — $0 (security risk, no ES charge)

### Top Impacted Accounts
1. [account_name] ([account_id]) — $X,XXX/month
2. ...
```

### Step 5.2 — Generate CSV Artifact

Produce a CSV artifact with these columns:
- `account_id` — AWS account ID
- `account_name` — Account name (if available from Organizations)
- `resource_arn` — Full ARN of the resource
- `resource_name` — Human-readable identifier (cluster name, instance ID, function name)
- `region` — AWS region
- `service` — AWS service (EKS, RDS, Lambda, ElastiCache, OpenSearch)
- `version` — Current version/engine version/runtime
- `instance_class` — Instance type (for RDS, ElastiCache, OpenSearch) or N/A
- `multi_az` — true/false (RDS only)
- `eos_date` — End of Standard Support date
- `eos_status` — IN_EXTENDED_SUPPORT / APPROACHING_EOS / PAST_EXTENDED_SUPPORT
- `es_year` — Which pricing year (1, 2, or 3)
- `monthly_es_cost` — Monthly Extended Support cost for this resource
- `annual_es_cost` — Annual Extended Support cost (monthly × 12)
- `urgency` — CRITICAL / HIGH / MEDIUM
- `recommended_target` — Recommended upgrade version
- `upgrade_guide_url` — Link to AWS migration documentation

Sort by `monthly_es_cost` descending.

Title the artifact: "EOS Cost Impact Report — [date]"

### Step 5.3 — Provide Upgrade Recommendations

For each affected version, include:
- Current version → Recommended target version
- Key considerations or breaking changes
- AWS documentation link for the upgrade path
- Estimated effort: mention sequential upgrade requirements if applicable

### Step 5.4 — Lambda Security Risk Section (if applicable)

If deprecated Lambda runtimes were found:
```
## Security Risk: Deprecated Lambda Runtimes

These functions are running on deprecated runtimes that no longer receive
security patches. While no Extended Support charge applies, they represent
a security and compliance risk.

| Function | Runtime | Last Modified | Account |
|----------|---------|---------------|---------|
| ... | python3.8 | 2023-... | ... |
```

---

## Error Handling

| Error | Behavior |
|-------|----------|
| Role assumption fails for a linked account | Skip that account, note in report: "Account [ID] skipped — role assumption failed" |
| Service API returns AccessDenied | Skip that service/region, note: "Insufficient permissions for [service] in [region]" |
| Documentation search returns no EOS date | Mark version as "UNVERIFIED", include in report with note |
| Documentation search returns no pricing | Set monthly_es_cost to "PRICING_UNAVAILABLE", do not estimate |
| No resources found for a service | Report: "No [service] resources found in scope" |
| Rate limiting (throttling) | Retry with exponential backoff (1s, 2s, 4s), max 3 retries |
| Timeout on large organization (1000+ accounts) | Report partial results, note which accounts were completed |

---

## Tips

- **Start with a single service** for faster results — EKS is the simplest (flat per-cluster rate)
- **RDS has the most complex pricing** due to vCPU mapping and Multi-AZ doubling
- **Lambda has no cost impact** but deprecated runtimes are a security risk worth flagging
- **Year 2 and Year 3 multipliers** significantly increase costs — resources past EOS for 2+ years may cost 2–3× more than Year 1 estimates suggest
- **Check Health Dashboard** for upcoming scheduled deprecation events that may not yet be reflected in resource versions
