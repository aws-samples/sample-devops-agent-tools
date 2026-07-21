# COPY / Data Loading Performance Queries

Run as superuser or admin. All queries are read-only.

## 1. COPY Performance Summary (Last 7 Days)

```sql
SELECT TRIM(file_format) AS format,
       COUNT(*) AS copy_count,
       AVG(loaded_rows) AS avg_rows,
       AVG(loaded_bytes / (1024*1024.0)) AS avg_mb,
       AVG(duration / 1000000.0) AS avg_duration_sec,
       AVG(loaded_bytes / NULLIF(duration, 0) * 1000000.0 / (1024*1024.0)) AS avg_mb_per_sec
FROM sys_load_history
WHERE start_time >= DATEADD(day, -7, GETDATE())
GROUP BY TRIM(file_format)
ORDER BY copy_count DESC;
```

## 2. COPY File Count & Size Analysis (detect low parallelism)

```sql
SELECT query_id,
       TRIM(file_format) AS format,
       COUNT(*) AS file_count,
       AVG(file_size / (1024*1024.0)) AS avg_file_size_mb,
       SUM(file_size / (1024*1024.0)) AS total_mb,
       SUM(loaded_rows) AS total_rows
FROM sys_load_detail
WHERE start_time >= DATEADD(day, -7, GETDATE())
GROUP BY query_id, TRIM(file_format)
HAVING COUNT(*) < 4  -- Flag: fewer files than recommended
ORDER BY total_mb DESC
LIMIT 50;
```

## 3. Small File Detection (< 10 MB average)

```sql
SELECT query_id,
       COUNT(*) AS file_count,
       AVG(file_size / (1024*1024.0)) AS avg_file_size_mb,
       MIN(file_size / (1024*1024.0)) AS min_file_size_mb,
       MAX(file_size / (1024*1024.0)) AS max_file_size_mb
FROM sys_load_detail
WHERE start_time >= DATEADD(day, -7, GETDATE())
GROUP BY query_id
HAVING AVG(file_size / (1024*1024.0)) < 10
ORDER BY avg_file_size_mb ASC
LIMIT 50;
```

## 4. COPY Errors (Last 7 Days)

```sql
SELECT err_code,
       TRIM(err_reason) AS err_reason,
       TRIM(filename) AS filename,
       line_number,
       colname,
       TRIM(raw_field_value) AS raw_value,
       starttime
FROM stl_load_errors
WHERE starttime >= DATEADD(day, -7, GETDATE())
ORDER BY starttime DESC
LIMIT 50;
```

## 5. Single-Row INSERT Detection (Anti-pattern)

```sql
SELECT DATE(start_time) AS insert_date,
       COUNT(*) AS single_insert_count
FROM sys_query_history
WHERE start_time >= DATEADD(day, -7, GETDATE())
  AND query_type = 'INSERT'
  AND returned_rows = 1
  AND user_id > 1
GROUP BY DATE(start_time)
ORDER BY insert_date DESC;
```

## 6. Commit Frequency (detect excessive commits)

```sql
SELECT DATE(startqueue) AS commit_date,
       COUNT(*) AS commit_count,
       AVG(DATEDIFF(microsecond, startqueue, endtime) / 1000000.0) AS avg_commit_sec,
       MAX(queuelen) AS max_queue_length
FROM stl_commit_stats
WHERE startqueue >= DATEADD(day, -7, GETDATE())
GROUP BY DATE(startqueue)
ORDER BY commit_date DESC;
```
