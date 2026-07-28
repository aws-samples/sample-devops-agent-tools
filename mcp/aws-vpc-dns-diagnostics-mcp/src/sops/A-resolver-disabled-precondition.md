# A — Resolver Disabled: the VPC-attribute precondition

## Why this is checked first

If `enableDnsSupport=false` on the VPC, the Amazon-provided resolver does not
answer at all. Every name fails, from every instance in the VPC, regardless of
private hosted zones, Resolver rules, or endpoint configuration. Diagnosing this
as a security-group, route-table, or rule-precedence problem wastes the
operator's time and can lead to changes that are not related to the cause.

`dns_probe_context` reads this attribute before any resolution is attempted.

## The two attributes

| Attribute | When false |
| --- | --- |
| `enableDnsSupport` | The VPC resolver does not answer. Total resolution failure inside the VPC. Private hosted zones cannot resolve. |
| `enableDnsHostnames` | Instances do not receive public DNS hostnames. Resolution still works. Private hosted zone resolution requires **both** attributes to be enabled. |

## Signature

Every name fails from every instance, including public names, with no successful
resolver identity line. Contrast with a scoped failure — one zone failing while
public names resolve points at a rule or PHZ issue instead.

## Remediation

Enabling `enableDnsSupport` is a VPC-wide change. Note two consequences before
recommending it:

- It affects every instance in the VPC, not only the one under investigation.
- If the VPC was deliberately configured with the resolver dark (some
  environments do this to force all resolution through a custom resolver),
  enabling it changes the intended security posture. Confirm intent before
  recommending the change.

If a custom resolver is intended to serve the VPC entirely, the correct fix may
be to leave `enableDnsSupport=false` and repair the custom resolver path instead.
Verify which design was intended.

## Simulation note

Mode B models this condition: a change that turns the VPC resolver dark is
reported by the `resolver-disabled` trap detector, which predicts NXDOMAIN for
affected names. See `B-mode-b-pre-change-validation`.

## Testing caveat

This condition is difficult to demonstrate safely in a live fixture, because
disabling `enableDnsSupport` is VPC-wide and severs SSM connectivity to every
instance in that VPC — including the instance you would use to observe the
effect. Verify it by reading the attribute rather than by inducing it.
