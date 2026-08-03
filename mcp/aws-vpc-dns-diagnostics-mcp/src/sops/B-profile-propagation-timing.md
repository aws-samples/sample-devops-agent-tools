# B — Route 53 Profile Propagation and Union Shift

## Two effects, one change

Associating or disassociating a Route 53 Profile does two things at once: it
shifts the effective configuration in bulk (the `Profile-union-shift` trap), and
it takes effect asynchronously rather than immediately.

## Union shift

The effective model is the union of directly attached constructs and
Profile-inherited ones. Associating a Profile can add resolver rules, private
hosted zones, and firewall rule groups in a single operation — any of which may
outrank a directly attached construct and change what an existing name resolves
to. Disassociating removes them just as broadly.

`dns_simulate_effective_config` tags every construct with its source (`direct` or
`profile:<id>`). Read those tags before recommending a Profile change: a
Profile-sourced construct can be altered by the Profile's owner, outside this
account's control, with the same bulk effect and no local change record.

## Propagation timing

Propagation runs through a multi-stage asynchronous pipeline:

| Stage | Typical window |
| --- | --- |
| Service-side propagation | ~300–350 seconds |
| Negative-cache worst case | up to ~900 seconds |

The worst case occurs when a query fires before propagation completes: the
negative answer is cached against the zone's SOA minimum TTL, and the name keeps
failing after the configuration is already correct. Total observed delay can
therefore reach roughly 20 minutes even though service-side work finished in
about 5.

Report *when* a change takes effect, not only the end state. An operator who
tests at 60 seconds and sees failure will often revert a change that was working.

## Guidance to give the operator

1. Poll the association status API until it reports complete before testing.
   Do not test on a timer.
2. Avoid querying the affected names before propagation completes, to keep a
   negative answer out of cache.
3. Where the zone is under your control, lower the SOA minimum TTL in advance to
   shrink the negative-cache window.
4. Flush client caches after propagation. On Kubernetes with CoreDNS, restart the
   CoreDNS deployment; CoreDNS applies its own cache on top of the resolver's.

## Detection

`dns_simulate_change` reports `Profile-union-shift` for association and
disassociation changes and annotates the affected deltas with the propagation
window. Profile-sourced deltas always carry the timing annotation — surface it in
the report rather than only the name diff.

## Cross-account limitation

Profile **contents** are opaque to a consumer account. A profile-contained
resolver rule or private hosted zone is enumerable, but `get_resolver_rule` and
`get_hosted_zone` are denied cross-account. No Route 53 Profiles API action
exposes a profile's zone, record, or rule contents to a consumer.

"Enumerable but opaque" is the complete and correct model. When simulating a
Profile change in a consumer account, state that the contents could not be read
and that the prediction is therefore bounded. See
`C-cross-account-opaque-constructs`.
