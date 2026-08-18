# analytics-opensearch-expertise

A read-only Amazon OpenSearch Service **domain health assessment** skill for AWS DevOps Agent. Given a domain ARN (or domain name + region), it runs control-plane API checks across cluster health, shard strategy, performance, security posture, and cost optimization, then produces a structured findings report with prioritized recommendations.

## What it does

Analyzes an OpenSearch domain across five categories:

1. **Cluster Health & Configuration** — status, node/AZ balance, dedicated masters, upgrade eligibility
2. **Storage & Shard Strategy** — EBS volume type, shard density, active vs. total shards, free storage
3. **Performance** — JVM pressure, CPU, search/indexing latency, HTTP errors, cluster status history
4. **Security & Access** — encryption at rest, node-to-node encryption, HTTPS enforcement, network exposure, access policy, fine-grained access control
5. **Cost Optimization** — instance right-sizing, storage tiering, reserved instance coverage

It is **100% control-plane / API-driven** — no data-plane access (no `_cluster/*` calls, no index reads), which makes it compatible with DevOps Agent without a custom MCP server.

## Prerequisites

**IAM permissions** the DevOps Agent execution role needs (all read-only):

- `es:DescribeDomain`
- `es:DescribeDomainHealth`
- `es:DescribeDomainConfig`
- `es:GetCompatibleVersions`
- `es:DescribeReservedInstances`
- `es:ListDomainNames`
- `es:ListTags`
- `cloudwatch:GetMetricData`

Most are covered by `AIDevOpsAgentAccessPolicy`; attach the supplemental policy for any gaps.

**AWS resources:** one or more OpenSearch Service domains in the target account/region.

## How to use it with DevOps Agent

1. Zip this skill directory and upload it to your DevOps Agent Space.
2. Select relevant subagents (Chat, Investigations / Incident RCA).
3. Prompt in natural language without naming the skill, e.g.:
   - "Is my OpenSearch domain healthy? ARN: `arn:aws:es:us-east-1:123456789012:domain/my-domain`"
   - "Review the cluster configuration and security posture of this OpenSearch domain."
   - "Are there any cost savings available on my OpenSearch setup?"
4. Review the agent's reasoning trace to confirm the skill activated and the checks ran.

If the agent does not invoke the skill, refine the `description` field in `SKILL.md` (see "Optimizing description" in the Agent Skills specification).

## Non-production disclaimer

> ⚠️ This skill is sample code, not intended for production use without additional review and testing. Users should validate in a non-production environment first.

## Maintainers

- genealpe
- prasadnu
