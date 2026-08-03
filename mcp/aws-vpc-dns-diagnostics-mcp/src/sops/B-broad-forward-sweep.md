# B — Broad FORWARD Sweep

## Mechanism

A FORWARD rule for `.` (the root) or for a broad suffix such as `amazonaws.com`
captures every name beneath it and sends those queries to an on-premises or
custom resolver. A specific FORWARD rule sits at precedence level 2 — above
SYSTEM rules, endpoint private DNS, and private hosted zones. Anything the sweep
covers is diverted before those lower levels are consulted.

If the target resolver does not host the swept zones, the names time out. If it
answers with public-internet results, AWS service traffic silently bypasses VPC
endpoints and takes the public path.

## Signature

- AWS service FQDNs time out or NXDOMAIN after a hybrid DNS change
- Internal PHZ names time out while public names still resolve
- Endpoint private IPs stop being returned even though the endpoint is healthy
- A timeout rather than a fast NXDOMAIN, indicating a forward to an unresponsive
  target

## The carve-out pattern

A `.` FORWARD rule requires SYSTEM carve-outs for everything that must stay with
the VPC resolver. A SYSTEM rule at level 3 returns the query to VPC recursion,
but it is outranked by any *more specific* FORWARD rule, so specificity matters
more than rule order.

Typical carve-outs:

| Carve-out | Why |
| --- | --- |
| `amazonaws.com` | keeps AWS service FQDNs and endpoint private DNS resolving |
| Alternate AWS service domains in use | a differently-suffixed service alias is not covered by an `amazonaws.com` carve-out |
| Each associated PHZ zone | a broad sweep otherwise outranks the PHZ |
| Service-managed endpoint zones | private DNS zones installed by interface endpoints |

The second row is the one most often missed. A team adopts an alternate service
FQDN to work around an endpoint shadow (see `B-vpce-shadow-nxdomain`), then a
root FORWARD rule with only an `amazonaws.com` carve-out sweeps that alternate
name on-premises. Each change looks correct in isolation.

## Detection

`dns_simulate_change` reports the `broad-FORWARD-sweep` trap, lists the AWS FQDNs
and PHZ names that lose resolution, and identifies the missing carve-outs.

## Remediation

1. Prefer narrowing the FORWARD rule to the specific corporate zones that need
   on-premises resolution, rather than forwarding `.` and carving back.
2. Where a root forward is required, add a SYSTEM rule for each domain that must
   stay local, including alternate AWS service domains and every associated PHZ
   zone.
3. Re-run `dns_simulate_change` after drafting the carve-outs. Narrowing a sweep
   can restore one set of names while breaking names that relied on the broad
   forward — the same collision described in
   `A-forward-vs-phz-precedence-collision`.

## Confirm with Mode A

After applying carve-outs, probe one name per category — an AWS service FQDN, a
PHZ name, a corporate zone name, and a public name — and read `hostname.bind` on
each to confirm the intended resolver answered. Configuration that looks correct
can still route unexpectedly.
