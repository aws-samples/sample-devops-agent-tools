# rds-aidba - Read-Only MySQL/PostgreSQL MCP Server

Custom MCP server for AWS DevOps Agent providing safe, query-allowlisted diagnostic access to Aurora MySQL, RDS MySQL, Aurora PostgreSQL, and RDS PostgreSQL via the RDS Data API.

## Tools (10)

| Tool | Description |
|------|-------------|
| execute_health_query | Run predefined query by engine + category + query_id |
| list_health_queries | List all queries for an engine (mysql/postgresql) |
| run_category_check | All queries in a category |
| run_full_health_check | Key queries from all categories |
| list_clusters | List Aurora/RDS clusters |
| get_cluster_health | Config, encryption, backups, monitoring |
| get_cluster_metrics | CloudWatch: CPU, connections, IOPS, lag |
| get_performance_insights | PI wait events and DB load |
| get_proxy_health | RDS Proxy status and targets |
| get_serverless_capacity | Serverless v2 ACU utilization |

## Queries: 54 total (24 MySQL + 30 PostgreSQL, 10 categories each)

## Security

- Query allowlist only (no dynamic SQL)
- Cluster/database allowlists (defense-in-depth)
- Production enforcement blocks wildcards
- No VPC required (RDS Data API)
- Function URL with AWS_IAM auth

## Deploy

    sam build
    sam deploy --stack-name rds-aidba-mcp --capabilities CAPABILITY_NAMED_IAM --resolve-s3 --no-confirm-changeset

## Register in DevOps Agent

- URL: Function URL from stack output (use as-is, already includes /mcp)
- Service Name: lambda
- Auth: IAM (SigV4)

## Disclaimer

This is sample code, not intended for production use without review. Validate in non-production first.
