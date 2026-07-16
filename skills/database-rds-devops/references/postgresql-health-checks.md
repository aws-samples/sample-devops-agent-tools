# PostgreSQL Health Check Queries — Aurora PostgreSQL & RDS PostgreSQL

## Overview

Curated read-only diagnostic queries for Aurora PostgreSQL and RDS PostgreSQL health assessment. All queries use `pg_stat_*` views, `pg_settings`, and system catalogs.

### Prerequisites

- `pg_stat_statements` extension installed and enabled
- Add `pg_stat_statements` to `shared_preload_libraries` parameter (requires restart)
- Monitoring user with appropriate SELECT privileges on system catalogs

---

## PG Query 3.1 — Active Sessions

**Purpose:** Identify currently executing queries and long-running transactions.

```sql
SELECT 
  pid, usename, datname, state, wait_event_type, wait_event,
  EXTRACT(EPOCH FROM (now() - query_start))::int AS duration_seconds,
  LEFT(query, 200) AS query_text
FROM pg_stat_activity
WHERE state != 'idle' AND pid != pg_backend_pid()
ORDER BY query_start ASC LIMIT 20;
```

**Thresholds:** duration_seconds — 🟢 < 60 | 🟡 60-300 | 🔴 > 300

---

## PG Query 5.3 — Table Bloat

**Purpose:** Detect tables with excessive dead tuples indicating bloat.

```sql
SELECT
  schemaname, relname AS table_name, n_live_tup, n_dead_tup,
  ROUND(n_dead_tup::numeric / NULLIF(n_live_tup, 0), 3) AS bloat_ratio,
  last_autovacuum, last_autoanalyze
FROM pg_stat_user_tables
WHERE n_dead_tup > 10000
ORDER BY n_dead_tup DESC LIMIT 10;
```

**Thresholds:** bloat_ratio — 🟢 < 0.1 | 🟡 0.1-0.3 | 🔴 > 0.3

---

## PG Query 6.1 — Top Queries (pg_stat_statements)

**Purpose:** Identify queries consuming the most execution time.

**Prerequisite:** `pg_stat_statements` extension must be installed.

```sql
SELECT 
  LEFT(query, 200) AS query_text, calls,
  ROUND(total_exec_time::numeric / 1000, 2) AS total_time_sec,
  ROUND(mean_exec_time::numeric / 1000, 4) AS avg_time_sec, rows,
  ROUND((shared_blks_hit::numeric / NULLIF(shared_blks_hit + shared_blks_read, 0)) * 100, 2) AS cache_hit_pct
FROM pg_stat_statements
WHERE userid != (SELECT usesysid FROM pg_user WHERE usename = 'rdsadmin')
ORDER BY total_exec_time DESC LIMIT 10;
```

**Interpretation:** cache_hit_pct < 90% → Queries hitting disk heavily → need more shared_buffers or indexes.

---

## PG Query 7.2 — Transaction ID Age (Wraparound Risk)

**Purpose:** Check for transaction ID wraparound risk across all databases.

```sql
SELECT 
  datname,
  age(datfrozenxid) AS txid_age,
  ROUND(age(datfrozenxid)::numeric / 2147483647 * 100, 2) AS wraparound_pct
FROM pg_database
WHERE datname NOT IN ('template0', 'template1', 'rdsadmin')
ORDER BY age(datfrozenxid) DESC;
```

**Thresholds:** wraparound_pct — 🟢 < 47% | 🟡 47-70% | 🔴 > 70%

**Critical Warning:** Wraparound causes database shutdown at 2 billion transactions. Age > 1.5 billion requires immediate VACUUM intervention.

---

## Platform Compatibility

| Query | Aurora PostgreSQL | RDS PostgreSQL |
|-------|-------------------|----------------|
| PG 3.1 | ✅ | ✅ |
| PG 5.3 | ✅ | ✅ |
| PG 6.1 | ✅ (requires extension) | ✅ (requires extension) |
| PG 7.2 | ✅ | ✅ |
