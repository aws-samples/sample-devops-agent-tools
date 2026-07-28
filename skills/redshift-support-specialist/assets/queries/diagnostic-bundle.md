# Redshift Query Diagnostic Bundle

Produces ONE result set with all diagnostic data needed for query optimization.

## Instructions

1. Replace `<QUERY_ID>` with the slow query's query_id
2. Replace `'<TABLE1>','<TABLE2>'` with the table names used in the query
3. Run the query via the `execute_query` MCP tool — one result with 3 columns: section, key, value
4. Analyze ALL returned rows directly — no CSV export or manual sharing step is needed

## Query

```sql
WITH history AS (
    SELECT '1-HISTORY' AS section,
           key,
           value
    FROM (
        SELECT 'query_id' AS key, CAST(query_id AS VARCHAR) AS value FROM sys_query_history WHERE query_id = <QUERY_ID>
        UNION ALL
        SELECT 'elapsed_sec', CAST(elapsed_time / 1000000.0 AS VARCHAR) FROM sys_query_history WHERE query_id = <QUERY_ID>
        UNION ALL
        SELECT 'execution_sec', CAST(execution_time / 1000000.0 AS VARCHAR) FROM sys_query_history WHERE query_id = <QUERY_ID>
        UNION ALL
        SELECT 'queue_sec', CAST(queue_time / 1000000.0 AS VARCHAR) FROM sys_query_history WHERE query_id = <QUERY_ID>
        UNION ALL
        SELECT 'compile_sec', CAST(compile_time / 1000000.0 AS VARCHAR) FROM sys_query_history WHERE query_id = <QUERY_ID>
        UNION ALL
        SELECT 'planning_sec', CAST(planning_time / 1000000.0 AS VARCHAR) FROM sys_query_history WHERE query_id = <QUERY_ID>
        UNION ALL
        SELECT 'lock_wait_sec', CAST(lock_wait_time / 1000000.0 AS VARCHAR) FROM sys_query_history WHERE query_id = <QUERY_ID>
        UNION ALL
        SELECT 'result_cache_hit', CAST(result_cache_hit AS VARCHAR) FROM sys_query_history WHERE query_id = <QUERY_ID>
        UNION ALL
        SELECT 'query_type', CAST(query_type AS VARCHAR) FROM sys_query_history WHERE query_id = <QUERY_ID>
        UNION ALL
        SELECT 'status', CAST(status AS VARCHAR) FROM sys_query_history WHERE query_id = <QUERY_ID>
    )
),
detail AS (
    SELECT '2-DETAIL' AS section,
           CAST(step_num AS VARCHAR) || '|' || COALESCE(TRIM(step_label), '') AS key,
           CAST(duration / 1000000.0 AS VARCHAR) || '|' ||
           CAST(rows_produced AS VARCHAR) || '|' ||
           CAST(bytes_scanned AS VARCHAR) || '|' ||
           CAST(spill_local AS VARCHAR) || '|' ||
           CAST(spill_remote AS VARCHAR) || '|' ||
           CAST(bytes_broadcast AS VARCHAR) || '|' ||
           CAST(bytes_distributed AS VARCHAR) AS value
    FROM sys_query_detail
    WHERE query_id = <QUERY_ID>
    ORDER BY duration DESC
    LIMIT 20
),
plan_data AS (
    SELECT '3-PLAN' AS section,
           CAST(plan_node_id AS VARCHAR) AS key,
           TRIM(plan_node) AS value
    FROM sys_query_explain
    WHERE query_id = <QUERY_ID>
    ORDER BY plan_node_id
),
table_info AS (
    SELECT '4-TABLE_INFO' AS section,
           TRIM("schema") || '.' || TRIM("table") AS key,
           CAST(tbl_rows AS VARCHAR) || '|' ||
           COALESCE(diststyle, '') || '|' ||
           COALESCE(sortkey1, '') || '|' ||
           CAST(skew_rows AS VARCHAR) || '|' ||
           CAST(stats_off AS VARCHAR) || '|' ||
           CAST(unsorted AS VARCHAR) || '|' ||
           CAST(max_varchar AS VARCHAR) || '|' ||
           COALESCE(CAST(encoded AS VARCHAR), '') AS value
    FROM svv_table_info
    WHERE "table" IN ('<TABLE1>','<TABLE2>')
)
SELECT section, key, value FROM history
UNION ALL
SELECT section, key, value FROM detail
UNION ALL
SELECT section, key, value FROM plan_data
UNION ALL
SELECT section, key, value FROM table_info
ORDER BY section, key;
```

## Helper: Find your query_id

```sql
SELECT query_id, user_id, SUBSTRING(query_text, 1, 100) as query_preview,
       elapsed_time/1000000.0 as elapsed_sec, start_time
FROM sys_query_history
WHERE start_time >= DATEADD(hour, -24, GETDATE())
  AND user_id > 1
ORDER BY elapsed_time DESC LIMIT 20;
```
