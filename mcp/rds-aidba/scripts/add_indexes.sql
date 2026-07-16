USE mcptest;

-- Add indexes to improve query performance
ALTER TABLE customers ADD INDEX idx_email (email);
ALTER TABLE customers ADD INDEX idx_country (country);
ALTER TABLE customers ADD INDEX idx_created_at (created_at);

ALTER TABLE orders ADD INDEX idx_customer_id (customer_id);
ALTER TABLE orders ADD INDEX idx_order_date (order_date);
ALTER TABLE orders ADD INDEX idx_status (status);
ALTER TABLE orders ADD INDEX idx_created_at (created_at);

ALTER TABLE order_items ADD INDEX idx_order_id (order_id);
ALTER TABLE order_items ADD INDEX idx_product_id (product_id);

ALTER TABLE products ADD INDEX idx_category (category);

ALTER TABLE audit_log ADD INDEX idx_user_id (user_id);
ALTER TABLE audit_log ADD INDEX idx_created_at (created_at);
ALTER TABLE audit_log ADD INDEX idx_table_name (table_name);

-- Verify indexes were created
SELECT 
    TABLE_NAME,
    INDEX_NAME,
    COLUMN_NAME,
    SEQ_IN_INDEX,
    INDEX_TYPE
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'mcptest'
  AND TABLE_NAME IN ('customers', 'orders', 'order_items', 'products', 'audit_log')
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;
