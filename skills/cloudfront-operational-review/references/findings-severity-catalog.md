# CloudFront Findings & Severity Catalog

Organized by AWS Well-Architected pillar. Maps directly to the checks in `SKILL.md` Step 9.
Each row: the check, the condition that raises it, and the assigned severity. Severities are
starting points — escalate for tier-1 / regulated (PCI, HIPAA) workloads.

## Severity Definitions

| Severity | Definition | SLA |
|----------|------------|-----|
| CRITICAL | Immediate risk to availability, security, or data integrity | 24–48 hours |
| HIGH | Significant gap that could lead to incidents | 1 week |
| MEDIUM | Notable improvement opportunity | 30 days |
| LOW | Minor optimization or hardening | When convenient |
| INFO | Observation, no action required | N/A |

## Security

| Check | Condition | Severity |
|---|---|---|
| WAF web ACL attached | `WebACLId` empty on internet-facing distribution | HIGH (CRITICAL for auth'd/sensitive apps) |
| Public S3 origin | S3 origin with no OAC/OAI and public bucket policy | CRITICAL |
| Origin Access Control | S3 origin using legacy OAI instead of OAC | MEDIUM |
| TLS minimum protocol | `MinimumProtocolVersion` ≤ `TLSv1.1_2016` | HIGH |
| TLS minimum protocol | `MinimumProtocolVersion` = `TLSv1.2_2018/2019` (not 2021) | LOW |
| Viewer protocol policy | `allow-all` (plain HTTP permitted) | HIGH |
| Custom SSL certificate | Custom CNAME served on default `*.cloudfront.net` cert | MEDIUM |
| Field-level encryption | Sensitive form fields (PII/PCI) without FLE | MEDIUM |
| Signed URLs / cookies | Private content without `TrustedKeyGroups` | MEDIUM |
| Geo restriction | Geo/regulatory-locked content with `RestrictionType=none` | LOW–MEDIUM |
| Security response headers | No response headers policy (HSTS, X-Content-Type-Options) | LOW |
| Lambda@Edge / Functions | Untracked edge functions altering security behavior | INFO–LOW |

## Reliability

| Check | Condition | Severity |
|---|---|---|
| Origin failover | Critical distribution, single origin, no origin group | MEDIUM (HIGH for tier-1) |
| Origin error trend | Custom-origin 502/503/504 rising over 7 days | HIGH |
| 5xx error rate | `5xxErrorRate` 7-day avg > 1% | HIGH |
| 5xx error rate | `5xxErrorRate` 7-day avg > 5% | CRITICAL |
| VPC origin backing health | Backing ALB/NLB unhealthy or single-AZ | HIGH |
| Connection settings | `ConnectionAttempts=1` with no failover | MEDIUM |
| Origin timeouts | Default timeouts unsuited to a slow origin | MEDIUM |
| Distribution status | Stuck `InProgress` well beyond deploy window | MEDIUM |

## Performance

| Check | Condition | Severity |
|---|---|---|
| Cache hit ratio | `CacheHitRate` 7-day avg < 90% | MEDIUM |
| Cache hit ratio | `CacheHitRate` 7-day avg < 80% | HIGH |
| Compression | `Compress=false` on text/JSON/HTML behaviors | MEDIUM |
| HTTP version | `HttpVersion` not `http2and3` (no HTTP/3) | LOW |
| HTTP version | HTTP/1.1 only | MEDIUM |
| Origin Shield | High-traffic distribution without Origin Shield | LOW–MEDIUM |
| Origin latency | `OriginLatency` 7-day avg > 250 ms | MEDIUM |
| Origin latency | `OriginLatency` 7-day avg > 1000 ms | HIGH |
| Cache key hygiene | Cache policy forwards unnecessary headers/cookies/query strings | MEDIUM |

## Cost Optimization

| Check | Condition | Severity |
|---|---|---|
| Price class | `PriceClass_All` for region-limited audience | MEDIUM |
| Cache hit ratio → cost | `CacheHitRate` < 90% raising origin/data-transfer cost | MEDIUM |
| Unused distribution | `Enabled=false` or `Requests` 7-day sum ≈ 0 | MEDIUM |
| Additional metrics on idle | Monitoring subscription on near-zero-traffic distribution | LOW |
| Cost-allocation tags | Missing `Environment`, `Owner`, `CostCenter`, `Application` | LOW |

## Operational Excellence

| Check | Condition | Severity |
|---|---|---|
| CloudWatch alarms | Missing alarm on `5xxErrorRate` / `TotalErrorRate` | MEDIUM |
| CloudWatch alarms | Missing alarm on `OriginLatency` / `CacheHitRate` | MEDIUM |
| Standard logging | `Logging.Enabled=false` (no access logs) | MEDIUM |
| Additional metrics | `GetMonitoringSubscription` disabled | LOW–MEDIUM |
| Real-time logs | Absent for high-value distribution needing sub-minute visibility | LOW |
| Default root object | Not set on a website distribution | LOW |
| Operational tags | Missing `Environment`, `Owner`, `Runbook`, `OnCall` | LOW |

## Best-Practices Checklist (quick pass/fail)

### Security
- [ ] WAF web ACL attached to internet-facing distributions
- [ ] `MinimumProtocolVersion` ≥ `TLSv1.2_2021`
- [ ] `ViewerProtocolPolicy` = `redirect-to-https` or `https-only`
- [ ] S3 origins locked down with **OAC** (no public bucket access; OAI only as legacy)
- [ ] Custom domains use an ACM certificate (not the default cert)
- [ ] Field-level encryption for sensitive fields where applicable
- [ ] Signed URLs/cookies (key groups) for private content
- [ ] Geo restriction configured where required
- [ ] Response headers policy adds HSTS + security headers

### Reliability
- [ ] Origin groups configured for automatic failover on critical distributions
- [ ] VPC origin backing target healthy and multi-AZ
- [ ] `5xxErrorRate` within error budget (< 1%)
- [ ] Sensible `ConnectionAttempts` / timeouts for the origin type

### Performance
- [ ] `CacheHitRate` ≥ 95%
- [ ] Compression enabled on compressible content types
- [ ] `HttpVersion` = `http2and3`
- [ ] Origin Shield enabled for high-traffic / cache-consolidation needs
- [ ] Cache key scoped to only necessary headers/cookies/query strings

### Cost Optimization
- [ ] Price class matches audience geography
- [ ] Cache hit ratio high enough to minimize origin fetch cost
- [ ] No enabled-but-idle distributions
- [ ] Cost-allocation tags applied

### Operational Excellence
- [ ] Alarms on `5xxErrorRate`, `TotalErrorRate`, `OriginLatency`, `CacheHitRate`
- [ ] Standard access logging enabled
- [ ] Monitoring subscription (additional metrics) enabled
- [ ] Operational tags present (`Environment`, `Owner`, `Runbook`, `OnCall`)
