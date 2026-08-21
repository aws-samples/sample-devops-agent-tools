---
name: eos-cost-analysis
description: Discovers AWS resources approaching or past End of Standard Support and
  calculates Extended Support cost impact. Use when a user asks about end of support,
  extended support costs, version deprecation, EOS analysis, or upgrade planning for
  EKS, RDS, Lambda, ElastiCache, or OpenSearch. This skill enumerates resources via
  AWS APIs, classifies their EOS status, retrieves live Extended Support pricing from
  the AWS Pricing API, and produces a cost-impact report with per-resource breakdown.
metadata:
  author: rajatgoy
  version: "1.0.0"
  aws-devops-agent-skills.agent-types: "Chat tasks, Custom agents"
  aws-devops-agent-skills.aws-services: "Amazon EKS, Amazon RDS, AWS Lambda, Amazon ElastiCache, Amazon OpenSearch Service"
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

## Extended Support Pricing

Pricing is retrieved live from the AWS Pricing API (`pricing:GetProducts`) — see Phase 3, Step 3.2. Tier behavior differs by service:

- **EKS**: $0.50/cluster/hour, flat (no Year 3 escalation). This is the ES premium on top of the $0.10 standard cluster cost.
- **RDS**: $0.10/vCPU/hour (Year 1-2), $0.20/vCPU/hour (Year 3)
- **ElastiCache**: per-node rate varies by node type; Year 3 is 2× Year 1-2
- **OpenSearch**: $0.0065 per Normalized Instance Hour, single tier
- **Lambda**: no Extended Support charge (deprecated runtimes are a security risk only)

After Year 3, AWS may force-upgrade the resource.

## Prerequisites

- IAM permissions for service discovery APIs (see list per service above)
- `pricing:GetProducts` and `pricing:GetAttributeValues` permissions (managed policy: `AWSPriceListServiceFullAccess`) for live Extended Support pricing
- For organization-wide analysis: resources across all associated accounts (via Agent Space cloud source associations)
- For EOS date validation and upgrade guidance: `verify_aws_claim` or AWS documentation search

## Constraints

- NEVER guess or estimate EOS dates — always verify from AWS documentation, per exact engine + version
- NEVER reuse one version's EOS date for another version, even within the same service — verify each version independently
- NEVER fabricate pricing rates — retrieve live rates from the AWS Pricing API (Step 3.2)
- When calling the Pricing API, ALWAYS extract only `pricePerUnit` — never dump the full PriceList (responses are very large)
- If a version's EOS date is not found, mark it as "UNVERIFIED — check AWS docs"
- If a pricing usagetype returns empty, use `pricing:GetAttributeValues` to discover the correct format before giving up
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

4. **State scope and estimate scan size, then proceed** (do NOT block on a prompt):
   - Announce: "Analyzing [service(s)] across [N accounts] in [regions]."
   - Estimate scan size before starting: roughly `services × accounts × regions` List calls (plus per-resource Describe calls). Report the rough magnitude so the user knows what to expect.
   - **Large-scope warning:** if the scope is large (e.g., >10 accounts, or all services × all regions × many accounts), add a heads-up that this may take a while and involve hundreds of API calls — but still proceed automatically.
   - Default behavior: all services, all associated accounts, all regions — proceed WITHOUT asking, unless the user narrows it. Only the scope announcement and size estimate are required; no confirmation gate.

---

## Phase 2: Resource Discovery

For each account and region in scope, call the appropriate service APIs.

**PAGINATION (MANDATORY):** All discovery APIs paginate. You MUST follow pagination tokens to retrieve ALL resources — never rely on the first page. Continue calling with the `NextToken` / `Marker` / `nextToken` until no token is returned:
- `lambda:ListFunctions` → `NextMarker` (default page size 50)
- `rds:DescribeDBInstances` / `rds:DescribeDBClusters` → `Marker`
- `eks:ListClusters` → `nextToken`
- `elasticache:DescribeCacheClusters` → `Marker`
- `elasticache:DescribeReplicationGroups` → `Marker`
- `elasticache:DescribeServerlessCaches` → `NextToken`
- `opensearch:ListDomainNames` → returns all in one call (no pagination)

Under-counting due to skipped pages is a correctness failure. Always exhaust pagination.

### EKS Discovery

```
use_aws: eks:ListClusters (region: <region>)
→ For each cluster name:
  use_aws: eks:DescribeCluster (name: <cluster_name>, region: <region>)
  → Extract: clusterName, version, arn, createdAt
```

**Record:** account_id, region, cluster_name, cluster_arn, kubernetes_version

### RDS Discovery

RDS Extended Support applies to both standalone RDS instances AND Aurora clusters. Query BOTH APIs — Aurora version/engine data comes from the cluster API, not the instance API.

```
use_aws: rds:DescribeDBInstances (region: <region>)   # standalone RDS + Aurora member instances
→ For each instance:
  → Extract: DBInstanceIdentifier, Engine, EngineVersion, DBInstanceClass,
             MultiAZ, DBInstanceArn, DBClusterIdentifier

use_aws: rds:DescribeDBClusters (region: <region>)    # Aurora clusters (authoritative engine version)
→ For each cluster:
  → Extract: DBClusterIdentifier, Engine, EngineVersion, DBClusterArn,
             DBClusterMembers (instance identifiers)
```

**Filter:** Include `Engine` in: `postgres`, `mysql`, `mariadb`, `aurora-mysql`, `aurora-postgresql`, `oracle-*`, `sqlserver-*`.
**Aurora handling:** For Aurora, use the cluster's `EngineVersion` as the authoritative version (instance-level version may lag). Extended Support is billed per Aurora instance (vCPU-based), so map each cluster member instance's class to vCPUs.
**Record:** account_id, region, resource_id, engine, engine_version, instance_class, multi_az, arn, is_aurora, cluster_id

**Instance class to vCPU mapping** (required for cost calculation):
- Look up the vCPU count for each instance class using documentation search (`verify_aws_claim`) or the AWS Pricing API product attributes
- Common mappings: db.t3.micro=2, db.t3.small=2, db.t3.medium=2, db.r5.large=2, db.r5.xlarge=4, db.r5.2xlarge=8, db.r5.4xlarge=16, db.r6g.large=2, db.r6g.xlarge=4, db.r6g.2xlarge=8
- If an instance class is not in this list, verify its vCPU count rather than guessing

### Lambda Discovery

```
use_aws: lambda:ListFunctions (region: <region>)
→ For each function:
  → Extract: FunctionName, Runtime, FunctionArn, LastModified
```

**Filter:** Include functions whose runtime is deprecated or approaching deprecation.
**Do NOT pattern-match against a hardcoded runtime list** — deprecated runtimes change frequently. Instead, collect the DISTINCT set of runtimes in use, then verify each one's deprecation status at runtime via documentation search (`verify_aws_claim`: "AWS Lambda [runtime] deprecation date" / "AWS Lambda runtime deprecation schedule"). Flag any runtime that is past its deprecation date or has a deprecation date within 6 months.
**Record:** account_id, region, function_name, runtime, arn, last_modified

### ElastiCache Discovery

```
use_aws: elasticache:DescribeCacheClusters (region: <region>, ShowCacheNodeInfo: true)
→ For each cluster:
  → Extract: CacheClusterId, Engine, EngineVersion, CacheNodeType, NumCacheNodes, ARN

use_aws: elasticache:DescribeReplicationGroups (region: <region>)   # for clustered/replicated deployments
→ Extract group-level engine version where applicable

use_aws: elasticache:DescribeServerlessCaches (region: <region>)    # ElastiCache Serverless — SEPARATE resource type
→ For each serverless cache:
  → Extract: ServerlessCacheName, Engine, MajorEngineVersion, FullEngineVersion, ARN
```

**Serverless caches are a distinct resource type** — they are NOT returned by `DescribeCacheClusters`. You MUST call `DescribeServerlessCaches` separately or serverless caches on an EOS engine version will be silently missed. Serverless uses `MajorEngineVersion` / `FullEngineVersion` (there is no node type / node count); its ES cost model is usage-based rather than per-node, so price it from the Pricing API for the serverless usagetype rather than the node-based formula.
**Filter:** Include ALL engines that offer Extended Support — `redis`, `valkey`, and `memcached`. Do NOT limit to Redis only. Verify each engine+version's EOS status in Phase 3 rather than pre-filtering on "deprecated" here (discover everything, classify later).
**Record (node-based):** account_id, region, cluster_id, engine, engine_version, node_type, num_nodes, arn
**Record (serverless):** account_id, region, serverless_cache_name, engine, engine_version, is_serverless=true, arn

### OpenSearch Discovery

```
use_aws: opensearch:ListDomainNames (region: <region>)
→ For each domain name:
  use_aws: opensearch:DescribeDomain (DomainName: <name>, region: <region>)
  → Extract: DomainName, EngineVersion, ClusterConfig.InstanceType,
             ClusterConfig.InstanceCount, ARN
```

**Engine family split (date-bleed risk):** `EngineVersion` comes back prefixed as either `OpenSearch_2.x` or `Elasticsearch_7.x`. These are TWO different engine families with COMPLETELY different EOS calendars — an OpenSearch version's EOS date must never be applied to an Elasticsearch version, or vice versa. Parse the prefix, treat `Elasticsearch_*` and `OpenSearch_*` as separate engines during EOS validation (Phase 3), and verify each independently. The Pricing API usagetype may also differ between the two families — resolve it per family, don't assume one covers both.
**Record:** account_id, region, domain_name, engine_family (OpenSearch|Elasticsearch), engine_version, instance_type, instance_count, arn

### Discovery Summary

After discovery completes, present a summary that makes COVERAGE provable — a reader must be able to confirm every discovery path actually ran, even when it found nothing chargeable:

- **APIs called per service** — explicitly confirm which discovery APIs were invoked, especially the ones that are easy to skip: `elasticache:DescribeServerlessCaches`, `elasticache:DescribeReplicationGroups`, `rds:DescribeDBClusters`. If an API was not called for a region, say so.
- **Total resources found per service** (count of ALL discovered, not just affected).
- **Every discovered resource must be accounted for**, including SUPPORTED ones. Do NOT silently drop supported resources — list them (at minimum a per-service, per-engine count of SUPPORTED resources) so the reader can tell "scanned and supported" apart from "never scanned". A bare "12 supported" with no breakdown is not acceptable.
- **Unique versions detected** (with counts) and their classification.
- **Accounts and regions with resources.**

This visibility is what lets a reviewer distinguish a genuine clean result (e.g., serverless caches found but on supported engines) from a coverage gap (serverless never queried). The final report (Phase 5) MUST carry this through: include a "Discovered but SUPPORTED (no charge)" section listing those resources or per-engine counts.

---

## Phase 3: EOS Classification and Pricing Validation

### Step 3.1 — Validate EOS Dates

For each unique version discovered, verify its End of Standard Support date independently:

1. Search AWS documentation for the EXACT engine + version combination discovered (e.g., the specific engine family AND version number as reported by the discovery API — do not generalize).
2. Search AWS documentation: "[service] version lifecycle" / "[service] supported versions calendar"
3. Extract: `eos_date` (end of standard support), `es_end_date` (end of extended support)

**CRITICAL — Per-version verification (avoid date bleed):**
- Verify the EOS date for EACH distinct version SEPARATELY. Never reuse one version's date for another version, even within the same service.
- Many AWS services have MULTIPLE engine families or generations with completely different lifecycles (for example, a service may have a legacy engine line and a newer engine line that share a console but have separate support calendars). Treat each engine family + version as its own lookup.
- Match the EOS date to the EXACT version string returned by discovery. If discovery returns an engine name and a version number, both must match the documentation entry you cite.
- If two versions appear to have the same EOS date, double-check — identical dates across clearly different versions/engines is a red flag that a date was incorrectly copied.

**Sanity checks before accepting an EOS date:**
- Does the date make chronological sense given the version's release date? (Older versions should have earlier EOS dates than newer ones of the same engine.)
- Is a version that is clearly several generations behind the current release being reported as "supported with a future EOS date"? If so, re-verify — it may already be past EOS.

If documentation search returns no result for a specific version:
- Mark as `UNVERIFIED`
- Note in the report: "EOS date could not be confirmed from AWS documentation — verify manually"
- Do NOT substitute a similar version's date as a proxy

### Step 3.2 — Get Extended Support Pricing (AWS Pricing API)

**Use the AWS Pricing API (`pricing:GetProducts`) for authoritative, real-time Extended Support pricing.** Do NOT use documentation search or model knowledge for pricing.

**CRITICAL — Response size:** The Pricing API returns large JSON. ALWAYS use the exact usagetype patterns below and extract ONLY `pricePerUnit`. Never dump the full PriceList into context.

**Pricing API constraints:**
- Region: call in `us-east-1` (Pricing API is only available in us-east-1 and ap-south-1)
- Requires `pricing:GetProducts` and `pricing:GetAttributeValues` permissions
- The `regionCode` filter targets the region you're pricing for (e.g., `us-east-1`), separate from the API endpoint region

**Per-service pricing lookup:**

| Service | ServiceCode | Usage Type Pattern | Region Prefix | Billing Unit | Year 3 |
|---------|-------------|-------------------|---------------|--------------|--------|
| EKS | `AmazonEKS` | `{PREFIX}-AmazonEKS-Hours:extendedSupport` | Yes (USE1, USW2...) | per cluster/hour | Flat (no escalation) |
| RDS | `AmazonRDS` | `ExtendedSupport:Yr1-Yr2:{Engine}` / `ExtendedSupport:Yr3:{Engine}` | **NONE — no prefix; regionCode filter only** | per vCPU/hour | 2× Yr1-2 |
| ElastiCache | `AmazonElastiCache` | `{PREFIX}-ExtendedSupportYr1_Yr2-NodeUsage:{nodeType}` / `{PREFIX}-ExtendedSupportYr3-NodeUsage:{nodeType}` | Yes | per node/hour | 2× Yr1-2 |
| OpenSearch | `AmazonES` | `{PREFIX}-OpenSearchExtendedSupport` | Yes | per NIH/hour | Single tier (no escalation) |
| Lambda | — | No Extended Support charge | — | — | — |

**RDS is the exception — its ExtendedSupport usagetype has NO region prefix.** Do NOT prepend `USE1-`/`EU-`/etc. to RDS usagetypes. The correct RDS pattern is exactly `ExtendedSupport:Yr1-Yr2:{Engine}` / `ExtendedSupport:Yr3:{Engine}` (e.g. `ExtendedSupport:Yr1-Yr2:AuroraMySQL2`, `ExtendedSupport:Yr3:PostgreSQL`) with the target region supplied ONLY via the `regionCode` filter. Constructing `USE1-ExtendedSupport...` returns null and causes wasted retry loops — for RDS, skip the prefix entirely. (EKS, ElastiCache, and OpenSearch DO use a region prefix; RDS does not.)

**Region prefix mapping** (for services that use a prefix — EKS, ElastiCache, OpenSearch; NOT RDS):

The AUTHORITATIVE way to get the region prefix is the Pricing API — do NOT rely on a hardcoded prefix for any region you are not certain about. For ANY region outside the common set below, call `pricing:GetAttributeValues` on the `usagetype` attribute (filtered by ServiceCode) and match the prefix for your target `regionCode`. This is the DEFAULT method, not a fallback.

Common prefixes (safe to use directly; still verify via API if a lookup returns empty):
- us-east-1 → `USE1` | us-east-2 → `USE2` | us-west-1 → `USW1` | us-west-2 → `USW2`
- eu-west-1 → `EU` (or `EUW1`) | eu-west-2 → `EUW2` | eu-central-1 → `EUC1`
- ap-northeast-1 → `APN1` | ap-southeast-1 → `APS1` | ap-southeast-2 → `APS2`
- ca-central-1 → `CAN1` | sa-east-1 → `SAE1`

Regions you MUST resolve via the API (prefixes vary / are not reliably guessable — includes ap-south-1, eu-north-1, eu-south-1, me-south-1, af-south-1, ap-east-1, and any region not listed above): call `pricing:GetAttributeValues` and read the actual `usagetype` prefix rather than guessing. Never construct a usagetype from an unverified prefix — a wrong prefix silently returns empty and causes a resource to be under-costed as $0.

**Lookup procedure per affected resource:**

1. Build the usagetype string using the pattern + region prefix + resource attribute (engine/nodeType as applicable)
2. Call `pricing:GetProducts`:
   ```
   use_aws: pricing:GetProducts (
     region: "us-east-1",
     ServiceCode: "<ServiceCode>",
     Filters: [
       {Type: "TERM_MATCH", Field: "usagetype", Value: "<constructed usagetype>"},
       {Type: "TERM_MATCH", Field: "regionCode", Value: "<target region>"}
     ]
   )
   ```
   **Apply a `jmespath_filter` at the API call to return ONLY the price**, so the large PriceList JSON never enters context. Pricing API returns each product as a JSON-encoded string in `PriceList`; filter/parse down to the `pricePerUnit.USD` value inside the on-demand price dimensions rather than pulling the whole payload. Filtering at the call level is more reliable than asking the model to extract it afterward.
3. Extract ONLY `pricePerUnit.USD` from the response — ignore everything else
4. If the exact usagetype returns empty, call `pricing:GetAttributeValues` for the `usagetype` attribute to discover the correct format, then retry

**Known base rates (us-east-1, for validation — API is source of truth):**
- **EKS**: $0.50/cluster/hour (this is the Extended Support premium; the $0.10 standard rate applies regardless of ES)
- **RDS**: $0.10/vCPU/hour (Year 1-2), $0.20/vCPU/hour (Year 3)
- **ElastiCache**: ~$0.165/node/hour (Year 1-2), ~$0.330/node/hour (Year 3) — varies by node type
- **OpenSearch**: $0.0065/NIH/hour (single tier)
- **Lambda**: No charge — flag deprecated runtimes as security risk only

**IMPORTANT — Cost framing:** The rates above represent the AVOIDABLE Extended Support premium (what the customer eliminates by upgrading). For EKS, $0.50/hour is the ES surcharge on top of the $0.10 standard cluster cost the customer pays regardless. Report this premium as the cost exposure/savings opportunity.

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
1. Use `verify_aws_claim` (or AWS documentation search): "[service] upgrade from [version] to latest"
2. Extract: recommended target version, key breaking changes, migration guide URL

---

## Phase 4: Cost Calculation

### Step 4.1 — Determine pricing tier

Extended Support pricing tiers are based on how long a resource has been past End of Standard Support. The tier structure differs by service:

- Calculate months since EOS: `months_since_eos = (today - eos_date).months`
- **Year 1-2** — `months_since_eos < 24` (months 0–23 past EOS): base rate
- **Year 3** — `months_since_eos >= 24` (month 24 onward past EOS): elevated rate

Boundary is exclusive at 24: month 23 is still Year 1-2, month 24 is the first Year 3 month. No month falls in both tiers.

Tier behavior by service:
- **EKS**: flat rate — no Year 3 escalation (always $0.50/cluster/hour)
- **RDS**: Year 1-2 = $0.10/vCPU/hr, Year 3 = $0.20/vCPU/hr (2×)
- **ElastiCache**: Year 1-2 = base node rate, Year 3 = 2× node rate
- **OpenSearch**: flat rate — single tier, no escalation

**Use the exact rate returned by the Pricing API for the resource's tier (Step 3.2). Query the Yr3 usagetype pattern for resources 24+ months past EOS.**

### Step 4.2 — Calculate per-resource monthly cost

Use the rate from the Pricing API (Step 3.2), not hardcoded values. Formulas:

| Service | Formula |
|---------|---------|
| EKS | `monthly_cost = es_rate × 730` (es_rate = $0.50, flat) |
| RDS | `monthly_cost = vCPUs × es_rate × 730 × (2 if MultiAZ else 1)` (es_rate = $0.10 Yr1-2 / $0.20 Yr3 per vCPU) |
| ElastiCache | `monthly_cost = num_nodes × node_es_rate × 730` (node_es_rate from Pricing API for the node type + tier) |
| OpenSearch | `monthly_cost = total_NIH_per_hour × es_rate_per_NIH × 730` (NIH-based; es_rate from Pricing API) |
| Lambda | `monthly_cost = $0` (flag as security risk only) |

**Notes:**
- The EKS $0.50 rate is the Extended Support premium (the avoidable cost). The $0.10 standard cluster cost applies regardless and is NOT part of the exposure.
- For RDS, map instance class to vCPU count (e.g., db.r6g.xlarge = 4 vCPUs). Multi-AZ doubles the cost.
- For ElastiCache and OpenSearch, query the Pricing API per node type / instance type since rates vary.

**Deriving OpenSearch NIH (Normalized Instance Hours):** The formula needs `total_NIH_per_hour`, which is NOT the raw instance count. Compute it as:

```
total_NIH_per_hour = instance_count × NIH_factor(instance_type)
```

`NIH_factor` is the normalization multiplier for the instance size (AWS normalizes to a base unit). Do NOT hardcode these — the size-to-factor scale can change and varies by family. Resolve the factor at runtime:
1. Preferred: derive from the size within the family. AWS uses a doubling scale per size step (…medium=1, large=2, xlarge=4, 2xlarge=8, 4xlarge=16, 8xlarge=32, …). Confirm the family's base with documentation search (`verify_aws_claim`: "OpenSearch normalized instance hours factor [instance_type]").
   - **Sizes SMALLER than medium (small, micro) do NOT reliably follow the doubling scale — DO NOT extrapolate them as 0.5 / 0.25.** The fractional factor for small/micro search instances (and whether they are even eligible for Extended Support billing) must be looked up from AWS documentation for that specific size, not inferred. Extrapolating "medium=1 so small=0.5" is a guess and violates the never-guess rule. If the doc value for a small/micro size cannot be confirmed, mark the NIH factor `UNVERIFIED` and flag the resulting cost as an estimate rather than silently assuming a fraction.
2. If a domain has mixed instance types (data + dedicated master + warm nodes), sum NIH across each node group separately.
3. Multiply the summed `total_NIH_per_hour` by the per-NIH ES rate returned by the Pricing API (Step 3.2), then × 730 for monthly.

Never estimate NIH from instance count alone — that under- or over-counts cost by the normalization factor.

### Step 4.2b — Reactive (current) vs Proactive (projected) cost

Every affected resource has TWO cost figures. Compute both so the report shows what the customer pays now AND what they will pay if they do nothing:

- **Current monthly ES cost (reactive)** — what the resource is incurring RIGHT NOW.
  - `IN_EXTENDED_SUPPORT` / `PAST_EXTENDED_SUPPORT`: the Year 1-2 or Year 3 rate from Step 4.2. This is real spend today.
  - `APPROACHING_EOS`: **$0** — not yet in Extended Support, incurring nothing today.

- **Projected monthly ES cost (proactive)** — what the resource WILL cost once it enters Extended Support, if not upgraded first.
  - `APPROACHING_EOS`: compute the full Year 1-2 ES cost using the SAME per-resource formula (Step 4.2), as if it were already in ES. This is the cost that starts on its EOS date (within the next 6 months).
  - `IN_EXTENDED_SUPPORT` / `PAST_EXTENDED_SUPPORT`: projected = current (already incurring). If the resource will cross into Year 3 (`>= 24` months) within the projection window, project the Year 3 (elevated) rate and note the escalation.

This split is the whole point of the skill: **reactive** = stop the bleeding on resources already in ES; **proactive** = show the cost cliff approaching in the next 6 months so the customer can upgrade before it hits. Report both, never collapse them into one number.

### Step 4.3 — Assign urgency

| Urgency | Condition |
|---------|-----------|
| `CRITICAL` | `IN_EXTENDED_SUPPORT` or `PAST_EXTENDED_SUPPORT` (already incurring charges) |
| `HIGH` | `APPROACHING_EOS` within 3 months |
| `MEDIUM` | `APPROACHING_EOS` within 3–6 months |

### Step 4.4 — Aggregate totals

Calculate:
- **Total current monthly ES cost (reactive)** — sum of current cost across resources already in/past ES (excludes APPROACHING_EOS, which is $0 now)
- **Total current annual ES cost** (current monthly × 12)
- **Total projected monthly ES cost (proactive)** — sum of projected cost across ALL affected resources (includes APPROACHING_EOS at their future ES rate). This is the monthly run-rate if nothing is upgraded in the next 6 months.
- **Incremental cost cliff** — projected monthly − current monthly = the additional spend that switches on as APPROACHING_EOS resources cross their EOS dates
- **Top 10 most impacted accounts** (by projected monthly cost)
- **Cost by service** breakdown (current and projected)
- **Cost by urgency** breakdown

---

## Phase 5: Report Generation

### Step 5.1 — Present Summary

```
## EOS Cost Impact Summary

| Metric | Value |
|--------|-------|
| Current Monthly ES Cost (incurring now) | $X,XXX |
| Current Annual ES Cost | $XX,XXX |
| Projected Monthly ES Cost (if no upgrades in next 6 mo) | $X,XXX |
| Incremental Cost Cliff (projected − current) | $X,XXX/month |
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
- `es_year` — Which pricing year the resource is in: `1-2` or `3`. For resources not yet in Extended Support (`APPROACHING_EOS`), use the sentinel `N/A` (they are not incurring ES charges yet, so no pricing year applies).
- `current_monthly_es_cost` — Monthly ES cost being incurred RIGHT NOW ($0 for APPROACHING_EOS; the Year 1-2/Year 3 rate for resources already in/past ES)
- `projected_monthly_es_cost` — Monthly ES cost once the resource enters Extended Support (full ES rate for APPROACHING_EOS; equals current for resources already in ES, or the Year 3 rate if it escalates within the window)
- `es_start_date` — Date the projected cost begins (the EOS date for APPROACHING_EOS; blank/`N/A` if already incurring)
- `annual_es_cost` — Annual ES cost based on `projected_monthly_es_cost` × 12 (the forward-looking run-rate)
- `urgency` — CRITICAL / HIGH / MEDIUM
- `recommended_target` — Recommended upgrade version
- `upgrade_guide_url` — Link to AWS migration documentation

Sort by `projected_monthly_es_cost` descending (surfaces the biggest future exposure first).

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
| Documentation search returns no pricing | Set current_monthly_es_cost and projected_monthly_es_cost to "PRICING_UNAVAILABLE", do not estimate |
| No resources found for a service | Report: "No [service] resources found in scope" |
| Rate limiting (throttling) | Retry with exponential backoff (1s, 2s, 4s), max 3 retries |
| Timeout on large organization (1000+ accounts) | Report partial results, note which accounts were completed |

---

## Tips

- **Start with a single service** for faster results — EKS is the simplest (flat per-cluster rate)
- **RDS has the most complex pricing** due to vCPU mapping and Multi-AZ doubling
- **Lambda has no cost impact** but deprecated runtimes are a security risk worth flagging
- **Year 2 and Year 3 multipliers** significantly increase costs — resources past EOS for 2+ years may cost 2–3× more than Year 1 estimates suggest
- **Verify EOS dates from AWS documentation at runtime** — deprecation schedules shift, so confirm each engine + version rather than trusting cached knowledge
