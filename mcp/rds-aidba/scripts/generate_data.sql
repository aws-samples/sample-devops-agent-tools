USE mcptest;

-- Stored Procedure: Generate 100,000 customers
DELIMITER $$
CREATE PROCEDURE generate_customers(IN num_rows INT)
BEGIN
    DECLARE i INT DEFAULT 1;
    WHILE i <= num_rows DO
        INSERT INTO customers (email, first_name, last_name, country)
        VALUES (
            CONCAT('user', i, '@example.com'),
            CONCAT('FirstName', i),
            CONCAT('LastName', i),
            ELT(FLOOR(1 + RAND() * 5), 'USA', 'Canada', 'UK', 'Germany', 'France')
        );
        SET i = i + 1;
    END WHILE;
END$$
DELIMITER ;

-- Stored Procedure: Generate 500,000 orders
DELIMITER $$
CREATE PROCEDURE generate_orders(IN num_rows INT)
BEGIN
    DECLARE i INT DEFAULT 1;
    DECLARE cust_id INT;
    WHILE i <= num_rows DO
        SET cust_id = FLOOR(1 + RAND() * 100000);
        INSERT INTO orders (customer_id, order_date, total_amount, status)
        VALUES (
            cust_id,
            DATE_SUB(NOW(), INTERVAL FLOOR(RAND() * 365) DAY),
            ROUND(10 + RAND() * 990, 2),
            ELT(FLOOR(1 + RAND() * 4), 'pending', 'processing', 'shipped', 'delivered')
        );
        SET i = i + 1;
    END WHILE;
END$$
DELIMITER ;

-- Stored Procedure: Generate 1,000,000 order items
DELIMITER $$
CREATE PROCEDURE generate_order_items(IN num_rows INT)
BEGIN
    DECLARE i INT DEFAULT 1;
    DECLARE ord_id INT;
    WHILE i <= num_rows DO
        SET ord_id = FLOOR(1 + RAND() * 500000);
        INSERT INTO order_items (order_id, product_id, quantity, price)
        VALUES (
            ord_id,
            FLOOR(1 + RAND() * 10000),
            FLOOR(1 + RAND() * 10),
            ROUND(5 + RAND() * 95, 2)
        );
        SET i = i + 1;
    END WHILE;
END$$
DELIMITER ;

-- Execute data generation (this will take several minutes)
CALL generate_customers(100000);
CALL generate_orders(500000);
CALL generate_order_items(1000000);

-- Insert 10,000 products
INSERT INTO products (product_name, category, price, stock_quantity)
SELECT 
    CONCAT('Product ', n),
    ELT(FLOOR(1 + RAND() * 5), 'Electronics', 'Clothing', 'Books', 'Home', 'Sports'),
    ROUND(10 + RAND() * 990, 2),
    FLOOR(RAND() * 1000)
FROM (
    SELECT @row := @row + 1 AS n
    FROM (SELECT 0 UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) t1,
         (SELECT 0 UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) t2,
         (SELECT 0 UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) t3,
         (SELECT 0 UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) t4,
         (SELECT @row := 0) r
    LIMIT 10000
) numbers;
