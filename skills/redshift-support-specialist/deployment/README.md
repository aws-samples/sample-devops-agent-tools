# Redshift MCP Server — Serverless Deployment (Lambda, no ECR)

Reference material for deploying the Redshift MCP server. For the step-by-step setup flow (which this supports), see the main skill README's [Step 1 — MCP Server Deployment](../README.md#step-1--mcp-server-deployment).

## Files in this directory

| File | Purpose |
|---|---|
| [`sam-app/`](sam-app/) | Option A — AWS SAM application (recommended deploy path) |
| `build_zip.sh`, `deploy.sh` | Option B — plain AWS CLI + shell script deploy path |
| `deployer-permissions-policy.json` | IAM policy for the credentials used to *deploy* this stack |
| `redshift-access-policy.json` | IAM policy attached to the Lambda's own execution role (Redshift Data API read access) |
| `lambda-trust-policy.json` | Trust policy for the Lambda execution role |
| `scripts/mcp_call.py`, `scripts/list_clusters.py` | SigV4 test helpers for calling the deployed endpoint directly |
| `scripts/mcp_initialize_test.py` | Basic MCP `initialize` handshake test |

## Why this approach

This runs the **standard, unmodified** `awslabs.redshift-mcp-server@latest` PyPI package on AWS Lambda, fronted by an **API Gateway REST API** secured with AWS IAM (SigV4) authorization.

API Gateway sits in front of the Lambda function and exposes a single `/mcp` endpoint. Every request must be signed with AWS SigV4 for the `execute-api` service, from a principal that's been explicitly granted `execute-api:Invoke` on that endpoint (see **Grant invoke access to a caller** below). API Gateway validates the signature and the caller's IAM permissions before the request ever reaches the Lambda function, then forwards it into the Lambda execution environment where `mcp-proxy` bridges the HTTP request to the underlying MCP server process. This is the endpoint you register with AWS DevOps Agent.

No EC2 instance, no load balancer, no VPC networking, and no container registry (ECR) to maintain:

- **No forked server code.** Everything runs through [`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy), a generic stdio↔streamable-HTTP bridge, which spawns the exact command from the standard stdio MCP config:
  ```json
  {
    "mcpServers": {
      "awslabs.redshift-mcp-server": {
        "command": "uvx",
        "args": ["awslabs.redshift-mcp-server@latest"]
      }
    }
  }
  ```
  `uvx` always resolves the latest published PyPI release on cold start — no separate fork to keep in sync with upstream security fixes.
- **No container image, no ECR.** Packaged as a plain Lambda `.zip` deployment, using the public [AWS Lambda Web Adapter](https://github.com/aws/aws-lambda-web-adapter) **layer** (not an image) so the HTTP server `mcp-proxy` exposes runs inside Lambda's request/response model.
- **No VPC required.** The MCP server talks to Redshift only via the Redshift Data API (`redshift-data:ExecuteStatement` etc.) — plain AWS API calls, not a database socket connection.

## Architecture

```text
Caller (SigV4-signed request, service=execute-api)
                       │
                       ▼
API Gateway REST API
(AWS_IAM auth, /mcp)
                       │
                       ▼
Lambda execution environment (arm64, Python 3.13 runtime)
  ├─ Lambda Web Adapter (layer, /opt/extensions/lambda-adapter)
  │     forwards HTTP traffic to 127.0.0.1:8000
  └─ run.sh (function handler)
        └─ mcp-proxy --port=8000 --stateless --pass-environment -- \
             uvx awslabs.redshift-mcp-server@latest
                 └─ talks to Redshift via the Redshift Data API (boto3)
```

## Deployment prerequisites

### Tools

- AWS CLI v2, configured with credentials for the target account (see **Permissions required to deploy** below).
- Python 3.9+ with `pip`, and the `zip` command — both are preinstalled on macOS and most Linux distributions (Windows: use WSL, or install `zip` separately). Used at build time to install `uv` and `mcp-proxy` targeting the Lambda runtime's platform (`manylinux2014_aarch64`, Python 3.13) via `pip install --platform --only-binary=:all:` — no compiler, no Docker, no Finch. This works because every dependency in this deployment publishes prebuilt manylinux/arm64 wheels.
- Option A only: the [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-install.html) (`brew install aws-sam-cli` on macOS).

### Permissions required to deploy

The credentials you deploy with (not the Lambda's own execution role — see below) need permission to create the IAM role, the Lambda function, and the API Gateway REST API. [`deployer-permissions-policy.json`](deployer-permissions-policy.json) is a ready-to-use IAM policy scoped to this deployment's specific resource names (`redshift-mcp-lambda-execution-role` role, `redshift-mcp-proxy-zip*` function). If you deploy under a different function/role name, update the ARNs in that file to match, or use a broader policy (e.g. `AdministratorAccess`) for a one-off test in a sandbox account.

Attach it to your own IAM user/role, or hand it to whoever will run `deploy.sh` / `sam deploy`:

```bash
aws iam put-user-policy \
  --user-name <your-iam-user> \
  --policy-name RedshiftMcpDeployerAccess \
  --policy-document file://deployer-permissions-policy.json
```

(Substitute `put-role-policy --role-name <role>` if deploying from an assumed role instead of an IAM user.)

Option A (SAM) additionally needs the `SamCloudFormationStack` and `SamManagedArtifactBucket` statements in that file — `sam deploy --resolve-s3` creates a managed S3 bucket (`aws-sam-cli-managed-*`) for deployment artifacts and deploys via a CloudFormation stack. Option B (plain CLI) only needs the IAM and Lambda statements.

If you deploy with `CreateDevOpsAgentRole=true` (see [`sam-app/README.md`](sam-app/README.md#create-a-devops-agent-iam-role)), you also need the `IamDevOpsAgentRoleManagement` statement in that file, and must pass `--capabilities CAPABILITY_NAMED_IAM` instead of `CAPABILITY_IAM` (the created role has an explicit name).

### The MCP server's own execution role

The connected MCP server's Lambda execution role needs Redshift/Redshift Data API read permissions — see [`redshift-access-policy.json`](redshift-access-policy.json) for the exact policy used. Both deploy options attach this automatically; you don't need to do anything extra. No additional permissions are required on the DevOps Agent's own IAM role, since all Redshift access happens through the MCP server, not the agent directly.

### Database-level permissions inside Redshift

The IAM permissions above (`GetClusterCredentialsWithIAM`/`GetClusterCredentials`) only control whether the Lambda's execution role is allowed to fetch temporary database credentials — they don't control what that database user can actually see once connected. Amazon Redshift derives the database user name directly from the calling IAM identity — specifically the Lambda's **execution role**, not the function itself. This deployment names that role `redshift-mcp-lambda-execution-role` (see `RedshiftMcpFunctionRole` in `sam-app/template.yaml`, or `ROLE_NAME` in `deploy.sh`), which maps to the database user `IAMR:redshift-mcp-lambda-execution-role` — **IAM roles use the `IAMR:` prefix, not `IAM:`** (that prefix is for IAM users only) ([source](https://docs.aws.amazon.com/redshift-data/latest/APIReference/API_ListDatabases.html)).

By default, that database user can only see **its own** queries in the monitoring views this skill relies on (`SYS_QUERY_HISTORY`, `SVL_QLOG`, etc.) — it can't see other users' activity, which is normally the point of running operational reviews and diagnostics. To let it see everyone's queries, a Redshift superuser needs to grant the built-in `sys:monitor` role to that IAM-mapped database user, once per cluster/workgroup:

```sql
GRANT ROLE sys:monitor TO "IAMR:redshift-mcp-lambda-execution-role";
```

**Don't assume the role name is exactly `redshift-mcp-lambda-execution-role`** — always confirm the actual deployed name first:

- **SAM**: use the `GrantSysMonitorCommand` and `GrantTableInfoCommand` stack outputs, which are pre-filled with the exact role name (including any suffix CloudFormation may add).
- **Plain CLI**: `deploy.sh` prints the exact `GRANT` commands at the end of a successful run.
- Or check directly: `aws lambda get-function --function-name <function-name> --query Configuration.Role --output text`, then use the role name (the part after `role/`) in the grant.

`sys:monitor` grants visibility into all users' queries and workload activity; it does not grant `sys:operator` (which would additionally allow canceling other users' queries or running VACUUM) — this skill never needs that, since `execute_query` only runs read-only SELECTs. See [Amazon Redshift system-defined roles](https://docs.aws.amazon.com/redshift/latest/dg/r_roles-default.html) for the full list of `sys:*` roles.

Some views used by this skill (notably `SVV_TABLE_INFO`, used for table-health checks) are superuser-visible by default and aren't covered by `sys:monitor`. If a query against one of these fails with `permission denied for relation ...`, grant `SELECT` on that specific view too:

```sql
GRANT SELECT ON SVV_TABLE_INFO TO "IAMR:redshift-mcp-lambda-execution-role";
```

Run both grants once per database on each cluster/workgroup this skill will query — they don't need to be repeated per session, since they're persisted against the database user.

## Authentication model

This deployment does **not** create an API key, username/password, or any custom authentication. The endpoint is IAM-authorized, meaning every request must be signed with [AWS SigV4](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv4.html) using valid AWS credentials belonging to a principal (IAM user or role) that has been explicitly granted invoke access (see **Grant invoke access to a caller** below). Requests without a valid SigV4 signature, or from a principal that hasn't been granted access, are rejected by AWS before they reach your code — there is no application-level auth to configure.

Practically, this means:

- Any MCP client that supports SigV4-signed HTTP requests can call the endpoint, as long as it signs for the `execute-api` service (the same service AWS DevOps Agent's SigV4 auth signs for) and is running with credentials for a permitted principal.
- There is no shared secret to distribute — access control is entirely IAM-based, per caller identity.
- `scripts/mcp_call.py` and `scripts/list_clusters.py` demonstrate the exact SigV4 signing steps in Python (via `botocore.auth.SigV4Auth`) if you're integrating a custom client.

## Grant invoke access to a caller

Each principal (user or role) that needs to call the endpoint must be explicitly granted `execute-api:Invoke` on it, plus `lambda:InvokeFunction` on the underlying Lambda function — both are required, since the API integration invokes the Lambda using the caller's own IAM identity rather than API Gateway's own service principal. Both deploy paths can do this automatically for one caller role at deploy time:

- **SAM**: pass `--parameter-overrides CallerRoleArn=arn:aws:iam::<account-id>:role/<role-name>`.
- **Plain CLI**: pass the role ARN as `deploy.sh`'s third argument.

For any additional caller roles (or if you skipped the option above), grant access manually. Get `<api-id>` from the `RedshiftMcpApiUrl` stack output (SAM) or the printed endpoint (plain CLI), and `<function-name>`/`<function-arn>` from the `RedshiftMcpFunctionArn` output (SAM) or `aws lambda get-function` (plain CLI):

```bash
aws iam put-role-policy \
  --role-name <caller-role-name> \
  --policy-name InvokeRedshiftMcpApi \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": "execute-api:Invoke",
        "Resource": "arn:aws:execute-api:<region>:<account-id>:<api-id>/*/POST/mcp"
      },
      {
        "Effect": "Allow",
        "Action": "lambda:InvokeFunction",
        "Resource": "arn:aws:lambda:<region>:<account-id>:function:<function-name>"
      }
    ]
  }'
```

Repeat for every caller role (for example, each role used by an agent platform's AgentSpace/WebappAdmin roles).

## Configuration (environment variables on the Lambda function)

| Variable | Purpose | Notes |
|---|---|---|
| `AWS_LAMBDA_EXEC_WRAPPER` | `/opt/bootstrap` — invokes the handler through the Web Adapter layer's wrapper. | Fixed, don't change. |
| `AWS_LWA_PORT` | `8000` — port the adapter forwards traffic to. | Fixed, don't change. |
| `AWS_LWA_READINESS_CHECK_PATH` | `/mcp` — path the adapter polls until the server is ready. | Fixed, don't change. |
| `FASTMCP_LOG_LEVEL` | Log verbosity for the underlying MCP server. | Configurable (SAM parameter `FastMcpLogLevel`, or edit `deploy.sh`). |
| `AWS_DEFAULT_REGION` | Region for the MCP server's boto3 calls. | Automatically provided by the Lambda runtime — do not set manually (Lambda reserves this key). |

`mcp-proxy` runs with `--pass-environment`, so any additional variable set on the Lambda function is forwarded automatically to the spawned `uvx awslabs.redshift-mcp-server@latest` process (anything you'd otherwise put in the standard stdio config's `env` block).

## Updating to a newer server release

Nothing to do — `uvx awslabs.redshift-mcp-server@latest` re-resolves the latest PyPI version on every **cold start**. To force an immediate refresh, either wait for the next natural cold start, or trigger one with a no-op `update-function-configuration` (invalidates warm execution environments).

## Cost and operational notes

- **Cold start**: `uvx` downloads and installs `awslabs.redshift-mcp-server` and its dependencies fresh on every cold start (~10-20s extra latency vs. a pre-baked container image; observed ~11s total duration end-to-end on a cold `list_clusters` call in testing). Warm invocations are fast.
- **No idle cost** — unlike an EC2-based deployment, there is no cost when the function isn't being invoked (aside from negligible log storage).
- **512 MB memory / 60s timeout** by default — adjust if you see timeouts under load.
- **IAM permissions** granted to the execution role (see [`redshift-access-policy.json`](redshift-access-policy.json) / the SAM template's inline policy): `redshift:DescribeClusters`, `redshift-serverless:ListWorkgroups`/`GetWorkgroup`, `redshift-data:ExecuteStatement`/`DescribeStatement`/`GetStatementResult`, `redshift-serverless:GetCredentials`, `redshift:GetClusterCredentialsWithIAM`/`GetClusterCredentials`. This is the minimum set the MCP server's six tools need; it does not grant write access to Redshift data — the server's own `execute_query` tool runs SQL inside a read-only transaction regardless of IAM permissions.
