# Top 50 Slow Queries (Last 24 Hours)

Identifies the most expensive queries by elapsed time. Replace `24` with a different hour range if needed.

Run as superuser or admin. Read-only query.

```sql
SELECT query_id,
       user_id,
       TRIM(database_name) AS db,
       query_type,
       elapsed_time / 1000000.0 AS elapsed_sec,
       queue_time / 1000000.0 AS queue_sec,
       execution_time / 1000000.0 AS exec_sec,
       compile_time / 1000000.0 AS compile_sec,
       planning_time / 1000000.0 AS planning_sec,
       lock_wait_time / 1000000.0 AS lock_wait_sec,
       result_cache_hit,
       query_priority,
       service_class_name,
       compute_type,
       status,
       error_message,
       SUBSTRING(TRIM(query_text), 1, 500) AS query_text
FROM sys_query_history
WHERE start_time >= DATEADD(hour, -24, GETDATE())
  AND user_id > 1
ORDER BY elapsed_time DESC
LIMIT 50;
```
