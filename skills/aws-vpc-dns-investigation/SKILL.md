---
name: aws-vpc-dns-investigation
description: Use this skill when a name is not resolving as expected inside a VPC, or before applying a DNS control-plane change. Activate on symptoms such as NXDOMAIN or SERVFAIL from an EC2 instance, a hostname resolving to a public address when a private endpoint was expected, an AWS service endpoint that stopped resolving after a VPC endpoint or Route 53 change, an application reaching the wrong IP, resolution that works from one instance but not another, IPv6 or dualstack resolution differences, a suspected on-premises forwarding or hybrid DNS problem, or a request to check whether enabling private DNS, adding a Resolver rule, associating a private hosted zone, attaching DNS Firewall, or associating a Route 53 Profile would break anything. It drives the aws-vpc-dns-diagnostics MCP server to observe live resolution from inside the subnet and to simulate a proposed change before it is applied.
metadata:
  author: ddericco
  version: "1.0.0"
  aws-devops-agent-skills.agent-types: "Chat tasks, Incident RCA"
  aws-devops-agent-skills.aws-services: "Amazon VPC, Amazon Route 53, Amazon EC2, AWS Systems Manager"
  aws-devops-agent-skills.technical-domains: "Networking"
---

# Investigate VPC DNS Resolution

Use the tools on the connected `aws-vpc-dns-diagnostics` MCP server. Start by calling
`list_sops`, then `get_sop` with slug `Z-general-triage` to load the triage decision
tree, and follow the runbook it returns. The runbooks are the authoritative
procedure; this skill decides when to engage and in what order.

## Establish preconditions before interpreting any result

Call `dns_probe_context` first. A resolution result means nothing until you know
whether the VPC resolver is even answering.

`enableDnsSupport` gates the entire VPC resolver. When it is false, neither the
`.2` address nor the IPv6 resolver answers at all, and every downstream symptom is
explained by that one attribute. The same call returns the instance's address
family and the VPC DHCP option set's `domain-name-servers`, which is the resolver
the VPC intends the instance to use. Load `A-resolver-disabled-precondition` when
the attribute is false.

## Observe what actually resolves

For a live symptom, call `dns_probe_compare` with the failing name. It runs an
allowlisted probe set inside the instance through SSM and returns each resolver's
answer alongside the resolver's own identity from `hostname.bind`, so you learn
which resolver answered rather than assuming. The VPC DHCP resolver is added
automatically, so a custom or hybrid resolver is compared against the VPC resolver
without you looking it up first.

Compare the instance's actual `/etc/resolv.conf` against the DHCP option set from
`dns_probe_context`. A mismatch means the instance is not using the resolver the
VPC hands out, which is a different root cause from a misconfigured rule.

Judge answers by name category, not by whether resolvers agree. Two resolvers
returning the same wrong answer is still a failure, and a divergence can be
correct. Load `A-name-category-classification` before concluding.

## Validate a change before it is applied

When the request is whether a change is safe, call `dns_simulate_effective_config`
to get the VPC's effective configuration, which is the union of directly attached
resources and anything inherited through an associated Route 53 Profile, with each
construct tagged by its source. Then call `dns_simulate_change` with the proposed
change to get a per-name impact report. This is symbolic and read-only; it predicts
breakage without touching the control plane.

Never recommend applying one of these changes without simulating it first. A broad
FORWARD rule, enabling private DNS on an interface endpoint, or a Profile
association can silently redirect names that currently resolve correctly.

## Load the matching pattern runbook

When a signature appears in the output, retrieve the runbook for it with `get_sop`
rather than reasoning from first principles. Available patterns include custom or
hybrid resolver divergence, FORWARD versus private hosted zone precedence
collisions, address-family divergence, VPC endpoint shadow NXDOMAIN, broad FORWARD
sweep, DNS Firewall blocks, the `privateDnsEnabled` and `PrivateDnsPreference`
flag-AND mismatch, and Route 53 Profile propagation timing. Call `list_sops` for the
current catalogue and exact slugs.

## Report honestly

All tools are read-only observation and simulation. Do not modify, delete, or
create DNS resources as part of this skill; produce the diagnosis and the
recommended change, and leave application to the operator.

Cross-account constructs shared with the target account may be enumerable but
opaque, and the tools mark them as such. Report an opaque construct as unknown
rather than treating it as absent. Load `C-cross-account-opaque-constructs` and
`C-limitations-and-boundaries` and state the boundaries to the operator instead of
inferring past them.

Requires the aws-vpc-dns-diagnostics MCP server to be registered in the Agent Space
with its tools allowlisted. The server is in this repository at
`mcp/aws-vpc-dns-diagnostics-mcp/`. Mode A tools additionally require the target
instance to be reachable through SSM. If the server is not registered or SSM is
unreachable, report that as the blocker rather than guessing at the resolution
path.
