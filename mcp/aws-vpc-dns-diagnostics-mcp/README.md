# AWS VPC DNS Diagnostics MCP

> **⚠️ Proof of Concept (POC):** This project is sample code and is not intended
> for production use without additional review and testing. Validate it in a
> non-production account before using it with production workloads.

> ⚠️ This MCP server is designed exclusively for integration with AWS DevOps
> Agent via Streamable HTTP + SigV4. It is NOT compatible with local MCP clients
> (Kiro, Cursor, VS Code) that use stdio transport.

MCP server for AWS DevOps Agent that diagnoses VPC DNS resolution two ways no
describe API can: by observing what a name **actually** resolves to from inside a
subnet, and by predicting what a proposed DNS change **would break** before it is
applied.

Mode A (`dns_probe_*`) runs live, comparative, multi-resolver DNS resolution
inside an EC2 instance via SSM Run Command. It returns what a name resolves to
from that subnet, which resolver answered (`hostname.bind`), and how a
custom or hybrid resolver's answer differs from the VPC resolver.

Mode B (`dns_simulate_*`) is symbolic, read-only pre-change validation. It
predicts which currently-resolving names a proposed DNS control-plane change
would break: endpoint private DNS, a Resolver rule, a private hosted zone, DNS
Firewall, or a Route 53 Profile.

`list_sops` and `get_sop` serve 16 diagnostic runbooks at runtime, carrying the
decision trees, precedence model, and reporting rules for interpreting results.

> **Want to understand the internals?** See [Architecture & Design](docs/ARCHITECTURE.md)
> for the component layout, the resolution precedence engine, the trap detectors,
> and the security model.

---

## Why not just call the AWS APIs?

The EC2, VPC, and Route 53 describe APIs return DNS **configuration**. They never
return:

- what a name actually resolves to from a given subnet right now
- which resolver answered
- the instance's in-OS resolver configuration and NSS-effective answer
- whether a resolver is reachable from that subnet at all

Those are Mode A. Separately, no single describe call produces a precedence-aware,
Profile-union-aware impact diff of a proposed change. That is Mode B. This server
is the enforcement and composition layer over those primitives, not a thin
wrapper.

---

## Prerequisites

### 1. AWS SAM CLI

```bash
brew install aws-sam-cli        # macOS
# or: pip install aws-sam-cli
```

### 2. Python 3.12

The Lambda runtime is `python3.12`. A matching local interpreter is recommended
for running the tests.

### 3. AWS credentials

You need permissions to create IAM roles, Lambda functions, Lambda layers, a
Lambda Function URL, and SSM documents.

```bash
aws configure
# or: aws sso login --profile your-profile
export AWS_PROFILE=your-profile
```

### 4. SSM reachability on target instances (Mode A only)

Mode A executes inside an instance via SSM Run Command. Each target instance
needs:

- SSM Agent running (default on Amazon Linux 2023 AMIs)
- `AmazonSSMManagedInstanceCore` (or equivalent) on its instance profile
- A private path to SSM: interface endpoints for `ssm`, `ssmmessages`, and
  `ec2messages`

All three interface endpoints are required. An EC2 Instance Connect Endpoint is
not a substitute: Run Command works by the SSM Agent polling **outbound** to
`ssmmessages` and `ec2messages`, whereas EICE is an **inbound** interactive
SSH/RDP tunnel that carries no SSM control-plane traffic. An instance with EICE
but no path to those services reports `ConnectionStatus: Not connected` in SSM,
and `SendCommand` fails. EICE is useful alongside these endpoints as a
break-glass path for a human to inspect an instance, but Mode A cannot run on it.

The server reports unreachable SSM as a blocker rather than falling back to a
public path.

---

## Deployment

Two stacks. The central stack hosts the MCP Lambda; the scoped-roles stack is
deployed once per target account.

### Step 1 — Deploy the central MCP Lambda

```bash
sam build
sam deploy --guided
```

Note the `FunctionRoleArn` output. The scoped roles must trust it.

| Parameter | Purpose | Default |
| --- | --- | --- |
| `StageName` | `dev`, `staging`, or `prod`. Wildcard allowlists are refused when `prod`. | `prod` |
| `AllowedAccounts` | Account IDs the tools may inspect | **required, no default** |
| `AllowedRegions` | Regions the tools may operate in | `*` (dev only) |
| `AllowedVpcs` | VPC IDs the tools may target | `*` (dev only) |
| `AllowedResolvers` | Extra resolver IPs/hostnames the probes may query | `*` (dev only) |
| `DiagnosticDocumentName` | The single SSM document the probe role may send | `dns-diagnostic-probe` |
| `ProbeRoleArnPattern` | Per-account Mode A role ARN pattern | `arn:aws:iam::*:role/DnsDiagnosticProbeRole` |
| `ReadOnlyRoleArnPattern` | Per-account Mode B role ARN pattern | `arn:aws:iam::*:role/DnsDiagnosticReadOnlyRole` |

`AllowedAccounts` has no default and does not accept `*`. It is the boundary that
stops the server assuming a role into an arbitrary account, so it must be stated
explicitly and the server refuses to start without it in **every** stage, not
only `prod`. Unset, empty, and `*` are all rejected at import.

Set every `Allowed*` parameter explicitly for anything beyond local testing. With
`StageName=prod`, the server refuses to start if any allowlist is `*`.

### Step 2 — Deploy scoped roles in each target account

```bash
aws cloudformation deploy \
  --template-file scoped-roles.yaml \
  --stack-name dns-diagnostics-scoped-roles \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides CentralFunctionRoleArn=<FunctionRoleArn from step 1>
```

This creates the read-only role, the probe role, and the diagnostic SSM document.

The two stacks reference each other, so deploy the central stack first for its
`FunctionRoleArn`. The `*RoleArnPattern` values use stable role **names**, so they
can be set up front.

### Step 3 — Register with DevOps Agent

Register the MCP endpoint using **AWS SigV4** auth. The endpoint is the
`MCPEndpointUrl` output with `/mcp` appended, because FastMCP serves the
Streamable HTTP transport at `/mcp` rather than at the root. A POST to the bare
Function URL returns 404, and `/mcp/` returns a 307 redirect that invalidates the
request signature, so use `/mcp` exactly.

Registration is account-level: the server is registered once per AWS account and
then shared with individual Agent Spaces, which select which tools they need.

| Setting | Value |
| --- | --- |
| Service type | `mcpserversigv4` |
| Endpoint | `MCPEndpointUrl` output from step 1, with `/mcp` appended |
| Region | The region the function is deployed in |
| Service name | `lambda` |
| IAM role | A role trusting `aidevops.amazonaws.com` with `lambda:InvokeFunctionUrl` and `lambda:InvokeFunctionWithResponseStream` on the function |

#### Option A — DevOps Agent console

1. Open the DevOps Agent console and go to **Capability Providers**.
2. Choose **Register MCP Server**.
3. **MCP server details**: enter a name, and the `MCPEndpointUrl` output from
   step 1 with `/mcp` appended as the **Endpoint URL**.
4. **Authorization flow**: select **AWS SigV4**.
5. **Authorization configuration**:
   - **Configure IAM role**: select an existing role, or follow the console's
     instructions to create one. The role must trust
     `aidevops.amazonaws.com` (see the trust policy below).
   - **AWS Region**: the region the function is deployed in.
   - **Service Name**: `lambda`.
6. **Review and submit.** DevOps Agent validates the connection by calling the
   MCP `initialize` and `tools/list` methods against your endpoint.

#### Option B — AWS CLI

```bash
aws devops-agent register-service \
  --service mcpserversigv4 \
  --service-details '{
    "mcpserversigv4": {
      "name": "aws-vpc-dns-diagnostics",
      "endpoint": "<MCPEndpointUrl>/mcp",
      "authorizationConfig": {
        "region": "<region>",
        "service": "lambda",
        "mcpRoleArn": "<role-arn>"
      }
    }
  }'
```

Then associate the returned `serviceId` with your Agent Space.

#### IAM role for SigV4 signing

DevOps Agent assumes this role in your account to sign requests to the endpoint.
The trust policy needs confused-deputy conditions:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "aidevops.amazonaws.com" },
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": { "aws:SourceAccount": "ACCOUNT_ID" },
      "ArnLike": { "aws:SourceArn": "arn:aws:aidevops:REGION:ACCOUNT_ID:service/*" }
    }
  }]
}
```

Attach only the permission needed to invoke the endpoint:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "lambda:InvokeFunctionUrl",
    "Resource": "arn:aws:lambda:REGION:ACCOUNT_ID:function:aws-vpc-dns-diagnostics-mcp-STAGE",
    "Condition": { "StringEquals": { "lambda:FunctionUrlAuthType": "AWS_IAM" } }
  },
  {
    "Effect": "Allow",
    "Action": "lambda:InvokeFunctionWithResponseStream",
    "Resource": "arn:aws:lambda:REGION:ACCOUNT_ID:function:aws-vpc-dns-diagnostics-mcp-STAGE"
  }]
}
```

Both actions are required. The Function URL is created with
`InvokeMode: RESPONSE_STREAM` so the transport can stream SSE, and the streaming
invoke path is authorized by `lambda:InvokeFunctionWithResponseStream`, which is a
separate action from `lambda:InvokeFunctionUrl`. Granting only the latter leaves
the streaming call an implicit deny. Note that
`lambda:InvokeFunctionWithResponseStream` does not accept the
`lambda:FunctionUrlAuthType` condition key, so it is a separate statement.

Verify both before registering, rather than assuming:

```bash
for action in lambda:InvokeFunctionUrl lambda:InvokeFunctionWithResponseStream; do
  aws iam simulate-principal-policy \
    --policy-source-arn <role-arn> \
    --action-names "$action" \
    --resource-arns arn:aws:lambda:REGION:ACCOUNT_ID:function:aws-vpc-dns-diagnostics-mcp-STAGE \
    --query 'EvaluationResults[0].{Action:EvalActionName,Decision:EvalDecision}'
done
```

Both must report `allowed`. An `implicitDeny` here surfaces at registration as an
opaque `403 Forbidden` from the Function URL, with no invocation recorded in the
function's CloudWatch log group.

### Step 4 — Configure tools in your Agent Space

After registering at the account level, choose which tools each Agent Space may
use:

1. In the DevOps Agent console, select your Agent Space.
2. Go to the **Capabilities** tab.
3. Select the registered `aws-vpc-dns-diagnostics` MCP server.
4. Choose **Select specific tools**, **not** *Allow all tools*.
5. Allowlist only the tools that Agent Space needs, then choose **Add**.

Allowlist the minimum set for the job. Two useful groupings:

| Use case | Allowlist |
| --- | --- |
| Pre-change validation only (no instance execution) | `list_sops`, `get_sop`, `dns_simulate_effective_config`, `dns_simulate_change` |
| Full diagnosis, including live probes | all six tools |

The first grouping is worth preferring where it is sufficient, because it excludes
both `dns_probe_*` tools and therefore never exercises the probe role that holds
`ssm:SendCommand`. Tool-level allowlisting is enforced by the Agent Space and is
independent of this server's own allowlists.

---

## Security Model

### Deviations from the reference MCP pattern

The common reference pattern for a DevOps Agent MCP server is a Lambda deployed
**inside a VPC** that reads credentials from **Secrets Manager** to reach a data
resource such as RDS, Redshift, or OpenSearch. This server deviates on both
points, deliberately. Both deviations reduce the amount of sensitive material and
network surface involved, rather than working around a constraint.

**No Secrets Manager, because there are no credentials.** The reference pattern
needs Secrets Manager because it holds a database username and password. This
server authenticates entirely with IAM: the central Lambda's execution role holds
only `sts:AssumeRole`, and it assumes one of two scoped roles per call. There is
no secret to store, rotate, retrieve, or leak into a tool response. The
credential path the requirement protects does not exist here. Adding Secrets
Manager would mean inventing a secret in order to store one.

**Lambda runs outside a VPC, because there is no in-VPC data resource.** The
reference pattern attaches the Lambda to a VPC to reach a private database
endpoint. This server talks only to AWS control-plane APIs (SSM, EC2, Route 53,
Route 53 Resolver, Route 53 Profiles, VPC Lattice) and never opens a connection to
a customer data resource. The private-path requirement applies to the **target
instances**, not the Lambda: Mode A reaches them through SSM, which requires
`ssm`, `ssmmessages`, and `ec2messages` interface endpoints in the target VPC,
and the server reports unreachable SSM as a blocker rather than falling back to a
public path.

Putting the Lambda in a VPC would add NAT or interface endpoints purely so it
could keep reaching the same public AWS API endpoints, with no reduction in what
it can touch. If your environment requires the MCP endpoint itself to be
privately reachable, that is a separate concern addressed by DevOps Agent's
private connection feature rather than by the function's own VPC attachment.

### Read-only by construction

Mode A executes on an instance, so the boundary that matters is what it is *able*
to execute. This server does not send a command string. It sends three structured
parameters to one purpose-built SSM document that renders a fixed probe set:

| Parameter | Constraint |
| --- | --- |
| `Name` | `allowedPattern` limits it to DNS labels (`[A-Za-z0-9_-]`, dot-separated), max 253 chars |
| `Resolver` | `allowedPattern` limits it to `[A-Za-z0-9_.:-]`, max 253 chars |
| `Family` | `allowedValues: [A, AAAA]` |

The probe set is `cat /etc/resolv.conf`, `resolvectl status`, three `dig`
queries, and `getent hosts`. Every command reads. None writes, installs, captures
traffic, or produces an artifact. The set is fixed in the document and cannot be
extended by a caller.

Three independent layers enforce this:

1. **Server-side validators** reject shell metacharacters, require `Resolver` to
   be a literal IP or an explicitly allowlisted hostname, and constrain `Family`
   to an enum.
2. **Document `allowedPattern`** re-validates every parameter inside SSM,
   independently of the server. A caller reaching SSM directly still cannot pass
   a value containing a quote, semicolon, backtick, pipe, space, or newline.
3. **IAM** grants the probe role exactly one privileged action:
   `ssm:SendCommand`, resource-scoped to this one document ARN. It cannot send
   `AWS-RunShellScript` or any other document.

Because the reachable command set is fixed, read-only, and produces no artifact,
Mode A does not gate on human approval. The `Resolver` allowlist is deliberately
fail-closed: with `ALLOWED_RESOLVERS` unset, only literal IPs are accepted and
every hostname is rejected, so the comparison feature cannot be turned into an
arbitrary-egress primitive via an unvetted hostname.

Mode B holds no `ssm:SendCommand` grant at all. It runs on a separate read-only
role, so a simulation call can never ride on credentials capable of executing
anything.

### Safety — what this server can and cannot do

**On a target instance (Mode A), the reachable command set is fixed:**

- ✅ `cat /etc/resolv.conf`: the instance's configured resolvers
- ✅ `resolvectl status`: systemd-resolved state, when present
- ✅ `dig <name> <A|AAAA> @<resolver>`: the answer, short form
- ✅ `dig <name> <A|AAAA> @<resolver> +stats`: full response with flags and timing
- ✅ `dig hostname.bind CH TXT @<resolver>`: which resolver actually answered
- ✅ `getent hosts <name>`: the OS-effective answer through NSS

- ❌ No arbitrary or caller-supplied commands: the server sends parameters, never a command string
- ❌ No writes, installs, package operations, or service restarts
- ❌ No packet capture, no file uploads, no artifacts produced
- ❌ No reads outside the four commands above (no arbitrary file reads)
- ❌ No other SSM document: `ssm:SendCommand` is resource-scoped to one document ARN
- ❌ No `AWS-RunShellScript`

**In the AWS control plane, both tool families are read-only:**

- ✅ `Describe*` / `Get*` / `List*` on EC2, Route 53, Route 53 Resolver, Route 53 Profiles, VPC Lattice
- ❌ No CloudWatch Logs grant: query-log volume enrichment is designed but not implemented, so the permission is deliberately absent
- ❌ No mutating API of any kind: no create, modify, associate, or delete
- ❌ Mode B holds no `ssm:SendCommand` grant at all
- ❌ No account, region, VPC, or resolver outside the configured allowlists
- ❌ No startup at all without an explicit `ALLOWED_ACCOUNTS` list, in any stage
- ❌ No startup at all when `StageName=prod` and any allowlist is a wildcard

**Inputs are constrained before they reach anything:**

- ✅ `Name`: DNS labels only (`[A-Za-z0-9_-]`, dot-separated), max 253 chars
- ✅ `Resolver`: a literal IPv4/IPv6 address, or a hostname explicitly listed in `ALLOWED_RESOLVERS`
- ✅ `Family`: `A` or `AAAA` only
- ✅ `slug` (for `get_sop`): must match an in-code catalogue entry
- ❌ No shell metacharacters: rejected by the server, then again by the document's `allowedPattern`
- ❌ No hostname resolvers when `ALLOWED_RESOLVERS` is unset (fail-closed; literal IPs only)
- ❌ No path traversal in `get_sop`: allowlist lookup, plus a `realpath` containment check

### Never use a wildcard resolver allowlist

`ALLOWED_RESOLVERS` is the one allowlist whose wildcard widens the blast radius
beyond read-only. With it set to `*`, `dns_probe_compare` accepts any literal
resolver IP and queries it from the target instance. That is a caller-directed
outbound DNS query, and the only outbound path in this server a caller can point
somewhere new. The queried name is DNS-charset only and capped at 253 characters, so the
channel is narrow, but it is real.

`StageName=prod` refuses to start on a wildcard. That is not sufficient on its
own: **set an explicit `ALLOWED_RESOLVERS` list in any deployment reachable by
DevOps Agent, whatever the stage.** The server logs a warning at startup when the
wildcard is active.

Hostnames are refused unless explicitly listed, in every stage. That is
deliberate. It prevents the resolver-comparison feature becoming an
arbitrary-egress primitive via an unvetted hostname.

### Data trust boundary — probe output is untrusted

Mode A returns instance output verbatim: `/etc/resolv.conf`, `resolvectl status`,
`dig` responses, and `getent` results. Any of it can be attacker-influenced. A DNS
TXT record or a poisoned `resolv.conf` comment can carry text shaped like
instructions to an agent.

This server does not interpret, execute, or act on that content. It passes it
through as data, and holds no tool that could act on an injected instruction: every
tool is read-only, and with an explicit resolver allowlist there is no
caller-directable outbound channel. A consuming agent should nonetheless treat
probe output as untrusted input rather than as trustworthy diagnostic narration.

### Controls

| Control | Default |
| --- | --- |
| Authentication | SigV4 (`AuthType: AWS_IAM`) on the Function URL — always on |
| Central Lambda role permissions | `sts:AssumeRole` only; holds no diagnostic permissions |
| Credential scoping | Per tool family; Mode B's role never holds `ssm:SendCommand` |
| Probe execution surface | One SSM document, fixed read-only probe set |
| `ssm:SendCommand` scope | Resource-scoped to that one document ARN |
| Account allowlist | Required in every stage; unset, empty, and `*` all refuse startup |
| Region / VPC allowlists | Enforced in one place, before any AWS call |
| Resolver allowlist | Fail-closed: literal IPs only unless a hostname is listed |
| Production enforcement | Wildcard allowlists refused at startup when `StageName=prod` |
| SSM path | `ssm` + `ssmmessages` + `ec2messages` interface endpoints required; no public-path fallback |
| Cross-account reads | Denials become `OPAQUE` markers, never a crash or a false negative |

### Cross-account behavior

Constructs shared via AWS RAM or contributed through a Route 53 Profile may be
enumerable but opaque to a consumer account. Shared DNS Firewall domain lists,
profile-contained resolver rules, and profile-contained private hosted zones all
deny their detail reads cross-account. The server models these as `OPAQUE` rather
than crashing or silently reporting the name as unaffected, and an opaque firewall
rule is evaluated first because a hidden block list may cover any name.

The practical consequence is reported honestly: when a construct is opaque, the
answer is "cannot determine from this account," not "not affected."

---

## Tools

| Tool | Mode | Purpose |
| --- | --- | --- |
| `list_sops` | — | Catalogue of the 16 diagnostic runbooks with one-line purposes |
| `get_sop` | — | Full text of one runbook by slug |
| `dns_probe_context` | A | VPC-attribute precondition, instance addressing family, DHCP-configured resolvers |
| `dns_probe_compare` | A | Per-resolver, per-family answer matrix from inside the instance |
| `dns_simulate_effective_config` | B | The VPC's effective config (direct + Profile-inherited), source-tagged |
| `dns_simulate_change` | B | Predicted per-name impact of a proposed change, with traps and severity |

### Runbooks

The server ships its own interpretation guidance rather than relying on preloaded
instructions. Slug prefixes: `Z` start-here triage, `A` live diagnosis and safety
rules, `B` pre-change validation, `C` cross-cutting concerns. Call `list_sops`
for the catalogue, or `get_sop("Z-general-triage")` for a vague symptom.

### Agent workflow

```
list_sops → get_sop(Z-general-triage) → dns_probe_context → dns_probe_compare
                                      ↘ dns_simulate_effective_config → dns_simulate_change
```

---

## Usage Examples

### Live resolution divergence

```
An app on i-0abc123def in us-east-1 is connecting to the wrong database host.
DNS looks correct in the console. Find out what the instance actually resolves.
```

### Pre-change validation

```
We want to enable private DNS on the Secrets Manager endpoint in vpc-0abc123.
What would that break?
```

### Hybrid DNS troubleshooting

```
After adding a '.' forwarding rule to our on-prem resolver, some AWS service
endpoints stopped resolving in vpc-0abc123. Figure out which ones and why.
```

---

## Structure

```
aws-vpc-dns-diagnostics-mcp/
├── README.md
├── LICENSE                    # Apache-2.0
├── template.yaml              # SAM: central MCP Lambda (assume-role only)
├── scoped-roles.yaml          # Per target account: both scoped roles + SSM document
├── docs/
│   └── ARCHITECTURE.md
├── ssm-document/
│   └── dns-diagnostic-probe.yaml   # Standalone copy of the on-instance probe document
├── src/
│   ├── server.py              # FastMCP server: all six tools
│   ├── dns_model.py           # Mode B: effective model + resolution engine + trap detectors
│   ├── run.sh                 # Lambda Web Adapter entry point
│   └── sops/                  # 16 runbooks, bundled into the deployment package
├── layers/dependencies/       # fastmcp, boto3
├── tests/
│   ├── test_allowlist.py      # Injection safety, probe boundary, DHCP read, allowlists
│   ├── test_simulate.py       # Resolution engine, trap detectors, severity ranking
│   └── test_sops.py           # Runbook catalogue integrity and path-traversal safety
└── test-infra/                # CFN fixtures for reproducing diagnostic scenarios
```

---

## Test

```bash
uv run --with fastmcp --with boto3 --with pytest pytest tests/ -q
```

77 tests: injection safety and the probe parameter boundary, the Mode B
resolution engine and all six trap detectors, cross-account opaque-marker
handling, runbook catalogue integrity, regressions found in live validation, and
guards asserting the IAM grants cannot silently re-widen.

---

## Local Testing

Run the server locally to verify the MCP handshake and tool surface before
deploying. FastMCP serves Streamable HTTP on port 8000 at `/mcp`, the same path
Lambda Web Adapter targets for its readiness check.

```bash
cd src
ALLOWED_ACCOUNTS=111122223333 ALLOWED_REGIONS=us-east-1 STAGE_NAME=dev \
  uv run --with fastmcp --with boto3 python server.py
```

In another shell, initialize a session and capture the session ID:

```bash
SID=$(curl -s -D - -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
       "protocolVersion":"2025-06-18","capabilities":{},
       "clientInfo":{"name":"local","version":"1.0"}}}' \
  | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}')
echo "$SID"
```

A returned `mcp-session-id` confirms Streamable HTTP. Complete the handshake, then
list the tools:

```bash
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

All six tools should appear. Fetch a runbook to confirm the bundled SOPs are
readable without any AWS access:

```bash
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{
       "name":"get_sop","arguments":{"slug":"Z-general-triage"}}}'
```

The `dns_probe_*` and `dns_simulate_*` tools need real AWS credentials and a
deployed scoped role, so they are best exercised after deployment.

### Testing the deployed endpoint

The Function URL requires SigV4, so plain `curl` returns 403. Sign the request:

```bash
uv run --with boto3 --with requests --with requests-auth-aws-sigv4 python - <<'PY'
import json, requests
from requests_auth_aws_sigv4 import AWSSigV4
url = "<MCPEndpointUrl>"  # from the SAM output; append /mcp if not present
r = requests.post(url,
    auth=AWSSigV4('lambda', region='us-east-1'),
    headers={'Content-Type': 'application/json',
             'Accept': 'application/json, text/event-stream'},
    data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "verify", "version": "1.0"}}}),
    timeout=60)
print(r.status_code, r.headers.get('mcp-session-id'))
PY
```

An unsigned request returning 403 and a signed request returning 200 with an
`mcp-session-id` together confirm the endpoint is correctly gated and speaking
Streamable HTTP.

---

## Test Infrastructure

`test-infra/` holds CloudFormation fixtures that reproduce the diagnostic
scenarios end to end, including a custom split-horizon resolver, an interface
endpoint with private DNS, a private hosted zone, a Resolver outbound endpoint
with FORWARD and SYSTEM rules, a DNS Firewall rule group, and a two-account
provider/consumer pair for the cross-account opacity cases.

These fixtures create billable resources. A Resolver outbound endpoint is the
main cost driver. Tear down in reverse order (`03`, then `02`, then `01`) when
finished.

---

## Cleanup

```bash
sam delete                                                   # central stack
aws cloudformation delete-stack --stack-name dns-diagnostics-scoped-roles   # per target account
```

Deregister the MCP server from your DevOps Agent space as well.

---

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE)
file.
