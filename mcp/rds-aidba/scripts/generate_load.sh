#!/bin/bash

# Configuration
RESOURCE_ARN="arn:aws:rds:us-east-1:727896739153:cluster:ams-mcp-test-km-2"
SECRET_ARN="arn:aws:secretsmanager:us-east-1:727896739153:secret:rds!cluster-115a7c75-2c6e-4138-b0b8-96048fe135e3-66tfMa"
DATABASE="mcptest"
REGION="us-east-1"

echo "🔥 Starting load generation for 10 minutes..."

# Function to execute query via RDS Data API
execute_query() {
    local sql="$1"
    aws rds-data execute-statement \
        --resource-arn "$RESOURCE_ARN" \
        --secret-arn "$SECRET_ARN" \
        --database "$DATABASE" \
        --sql "$sql" \
        --region "$REGION" \
        --output json > /dev/null 2>&1
}

# Generate load for 10 minutes
END_TIME=$((SECONDS + 600))
ITERATION=0

while [ $SECONDS -lt $END_TIME ]; do
    ITERATION=$((ITERATION + 1))
    REMAINING=$((END_TIME - SECONDS))
    echo "⚡ Iteration $ITERATION - Generating load... (${REMAINING}s remaining)"
    
    # Query 1: Full table scan on customers (no index on email)
    execute_query "SELECT * FROM customers WHERE email LIKE '%example.com%' LIMIT 100;" &
    
    # Query 2: Unindexed JOIN (no index on orders.customer_id)
    execute_query "SELECT c.customer_id, c.email, COUNT(o.order_id) AS order_count FROM customers c LEFT JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.customer_id, c.email LIMIT 50;" &
    
    # Query 3: Inefficient subquery pattern
    execute_query "SELECT customer_id, (SELECT COUNT(*) FROM orders WHERE customer_id = c.customer_id) AS cnt FROM customers c LIMIT 100;" &
    
    # Query 4: Function on indexed column (prevents index usage)
    execute_query "SELECT * FROM orders WHERE YEAR(order_date) = 2024 LIMIT 100;" &
    
    # Query 5: OR conditions (prevents index usage)
    execute_query "SELECT * FROM orders WHERE status = 'pending' OR total_amount > 500 LIMIT 100;" &
    
    # Query 6: Cartesian product (intentionally expensive)
    execute_query "SELECT * FROM customers c, orders o WHERE c.customer_id <= 10 AND o.order_id <= 100 LIMIT 50;" &
    
    # Query 7: Access inefficient view
    execute_query "SELECT * FROM inefficient_customer_orders LIMIT 20;" &
    
    # Query 8: Full table scan on products (no index on category)
    execute_query "SELECT * FROM products WHERE category = 'Electronics' LIMIT 100;" &
    
    # Wait for background jobs to complete
    wait
    
    # Small delay between batches
    sleep 3
done

echo "✅ Load generation complete! Generated load for $ITERATION iterations."
