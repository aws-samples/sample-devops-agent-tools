# A — Mode A: Live Multi-Resolver Comparison

## When to use

- A name resolves differently than expected from an instance.
- The environment runs a custom or hybrid resolver (Active Directory DNS,
  Infoblox, an on-premises forwarder, a local dnsmasq/unbound stub).
- A PrivateLink or VPC endpoint FQDN returns a public IP or NXDOMAIN
  unexpectedly.
- You need ground truth. The EC2, VPC, and Route 53 describe APIs return DNS
  *configuration*. They never return what a name actually resolves to from a
  given subnet right now, or which resolver answered. Mode A does.

## SSM prerequisites

The probe executes inside the instance via SSM Run Command. It requires:

- SSM Agent running, with `AmazonSSMManagedInstanceCore` (or equivalent) on the
  instance role.
- SSM reachability through VPC interface endpoints for `ssm`, `ssmmessages`, and
  `ec2messages`.

An EC2 Instance Connect Endpoint does not satisfy the third requirement. Run
Command works by the agent polling outbound to `ssmmessages` and `ec2messages`;
EICE is an inbound SSH/RDP tunnel and carries no SSM control-plane traffic. An
instance reachable only by EICE shows as `Not connected` in SSM and cannot be
probed.

If SSM is unreachable, report that as the blocker. Do not route around it.

## Workflow

### Step 1 — Establish the precondition

Call `dns_probe_context(account_id, region, instance_id)`. Read:

- `enableDnsSupport` / `enableDnsHostnames` — if support is false, stop and see
  `A-resolver-disabled-precondition`.
- Instance addressing family — determines which resolver addresses can exist.
- DHCP option set `domain-name-servers` — the **VPC-intended** resolver.

### Step 2 — Compare resolvers

Call `dns_probe_compare(account_id, region, instance_id, name)`. With
`include_dhcp_dns=true` (the default) the DHCP-configured resolvers are added
automatically, expanding `AmazonProvidedDNS` to the VPC resolver address
appropriate for the instance's stack. Pass extra `resolvers` only to compare
additional targets, such as a Resolver outbound endpoint IP or an allowlisted
on-premises resolver. Both A and AAAA are probed per resolver.

### Step 3 — Confirm who answered

Read the `hostname.bind` identity line. This distinguishes "the VPC resolver
answered" from "a local stub answered and happened to return the same thing."
Never infer the responder from the answer alone.

### Step 4 — Compare intended vs actual

If the DHCP-handed resolver from Step 1 differs from the instance's actual
`resolv.conf`, the instance is not using the resolver the VPC hands out. That
mismatch is frequently the root cause. See `A-custom-resolver-divergence`.

### Step 5 — Judge against the name category

Classify the name and check the observed winner against the expected winner. See
`A-name-category-classification`.

## What Mode A cannot tell you

- Anything about instances other than the one probed.
- Whether a *future* change is safe — that is Mode B.
- Provider-side configuration of constructs shared into this account. See
  `C-cross-account-opaque-constructs`.
