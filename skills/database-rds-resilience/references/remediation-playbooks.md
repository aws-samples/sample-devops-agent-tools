# Remediation Playbooks — RDS/Aurora Resilience

CLI templates for manual execution by an operator. None of these commands are run by the skill itself — it produces recommendations only.

## P1 — Enable Multi-AZ (Deferred — Applies at Next Maintenance Window)

```bash
# Deferred (recommended): queues the change for the next maintenance window,
# avoiding an immediate failover.
aws rds modify-db-instance \
  --db-instance-identifier {{INSTANCE_ID}} --multi-az --region {{REGION}}

⚠️ Do not add --apply-immediately unless you accept the risk it introduces:

    Converting to Multi-AZ triggers an initial synchronization to the new standby, which has a measurable performance impact on the primary during the sync window.
    With --apply-immediately, this change (and any other pending changes) applies now, and can itself trigger a brief failover/outage — do not run with --apply-immediately during business hours without a maintenance window.

Impact: RTO drops from 30-60min to 60-120s once complete. Cost: ~2x instance.
P1 — Add Aurora Reader
aws rds create-db-instance \
  --db-instance-identifier {{CLUSTER_ID}}-reader-1 \
  --db-instance-class {{INSTANCE_CLASS}} \
  --engine aurora-postgresql \
  --db-cluster-identifier {{CLUSTER_ID}} \
  --availability-zone {{DIFFERENT_AZ}} \
  --region {{REGION}}

Impact: Adding a reader does not affect the writer. Enables automatic failover; RTO drops to <30s once the reader is available.
P2 — Encrypt Existing Database (Requires Downtime + Endpoint Change)
# 1. Create snapshot
aws rds create-db-cluster-snapshot \
  --db-cluster-identifier {{CLUSTER_ID}} \
  --db-cluster-snapshot-identifier {{CLUSTER_ID}}-pre-encrypt

# 2. Copy with encryption
aws rds copy-db-cluster-snapshot \
  --source-db-cluster-snapshot-identifier {{CLUSTER_ID}}-pre-encrypt \
  --target-db-cluster-snapshot-identifier {{CLUSTER_ID}}-encrypted \
  --kms-key-id {{KMS_KEY_ARN}}

# 3. Restore encrypted cluster (NEW endpoint)
aws rds restore-db-cluster-from-snapshot \
  --db-cluster-identifier {{CLUSTER_ID}}-encrypted \
  --snapshot-identifier {{CLUSTER_ID}}-encrypted \
  --engine aurora-postgresql \
  --engine-version {{ENGINE_VERSION}}

# 4. Create instance in new cluster
aws rds create-db-instance \
  --db-instance-identifier {{CLUSTER_ID}}-encrypted-writer \
  --db-instance-class {{INSTANCE_CLASS}} \
  --engine aurora-postgresql \
  --db-cluster-identifier {{CLUSTER_ID}}-encrypted

⚠️ Endpoint changes. Application must be updated. Plan a maintenance window.
P3 — Setup Aurora Global Database
# Prerequisite: cluster must be encrypted + on a version that supports Global Database
aws rds create-global-cluster \
  --global-cluster-identifier {{GLOBAL_ID}} \
  --source-db-cluster-identifier {{PRIMARY_CLUSTER_ARN}} \
  --region {{PRIMARY_REGION}}

# Add secondary region
aws rds create-db-cluster \
  --db-cluster-identifier {{SECONDARY_CLUSTER_ID}} \
  --engine aurora-postgresql \
  --engine-version {{VERSION}} \
  --global-cluster-identifier {{GLOBAL_ID}} \
  --region {{SECONDARY_REGION}}

# Add instance to secondary
aws rds create-db-instance \
  --db-instance-identifier {{SECONDARY_CLUSTER_ID}}-reader-1 \
  --db-instance-class {{INSTANCE_CLASS}} \
  --engine aurora-postgresql \
  --db-cluster-identifier {{SECONDARY_CLUSTER_ID}} \
  --region {{SECONDARY_REGION}}

Result: RPO <1s, RTO <1min for regional failure, once fully provisioned.
Report Output Format
# RBUI Resilience Assessment Report
**Account:** {{ACCOUNT_ID}} | **Region:** {{REGION}} | **Date:** {{DATE}}

## Overall Score: {{SCORE}}/100 ({{RATING}})

## Infrastructure Inventory
|
 Resource 
|
 Engine 
|
 Size 
|
 Encrypted 
|
 Multi-AZ 
|
 DR 
|

|
----------
|
--------
|
------
|
-----------
|
----------
|
-----
|


## Blockers Detected
|
 Severity 
|
 Blocker ID 
|
 Resource 
|
 Description 
|
 RTO/RPO Impact 
|

|
----------
|
-----------
|
----------
|
-------------
|
---------------
|


## Realistic RTO/RPO (Current State)
|
 Resource 
|
 Actual RPO 
|
 Actual RTO 
|
 Stated Target 
|
 Gap 
|

|
----------
|
-----------
|
-----------
|
--------------
|
-----
|


## Remediation Plan
### P1 — Immediate (In-Region HA)
### P2 — This Week (Data Protection)
### P3 — 30 Days (Cross-Region DR)

## Cost Impact
| Action | Monthly Cost Change |
|--------|-------------------|

