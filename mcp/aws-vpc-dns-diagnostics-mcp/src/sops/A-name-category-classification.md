# A — Name-Category Classification (judge correctness, not agreement)

Divergence between resolvers is not inherently a fault. Classify the name first,
then check the observed winner against the expected winner.

## Decision table

| Category | Expected winner | Fault condition |
| --- | --- | --- |
| AWS service FQDN, VPC endpoint present | resolver forwards `amazonaws.com` to the VPC resolver, which returns the endpoint ENI private IP | a custom resolver returns a **public** IP (endpoint bypassed) |
| AWS service FQDN, no VPC endpoint | public IP | swept to on-premises or NXDOMAIN (over-broad FORWARD rule) |
| Private hosted zone / VPC-internal | private IP from the PHZ | custom resolver NXDOMAINs because it does not forward the PHZ zone back to the VPC resolver |
| On-premises / corporate zone | the custom or on-premises resolver answers; the VPC resolver NXDOMAIN is **correct** | the VPC resolver leaks an answer, **or** the custom resolver NXDOMAINs |
| Public | all resolvers agree | disagreement indicates split-horizon, a stale cache, or hijack |

## How to classify

1. **AWS service FQDN** — matches an AWS service endpoint pattern such as
   `<service>.<region>.amazonaws.com`. Determine whether an interface endpoint
   for that service exists in the VPC, and whether its private DNS is enabled;
   that decides which of the two AWS rows applies.
2. **Private hosted zone name** — falls within the zone name of a PHZ associated
   with the VPC.
3. **On-premises / corporate zone** — falls within a zone targeted by a FORWARD
   rule pointing at a non-AWS resolver.
4. **Public** — everything else.

## Worked example

An instance runs a local split-horizon resolver. `db.internal.corp` returns
`10.42.200.99` via the custom resolver and `10.42.200.10` via the VPC resolver,
where the PHZ holds the `.10` record. `getent` returns `.99`, so the OS path
follows the custom resolver.

This is *not* automatically a fault. Two readings are possible:

- If the custom resolver is authoritative for `internal.corp` by design, `.99`
  is correct and the PHZ record is redundant or stale.
- If the PHZ is meant to be authoritative, the custom resolver is shadowing it
  and should forward that zone back to the VPC resolver.

Resolve the ambiguity by asking which source is intended to be authoritative.
Report both answers and the OS-effective result rather than picking one.

Note that no describe API surfaces this divergence — only an in-instance probe
does.
