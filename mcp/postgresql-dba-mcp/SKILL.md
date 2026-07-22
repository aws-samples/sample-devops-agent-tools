---
name: postgresql-dba-mcp
description: >
  Comprehensive PostgreSQL DBA diagnostic skill for Amazon RDS and Aurora
  PostgreSQL instances. Provides data-plane access via predefined read-only
  queries covering health checks, vacuum/bloat analysis, parameter tuning,
  index optimization, pre-upgrade validation, SQL plan analysis, replication
  health, connection management, and lock troubleshooting. Uses a query-allowlist
  approach with 39 vetted diagnostic queries. Use this skill when a customer
  reports database slowness, bloat, connection issues, lock contention,
  replication lag, or is planning a major version upgrade.
metadata:
  version: "1.0"
  author: siviv
  aws-devops-agent-skills.technical-domains: rds,aurora,postgresql,database,performance,vacuum,indexes,replication
---

# PostgreSQL DBA MCP Skill

Comprehensive diagnostic skill for Amazon RDS and Aurora PostgreSQL. This skill
provides decision trees, thresholds, and safety rules for interpreting results
from the postgresql-dba-mcp server tools.

## Critical Safety Rules (Apply to ALL recommendations)

1. **NEVER recommend VACUUM FULL** - it takes an ACCESS EXCLUSIVE lock, blocks
   all reads and writes, causes extended downtime. Always recommend pg_repack
   for bloat reclamation instead.
2. **ALWAYS use CREATE INDEX CONCURRENTLY** - never suggest CREATE INDEX without
   CONCURRENTLY in production.
3. **NEVER kill autovacuum workers** - they are doing necessary cleanup work.
4. **For connection issues**, recommend connection pooling (PgBouncer or RDS Proxy)
   rather than increasing max_connections.
5. **For lock contention**, use pg_cancel_backend() before pg_terminate_backend().
6. **NEVER suggest production changes without a maintenance window**.
7. **For upgrades**, ALWAYS recommend testing on a snapshot clone first.
8. **Aurora vs RDS differences**:
   - shared_buffers: RDS = 25% of RAM. Aurora = 75% of RAM (default, leave it).
   - Checkpoint parameters (checkpoint_timeout, max_wal_size): RDS = tune these.
     Aurora = IGNORED (Aurora streams logs to storage continuously).
   - effective_cache_size: RDS = 50-75% of RAM. Aurora = same as shared_buffers.
   - random_page_cost = 1.1 for all RDS/Aurora (SSD/NVMe storage).

---

## Feature 1: Health Check

### When to Activate
- Customer asks about database health or configuration review
- General performance degradation reported
- Proactive assessment before peak traffic

### Investigation Steps
1. Call `run_full_health_check` for quick triage (version, connections, cache hit, XID age)
2. Call `get_instance_config` for infrastructure assessment
3. Call `get_instance_metrics` for CloudWatch data (CPU, memory, IOPS, latency)
4. If issues found, drill into specific categories with `execute_health_query`

### Thresholds
| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| Cache hit ratio | > 99% | 95-99% | < 95% |
| CPU utilization | < 60% | 60-80% | > 80% |
| Free storage | > 30% | 10-30% | < 10% |
| XID wraparound % | < 50% | 50-75% | > 75% |
| Dead tuple ratio | < 10% | 10-30% | > 30% |
| Connections used | < 60% of max | 60-80% | > 80% |

---

## Feature 2: Vacuum and Bloat Analysis

### When to Activate
- Dead tuple ratio exceeds 20%
- Customer reports table bloat or storage growth
- Autovacuum not keeping up
- XID wraparound age approaching limits
- Queries slowing due to bloated tables

### Investigation Steps
1. Call `execute_health_query(category="7", query_id="7.1")` - tables needing vacuum
2. Call `execute_health_query(category="7", query_id="7.3")` - XID wraparound risk
3. Call `execute_health_query(category="5", query_id="5.2")` - table bloat estimate
4. Call `execute_health_query(category="7", query_id="7.2")` - tables never vacuumed

### Decision Tree

**If dead_tuple_pct > 50% AND last_autovacuum IS NULL:**
1. Check for blocking transactions (idle in transaction sessions)
2. Recommend: `VACUUM ANALYZE schema.tablename;` (safe, non-blocking for reads)
3. If disk reclamation needed: recommend pg_repack (NEVER VACUUM FULL)
4. Set per-table autovacuum: `ALTER TABLE SET (autovacuum_vacuum_scale_factor = 0.01)`

**If XID age > 50% of autovacuum_freeze_max_age:**
- URGENT: autovacuum must run, check for blocking transactions
- If blocked: recommend `idle_in_transaction_session_timeout = 300000`

### Key Parameters
| Parameter | Recommended | Why |
|-----------|-------------|-----|
| autovacuum_vacuum_cost_delay | 2 ms | Default 20ms too slow for SSDs |
| autovacuum_vacuum_scale_factor | 0.01 | Default 0.2 too high for large tables |
| autovacuum_max_workers | 5 | Default 3 insufficient for busy instances |
| vacuum_cost_limit | 400 | Default 200 conservative for SSDs |

---

## Feature 3: Parameter Tuning

### When to Activate
- Customer reports slow queries, high CPU, or memory pressure
- After a health check reveals configuration issues
- Customer using default parameter group
- Customer migrated from on-premises

### Investigation Steps
1. Call `get_instance_config` - get instance class and RAM
2. Call `get_parameter_group` - get current parameter values
3. Call `execute_health_query(category="2", query_id="2.1")` - live pg_settings values
4. Call `get_instance_metrics` - infer workload profile from CloudWatch

### Aurora vs RDS Parameter Rules
| Parameter | RDS Recommendation | Aurora Recommendation |
|-----------|-------------------|----------------------|
| shared_buffers | 25% of RAM | Leave at default (75% of RAM) |
| effective_cache_size | 75% of RAM | Same as shared_buffers |
| checkpoint_timeout | 15 min | IGNORED - do not tune |
| max_wal_size | 4 GB | IGNORED - do not tune |
| random_page_cost | 1.1 | 1.1 |
| effective_io_concurrency | 200 | 200 |
| work_mem | Scale with RAM / connections | Same |
| jit | off for OLTP | off (always for Aurora) |

### Key Rule
- work_mem x max_connections must fit in RAM
- If product exceeds 50% of RAM, warn about OOM risk
- Flag default parameter groups as CRITICAL (zero tuning)

---

## Feature 4: Index Optimization

### When to Activate
- Sequential scans on large tables
- High read latency or IOPS
- Storage growing from redundant indexes
- Foreign key columns causing slow cascading deletes

### Investigation Steps
1. Call `execute_health_query(category="8", query_id="8.1")` - unused indexes
2. Call `execute_health_query(category="8", query_id="8.2")` - duplicate indexes
3. Call `execute_health_query(category="8", query_id="8.3")` - scan ratio analysis
4. Call `execute_health_query(category="11", query_id="11.1")` - tables without PK

### Decision Tree
- idx_scan = 0 AND not a unique/PK constraint: candidate for dropping
- Recommend monitoring for 2 weeks before dropping (monthly batch jobs may use it)
- For new indexes: ALWAYS `CREATE INDEX CONCURRENTLY`
- For duplicate indexes: keep the more selective one, drop the redundant one

---

## Feature 5: Pre-Upgrade Check

### When to Activate
- Customer planning major version upgrade
- Evaluating upgrade path
- Checking extension compatibility

### Investigation Steps
1. Call `check_upgrade_readiness` - control-plane checks (7 checks)
2. Call `execute_health_query(category="10", query_id="10.1")` - prepared transactions
3. Call `execute_health_query(category="10", query_id="10.2")` - reg* data types
4. Call `execute_health_query(category="10", query_id="10.3")` - logical replication slots
5. Call `execute_health_query(category="10", query_id="10.4")` - unknown data types
6. Call `execute_health_query(category="10", query_id="10.5")` - sql_identifier
7. Call `execute_health_query(category="10", query_id="10.6")` - extensions
8. Call `execute_health_query(category="10", query_id="10.7")` - views on system catalogs

### Hard Blockers (Upgrade WILL fail)
- Open prepared transactions
- Unsupported reg* data types
- Logical replication slots (must be dropped)
- Unknown data types
- sql_identifier columns
- Primary user name starting with 'pg_'

### Recommendations
- ALWAYS test on a snapshot clone first
- Take manual snapshot before upgrade
- For Aurora: consider Blue/Green deployment for zero downtime
- After upgrade: run ANALYZE on all databases, update extensions

---

## Feature 6: SQL Plan Analysis

### When to Activate
- Specific slow queries reported
- Query plan regression after changes
- High CPU with identifiable slow queries

### Investigation Steps
1. Call `execute_health_query(category="6", query_id="6.1")` - top queries by total time
2. Call `execute_health_query(category="6", query_id="6.2")` - top queries by mean time
3. Call `explain_query` with the problematic query - get execution plan
4. Call `execute_health_query(category="6", query_id="6.4")` - index hit ratio

### Plan Red Flags
- Sequential Scan on tables with > 10K rows: likely missing index
- Sort with "external merge": work_mem too low, spilling to disk
- Nested Loop on large tables: missing join index
- Bitmap Heap Scan with lossy blocks: index not selective enough

### Remediation
- Missing index: `CREATE INDEX CONCURRENTLY`
- Sort spill: `SET LOCAL work_mem = '128MB';` per session (not globally)
- Bad cost estimates: check random_page_cost, run ANALYZE
- NEVER suggest EXPLAIN ANALYZE (executes the query). Use EXPLAIN only.

---

## Feature 7: Replication Health

### When to Activate
- Read replica falling behind
- WAL files accumulating
- Queries on replica being cancelled
- Logical replication slot not advancing

### Investigation Steps
1. Call `execute_health_query(category="4", query_id="4.1")` - replication status
2. Call `execute_health_query(category="4", query_id="4.2")` - replication slots
3. Call `get_instance_metrics` - check ReplicaLag CloudWatch metric

### Decision Tree
- Orphaned slot (active=false, retained_bytes growing): confirm subscriber is gone, then `SELECT pg_drop_replication_slot('slot_name');`
- Replica lag: check replica size, long-running queries on replica, max_standby_streaming_delay
- Aurora: replica lag > 100ms is unusual, check writer CPU

### Key Rule
- NEVER drop an active replication slot (causes data loss for subscriber)
- Aurora replicas use shared storage, not WAL shipping (different behavior)

---

## Feature 8: Connection Management and Lock Troubleshooting

### When to Activate
- "Too many connections" errors
- Queries waiting or timing out
- Throughput drops during peak hours
- Deadlock errors in logs

### Investigation Steps
1. Call `execute_health_query(category="3", query_id="3.1")` - connection summary
2. Call `execute_health_query(category="3", query_id="3.4")` - connections by user/db
3. Call `execute_health_query(category="3", query_id="3.2")` - long-running queries
4. Call `execute_health_query(category="3", query_id="3.3")` - lock waits

### Connection Exhaustion
- Most connections idle: application not closing connections, recommend pooling
- Recommend RDS Proxy or PgBouncer (not increasing max_connections)
- Each idle connection uses ~5-10 MB RAM

### Lock Contention
- Identify root blocker from lock waits query
- If idle in transaction: likely abandoned, safe to terminate
- Use pg_cancel_backend() first, pg_terminate_backend() only if cancel fails
- Recommend: idle_in_transaction_session_timeout = 300000 (5 min)
- Recommend: lock_timeout = 5000 (5 sec), statement_timeout = 300000 (5 min)

### Safe DDL Practices
- CREATE INDEX: always CONCURRENTLY
- ALTER TABLE ADD COLUMN: safe in PG 11+ (no rewrite if has DEFAULT)
- ALTER TABLE ALTER TYPE: dangerous, causes rewrite. Use add-column migration strategy.
