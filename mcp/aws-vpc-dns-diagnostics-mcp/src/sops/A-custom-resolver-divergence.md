# A — Custom / Hybrid Resolver Divergence

## Symptom

The same name returns different answers depending on which resolver is queried,
or an application resolves a name differently than a manual `dig` against the
VPC resolver suggests it should.

## Why describe APIs cannot find this

The DHCP option set says which resolver the VPC *hands out*. It does not say
which resolver the instance is *using*. An instance can point `resolv.conf` at
`127.0.0.1` (a local dnsmasq, unbound, or systemd-resolved stub), at a domain
controller, or at an on-premises forwarder — none of which is visible from any
AWS API. Only an in-instance probe resolves the ambiguity.

## Diagnosis

1. `dns_probe_context` — record the DHCP-handed resolver (VPC-intended).
2. `dns_probe_compare` — record the instance's actual `resolv.conf` and the
   per-resolver answer matrix.
3. Compare the two. A mismatch means the instance is not using the VPC-handed
   resolver.
4. Read `hostname.bind` per resolver to identify each responder.
5. Check the OS-effective answer (`getent`), which follows the real NSS path
   including `resolv.conf` order, `nsswitch.conf`, and `/etc/hosts`. The
   OS-effective answer is what the application gets — it can differ from every
   individual `dig` result.

## Interpretation

| Pattern | Reading |
| --- | --- |
| Custom resolver answers a corporate zone; VPC resolver NXDOMAINs | Correct by design. Not a fault. |
| Custom resolver NXDOMAINs a PHZ name the VPC resolver answers | The custom resolver does not forward that zone back to the VPC resolver. Add a forward for the PHZ zone. |
| Custom resolver returns a public IP for an AWS FQDN with a VPC endpoint present | The endpoint is being bypassed. The custom resolver must forward `amazonaws.com` to the VPC resolver. See `B-vpce-shadow-nxdomain`. |
| Both answer, different private IPs | Two authoritative sources for one zone. Determine which is intended; report both. |
| `getent` disagrees with every `dig` | Inspect `/etc/hosts`, `nsswitch.conf`, and a local stub resolver's cache. |

## Resolver address caveat

When a local stub resolver forwards upstream, the link-local VPC resolver
address (`169.254.169.253`) may not answer directly from the instance while the
VPC+2 address does. If a probe against the link-local address times out but the
VPC+2 address answers, treat that as a local resolver-path artifact rather than
evidence the VPC resolver is down. Probe both before concluding.

## Remediation shape

Do not recommend removing the custom resolver as a first move — it usually
exists for corporate zone resolution. Recommend the specific forward rules that
make both zone sets resolve: corporate zones to the custom resolver,
`amazonaws.com` and PHZ zones back to the VPC resolver.
