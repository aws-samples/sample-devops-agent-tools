---
name: cloudfront-operational-review
description: Comprehensive Amazon CloudFront operational review aligned with the AWS
  Well-Architected Framework and CloudFront best practices. Use this skill when a user
  asks to review, audit, or assess CloudFront distributions or a CDN for best-practices
  compliance, security posture (WAF, TLS, OAC), reliability (origin failover), caching
  and performance, cost optimization, or operational readiness. Triggers on requests
  like "CloudFront review", "CDN audit", "review my distribution", "CloudFront best
  practices", "CloudFront health check", "audit my CloudFront security", "why is my
  CloudFront cache hit ratio low", or "ORR for CloudFront" — even when the user names
  a distribution ID or domain without saying "CloudFront" explicitly.
metadata:
  author: derekzie
  version: "1.0.0"
  aws-devops-agent-skills.agent-types: "Chat tasks, Evaluation"
  aws-devops-agent-skills.aws-services: "Amazon CloudFront"
  aws-devops-agent-skills.technical-domains: "Networking / Content Delivery"
---

# CloudFront Operational Review

Conduct a comprehensive operational review of Amazon CloudFront distributions aligned
with the [AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html)
and the [Amazon CloudFront Developer Guide](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Introduction.html)
best-practices guidance (security, performance, reliability, and cost).

This skill uses the **AWS CloudFront, CloudWatch, CloudWatch Logs, WAFv2, ACM, and Cost
Explorer APIs only** — no internal tooling, custom scripts, or non-AWS MCP servers. All
data is collected through native, **read-only** AWS APIs available to the DevOps Agent's
primary cloud-source role. The skill never calls a mutating (`Create*`, `Update*`,
`Delete*`, `Associate*`, `Tag*`) API.

## When to Use

Activate this skill when the user asks to:
- Review, audit, or assess CloudFront distributions or their CDN configuration
- Check CloudFront best-practices compliance
- Evaluate CloudFront security (WAF, TLS, OAC), reliability, caching/performance, or cost
- Perform a CloudFront operational readiness review (ORR)
- Investigate distribution health, high error rates, or low cache hit ratio
- Assess whether a distribution follows AWS Well-Architected guidance

## Step 1: Identify Target Resources

Ask the user which distributions to review. Accept:
- Specific distribution IDs (e.g. `E1A2B3C4D5E6F7`) or their CloudFront domain names
  (e.g. `d111111abcdef8.cloudfront.net`) or alternate domain names (CNAMEs)
- "all distributions" in the account
- A tag-based scope (e.g. "all distributions tagged `Environment=prod`")

If no scope is given, default to **all distributions in the account**.

> **CloudFront is a global service.** Distributions are not regional — you do **not**
> iterate per region to list them. `cloudfront.*` calls are made against the global
> endpoint (SDKs route these through `us-east-1`). See "Known API Quirks".

## Step 2: Discover CloudFront Distributions

```
cloudfront.ListDistributions          # all distributions (paginate with Marker/NextMarker)
cloudfront.GetDistribution            # per distribution — status, ARN, DomainName, ETag
cloudfront.GetDistributionConfig      # per distribution — full editable config
```

Capture per distribution:
- `Id`, `ARN`, `DomainName`, `Status` (Deployed/InProgress), `Enabled`, `LastModifiedTime`
- `Aliases` (CNAMEs) and `PriceClass` (`PriceClass_All` / `PriceClass_200` / `PriceClass_100`)
- `DefaultRootObject`, `HttpVersion` (http1.1 / http2 / http2and3), `IsIPV6Enabled`
- `WebACLId` (WAF association — empty string means none)
- `DefaultCacheBehavior` and each `CacheBehaviors` item:
  `ViewerProtocolPolicy`, `AllowedMethods`, `Compress`, `CachePolicyId`,
  `OriginRequestPolicyId`, `ResponseHeadersPolicyId`, `FieldLevelEncryptionId`,
  `TrustedKeyGroups` / `TrustedSigners`, `FunctionAssociations`, `LambdaFunctionAssociations`
- `Origins` and `OriginGroups`: per origin — `DomainName`, `OriginAccessControlId`,
  legacy `S3OriginConfig.OriginAccessIdentity`, `CustomOriginConfig`
  (`OriginProtocolPolicy`, `OriginSslProtocols`, timeouts), `VpcOriginConfig`,
  `OriginShield`, `ConnectionAttempts`, `ConnectionTimeout`
- `ViewerCertificate`: `CloudFrontDefaultCertificate` (true = `*.cloudfront.net` default
  cert), `ACMCertificateArn` / `IAMCertificateId`, `MinimumProtocolVersion`, `SSLSupportMethod`
- `Restrictions.GeoRestriction` (`RestrictionType`, `Items`)
- `Logging` (legacy standard/S3 access logging: `Enabled`, `Bucket`, `Prefix`)

## Step 3: Discover Configuration Dependencies

For each distribution, resolve the referenced policies and associated resources:

```
cloudfront.ListCachePolicies                 # resolve CachePolicyId → TTLs, key settings
cloudfront.ListOriginRequestPolicies         # resolve OriginRequestPolicyId
cloudfront.ListResponseHeadersPolicies       # resolve ResponseHeadersPolicyId (HSTS, CORS)
cloudfront.ListOriginAccessControls          # OAC inventory (signing behavior)
cloudfront.ListCloudFrontOriginAccessIdentities   # legacy OAI inventory
cloudfront.ListFieldLevelEncryptionConfigs   # field-level encryption
cloudfront.ListKeyGroups                     # signed URL / signed cookie key groups
cloudfront.ListFunctions                     # CloudFront Functions (viewer request/response)
cloudfront.ListVpcOrigins                    # VPC origins inventory (regional resource)
cloudfront.GetVpcOrigin                      # per VPC origin — backing ALB/NLB/EC2 ARN, status
cloudfront.ListTagsForResource              # tags — param is `Resource` = distribution ARN
```

For WAF and TLS:

```
wafv2.GetWebACLForResource   # Scope=CLOUDFRONT, ResourceArn=<distribution ARN>, region us-east-1
acm.DescribeCertificate      # for ACMCertificateArn — MUST be called in us-east-1 for CloudFront
```

`LambdaFunctionAssociations` reference Lambda@Edge function versions (their ARNs live in
`us-east-1`). Record the associated function ARNs and event types (`viewer-request`,
`origin-request`, `origin-response`, `viewer-response`).

## Step 4: Discover Logging and Monitoring Configuration

```
cloudfront.GetMonitoringSubscription   # per distribution — are additional CloudWatch metrics enabled?
cloudfront.ListRealtimeLogConfigs      # real-time log configs (Kinesis) and their fields
```

Also record from Step 2 whether **standard access logging** (`Logging.Enabled`) is on and
which S3 bucket receives logs. Newer distributions may log to CloudWatch Logs / S3 /
Firehose via the logging v2 configuration — note the destination if present.

## Step 5: Collect CloudWatch Metrics (7-Day Historical)

**One** `cloudwatch.GetMetricData` call per distribution. Namespace `AWS/CloudFront`.
`Period: 21600` (6 hours). `StartTime`: 7 days ago. `EndTime`: now.

> **Metrics are global and live in `us-east-1`.** Always query CloudWatch in `us-east-1`
> regardless of where origins are. Default metrics use dimensions
> `DistributionId=<id>` **and** `Region=Global`. Additional metrics use
> `DistributionId=<id>` and `Region=Global` and are only populated when a
> **monitoring subscription** is enabled (Step 4).

### 5.1 Default metrics (always available, dimension `DistributionId` + `Region=Global`)

| id | metricName | stat | unit |
|----|------------|------|------|
| `requests` | Requests | Sum | count |
| `bytesDown` | BytesDownloaded | Sum | bytes |
| `bytesUp` | BytesUploaded | Sum | bytes |
| `err4xx` | 4xxErrorRate | Average | % |
| `err5xx` | 5xxErrorRate | Average | % |
| `errTotal` | TotalErrorRate | Average | % |

### 5.2 Additional metrics (only if monitoring subscription enabled)

| id | metricName | stat | unit |
|----|------------|------|------|
| `cacheHit` | CacheHitRate | Average | % |
| `originLatency` | OriginLatency | Average | ms |
| `err401` | 401ErrorRate | Average | % |
| `err403` | 403ErrorRate | Average | % |
| `err404` | 404ErrorRate | Average | % |
| `err502` | 502ErrorRate | Average | % |
| `err503` | 503ErrorRate | Average | % |
| `err504` | 504ErrorRate | Average | % |

If additional metrics are not enabled, record that as an Operational Excellence finding and
derive cache hit ratio from standard access logs (Step 6) instead.

Fetch `cloudwatch.DescribeAlarmsForMetric` (in `us-east-1`) for the key metrics
(`5xxErrorRate`, `TotalErrorRate`, `OriginLatency`, `CacheHitRate`) per distribution so the
report can flag missing alarms.

Full threshold table: `references/metrics-thresholds.md`.

## Step 6: Collect Logs (7-Day)

### 6.1 Standard access logs
If `Logging.Enabled` is true, the logs land in the configured S3 bucket (fields include
`sc-status`, `x-edge-result-type`, `x-edge-response-result-type`, `time-taken`,
`cs-protocol`, `ssl-protocol`). If the agent has read access to the log bucket / a log
table (Athena, CloudWatch Logs), scan the 7-day window for these signals:

| Pattern / field value | What it indicates |
|-----------------------|-------------------|
| `x-edge-result-type = Error` | request errored at the edge |
| `x-edge-result-type = Miss` (high ratio) | low cache efficiency |
| `x-edge-response-result-type = Error` | error returned to viewer |
| `sc-status` 502 / 503 / 504 | origin connection / gateway failures |
| `sc-status` 504 + `NonS3OriginCommError` | origin timeout / connection failure |
| `x-edge-result-type = OriginShieldHit` | Origin Shield effectiveness |
| `ssl-protocol = TLSv1 / TLSv1.1` | deprecated TLS in use by viewers |

### 6.2 504 / origin-connection failure analysis
504s and origin errors typically map to one of: **origin timeout** (origin slower than
`OriginReadTimeout`), **connection refused / closed** (origin down or security-group
block), or **TLS negotiation failure** (origin cert / protocol mismatch, `OriginSslProtocols`
too restrictive). Correlate the 504/5xx rate from Step 5 with these log patterns and the
`OriginLatency` metric.

### 6.3 Real-time logs
If a real-time log config exists (Step 4), note the destination (Kinesis) and fields;
real-time logs give per-request granularity for deeper latency/error analysis.

## Step 7: Collect Events

CloudFront configuration changes and WAF actions surface through CloudTrail (management
events) rather than a CloudFront-native event API. If CloudTrail access is available, look
(last 14 days) for `UpdateDistribution`, `CreateInvalidation`, WAF `UpdateWebACL`, and ACM
certificate changes to correlate config drift with metric/log anomalies. If CloudTrail is
not in scope, rely on `LastModifiedTime` from `GetDistribution` and note the limitation.

## Step 8: Cost Data

Once per review (not per distribution):

```
costexplorer.GetCostAndUsage    # 3 months, GroupBy USAGE_TYPE,
                                # filter Service = "Amazon CloudFront"
```

CloudFront charges are driven by **data transfer out to the internet**, **HTTP/HTTPS
request counts**, and add-ons (real-time logs, Origin Shield, invalidations beyond the free
tier, additional CloudWatch metrics). Cost Explorer does not break down per distribution, so
attribute proportionally:

1. Get the most recent full month total CloudFront spend by `USAGE_TYPE`.
2. Weight per distribution by 7-day `BytesDownloaded` + `Requests` share across all reviewed
   distributions.
3. Note the estimate in the report with an "estimated" badge.

Cross-reference `PriceClass` and `CacheHitRate`: a lower cache hit ratio raises origin fetch
and data-transfer cost; a broader price class raises edge cost.

## Step 9: Analyze Against Best Practices

Evaluate ALL collected data across the Well-Architected pillars and assign a severity to
every finding: CRITICAL, HIGH, MEDIUM, LOW, or INFO. The full findings catalog with
severity rationale is in `references/findings-severity-catalog.md`.

### 9.1 Security
Ref: [Configuring secure access and restricting access to content](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/SecurityAndPrivateContent.html)

- **WAF**: no `WebACLId` on an internet-facing distribution → HIGH (CRITICAL for auth'd or
  sensitive apps). Confirm with `wafv2.GetWebACLForResource`.
- **TLS minimum protocol**: `MinimumProtocolVersion` of `SSLv3` / `TLSv1` / `TLSv1_2016` /
  `TLSv1.1_2016` → HIGH. Recommend `TLSv1.2_2021` or higher.
- **Viewer protocol policy**: `allow-all` (permits plain HTTP) → HIGH. Use
  `redirect-to-https` or `https-only`.
- **Origin access**: S3 origin reachable publicly / no OAC (or legacy OAI only) →
  HIGH. Prefer **Origin Access Control (OAC)** over legacy OAI; direct public S3 origin → CRITICAL.
- **Default certificate + custom domain**: serving a custom CNAME on the default
  `*.cloudfront.net` certificate → MEDIUM (should use ACM custom cert).
- **Field-level encryption**: sensitive form fields (PII/PCI) without field-level encryption
  → MEDIUM where applicable.
- **Signed URLs / cookies**: private content served without `TrustedKeyGroups` → MEDIUM
  where content is meant to be restricted.
- **Geo restriction**: regulatory/geo-locked content with `RestrictionType=none` → LOW/MEDIUM
  where applicable.
- **Response headers policy**: no security headers (HSTS, `X-Content-Type-Options`,
  `frame-options`) → LOW.

### 9.2 Reliability
Ref: [Optimizing high availability with CloudFront origin failover](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/high_availability_origin_failover.html)

- **Origin failover**: single custom origin with no **origin group** for a critical
  distribution → MEDIUM (HIGH for tier-1). Origin groups enable automatic failover on 5xx/timeouts.
- **Origin health**: custom-origin `502/503/504` trend rising (Step 5) → HIGH.
- **VPC origin backing health**: VPC origin whose backing ALB/NLB is unhealthy or in a single
  AZ → HIGH. Verify via `GetVpcOrigin` status.
- **Connection settings**: very low `ConnectionAttempts` (1) with no failover, or default
  timeouts unsuited to a slow origin → MEDIUM.
- **5xx / origin error rate**: `5xxErrorRate` 7-day avg > 1% → HIGH, > 5% → CRITICAL.

### 9.3 Performance
Ref: [Optimizing caching and availability](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/ConfiguringCaching.html)

- **Cache hit ratio**: `CacheHitRate` 7-day avg < 90% → MEDIUM, < 80% → HIGH. Investigate
  cache policy TTLs, cache-key includes (unnecessary headers/cookies/query strings), and
  cache-control at origin.
- **Compression**: `Compress=false` on text/JSON/HTML behaviors → MEDIUM (enables Gzip/Brotli).
- **HTTP version**: `HttpVersion` not `http2and3` (no HTTP/3) → LOW; HTTP/1.1 only → MEDIUM.
- **Origin Shield**: high-traffic distribution without Origin Shield → LOW/MEDIUM (reduces
  origin load, improves cache consolidation).
- **Origin latency**: `OriginLatency` 7-day avg > 250 ms → MEDIUM, > 1000 ms → HIGH.
- **Price class vs latency**: `PriceClass_100`/`200` while serving a global audience →
  LOW/MEDIUM (added latency for excluded regions) — balance against cost (Step 9.4).

### 9.4 Cost Optimization
Ref: [Reducing the cost of your CloudFront distribution](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/PriceClass.html)

- **Price class**: `PriceClass_All` where the audience is region-limited → MEDIUM (narrow it).
- **Low cache hit ratio → cost**: `CacheHitRate` < 90% raises origin fetch + data-transfer
  cost → MEDIUM (linked to 9.3).
- **Unused / disabled distributions**: `Enabled=false` or `Requests` 7-day sum ≈ 0 → MEDIUM
  (delete or consolidate).
- **Additional metrics on idle distributions**: monitoring subscription enabled on a
  near-zero-traffic distribution → LOW.
- **Cost-allocation tags**: missing `Environment`, `Owner`, `CostCenter`, `Application` →
  LOW.

### 9.5 Operational Excellence
Ref: [Monitoring CloudFront](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/monitoring-using-cloudwatch.html)

- **CloudWatch alarms**: missing alarms on `5xxErrorRate` / `TotalErrorRate`,
  `OriginLatency`, `CacheHitRate` → MEDIUM each.
- **Standard logging**: `Logging.Enabled=false` (no access logs) → MEDIUM.
- **Additional metrics subscription**: `GetMonitoringSubscription` shows disabled → LOW/MEDIUM
  (no `CacheHitRate` / `OriginLatency` / per-status error rates).
- **Real-time logs**: absent for a high-value distribution needing sub-minute visibility → LOW.
- **Tagging**: missing operational tags (`Environment`, `Owner`, `Runbook`, `OnCall`) → LOW.
- **Default root object**: not set on a website distribution → LOW (bare-domain requests 404).

## Step 10: Generate Report

Generate a separate shareable report artifact for **each distribution** reviewed.

Artifact naming: `cloudfront-review-<distribution-id>-<YYYY-MM-DD>.md`
Example: `cloudfront-review-E1A2B3C4D5E6F7-2026-07-17.md`

For each distribution, create the artifact as a Markdown document with:

### Report Header
```
# CloudFront Operational Review — <distribution-id>
Account: <account-id> | Service: Amazon CloudFront (global) | Date: <YYYY-MM-DD>
Domain: <d***.cloudfront.net> | Aliases: <CNAMEs> | Status: <Deployed/InProgress> | Enabled: <yes/no>
Price Class: <PriceClass_*> | HTTP: <http2and3> | WAF: <yes/no>
```

### Executive Summary
- Health: ✅ HEALTHY / ⚠️ WARNINGS / ❌ CRITICAL
- Finding counts by severity
- Top 3 critical/high items

### Configuration Snapshot
| Item | Value |
| Origins | domain(s), type (S3/custom/VPC), OAC/OAI, protocol policy, timeouts |
| Origin groups | failover configured? members |
| Cache behaviors | count, viewer protocol policy, cache policy, compression |
| Security | WAF web ACL, TLS min version, viewer protocol, cert (ACM/default), FLE |
| Access control | signed URLs/cookies (key groups), geo restriction |
| Edge compute | CloudFront Functions, Lambda@Edge associations |
| Delivery | price class, HTTP version, IPv6, Origin Shield |
| Observability | standard logging, monitoring subscription, real-time logs, alarms |

### Findings by Pillar
For each of Security, Reliability, Performance, Cost, Operational Excellence:

| # | Finding | Severity | Current State | Recommendation |

### CloudWatch Metrics (7-Day)
| Metric | Stat | 7-Day Value | Status | Finding |

### Log Analysis (7-Day)
| Pattern / status | Occurrences or rate | Severity | Finding |
Include the 504 / origin-connection-failure breakdown (timeout vs refused vs TLS).

### Configuration Change / Event Notes
`LastModifiedTime` and any CloudTrail-visible `UpdateDistribution` / WAF / ACM changes.

### Cost Summary
- Latest-month estimated cost for this distribution (proportional split, "estimated" badge)
- Top 3 cost-optimization opportunities (linked to findings)

### Priority Matrix
| # | Finding | Severity | Pillar | Effort | Impact |

### Next Steps
- Immediate (CRITICAL/HIGH — 7 days)
- Short-term (MEDIUM — 30 days)
- Long-term (LOW — 90 days)

### Appendix — Reference Links
- [Amazon CloudFront Developer Guide](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Introduction.html)
- [Security in CloudFront](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/security.html)
- [Restricting access to an S3 origin with OAC](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)
- [Origin failover](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/high_availability_origin_failover.html)
- [Using Origin Shield](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/origin-shield.html)
- [Managed cache policies](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-cache-policies.html)
- [Monitoring CloudFront with CloudWatch](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/monitoring-using-cloudwatch.html)
- [CloudFront pricing / price classes](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/PriceClass.html)
- [AWS WAF with CloudFront](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/distribution-web-aws-waf.html)
- [Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html)

## Severity Definitions

| Severity | Definition | SLA |
|----------|------------|-----|
| CRITICAL | Immediate risk to availability, security, or data integrity | Fix within 24–48 hours |
| HIGH | Significant gap that could lead to incidents | Fix within 1 week |
| MEDIUM | Notable improvement opportunity | Plan within 30 days |
| LOW | Minor optimization or hardening | Address when convenient |
| INFO | Observation, no action required | N/A |

## Known API Quirks (recorded so the agent doesn't trip on them)

- **CloudFront is GLOBAL.** `cloudfront.*` calls use the global endpoint (SDKs sign against
  `us-east-1`). Do **not** loop per region to list distributions.
- **Metrics live in `us-east-1`.** Query `AWS/CloudFront` in `us-east-1`. Default metrics use
  dimensions `DistributionId` + `Region=Global`; additional metrics also use `Region=Global`
  and require a monitoring subscription.
- **WAF for CloudFront is global scope.** Call `wafv2.GetWebACLForResource` with
  `Scope=CLOUDFRONT` from `us-east-1`, passing the distribution ARN as `ResourceArn`.
- **ACM certs for CloudFront must be in `us-east-1`.** Call `acm.DescribeCertificate` in
  `us-east-1` even if origins are elsewhere.
- **VPC origins are a REGIONAL resource** even though the distribution is global. The backing
  ALB/NLB/EC2 lives in a specific region; resolve health there.
- **`cloudfront.ListTagsForResource` parameter is `Resource`** (the distribution ARN) — not
  `ResourceName` (which is the RDS convention). Different services differ here.
- **Pagination uses markers.** `ListDistributions`, `ListVpcOrigins`, `ListCachePolicies`,
  `ListRealtimeLogConfigs`, etc. paginate with `Marker` / `NextMarker`, not `NextToken`.
- **`WebACLId` empty string means no WAF** — treat `""` as "not attached", not "unknown".
- **`GetDistributionConfig` vs `GetDistribution`.** Config returns the editable body (+ETag);
  `GetDistribution` adds status/ARN/domain metadata. Use both as needed; this is read-only.

## Data Source Boundaries

This skill explicitly does **not** call:
- Any mutating CloudFront API (`CreateDistribution`, `UpdateDistribution`,
  `CreateInvalidation`, `DeleteDistribution`, `TagResource`, etc.).
- Internal Amazon tooling, custom scripts, or non-AWS MCP servers.

It stays self-contained on the AWS DevOps Agent's primary cloud-source IAM role using
read-only `cloudfront`, `cloudwatch`, `logs`, `wafv2`, `acm`, and `ce` actions. If the agent
lacks access to the standard access-log S3 bucket, note the limitation in the report and
rely on CloudWatch metrics (Step 5) for error and cache analysis.
