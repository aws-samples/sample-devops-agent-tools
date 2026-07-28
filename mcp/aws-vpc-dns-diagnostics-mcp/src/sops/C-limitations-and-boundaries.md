# C — Limitations and Boundaries (state these to the operator)

Report these boundaries explicitly. A confident answer that overstates its scope
is worse than a qualified one.

## Mode A boundaries

| Limitation | Consequence |
| --- | --- |
| Requires SSM connectivity via `ssm` + `ssmmessages` + `ec2messages` interface endpoints (an EC2 Instance Connect Endpoint does not qualify) | If SSM is unreachable, Mode A cannot run. Report the blocker; do not route around it. |
| Observes one instance | Results reflect that instance's resolver path, `resolv.conf`, local stub, and `/etc/hosts`. Probe one instance per relevant subnet before generalizing. |
| Read-only, fixed probe set | Only the allowlisted probes run. Arbitrary commands cannot be executed by design. |
| Point-in-time | A passing probe does not mean the name resolves reliably. Intermittent failures need repeated observation. |

## Mode B boundaries

| Limitation | Consequence |
| --- | --- |
| Coverage equals the candidate name set | A name in neither the configuration nor the operator-supplied list is not simulated. Its absence from the report is **not** evidence it is safe. |
| Models documented precedence | Undocumented or newly changed service behavior may differ. Confirm with Mode A when the stakes are high. |
| Query logs are optional enrichment | Without them, volume ranking is unavailable; correctness is unaffected. |
| Cross-account constructs may be opaque | Predictions are bounded by what the consumer can read. See `C-cross-account-opaque-constructs`. |
| Symbolic, not observed | Always phrase output as a prediction. |

## Where the two modes disagree

Mode A wins. It observes actual behavior; Mode B infers from configuration. A
disagreement is itself a finding — it usually means either an undocumented
service behavior or a construct the model could not read.

## What neither mode covers

- DNS resolution from outside the VPC (on-premises clients, other VPCs).
- Application-layer caching. A JVM or a service mesh sidecar may hold a stale
  answer long after the resolver returns a new one.
- Authoritative zone data correctness at an on-premises resolver.
- Whether an intended design is *correct* — only whether it behaves as
  configured.

## Honest reporting checklist

Before delivering a conclusion, confirm you have stated:

1. Which instance and subnet the observation came from.
2. Which address families were tested.
3. Whether any construct was opaque, and which account or Profile owns it.
4. Whether the finding is observed (Mode A) or predicted (Mode B).
5. For a Profile-related change, the propagation window.
