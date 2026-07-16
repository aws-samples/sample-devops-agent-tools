# mysql-aidba

<div align="center">

**AI-Powered MySQL Database Administration Platform for Amazon Aurora & RDS MySQL**

*Conversational, real-time DBA intelligence powered by Amazon Bedrock*

---

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Amazon Bedrock](https://img.shields.io/badge/Amazon-Bedrock-orange.svg)](https://aws.amazon.com/bedrock/)
[![Aurora MySQL](https://img.shields.io/badge/Amazon-Aurora%20MySQL-blue.svg)](https://aws.amazon.com/rds/aurora/)
[![RDS MySQL](https://img.shields.io/badge/Amazon-RDS%20MySQL-blue.svg)](https://aws.amazon.com/rds/mysql/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

> **Author:** Kiran Mayee Mulupuru  
> **Role:** Sr. Specialist Database TAM, AWS Enterprise Support  
> **Audience:** AWS Internal Teams — Database TAMs, Solutions Architects,
> Enterprise Support Engineers, and customers operating Aurora/RDS MySQL workloads

---

## Table of Contents

1. [Overview](#1-overview)
2. [Key Capabilities](#2-key-capabilities)
3. [Architecture](#3-architecture)
4. [How It Works](#4-how-it-works)
5. [Prerequisites](#5-prerequisites)
6. [Installation](#6-installation)
7. [Configuration](#7-configuration)
8. [IAM Permissions](#8-iam-permissions)
9. [Usage](#9-usage)
10. [Health Check Categories](#10-health-check-categories)
11. [CloudWatch Log Analysis](#11-cloudwatch-log-analysis)
12. [Kiro IDE Integration](#12-kiro-ide-integration)
13. [Deployment — AWS Compute Targets](#13-deployment--aws-compute-targets)
14. [Extending the Tool](#14-extending-the-tool)
15. [Security & Compliance](#15-security--compliance)
16. [Project Structure](#16-project-structure)
17. [Troubleshooting](#17-troubleshooting)
18. [Roadmap](#18-roadmap)

---

## 1. Overview

`mysql-aidba` is a production-grade, conversational AI database administration
platform built specifically for AWS-managed MySQL environments. It bridges the gap
between raw AWS observability data and actionable DBA intelligence by combining:

- **Natural language interaction** — engineers ask questions in plain English;
  the tool determines what to query, runs it live, and returns expert analysis
- **Amazon Bedrock (Claude)** — large language model reasoning grounded in
  live query results and AWS-prescribed best practices
- **AWS Labs MySQL MCP Server** — secure, read-only SQL execution against
  Aurora/RDS clusters via RDS Data API or direct TCP, with no credentials
  stored in application code
- **Curated health check library** — 23 diagnostic SQL queries across 11
  categories, each producing structured, severity-rated findings
- **Environment-aware guidance engine** — automatically detects whether the
  target is Aurora MySQL, RDS MySQL, or self-managed EC2 MySQL, then scopes
  all recommendations to that environment exclusively

The tool is designed for **read-only, zero-impact analysis**. It executes no
DDL, DML, or configuration changes. Every recommendation references the
applicable AWS service (Parameter Groups, RDS Proxy, Performance Insights,
Aurora Fast Cloning, etc.) rather than generic MySQL community guidance.

---

## 2. Key Capabilities

### Conversational DBA Intelligence
Ask questions the way a DBA would ask a colleague. The intent classifier maps
natural language to the correct health check categories and executes only the
relevant queries, minimizing load on the database.

```
You> Why are my queries slow right now?
You> Are there any lock waits or blocking transactions?
You> How is my InnoDB buffer pool performing?
You> What are my top 10 most expensive queries since startup?
You> Is replication lagging and by how much?
You> Show me which indexes are never used
```

### Automatic Environment Detection
On session start, `mysql-aidba` silently probes the cluster and identifies
whether it is Amazon Aurora MySQL, Amazon RDS MySQL, or self-managed MySQL on
EC2. All subsequent responses — recommendations, service names, configuration
references — are scoped exclusively to the detected environment. The tool never
produces conditional "if Aurora / if RDS / if EC2" output.

### Real-Time Activity Analysis
Detects "run it", "show me live", "what is running" intent and immediately
executes `information_schema.PROCESSLIST` and
`performance_schema.events_statements_current` queries, returning live thread
state, query text, blocking relationships, and duration status.

### CloudWatch Logs Integration
Fetches and parses MySQL error logs, slow query logs, and audit logs directly
from Amazon CloudWatch Logs. Analysis includes per-category event counts,
timeline spike detection, rdsadmin query filtering, `long_query_time`
misconfiguration detection, and severity-scored recommendations — without
requiring the user to write CloudWatch Logs Insights queries.

### AWS-Grounded Recommendations
Every AI response is anchored to a structured guidance block sourced from:
- AWS Official Documentation (`docs.aws.amazon.com`)
- AWS Prescriptive Guidance
- Amazon Aurora MySQL Best Practices
- AWS Enterprise Support validated runbooks

The guidance block is injected into every Bedrock prompt with explicit
knowledge-priority instructions, preventing the model from defaulting to
generic community advice or Percona/MariaDB patterns for managed service
environments.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         mysql-aidba Platform                        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  CLI Layer  (cli.py)                                        │    │
│  │  • Click-based interactive REPL                             │    │
│  │  • Rich terminal rendering                                  │    │
│  │  • Non-interactive modes: --health, --category, --cloudwatch│    │
│  └───────────────────────────┬─────────────────────────────────┘    │
│                              │                                      │
│  ┌───────────────────────────▼─────────────────────────────────┐    │
│  │  AI-DBA Agent  (agent.py)                                   │    │
│  │  • Intent classifier (keyword → category mapping)           │    │
│  │  • Environment-aware prompt builder                         │    │
│  │  • Conversation history manager                             │    │
│  │  • CloudWatch fetch coordinator                             │    │
│  │  • Context-aware log detection (session state)              │    │
│  └────────────┬──────────────────────────┬─────────────────────┘    │
│               │                          │                          │
│  ┌────────────▼────────────┐  ┌──────────▼──────────────────-───┐   │
│  │  Health Check Registry  │  │  DBA Guidance Knowledge Base    │   │
│  │  (health_checks/)       │  │  (health_checks/guidance.py)    │   │
│  │  • 23 SQL diagnostics   │  │  • Per-category guidance        │   │
│  │  • 11 categories        │  │  • Environment-scoped           │   │
│  │  • Severity ratings     │  │    (aurora / rds / ec2)         │   │
│  │  • CloudWatch metadata  │  │  • Numeric thresholds           │   │
│  └────────────┬────────────┘  │  • AWS documentation sources    │   │
│               │               └─────────────────────────────────┘   │
│  ┌────────────▼────────────────────────────────────────────────┐    │
│  │  MySQL MCP Client  (mcp_client.py)                          │    │
│  │  • Manages awslabs.mysql-mcp-server subprocess (uvx)        │    │
│  │  • Serialized JSON-RPC over stdio (single-threaded safe)    │    │
│  │  • Sequential batch query execution                         │    │
│  │  • Environment probe (probe_environment())                  │    │
│  │  • CloudWatch Logs client (error / slowquery / audit)       │    │
│  └────────────┬──────────────────────────────────────────-─────┘    │
└───────────────┼─────────────────────────────────────────────────────┘
                │
     ┌──────────▼───────-───┐
     │  AWS Services Layer  │
     ├──────────────────────┤
     │  Amazon Bedrock      │  ◄── Claude (Haiku 4.5 / Sonnet 4)
     │  Amazon RDS Data API │  ◄── Aurora Serverless / Aurora MySQL
     │  Direct TCP (MySQL)  │  ◄── RDS MySQL / Aurora reader endpoint
     │  AWS Secrets Manager │  ◄── DB credentials (never in code)
     │  Amazon CloudWatch   │  ◄── Error / slow query / audit logs
     └──────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|---|---|
| **Serialized MCP RPC** | The `awslabs.mysql-mcp-server` communicates over a single stdio pipe. Concurrent `readuntil()` calls on the same stdout cause race conditions. All send/receive pairs are protected by `asyncio.Lock`. |
| **Sequential batch execution** | `run_health_checks_batch()` runs checks sequentially rather than with `asyncio.gather()` to maintain stdio pipe integrity. |
| **Environment probe at session start** | Cluster type (Aurora/RDS/EC2) is detected once via a lightweight SQL probe, cached as `EnvironmentInfo`, and injected into every Bedrock prompt. This eliminates conditional AI output and ensures all recommendations are environment-specific. |
| **Read-only enforcement** | `readonly: true` is set in both config.yaml and passed as `--readonly True` to the MCP server. The MCP server enforces this at the SQL execution layer. |
| **rdsadmin filtering** | All health check SQL and CloudWatch log parsers exclude `rdsadmin` queries by default — these are AWS-managed internal operations and should never appear as customer issues. |

---

## 4. How It Works

### Session Initialization
1. CLI reads `config/config.yaml` and constructs a `ClusterConfig` object
2. `AIDBAAgent` initializes a Bedrock client (Claude) and an `MySQLMCPClient`
3. `MySQLMCPClient` spawns `awslabs.mysql-mcp-server` as a subprocess via `uvx`
4. MCP JSON-RPC handshake completes; available tools are discovered
5. `probe_environment()` runs a single lightweight SQL query to detect cluster
   type, MySQL version, Aurora version, and key configuration parameters
6. `EnvironmentInfo` is cached and injected into every subsequent prompt

### Per-Request Flow
```
User input
    │
    ├─ /command?          → Built-in command handler
    │
    ├─ "run it" pattern?  → Execute PROCESSLIST + events_statements_current
    │                       → Format results → Bedrock analysis
    │
    ├─ CloudWatch pattern? → Detect log types (error/slowquery/audit)
    │                       → fetch_cloudwatch_logs() from configured log groups
    │                       → analyze_*_logs() parsing and severity scoring
    │                       → Bedrock analysis with log data in prompt
    │
    └─ Natural language    → _classify_intent() → category list
                            → get_checks_by_category() → SQL check list
                            → run_health_checks_batch() → QueryResult list
                            → build_guidance_context() → guidance block
                            → Bedrock prompt assembly (env → guidance → data)
                            → Bedrock invocation → structured response
```

### Prompt Assembly Order
Every Bedrock prompt is assembled in this strict priority order:
1. **`=== CONFIRMED CLUSTER ENVIRONMENT ===`** block (cluster type, version,
   hostname, configuration parameters, guidance scope constraints)
2. **Authority header** — instructs Claude to treat the guidance block as
   primary knowledge source over training data
3. **Per-category guidance block** — recommendations, thresholds, scoring
   rules, and AWS documentation sources for the detected environment only
4. **Live query results** — actual SQL output from the health checks
5. **User question** — plain-English question or analysis request

---

## 5. Prerequisites

### Software Requirements

| Requirement | Minimum Version | Notes |
|---|---|---|
| Python | 3.10 | 3.14 confirmed working |
| `uv` package manager | Latest | Preferred installation method |
| `uvx` | Bundled with `uv` | Required to spawn the MCP server subprocess |
| AWS CLI | 2.x | For credential validation and profile management |

### AWS Service Requirements

| Service | Requirement | Notes |
|---|---|---|
| Amazon Bedrock | Model access enabled | Enable Claude Haiku 4.5 or Sonnet 4 in the Bedrock console under **Model access** |
| Aurora MySQL / RDS MySQL | MySQL 5.7 or 8.0 | Aurora MySQL 2.x (MySQL 5.7-compat) or 3.x (MySQL 8.0-compat); RDS MySQL 5.7 or 8.0 |
| AWS Secrets Manager | DB secret with `username` and `password` keys | RDS-managed secrets (`rds!cluster-*`) are supported |
| Amazon CloudWatch Logs | Log export enabled on cluster/instance | Required for `/cloudwatch` analysis; error, slowquery, and audit log export must be enabled in the RDS/Aurora console |
| RDS Data API | Enabled on Aurora cluster | Required for `resource_arn` connection mode; not required for direct TCP (`hostname`) mode |

### Network Requirements

| Mode | Network Requirement |
|---|---|
| RDS Data API (`resource_arn`) | HTTPS to `rds-data.REGION.amazonaws.com` — works from any network with AWS API access |
| Direct TCP (`hostname`) | TCP port 3306 to the RDS/Aurora endpoint — must be within the same VPC, or VPC peering / VPN / Direct Connect must be in place |
| Bedrock | HTTPS to `bedrock-runtime.REGION.amazonaws.com` |
| CloudWatch Logs | HTTPS to `logs.REGION.amazonaws.com` |

### Database User Permissions

The database user referenced in the Secrets Manager secret requires the
following MySQL privileges for read-only analysis:

```sql
GRANT SELECT, PROCESS, REPLICATION CLIENT, SHOW DATABASES
  ON *.* TO 'aidba_user'@'%';
GRANT SELECT ON performance_schema.* TO 'aidba_user'@'%';
GRANT SELECT ON information_schema.* TO 'aidba_user'@'%';
```

> **Note:** `PROCESS` is required to view all threads in
> `information_schema.PROCESSLIST`. Without it, the tool only sees threads
> belonging to the connected user.

---

## 6. Installation

### Option A — uv (Recommended)

`uv` creates a fully isolated virtual environment and installs all dependencies
from the locked dependency graph in `uv.lock`. This is the preferred method for
all deployments.

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # or ~/.zshrc

# Clone the repository
git clone <internal-repo-url> /opt/mysql-aidba
cd /opt/mysql-aidba

# Install all dependencies into an isolated .venv
uv sync
```

Verify the installation:

```bash
uv run mysql-aidba --help
```

Expected output:
```
Usage: mysql-aidba [OPTIONS]

  mysql-aidba — AI-Powered MySQL DBA for Amazon Aurora & RDS MySQL.

  Ask questions in plain English and get AI-driven DBA insights powered
  by Amazon Bedrock and the AWS Labs MySQL MCP Server.

Options:
  -c, --cluster TEXT    Cluster name from config.yaml [default: primary]
  -f, --config TEXT     Path to config.yaml [default: config/config.yaml]
  -p, --profile TEXT    AWS CLI profile override
  -r, --region TEXT     AWS region override
  -m, --model TEXT      Bedrock model ID override
      --health          Run full health check and exit
      --category TEXT   Run single category check and exit
      --cloudwatch-logs Analyze CloudWatch logs and exit
      --hours-back INT  Hours of CloudWatch logs to retrieve [default: 1]
  -v, --verbose         Enable debug logging
      --help            Show this message and exit.
```

### Option B — pip (Fallback)

Use this method only if `uv` is not available in your environment.

```bash
cd /opt/mysql-aidba
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Verify:
```bash
mysql-aidba --help
```

### Verify Core Dependencies

```bash
# Confirm uvx is available (required for MCP server subprocess)
which uvx

# Confirm boto3 and bedrock client are importable
uv run python -c "import boto3; print('boto3:', boto3.__version__)"

# Confirm all internal modules load correctly
uv run python -c "
from mysql_aidba.health_checks import ALL_HEALTH_CHECKS, CATEGORIES
from mysql_aidba.mcp_client import ClusterConfig, MySQLMCPClient
from mysql_aidba.agent import AIDBAAgent
print(f'Health checks : {len(ALL_HEALTH_CHECKS)}')
print(f'Categories    : {len(CATEGORIES)}')
print('All imports   : OK')
"
```

---

## 7. Configuration

### 7.1 Primary Configuration — `config/config.yaml`

The main configuration file controls AWS connectivity, Bedrock model selection,
cluster definitions, CloudWatch integration, and CLI behavior.

```bash
cp config/config.yaml.example config/config.yaml
```

**Full annotated configuration reference:**

```yaml
# ============================================================================
# AWS CONFIGURATION
# ============================================================================
aws:
  region: "us-east-1"     # AWS region where your clusters reside
  profile: "default"       # AWS CLI named profile
                            # Omit (or use "default") when running on EC2
                            # with an IAM instance role

# ============================================================================
# BEDROCK AI MODEL CONFIGURATION
# ============================================================================
bedrock:
  # Recommended: Claude Haiku 4.5 (fast, cost-effective for DBA queries)
  model_id: "us.anthropic.claude-haiku-4-5-20251001-v1:0"

  # Alternatives:
  # model_id: "us.anthropic.claude-sonnet-4-20250514-v1:0"    # Higher quality
  # model_id: "anthropic.claude-3-5-sonnet-20241022-v2:0"     # Claude 3.5 Sonnet v2
  # model_id: "anthropic.claude-3-haiku-20240307-v1:0"        # Claude 3 Haiku

  max_tokens: 4096
  temperature: 0.2   # Low temperature for deterministic DBA analysis

# ============================================================================
# CLOUDWATCH LOGS CONFIGURATION
# ============================================================================
cloudwatch:
  enabled: true
  log_types: ["error", "slowquery", "audit"]
  default_hours_back: 1    # Default lookback window
  max_hours_back: 24       # Maximum allowed lookback (cap for cost control)

# ============================================================================
# CLUSTER DEFINITIONS
# ============================================================================
clusters:

  # ------------------------------------------------------------------
  # Aurora Writer Endpoint — RDS Data API mode
  # Use this mode when RDS Data API is enabled on the Aurora cluster.
  # Recommended for Aurora Serverless and most Aurora MySQL clusters.
  # ------------------------------------------------------------------
  primary:
    resource_arn: "arn:aws:rds:us-east-1:ACCOUNT_ID:cluster:MY-CLUSTER-NAME"
    secret_arn:   "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:MY-SECRET-NAME"
    database:     "mydatabase"
    region:       "us-east-1"
    readonly:     true

    # CloudWatch log groups for this cluster (enable log export in RDS console)
    cloudwatch_log_group:            "/aws/rds/cluster/MY-CLUSTER-NAME/error"
    cloudwatch_slow_query_log_group: "/aws/rds/cluster/MY-CLUSTER-NAME/slowquery"
    cloudwatch_audit_log_group:      "/aws/rds/cluster/MY-CLUSTER-NAME/audit"

  # ------------------------------------------------------------------
  # Aurora Reader Endpoint — Direct TCP mode
  # Use this mode when RDS Data API is not available, or for direct
  # TCP connections to the Aurora reader/RDS read replica endpoint.
  # Requires VPC/network access to port 3306.
  # ------------------------------------------------------------------
  replica:
    hostname:   "my-cluster.cluster-ro-xxxx.us-east-1.rds.amazonaws.com"
    port:       3306
    secret_arn: "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:MY-SECRET-NAME"
    database:   "mydatabase"
    region:     "us-east-1"
    readonly:   true

    cloudwatch_log_group:            "/aws/rds/cluster/MY-CLUSTER-NAME/error"
    cloudwatch_slow_query_log_group: "/aws/rds/cluster/MY-CLUSTER-NAME/slowquery"
    cloudwatch_audit_log_group:      "/aws/rds/cluster/MY-CLUSTER-NAME/audit"

# ============================================================================
# HEALTH CHECK THRESHOLDS
# ============================================================================
health_checks:
  slow_query_threshold_seconds:      1     # Queries exceeding this are flagged
  connection_usage_warn_pct:         75    # Warn at 75% of max_connections
  replication_lag_warn_seconds:      30    # Aurora: milliseconds; RDS: seconds
  innodb_buffer_pool_usage_warn_pct: 90    # Warn when buffer pool is >90% full

# ============================================================================
# CLI SETTINGS
# ============================================================================
cli:
  output_format: "rich"         # Options: rich | plain | json
  history_file: "~/.mysql_aidba_history"
  max_history: 500
  banner: true
```

### 7.2 Connection Modes Comparison

| Mode | Parameter | When to Use |
|---|---|---|
| **RDS Data API** | `resource_arn` | Aurora MySQL clusters with Data API enabled; no VPC network access required; supports Aurora Serverless |
| **Direct TCP** | `hostname` + `port` | RDS MySQL instances; Aurora without Data API; reader endpoints; any MySQL reachable on port 3306 |

> Both modes retrieve database credentials exclusively from AWS Secrets Manager
> at runtime. Credentials are never stored in configuration files or code.

### 7.3 Enabling CloudWatch Log Export

Before using the `/cloudwatch` command, ensure log export is enabled on the
cluster in the AWS Console:

1. Navigate to **Amazon RDS → Databases → [your cluster] → Modify**
2. Under **Log exports**, enable: **Error log**, **Slow query log**, **Audit log**
3. Click **Continue** → **Apply immediately**

Log groups are created automatically in CloudWatch Logs:
```
/aws/rds/cluster/{cluster-name}/error
/aws/rds/cluster/{cluster-name}/slowquery
/aws/rds/cluster/{cluster-name}/audit
```

For Aurora MySQL audit logging, also enable the Aurora MySQL audit plugin via the
Aurora Cluster Parameter Group:
```
server_audit_logging          = 1
server_audit_events           = CONNECT,QUERY_DDL,QUERY_DML
server_audit_excl_users       = rdsadmin
```

### 7.4 MCP Server Configuration — `config/mcp_config.json`

The MCP configuration file is used for direct Kiro IDE integration and
for any MCP-compatible host that manages the `awslabs.mysql-mcp-server`
subprocess independently.

```bash
cp config/mcp_config.json.example config/mcp_config.json
```

```json
{
  "mcpServers": {
    "mysql-primary": {
      "command": "uvx",
      "args": [
        "awslabs.mysql-mcp-server@latest",
        "--resource_arn", "arn:aws:rds:us-east-1:ACCOUNT_ID:cluster:MY-CLUSTER",
        "--secret_arn",   "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:MY-SECRET",
        "--database",     "mydatabase",
        "--region",       "us-east-1",
        "--readonly",     "True"
      ],
      "env": {
        "AWS_PROFILE": "default",
        "AWS_REGION":  "us-east-1",
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    },
    "mysql-replica": {
      "command": "uvx",
      "args": [
        "awslabs.mysql-mcp-server@latest",
        "--hostname",   "my-cluster.cluster-ro-xxxx.us-east-1.rds.amazonaws.com",
        "--secret_arn", "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:MY-SECRET",
        "--database",   "mydatabase",
        "--region",     "us-east-1",
        "--port",       "3306",
        "--readonly",   "True"
      ],
      "env": {
        "AWS_PROFILE": "default",
        "AWS_REGION":  "us-east-1",
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    }
  }
}
```

> **Security:** Both `config/config.yaml` and `config/mcp_config.json` are
> listed in `.gitignore`. Only `*.example` template files are committed to
> version control. Real ARNs, account IDs, and cluster identifiers must never
> be committed to any repository.

---

## 8. IAM Permissions

### 8.1 Minimum Required IAM Policy

Attach the following policy to the IAM role or user running `mysql-aidba`.
Replace placeholder values with your actual account ID, region, cluster ARN,
and secret ARN.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockModelInvocation",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "arn:aws:bedrock:us-east-1::foundation-model/us.anthropic.claude-sonnet-4-20250514-v1:0"
      ]
    },
    {
      "Sid": "RDSDataAPIReadOnly",
      "Effect": "Allow",
      "Action": "rds-data:ExecuteStatement",
      "Resource": "arn:aws:rds:us-east-1:ACCOUNT_ID:cluster:MY-CLUSTER-NAME"
    },
    {
      "Sid": "SecretsManagerReadOnly",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:MY-SECRET-NAME-*"
    },
    {
      "Sid": "CloudWatchLogsReadOnly",
      "Effect": "Allow",
      "Action": [
        "logs:FilterLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
        "logs:GetLogEvents"
      ],
      "Resource": [
        "arn:aws:logs:us-east-1:ACCOUNT_ID:log-group:/aws/rds/cluster/MY-CLUSTER-NAME:*",
        "arn:aws:logs:us-east-1:ACCOUNT_ID:log-group:/aws/rds/cluster/MY-CLUSTER-NAME/*"
      ]
    }
  ]
}
```

> **Note on Direct TCP mode:** If using `hostname` (direct TCP) instead of
> `resource_arn` (RDS Data API), the `rds-data:ExecuteStatement` statement
> is not required. SQL execution is handled by the MCP server directly using
> credentials retrieved from Secrets Manager.

### 8.2 Credential Validation

Run these commands before the first session to confirm all permissions are
correctly configured:

```bash
# 1. Confirm caller identity
aws sts get-caller-identity

# 2. Confirm Bedrock model access
aws bedrock list-foundation-models \
  --region us-east-1 \
  --query "modelSummaries[?contains(modelId,'claude')].{id:modelId,state:modelLifecycle.status}" \
  --output table

# 3. Confirm Secrets Manager access
aws secretsmanager get-secret-value \
  --secret-id "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:MY-SECRET" \
  --region us-east-1 \
  --query "SecretString" \
  --output text

# 4. Confirm CloudWatch Logs access
aws logs describe-log-groups \
  --log-group-name-prefix "/aws/rds/cluster/MY-CLUSTER-NAME" \
  --region us-east-1 \
  --query "logGroups[].logGroupName"
```

---

## 9. Usage

### 9.1 Full Health Check (Non-Interactive)

Executes all 23 diagnostic SQL checks across all 10 SQL-based categories and
returns a complete, severity-scored DBA assessment. Recommended for initial
triage and scheduled health reporting.

```bash
uv run mysql-aidba --cluster primary --health
```

Output includes `[CRITICAL]`, `[WARNING]`, and `[INFO]` classified findings,
root cause analysis, and prioritized recommendations scoped to the detected
environment.

### 9.2 Category-Specific Check (Non-Interactive)

Executes all checks within a single category for focused analysis.

```bash
uv run mysql-aidba --cluster primary --category connections
uv run mysql-aidba --cluster primary --category performance
uv run mysql-aidba --cluster primary --category replication
uv run mysql-aidba --cluster primary --category innodb
uv run mysql-aidba --cluster primary --category storage
uv run mysql-aidba --cluster primary --category activity
uv run mysql-aidba --cluster primary --category maintenance
uv run mysql-aidba --cluster primary --category optimization
uv run mysql-aidba --cluster primary --category configuration
uv run mysql-aidba --cluster primary --category summary
```

### 9.3 CloudWatch Log Analysis (Non-Interactive)

Fetches and analyzes error, slow query, and audit logs from CloudWatch Logs.
The `--hours-back` parameter controls the analysis window (1–24 hours).

```bash
# Analyze last 1 hour (default)
uv run mysql-aidba --cluster primary --cloudwatch-logs

# Analyze last 6 hours
uv run mysql-aidba --cluster primary --cloudwatch-logs --hours-back 6

# Analyze last 24 hours
uv run mysql-aidba --cluster primary --cloudwatch-logs --hours-back 24
```

### 9.4 Interactive REPL

The primary interface for conversational DBA analysis. Start a session and
ask questions in plain English.

```bash
uv run mysql-aidba --cluster primary
```

#### Built-In Commands

| Command | Description |
|---|---|
| `/health` | Run all health checks and return a full scored assessment |
| `/health <category>` | Run health checks for a specific category |
| `/cloudwatch` | Analyze CloudWatch logs for the last 1 hour |
| `/cloudwatch <hours>` | Analyze CloudWatch logs for the specified number of hours (1–24) |
| `/categories` | List all available health check categories |
| `/reset` | Clear conversation history and session context |
| `/help` | Display the help banner |
| `/quit` or `exit` | Exit the tool |

#### Plain-English Query Examples

**Real-time activity analysis:**
```
You> What queries are running right now?
You> Are there any lock waits or blocked transactions?
You> What is causing high CPU on the database?
You> Show me all long-running queries
You> Is there anything hanging or stuck?
```

**Performance analysis:**
```
You> What are my slowest queries in the last 24 hours?
You> Which queries are doing full table scans?
You> Show me the top 10 queries by total execution time
You> What is the average query latency?
You> Are there queries with high lock time?
```

**Capacity and health analysis:**
```
You> How is my InnoDB buffer pool performing?
You> What is my connection utilization?
You> Show me the largest tables in the database
You> Is there table fragmentation I should address?
You> How is replication lag looking?
```

**CloudWatch log analysis:**
```
You> Show me the latest error logs
You> Are there any slow queries in CloudWatch?
You> Fetch and analyze the audit logs
You> What errors occurred in the last hour?
You> Check the slow query logs and identify the top offenders
```

### 9.5 Override Options

```bash
# Use a different AWS CLI profile
uv run mysql-aidba --cluster primary --profile prod-sso-role

# Use a different region
uv run mysql-aidba --cluster primary --region eu-west-1

# Override the Bedrock model
uv run mysql-aidba --cluster primary \
  --model "us.anthropic.claude-sonnet-4-20250514-v1:0"

# Target a replica cluster
uv run mysql-aidba --cluster replica --health

# Enable debug logging (shows MCP JSON-RPC traffic)
uv run mysql-aidba --cluster primary --verbose

# Alternate module entry point
uv run python -m mysql_aidba --cluster primary --health
```

---

## 10. Health Check Categories

The tool maintains 23 diagnostic SQL checks across 11 categories. Each check
is a structured Python dict containing a category, severity rating
(`baseline`, `warning`, `critical`), minimum MySQL version, and the full SQL
query.

| Category | Checks | Description |
|---|---|---|
| `connections` | 2 | Connection utilization %, thread counts (running/sleeping/waiting), max_connections proximity |
| `configuration` | 1 | Key system variables: performance_schema, InnoDB engine settings, slow query logging configuration |
| `activity` | 3 | Active lock detection, full PROCESSLIST analysis, InnoDB row-level lock waits with blocking query details |
| `replication` | 1 | Binary logging status, GTID mode, server ID, read_only flag, writer vs. replica role detection |
| `storage` | 2 | Per-schema data/index sizes, top 20 largest tables with fragmentation percentage |
| `innodb` | 1 | Buffer pool size, hit ratio, dirty/free page counts |
| `performance` | 1 | Top 50 queries by total execution time from `performance_schema.events_statements_summary_by_digest`, with full SQL, optimization priority, and issue classification |
| `maintenance` | 1 | Table fragmentation percentage and free space for tables > 1 MB |
| `optimization` | 1 | Index usage analysis from `table_io_waits_summary_by_index_usage` — unused/low-use indexes with DROP recommendations |
| `summary` | 1 | Consolidated health scorecard: uptime, connection utilization, buffer pool hit ratio |
| `cloudwatch` | 3 | CloudWatch error logs, slow query logs, and audit logs (metadata only — fetched via CloudWatch Logs API, not SQL) |

### Severity Levels

| Level | Meaning |
|---|---|
| `critical` | Potential data integrity, availability, or severe performance impact. Immediate attention recommended. |
| `warning` | Performance degradation or resource utilization threshold approaching. Review within the current operational cycle. |
| `baseline` | Informational — establishes current state for trend analysis. No immediate action required. |

---

## 11. CloudWatch Log Analysis

### Log Types and Parser Behavior

#### Error Log Analysis
Categorizes log events into the following buckets with per-category thresholds:

| Category | Warning Threshold | Critical Threshold |
|---|---|---|
| Connection errors (aborted connections, Too many connections) | > 5 | > 20 |
| Access denied / authentication failures | > 5 | > 20 |
| InnoDB errors | — | ≥ 1 (any occurrence) |
| Deadlocks / lock wait timeouts | > 2 | > 10 |
| Replication errors | — | ≥ 1 (any occurrence) |
| Table errors | ≥ 1 | — |

Includes timeline bucketing (10-minute intervals) for spike detection.

#### Slow Query Log Analysis
- Parses `Query_time`, `Lock_time`, `Rows_sent`, `Rows_examined`, and SQL
  text from raw CloudWatch log events
- **Filters rdsadmin queries** — all statistics are computed on customer
  application queries only
- Reports customer vs. rdsadmin split clearly
- Detects `long_query_time` set at or near 0 (symptom: hundreds of rdsadmin
  heartbeat queries appearing as "slow")
- Classifies each query into performance bands:
  `CRITICAL (>10s)`, `SLOW (1-10s)`, `MEDIUM (0.1-1s)`, `FAST (<0.1s)`
- Returns top 10 customer queries by execution time with efficiency ratios

#### Audit Log Analysis
Security-focused categorization:

| Category | Risk Level |
|---|---|
| Failed logins > 10 | Potential brute force — review Security Groups |
| Failed logins > 50 | CRITICAL brute force — immediate response required |
| Suspicious queries (DROP DATABASE, LOAD DATA INFILE, etc.) | Data integrity risk |
| Privilege changes (GRANT/REVOKE) | Access control review required |
| Schema changes (DDL) | Change management verification |

### Context-Aware Log Detection

The agent maintains conversation context. If a user confirms that logs are
enabled or publishing ("the slow query logs are already enabled"), subsequent
messages mentioning logs automatically trigger a CloudWatch fetch without
requiring explicit commands or `/cloudwatch` syntax.

---

## 12. Kiro IDE Integration

`mysql-aidba` includes full support for the Kiro IDE MCP host. Kiro manages
the `awslabs.mysql-mcp-server` subprocess lifecycle and exposes a
`run_query` tool to Kiro's agent.

### Configuration

Point Kiro at the `mcp_config.json` file:

```bash
kiro --mcp-config /opt/mysql-aidba/config/mcp_config.json
```

Alternatively, merge the `mcpServers` block from `config/mcp_config.json`
into your existing Kiro MCP configuration (`~/.kiro/mcp.json`):

```json
{
  "mcpServers": {
    "mysql-primary": { ... },
    "mysql-replica": { ... }
  }
}
```

Kiro will automatically start the MCP server subprocess on connection and
expose the `run_query` tool. Both the `mysql-primary` and `mysql-replica`
servers can be active simultaneously for cross-cluster analysis.

---

## 13. Deployment — AWS Compute Targets

### EC2 Instance

```bash
# 1. Transfer project files from local machine
rsync -av \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.egg-info' \
  --exclude='.git' \
  /local/path/mysql-aidba/ \
  ec2-user@EC2-PUBLIC-IP:/opt/mysql-aidba/

# 2. Install uv on the EC2 instance
ssh ec2-user@EC2-PUBLIC-IP
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# 3. Install dependencies
cd /opt/mysql-aidba
uv sync

# 4. Configure
cp config/config.yaml.example config/config.yaml
# Edit config/config.yaml with real ARNs and cluster details

# 5. Verify
uv run mysql-aidba --help
aws sts get-caller-identity   # Confirm IAM role is attached to the instance
```

**IAM Instance Role:** If the EC2 instance has an IAM instance role with the
required permissions, no `profile` configuration is needed. Remove the
`profile` key from `config.yaml` or set it to `default` — boto3 automatically
retrieves credentials from the instance metadata service (IMDS).

### AWS Cloud9 / CloudShell

```bash
# uv install
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# Clone and install
git clone <repo-url> ~/mysql-aidba
cd ~/mysql-aidba
uv sync

# Configure and run
cp config/config.yaml.example config/config.yaml
uv run mysql-aidba --cluster primary --health
```

### Scheduled Execution via Cron or EventBridge

For automated health reporting, run non-interactive mode on a schedule:

```bash
# /etc/cron.d/mysql-aidba-health
0 */6 * * * ec2-user cd /opt/mysql-aidba && \
  uv run mysql-aidba --cluster primary --health \
  >> /var/log/mysql-aidba/health-$(date +\%Y\%m\%d-\%H\%M).log 2>&1
```

---

## 14. Extending the Tool

### Adding a New Health Check

Health checks are defined as Python dicts in
`mysql_aidba/health_checks/__init__.py`. To add a new check:

1. **Define the check dict:**

```python
{
    "name":        "my_new_check",           # Unique identifier
    "description": "Human-readable description for CLI display",
    "category":    "performance",            # Must match a key in CATEGORIES
    "severity":    "warning",               # baseline | warning | critical
    "aurora_only": False,                   # True if Aurora-specific SQL
    "min_version": "5.7",                   # Minimum MySQL version required
    "sql": """
        SELECT ... ;
    """,
}
```

2. **Add to the appropriate category list** (e.g., `PERFORMANCE`, `CONNECTIONS`).
   The check is automatically included in `ALL_HEALTH_CHECKS`, the CLI, and
   the agent.

3. **Add guidance context** in `mysql_aidba/health_checks/guidance.py` under
   the matching category key. Provide environment-specific recommendations
   for `aurora`, `rds`, and `ec2` sub-keys.

### Adding a New Category

1. Define a new list in `health_checks/__init__.py`:
```python
MY_NEW_CATEGORY: List[Dict[str, Any]] = [ ... ]
```

2. Add to `ALL_HEALTH_CHECKS`, `CATEGORIES`, and `CATEGORY_DESCRIPTIONS`
3. Add intent keywords in `agent.py` under `INTENT_KEYWORDS`
4. Add guidance in `guidance.py` under `_GUIDANCE`

### Supported Bedrock Models

The tool is tested with the following Bedrock models. Specify via `--model`
or in `config.yaml`:

| Model ID | Performance | Cost | Notes |
|---|---|---|---|
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Fast | Lowest | Default; recommended for interactive sessions |
| `us.anthropic.claude-sonnet-4-20250514-v1:0` | High quality | Medium | Recommended for full health checks |
| `anthropic.claude-3-5-sonnet-20241022-v2:0` | High quality | Medium | Claude 3.5 Sonnet v2 |
| `anthropic.claude-3-haiku-20240307-v1:0` | Fast | Lowest | Claude 3 Haiku baseline |

---

## 15. Security & Compliance

### Credential Handling

| Control | Implementation |
|---|---|
| **No credentials in code** | All database credentials are retrieved from AWS Secrets Manager by the MCP server at runtime via `secretsmanager:GetSecretValue` |
| **No credentials in config files** | `config.yaml` contains only ARNs (resource identifiers), not usernames or passwords |
| **No credentials in git** | `config/config.yaml` and `config/mcp_config.json` are `.gitignore`-listed; only `.example` templates are committed |
| **Secrets Manager integration** | Supports both RDS-managed secrets (`rds!cluster-*`) and customer-managed secrets |

### Read-Only Enforcement

| Layer | Enforcement Mechanism |
|---|---|
| **Configuration** | `readonly: true` set per cluster in `config.yaml` |
| **MCP Server** | `--readonly True` passed as a CLI argument to `awslabs.mysql-mcp-server`; the server enforces read-only at the SQL execution layer |
| **Health check SQL** | All 23 health check queries are `SELECT` statements only; no DDL or DML |
| **Agent design** | No code path in `agent.py` generates or executes write SQL |

### Data Flow Security

```
mysql-aidba CLI
      │
      │  Credentials: Never stored. MCP server fetches from Secrets Manager.
      │
      ▼
awslabs.mysql-mcp-server (subprocess)
      │
      │  Transport: stdio JSON-RPC (local, no network exposure)
      │
      ├──► RDS Data API (HTTPS/TLS 1.2+, IAM-authenticated)
      └──► Direct TCP  (TLS to RDS/Aurora endpoint, password from Secrets Manager)
```

### IAM Least Privilege

The minimum IAM policy in [Section 8](#8-iam-permissions) follows least
privilege principles:
- Bedrock: scoped to specific model ARNs only
- RDS Data API: scoped to the specific cluster ARN
- Secrets Manager: scoped to the specific secret ARN
- CloudWatch Logs: scoped to the specific RDS log group prefix

### Audit Trail

All SQL executed against the database is routed through the
`awslabs.mysql-mcp-server` subprocess. If Aurora Audit Logging is enabled on
the cluster, all `mysql-aidba` queries are recorded in the audit log with the
IAM-authenticated database user.

---

## 16. Project Structure

```
mysql-aidba/
│
├── config/
│   ├── config.yaml                 # Active configuration (gitignored)
│   ├── config.yaml.example         # Template — committed to version control
│   ├── mcp_config.json             # Active MCP/Kiro config (gitignored)
│   └── mcp_config.json.example     # Template — committed to version control
│
├── mysql_aidba/
│   ├── __init__.py                 # Package version and metadata
│   ├── __main__.py                 # python -m mysql_aidba entry point
│   ├── agent.py                    # AI-DBA agent: intent classification,
│   │                               #   prompt assembly, Bedrock invocation,
│   │                               #   CloudWatch coordination,
│   │                               #   conversation history management
│   ├── cli.py                      # Click CLI, interactive REPL,
│   │                               #   Rich terminal rendering,
│   │                               #   non-interactive mode handlers
│   ├── mcp_client.py               # MySQL MCP Server client:
│   │                               #   subprocess lifecycle management,
│   │                               #   serialized JSON-RPC,
│   │                               #   environment probe,
│   │                               #   CloudWatch log fetching and parsing,
│   │                               #   QueryResult and EnvironmentInfo dataclasses
│   ├── utils/
│   │   └── __init__.py             # Shared utility functions
│   └── health_checks/
│       ├── __init__.py             # 23 health check SQL queries across
│       │                           #   11 categories; registry helpers
│       └── guidance.py             # DBA guidance knowledge base:
│                                   #   per-category, per-environment
│                                   #   recommendations, thresholds,
│                                   #   scoring rules, and documentation sources
│
├── scripts/                        # Utility SQL scripts for testing and
│   ├── create_schema.sql           #   load generation (not used in production)
│   ├── create_bad_queries.sql
│   ├── create_bloat.sql
│   ├── generate_data.sql
│   ├── generate_load.sh
│   ├── add_indexes.sql
│   └── diagnose_cluster.sh
│
├── pyproject.toml                  # Package metadata and build configuration
├── requirements.txt                # Pinned dependencies for pip-based installs
├── uv.lock                         # Locked dependency graph for uv
├── .gitignore
└── README.md
```

---

## 17. Troubleshooting

### Common Issues and Resolutions

| Symptom | Likely Cause | Resolution |
|---|---|---|
| `ModuleNotFoundError: No module named 'boto3'` | Running `mysql-aidba` directly instead of via `uv run` | Always use `uv run mysql-aidba` to ensure the project's isolated `.venv` is used |
| `VIRTUAL_ENV ... does not match` warning at startup | A different virtual environment is activated in the current shell | Safe to ignore — `uv run` uses the project's own `.venv`. Run `deactivate` to suppress the warning. |
| `run_query tool not found in MCP server` | `uvx` not in PATH or MCP server failed to start | Run `which uvx`; if not found, re-run the `uv` install script and reload your shell |
| `AccessDeniedException` on Bedrock | Model not enabled or IAM permission missing | Enable the model in **AWS Console → Bedrock → Model access**; add `bedrock:InvokeModel` to the IAM policy |
| `AccessDeniedException` on RDS Data API | Missing `rds-data:ExecuteStatement` permission | Add the permission scoped to the cluster ARN; verify the cluster has RDS Data API enabled |
| `ResourceNotFoundException` on Secrets Manager | Incorrect `secret_arn` in config.yaml | Verify the ARN in the Secrets Manager console; ensure the secret exists in the same region |
| Empty query results from health checks | RDS Data API not enabled on Aurora cluster | Enable RDS Data API: **RDS Console → Clusters → [cluster] → Modify → Enable RDS Data API** |
| CloudWatch log fetch returns 0 events | Log export not enabled, wrong log group ARN, or no events in time window | Verify log export in RDS console; confirm log group names in config.yaml match CloudWatch; try `--hours-back 24` |
| `long_query_time` too-low detection warning | `long_query_time` set to 0 or near-0, capturing all rdsadmin heartbeat queries | Set `long_query_time = 1` in the Aurora Cluster Parameter Group |
| AI response contains "if Aurora / if RDS" conditional language | Environment probe failed silently | Run with `--verbose` to inspect the probe query response; verify the database user has `SELECT` on `@@aurora_version` |
| MCP server subprocess hangs on connect | Network connectivity issue to RDS endpoint (direct TCP mode) | Verify Security Group allows port 3306 from the tool's source IP; confirm VPC routing |

### Debug Mode

Enable verbose logging to inspect the full MCP JSON-RPC traffic, SQL queries
sent, raw responses, and CloudWatch API calls:

```bash
uv run mysql-aidba --cluster primary --health --verbose
```

Debug output includes:
- MCP server startup command and PID
- JSON-RPC request/response payloads
- Environment probe result
- CloudWatch Logs API call parameters and response counts
- Bedrock model invocation token counts

---

## 18. Roadmap

The following capabilities are under active development or planned for future
releases:

| Feature | Description | Priority |
|---|---|---|
| **Performance Insights Integration** | Direct API integration with Amazon RDS Performance Insights for `db.wait_event` analysis and `top_sql` by DB load | High |
| **Aurora Global Database Support** | Cross-region lag monitoring and failover readiness assessment for Aurora Global Database clusters | High |
| **RDS Enhanced Monitoring Integration** | OS-level metrics (CPU steal, memory usage per process) via Enhanced Monitoring API | Medium |
| **Scheduled Health Reports** | Built-in scheduler for automated hourly/daily health check runs with email/SNS delivery | Medium |
| **Multi-Cluster Comparison** | Side-by-side health comparison across primary and replica clusters, or across environments | Medium |
| **Query Plan Analysis** | Automated `EXPLAIN` execution and plan quality assessment for identified slow queries | Medium |
| **Slack / Amazon Chime Integration** | Post health check summaries and critical alerts to Slack channels or Amazon Chime webhooks | Low |
| **Historical Trend Analysis** | Persist health check results to Amazon DynamoDB for trend visualization and regression detection | Low |

---

## Support

This tool is maintained by the AWS Enterprise Support Database TAM team.

For internal AWS teams:
- Raise issues or feature requests via the internal repository issue tracker
- For architecture discussions, contact the database TAM team via internal Slack

For customers using this tool under Enterprise Support guidance:
- Engage your assigned TAM for configuration assistance and customization
- Critical issues should be escalated via your existing Enterprise Support channels

---

## License

This tool is intended for use by AWS internal teams and AWS Enterprise Support
customers under guidance from an assigned Technical Account Manager.

See [LICENSE](LICENSE) for full terms.

---

*mysql-aidba — Built by AWS Enterprise Support to give engineering teams
AI-driven visibility into their Aurora and RDS MySQL databases.*
