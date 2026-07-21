# Redshift MCP Server — AWS SAM Application

An [AWS SAM](https://docs.aws.amazon.com/serverless-application-model/)
(CloudFormation transform) template that deploys the standard,
unmodified `awslabs.redshift-mcp-server` PyPI package as an AWS Lambda
function, reachable through an **API Gateway REST API** (`execute-api`)
secured with AWS IAM (SigV4) authorization. This is the endpoint to
register with AWS DevOps Agent's SigV4 MCP-server capability provider —
DevOps Agent's SigV4 auth is documented and tested against `execute-api`
(see [Connecting MCP Servers](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-integrations-and-knowledge-connecting-mcp-servers.html)).

See the parent [`../README.md`](../README.md) for the full architecture explanation.

## Files

| File | Purpose |
|---|---|
| `template.yaml` | The SAM/CloudFormation template — an `AWS::Serverless::Function` resource (with a custom `Makefile` build method) with an `AWS::Serverless::Api` (IAM-authorized) event source, inline IAM policies, and stack outputs. |
| `src/Makefile` | Custom SAM build step (`BuildMethod: makefile`). Installs `uv` and `mcp-proxy` via a plain `pip install --platform manylinux2014_aarch64 --only-binary=:all:` targeting Python 3.13/arm64 — no container required, since every dependency publishes prebuilt manylinux wheels. |
| `src/run.sh` | Lambda function handler (a shell script, invoked via the Lambda Web Adapter's `AWS_LAMBDA_EXEC_WRAPPER`). Starts `mcp-proxy`, which spawns `uvx awslabs.redshift-mcp-server@latest`. |
| `src/requirements.txt` | Python dependencies installed by `src/Makefile` at build time: `uv` (provides `uvx`) and `mcp-proxy`. |

## Prerequisites

- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-install.html)
- Python 3.9+ with `pip` on your build machine (used by `src/Makefile` at
  build time — no Docker or Finch needed; see the parent
  [`../README.md`](../README.md) for why).
- AWS credentials with permission to create IAM roles, Lambda functions,
  API Gateway REST APIs, and (for `--resolve-s3`) an S3 bucket for
  deployment artifacts.

## Deploy

### Quick deploy (no prompts)

This uses the template's default parameter values (see **Parameters**
below) and skips every interactive prompt — one command, nothing to
answer:

```bash
sam build
sam deploy \
  --stack-name redshift-mcp \
  --capabilities CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --region us-east-1 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset
```

`--capabilities CAPABILITY_NAMED_IAM` is always used here (rather than
`CAPABILITY_IAM`) so this same command works whether or not you later set
`CreateDevOpsAgentRole=true` — `CAPABILITY_NAMED_IAM` covers both named and
unnamed IAM resources, so there's no need to branch on which one to pass.

To override any parameter without triggering prompts, add
`--parameter-overrides`:
```bash
sam deploy \
  --stack-name redshift-mcp \
  --capabilities CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --region us-east-1 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides FastMcpLogLevel=DEBUG CallerRoleArn=arn:aws:iam::<account-id>:role/<role-name>
```

### Guided deploy (interactive, saves answers for next time)

If you'd rather be prompted for each value (and have them saved to
`samconfig.toml` for future `sam deploy` runs with no flags at all):

```bash
sam build
sam deploy --guided
```

Note that guided mode does not prompt for `--capabilities` — it defaults
to `CAPABILITY_IAM`, which is insufficient if you answer
`CreateDevOpsAgentRole=true`. If you hit `Requires capabilities:
[CAPABILITY_NAMED_IAM]` after confirming the prompts, re-run:
```bash
sam deploy --capabilities CAPABILITY_NAMED_IAM
```
(Your other answers are already saved from the first guided run, so this
reuses them.)

After deploy, the stack outputs include the MCP endpoint URL:
```
Key                 RedshiftMcpApiUrl
Value                https://<api-id>.execute-api.<region>.amazonaws.com/Prod/mcp
```
`RedshiftMcpApiUrl` is already the full URL including `/mcp` — use it
as-is when registering with AWS DevOps Agent (Service Name =
`execute-api`).

## Parameters

| Parameter | Default | Description |
|---|---|---|
| `FastMcpLogLevel` | `INFO` | Log level for the underlying `awslabs.redshift-mcp-server` process (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `LambdaWebAdapterLayerVersion` | `28` | Version number of the public `awsguru/LambdaAdapterLayerArm64` layer. Check the [adapter's releases](https://github.com/aws/aws-lambda-web-adapter/releases) for newer versions and override with `--parameter-overrides LambdaWebAdapterLayerVersion=<N>`. |
| `CallerRoleArn` | `''` (empty) | Optional ARN of an **existing** IAM role to auto-grant `execute-api:Invoke` (and `lambda:InvokeFunction`) at deploy time (see below). Leave empty to skip and grant access manually later. |
| `CreateDevOpsAgentRole` | `false` | If `true`, this stack creates a **new** IAM role for AWS DevOps Agent to assume, with the trust policy, MCP invoke permission, and Redshift Data API permissions it needs (see "Create a DevOps Agent IAM role" below). Use this instead of `CallerRoleArn` when the role doesn't exist yet. |
| `DevOpsAgentRoleName` | `DevOpsAgentRole-Redshift-support-specialist` | Name for the role created when `CreateDevOpsAgentRole` is `true`. Ignored otherwise. |

## Grant a caller access

**If you already have a role** (e.g. one you created yourself): pass its
ARN as the `CallerRoleArn` parameter (see the non-interactive example
above). The template grants that role invoke access automatically — no
extra step needed after `sam deploy` completes:
- `execute-api:Invoke` on the API Gateway `/mcp` method, via an inline
  policy (`CallerApiInvokePolicy`) attached directly to that role.
- `lambda:InvokeFunction` directly on the Lambda function, required
  because this template's AWS_IAM-authorized API integration invokes the
  Lambda backend using the caller's own IAM identity rather than via API
  Gateway's own service principal.

The stack output `CallerRoleGranted` confirms which role (if any) was
granted.

**If you don't have a role yet** (e.g. setting up AWS DevOps Agent for the
first time): use `CreateDevOpsAgentRole=true` instead — see "Create a
DevOps Agent IAM role" below, which creates the role for you as part of
this same deploy.

**Manually, for additional callers (or if you skipped both options above):**

Both statements below are required (see the note above about why):
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
(Get `<api-id>` from the `RedshiftMcpApiUrl` output, and `<function-name>`
from the `RedshiftMcpFunctionArn` output.) The stack output
`GrantInvokeCommand` gives you this command pre-filled with the actual
API ID and function ARN.

## Grant database-level permissions inside Redshift

Getting invoke access on the API doesn't grant anything inside the Redshift database itself.
Amazon Redshift maps the Lambda's execution role to a database user (`IAMR:<role-name>`,
using the `IAMR:` prefix since it's an IAM role, not an IAM user), and by default that
database user can only see its own queries — not other users' activity that this skill
needs to review.

Two stack outputs give you the exact SQL to run (as a database superuser) on each
cluster/workgroup this skill will query, pre-filled with the actual deployed role name:

- `GrantSysMonitorCommand` — grants the `sys:monitor` role, letting the database user
  see all users' queries in monitoring views (`SYS_QUERY_HISTORY`, `SVL_QLOG`, etc.).
- `GrantTableInfoCommand` — grants `SELECT` on `SVV_TABLE_INFO`, which is superuser-visible
  by default and isn't covered by `sys:monitor`.

Both are safe to re-run; they don't need to be repeated per session since grants persist
against the database user. See the parent [`../README.md`](../README.md#database-level-permissions-inside-redshift)
for the full explanation.

## Create a DevOps Agent IAM role

Connecting this MCP server to an AWS DevOps Agent Agent Space normally
requires manually creating an IAM role for the agent to assume: a trust
policy for the `aidevops.amazonaws.com` service principal, permission to
invoke the API Gateway endpoint, and Redshift Data API permissions. This
template can do all of that for you in one deploy:

```bash
sam deploy \
  --stack-name redshift-mcp \
  --capabilities CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --region us-east-1 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides CreateDevOpsAgentRole=true
```

`CAPABILITY_NAMED_IAM` (not just `CAPABILITY_IAM`) is required here
because the created role has an explicit name (`DevOpsAgentRoleName`) —
this is already the default in the **Quick deploy** command above, so
you only need to add `--parameter-overrides CreateDevOpsAgentRole=true`
to that command rather than retyping the whole thing.

This creates an `AWS::IAM::Role` named `DevOpsAgentRole-Redshift-support-specialist`
(override with `DevOpsAgentRoleName`) with:

- A trust policy allowing `aidevops.amazonaws.com` to assume the role,
  scoped to this account (`aws:SourceAccount`) and region
  (`aws:SourceArn: arn:aws:aidevops:<region>:<account-id>:service/*`).
- An inline policy granting **both** `execute-api:Invoke` on this
  deployment's `/mcp` API Gateway method AND `lambda:InvokeFunction`
  directly on the Lambda function. Both are required: this template's
  AWS_IAM-authorized API integration invokes the Lambda backend using the
  *caller's own IAM identity* (SAM sets the integration's `credentials` to
  a caller-passthrough wildcard) rather than via API Gateway's own service
  principal, so the caller needs direct Lambda invoke rights too, not just
  API Gateway invoke rights. Without `lambda:InvokeFunction`, requests
  fail at the integration layer with "Execution failed due to
  configuration error: Invalid permissions on Lambda function" even
  though the `execute-api:Invoke` check passes.
- An inline policy granting the same Redshift Data API read permissions as
  the Lambda's own execution role (`redshift:DescribeClusters`,
  `redshift-serverless:ListWorkgroups`/`GetWorkgroup`,
  `redshift-data:ExecuteStatement`/`DescribeStatement`/`GetStatementResult`,
  `redshift-serverless:GetCredentials`,
  `redshift:GetClusterCredentialsWithIAM`/`GetClusterCredentials`).

The stack output `DevOpsAgentRoleArn` gives you the ARN to paste into the
Agent Space's MCP server capability provider connection settings. When
registering, use:
- Endpoint URL: the `RedshiftMcpApiUrl` stack output
- Auth method: AWS SigV4
- IAM role: `DevOpsAgentRoleArn` stack output
- AWS Region: this stack's deploy region (e.g. `us-east-1`)
- Service Name: `execute-api`

To grant this role to *additional* callers on future deploys, or if it
already exists and you just want to re-grant access, leave
`CreateDevOpsAgentRole` at its default (`false`) and pass the existing
role's ARN via `CallerRoleArn` instead — the two parameters are
independent, so you won't recreate the role by mistake.

## Local testing before deploying

```bash
sam local invoke RedshiftMcpFunction \
  --event <(echo '{}') \
  --parameter-overrides FastMcpLogLevel=DEBUG
```

Note: `sam local invoke` sends a single synchronous Lambda event and won't
fully exercise the HTTP server behavior the Web Adapter provides — it's
useful for confirming the function initializes without error, but for a
real HTTP round-trip test, deploy and use `../scripts/list_clusters.py`
against the deployed API Gateway endpoint.

## Tear down

```bash
sam delete --stack-name <stack-name>
```
