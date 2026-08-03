# A — Address-Family Divergence (A vs AAAA, IPv4 vs IPv6 resolvers)

## Two distinct issues share this symptom

### Issue 1 — the record exists for one family only

`db.internal.corp` returns an A record but an empty AAAA, or vice versa. The
resolver path is healthy; the zone simply has no record for the other family.
A private hosted zone holding only A records behaves exactly this way.

This is a **data** problem, not a resolution problem. Confirm by probing a name
known to have both families (a dualstack public name); if that returns both, the
resolver path is fine and the gap is in the zone.

Impact depends on the client. An application with `AI_ADDRCONFIG` or a
happy-eyeballs implementation usually falls back cleanly. One that requests AAAA
exclusively fails. Ask which behavior the caller has before ranking severity.

### Issue 2 — the resolver address for one family does not exist

In an IPv6-only subnet there is no `169.254.169.253`. Only `fd00:ec2::253`
answers. In a dualstack VPC both answer. Probing the IPv4 resolver from an
IPv6-only instance times out — **that is expected**, not a fault.

## Diagnosis

1. `dns_probe_context` — read the instance addressing family. This determines
   which resolver addresses can exist at all.
2. `dns_probe_compare` — probe both families. Compare per-family results per
   resolver.

## Interpretation

| Observation | Reading |
| --- | --- |
| A answers, AAAA empty, both resolvers agree | Zone has no AAAA record. Data gap. |
| A answers, AAAA empty, only on a custom resolver | The custom resolver is not forwarding AAAA for that zone. |
| IPv4 resolver times out on an IPv6-only instance | Expected. Not a fault. |
| Both resolver families time out | Resolver path problem. Check `enableDnsSupport` first — see `A-resolver-disabled-precondition`. |
| AAAA answers, A empty for an AWS FQDN | Verify the endpoint's configured address family; a dualstack endpoint is required for both. |

## Reporting rule

Never report the absent IPv4 resolver as a fault on an IPv6-only instance, and
never report an empty AAAA as a resolver failure without first checking whether
the zone holds a record for that family. State which family you tested; a claim
of "DNS works" that only covers A is incomplete on a dualstack VPC.
