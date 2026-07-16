# MySQL AIDBA MCP Server Setup

Custom MCP client + agent for executing read-only database health check queries against Aurora MySQL, RDS MySQL, and Aurora PostgreSQL instances.

**Location in this repo:** `mcp/rds-aidba/`

## Architecture

```
┌─────────────────────────┐     ┌───────────────────────────────┐     ┌─────────────────────┐
│   DevOps Agent          │     │  mysql-aidba                  │     │                     │
│   (invokes skill)       │────▶│  ├── MCP Client (stdio)       │────▶│  Aurora/RDS MySQL   │
│                         │◀────│  ├── Health Check Registry     │◀────│  (read-only)        │
│                         │     │  ├── Bedrock Agent (analysis)  │     │                     │
│                         │     │  └── CloudWatch Logs client    │     └─────────────────────┘
└─────────────────────────┘     └───────────────┬───────────────┘
                                                │
                                    ┌───────────┴───────────┐
                                    │                       │
                              ┌─────▼─────┐         ┌──────▼──────┐
                              │  Secrets   │         │  CloudWatch │
                              │  Manager   │         │  Logs       │
                              └───────────┘         └─────────────┘
```

## Transport

- **Type:** stdio (subprocess)
- **MCP Server:** `awslabs.mysql-mcp-server@latest` (invoked via `uvx`)
- **Protocol:** MCP over stdin/stdout
- **Connection Modes:**
  - **RDS Data API** — Uses `resource_arn` (recommended for Aurora Serverless v2, no VPC needed)
  - **Direct TCP** — Uses `hostname` (for standard Aurora/RDS with VPC connectivity)

## Security Model

| Control | Implementation |
|---------|---------------|
| Read-only enforcement | `--readonly True` flag on MCP server |
| Credential isolation | Credentials stored in Secrets Manager, retrieved via IAM role |
| Query whitelist | Only predefined health check queries in the registry |
| No credential exposure | Credentials never appear in logs, output, or agent responses |
| Execution safety | No DDL, DML, or DCL operations permitted |

## Prerequisites

### 1. Install `uv` (required for `uvx`)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. AWS Resources Required

- **Aurora MySQL or RDS MySQL cluster/instance** with connectivity
- **Secrets Manager Secret** storing database credentials
- **IAM permissions** for the execution environment:
  - `secretsmanager:GetSecretValue` for the DB credential secret
  - `rds-data:ExecuteStatement` (if using RDS Data API mode)
  - `cloudwatch:GetMetricData`, `logs:StartQuery`, `logs:GetQueryResults` (for CloudWatch integration)
  - `bedrock:InvokeModel` (for AI-powered analysis)

### 3. Database User Setup

Create a read-only monitoring user:

**MySQL/Aurora MySQL:**
```sql
CREATE USER 'mysql_aidba_monitor'@'%' IDENTIFIED BY '<password>';
GRANT SELECT ON performance_schema.* TO 'mysql_aidba_monitor'@'%';
GRANT SELECT ON information_schema.* TO 'mysql_aidba_monitor'@'%';
GRANT SELECT ON sys.* TO 'mysql_aidba_monitor'@'%';
GRANT SELECT ON mysql.innodb_index_stats TO 'mysql_aidba_monitor'@'%';
GRANT SELECT ON mysql.ro_replica_status TO 'mysql_aidba_monitor'@'%';  -- Aurora only
GRANT PROCESS ON *.* TO 'mysql_aidba_monitor'@'%';
```

### 4. Secrets Manager Secret Format

```json
{
  "host": "<database-endpoint>",
  "port": 3306,
  "username": "mysql_aidba_monitor",
  "password": "<password>",
  "database": "information_schema",
  "engine": "mysql"
}
```

## Installation & Configuration

### Step 1: Install mysql-aidba

```bash
cd mcp-servers/mysql-aidba
pip install -e .
```

### Step 2: Create Configuration

```bash
cp config/config.yaml.example config/config.yaml
```

Edit `config/config.yaml` with your cluster details:

```yaml
aws:
  region: "us-east-1"
  profile: "default"

bedrock:
  model_id: "anthropic.claude-3-5-sonnet-20241022-v2:0"
  max_tokens: 4096
  temperature: 0.2

clusters:
  primary:
    connection_mode: "rds_data_api"
    resource_arn: "arn:aws:rds:us-east-1:123456789012:cluster:my-cluster"
    secret_arn: "arn:aws:secretsmanager:us-east-1:123456789012:secret:my-secret"
    database: "mydb"
    readonly: true
```

### Step 3: Configure MCP Server

```bash
cp config/mcp_config.json.example config/mcp_config.json
```

Edit with your cluster ARNs:

```json
{
  "mcpServers": {
    "mysql-primary": {
      "command": "uvx",
      "args": [
        "awslabs.mysql-mcp-server@latest",
        "--resource_arn", "arn:aws:rds:us-east-1:123456789012:cluster:my-cluster",
        "--secret_arn", "arn:aws:secretsmanager:us-east-1:123456789012:secret:my-secret",
        "--database", "mydb",
        "--region", "us-east-1",
        "--readonly", "True"
      ],
      "env": {
        "AWS_PROFILE": "default",
        "AWS_REGION": "us-east-1",
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    }
  }
}
```

### Step 4: Run

```bash
rds-aidba --config config/config.yaml --cluster primary
```

## Usage

### Interactive CLI

```bash
# Full health check
rds-aidba> /health

# Category-specific check
rds-aidba> /health connections
rds-aidba> /health performance
rds-aidba> /health storage

# CloudWatch log analysis
rds-aidba> /cloudwatch

# Natural language queries
rds-aidba> Why is my CPU high?
rds-aidba> Show me the slowest queries
rds-aidba> Check for lock contention
```

### Available Health Check Categories

| Category | Description | Queries |
|----------|-------------|---------|
| connections | Connection utilization, thread states | server_information, connection_overview |
| configuration | System variables, buffer pool, InnoDB settings | system_variables |
| activity | Lock detection, long-running queries, active transactions | lock_detection |
| replication | Replica lag, replication thread status | replication_status |
| storage | Database sizes, table sizes, fragmentation | storage_analysis |
| performance | Top queries by time/CPU/IO, index usage | performance_metrics |
| maintenance | Auto-increment capacity, vacuum status | maintenance_health |
| optimization | Redundant indexes, unused indexes | optimization_opportunities |
| summary | Composite health score (50 points) | health_score |

## Integration with DevOps Agent

When using with AWS DevOps Agent:

1. The skill (`skills/database-mysql-devops/SKILL.md`) provides the diagnostic knowledge
2. The MCP server (`mcp-servers/mysql-aidba/`) provides the data-plane execution
3. AWS CLI + CloudWatch provide control-plane and observability data

The skill instructs DevOps Agent to:
- Use AWS CLI for Layer 1 (configuration) checks
- Use CloudWatch for Layer 2 (metrics/logs) checks
- Use rds-aidba MCP for Layer 3 (database-level) queries when available

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| `uvx not found` | `uv` not installed | Install: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `ConnectionRefused` | Security group blocks connection | Add execution environment to DB security group |
| `AccessDenied` | IAM role missing Secrets Manager access | Add `secretsmanager:GetSecretValue` permission |
| `QueryTimeout` | Query exceeded timeout on heavy-load DB | Retry during off-peak hours |
| MCP server not responding | Process crashed or misconfigured | Check `FASTMCP_LOG_LEVEL=DEBUG` for details |

## Fallback Behavior

If the MCP server is unavailable, the skill gracefully degrades:

```
Full Capability (Layer 1 + 2 + 3):
  AWS CLI checks + CloudWatch analysis + Database-level queries
  → Combined 110-point health score

Degraded (Layer 1 + 2 only):
  AWS CLI checks + CloudWatch analysis
  → 60-point AWS-level health score
  → Skill provides SQL queries for manual execution
```
