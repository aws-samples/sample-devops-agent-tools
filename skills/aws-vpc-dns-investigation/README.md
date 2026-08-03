# AWS VPC DNS Investigation

This skill tells the AWS DevOps Agent when to reach for the
[aws-vpc-dns-diagnostics MCP server](../../mcp/aws-vpc-dns-diagnostics-mcp/) and in
what order to use its tools, so a cold symptom like "this name will not resolve"
leads to a consistent investigation instead of an ad hoc one.

## ⚠️ Important Notice

This skill is sample code, not intended for production use without additional
review and testing. Users should validate in a non-production environment first.
It is read-only: it drives observation and simulation tools and takes no action on
your DNS configuration.

## Purpose

The MCP server's bundled runbooks tell the agent how to interpret a DNS result once
it has already decided to look. Nothing tells it when to look, or that the VPC
attribute check has to come before any interpretation. This skill supplies that
activation trigger and the investigation sequence.

The sequence matters because VPC DNS failures are ordered. `enableDnsSupport`
gates the entire VPC resolver, so a resolution result read before that attribute is
checked can be attributed to the wrong cause. Likewise, an answer from an unknown
resolver is not evidence until you know which resolver answered, and agreement
between resolvers is not the same as correctness.

## Key Capabilities

- Recognize VPC DNS symptoms from an operator's description without being told to
  use the skill
- Enforce the precondition order: VPC attributes and host context before any
  interpretation of a resolution result
- Drive live, multi-resolver comparison from inside the affected subnet, including
  resolver identity, rather than resolving from the agent's own vantage point
- Route a proposed DNS change through symbolic simulation before it is recommended
- Load the matching pattern runbook for a signature instead of reasoning from
  general knowledge
- Report cross-account opaque constructs and missing SSM reachability as boundaries
  rather than inferring past them

## Prerequisites

- The `aws-vpc-dns-diagnostics` MCP server registered in your Agent Space with its
  tools allowlisted. The server and its deployment instructions are in this
  repository at [`mcp/aws-vpc-dns-diagnostics-mcp/`](../../mcp/aws-vpc-dns-diagnostics-mcp/)
- The scoped IAM roles deployed in each target account, and those account IDs
  supplied in the server's `AllowedAccounts` parameter. There is no default and a
  wildcard is refused
- For the Mode A live probe tools, the target EC2 instance reachable through SSM:
  interface endpoints for `ssm`, `ssmmessages`, and `ec2messages`, and an instance
  role with `AmazonSSMManagedInstanceCore`
- No additional DevOps Agent role permissions. The agent calls the MCP server,
  which assumes its own scoped roles in the target accounts

## Limitations

- The skill is guidance only. Without the MCP server registered and allowlisted,
  none of the tools it references are callable
- Mode A requires SSM reachability to the target instance. An instance that is not
  SSM-managed can be reasoned about from configuration but not probed
- Cross-account constructs shared with the target account may be enumerable while
  their contents remain opaque. These are reported as unknown, not absent
- Simulation is symbolic. It predicts the effect of a change on resolution and does
  not apply, stage, or validate the change against the live control plane

## Agent Types

- **Chat tasks** - conversational DNS diagnosis and pre-change validation
- **Incident RCA** - automated investigation where a failure may have a DNS cause

## Uploading to AWS DevOps Agent

Register the MCP server first. The skill references its tools by name, and trigger
behavior cannot be validated until those tools are present in the Agent Space.

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html)
in your Agent Space, import this skill directly from the repository. In the DevOps
Agent web app, go to Settings → Add Skill → Import from repository, then point to
the `skills/aws-vpc-dns-investigation` directory.

**Option B: Upload as a zip file**

1. Zip the directory, including only allowed extensions:

   ```bash
   cd skills
   zip -r aws-vpc-dns-investigation.zip aws-vpc-dns-investigation/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
   ```

2. In the AWS DevOps Agent web app, go to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the zip file.
5. Select the agent types: **Chat tasks** and **Incident RCA**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Assign the skill to the `CHAT` and `INCIDENT_RCA` agent types. See
[Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end).

## How to Use This Skill

### Chat

- "secretsmanager.us-east-1.amazonaws.com returns NXDOMAIN from this instance but works from another one."
- "Our app is reaching a public IP for a service we put behind an interface endpoint."
- "Would enabling private DNS on this VPC endpoint break anything?"
- "What would a Resolver FORWARD rule for '.' pointing at on-prem do to the names that resolve today?"
- "Show me the effective DNS configuration for this VPC, including anything inherited from a Route 53 Profile."

### Investigation

- "An application started failing after a network change last night. Resolution looks wrong from the subnet."
- "Half our instances can reach the internal API by hostname and half cannot."
- "A service endpoint stopped resolving after we associated a Route 53 Profile."
- "IPv6 clients get a different answer than IPv4 clients for the same name."

## Learn More

- [aws-vpc-dns-diagnostics MCP server](../../mcp/aws-vpc-dns-diagnostics-mcp/README.md)
- [Architecture](../../mcp/aws-vpc-dns-diagnostics-mcp/docs/ARCHITECTURE.md)
- [AWS DevOps Agent Skills documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html)
