# A — FORWARD-vs-PHZ Precedence Collision

## Symptom

An internal zone name **times out** rather than returning NXDOMAIN, while public
names and AWS service FQDNs continue to resolve normally. A private hosted zone
for the affected zone exists and is associated with the VPC, so the name "should"
resolve.

## Cause

A Resolver FORWARD rule and a private hosted zone both claim the same domain. A
specific FORWARD rule **outranks** an associated PHZ in the resolution
precedence order. The query is therefore sent to the FORWARD rule's target
resolver instead of being answered from the PHZ. If that target is unreachable or
does not host the zone, the query times out.

The timeout signature is diagnostic. A missing record yields NXDOMAIN quickly; a
forward to a dead target hangs until it times out.

## Diagnosis

1. `dns_simulate_effective_config` — inventory the VPC's effective configuration
   and look for a FORWARD rule whose domain equals or is a parent of the PHZ zone
   name.
2. `dns_probe_compare` — confirm the timeout, and confirm that names outside the
   contested zone still resolve. That narrows the fault to one zone rather than
   the resolver path as a whole.
3. Check the FORWARD rule's target IPs for reachability from the outbound
   endpoint's subnets, including security group egress on UDP/TCP 53.

## Precedence order (highest to lowest)

1. DNS Firewall (BLOCK or OVERRIDE, applied before resolution completes)
2. Specific FORWARD rule
3. SYSTEM rule
4. VPC endpoint private DNS
5. Associated private hosted zone
6. Service network VPC association `PrivateDnsPreference` gate (AND-ed with
   `privateDnsEnabled`)
7. VPC resolver recursion (default)

The PHZ sits at level 5, below the FORWARD rule at level 2. This ordering is why
the collision resolves in favor of the forward.

## Remediation

Choose one:

- **Narrow the FORWARD rule** so it no longer covers the PHZ zone. Preferred when
  the PHZ is intended to be authoritative.
- **Add a SYSTEM rule** for the specific PHZ zone. A SYSTEM rule at level 3
  outranks the PHZ but is itself outranked by a more specific FORWARD, so verify
  specificity carefully.
- **Separate the domains** so the forward targets a distinct zone (for example,
  forward `onprem.corp` while the PHZ serves `internal.corp`).

## Validate before applying

Run `dns_simulate_change` with the proposed rule modification. Narrowing a
FORWARD rule can un-break this zone while breaking names that legitimately
depended on the broader sweep. See `B-broad-forward-sweep`.
