# Remediation Reference (verbatim dictionary — 24 entries)

Loaded by SKILL.md via `read_skill_resource` before the Prioritized Recommendations
section is written. For every non-passing finding, copy the "Why it matters" line and
"Resolve" steps verbatim (observed values may be instantiated) and include the
"Dive deeper" links EXACTLY as written. Never emit a documentation link not in this file.


#### 1.1 Cluster Status
**Why it matters:** A non-green cluster indicates shards are unassigned, meaning partial data unavailability (red) or reduced redundancy (yellow).
**Resolve:** 1) Run `GET _cluster/allocation/explain` to identify root cause of unassigned shards 2) Address the cause (disk space, node count, AZ imbalance) and verify shards reallocate 3) For red status, restore affected indices from a snapshot if primaries are unrecoverable
**Dive deeper:** [Recommended CloudWatch alarms](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/cloudwatch-alarms.html) · [Why is my cluster in red or yellow status?](https://repost.aws/knowledge-center/opensearch-red-yellow-status)

#### 1.2 Node Count & AZ Balance
**Why it matters:** Unbalanced data node counts across AZs cause uneven shard distribution and reduce fault tolerance during an AZ failure.
**Resolve:** 1) Set `ZoneAwarenessEnabled: true` with `AvailabilityZoneCount: 3` via `UpdateDomainConfig` 2) Adjust `InstanceCount` to a multiple of the AZ count (e.g., 6 or 9 for 3-AZ) 3) Consider Multi-AZ with Standby for 99.99% SLA
**Dive deeper:** [Configuring a multi-AZ domain](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/managedomains-multiaz.html) · [Operational best practices](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp.html)

#### 1.3 Dedicated Master Configuration
**Why it matters:** Without dedicated masters, data nodes handle both queries and cluster management; a single-node overload can destabilize the entire cluster.
**Resolve:** 1) Enable dedicated masters via `UpdateDomainConfig` with `DedicatedMasterEnabled: true`, `DedicatedMasterCount: 3` 2) Choose an instance type appropriate for your shard count (≥8 GiB RAM for clusters with >10K shards) 3) Never use an even master count — always 3 (or 5 for very large clusters)
**Dive deeper:** [Dedicated master nodes](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/managedomains-dedicatedmasternodes.html) · [Configuring a multi-AZ domain](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/managedomains-multiaz.html)

#### 1.4 Engine Version & Upgrade Eligibility
**Why it matters:** Older engine versions miss security patches, performance improvements, and new features; falling behind compounds upgrade risk.
**Resolve:** 1) Call `GetCompatibleVersions` to confirm eligible target versions 2) Take a manual snapshot as a rollback point 3) Initiate in-place upgrade via `UpgradeDomain` to the latest same-major version (e.g., 2.11 → 2.19 in one hop)
**Dive deeper:** [Upgrading OpenSearch Service domains](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/version-migration.html) · [Operational best practices — stability](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp.html)

#### 2.1 EBS Configuration
**Why it matters:** gp2 volumes lack configurable IOPS/throughput and cost more per IOPS than gp3, limiting both performance and cost efficiency.
**Resolve:** 1) Call `UpdateDomainConfig` to change `VolumeType` from `gp2` to `gp3` 2) Set baseline `Iops` (3000 free) and `Throughput` (125 MiB/s free) — increase if workload demands 3) Note: volume type change triggers a blue/green deployment
**Dive deeper:** [Making configuration changes (blue/green)](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/managedomains-configuration-changes.html) · [Sizing domains — storage](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/sizing-domains.html)

#### 2.2 Shard Count & Density
**Why it matters:** Excessive shards per node consume heap and CPU, cause slow recovery, GC thrashing, and can hit the hard 1,000-shards-per-node limit.
**Resolve:** 1) Consolidate small indices using index rollover or reindex into fewer, larger shards (target 10–50 GiB per shard) 2) Reduce replica count on non-critical indices 3) Scale data node count so density stays ≤25 shards per GiB of heap
**Dive deeper:** [Choosing the number of shards](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp-sharding.html) · [Operational best practices — shard strategy](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp.html)

#### 2.3 Active Shards vs. Total Shards
**Why it matters:** Non-active shards (initializing, relocating, or unassigned) mean queries hit fewer replicas and data may be at risk if the gap persists.
**Resolve:** 1) Run `GET _cluster/allocation/explain` to determine why shards are unassigned 2) Address root cause: add disk space, fix allocation filters, or increase node count 3) For stuck initializing shards, check `cluster.routing.allocation.node_concurrent_recoveries` and increase if throttled
**Dive deeper:** [Choosing the number of shards](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp-sharding.html) · [Why is my cluster in red or yellow status?](https://repost.aws/knowledge-center/opensearch-red-yellow-status)

#### 2.4 Free Storage Space
**Why it matters:** When any node's free space drops below 10%, OpenSearch triggers a high watermark that blocks shard allocation and may set indices to read-only.
**Resolve:** 1) Delete unnecessary indices or move cold data to UltraWarm/cold storage 2) Increase `EBSOptions.VolumeSize` via `UpdateDomainConfig` (in-place for gp3 increases) 3) Add data nodes to spread existing data across more disks
**Dive deeper:** [Recommended CloudWatch alarms (FreeStorageSpace)](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/cloudwatch-alarms.html) · [Sizing domains — calculating storage](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/sizing-domains.html)

#### 3.1 JVM Memory Pressure
**Why it matters:** Sustained high JVM heap pressure triggers prolonged garbage collection pauses, circuit breaker trips, and ultimately OOM crashes that take nodes offline.
**Resolve:** 1) Identify memory-heavy queries or aggregations using `_nodes/stats/jvm` and slow logs 2) Reduce shard count per node (target ≤25 shards/GiB heap) or scale to a larger instance type 3) Clear field data cache (`POST /_cache/clear?fielddata=true`) if pressure is acute
**Dive deeper:** [Recommended CloudWatch alarms](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/cloudwatch-alarms.html) · [Troubleshoot high JVM memory pressure on OpenSearch Service](https://repost.aws/knowledge-center/opensearch-high-jvm-memory-pressure)

#### 3.2 CPU Utilization
**Why it matters:** Sustained CPU saturation causes request timeouts, search/write rejections, and cluster unresponsiveness under load.
**Resolve:** 1) Identify expensive queries via `_tasks?actions=*search&detailed` and cancel long-runners 2) Reduce concurrent bulk-indexing pressure or throttle client-side traffic 3) Scale out (add data nodes) or scale up (larger instance type) to increase vCPU headroom
**Dive deeper:** [Operational best practices – Performance](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp.html) · [Troubleshoot high CPU utilization on OpenSearch Service](https://repost.aws/knowledge-center/opensearch-troubleshoot-high-cpu)

#### 3.3 Search Latency
**Why it matters:** Elevated search latency degrades user-facing query response times and may indicate undersized clusters, expensive queries, or cache pressure.
**Resolve:** 1) Enable search slow logs and identify queries exceeding acceptable thresholds 2) Optimize query scope—add filters, reduce date ranges, avoid unbounded `match_all` or deep pagination 3) Ensure CPUUtilization and JVMMemoryPressure are below 80%; scale resources if needed
**Dive deeper:** [Operational best practices – Performance](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp.html) · [Troubleshoot search latency spikes in OpenSearch Service](https://repost.aws/knowledge-center/opensearch-latency-spikes)

#### 3.4 Indexing Latency
**Why it matters:** High indexing latency signals merge pressure, refresh overhead, or resource contention that slows data ingestion throughput.
**Resolve:** 1) Increase `refresh_interval` to 30s+ to reduce segment creation frequency 2) Reduce bulk request concurrency and ensure bulk payloads are 3–5 MiB 3) Check for Lucene merge thread saturation (`_nodes/hot_threads`) and scale up if disk I/O is the bottleneck
**Dive deeper:** [Operational best practices – Performance](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp.html) · [CloudWatch metrics reference](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/managedomains-cloudwatchmetrics.html)

#### 3.5 Search & Indexing Rate
**Why it matters:** Understanding workload volume is essential for capacity planning—unexpected rate spikes correlate with latency increases and thread pool rejections.
**Resolve:** 1) Monitor SearchRate and IndexingRate in CloudWatch to baseline normal traffic patterns 2) Set CloudWatch alarms at 2× baseline to detect anomalous spikes early 3) Implement client-side backoff and traffic shaping during peak periods
**Dive deeper:** [CloudWatch metrics reference](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/managedomains-cloudwatchmetrics.html) · [Recommended CloudWatch alarms](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/cloudwatch-alarms.html)

#### 3.6 HTTP Error Rate (4xx/5xx)
**Why it matters:** 5xx errors indicate cluster-side failures (overload, circuit breakers, shard failures) causing data unavailability; 4xx errors signal client misconfigurations or auth issues.
**Resolve:** 1) For 5xx: check JVMMemoryPressure and CPUUtilization—reduce traffic or scale cluster 2) For 5xx: investigate ThreadpoolSearchRejected/ThreadpoolWriteRejected for queue saturation 3) For 4xx: review access policies, query syntax, and authentication configuration
**Dive deeper:** [Troubleshooting OpenSearch Service](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/handling-errors.html) · [Resolve HTTP 503 errors in OpenSearch Service](https://repost.aws/knowledge-center/opensearch-http-503-errors)

#### 3.7 Cluster Status History
**Why it matters:** Red status means primary shards are unassigned with active data loss risk; yellow status means reduced fault tolerance from missing replicas.
**Resolve:** 1) Run `GET _cluster/allocation/explain` to identify why shards cannot be placed 2) For red: check for failed nodes, storage exhaustion, or exceeded shard limits and resolve the root cause 3) For yellow: ensure node count accommodates replica placement across AZs and disk watermarks are not breached
**Dive deeper:** [Troubleshooting – Red/Yellow cluster status](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/handling-errors.html) · [Recommended CloudWatch alarms](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/cloudwatch-alarms.html)

#### 4.1 Encryption at Rest
**Why it matters:** Data on disk (indexes, logs, swap files, automated snapshots) is stored unencrypted, violating most compliance frameworks.
**Resolve:** 1) In the AWS Console, choose the domain → Actions → Edit security configuration → enable "Encryption of data at rest" 2) Select an AWS KMS key (AWS-owned or customer-managed) 3) Note: enabling on existing domains requires OpenSearch or Elasticsearch 6.7+; once enabled it cannot be disabled — reversal requires snapshot/restore to a new domain.
**Dive deeper:** [Encryption of data at rest](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/encryption-at-rest.html) · [Fine-grained access control (prerequisite context)](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/fgac.html)

#### 4.2 Node-to-Node Encryption
**Why it matters:** Inter-node traffic within the domain's internal VPC is unencrypted, exposing data in transit between cluster nodes.
**Resolve:** 1) Console → domain → Actions → Edit security configuration → enable "Node-to-node encryption" 2) Enabling on existing domains requires OpenSearch or Elasticsearch 6.7+ 3) Once enabled, it cannot be disabled — reversal requires snapshot/restore to a new domain.
**Dive deeper:** [Node-to-node encryption](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/ntn.html)

#### 4.3 HTTPS Enforcement
**Why it matters:** Without HTTPS enforcement, clients can connect over plaintext HTTP, exposing queries and data in transit to interception.
**Resolve:** 1) Console → domain → Actions → Edit security configuration → set "Require HTTPS" to enabled 2) Set TLS security policy to `Policy-Min-TLS-1-2-2019-07` or newer 3) This is a configuration change on the existing domain — no snapshot/restore needed.
**Dive deeper:** [Creating and managing domains (DomainEndpointOptions)](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/createupdatedomains.html) · [Data protection overview](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/data-protection.html)

#### 4.4 Network Exposure (VPC)
**Why it matters:** Public-endpoint domains are reachable from any internet-connected client; VPC deployment adds network-layer isolation via security groups and private subnets.
**Resolve:** 1) Create a new domain with VPC access (existing public domains cannot be switched to VPC in-place) 2) Configure security groups to restrict inbound access to authorized CIDR ranges or linked services 3) Migrate data via snapshot/restore from the public domain to the new VPC domain.
**Dive deeper:** [Launching domains within a VPC](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/vpc.html)

#### 4.5 Access Policy
**Why it matters:** A `Principal: "*"` policy with no IP/IAM condition allows unauthenticated access from any source, enabling data exfiltration or modification.
**Resolve:** 1) Replace the open policy with specific IAM principals (ARNs) in the resource-based access policy 2) If VPC-deployed, an open policy may be acceptable when combined with security groups — evaluate your threat model 3) For public domains, add IP-condition blocks or switch to fine-grained access control.
**Dive deeper:** [Identity and Access Management in OpenSearch Service](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/ac.html)

#### 4.6 Fine-Grained Access Control (FGAC)
**Why it matters:** Without FGAC, there are no index-level, document-level, or field-level permissions — all authenticated users get full cluster access.
**Resolve:** 1) Console → domain → Actions → Edit security configuration → enable "Fine-grained access control" 2) Choose a master user (IAM ARN or internal user database) 3) Map roles to users/backend roles in OpenSearch Dashboards Security plugin. Note: requires HTTPS enforcement and node-to-node encryption as prerequisites.
**Dive deeper:** [Fine-grained access control](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/fgac.html)

#### 5.1 Instance Right-Sizing
**Why it matters:** Over-provisioned instances waste spend when sustained CPU and JVM utilization are both low — the cluster has more capacity than the workload requires.
**Resolve:** 1) Review CloudWatch CPUUtilization (avg) and JVMMemoryPressure (max) over 7–14 days 2) If both are consistently low (CPU <20% avg AND JVM <50% max), test a smaller instance type (e.g., r6g.xlarge → r6g.large) in a blue/green configuration 3) Validate latency/throughput remain acceptable before decommissioning the larger nodes.
**Dive deeper:** [Sizing domains](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/sizing-domains.html) · [Choosing instance types and testing](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp-instances.html)

#### 5.2 Storage Tiering (UltraWarm / Cold)
**Why it matters:** Hot storage costs ~3–10× more per GiB than UltraWarm; infrequently accessed indices (e.g., logs >30 days old) sitting on hot nodes represent avoidable spend.
**Resolve:** 1) Enable UltraWarm on the domain (requires dedicated master nodes) 2) Migrate read-only indices older than your retention threshold using the `_ultrawarm/migration` API or ISM policies 3) For rarely queried archival data, enable Cold storage and migrate from warm to cold.
**Dive deeper:** [UltraWarm storage](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/ultrawarm.html)

#### 5.3 Reserved Instance Coverage
**Why it matters:** On-demand pricing for steady-state nodes is 30–50% more expensive than equivalent Reserved Instances over a 1- or 3-year term.
**Resolve:** 1) In the OpenSearch Service console → Reserved Instances → compare current on-demand instance types against available RI offerings 2) Purchase RIs matching your data node instance type and count (All Upfront gives deepest discount) 3) Use AWS Cost Explorer to validate RI utilization after purchase.
**Dive deeper:** [Reserved Instances in OpenSearch Service](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/ri.html)
