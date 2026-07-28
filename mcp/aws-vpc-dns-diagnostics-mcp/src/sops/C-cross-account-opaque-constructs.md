# C — Cross-Account Opaque Constructs

## The consumer-side rule

When DNS constructs are shared into an account via AWS RAM or contributed through
a Route 53 Profile, some are fully readable and some are **enumerable but
opaque** — you can see that they exist, but not what is inside them.

Maintain a strict consumer-side perspective. Never assume visibility into
provider-owned internals, and never enumerate provider-side constructs from an
account that does not own them.

## Measured visibility

| Construct | Consumer-side visibility |
| --- | --- |
| Directly associated private hosted zone | fully readable (name, id, owner) |
| RAM-shared resolver rule | fully readable (domain, target, owner) |
| RAM-shared DNS Firewall rule group | association and rules visible; **domain lists denied** (`list_firewall_domains`) |
| Profile-contained resolver rule | enumerable; **`get_resolver_rule` denied** |
| Profile-contained private hosted zone | enumerable; **`get_hosted_zone` denied** |

No Route 53 Profiles API action exposes a profile's zone, record, or rule contents
to a consumer account.

## How the server handles this

Every per-resource detail read is wrapped so a denial produces an `OPAQUE`
marker rather than a failed model build. The resolution engine then applies two
rules:

- An **opaque firewall rule** is treated as OPAQUE **first**, ahead of everything
  else, because a hidden block list may cover any name.
- An **opaque resolver rule** is treated as OPAQUE only when no concrete rule
  matched.

A model containing opaque markers is a valid, complete model of what the consumer
can actually see. It is not a degraded result to be apologized for — it is the
correct answer to "what is visible from here."

## Reporting rules

1. When a name resolves to OPAQUE, report "cannot determine from this account,"
   never "not affected." The distinction matters: absence of visible evidence is
   not evidence of absence.
2. Name the owning account or Profile where the API returns it, so the operator
   knows whom to ask.
3. State that the Mode B prediction is bounded by the opaque constructs. A
   prediction that cannot see a block list cannot promise a name will resolve.
4. Where ground truth is needed and an instance is available, use Mode A. An
   in-instance probe observes the *result* of an opaque construct even when its
   configuration cannot be read. This is the most reliable way around opacity.

## PHZ-in-Profile note

A private hosted zone can be associated with a Route 53 Profile, but the zone
must be **private**. Attempting to associate a public zone produces a misleading
error suggesting the operation is unsupported. In CloudFormation, a hosted zone
requires a `VPCs` property to be created as private — omitting it silently creates
a public zone, which the Profile then rejects. If a Profile association fails,
check the zone's type before concluding the feature is unavailable.

## Consumer-side derivation for Lattice

For VPC Lattice shadows, derive what is visible from the consumer's own endpoint
records rather than enumerating provider-side resource configurations. Query the
endpoints the consumer owns and read the configuration for those specific ARNs.
Do not attempt provider-side enumeration such as listing resource gateways or
resource configurations from a consumer account — those calls are denied by
design, and treating a denial as an error rather than an expected boundary
produces false failures.
