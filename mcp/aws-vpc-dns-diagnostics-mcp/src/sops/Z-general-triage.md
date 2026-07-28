# Z — General DNS Triage (start here)

Use this runbook when the symptom is vague ("DNS is broken", "the app can't
reach the database", "resolution is flaky") and you do not yet know which
specific failure mode applies.

## Step 0 — Establish the precondition before diagnosing anything

Call `dns_probe_context` for an affected instance. Read three things:

1. **`enableDnsSupport` / `enableDnsHostnames`.** If `enableDnsSupport=false`,
   the VPC resolver is intentionally dark. Lead with that. Do not diagnose it as
   a security-group, routing, or resolver-configuration problem. See
   `A-resolver-disabled-precondition`.
2. **Instance addressing family.** In an IPv6-only subnet there is no
   `169.254.169.253`; only `fd00:ec2::253` answers. See
   `A-address-family-divergence`.
3. **DHCP option set `domain-name-servers`.** This is the resolver the VPC
   *intends* the instance to use. If it differs from the instance's actual
   `resolv.conf` (returned by `dns_probe_compare`), the instance is not using
   the VPC-handed resolver — that discrepancy is often the whole answer.

If SSM is unreachable, stop and report it. Do not attempt a workaround over the
public internet path. See `A-mode-a-live-resolver-comparison`, "SSM
prerequisites".

## Step 1 — Classify the question as reactive or predictive

| The operator is asking | Use | Runbook |
| --- | --- | --- |
| "Why is this name resolving wrong *right now*?" | Mode A (`dns_probe_*`) | `A-mode-a-live-resolver-comparison` |
| "What will break if I make this change?" | Mode B (`dns_simulate_*`) | `B-mode-b-pre-change-validation` |

These are different tools with different guarantees. Mode A returns ground truth
observed from inside a subnet. Mode B returns a prediction derived from
control-plane configuration. When they disagree, Mode A wins — the service may
behave in ways the documented precedence model does not capture.

## Step 2 — Get the observed answer matrix

Call `dns_probe_compare` with the failing name. By default it auto-adds the
DHCP-configured resolver(s), so you usually pass no `resolvers` argument. Read
the `hostname.bind` identity line to confirm *which* resolver actually answered
rather than assuming.

## Step 3 — Match the observation to a failure mode

| Observation | Likely mode | Runbook |
| --- | --- | --- |
| Same name, different answers per resolver | custom-resolver divergence | `A-custom-resolver-divergence` |
| AWS service FQDN returns a public IP where a VPC endpoint exists | VPCE private DNS not in effect | `B-vpce-shadow-nxdomain` |
| AWS service FQDN NXDOMAINs or times out | over-broad FORWARD rule | `B-broad-forward-sweep` |
| Internal zone name times out (does not NXDOMAIN) | FORWARD outranking a PHZ | `A-forward-vs-phz-precedence-collision` |
| A resolves but AAAA is empty (or vice versa) | per-family record gap | `A-address-family-divergence` |
| Config reads return "opaque" markers | shared cross-account construct | `C-cross-account-opaque-constructs` |
| Change was applied but has not taken effect | Profile propagation delay | `B-profile-propagation-timing` |

## Step 4 — Judge correctness, not agreement

Before calling anything a fault, classify the name and check it against the
expected winner. Resolvers disagreeing is frequently the *correct* design. See
`A-name-category-classification`.

## Step 5 — Report limitations honestly

State the boundaries of what you verified. See `C-limitations-and-boundaries`.
