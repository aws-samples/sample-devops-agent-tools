USE mcptest;

-- Insert 1 million audit log records
INSERT INTO audit_log (user_id, action, table_name, record_id, old_value, new_value)
SELECT 
    FLOOR(1 + RAND() * 10000),
    ELT(FLOOR(1 + RAND() * 4), 'INSERT', 'UPDATE', 'DELETE', 'SELECT'),
    ELT(FLOOR(1 + RAND() * 4), 'customers', 'orders', 'products', 'order_items'),
    FLOOR(1 + RAND() * 100000),
    CONCAT('Old value ', n),
    CONCAT('New value ', n)
FROM (
    SELECT @row := @row + 1 AS n
    FROM (SELECT 0 UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) t1,
         (SELECT 0 UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) t2,
         (SELECT 0 UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) t3,
         (SELECT 0 UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) t4,
         (SELECT 0 UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) t5,
         (SELECT @row := 0) r
    LIMIT 1000000
) numbers;

-- Delete 80% of records to create massive fragmentation
DELETE FROM audit_log WHERE log_id % 5 != 0;

-- Check fragmentation (should show ~75% fragmentation)
SELECT 
    table_name,
    ROUND(data_length / 1024 / 1024, 2) AS data_mb,
    ROUND(index_length / 1024 / 1024, 2) AS index_mb,
    ROUND(data_free / 1024 / 1024, 2) AS free_mb,
    ROUND((data_free / (data_length + index_length + data_free)) * 100, 2) AS fragmentation_pct
FROM information_schema.tables
WHERE table_schema = 'mcptest'
  AND table_name = 'audit_log';
