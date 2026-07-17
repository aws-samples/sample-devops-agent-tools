# Amazon Redshift Support Specialist — AWS DevOps Agent Skill

A self-contained solution for connecting [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html) to Amazon Redshift: this skill (query optimization, operational reviews, disaster recovery guidance, incident detection guidance, and cost optimization), plus a ready-to-use serverless deployment of the `awslabs.redshift-mcp-server` MCP server it relies on.

> ⚠️ **Non-production disclaimer:** This skill is sample code, not intended for production use without additional review and testing. Users should validate in a non-production environment first.

## Purpose

Amazon Redshift domain expertise for [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html). Query performance and cluster operations are highly specialized — this skill packages that expertise (system-table diagnostics, signal thresholds, best-practices references) so the agent can run real diagnostics through the connected Redshift MCP server instead of giving generic advice, and so users don't have to manually extract data or paste CSVs into chat to get an answer.

## Key Capabilities

- **Query optimization** — diagnoses a specific slow query (EXPLAIN plan, disk spill, distribution/sort key issues) and returns concrete SQL/config fixes.
- **High-level operational review** — quick PASS/WARN/FAIL health check using only `list_clusters` data.
- **Detailed operational review** — full diagnostic sweep (storage, WLM, table design, Advisor recommendations) producing both a downloadable HTML report and an in-chat Markdown summary.
- **Disaster recovery guidance** — reference checklist and RPO/RTO guidance for snapshot/backup posture (evaluated manually since AWS CLI access isn't available through the MCP server).
- **Incident detection guidance** — recommended CloudWatch alarm set for provisioned clusters and Serverless workgroups.
- **Cost optimization** — node/RPU right-sizing and serverless migration analysis using live table and workload data.

## Setup Overview

Follow these steps **in order** — each one depends on the previous:

1. **[MCP Server Deployment](#step-1--mcp-server-deployment)** — deploy the Redshift MCP server (AWS SAM or plain CLI) and confirm it works.
2. **[Connect the MCP server to your Agent Space](#step-2--connect-the-mcp-server-to-your-agent-space)** — register it and allowlist its tools.
3. **[Create the redshift-support-specialist Skill](#step-3--create-the-redshift-support-specialist-skill)** — upload the skill to your Agent Space.
4. **[Create the Custom Agent](#step-4--create-the-custom-agent)** — a dedicated agent pre-wired to this skill.
5. **[How to Use the Skill](#step-5--how-to-use-the-skill)** — ask the DevOps Agent things like "run a health check on my Redshift cluster" in Chat.

## Prerequisites

### An AWS DevOps Agent Space with the target AWS account

You need an existing [Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html) with the target AWS account configured as a cloud source.

### Tools to deploy the MCP server

- AWS CLI v2, configured with credentials for the target account.
- Python 3.9+ with `pip`, and the `zip` command (preinstalled on macOS/most Linux; Windows: use WSL, or install `zip` separately).
- If using Option A (AWS SAM): the [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-install.html) (`brew install aws-sam-cli` on macOS).

Full details (including exactly what each tool is used for) are in [`deployment/README.md`](deployment/README.md#deployment-prerequisites).

### IAM permissions to deploy

The credentials you deploy with need permission to create the IAM role, the Lambda function, and the API Gateway REST API. A ready-to-use scoped policy is provided at [`deployment/deployer-permissions-policy.json`](deployment/deployer-permissions-policy.json) — see [`deployment/README.md`](deployment/README.md#permissions-required-to-deploy) for how to attach it and for capability nuances (SAM vs. plain CLI, `CreateDevOpsAgentRole=true`).

## Step 1 — MCP Server Deployment

This skill requires the `awslabs.redshift-mcp-server` MCP server to be running and reachable. It exposes exactly six tools this skill relies on: `list_clusters`, `list_databases`, `list_schemas`, `list_tables`, `list_columns`, and `execute_query`.

Deployment runs the **standard, unmodified** `awslabs.redshift-mcp-server@latest` PyPI package on AWS Lambda, fronted by an **API Gateway REST API** secured with AWS IAM (SigV4) authorization — no EC2, no load balancer, no VPC, no container registry. This is the endpoint you register with AWS DevOps Agent in Step 2. For the full architecture rationale, see [`deployment/README.md`](deployment/README.md#why-this-approach).

### Two ways to deploy

Both options provision the same thing: the Lambda function plus an API Gateway REST API with an AWS_IAM-authorized `POST /mcp` method in front of it.

#### Option A — AWS SAM (recommended; this *is* a CloudFormation template)

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

After a successful deploy, SAM prints the stack outputs:

| Output | Description | Example value |
|---|---|---|
| `RedshiftMcpApiUrl` | The endpoint to register with AWS DevOps Agent's SigV4 MCP-server capability provider (Service Name = `execute-api`). Backed by API Gateway, IAM/SigV4 authorized. | `https://<api-id>.execute-api.<region>.amazonaws.com/Prod/mcp` |
| `DevOpsAgentRoleArn` | ARN of the IAM role created for AWS DevOps Agent — only present when `CreateDevOpsAgentRole` was `true`. Use this when connecting the MCP server to your Agent Space capability provider. | `arn:aws:iam::<account-id>:role/DevOpsAgentRole-Redshift-support-specialist` |
| `RedshiftMcpFunctionArn` | ARN of the Redshift MCP Lambda function. | `arn:aws:lambda:<region>:<account-id>:function:redshift-mcp-redshift-mcp` |
| `GrantInvokeCommand` | A ready-to-run `aws iam put-role-policy` command (pre-filled with your actual API ID and function ARN) to grant `execute-api:Invoke` access to an additional caller role — see below. | See command below. |
| `CallerRoleGranted` | The role (if any) granted `execute-api:Invoke` via the `CallerRoleArn` parameter at deploy time. Empty if `CallerRoleArn` wasn't provided. | *(empty, or a role ARN)* |

`GrantInvokeCommand`'s value looks like this (replace `<ROLE_NAME>` with the caller role to grant):

```bash
aws iam put-role-policy \
  --role-name <ROLE_NAME> \
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
        "Resource": "arn:aws:lambda:<region>:<account-id>:function:redshift-mcp-redshift-mcp"
      }
    ]
  }'
```

What you'll actually use from this:

- **`RedshiftMcpApiUrl`** — the endpoint you'll register with AWS DevOps Agent in Step 2 (Service Name = `execute-api`).
- **`DevOpsAgentRoleArn`** — only appears if you deployed with `CreateDevOpsAgentRole=true` (see below). You'll use this in Step 2 as the IAM role.
- **`GrantInvokeCommand`** — a ready-to-run `aws iam put-role-policy` command (with your actual API ID and function ARN filled in) for granting invoke access to any additional caller role later. See [`deployment/README.md`](deployment/README.md#grant-invoke-access-to-a-caller).

See [`deployment/sam-app/README.md`](deployment/sam-app/README.md) for the full parameter reference, and for an interactive `sam deploy --guided` alternative if you'd rather be prompted for each value.

#### Option B — Plain AWS CLI + shell script (no SAM CLI required)

If you'd rather not install the SAM CLI, `deployment/build_zip.sh` + `deployment/deploy.sh` do the same thing directly with the AWS CLI — building the Lambda package, creating the function, and provisioning the API Gateway REST API (resource, method, integration, and stage) via `aws apigateway` calls:

```bash
cd skills/redshift-support-specialist/deployment
./deploy.sh                              # uses defaults: redshift-mcp-proxy-zip, us-east-1
./deploy.sh my-function-name us-west-2   # custom name/region
./deploy.sh my-function-name us-west-2 arn:aws:iam::<account-id>:role/<caller-role>   # also grants invoke access
```

`build_zip.sh` runs a plain `pip install --platform manylinux2014_aarch64` on the host — no Docker or Finch required, for the same reason as Option A above. The optional third argument to `deploy.sh` grants that caller role `execute-api:Invoke` on the API and `lambda:InvokeFunction` on the function automatically, so you can skip the manual grant step below.

### Database-level permissions inside Redshift

The IAM policy above only controls whether the Lambda can fetch temporary database credentials — it doesn't control what the resulting database user can see once connected. By default, that user can only see its own queries in monitoring views, not other users' activity. On each cluster/workgroup this skill will query, run once as a database superuser:

```sql
GRANT ROLE sys:monitor TO "IAMR:<lambda-execution-role-name>";
```

**Get the exact role name from your deployment**, don't assume it — use the `GrantSysMonitorCommand` stack output (SAM) or the command printed at the end of `deploy.sh` (plain CLI), both pre-filled with the real role name. See [`deployment/README.md`](deployment/README.md#database-level-permissions-inside-redshift) for the full explanation, why IAM roles use the `IAMR:` prefix (not `IAM:`), and an additional grant needed for `SVV_TABLE_INFO`.

### Test the deployment

Before moving to Step 2, confirm the deployment actually works. The quickest smoke test is `deployment/scripts/list_clusters.py` — it calls the deployed endpoint's `list_clusters` MCP tool and prints every cluster/workgroup in the account, confirming SigV4 auth, API Gateway, and the Lambda all work end-to-end.

The only dependency is `boto3`. On most systems (macOS with Homebrew Python, recent Linux distros), `pip3 install boto3` fails with an "externally-managed-environment" error (PEP 668). Use a virtual environment instead:

```bash
cd skills/redshift-support-specialist
python3 -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate
pip install boto3
```

Then, with the virtualenv still active:

```bash
export AWS_PROFILE="your-profile"   # optional, uses default credential chain if unset
export MCP_FUNCTION_URL="https://<api-id>.execute-api.<region>.amazonaws.com/Prod/mcp"   # RedshiftMcpApiUrl stack output

python3 deployment/scripts/list_clusters.py
```

When you're done testing, run `deactivate` to leave the virtualenv.

Expected output:
```text
HTTP 200

Found 3 clusters/workgroups:

- my-provisioned-cluster                        type=provisioned  status=available
- my-serverless-workgroup                       type=serverless   status=AVAILABLE
- another-cluster                               type=provisioned  status=paused
```

If this works, the deployment is good — continue to Step 2. If it fails, fix the deployment before proceeding; connecting a broken endpoint to DevOps Agent will just produce the same failure inside Chat, with less visibility into why.

For a deeper test that runs actual SQL through the MCP server, use `mcp_call.py` with a real cluster identifier and database from the list above (same virtualenv, still active):

```bash
python3 deployment/scripts/mcp_call.py execute_query \
  '{"cluster_identifier": "my-cluster", "database_name": "dev", "sql": "SELECT 1"}'
```

Environment variable reference, cold-start/cost notes, and how the MCP server picks up new PyPI releases are covered in [`deployment/README.md`](deployment/README.md).

### Tearing down

**SAM:**
```bash
cd skills/redshift-support-specialist/deployment/sam-app
sam delete --stack-name redshift-mcp --region us-east-1
```

Replace `redshift-mcp` and `us-east-1` with the stack name and region you deployed with. Add `--profile <name>` if you're not using your default AWS credentials. `sam delete` prompts for confirmation, then removes the CloudFormation stack (Lambda function, API Gateway REST API, IAM roles) and the SAM-managed S3 deployment artifacts for this stack.

**Plain CLI:**
```bash
aws apigateway delete-rest-api --rest-api-id <api-id>
aws lambda delete-function --function-name <function-name>
aws iam delete-role-policy --role-name redshift-mcp-lambda-execution-role --policy-name RedshiftMcpAccess
aws iam detach-role-policy --role-name redshift-mcp-lambda-execution-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name redshift-mcp-lambda-execution-role
```
(Get `<api-id>` from the `deploy.sh` output, or `aws apigateway get-rest-apis`.)

## Step 2 — Connect the MCP server to your Agent Space

Once the MCP server is deployed and you've confirmed it works (Step 1's **Test the deployment**), register it with your Agent Space:

**2a. Register the MCP server (account level):**

1. Sign in to the AWS Management Console and open the AWS DevOps Agent console.
2. Go to **Capability Providers** (side navigation) → find **MCP Server** → choose **Register**.
3. Enter:
   - **Name** — any descriptive name (e.g. `redshift-mcp`).
   - **Endpoint URL** — the `RedshiftMcpApiUrl` value from your Step 1 stack outputs (e.g. `https://<api-id>.execute-api.<region>.amazonaws.com/Prod/mcp`).
   - Leave **Enable Dynamic Client Registration** and **Connect to endpoint using private connection** unchecked (this deployment is public API Gateway, not a private VPC endpoint).
4. Choose **Next**.

**2b. Authorization:** select **AWS SigV4** → **Next**.

**2c. Authorization configuration:**

1. **Configure IAM role**:
   - If you deployed with `CreateDevOpsAgentRole=true`, choose **Use an existing role** and select the role at the `DevOpsAgentRoleArn` stack output (e.g. `DevOpsAgentRole-Redshift-support-specialist`) — it's already trust-configured and permissioned for this exact endpoint.
   - Otherwise, choose **Create a new role manually** and follow the console's prompts (trust policy for `aidevops.amazonaws.com`, permissions for `execute-api:Invoke` on this API — see [`deployment/README.md`](deployment/README.md#grant-invoke-access-to-a-caller) for the policy shape).
2. **AWS Region** — the region you deployed to (e.g. `us-east-1`).
3. **Service Name** — `execute-api`.
4. Choose **Add**, then wait for AWS DevOps Agent to register the MCP server successfully. If registration fails, re-check the endpoint URL and that the IAM role has both `execute-api:Invoke` and `lambda:InvokeFunction` (see [`deployment/README.md`](deployment/README.md#grant-invoke-access-to-a-caller)).

**2d. Add it to your Agent Space:**

1. In the AWS DevOps Agent console, select your Agent Space → **Capabilities** tab.
2. In the **MCP Servers** section, choose **Add** → select the server you just registered.
3. Choose **Allow all tools** (this skill needs all six: `list_clusters`, `list_databases`, `list_schemas`, `list_tables`, `list_columns`, `execute_query`).
4. Choose **Add**.

> Full reference: [Connecting MCP Servers](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-integrations-and-knowledge-connecting-mcp-servers.html)

## Step 3 — Create the redshift-support-specialist Skill

> Reference: [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#uploading-a-skill)

You can upload this skill in one of three ways:

**Option A: Import from GitHub (recommended)**

This requires a GitHub connection on your Agent Space, set up in two steps:

1. **Register GitHub at the account level** — in the AWS Management Console, go to **Capability Providers** (account-level, not inside a specific Agent Space) → find **GitHub** → **Register**. Choose User or Organization, pick GitHub App permissions, submit, then authorize and install the app on GitHub. Full steps: [Connecting GitHub](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html).
2. **Attach it to your Agent Space** — open your Agent Space's own console page (not the DevOps Agent web app) → **Capabilities** tab → **Pipeline** section → **Add** → select the GitHub registration from step 1 → choose the repository (this one, if importing this skill) → **Add**.

Once connected, import this skill directly from the repository: in the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/redshift-support-specialist` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

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

## Step 4 — Create the Custom Agent

In addition to using this skill from the base DevOps Agent Chat, create a dedicated **custom agent** pre-wired to this skill and its MCP tools — useful if you want a purpose-built entry point for Redshift work, or want to enforce agent-specific behavior (this custom agent always runs in the active chat session and never switches to background mode).

The custom agent's system prompt, README, and changelog live in [`custom-agents/redshift-support-specialist/`](../../custom-agents/redshift-support-specialist/) at the repo root.

**4a. Create the agent:**

1. In the DevOps Agent web app, go to the **Agents** page.
2. In the **Custom Agents** section, click **Create agent**.
3. In the dialog, click **Form**.
4. Fill out the form:
   - **Name** — `redshift-support-specialist` (lowercase letters, numbers, hyphens only).
   - **System prompt** — copy the full content of [`custom-agents/redshift-support-specialist/SYSTEM_PROMPT.md`](../../custom-agents/redshift-support-specialist/SYSTEM_PROMPT.md) and paste it in.
   - **Skills** — select the `redshift-support-specialist` skill (the one you uploaded in Step 3).
5. Click **Create agent**.

**4b. Assign the MCP tools (Chat only):**

MCP tools cannot be assigned through the Form — they can only be configured through Chat, either when creating the agent via Chat instead of Form, or by editing an existing agent:

1. On the newly created agent's page, click **Edit**, then select **Chat**. A new chat opens.
2. Once DevOps Agent finishes loading the agent's context, type:

   ```text
   Add the list_clusters, list_databases, list_schemas, list_tables, list_columns, and execute_query tools from the awslabs.redshift-mcp-server MCP server to this custom agent.
   ```

3. Once the chat finishes, verify all six tools appear under **Tools** on the agent's page. This agent has no other way to reach Redshift — without these tools assigned, it cannot call the MCP server at all.

See [`custom-agents/redshift-support-specialist/README.md`](../../custom-agents/redshift-support-specialist/README.md) for prerequisites, an important behavior note (this agent always runs in active chat, never background), and how to execute the agent once created.

## Step 5 — How to Use the Skill

This skill is intended for Chat — just describe what you need in plain language. The agent matches your request to one of the six capabilities below, discovers the cluster/workgroup itself, and collects diagnostics live through the MCP server. You never need to supply a cluster identifier from memory, an AWS CLI profile, or a CSV export.

### Using the custom agent

If you created the custom agent in Step 4, start there:

1. Start a new chat and ask for the `redshift-support-specialist` custom agent by name (e.g. "Use the redshift-support-specialist agent").
2. Ask it to show what it can do (e.g. "What can you help me with?") and follow its instructions from there.
3. If it ever offers a background-mode option (e.g. for a Detailed Operational Review), explicitly ask it to run interactively in the chat instead — e.g. "Run this interactively in the chat, not in the background." The custom agent's system prompt is designed to always run in the active chat session, but if you see a background-mode prompt anyway, this confirms your intent.

If you're not using the custom agent, the base DevOps Agent Chat with this skill uploaded works the same way — just describe what you need directly, without naming an agent.

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

A companion custom agent system prompt for pairing with this skill lives in [`custom-agents/redshift-support-specialist/`](../../custom-agents/redshift-support-specialist/) — see [Step 4 — Create the Custom Agent](#step-4--create-the-custom-agent) above.

Only `SKILL.md`, `references/`, `assets/`, and `evals/` are part of the [Agent Skills specification](https://agentskills.io/specification) upload package (see packaging command above). `deployment/` is supplementary material for this repository and is excluded from the skill zip.

## Limitations

- **No AWS CLI or CloudWatch access.** Every Redshift interaction goes through the six MCP server tools only (`list_clusters`, `list_databases`, `list_schemas`, `list_tables`, `list_columns`, `execute_query`). Checks that require CloudWatch metrics/alarms, snapshot inventory, SSL/audit-log/parameter-group configuration, or Reserved Instance coverage are reported as "Not Available" rather than guessed — see Capabilities 4 and 5 in `SKILL.md`.
- **Read-only.** `execute_query` runs inside a read-only transaction — the skill never runs INSERT/UPDATE/DELETE/ALTER/DROP/CREATE/GRANT/VACUUM/ANALYZE; it only recommends such statements for the user to run themselves.
- **One query per `execute_query` call.** Diagnostics that need multiple result sets require multiple tool calls; there is no multi-statement/transaction support.
- **No data retention.** Every session collects data fresh; nothing from a prior report or customer is cached or reused across sessions.

## Agent Types

This skill is intended for:

- **Chat** — conversational invocation ("why is this Redshift query slow?", "run a Redshift health check on my cluster").

Select **Generic** at upload time if you want the skill available to all agent types.

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

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
