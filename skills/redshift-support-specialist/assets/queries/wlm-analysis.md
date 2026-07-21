# WLM & Concurrency Analysis Queries

Run as superuser or admin. All queries are read-only.

## 1. WLM Queue Performance (Last 24 Hours)

```sql
SELECT service_class_name,
       COUNT(*) AS query_count,
       AVG(queue_time / 1000000.0) AS avg_queue_sec,
       MAX(queue_time / 1000000.0) AS max_queue_sec,
       AVG(execution_time / 1000000.0) AS avg_exec_sec,
       MAX(execution_time / 1000000.0) AS max_exec_sec
FROM sys_query_history
WHERE start_time >= DATEADD(hour, -24, GETDATE())
  AND user_id > 1
  AND service_class_name IS NOT NULL
GROUP BY service_class_name
ORDER BY avg_queue_sec DESC;
```

## 2. Current WLM Queue State (Real-Time)

```sql
SELECT service_class,
       num_executing_queries,
       num_queued_queries,
       num_slots,
       evictable_mem / (1024 * 1024) AS evictable_mem_mb
FROM stv_wlm_service_class_state
WHERE service_class > 4  -- User-defined queues only
ORDER BY service_class;
```

## 3. WLM Queue Configuration

```sql
SELECT service_class,
       num_query_tasks AS concurrency,
       query_working_mem / 1024 AS working_mem_mb,
       max_execution_time / 1000000 AS max_exec_sec,
       priority
FROM stv_wlm_service_class_config
WHERE service_class > 4
ORDER BY service_class;
```

## 4. Queries Currently Running (with WLM state)

```sql
SELECT q.query,
       q.service_class,
       q.wlm_start_time,
       q.state,
       q.queue_time / 1000000.0 AS queue_sec,
       q.exec_time / 1000000.0 AS exec_sec,
       TRIM(s.query_text) AS query_text
FROM stv_wlm_query_state q
LEFT JOIN stv_recents s ON q.query = s.query
WHERE q.service_class > 4
ORDER BY q.exec_time DESC;
```

## 5. QMR Rule Actions (Last 7 Days)

```sql
SELECT rule,
       action,
       service_class,
       COUNT(*) AS action_count
FROM stl_wlm_rule_action
WHERE record_time >= DATEADD(day, -7, GETDATE())
GROUP BY rule, action, service_class
ORDER BY action_count DESC;
```

## 6. Concurrency Scaling Usage (Last 7 Days)

```sql
SELECT DATE(start_time) AS query_date,
       COUNT(*) AS burst_query_count,
       SUM(execution_time / 1000000.0) AS total_burst_exec_sec
FROM sys_query_history
WHERE start_time >= DATEADD(day, -7, GETDATE())
  AND compute_type = 'Concurrency Scaling'
GROUP BY DATE(start_time)
ORDER BY query_date DESC;
```
