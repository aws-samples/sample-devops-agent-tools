# B — Mode B: Pre-Change Validation

## When to use

Before any of the following is applied:

- Enabling private DNS on a VPC interface endpoint
- Associating or disassociating a private hosted zone
- Adding, altering, or removing a Route 53 Resolver rule
- Associating a DNS Firewall rule group
- Associating or disassociating a Route 53 Profile
- Changing the VPC DHCP option set's `domain-name-servers`

Also use it whenever the operator asks "what will break if I make this change?"

## The gap this fills

The highest-impact DNS outages are invisible before the change. The
configuration looks correct, the change looks additive, and the breakage only
appears once a name that used to resolve stops resolving. Mode B builds the
effective model, applies the proposed change symbolically, and diffs the
resolution outcome per name — without touching anything.

Mode B is **read-only**. It performs describe, get, and list calls only, on a
role that never holds `ssm:SendCommand`.

## Workflow

### Step 1 — Inventory the effective configuration

Call `dns_simulate_effective_config(account_id, region, vpc_id)`. This returns
the union of directly attached and Route 53 Profile-inherited constructs, each
tagged with its source (`direct` or `profile:<id>`). Read the source tags: a
construct inherited from a Profile may be changed by a Profile owner outside this
account's control.

### Step 2 — Simulate the change

Call `dns_simulate_change(account_id, region, vpc_id, change, ...)` with the
structured change descriptor. Candidate names default to those derived from
configuration. Supply an explicit `candidate_names` list to focus the analysis on
names the operator cares about, and `volumes` to weight the ranking.

### Step 3 — Read the traps before the diff

A triggered trap detector is more informative than the raw name diff, because it
names the *mechanism*. Six detectors run:

| Detector | Meaning | Runbook |
| --- | --- | --- |
| `VPCE-shadow-NXDOMAIN` | Enabling private DNS shadows a service apex still queried the old way | `B-vpce-shadow-nxdomain` |
| `broad-FORWARD-sweep` | A new `.` or `amazonaws.com` FORWARD rule captures AWS FQDNs with no SYSTEM carve-out | `B-broad-forward-sweep` |
| `flag-AND-mismatch` | `privateDnsEnabled` and `PrivateDnsPreference` combine to leave a custom domain uninstalled | `B-flag-and-mismatch` |
| `DNS-Firewall-block` | A rule-group change blocks a candidate name | `B-dns-firewall-block` |
| `Profile-union-shift` | An association change shifts the effective set in bulk | `B-profile-propagation-timing` |
| `resolver-disabled` | A DHCP change turns the VPC resolver dark | `A-resolver-disabled-precondition` |

### Step 4 — Rank and report

Any triggered trap escalates the finding to high severity; ties break on query
volume. Report the mechanism, the affected names, and the propagation window
where a Profile is involved.

### Step 5 — Confirm with Mode A where it matters

For a high-severity prediction on a name the operator cannot afford to lose,
confirm current ground truth with `dns_probe_compare` before the change, and
again after. The simulator models documented precedence; observed behavior wins.

## Precedence model used

1. DNS Firewall (BLOCK / OVERRIDE)
2. Specific FORWARD rule
3. SYSTEM rule
4. VPC endpoint private DNS
5. Associated private hosted zone
6. Service network VPC association `PrivateDnsPreference` gate (AND-ed with
   `privateDnsEnabled`)
7. VPC resolver recursion (default)

## Coverage limit — state this to the operator

Mode B's coverage equals its candidate name set. A name that appears in neither
the configuration nor the operator-supplied list is not simulated, and its
absence from the report is not evidence that it is safe. Query logs, when
available, are optional enrichment used for volume ranking — they are not
required for correctness.
