# Troubleshooting Runbooks — Aurora MySQL & RDS MySQL

Decision-tree based troubleshooting for the most common database issues. Each runbook follows the pattern: Symptom → Data Collection → Root Cause Identification → Remediation.

---

## Runbook 1: High CPU Utilization

### Symptoms
- CloudWatch `CPUUtilization` > 80% sustained
- Application response times degraded
- `Threads_running` > 20

### Data Collection Steps

```
1. CloudWatch: Get CPUUtilization trend (1h, 5-min granularity)
   aws cloudwatch get-metric-data ... CPUUtilization

2. CloudWatch Logs: Check slow query log for correlating queries
   filter @message like /Query_time/ | sort @timestamp desc | limit 20

3. Database (Query 6.2): Identify CPU-intensive queries
   → Queries with high SUM_SORT_ROWS, SUM_CREATED_TMP_DISK_TABLES, SUM_NO_INDEX_USED

4. Database (Query 3.1): Check Threads_running count
   → If > 50: severe CPU saturation

5. Database (Query 6.4): Check index usage statistics
   → Tables with full_scan_pct > 30%: missing indexes
```

### Root Cause Decision Tree

```
High CPU
├── Threads_running > 50?
│   ├── YES → Check Query 3.2 for "Sending data" or "Creating sort index" states
│   │         → Missing indexes or full table scans
│   └── NO  → Single expensive query consuming all CPU
│             → Check Query 6.1 for top query by total time
├── tmp_disk_tables > 0?
│   ├── YES → Queries spilling temp tables to disk
│   │         → Increase tmp_table_size or restructure query
│   └── NO  → Sort operations or joins without indexes
├── no_index_used > 0?
│   ├── YES → Missing indexes causing full table scans
│   │         → Add composite index on WHERE/JOIN columns
│   └── NO  → Complex aggregations or functions on indexed columns
└── Recent version upgrade or parameter change?
    └── YES → Compare Performance Insights before/after change
```

### Remediations (Priority Order)

1. 🔴 **Immediate:** Add missing indexes on tables with high full_scan_pct
2. 🔴 **Immediate:** Kill long-running queries if lock_wait_timeout exceeded
3. 🟡 **Short-term:** Optimize top CPU-consuming queries (rewrite, add indexes)
4. 🟡 **Short-term:** Move read queries to reader endpoint/replica
5. 🟢 **Planned:** Scale up instance class if legitimate workload growth

---

## Runbook 2: Connection Exhaustion ("Too many connections")

### Symptoms
- Application error: "Too many connections"
- CloudWatch `DatabaseConnections` approaching `max_connections`
- New connections refused

### Data Collection Steps

```
1. CloudWatch: Get DatabaseConnections trend (6h)
2. Database (Query 3.1): Current utilization_pct
3. Database (Query 3.2): Thread details — identify top connection sources
4. CloudWatch Logs: Search error log for "Too many connections"
   filter @message like /Too many connections/ | stats count(*) by bin(5m)
5. Check: Are connections mostly Sleep (idle) or active?
```

### Root Cause Decision Tree

```
Too Many Connections
├── Most connections in "Sleep" state?
│   ├── YES → Connection leak in application
│   │         → Applications not closing connections properly
│   │         → wait_timeout too high (holding idle connections)
│   └── NO  → Legitimate traffic spike
├── Connections from single source IP/user?
│   ├── YES → Single application opening too many connections
│   │         → Connection pool misconfigured (pool too large)
│   └── NO  → Multiple applications competing for connections
├── max_connections at default value?
│   ├── YES → Never tuned for workload
│   │         → Increase via Parameter Group (but investigate root cause)
│   └── NO  → Workload outgrew configuration
└── Recent deployment or traffic event?
    └── YES → Correlate with deployment timeline
```

### Remediations (Priority Order)

1. 🔴 **Immediate:** Implement **RDS Proxy** for connection pooling (reduces connections by 80%)
2. 🔴 **Immediate:** Kill idle Sleep connections older than 1 hour
3. 🟡 **Short-term:** Set `wait_timeout = 300` to auto-close idle connections
4. 🟡 **Short-term:** Fix connection leaks in application (ensure close/return to pool)
5. 🟢 **Planned:** Right-size connection pools per application service

---

## Runbook 3: Replication Lag (Aurora MySQL)

### Symptoms
- CloudWatch `AuroraReplicaLag` > 100ms (unusual for Aurora)
- Read-after-write inconsistency reported by application
- Reader endpoint returning stale data

### Data Collection Steps

```
1. CloudWatch: AuroraReplicaLag trend (1h, 1-min granularity)
2. Database (Query 4.2): Per-replica lag from mysql.ro_replica_status
3. CloudWatch: Reader instance CPUUtilization
4. Database (Query 3.3): Long-running transactions on writer
5. CloudWatch: VolumeWriteIOPs on writer (bulk DML detection)
```

### Root Cause Decision Tree

```
Aurora Replica Lag > 100ms
├── Reader CPU > 80%?
│   ├── YES → Reader overloaded with queries
│   │         → Scale reader instance class or add more readers
│   └── NO  → Writer-side issue
├── Long transaction on writer (> 300s)?
│   ├── YES → Transaction holding undo logs, blocking redo apply
│   │         → Investigate transaction, consider kill if safe
│   └── NO  → Check VolumeWriteIOPs
├── VolumeWriteIOPs spike (3x+ baseline)?
│   ├── YES → Bulk DML operation (large INSERT/UPDATE/DELETE)
│   │         → Batch operations in smaller transactions
│   └── NO  → Potential storage subsystem issue
└── All replicas lagging equally?
    ├── YES → Writer-side root cause (transaction, DML, storage)
    └── NO  → Specific reader overloaded or undersized
```

### Remediations

1. 🔴 **Immediate:** Scale up lagging reader instance class
2. 🟡 **Short-term:** Break bulk DML into smaller batches (1000-5000 rows)
3. 🟡 **Short-term:** Move heavy read queries off saturated reader
4. 🟢 **Planned:** Configure Auto Scaling for read replicas

---

## Runbook 4: Lock Contention & Deadlocks

### Symptoms
- Application errors: "Lock wait timeout exceeded"
- CloudWatch Logs: "Deadlock found" patterns
- Query 3.4 shows active lock waits > 10 seconds
- Application timeout errors increasing

### Data Collection Steps

```
1. Database (Query 3.4): Active lock waits — blocking vs waiting
2. Database (Query 3.3): Long-running transactions holding locks
3. CloudWatch Logs: Search for deadlock and lock timeout patterns
   filter @message like /Deadlock found|Lock wait timeout/
4. Database (Query 3.2): Thread states showing "Waiting for lock"
5. SHOW ENGINE INNODB STATUS: Full deadlock information
```

### Root Cause Decision Tree

```
Lock Contention
├── Single blocking transaction?
│   ├── YES → Long-running transaction holding row/gap locks
│   │         → Check if transaction is idle (forgot to COMMIT)
│   │         → Check if DDL (ALTER TABLE) holding metadata lock
│   └── NO  → Multiple transactions competing for same rows
├── Same table in all lock waits?
│   ├── YES → Hot table (high concurrency on few rows)
│   │         → Review access patterns, add index to reduce row scanning
│   └── NO  → Application-level deadlock (conflicting transaction order)
├── Deadlock pattern in error log?
│   ├── YES → Circular wait between two transactions
│   │         → Fix transaction ordering in application
│   │         → Add covering index to reduce lock scope
│   └── NO  → Simple lock wait timeout (not circular)
└── gap locks involved? (REPEATABLE READ isolation)
    └── YES → Consider READ COMMITTED isolation if appropriate
```

### Remediations

1. 🔴 **Immediate:** Kill blocking transaction if idle/stuck (verify safety first)
2. 🟡 **Short-term:** Add index to reduce lock scope (fewer rows locked per query)
3. 🟡 **Short-term:** Ensure consistent transaction ordering across application services
4. 🟢 **Planned:** Consider `innodb_lock_wait_timeout` adjustment
5. 🟢 **Planned:** Evaluate `READ COMMITTED` isolation level if gap locks problematic

---

## Runbook 5: Storage Growth & Fragmentation

### Symptoms
- CloudWatch `VolumeBytesUsed` (Aurora) or `FreeStorageSpace` (RDS) trending alarm
- Query 5.3 shows tables with fragmentation > 20%
- `OPTIMIZE TABLE` needed but window unclear

### Data Collection Steps

```
1. CloudWatch: Storage trend (7 days, 1h granularity)
2. Database (Query 5.1): Per-database sizes
3. Database (Query 5.2): Top 10 largest tables
4. Database (Query 5.3): Fragmentation analysis
5. Check: Binary log retention consuming space? (RDS only)
   aws rds describe-db-instances → BinlogRetentionPeriod
```

### Root Cause Decision Tree

```
Storage Growth
├── Single database/table driving growth?
│   ├── YES → Application data growth or bulk import
│   │         → Review data retention policies
│   └── NO  → Organic growth across all schemas
├── High fragmentation (> 20%)?
│   ├── YES → DELETE operations without OPTIMIZE TABLE
│   │         → Aurora: Use ALTER TABLE ... FORCE to reclaim
│   │         → RDS: Run OPTIMIZE TABLE during maintenance window
│   └── NO  → Genuine data volume increase
├── Binary logs consuming space? (RDS only)
│   ├── YES → Reduce binlog retention period
│   │         → call mysql.rds_set_configuration('binlog retention hours', 24)
│   └── NO  → Data files or temp tables
└── Aurora: FreeLocalStorage low?
    └── YES → Temp tables or sorts spilling to local SSD
              → Increase instance class or optimize queries using temp tables
```

### Remediations

1. 🔴 **Immediate (RDS):** Increase allocated storage or enable Auto Scaling
2. 🟡 **Short-term:** Run `OPTIMIZE TABLE` on top fragmented tables
3. 🟡 **Short-term:** Implement data archival/purge for historical data
4. 🟢 **Planned:** Partitioning strategy for large time-series tables
5. 🟢 **Planned:** Aurora I/O-Optimized for high storage workloads

---

## Runbook 6: Performance Insights — Wait Event Analysis

### Common Wait Events & Actions

| Wait Event | Category | Meaning | Action |
|-----------|----------|---------|--------|
| `io/table/sql/handler` | I/O | Table data reads from storage | Add indexes to reduce rows scanned |
| `io/file/innodb/innodb_data_file` | I/O | InnoDB data file reads | Increase buffer pool or instance class |
| `lock/table/sql/handler` | Lock | Table-level metadata lock | Find and resolve DDL blocking queries |
| `synch/mutex/innodb/trx_mutex` | Synch | Transaction contention | Reduce transaction duration |
| `synch/cond/sql/MYSQL_BIN_LOG::COND_done` | Synch | Binary log sync | Normal for sync_binlog=1; Aurora handles automatically |
| `wait/io/aurora_redo_log_flush` | I/O (Aurora) | Write commit flush | Normal for write-heavy workloads |
| `cpu` | CPU | Query execution on CPU | Optimize queries (indexes, rewrites) |
| `idle` | Idle | Connection waiting for client | Normal — connections in pool waiting for work |

---

## Runbook 7: Post-Failover Investigation

### Symptoms
- Application connectivity disruption (< 30s Aurora, 1-2min RDS)
- Short uptime detected (Query 1.1)
- RDS Events show failover occurred

### Investigation Steps

```
1. Check RDS Events for failover reason:
   aws rds describe-events --source-identifier <cluster-id> --duration 60

2. Check writer instance uptime (Query 1.1) — confirms recent restart

3. Pre-failover error log analysis:
   CloudWatch Logs: errors in the 5 minutes before failover timestamp

4. CloudWatch metrics at failover time:
   - CPUUtilization spike → instance overloaded
   - FreeableMemory → 0 → OOM
   - DatabaseConnections spike → connection storm
```

### Common Failover Causes

| Cause | Pre-Failover Signal | Prevention |
|-------|-------------------|------------|
| Instance failure | No warning | Multi-AZ + RDS Proxy absorbs transparently |
| Storage full (RDS) | FreeStorageSpace → 0 | Enable Storage Auto Scaling |
| OOM | FreeableMemory → 0 | Scale instance class or optimize memory use |
| Patching | Scheduled maintenance event | Configure maintenance window for low-traffic |
| Manual | User-initiated | Expected — verify DNS propagation |

---

## Runbook 8: Slow Query Investigation (CloudWatch Logs)

### Investigation Flow

```
1. Identify time range of performance degradation

2. Query slow query log:
   fields @timestamp, @message
   | filter @message like /Query_time/
   | parse @message "# Query_time: * Lock_time: * Rows_sent: * Rows_examined: *" as qt, lt, rs, re
   | filter qt > 5
   | sort qt desc
   | limit 20

3. Identify patterns:
   - Same query repeated → hot path optimization needed
   - High Rows_examined, low Rows_sent → missing index
   - High Lock_time → lock contention (see Runbook 4)
   - Temp table usage → memory/query optimization needed

4. Cross-reference with CloudWatch metrics at same timestamp:
   - CPU spike → confirms query CPU impact
   - IOPS spike → confirms I/O-heavy query
   - Connection spike → confirms connection storm
```

### CloudWatch Logs Insights Queries

**Top slow queries by execution time:**
```
fields @timestamp, @message
| filter @message like /Query_time/
| parse @message "# Query_time: *" as query_time
| sort query_time desc
| limit 10
```

**Error rate by hour:**
```
fields @timestamp, @message
| filter @message like /ERROR|Warning/
| stats count(*) as error_count by bin(1h)
| sort error_count desc
```

**Deadlock frequency:**
```
fields @timestamp, @message
| filter @message like /Deadlock found/
| stats count(*) as deadlocks by bin(1h)
```

**Connection errors:**
```
fields @timestamp, @message
| filter @message like /Too many connections|Aborted connection|Access denied/
| stats count(*) by bin(15m)
```
