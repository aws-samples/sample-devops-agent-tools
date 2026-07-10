# MSK Operations — AWS DevOps Agent Skill

An Amazon MSK Provisioned operations, troubleshooting, and health-assessment
skill for [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html).
Covers Standard and Express brokers and both proactive operational reviews and
ad-hoc incident response (performance, consumer lag, storage, maintenance,
client tuning).

> ⚠️ **Non-production disclaimer.** This skill is sample code, not intended for
> production use without additional review and testing. Users should validate in
> a non-production environment first.

## Purpose

Give AWS DevOps Agent the domain knowledge to answer MSK questions accurately
without falling back on training data (which routinely conflates Standard and
Express broker behavior). The skill activates in two shapes:

1. **Operational review** — assess an MSK cluster's health against best
   practices and produce a prioritized findings report.
2. **Ad-hoc troubleshooting** — investigate a specific MSK symptom (high CPU,
   consumer lag, disk full, TrafficShaping, unexpected broker restart, etc.)
   and recommend the correct next action.

## Key Capabilities

- Determine broker type (Standard vs Express) and route to the correct diagnostic
  path — many CloudWatch metrics and behaviors differ between the two.
- Troubleshoot broker performance issues (`CpuUser`, `CpuSystem`,
  `RequestHandlerAvgIdlePercent`, `NetworkProcessorAvgIdlePercent`,
  `ProduceTotalTimeMsMean`, `TrafficShaping`).
- Diagnose consumer lag using `SumOffsetLag`, `MaxOffsetLag`, and (when
  `PER_TOPIC_PER_PARTITION` is enabled) per-partition `OffsetLag`.
- Manage broker storage — EBS scaling for Standard, `StorageUsed` monitoring for
  Express, tiered storage, retention planning.
- Recommend CloudWatch alarms in the `AWS/Kafka` namespace and validate that
  the right monitoring level is enabled.
- Explain rolling restart, patching, and version upgrade behavior; call out
  operations that are unsafe during `UnderReplicatedPartitions > 0`.
- Advise on Kafka client (producer / consumer) configuration — batch sizing,
  `linger.ms`, compression, `acks`, `min.insync.replicas` — and on
  authentication choices (IAM, SCRAM, mTLS).

## Prerequisites

### IAM permissions

The AWS DevOps Agent's primary cloud-source role needs read access to MSK and
CloudWatch. Most calls are covered by `AIDevOpsAgentAccessPolicy`; the
following actions are the ones the skill uses in practice:

```
kafka:DescribeClusterV2
kafka:ListClustersV2
kafka:ListNodes
kafka:GetBootstrapBrokers
kafka:ListClusterOperationsV2
kafka:DescribeConfigurationRevision
kafka:ListConfigurations
cloudwatch:GetMetricData
cloudwatch:GetMetricStatistics
cloudwatch:ListMetrics
cloudwatch:DescribeAlarms
logs:DescribeLogGroups
logs:FilterLogEvents
```

Write actions (`UpdateBrokerStorage`, `CreateConfiguration`,
`PutMetricAlarm`, etc.) are only recommended in the skill output — the operator
is expected to run them after review.

### Cluster access

- The MSK cluster must live in an account configured as a cloud source in your
  Agent Space.
- No data-plane / Kafka-protocol access is required. Everything the skill uses
  comes from the AWS control-plane APIs and CloudWatch.

## Limitations

- Covers **MSK Provisioned only** — does **not** cover MSK Connect, MSK
  Serverless, or MSK Replicator.
- No Kafka data-plane visibility. The skill cannot read topic contents, run
  `kafka-consumer-groups.sh`, or otherwise interact with the Kafka protocol.
- Some checks require monitoring level `PER_BROKER` or higher (thread pool idle
  %, `VolumeQueueLength`, `BwInAllowanceExceeded`, IAM connection metrics). At
  `DEFAULT` level the skill will explicitly note which checks are limited.
- Sizing questions (broker count, instance type choice, monthly cost) are
  redirected to the AWS documentation — the skill does not include a bundled
  sizing calculator.

## Agent Types

This skill is intended for the following agent types (selected in the Operator
Web App at upload time):

- **On-demand** — conversational invocation in Chat ("my MSK cluster is
  latent", "run an MSK health check on `prod-cluster`", "why did broker 2
  restart last night?").
- **Evaluation** — proactive best-practices recommendations.

Select **Generic** instead if you want the skill available to all agent types.

## Uploading to AWS DevOps Agent

> Reference: [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#uploading-a-skill)

### 1. Package the skill

From the `skills/` directory in this repo:

```bash
cd skills
zip -r msk-operations.zip msk-operations/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
```

The resulting `msk-operations.zip` contains:

```
msk-operations/
├── SKILL.md
└── references/
    ├── troubleshoot-performance.md
    ├── troubleshoot-consumer-lag.md
    ├── manage-storage.md
    ├── monitor-and-alarm.md
    ├── maintenance-operations.md
    └── configure-clients.md
```

Constraints (enforced at upload time):

- Total zip size ≤ **6 MB**.
- `SKILL.md` is required and must include `name` and `description` frontmatter.
- A `scripts/` directory is **not** allowed — uploads containing scripts are rejected.

### 2. Upload via the Operator Web App

1. Navigate to the **Skills** page in your Agent Space Operator Web App.
2. Click **Add skill** → **Upload skill**.
3. Drag and drop `msk-operations.zip` (or browse to it).
4. Select agent types: **On-demand** and **Evaluation** (or leave **Generic**
   to make it available to all agent types).
5. Review the validation results.
6. Click **Upload**.

## How to Use This Skill

In DevOps Agent Chat, use natural language. You do NOT need to mention the
skill name — the agent activates it based on the MSK / Kafka triggers in the
skill description.

### Ad-hoc troubleshooting prompts

- *"My MSK cluster `prod-orders` is showing high produce latency in `us-east-1`.
  What should I check?"*
- *"Consumer group `payments-consumer` on cluster `prod-orders` has growing
  lag. Diagnose it."*
- *"UnderReplicatedPartitions is > 0 on broker 2 of `prod-orders`. What's the
  safe next step?"*
- *"Why did broker 3 of `prod-orders` restart last night?"*
- *"Storage on `prod-orders` is at 82%. What are my options?"*
- *"TrafficShaping is firing on all three brokers of `prod-orders`."*

### Operational-review prompts

- *"Run an MSK operational review on cluster `prod-orders`."*
- *"Audit `prod-orders` against MSK best practices."*
- *"Are the CloudWatch alarms on `prod-orders` complete? Which ones am I
  missing?"*
- *"Health-check every MSK Provisioned cluster in `us-east-1`."*

### Client tuning prompts

- *"My Java Kafka producer is getting `NotEnoughReplicasException` when writing
  to MSK. What producer settings should I check?"*
- *"What should `linger.ms` and `batch.size` be for a producer writing 200 MB/s
  to a Standard broker cluster?"*

## Skill Contents

```
msk-operations/
├── SKILL.md
├── README.md
├── CHANGELOG.md
├── .skilleval.yaml
├── evals/
│   ├── evals.json
│   └── eval_queries.json
└── references/
    ├── troubleshoot-performance.md
    ├── troubleshoot-consumer-lag.md
    ├── manage-storage.md
    ├── monitor-and-alarm.md
    ├── maintenance-operations.md
    └── configure-clients.md
```

`README.md`, `CHANGELOG.md`, `.skilleval.yaml`, and `evals/` are for repo
maintenance and are not required at the cluster — the `zip` command above
excludes them from the upload artifact.
