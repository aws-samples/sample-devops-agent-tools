# CloudFront CloudWatch Metrics Thresholds Reference

All metrics retrieved via a single `cloudwatch.GetMetricData` call per distribution over a
7-day window with `Period=21600` (6h), in **`us-east-1`** (CloudFront metrics are global and
live in us-east-1). Severity reflects sustained values, not single spikes.

## Default Metrics (Namespace `AWS/CloudFront`, dimensions `DistributionId` + `Region=Global`)

Always available for every distribution at no additional charge.

| Metric | Stat | Normal | Warning | Critical | Finding |
|---|---|---|---|---|---|
| Requests | Sum | traffic baseline | — | ≈ 0 on an enabled dist | Idle distribution — candidate to disable/delete |
| BytesDownloaded | Sum | traffic baseline | — | — | Data-transfer cost driver; compare to cache hit ratio |
| BytesUploaded | Sum | traffic baseline | — | — | Upload-heavy patterns; validate methods allowed |
| 4xxErrorRate | Average | < 1% | > 2% | > 10% | Client errors — check auth, signed URLs, 403/404 causes |
| 5xxErrorRate | Average | < 0.5% | > 1% | > 5% | Origin/edge failures — investigate origin health & failover |
| TotalErrorRate | Average | < 1% | > 3% | > 10% | Overall error budget breach |

## Additional Metrics (require a monitoring subscription; dimensions `DistributionId` + `Region=Global`)

Only populated when `cloudfront.GetMonitoringSubscription` shows additional metrics enabled
(these incur CloudWatch charges). If disabled, this is an Operational Excellence finding and
cache hit ratio must be derived from standard access logs.

| Metric | Stat | Normal | Warning | Critical | Finding |
|---|---|---|---|---|---|
| CacheHitRate | Average | > 95% | < 90% | < 80% | Low cache efficiency — review cache policy TTLs & cache key |
| OriginLatency | Average | < 100 ms | > 250 ms | > 1000 ms | Slow origin — check origin scaling, Origin Shield, region |
| 401ErrorRate | Average | ≈ 0 | > 1% | > 5% | Auth failures at viewer |
| 403ErrorRate | Average | < 1% | > 2% | > 10% | Access denied — OAC/OAI, signed URL, WAF blocks, S3 perms |
| 404ErrorRate | Average | < 2% | > 5% | > 15% | Missing objects — default root object, routing, cache key |
| 502ErrorRate | Average | ≈ 0 | > 0.5% | > 2% | Bad gateway — origin TLS/protocol mismatch |
| 503ErrorRate | Average | ≈ 0 | > 0.5% | > 2% | Origin overloaded / capacity exceeded |
| 504ErrorRate | Average | ≈ 0 | > 0.5% | > 2% | Origin timeout / connection failure (NonS3OriginCommError) |

## 504 / Origin-Connection Failure Triage (map metric spikes to root cause)

| Symptom | Likely root cause | Where to confirm |
|---|---|---|
| 504 + high OriginLatency | Origin slower than `OriginReadTimeout` | Origin app latency, raise timeout or scale origin |
| 504 + connection refused/closed in logs | Origin down or security group blocks CloudFront | Origin health, SG ingress, VPC origin backing target |
| 502 + TLS/handshake errors | Origin cert expired or `OriginSslProtocols` mismatch | Origin cert, `OriginProtocolPolicy`, SSL protocols |
| 503 sustained | Origin capacity exceeded | Origin autoscaling, Origin Shield to absorb load |

## Standard Access Log Signals (7-day scan)

| Field / value | Severity if prevalent | Action |
|---|---|---|
| `x-edge-result-type = Error` | HIGH | Correlate with 5xx metric and origin logs |
| `x-edge-result-type = Miss` (high ratio) | MEDIUM | Improve cacheability (TTL, cache key) |
| `sc-status` 502/503/504 | HIGH | Origin failover / timeout / capacity review |
| `x-edge-result-type = OriginShieldHit` present | INFO | Origin Shield working as intended |
| `ssl-protocol = TLSv1 / TLSv1.1` | MEDIUM | Deprecated viewer TLS — raise `MinimumProtocolVersion` |

## Alarm Coverage Expectations

The skill flags any of these as **MEDIUM** when missing
(`cloudwatch.DescribeAlarmsForMetric` in `us-east-1` returns nothing for the distribution +
metric):

| Metric | Threshold (suggested) |
|---|---|
| 5xxErrorRate | > 1% for 5 minutes |
| TotalErrorRate | > 3% for 5 minutes |
| OriginLatency | > 1000 ms for 5 minutes (requires additional metrics) |
| CacheHitRate | < 80% for 15 minutes (requires additional metrics) |

## Notes

- Cache hit ratio, when computed from access logs, ≈ `Hit / (Hit + Miss + RefreshHit)` from
  `x-edge-result-type`. `RefreshHit` counts as a partial hit (revalidated at origin but not
  a full download).
- Error-rate metrics are percentages of total requests, already averaged by CloudFront — use
  `Average` stat, not `Sum`.
- Zero-traffic windows can make error-rate `Average` misleading; always read error rates
  alongside `Requests`.
