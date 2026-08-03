# B — VPC Endpoint Shadow NXDOMAIN

## Mechanism

Enabling private DNS on a VPC interface endpoint installs a service-managed
private hosted zone for the service's DNS name into the VPC. From that point the
service FQDN resolves to the endpoint's ENI private IPs instead of the public
service IPs.

The failure mode: the installed zone **shadows** the public name. Any name under
that zone which the endpoint does not serve now returns NXDOMAIN rather than
falling through to public resolution. The zone is authoritative, so there is no
fallback.

## Classic case

An interface endpoint is created for a service, private DNS is enabled, and a
name that used to resolve publicly stops resolving. The endpoint serves the
regional service endpoint but not the additional name the workload was using —
for example an alternate service alias, a differently-suffixed FQDN, or a
sibling API name that shares the shadowed apex.

This affects any workload path that depended on the shadowed name resolving
publicly, including bootstrap, package fetch, and OIDC/token endpoints.

## Detection

`dns_simulate_change` with the endpoint private-DNS change reports the
`VPCE-shadow-NXDOMAIN` trap and lists the names predicted to move from a
resolving answer to NXDOMAIN.

## Confirming after the fact

If private DNS is already enabled and the symptom is present, use Mode A:

- `dns_probe_compare` on the failing name. A private ENI IP confirms the endpoint
  path; NXDOMAIN confirms the shadow.
- Probe a name known to be served by the endpoint. If it returns a private IP
  while the failing name NXDOMAINs, the shadow is confirmed rather than a general
  resolver failure.

## Remediation options

| Option | Trade-off |
| --- | --- |
| Use an alternate service FQDN outside the shadowed zone, where one exists | Simplest; depends on the service publishing one |
| Disable endpoint private DNS and reach the service publicly | Loses the private path; may violate a no-public-egress requirement |
| Add a PHZ record for the shadowed name pointing at the endpoint | Only valid if the endpoint actually serves that name |
| Add a more specific FORWARD or SYSTEM rule for the name | A specific FORWARD outranks the endpoint zone; verify against `A-forward-vs-phz-precedence-collision` |

## Interaction warning

If an alternate FQDN is adopted as the workaround, verify that no broad FORWARD
rule sweeps that alternate name to an on-premises resolver. A `.` FORWARD rule
with only an `amazonaws.com` SYSTEM carve-out will capture a differently-suffixed
alternate name, breaking the workaround in a way that looks unrelated. See
`B-broad-forward-sweep`.

## Validate first

Run `dns_simulate_change` before enabling private DNS. This trap is
straightforward to predict and expensive to discover in production.
