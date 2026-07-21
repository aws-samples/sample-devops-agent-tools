# postgresql-dba-mcp — Read-Only PostgreSQL MCP Server

Custom MCP server for AWS DevOps Agent that provides **safe, query-allowlisted diagnostic access** to Amazon RDS for PostgreSQL and Aurora PostgreSQL instances.

## Architecture

```
DevOps Agent (AgentSpace)
        |
        v  (Streamable HTTP + SigV4 auth)
+--------------------------------------------------+
|  Lambda (postgresql-dba-mcp)                     |
|                                                  |
|  Tools:                                          |
|  - execute_health_query(category, query_id, ...) |
|  - list_health_queries()                         |
|  - run_full_health_check(endpoint, db)           |
|  - list_rds_instances()                          |
|  - get_instance_config(instance_id)              |
|  - get_instance_metrics(instance_id)             |
|                                                  |
|  Safety layer:                                   |
|  + Only 25 predefined diagnostic queries         |
|  + Credentials from Secrets Manager              |
|  x No dynamic SQL / arbitrary queries            |
|  x No DDL, DML, or DCL                          |
+--------------------------------------------------+
        |
        v  (libpq / pg8000)
+--------------------------------------------------+
|  Customer's RDS/Aurora PostgreSQL Instance (VPC) |
+--------------------------------------------------+
```

## Security Model

This server uses a **query-allowlist** approach:

1. **No dynamic SQL** — the agent cannot generate or pass arbitrary queries
2. **25 predefined queries only** — mapped to 9 diagnostic categories
3. **Secrets Manager** — database credentials retrieved at runtime, never exposed
4. **VPC deployment** — Lambda runs in customer's VPC for database connectivity
5. **Read-only DB user** — MCP connects with a user that has only SELECT privileges
6. **Tool-level allowlists** — instance and database names validated before execution
7. **Production enforcement** — wildcard allowlists blocked in prod stage

## Tools

| Tool | Description |
|------|-------------|
| `execute_health_query` | Run a predefined query by category + query_id |
| `list_health_queries` | List all available diagnostic queries |
| `run_full_health_check` | Run key queries from all categories in one shot |
| `list_rds_instances` | List PostgreSQL instances in the account |
| `get_instance_config` | Detailed instance configuration and settings |
| `get_instance_metrics` | CloudWatch metrics (CPU, memory, IOPS, latency) |

## Query Categories (25 queries across 9 categories)

| Category | Query IDs | Description |
|----------|-----------|-------------|
| 1. Server Information | 1.1-1.3 | Version, uptime, database sizes |
| 2. System Configuration | 2.1-2.2 | Key parameters, memory settings |
| 3. Current Activity | 3.1-3.4 | Connections, long queries, locks |
| 4. Replication | 4.1-4.2 | Replication status, slot lag |
| 5. Storage and Bloat | 5.1-5.3 | Table sizes, dead tuples, tablespaces |
| 6. Performance | 6.1-6.4 | Top queries, cache hit ratio, index hits |
| 7. Vacuum & Maintenance | 7.1-7.3 | Vacuum needs, XID wraparound risk |
| 8. Index Optimization | 8.1-8.3 | Unused indexes, duplicates, scan ratios |
| 9. Composite Health | 9.1 | Aggregated health score metrics |


## Prerequisites

- AWS SAM CLI
- VPC with connectivity to RDS/Aurora PostgreSQL instances
- Secrets Manager secret with database credentials (JSON: `{"username": "...", "password": "..."}`)
- A read-only PostgreSQL user with SELECT privileges on system catalogs
- IAM role with:
  - `secretsmanager:GetSecretValue` (scoped to specific secret)
  - `rds:DescribeDBInstances`, `rds:DescribeDBClusters`, `rds:DescribeDBEngineVersions`
  - `ec2:DescribeInstanceTypes`
  - `cloudwatch:GetMetricStatistics`
  - VPC execution (`ec2:CreateNetworkInterface`, etc.)

## Deployment

```bash
sam build
sam deploy --guided \
  --parameter-overrides \
    SecretArn=arn:aws:secretsmanager:us-east-1:ACCOUNT:secret:pg-dba-creds \
    VpcId=vpc-xxx \
    SubnetIds=subnet-xxx,subnet-yyy \
    SecurityGroupId=sg-xxx \
    AllowedInstances=my-prod-db,my-staging-db \
    AllowedDatabases=postgres,myapp
```

## V2 Skills Coverage

This MCP server consolidates all 4 skills from the v2 tool:

| V2 Skill | MCP Categories | MCP Tools |
|----------|---------------|-----------|
| health_check.py | 1, 3, 5, 7, 8, 9, 11 | `run_full_health_check`, `execute_health_query` |
| parameter_tuning.py | 2 | `execute_health_query`, `get_instance_config`, `get_instance_metrics` |
| pre_upgrade_check.py | 10 | `execute_health_query`, `list_rds_instances`, `get_instance_config` |
| sql_tuning.py | 6 | `execute_health_query`, `explain_query` |

## Local Testing

```bash
pip install -r layers/dependencies/requirements.txt
cd src
python server.py
# Server starts on http://0.0.0.0:8000
```

## Disclaimer

This MCP server is sample code, not intended for production use without additional
review and testing. Users should validate in a non-production environment first.
Ensure the database user has only read-only (SELECT) privileges.
