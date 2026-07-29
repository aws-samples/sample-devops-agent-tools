# Architecture & Design

How the AWS VPC DNS Diagnostics MCP server is put together, why it is one server
rather than two, how the resolution engine works, and where the enforcement
boundaries sit.

---

## 1. System layout

```
DevOps Agent
     │  MCP over Streamable HTTP, SigV4-signed
     ▼
Lambda Function URL  (AuthType: AWS_IAM)
     │
     ▼
Lambda: FastMCP server behind Lambda Web Adapter
     │  execution role holds sts:AssumeRole ONLY
     │
     ├── assume DnsDiagnosticReadOnlyRole ──► Route 53 / Resolver / Profiles /
     │      (Mode B, per target account)       Lattice / EC2 describe + list
     │
     └── assume DnsDiagnosticProbeRole ─────► ssm:SendCommand, scoped to ONE
            (Mode A, per target account)       diagnostic document
                                                     │
                                                     ▼
                                          Target EC2 instance
                                          fixed read-only probe set
```

The central Lambda holds no diagnostic permissions of its own. Every AWS call is
made with credentials from one of two roles deployed in the target account, chosen
by which tool family was invoked.

### Why Lambda Web Adapter and a Function URL

DevOps Agent requires MCP over the Streamable HTTP transport. FastMCP serves that
natively, and Lambda Web Adapter lets the same ASGI app run unmodified in Lambda.
The Function URL provides the HTTPS endpoint with `AuthType: AWS_IAM`, which maps
onto DevOps Agent's documented SigV4 auth path: signing service `lambda`, action
`lambda:InvokeFunctionUrl`. No API Gateway or gateway front end is required.

`AWS_LWA_INVOKE_MODE: response_stream` is set so streaming responses pass through
correctly, and the readiness check targets `/mcp`.

---

## 2. One server, three tool families

| Family | Tools | Credentials | Nature |
| --- | --- | --- | --- |
| Runbooks | `list_sops`, `get_sop` | none | Local filesystem read |
| Mode A | `dns_probe_context`, `dns_probe_compare` | probe role | Live, observed |
| Mode B | `dns_simulate_effective_config`, `dns_simulate_change` | read-only role | Symbolic, predicted |

The two diagnostic modes share one server because they answer two halves of the
same question and are most useful in sequence: Mode B predicts what a change will
do, Mode A confirms what actually happened. Splitting them across two servers
would force an operator to register two endpoints and would separate the
precedence model from the ground truth that validates it.

They are nonetheless isolated where it matters. Each family assumes a different
role, so a read-only simulation never executes on credentials capable of running
a command.

### Runbooks as a tool, not a skill file

Interpretation guidance ships inside the server as 16 markdown runbooks under
`src/sops/`, retrieved at runtime through `list_sops` and `get_sop`. The agent
asks for the procedure it needs instead of carrying all of it in context.

The runbooks are bundled into the deployment package and read from the Lambda
filesystem. There is no S3 bucket and no additional IAM grant. `get_sop`
validates the requested slug against an in-code catalogue, so no filename is ever
constructed from unvalidated caller input; a `realpath` containment check backs
that up, and a test asserts the catalogue and the directory match in both
directions.

They are organized by the shape of the operator's problem rather than by tool:
`Z` for start-here triage, `A` for live diagnosis and safety rules, `B` for
pre-change validation, `C` for cross-cutting concerns. Runbooks cross-reference
one another, so a symptom leads to a mechanism and then to any interacting trap.

---

## 3. Mode A — live observation

### The gap it fills

Describe APIs return configuration. They cannot report what a name resolves to
from a subnet, which resolver answered, what the instance's `resolv.conf` says, or
what the NSS-effective answer is. A DHCP option set states which resolver the VPC
*hands out*; it cannot state which resolver the instance is *using*. An instance
pointing at a local stub, a domain controller, or an on-premises forwarder looks
identical from the control plane.

### Flow

1. `dns_probe_context` reads the VPC attributes (`enableDnsSupport`,
   `enableDnsHostnames`), the instance's addressing family, and the DHCP option
   set's `domain-name-servers`, classifying `AmazonProvidedDNS` versus a custom
   resolver. This is the precondition block: if DNS support is off, no resolution
   diagnosis is meaningful.
2. `dns_probe_compare` sends the probe for each (name, resolver, family)
   combination and assembles an answer matrix. With `include_dhcp_dns=true`
   (default) the DHCP-configured resolvers are added automatically, expanding
   `AmazonProvidedDNS` to the resolver address appropriate for the instance's
   stack.
3. Each probe returns `resolv.conf`, `resolvectl status`, a short-form `dig`
   answer, `dig +stats`, a `hostname.bind` CH TXT identity lookup, and
   `getent hosts`.

The `hostname.bind` lookup identifies which resolver actually answered, which
distinguishes "the VPC resolver answered" from "a local stub answered and returned
the same value." `getent` captures the OS-effective result, which can differ from
every individual `dig` because it follows `resolv.conf` order, `nsswitch.conf`,
and `/etc/hosts`.

### On-instance enforcement boundary

The SSM document, not the server, is the boundary. It accepts three
`allowedPattern`-validated parameters and renders a fixed read-only probe set; the
server sends structured parameters rather than a command string. Server-side
validators are layer 1, the document's `allowedPattern` is an independent layer 2,
and a resource-scoped `ssm:SendCommand` to that single document ARN is layer 3.
The two tool families use separate assumed roles, so Mode B never holds a grant
capable of execution.

The document also declares a `Linux` platform precondition and a 60-second step
timeout, and deliberately omits `set -e` so that an informative non-zero result
(NXDOMAIN, SERVFAIL) does not abort the remaining probes.

---

## 4. Mode B — symbolic prediction

### Effective model

`_build_effective_model` assembles an `EffectiveModel` from the union of
directly-attached constructs and those inherited through an associated Route 53
Profile. Every construct is tagged with its source (`direct` or `profile:<id>`),
because a Profile-sourced construct can be changed by the Profile's owner outside
this account's control.

Constructs modeled: resolver rules (FORWARD and SYSTEM), private hosted zone
associations, DNS Firewall rule groups and their domain lists, interface endpoint
private DNS shadows, and VPC Lattice service network associations with their
`privateDnsEnabled` and `PrivateDnsPreference` flags.

### Resolution precedence

`resolve()` walks seven levels in order and returns the winning construct, its
source, and an answer class:

1. DNS Firewall (BLOCK / OVERRIDE — applied before resolution completes)
2. Specific FORWARD rule
3. SYSTEM rule
4. Interface endpoint private DNS
5. Associated private hosted zone
6. Service network VPC association `PrivateDnsPreference` gate, AND-ed with
   `privateDnsEnabled`
7. VPC resolver recursion (default)

Level 2 sitting above level 5 is why a FORWARD rule and a private hosted zone
claiming the same domain resolves in favor of the forward. That collision
presents as a timeout rather than an NXDOMAIN and is documented in
`A-forward-vs-phz-precedence-collision`.

### Trap detectors

`detect_traps()` runs six detectors that name the *mechanism* of a predicted
breakage rather than only the affected names:

| Detector | Mechanism |
| --- | --- |
| `VPCE-shadow-NXDOMAIN` | Endpoint private DNS shadows a service apex still queried the old way |
| `broad-FORWARD-sweep` | A `.` or broad-suffix FORWARD rule captures AWS FQDNs and PHZ names with no SYSTEM carve-out |
| `flag-AND-mismatch` | `privateDnsEnabled` and `PrivateDnsPreference` combine to leave a custom domain uninstalled |
| `DNS-Firewall-block` | A rule-group change blocks a candidate name |
| `Profile-union-shift` | An association change shifts the effective set in bulk |
| `resolver-disabled` | A DHCP change turns the VPC resolver dark |

### Simulation and ranking

`simulate()` builds the current model, applies the change symbolically via
`apply_change()`, builds the post-change model, and diffs resolution per candidate
name. Any triggered trap escalates severity to high; ties break on query volume.
Profile-sourced deltas are annotated with the propagation window (roughly 300–350
seconds service-side, up to about 900 seconds in the negative-cache worst case).

Candidate names default to those derived from configuration. Volume ranking is an
optional enrichment: the caller may pass a `volumes` map (name to query count) and
the report is weighted by it, but the server does **not** read Resolver query logs
itself. Reading them directly is designed but not implemented, and the read-only
role holds no CloudWatch Logs grant, because the permission is withheld until the
code that would use it exists. Ranking is never required for correctness. Coverage
equals the candidate set, so a name absent from the report is not thereby proven
safe, and the runbooks require stating that limit.

---

## 5. Cross-account model

A consumer account can enumerate shared DNS constructs without being able to read
inside them. Measured behavior:

| Construct | Consumer-side visibility |
| --- | --- |
| Directly associated private hosted zone | fully readable |
| RAM-shared resolver rule | fully readable |
| RAM-shared DNS Firewall rule group | association and rules visible; domain lists **denied** |
| Profile-contained resolver rule | enumerable; `get_resolver_rule` **denied** |
| Profile-contained private hosted zone | enumerable; `get_hosted_zone` **denied** |

No Route 53 Profiles API action exposes a profile's contents to a consumer.
"Enumerable but opaque" is the complete and correct model.

Every per-resource detail read in `_build_effective_model` is therefore wrapped so
a denial yields an `OPAQUE` marker rather than failing the whole model build. The
engine applies two rules: an opaque **firewall** rule is treated as OPAQUE first,
because a hidden block list may cover any name; an opaque **resolver** rule is
OPAQUE only when no concrete rule matched.

This shapes reporting. When a name resolves to OPAQUE the correct statement is
"cannot determine from this account," never "not affected." Where ground truth is
required, Mode A observes the *result* of an opaque construct even when its
configuration cannot be read.

Two implementation notes worth preserving: consumer-side Lattice shadows are
derived from the consumer's own endpoint records rather than by enumerating
provider-side resource configurations, which is denied by design; and the
read-only role's Route 53, Resolver, Profiles, and Lattice grants carry **no**
`aws:ResourceAccount` condition, because those services do not populate that key
and the condition would evaluate false and silently deny. The EC2 grants do
support it and keep the account guard. The Lattice grants name their two APIs
explicitly rather than using `List*`/`Get*`, so a future API carrying one of those
prefixes is not picked up implicitly.

---

## 6. Fail-closed design

| Guard | Behavior |
| --- | --- |
| `ALLOWED_ACCOUNTS` unset, empty, or `*` | Refused at startup in EVERY stage |
| Wildcard allowlists under `STAGE_NAME=prod` | Refused at startup |
| Resolver hostnames with an empty `ALLOWED_RESOLVERS` | All hostnames rejected; literal IPs only |
| Account / region / VPC outside the allowlist | Rejected before any AWS call |
| SSM unreachable | Reported as a blocker; no public-path fallback |
| `enableDnsSupport=false` | Reported as the precondition; resolution diagnosis is not attempted |
| Cross-account detail read denied | `OPAQUE` marker, not a crash and not a false negative |

The resolver allowlist inverts the usual convention deliberately. Elsewhere an
empty allowlist means allow-all; for resolvers it means literal IPs only, so the
comparison feature cannot become an arbitrary-egress primitive through an unvetted
hostname.

---

## 7. Testing

77 tests, no AWS calls. Cross-account denial paths are exercised with fake
sessions that raise the real botocore exceptions.

| Suite | Coverage |
| --- | --- |
| `test_allowlist.py` | Injection safety, structured-parameter probe boundary, DHCP read and classification, allowlist enforcement, opaque-marker handling under denial |
| `test_simulate.py` | Resolution engine across all seven precedence levels, all six trap detectors, severity ranking |
| `test_sops.py` | Runbook catalogue and directory consistency in both directions, retrieval, unknown-slug rejection, path-traversal refusal |
| `test_live_regressions.py` | Defects found only in live AWS validation: the `target_ips` change shape, FORWARD target rendering, and the EC2 reads the probe role needs for DHCP discovery |
| `test_security_review.py` | Guards from the MCP security review: no CloudWatch Logs grants, no VPC Lattice wildcards, granted Lattice APIs match the code, probe-role SSM grants stay within an allowlist, resolver wildcard warns while still refusing hostnames |

CloudFormation fixtures that reproduce these scenarios against live AWS live in
`test-infra/`, including a two-account provider/consumer pair for the
cross-account opacity cases. See "Test Infrastructure" in the README for what
each stack creates and the teardown order.

### Known verification gaps

- `enableDnsSupport=false` is covered by unit test but not demonstrated live:
  disabling it is VPC-wide and would sever SSM to every instance in the VPC,
  including the one needed to observe the effect.
- Link-local resolver reachability varies by instance resolver path. In testing,
  direct queries to the link-local address timed out from instances where the
  VPC+2 address answered reliably. Probe both before concluding the VPC resolver
  is down.
