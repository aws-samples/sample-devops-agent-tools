# B — DNS Firewall Block

## Mechanism

DNS Firewall evaluates at the top of the precedence order, before resolution
completes. A BLOCK action returns NXDOMAIN, NODATA, or an OVERRIDE answer
regardless of what any private hosted zone, endpoint private DNS, or Resolver
rule would have returned. Nothing downstream can recover the query.

Because the block wins first, the symptom is indistinguishable from a missing
record unless you inspect the firewall configuration or the query logs.

## Signature

- A name returns NXDOMAIN immediately, with correct configuration everywhere else
- The name resolves from an instance in a VPC without the rule group associated
- Query logs show a BLOCK action for the name

The second point is the cheapest discriminator when a comparable VPC exists.

## Detection

`dns_simulate_change` reports the `DNS-Firewall-block` trap when a rule-group
change blocks a candidate name. Report the domain list and rule that matched.

## Cross-account limitation — read this before concluding

When a DNS Firewall rule group is shared into the account via AWS RAM, the
association and its rules are visible, but the **domain lists are not**. The
`list_firewall_domains` call is denied cross-account, so the consumer cannot
enumerate which domains are blocked.

The server models this as an `OPAQUE` answer class rather than crashing or
silently reporting the name as unaffected. An opaque firewall rule is treated as
OPAQUE **first**, because a hidden block list may cover any name.

Practical consequence: when a shared rule group is present and opaque, you cannot
prove a name is unblocked from the consumer side. Say so explicitly. Do not
report "not blocked" when the correct statement is "cannot determine from this
account." See `C-cross-account-opaque-constructs`.

## Diagnosis

1. `dns_simulate_effective_config` — identify associated rule groups and whether
   any are opaque.
2. If the rule group is owned locally, read the domain lists and match the name.
3. If opaque, ask the rule-group owner to confirm, or resolve the same name from
   a VPC without the association.
4. `dns_probe_compare` — a fast, uniform NXDOMAIN across all resolvers is
   consistent with a firewall block; per-resolver divergence points elsewhere.

## Remediation

Removing a domain from a block list is a security-relevant change. Confirm with
the rule group's owner rather than recommending removal directly, and prefer a
scoped exception over disabling the rule. If the block is intentional, the
correct outcome is to change the workload, not the firewall.
