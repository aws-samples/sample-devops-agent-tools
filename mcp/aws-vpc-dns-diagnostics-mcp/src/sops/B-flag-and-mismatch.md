# B — Flag-AND Mismatch (privateDnsEnabled × PrivateDnsPreference)

## Mechanism

Two independent flags govern whether a custom domain is published into a
consumer VPC through VPC Lattice, and they are **AND-ed**. Setting one without
the other leaves the domain uninstalled, while both the resource configuration
and the service network association appear correctly configured.

| Flag | Level | Effect |
| --- | --- | --- |
| `privateDnsEnabled` | service network resource association | publishes the resource configuration's custom domain into the consumer VPC |
| `PrivateDnsPreference` | service network VPC association | gates which domains may be overridden in that VPC |

If `privateDnsEnabled=false`, the custom domain is not installed **even when**
the VPC association permits all domains. Conversely, a permissive preference does
nothing on its own.

## PrivateDnsPreference values

| Value | Behavior |
| --- | --- |
| `VERIFIED_DOMAINS_ONLY` (default) | blocks AWS-owned FQDNs from being overridden |
| `SPECIFIED_DOMAINS_ONLY` | scoped middle ground; only listed domains |
| `ALL_DOMAINS` | forces override for any domain, including AWS FQDNs |

Under `ALL_DOMAINS`, only **published** resource-configuration domains are
redirected. An unpublished AWS service name is not black-holed; it times out.

## Immutability warning

Several of these properties are create-only. `privateDnsEnabled` on a service
network resource association has no update API. On the VPC association,
`PrivateDnsEnabled` and DNS options are likewise create-only, so correcting them
requires delete and recreate — in CloudFormation, with a new logical ID. Plan the
change as a replacement, not an in-place update, and account for the resolution
gap during recreation.

Note also that `privateDnsEnabled` is set to true automatically when a custom
domain name is present at create time.

## Misleading field

The `privateDnsEntry.domainName` field on a service network resource association
is populated **even when private DNS is disabled**. Do not treat its presence as
evidence that the domain is published. Verify the flag itself, and confirm
resolution with Mode A.

## Detection

`dns_simulate_change` reports the `flag-AND-mismatch` trap when a proposed change
leaves the two flags in a combination that does not install the intended domain.

## Diagnosis and remediation

1. `dns_simulate_effective_config` — read both flags as they currently stand.
2. `dns_probe_compare` on the custom domain. NXDOMAIN or a timeout, with a
   populated `privateDnsEntry.domainName`, confirms the mismatch.
3. Set both flags consistently. Because they are create-only, schedule the
   recreate and validate afterwards with Mode A rather than assuming the change
   took effect.
