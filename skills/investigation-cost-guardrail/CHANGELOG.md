# Changelog

## 2.1.0

  - Add `references/pricing-reference.md` to resolve the per-region rate for a paid operation at estimation time.
  - It ships Price List Query API lookup templates for the operations the skill estimates most often (CloudWatch Logs Insights, `GetMetricData`, Contributor Insights, X-Ray, Athena, S3 requests).
  - `SKILL.md` loads it on the first operation Layer 2 classifies as PAID and reuses it for the rest of the investigation; baseline rates are indicative only.


## 2.0.0

- Expand the scope from a fixed CloudWatch/X-Ray/CloudTrail set to all AWS service with the agent's own tools; `aws-services` metadata changes to `All`.
- Add three-layer classification — heuristic rules for unlisted operations, a known-paid registry with per-operation formulas (Athena, DynamoDB, S3, Kinesis, SageMaker, Lambda), and post-execution validation of metered response fields.
- Add native tool classification and PromQL cost controls for `get_prometheus_metrics` (series × datapoints, 500-series cap, step-size and time-range guidance).
- Expand the cost-efficient suggestions offered on cancel into generic and service-specific alternatives with quantified savings.
- Add per-investigation budget enforcement ($10 default) with a running cost accumulator.


## 1.0.0

- Initial version.
- Estimate downstream AWS API cost before any paid call, covering CloudWatch Logs Insights, `GetMetricData`, X-Ray, Contributor Insights, and Live Tail.
- Derive every involved region from resource ARNs, the triggering alarm, and topology, with no region defaulting.
- Measure real volume via the CloudWatch `IncomingBytes` metric when a time window is provided.
- Render a per-step cost breakdown with running total and threshold before every proceed/cancel decision.
- Cancel when no time window is provided or the estimate meets the threshold (default $10), with more cost-efficient suggestions on cancel.
