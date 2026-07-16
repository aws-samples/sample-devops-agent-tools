USE mcptest;

-- N+1 Query Pattern Simulator
DELIMITER $$
CREATE PROCEDURE simulate_n_plus_1()
BEGIN
    DECLARE done INT DEFAULT FALSE;
    DECLARE cust_id INT;
    DECLARE cur CURSOR FOR SELECT customer_id FROM customers LIMIT 1000;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;
    
    OPEN cur;
    read_loop: LOOP
        FETCH cur INTO cust_id;
        IF done THEN
            LEAVE read_loop;
        END IF;
        -- Individual query for each customer (N+1 anti-pattern)
        SELECT COUNT(*) INTO @order_count FROM orders WHERE customer_id = cust_id;
    END LOOP;
    CLOSE cur;
END$$
DELIMITER ;

-- Inefficient Subquery Pattern (causes multiple table scans)
CREATE VIEW inefficient_customer_orders AS
SELECT 
    c.customer_id,
    c.email,
    (SELECT COUNT(*) FROM orders WHERE customer_id = c.customer_id) AS order_count,
    (SELECT SUM(total_amount) FROM orders WHERE customer_id = c.customer_id) AS total_spent,
    (SELECT MAX(order_date) FROM orders WHERE customer_id = c.customer_id) AS last_order_date
FROM customers c;

-- Query with OR conditions (prevents index usage)
CREATE VIEW slow_query_view AS
SELECT *
FROM orders
WHERE status = 'pending' OR total_amount > 500 OR customer_id < 1000;
