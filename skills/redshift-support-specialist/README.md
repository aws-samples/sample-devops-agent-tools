# Amazon Redshift Support Specialist — AWS DevOps Agent Skill

A self-contained solution for connecting [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html) to Amazon Redshift: this skill (query optimization, operational reviews, disaster recovery guidance, incident detection guidance, and cost optimization), plus a ready-to-use serverless deployment of the `awslabs.redshift-mcp-server` MCP server it relies on.

> ⚠️ **Non-production disclaimer:** This skill is sample code, not intended for production use without additional review and testing. Users should validate in a non-production environment first.

## How the Pieces Fit Together

```text
AWS DevOps Agent Chat
        |  (natural language: "why is this Redshift query slow?")
        v
This skill: redshift-support-specialist
        |  (calls the 6 MCP tools: list_clusters, list_databases,
        |   list_schemas, list_tables, list_columns, execute_query)
        v
Redshift MCP Server on Lambda, behind API Gateway (AWS_IAM auth)   (deployment/)
        |  (Redshift Data API -- no VPC, no container image, no ECR)
        v
Amazon Redshift (provisioned clusters / Serverless workgroups)
```

## Setup Overview

Follow these steps **in order** — each one depends on the previous:

1. **Deploy the MCP server** ([Step 2](#step-2--deploy-the-redshift-mcp-server) below) — produces an API Gateway endpoint.
2. **Test the deployment** ([Step 2](#test-the-deployment) below) — confirm the endpoint actually works before wiring it into DevOps Agent.
3. **Connect the MCP server to your Agent Space** ([Step 3](#step-3--connect-the-mcp-server-to-your-agent-space) below) — register it and allowlist its tools.
4. **Upload this skill** ([Uploading to AWS DevOps Agent](#uploading-to-aws-devops-agent) below).
5. **(Optional) Create the custom agent** ([Create a Custom Agent](#optional-create-a-custom-agent) below) — a dedicated agent pre-wired to this skill.
6. **Use it** ([How to Use This Skill](#how-to-use-this-skill) below) — ask the DevOps Agent things like "run a health check on my Redshift cluster" in Chat.

## Purpose

Amazon Redshift domain expertise for [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html). Query performance and cluster operations are highly specialized — this skill packages that expertise (system-table diagnostics, signal thresholds, best-practices references) so the agent can run real diagnostics through the connected Redshift MCP server instead of giving generic advice, and so users don't have to manually extract data or paste CSVs into chat to get an answer.

## Key Capabilities

- **Query optimization** — diagnoses a specific slow query (EXPLAIN plan, disk spill, distribution/sort key issues) and returns concrete SQL/config fixes.
- **High-level operational review** — quick PASS/WARN/FAIL health check using only `list_clusters` data.
- **Detailed operational review** — full diagnostic sweep (storage, WLM, table design, Advisor recommendations) producing both a downloadable HTML report and an in-chat Markdown summary.
- **Disaster recovery guidance** — reference checklist and RPO/RTO guidance for snapshot/backup posture (evaluated manually since AWS CLI access isn't available through the MCP server).
- **Incident detection guidance** — recommended CloudWatch alarm set for provisioned clusters and Serverless workgroups.
- **Cost optimization** — node/RPU right-sizing and serverless migration analysis using live table and workload data.

## Prerequisites

### Step 1 — An AWS DevOps Agent Space with the target AWS account

You need an existing [Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html) with the target AWS account configured as a cloud source.

### Step 2 — Deploy the Redshift MCP Server

This skill requires the `awslabs.redshift-mcp-server` MCP server to be running and reachable. It exposes exactly six tools this skill relies on: `list_clusters`, `list_databases`, `list_schemas`, `list_tables`, `list_columns`, and `execute_query`.

This runs the **standard, unmodified** `awslabs.redshift-mcp-server@latest` PyPI package on AWS Lambda, fronted by an **API Gateway REST API** secured with AWS IAM (SigV4) authorization.

API Gateway sits in front of the Lambda function and exposes a single `/mcp` endpoint. Every request must be signed with AWS SigV4 for the `execute-api` service, from a principal that's been explicitly granted `execute-api:Invoke` on that endpoint (see **Grant invoke access to a caller** below). API Gateway validates the signature and the caller's IAM permissions before the request ever reaches the Lambda function, then forwards it into the Lambda execution environment where `mcp-proxy` bridges the HTTP request to the underlying MCP server process (see **Architecture** below). This is the endpoint you register with AWS DevOps Agent in Step 3.

No EC2 instance, no load balancer, no VPC networking, and no container registry (ECR) to maintain.

#### Why this approach

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

#### Architecture

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

#### Two ways to deploy

Both options provision the same thing: the Lambda function plus an API Gateway REST API with an AWS_IAM-authorized `POST /mcp` method in front of it.

##### Option A — AWS SAM (recommended; this *is* a CloudFormation template)

[`deployment/sam-app/`](deployment/sam-app/) contains a full [AWS SAM](https://docs.aws.amazon.com/serverless-application-model/) application. SAM templates are a CloudFormation transform (`Transform: AWS::Serverless-2016-10-31`) — `sam build`/`sam deploy` compile it down to a plain CloudFormation stack. This is the easiest path for anyone cloning this repo: it handles the Python dependency packaging automatically, so no manual zip-building or scripting is required.

```bash
cd skills/redshift-support-specialist/deployment/sam-app
sam build
sam deploy \
  --stack-name redshift-mcp \
  --capabilities CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --region us-east-1 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset
```

No `--use-container` and no Docker/Finch needed — `template.yaml` uses a `Makefile`-based custom build (`src/Makefile`) that installs dependencies with a plain `pip install --platform manylinux2014_aarch64 --only-binary=:all:`. Every dependency this deployment needs (`uv`, `mcp-proxy`, and their transitive deps like `cryptography` and `pydantic-core`) publishes prebuilt manylinux/arm64 wheels, so this produces a Lambda-compatible package without compiling anything or running a container.

This command deploys with all default parameter values and skips every interactive prompt. `CAPABILITY_NAMED_IAM` is always used (instead of the narrower `CAPABILITY_IAM`) so the same command works whether or not you later add `CreateDevOpsAgentRole=true`. To change a parameter (e.g. log level, or granting a caller role invoke access at deploy time), append `--parameter-overrides`:

```bash
sam deploy \
  --stack-name redshift-mcp \
  --capabilities CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --region us-east-1 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides CallerRoleArn=arn:aws:iam::<account-id>:role/<caller-role>
```

After a successful deploy, SAM prints the stack outputs. Example (values shown are illustrative — yours will have your own account ID, region, and generated API/function names):

```text
Key                 DevOpsAgentRoleArn
Description         ARN of the IAM role created for AWS DevOps Agent (only present when
CreateDevOpsAgentRole was 'true'). Use this ARN when connecting the MCP server
to your Agent Space capability provider.
Value               arn:aws:iam::<account-id>:role/DevOpsAgentRole-Redshift-support-specialist

Key                 RedshiftMcpFunctionArn
Description         ARN of the Redshift MCP Lambda function.
Value               arn:aws:lambda:<region>:<account-id>:function:redshift-mcp-redshift-mcp

Key                 RedshiftMcpApiUrl
Description         The endpoint to register with AWS DevOps Agent's SigV4 MCP-server
capability provider (Service Name = execute-api). Backed by API Gateway,
IAM/SigV4 authorized.
Value               https://<api-id>.execute-api.<region>.amazonaws.com/Prod/mcp

Key                 GrantInvokeCommand
Description         If CallerRoleArn was provided, that role already has invoke access
(see CallerRoleGranted below). Use this command to grant execute-api:Invoke
access to any additional caller role. Replace <ACCOUNT_ID> and <ROLE_NAME>.
Value               aws iam put-role-policy --role-name <ROLE_NAME> --policy-name InvokeRedshiftMcpApi --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"execute-api:Invoke","Resource":"arn:aws:execute-api:<region>:<account-id>:<api-id>/*/POST/mcp"},{"Effect":"Allow","Action":"lambda:InvokeFunction","Resource":"arn:aws:lambda:<region>:<account-id>:function:redshift-mcp-redshift-mcp"}]}'

Key                 CallerRoleGranted
Description         The role (if any) granted execute-api:Invoke via the CallerRoleArn
parameter at deploy time. Empty if CallerRoleArn was not provided.
Value
```

What you'll actually use from this:

- **`RedshiftMcpApiUrl`** — the endpoint you'll register with AWS DevOps Agent in Step 3 (Service Name = `execute-api`).
- **`DevOpsAgentRoleArn`** — only appears if you deployed with `CreateDevOpsAgentRole=true` (see below). You'll use this in Step 3 as the IAM role.
- **`GrantInvokeCommand`** — a ready-to-run `aws iam put-role-policy` command (with your actual API ID and function ARN filled in) for granting invoke access to any additional caller role later. See **Grant invoke access to a caller** below.

See [`deployment/sam-app/README.md`](deployment/sam-app/README.md) for the full parameter reference, and for an interactive `sam deploy --guided` alternative if you'd rather be prompted for each value.

##### Option B — Plain AWS CLI + shell script (no SAM CLI required)

If you'd rather not install the SAM CLI, `deployment/build_zip.sh` + `deployment/deploy.sh` do the same thing directly with the AWS CLI — building the Lambda package, creating the function, and provisioning the API Gateway REST API (resource, method, integration, and stage) via `aws apigateway` calls:

```bash
cd skills/redshift-support-specialist/deployment
./deploy.sh                              # uses defaults: redshift-mcp-proxy-zip, us-east-1
./deploy.sh my-function-name us-west-2   # custom name/region
./deploy.sh my-function-name us-west-2 arn:aws:iam::<account-id>:role/<caller-role>   # also grants invoke access
```

`build_zip.sh` runs a plain `pip install --platform manylinux2014_aarch64` on the host — no Docker or Finch required, for the same reason as Option A above. The optional third argument to `deploy.sh` grants that caller role `execute-api:Invoke` on the API and `lambda:InvokeFunction` on the function automatically, so you can skip the manual grant step below.

#### Deployment prerequisites

##### Tools

- AWS CLI v2, configured with credentials for the target account (see **Permissions required to deploy** below).
- Python 3.9+ with `pip`, and the `zip` command — both are preinstalled on macOS and most Linux distributions (Windows: use WSL, or install `zip` separately). Used at build time to install `uv` and `mcp-proxy` targeting the Lambda runtime's platform (`manylinux2014_aarch64`, Python 3.13) via `pip install --platform --only-binary=:all:` — no compiler, no Docker, no Finch. This works because every dependency in this deployment publishes prebuilt manylinux/arm64 wheels.
- Option A only: the [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-install.html) (`brew install aws-sam-cli` on macOS).

##### Permissions required to deploy

The credentials you deploy with (not the Lambda's own execution role — see below) need permission to create the IAM role, the Lambda function, and the API Gateway REST API. [`deployment/deployer-permissions-policy.json`](deployment/deployer-permissions-policy.json) is a ready-to-use IAM policy scoped to this deployment's specific resource names (`redshift-mcp-lambda-role` role, `redshift-mcp-proxy-zip*` function). If you deploy under a different function/role name, update the ARNs in that file to match, or use a broader policy (e.g. `AdministratorAccess`) for a one-off test in a sandbox account.

Attach it to your own IAM user/role, or hand it to whoever will run `deploy.sh` / `sam deploy`:

```bash
aws iam put-user-policy \
  --user-name <your-iam-user> \
  --policy-name RedshiftMcpDeployerAccess \
  --policy-document file://deployer-permissions-policy.json
```

(Substitute `put-role-policy --role-name <role>` if deploying from an assumed role instead of an IAM user.)

Option A (SAM) additionally needs the `SamCloudFormationStack` and `SamManagedArtifactBucket` statements in that file — `sam deploy --resolve-s3` creates a managed S3 bucket (`aws-sam-cli-managed-*`) for deployment artifacts and deploys via a CloudFormation stack. Option B (plain CLI) only needs the IAM and Lambda statements.

If you deploy with `CreateDevOpsAgentRole=true` (see [`deployment/sam-app/README.md`](deployment/sam-app/README.md#create-a-devops-agent-iam-role)), you also need the `IamDevOpsAgentRoleManagement` statement in that file, and must pass `--capabilities CAPABILITY_NAMED_IAM` instead of `CAPABILITY_IAM` (the created role has an explicit name).

##### The MCP server's own execution role

The connected MCP server's Lambda execution role needs Redshift/Redshift Data API read permissions — see [`deployment/redshift-access-policy.json`](deployment/redshift-access-policy.json) for the exact policy used. Both deploy options attach this automatically; you don't need to do anything extra. No additional permissions are required on the DevOps Agent's own IAM role, since all Redshift access happens through the MCP server, not the agent directly.

##### Authentication model — what this deployment produces

This deployment does **not** create an API key, username/password, or any custom authentication. The endpoint is IAM-authorized, meaning every request must be signed with [AWS SigV4](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv4.html) using valid AWS credentials belonging to a principal (IAM user or role) that has been explicitly granted invoke access (see **Grant invoke access to a caller** below). Requests without a valid SigV4 signature, or from a principal that hasn't been granted access, are rejected by AWS before they reach your code — there is no application-level auth to configure.

Practically, this means:

- Any MCP client that supports SigV4-signed HTTP requests can call the endpoint, as long as it signs for the `execute-api` service (the same service AWS DevOps Agent's SigV4 auth signs for) and is running with credentials for a permitted principal.
- There is no shared secret to distribute — access control is entirely IAM-based, per caller identity.
- `deployment/scripts/mcp_call.py` and `deployment/scripts/list_clusters.py` demonstrate the exact SigV4 signing steps in Python (via `botocore.auth.SigV4Auth`) if you're integrating a custom client.

#### Grant invoke access to a caller

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

#### Test the deployment

Before moving to Step 3, confirm the deployment actually works. The quickest smoke test is `deployment/scripts/list_clusters.py` — it calls the deployed endpoint's `list_clusters` MCP tool and prints every cluster/workgroup in the account, confirming SigV4 auth, API Gateway, and the Lambda all work end-to-end.

Requires `boto3` (`python3 -c "import boto3"` to check; `pip3 install boto3` if missing — on some systems you'll need `pip3 install --user boto3` or a virtualenv due to PEP 668's externally-managed-environment restriction).

```bash
export AWS_PROFILE="your-profile"   # optional, uses default credential chain if unset
export MCP_FUNCTION_URL="https://<api-id>.execute-api.<region>.amazonaws.com/Prod/mcp"   # RedshiftMcpApiUrl stack output

python3 deployment/scripts/list_clusters.py
```

Expected output:
```text
HTTP 200

Found 3 clusters/workgroups:

- my-provisioned-cluster                        type=provisioned  status=available
- my-serverless-workgroup                       type=serverless   status=AVAILABLE
- another-cluster                               type=provisioned  status=paused
```

If this works, the deployment is good — continue to Step 3. If it fails, fix the deployment before proceeding; connecting a broken endpoint to DevOps Agent will just produce the same failure inside Chat, with less visibility into why.

For a deeper test that runs actual SQL through the MCP server, use `mcp_call.py` with a real cluster identifier and database from the list above:

```bash
python3 deployment/scripts/mcp_call.py execute_query \
  '{"cluster_identifier": "my-cluster", "database_name": "dev", "sql": "SELECT 1"}'
```

#### Configuration (environment variables on the Lambda function)

| Variable | Purpose | Notes |
|---|---|---|
| `AWS_LAMBDA_EXEC_WRAPPER` | `/opt/bootstrap` — invokes the handler through the Web Adapter layer's wrapper. | Fixed, don't change. |
| `AWS_LWA_PORT` | `8000` — port the adapter forwards traffic to. | Fixed, don't change. |
| `AWS_LWA_READINESS_CHECK_PATH` | `/mcp` — path the adapter polls until the server is ready. | Fixed, don't change. |
| `FASTMCP_LOG_LEVEL` | Log verbosity for the underlying MCP server. | Configurable (SAM parameter `FastMcpLogLevel`, or edit `deploy.sh`). |
| `AWS_DEFAULT_REGION` | Region for the MCP server's boto3 calls. | Automatically provided by the Lambda runtime — do not set manually (Lambda reserves this key). |

`mcp-proxy` runs with `--pass-environment`, so any additional variable set on the Lambda function is forwarded automatically to the spawned `uvx awslabs.redshift-mcp-server@latest` process (anything you'd otherwise put in the standard stdio config's `env` block).

#### Updating to a newer server release

Nothing to do — `uvx awslabs.redshift-mcp-server@latest` re-resolves the latest PyPI version on every **cold start**. To force an immediate refresh, either wait for the next natural cold start, or trigger one with a no-op `update-function-configuration` (invalidates warm execution environments).

#### Cost and operational notes

- **Cold start**: `uvx` downloads and installs `awslabs.redshift-mcp-server` and its dependencies fresh on every cold start (~10-20s extra latency vs. a pre-baked container image; observed ~11s total duration end-to-end on a cold `list_clusters` call in testing). Warm invocations are fast.
- **No idle cost** — unlike an EC2-based deployment, there is no cost when the function isn't being invoked (aside from negligible log storage).
- **512 MB memory / 60s timeout** by default — adjust if you see timeouts under load.
- **IAM permissions** granted to the execution role (see `deployment/redshift-access-policy.json` / the SAM template's inline policy): `redshift:DescribeClusters`, `redshift-serverless:ListWorkgroups`/`GetWorkgroup`, `redshift-data:ExecuteStatement`/`DescribeStatement`/`GetStatementResult`, `redshift-serverless:GetCredentials`, `redshift:GetClusterCredentialsWithIAM`/`GetClusterCredentials`. This is the minimum set the MCP server's six tools need; it does not grant write access to Redshift data — the server's own `execute_query` tool runs SQL inside a read-only transaction regardless of IAM permissions.

#### Tearing down

**SAM:**
```bash
cd skills/redshift-support-specialist/deployment/sam-app
sam delete --stack-name <stack-name>
```

**Plain CLI:**
```bash
aws apigateway delete-rest-api --rest-api-id <api-id>
aws lambda delete-function --function-name <function-name>
aws iam delete-role-policy --role-name redshift-mcp-lambda-role --policy-name RedshiftMcpAccess
aws iam detach-role-policy --role-name redshift-mcp-lambda-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name redshift-mcp-lambda-role
```
(Get `<api-id>` from the `deploy.sh` output, or `aws apigateway get-rest-apis`.)

### Step 3 — Connect the MCP server to your Agent Space

Once the MCP server is deployed and you've confirmed it works (Step 2's **Test the deployment**), register it with your Agent Space:

**3a. Register the MCP server (account level):**

1. Sign in to the AWS Management Console and open the AWS DevOps Agent console.
2. Go to **Capability Providers** (side navigation) → find **MCP Server** → choose **Register**.
3. Enter:
   - **Name** — any descriptive name (e.g. `redshift-mcp`).
   - **Endpoint URL** — the `RedshiftMcpApiUrl` value from your Step 2 stack outputs (e.g. `https://<api-id>.execute-api.<region>.amazonaws.com/Prod/mcp`).
   - Leave **Enable Dynamic Client Registration** and **Connect to endpoint using private connection** unchecked (this deployment is public API Gateway, not a private VPC endpoint).
4. Choose **Next**.

**3b. Authorization:** select **AWS SigV4** → **Next**.

**3c. Authorization configuration:**

1. **Configure IAM role**:
   - If you deployed with `CreateDevOpsAgentRole=true`, choose **Use an existing role** and select the role at the `DevOpsAgentRoleArn` stack output (e.g. `DevOpsAgentRole-Redshift-support-specialist`) — it's already trust-configured and permissioned for this exact endpoint.
   - Otherwise, choose **Create a new role manually** and follow the console's prompts (trust policy for `aidevops.amazonaws.com`, permissions for `execute-api:Invoke` on this API — see Step 2's **Grant invoke access to a caller** for the policy shape).
2. **AWS Region** — the region you deployed to (e.g. `us-east-1`).
3. **Service Name** — `execute-api`.
4. Choose **Next**.

**3d. Review and submit:** review the details, choose **Submit**. AWS DevOps Agent validates the connection immediately — if it fails, re-check the endpoint URL and that the IAM role has both `execute-api:Invoke` and `lambda:InvokeFunction` (see Step 2's **Grant invoke access to a caller**).

**3e. Add it to your Agent Space:**

1. In the AWS DevOps Agent console, select your Agent Space → **Capabilities** tab.
2. In the **MCP Servers** section, choose **Add** → select the server you just registered.
3. Choose **Allow all tools** (this skill needs all six: `list_clusters`, `list_databases`, `list_schemas`, `list_tables`, `list_columns`, `execute_query`).
4. Choose **Add**.

> Full reference: [Connecting MCP Servers](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-integrations-and-knowledge-connecting-mcp-servers.html)

## Limitations

- **No AWS CLI or CloudWatch access.** Every Redshift interaction goes through the six MCP server tools only (`list_clusters`, `list_databases`, `list_schemas`, `list_tables`, `list_columns`, `execute_query`). Checks that require CloudWatch metrics/alarms, snapshot inventory, SSL/audit-log/parameter-group configuration, or Reserved Instance coverage are reported as "Not Available" rather than guessed — see Capabilities 4 and 5 in `SKILL.md`.
- **Read-only.** `execute_query` runs inside a read-only transaction — the skill never runs INSERT/UPDATE/DELETE/ALTER/DROP/CREATE/GRANT/VACUUM/ANALYZE; it only recommends such statements for the user to run themselves.
- **One query per `execute_query` call.** Diagnostics that need multiple result sets require multiple tool calls; there is no multi-statement/transaction support.
- **No data retention.** Every session collects data fresh; nothing from a prior report or customer is cached or reused across sessions.

## Agent Types

This skill is intended for:

- **Chat** — conversational invocation ("why is this Redshift query slow?", "run a Redshift health check on my cluster").

Select **Generic** at upload time if you want the skill available to all agent types.

## Uploading to AWS DevOps Agent

> Reference: [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#uploading-a-skill)

You can deploy this skill in one of three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/redshift-support-specialist` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

**Option B: Upload as a zip file**

1. Package the skill. From the `skills/` directory in this repo:

   ```bash
   cd skills
   zip -r redshift-support-specialist.zip redshift-support-specialist/ \
     -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.html' \
     -x '*/evals/*' '*/.skilleval.yaml' '*/CHANGELOG.md' '*/README.md' \
        '*/deployment/*'
   ```

   The resulting zip contains:

   ```text
   redshift-support-specialist/
   ├── SKILL.md
   ├── assets/
   │   ├── config/thresholds.yaml
   │   ├── queries/*.md
   │   └── templates/detailed-operational-review.{html,md}
   └── references/*.md
   ```

   Constraints (enforced at upload time):

   - Total zip size ≤ 6 MB.
   - `SKILL.md` is required and must include `name` and `description` frontmatter.
   - A `scripts/` directory is not allowed — this skill does not include one.

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the zip file (or browse to it).
5. Select agent type: **Chat** (or leave **Generic** to make it available to all agent types).
6. Review the validation results.
7. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign the skill to the `CHAT` agent type. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

## How to Use This Skill

This skill is intended for Chat — just describe what you need in plain language. The agent matches your request to one of the six capabilities below, discovers the cluster/workgroup itself, and collects diagnostics live through the MCP server. You never need to supply a cluster identifier from memory, an AWS CLI profile, or a CSV export.

### Chat — sample prompts

#### Query Optimization (live, via `execute_query`)

- "Why is this Redshift query running slow? `SELECT ...`"
- "This query has been running for 20 minutes on my-cluster, what's wrong with it?"

#### High-Level Operational Review (live, via `list_clusters`)

- "Run a health check on my Redshift cluster."
- "Give me a quick PASS/FAIL summary of all my Redshift clusters and workgroups."

#### Detailed Operational Review (live, via `execute_query` — the most thorough capability)

- "Run a detailed operational review on cluster `my-cluster`."
- "Do a full diagnostic sweep of my Redshift Serverless workgroup, all databases."

The agent will first ask you to confirm scope (which cluster/workgroup and database(s)), execution mode (background vs. step-by-step), and whether you want a downloadable HTML report — answer that one combined question and it proceeds. **To make sure you get the full HTML report** (not just the in-chat Markdown summary), say so explicitly, for example:

- "Run a detailed operational review on `my-cluster` and generate the full downloadable HTML report."
- "Yes, background mode, and yes I want the HTML report file." (as a reply to the agent's combined confirmation question)

Every report — HTML and Markdown — always includes the full section set: executive summary, cluster overview, all findings, WLM configuration, workload analysis, top queries by runtime, table design, Spectrum/external queries, data sharing, and prioritized recommendations. The "Cluster Level Review (Power-2)" section (CloudWatch metrics, SSL/audit config, support cases) is always marked "Not Available via MCP tools" since that data requires AWS CLI/CloudWatch access this skill doesn't have.

#### Disaster Recovery Recommendations (reference guidance; live data needs user-supplied config — AWS CLI access not available via the MCP server)

- "What's our disaster recovery posture for Redshift?"
- "What RPO/RTO can I expect with my current snapshot schedule?"

#### Incident Detection & Response (reference guidance; live data needs user-supplied config — CloudWatch access not available via the MCP server)

- "What alarms should I have configured for my Redshift Serverless workgroup?"
- "A query just failed with a disk-full error, what should I check?"

#### Cost Optimization (partially live via MCP server; Reserved Instance/CloudWatch utilization data requires user input)

- "Should I move this Redshift cluster to Serverless?"
- "Is my cluster over-provisioned? Can I resize down or move to Graviton (RG) instances?"

### What to expect from any request

- Discovers clusters/workgroups itself via `list_clusters` — never asks you to type a cluster identifier or CLI profile from memory.
- Collects diagnostics live via `execute_query` — never asks you to upload a CSV or run an extraction script.
- For the detailed operational review, always asks you to confirm database scope, background/step-by-step mode, and HTML-report preference in one combined message before collecting any data.
- Clearly marks any check that needs AWS CLI/CloudWatch access (which the MCP server does not provide) as "Not Available," rather than guessing.
- Quotes the actual tool error text back to you if a diagnostic query fails (missing view, permission denied, etc.) instead of just saying "failed," and continues with the remaining sections.

See `SKILL.md` for full workflow details per capability.

## Optional: Create a Custom Agent

Instead of (or in addition to) using this skill from the base DevOps Agent Chat, you can create a dedicated **custom agent** pre-wired to this skill and its MCP tools — useful if you want a purpose-built entry point for Redshift work, or want to enforce agent-specific behavior (this custom agent always runs in the active chat session and never switches to background mode).

The custom agent lives in [`custom-agents/redshift-support-specialist/`](../../custom-agents/redshift-support-specialist/) at the repo root. Its README covers:

- Creating the agent (Form: name, system prompt, skill selection).
- Assigning the six MCP tools via Chat (tools can only be assigned this way, not through the Form).
- Executing the agent.

This step is optional — the skill works from ordinary Chat without a custom agent, once Steps 1–4 above are done.

## Skill Contents

```text
redshift-support-specialist/
├── SKILL.md                       # Required: main skill instructions (with frontmatter)
├── README.md                      # Required: this file -- skill usage guide
├── CHANGELOG.md                   # Required: version history
├── LICENSE                        # Apache-2.0
├── NOTICE
├── references/                    # best practices, system tables guide, incident playbooks, etc.
├── assets/
│   ├── config/thresholds.yaml     # signal thresholds for automated health checks
│   ├── queries/                   # ready-to-run diagnostic SQL templates
│   └── templates/                 # HTML + Markdown report templates (structure only, no sample data)
├── evals/                         # evaluation data (not included in the upload zip)
└── deployment/                    # Serverless (Lambda) MCP server deployment -- see deployment/README.md
```

A companion custom agent system prompt for pairing with this skill lives in [`custom-agents/redshift-support-specialist/`](../../custom-agents/redshift-support-specialist/) — see **Optional: Create a Custom Agent** above.

Only `SKILL.md`, `references/`, `assets/`, and `evals/` are part of the [Agent Skills specification](https://agentskills.io/specification) upload package (see packaging command above). `deployment/` is supplementary material for this repository and is excluded from the skill zip.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
