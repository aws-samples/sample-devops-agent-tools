# database-rds-devops

Database DevOps expertise skill for AWS DevOps Agent — automated health assessments, performance diagnostics, log-based troubleshooting, and operational recommendations for Aurora MySQL, RDS MySQL, and Aurora PostgreSQL.

## What This Skill Does

This skill provides comprehensive database operations expertise covering:

- **12-dimension AWS-level health scoring** — encryption, monitoring, HA, version currency, auto scaling, deletion protection, and more
- **8-dimension database-level health scoring** — connections, buffer pool, replication, locks, monitoring, storage, indexes, instrumentation
- **23 MySQL health check queries** — read-only diagnostics across 9 categories using Performance Schema and Information Schema
- **4 PostgreSQL health check queries** — active sessions, table bloat, top queries, transaction ID wraparound
- **CloudWatch Metrics analysis** — CPU, connections, memory, IOPS, replica lag with severity thresholds
- **CloudWatch Logs Insights** — slow query log analysis, error pattern detection, deadlock identification
- **33-check operational validation** — monitoring, alerting, security, HA, cost optimization, tagging

## Prerequisites

### IAM Permissions

The DevOps Agent role needs the following permissions (most covered by `AIDevOpsAgentAccessPolicy`):

- `rds:DescribeDBClusters`
- `rds:DescribeDBInstances`
- `rds:DescribeDBEngineVersions`
- `rds:DescribeDBClusterParameters`
- `rds:DescribeDBLogFiles`
- `rds:ListTagsForResource`
- `rds:DescribeReservedDBInstances`
- `rds:DescribeGlobalClusters`
- `cloudwatch:GetMetricData`
- `cloudwatch:GetMetricStatistics`
- `cloudwatch:DescribeAlarms`
- `logs:StartQuery`
- `logs:GetQueryResults`
- `logs:DescribeLogGroups`
- `application-autoscaling:DescribeScalableTargets`
- `ec2:DescribeSecurityGroups`

### AWS Resources

- Aurora MySQL, RDS MySQL, or Aurora PostgreSQL instances/clusters
- CloudWatch Logs export enabled (slowquery, error, or postgresql logs)
- Enhanced Monitoring enabled (recommended)
- Performance Insights enabled (recommended)

### For Database-Level Diagnostics (Optional — requires MCP server)

This skill integrates with the **mysql-aidba** custom MCP server for executing read-only health check queries directly against your database.

- **Location:** `mcp/rds-aidba/` (co-located in this repo)
- **Transport:** stdio (via `uvx awslabs.mysql-mcp-server@latest`)
- **Setup Guide:** See `references/mcp-setup.md`

Requirements:
- Lambda function deployed with Function URL enabled
- Secrets Manager secret with database credentials
- `performance_schema` enabled (default ON for Aurora MySQL 2.x+)
- `pg_stat_statements` extension installed (Aurora PostgreSQL)
- Read-only monitoring user with SELECT on system schemas

**Note:** The MCP server is optional. Without it, the skill operates using AWS CLI + CloudWatch (Layers 1 & 2) and provides query references for manual execution.

## How to Use

### Subagents

This skill works with the following DevOps Agent subagents:
- **Chat** — Interactive health checks and troubleshooting
- **Incident RCA** — Root cause analysis for database performance incidents

### Example Prompts

```
"Run a health check on my Aurora MySQL cluster my-prod-cluster"
"Why is my RDS MySQL instance showing high CPU usage?"
"Check for slow queries on my Aurora cluster in the last 3 hours"
"My application is getting Too many connections errors"
"Is my Aurora PostgreSQL at risk for transaction ID wraparound?"
"Check replication lag on my Aurora read replicas"
"Analyze storage fragmentation on my MySQL databases"
"Review the security configuration of my database cluster"
```

## Skill Structure

```
database-rds-devops/
├── SKILL.md                              # Main skill instructions
├── README.md                             # This file
├── CHANGELOG.md                          # Version history
├── references/
│   ├── mysql-health-checks.md            # 23 MySQL diagnostic queries
│   ├── postgresql-health-checks.md       # 4 PostgreSQL diagnostic queries
│   ├── aurora-validation-checklist.md    # 33-check operational validation
│   └── mcp-setup.md                      # MCP server deployment guide
└── evals/
    ├── evals.json                        # Functional test scenarios
    └── eval_queries.json                 # Trigger tests
```

## Supported Engines

| Engine | Health Check | CloudWatch | Log Analysis | DB Queries |
|--------|-------------|------------|--------------|------------|
| Aurora MySQL 2.x (5.7 compat) | ✅ | ✅ | ✅ | ✅ |
| Aurora MySQL 3.x (8.0 compat) | ✅ | ✅ | ✅ | ✅ |
| RDS MySQL 5.7 | ✅ | ✅ | ✅ | ✅ |
| RDS MySQL 8.0 | ✅ | ✅ | ✅ | ✅ |
| Aurora PostgreSQL | ✅ | ✅ | ✅ | ✅ |
| RDS PostgreSQL | ✅ | ✅ | ✅ | ✅ |

## Safety

This skill operates in **read-only mode**:
- No DDL, DML, or DCL operations
- No configuration changes (recommendations only)
- No credential exposure
- All database queries are predefined (no dynamic SQL)

## Disclaimer

> ⚠️ This skill is sample code, not intended for production use without additional review and testing. Users should validate in a non-production environment first. The health check queries and thresholds are guidelines — actual thresholds should be tuned based on your workload characteristics.
