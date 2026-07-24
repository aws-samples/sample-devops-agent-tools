# AWS DMS Version Reference

## Version Lifecycle Matrix

| Version | Release Date | No New Instances After | Auto-Upgrade (EOL) | Status |
|---------|-------------|----------------------|---------------------|--------|
| **3.6.1** | May 15, 2025 | TBD | TBD | ✅ Current (latest) |
| **3.6.0** | Dec 27, 2024 | Apr 27, 2026 | July 27, 2026 | ❌ EOL |
| **3.5.4** | Nov 15, 2024 | Jan 31, 2027 | Mar 31, 2027 | ✅ Active (default for new instances) |
| **3.5.3** | May 17, 2024 | Feb 28, 2026 | June 30, 2026 | ❌ EOL |
| **3.5.2** | Oct 29, 2023 | Mar 30, 2025 | Apr 29, 2025 | ❌ EOL |
| **3.5.1** | Jun 30, 2023 | Nov 30, 2024 | Mar 15, 2025 | ❌ EOL |
| **3.4.7** | May 31, 2022 | Sep 30, 2024 | Oct 31, 2024 | ❌ EOL |
| **3.4.6** | Nov 30, 2021 | Sep 30, 2024 | Oct 31, 2024 | ❌ EOL |

**Notes:**
- Default engine version for new instances: **3.5.4** (3.6.1 will become the new default — TBD)
- 3.6.1 EOL dates have not yet been scheduled (shown as TBD)
- To provide a migration window to 3.6.1, AWS extended 3.5.4 support timeline (no-new-instance: Jan 31, 2027; EOL: Mar 31, 2027)
- Upgrading from 3.4.x to 3.5.x requires `AllowMajorVersionUpgrade = true` in the API/CLI
- DMS does NOT differentiate major/minor versions for automatic upgrades — it will auto-upgrade deprecated versions during the maintenance window

---

## Version-Specific Feature Availability

Use this table to determine which features are available based on the customer's DMS engine version:

| Feature | Minimum Version | Notes |
|---------|:-:|-------|
| Validation-only tasks | **3.4.6** | Full load validation-only with single-pass comparison |
| Inline LOB mode (`InlineLobMaxSize`) | **3.4.7** | Combines advantages of Full and Limited LOB modes |
| S3 Parquet as source | **3.5.3** | Read Parquet files from S3 source endpoints |
| PostgreSQL 16.x support | **3.5.3** | Source and target |
| Babelfish source support | **3.5.3** | Enhanced PostgreSQL source for Babelfish datatypes |
| Enhanced throughput (Oracle → Redshift) | **3.5.3** | DMS Serverless only |
| Data masking (column-level) | **3.5.4** | Digit randomization, masking, or hashing |
| Enhanced data validation performance | **3.5.4** | Faster processing for large datasets |
| PostgreSQL 17 support | **3.5.4** | Source and target |
| `disableUnicodeSourceFilter` ECA | **3.5.4** | Fixes PostgreSQL filtering performance degradation |
| Transformation metadata variables (`$AR_M_MODIFIED_SCHEMA`) | **3.6.0** | Dynamic transformation rules |
| LOB replication in UPSERT error-handling mode | **3.6.0** | LOB columns work with "insert missing target record" option |
| DMS Data Resync | **3.6.1** | Auto-fixes data inconsistencies (Oracle/SQL Server → PostgreSQL) |
| IAM database authentication (MariaDB, MySQL, PostgreSQL) | **3.6.1** | Connect via IAM instead of stored passwords |
| PostgreSQL Read Replica CDC | **3.6.1** | CDC from read replicas (PostgreSQL 16.x+ only) |
| SQL Server ODBC 18 driver | **3.6.1** | Updated from ODBC 17 |
| SQL Server Binary(16) to PostgreSQL UUID | **3.6.1** | Seamless conversion of binary GUID data to native UUID types |
| Enhanced logging for missing target columns | **3.6.1** | Column discrepancy notifications at WARNING level (was VERBOSE) |

---

## Critical Bug Fixes by Version

When troubleshooting, check if the customer's version has these known issues:

### Issues Fixed in 3.6.1 (upgrade if affected)

| Issue | Affected Versions | Symptoms |
|-------|:-:|---------|
| Oracle LOB memory leak (full LOB mode) | < 3.6.1 | Continuous memory growth → OOM during LOB replication |
| Data validation memory leak | < 3.6.1 | OOM failures during long-running validation tasks |
| PostgreSQL unbound numeric target failure | < 3.6.1 | Task fails migrating tables with large numeric values to PostgreSQL |
| Redshift LOB corruption with parallel apply | < 3.6.1 | Data corruption in LOB columns when using parallel apply threads to Redshift |
| SQL Server AlwaysOn case sensitivity | < 3.6.1 | Fails to detect primary replica due to case sensitivity in AG names |
| Source table drop/recreate resume failure | < 3.6.1 | Task can't resume after source table is dropped and recreated during task stop |
| DO_NOTHING mode silent table creation | < 3.6.1 | DMS creates target tables without warning in DO_NOTHING mode |
| Selection rules filtering with data masking | < 3.6.1 | Table selection rule filters fail when data masking transformations are applied |
| Transformation rules metadata expressions | < 3.6.1 | Expressions in transformation rules incorrectly applied |

### Issues Fixed in 3.5.4 (upgrade if affected)

| Issue | Affected Versions | Symptoms |
|-------|:-:|---------|
| PostgreSQL test_decoding event loss | < 3.5.4 | Certain CDC events not replicated when using test_decoding plugin |
| Oracle Binary Reader crash after July 2024 PSU | < 3.5.4 | Task crash after applying Oracle July 2024 Patch Set Update |
| MySQL Secrets Manager credential corruption | < 3.5.4 | Credentials become corrupted when using Secrets Manager with MySQL |
| PostgreSQL Multi-AZ failover fatal failure | < 3.5.4 | DMS task fatally fails instead of recovering after PostgreSQL MAZ failover |
| `TaskRecoveryTableEnabled` stop failure | < 3.5.4 | Task fails upon stop when `TaskRecoveryTableEnabled = true` |
| `TaskRecoveryTableEnabled` data duplication | < 3.5.4 | Transactions replicated twice when recovery table enabled |
| LOB column order mismatch | < 3.5.4 | LOB data replicated incorrectly when column order differs source vs target |
| SQL Server 2022 CU12 MS-Replication setup | < 3.5.4 | Can't auto-configure MS-Replication on SQL Server 2022 CU12+ |
| PostgreSQL MapBooleanAsBoolean with pglogical | < 3.5.4 | Boolean not migrated correctly with pglogical + MapBooleanAsBoolean=true |

### Issues Fixed in 3.6.0 (upgrade if affected)

| Issue | Affected Versions | Symptoms |
|-------|:-:|---------|
| PostgreSQL WAL slot continuous growth | < 3.6.0 | WAL slot grows unbounded → disk full, degraded performance |
| SQL Server incorrect high latency reporting | < 3.6.0 | CDCLatencySource shows very high values incorrectly |
| SQL Server non-standard UPDATE task termination | < 3.6.0 | Task terminates without error on non-standard UPDATEs |
| Batch Apply LOB lookup failure (DELETE+INSERT→UPDATE) | < 3.6.0 | LOB lookup fails when batch apply combines DELETE+INSERT into UPDATE |
| Many transformations crash on startup | < 3.6.0 | Task crashes during startup with numerous transformation rules |
| MySQL DDL capture failure (special format) | < 3.6.0 | Fails to capture certain DDL changes during CDC |
| S3 cross-account bucket access validation | < 3.6.0 | Inadequate S3 bucket ownership validation when using S3 as target/source |

---

## Upgrade Decision Guide

### When to Upgrade

| Current Version | Recommendation | Urgency |
|-----------------|---------------|---------|
| 3.4.x | **Must upgrade immediately** — EOL passed Oct 2024. DMS will force-upgrade. | ❌ Critical |
| 3.5.1 | **Must upgrade** — EOL passed Mar 2025 | ❌ Critical |
| 3.5.2 | **Must upgrade** — EOL passed Apr 2025 | ❌ Critical |
| 3.5.3 | **Must upgrade** — EOL passed June 2026 | ❌ Critical |
| 3.5.4 | Current default; stable. Support extended to Mar 2027. Upgrade to 3.6.1 for new features. | ℹ️ Optional |
| 3.6.0 | **Must upgrade to 3.6.1** — EOL passed July 27, 2026 | ❌ Critical |
| 3.6.1 | Latest — no action needed | ✅ Current |

### Upgrade Procedure

```bash
# Check current version
aws dms describe-replication-instances \
  --query "ReplicationInstances[*].[ReplicationInstanceIdentifier,EngineVersion]" \
  --profile <profile> --region <region>

# List available engine versions with lifecycle metadata (DMS 3.5.3+)
aws dms describe-engine-versions \
  --profile <profile> --region <region>

# Upgrade within same major version (3.5.x → 3.5.4, or 3.6.0 → 3.6.1)
aws dms modify-replication-instance \
  --replication-instance-arn <arn> \
  --engine-version <target-version> \
  --apply-immediately \
  --profile <profile> --region <region>

# Upgrade across major versions (3.4.x → 3.5.x or 3.5.x → 3.6.x)
aws dms modify-replication-instance \
  --replication-instance-arn <arn> \
  --engine-version <target-version> \
  --allow-major-version-upgrade \
  --apply-immediately \
  --profile <profile> --region <region>
```

**Important:**
- Upgrading briefly interrupts running tasks (Multi-AZ minimizes downtime)
- Always test upgrades in non-production first
- After upgrade, tasks resume from their last checkpoint
- If tasks fail to resume after upgrade (fixed in 3.5.4+), restart with `resume-processing`

---

## Source/Target Engine Compatibility Notes

### PostgreSQL Version Support

| PostgreSQL Version | DMS Support | CDC Support | Notes |
|-------------------|:-:|:-:|-------|
| 10.x - 15.x | ✅ | ✅ | Fully supported |
| 16.x | ✅ (3.5.3+) | ✅ | Requires DMS 3.5.3 or later |
| 17.x | ✅ (3.5.4+) | ✅ | Requires DMS 3.5.4 or later |
| Aurora PG 2.2+ (PG 10.6+) | ✅ | ✅ | Full CDC via logical replication |
| Aurora PG Read Replica CDC | ✅ (3.6.1+) | ✅ | Requires PG 16.x+, DMS 3.6.1+ |

### Oracle Known Issues by Version

| Oracle Scenario | DMS Version Required | Issue |
|-----------------|:-:|-------|
| Oracle with July 2024 PSU + Binary Reader | 3.5.4+ | Crash on < 3.5.4 |
| Oracle LOBs with Full LOB mode (long-running) | 3.6.1+ | Memory leak on < 3.6.1 |
| Oracle CLOB/CHAR with non-ASCII characters | 3.6.0+ | Incorrect replication on < 3.6.0 |

### SQL Server Known Issues by Version

| SQL Server Scenario | DMS Version Required | Issue |
|--------------------|:-:|-------|
| SQL Server 2022 CU12+ (MS-Replication auto-setup) | 3.5.4+ | Fails to auto-configure on < 3.5.4 |
| SQL Server AlwaysOn (case-sensitive AG names) | 3.6.1+ | Primary detection failure on < 3.6.1 |
| SQL Server ODBC 18 support | 3.6.1+ | Uses ODBC 17 on < 3.6.1 |
