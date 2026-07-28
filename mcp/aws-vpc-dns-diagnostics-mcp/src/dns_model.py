# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Mode B core: the shared effective-config model, the symbolic resolver engine,
and the trap detectors used by the dns_simulate_* tools.

Design contract (kept deterministic and side-effect free so it is unit-testable
without AWS):

  EffectiveModel  - the VPC's effective DNS config = union of directly-attached
                    resources and Route 53 Profile-inherited resources. Every
                    construct carries a `source` ("direct" or "profile:<id>").
  resolve(name)   - the 7-level precedence engine. Returns a Resolution naming
                    the winning construct, its source, and the answer class.
  apply_change()  - produces a new EffectiveModel with a proposed change applied.
  simulate()      - resolves each candidate name through the current and
                    post-change models, diffs them, runs the trap detectors, and
                    ranks the result.

Answer classes: BLOCKED, VPCE_PRIVATE, PHZ_PRIVATE, ONPREM, PUBLIC, NXDOMAIN.
Precedence (highest -> lowest): DNS Firewall > specific FORWARD > SYSTEM >
VPCE private DNS > PHZ > SNVA preference gate > .2 default.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# ---- answer classes -------------------------------------------------------

BLOCKED = "BLOCKED"
VPCE_PRIVATE = "VPCE_PRIVATE"
PHZ_PRIVATE = "PHZ_PRIVATE"
ONPREM = "ONPREM"
PUBLIC = "PUBLIC"
NXDOMAIN = "NXDOMAIN"
# A construct is associated but its details are not readable from this account
# (cross-account shared firewall domains / profile-contained rule). The true
# resolution cannot be predicted - surfaced so the tool is honest rather than
# silently treating the construct as inert.
OPAQUE = "OPAQUE"

# Route 53 Profile propagation window (service-side target .. negative-cache worst case)
PROFILE_PROPAGATION_SECONDS = (300, 900)


# ---- data model -----------------------------------------------------------

@dataclass(frozen=True)
class ResolverRule:
    """A Resolver rule. rule_type is FORWARD or SYSTEM; domain is the match apex.

    opaque=True means the rule is known to be associated but its details
    (domain/target) are NOT readable from this account (e.g. a rule delivered
    via a cross-account shared Route 53 Profile - get_resolver_rule denies).
    When opaque, `domain` may be '' and the engine cannot predict its effect."""
    domain: str
    rule_type: str  # "FORWARD" | "SYSTEM"
    target: str = ""  # e.g. "onprem" for FORWARD
    source: str = "direct"
    opaque: bool = False


@dataclass(frozen=True)
class FirewallRule:
    """A DNS Firewall domain-list rule. action is ALLOW/ALERT/BLOCK; block_response
    is NXDOMAIN/NODATA/OVERRIDE when action is BLOCK.

    opaque=True means the rule group is associated but its domain list is NOT
    readable from this account (e.g. a RAM-shared group - list_firewall_domains
    denies, or an AWS Managed Domain List). domains will be empty; the engine
    must treat the rule as 'present, match set unknown' rather than inert."""
    domains: tuple[str, ...]
    action: str  # "ALLOW" | "ALERT" | "BLOCK"
    block_response: str = "NXDOMAIN"
    priority: int = 100
    source: str = "direct"
    opaque: bool = False


@dataclass(frozen=True)
class Phz:
    """A private hosted zone associated with the VPC (zone apex)."""
    zone: str
    source: str = "direct"


@dataclass(frozen=True)
class Shadow:
    """A Lattice/PrivateLink-managed private-DNS shadow installed into the VPC.

    One construct covers every path that installs a shadow PHZ over an apex:
      * interface VPC endpoint private DNS (e.g. secretsmanager...amazonaws.com)
      * Service Network VPC Association (SNVA) published domains
      * Service Network endpoint
      * VPC Resource endpoint (resource configuration CustomDomainName)

    service_apex   the FQDN whose zone is shadowed.
    private_dns    whether the shadow is actually installed/enabled.
    served_names   specific records the shadow answers; when installed it
                   captures the whole apex but answers only served_names (+ the
                   exact apex) - any other subdomain NXDOMAINs. Empty = apex only.
    gated          True  -> subject to the SNVA PrivateDnsPreference gate for
                            AWS-owned FQDNs (interface VPCE / SNVA-published).
                   False -> endpoint-installed custom-domain shadow (resource /
                            SN endpoint); NOT gated by the SNVA preference.
    """
    service_apex: str
    private_dns: bool
    source: str = "direct"
    served_names: tuple[str, ...] = ()
    gated: bool = True


# Backwards-compatible alias: interface VPC endpoints are gated shadows.
Vpce = Shadow


@dataclass(frozen=True)
class EffectiveModel:
    """The VPC's effective DNS resolution configuration."""
    vpc_id: str
    firewall_rules: tuple[FirewallRule, ...] = ()
    resolver_rules: tuple[ResolverRule, ...] = ()
    phzs: tuple[Phz, ...] = ()
    vpces: tuple[Vpce, ...] = ()
    # SNVA gate: VERIFIED_DOMAINS_ONLY | ALL_DOMAINS | SPECIFIED_DOMAINS_ONLY
    snva_preference: str = "VERIFIED_DOMAINS_ONLY"
    # domains the SNVA override applies to when SPECIFIED_DOMAINS_ONLY
    specified_domains: tuple[str, ...] = ()
    dns_support: bool = True
    # operator-declared on-prem/corp zones (for category judgement)
    onprem_zones: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    name: str
    answer_class: str
    winner: str      # human label of the construct that won
    source: str      # "direct" | "profile:<id>" | "system"


# ---- helpers --------------------------------------------------------------

def _suffix_match(name: str, apex: str) -> bool:
    n = name.rstrip(".").lower()
    a = apex.rstrip(".").lower()
    if a in (".", ""):  # root FORWARD matches everything
        return True
    return n == a or n.endswith("." + a)


def _specificity(apex: str) -> int:
    """More labels == more specific. Root ('.') is least specific."""
    a = apex.rstrip(".")
    if a in ("", "."):
        return 0
    return a.count(".") + 1


def _is_aws_fqdn(name: str) -> bool:
    n = name.rstrip(".").lower()
    return n.endswith(".amazonaws.com") or n.endswith(".api.aws")


def _snva_allows(name: str, model: "EffectiveModel") -> bool:
    """Whether the SNVA PrivateDnsPreference permits overriding an AWS-owned FQDN
    for `name`.

    Live preference values (vpc-lattice dnsOptions.privateDnsPreference):
      - ALL_DOMAINS                          -> always override
      - VERIFIED_DOMAINS_ONLY (default)      -> never override an AWS FQDN
      - SPECIFIED_DOMAINS_ONLY               -> override only for a specified domain
      - VERIFIED_DOMAINS_AND_SPECIFIED_DOMAINS -> like SPECIFIED for AWS FQDNs
        (verified/AWS-owned names are still overridden only when specified)
    """
    pref = model.snva_preference
    if pref == "ALL_DOMAINS":
        return True
    if pref in ("SPECIFIED_DOMAINS_ONLY", "VERIFIED_DOMAINS_AND_SPECIFIED_DOMAINS"):
        return any(_suffix_match(name, d) for d in model.specified_domains)
    return False  # VERIFIED_DOMAINS_ONLY (default) and anything unknown


# ---- the resolver engine --------------------------------------------------

def resolve(name: str, model: EffectiveModel) -> Resolution:
    """Symbolically resolve `name` under `model`, honoring the precedence stack."""
    n = name.rstrip(".").lower()

    # 0. If the VPC resolver is disabled (enableDnsSupport=false), NOTHING the
    #    VPC resolver would answer works - and Resolver rules + DNS Firewall do
    #    not evaluate either. Short-circuit to NXDOMAIN before any other branch.
    if not model.dns_support:
        return Resolution(name, NXDOMAIN, "VPC resolver disabled (enableDnsSupport=false)", "system")

    # 1. DNS Firewall - evaluated before resolution completes. Lowest priority
    #    number wins; a BLOCK short-circuits. Opaque rule groups (domains not
    #    readable from this account) are evaluated first: since ANY name might be
    #    in the hidden block list, the effect is unpredictable -> OPAQUE.
    opaque_fw = sorted(
        [r for r in model.firewall_rules if r.opaque],
        key=lambda r: r.priority,
    )
    if opaque_fw:
        r0 = opaque_fw[0]
        return Resolution(
            name, OPAQUE,
            f"DNS Firewall rule group present but domain list not readable "
            f"from this account ({r0.action}); effect on '{name}' cannot be "
            f"predicted", r0.source,
        )
    fw = sorted(
        [r for r in model.firewall_rules
         if not r.opaque and any(_suffix_match(n, d) for d in r.domains)],
        key=lambda r: r.priority,
    )
    for rule in fw:
        if rule.action == "BLOCK":
            cls = BLOCKED if rule.block_response == "OVERRIDE" else NXDOMAIN
            return Resolution(name, cls,
                              f"DNS Firewall {rule.action}/{rule.block_response}", rule.source)
        # ALLOW/ALERT pass through to resolution below.
        break

    # 2/3. Resolver rules: most-specific match wins. At equal specificity a
    #      FORWARD is preferred over SYSTEM (documented modeling decision - see
    #      test_forward_beats_system_equal_specificity). RECURSIVE (the default
    #      Internet Resolver rule, e.g. the autodefined '.') means "resolve
    #      normally" and is NOT an override - skip it so resolution falls through
    #      to VPCE/PHZ/native below.
    matched = [
        r for r in model.resolver_rules
        if not r.opaque and _suffix_match(n, r.domain) and r.rule_type in ("FORWARD", "SYSTEM")
    ]
    if matched:
        matched.sort(key=lambda r: (_specificity(r.domain), r.rule_type == "FORWARD"), reverse=True)
        top = matched[0]
        if top.rule_type == "FORWARD":
            # Forwarded off to the target (on-prem). AWS FQDNs swept here break.
            cls = ONPREM
            return Resolution(
                name,
                cls,
                f"FORWARD rule '{top.domain}' -> {top.target or '(target unspecified)'}",
                top.source,
            )
        # SYSTEM rule: force VPC-native resolution; fall through to native logic.

    # 2b. Opaque resolver rules (e.g. a rule delivered via a cross-account shared
    #     Profile whose domain/target are not readable). We cannot tell whether
    #     `name` matches, so if no concrete rule above claimed it, surface the
    #     uncertainty rather than assuming it resolves normally.
    opaque_rr = [r for r in model.resolver_rules if r.opaque]
    if opaque_rr:
        return Resolution(
            name, OPAQUE,
            f"Resolver rule present but not readable from this account "
            f"({opaque_rr[0].source}); may forward '{name}' - effect cannot be "
            f"predicted", opaque_rr[0].source,
        )

    # 4. Managed private-DNS shadow (interface VPCE / SNVA / SN endpoint /
    #    resource endpoint). When installed the shadow PHZ captures the whole
    #    apex: it answers the exact apex + served_names, but a strict subdomain
    #    it does NOT serve returns NXDOMAIN - the shadow-NXDOMAIN trap (e.g.
    #    oidc.eks.<region>.amazonaws.com issuer path).
    for v in model.vpces:
        if not (v.private_dns and _suffix_match(n, v.service_apex)):
            continue
        # The SNVA PrivateDnsPreference gate applies ONLY to gated shadows
        # (interface VPCE / SNVA-published) overriding an AWS-owned FQDN.
        # Endpoint-installed custom-domain shadows (resource / SN endpoint) are
        # ungated - governed by their own per-endpoint private-DNS flag.
        if v.gated and _is_aws_fqdn(v.service_apex) and not _snva_allows(n, model):
            break  # gate blocks the override -> falls through to public
        nn = n.rstrip(".").lower()
        apex = v.service_apex.rstrip(".").lower()
        served = {s.rstrip(".").lower() for s in v.served_names}
        label = "VPCE/Lattice private DNS" if v.gated else "Lattice endpoint shadow"
        if nn == apex or nn in served:
            return Resolution(name, VPCE_PRIVATE, f"{label} '{v.service_apex}'", v.source)
        # Strict subdomain shadowed by the PHZ but not answered by it.
        return Resolution(name, NXDOMAIN, f"shadow, no record for '{name}'", v.source)

    # 5. Associated PHZ.
    phz = [p for p in model.phzs if _suffix_match(n, p.zone)]
    if phz:
        phz.sort(key=lambda p: _specificity(p.zone), reverse=True)
        return Resolution(name, PHZ_PRIVATE, f"PHZ '{phz[0].zone}'", phz[0].source)

    # 6/7. On-prem declared zone with no forwarding path -> NXDOMAIN from .2;
    #      otherwise default public recursion.
    if any(_suffix_match(n, z) for z in model.onprem_zones):
        # No FORWARD matched above, so the VPC resolver has no path to on-prem.
        return Resolution(name, NXDOMAIN, "no FORWARD path for on-prem zone", "system")
    return Resolution(name, PUBLIC, "VPC .2 recursion", "system")


# ---- change application ---------------------------------------------------

def apply_change(model: EffectiveModel, change: dict) -> EffectiveModel:
    """Return a new EffectiveModel with the proposed change applied."""
    ctype = change.get("type")
    src = f"profile:{change['profile_id']}" if ctype == "associate_profile" and change.get("profile_id") else "direct"

    if ctype == "enable_vpce_private_dns":
        apex = change["service_apex"]
        served = tuple(change.get("served_names", ()))
        # Flip an existing endpoint for this apex if present; else append.
        existing = [v for v in model.vpces if v.service_apex.rstrip(".").lower() == apex.rstrip(".").lower()]
        if existing:
            others = tuple(v for v in model.vpces if v not in existing)
            return replace(model, vpces=others + (Vpce(apex, True, existing[0].source, served),))
        return replace(model, vpces=model.vpces + (Vpce(apex, True, "direct", served),))

    if ctype == "associate_phz":
        return replace(model, phzs=model.phzs + (Phz(change["zone"], "direct"),))

    if ctype == "add_resolver_rule":
        # Accept either "target" (a rendered label) or "target_ips" (the shape
        # the Route 53 Resolver API and this server's docs use). Without the
        # latter, a caller passing target_ips produced a rule whose target
        # rendered as an empty string in the impact report.
        target = change.get("target") or ""
        if not target:
            ips = change.get("target_ips") or ()
            if isinstance(ips, str):
                ips = (ips,)
            target = ", ".join(str(i) for i in ips)
        rule = ResolverRule(
            domain=change["domain"],
            rule_type=change.get("rule_type", "FORWARD"),
            target=target,
            source="direct",
        )
        return replace(model, resolver_rules=model.resolver_rules + (rule,))

    if ctype == "associate_dns_firewall":
        rule = FirewallRule(
            domains=tuple(change.get("domains", ())),
            action=change.get("action", "BLOCK"),
            block_response=change.get("block_response", "NXDOMAIN"),
            priority=change.get("priority", 100),
            source="direct",
        )
        return replace(model, firewall_rules=model.firewall_rules + (rule,))

    if ctype == "associate_profile":
        # Bulk change: the profile contributes a set of resources, all tagged
        # with the profile source.
        res = change.get("resources", {})
        return replace(
            model,
            resolver_rules=model.resolver_rules + tuple(
                ResolverRule(r["domain"], r.get("rule_type", "FORWARD"), r.get("target", ""), src)
                for r in res.get("resolver_rules", [])
            ),
            firewall_rules=model.firewall_rules + tuple(
                FirewallRule(tuple(f["domains"]), f.get("action", "BLOCK"),
                             f.get("block_response", "NXDOMAIN"), f.get("priority", 100), src)
                for f in res.get("firewall_rules", [])
            ),
            phzs=model.phzs + tuple(Phz(p["zone"], src) for p in res.get("phzs", [])),
        )

    if ctype == "set_snva_preference":
        return replace(model, snva_preference=change["preference"])

    if ctype == "set_dhcp_dns":
        # Modeled as toggling the VPC resolver on/off for this simulation scope.
        return replace(model, dns_support=change.get("dns_support", model.dns_support))

    return model


def _change_touches_profile(change: dict) -> bool:
    return change.get("type") == "associate_profile"


# ---- trap detectors -------------------------------------------------------

def detect_traps(name: str, before: Resolution, after: Resolution,
                 model_before: EffectiveModel, model_after: EffectiveModel,
                 change: dict) -> list[str]:
    """Return the list of trap labels triggered for this name by this change."""
    traps: list[str] = []

    # 1. VPCE-shadow-NXDOMAIN: a name that resolved (public) now NXDOMAINs because
    #    a VPCE private-DNS enable shadows its apex without answering it.
    if (change.get("type") == "enable_vpce_private_dns"
            and before.answer_class in (PUBLIC, VPCE_PRIVATE)
            and after.answer_class == NXDOMAIN):
        traps.append("VPCE-shadow-NXDOMAIN")

    # 2. broad-FORWARD-sweep: a new '.' or amazonaws.com FORWARD rule sweeps an
    #    AWS-service FQDN on-prem with no protective SYSTEM carve-out.
    if change.get("type") == "add_resolver_rule" and change.get("rule_type", "FORWARD") == "FORWARD":
        dom = change.get("domain", "")
        if (_is_aws_fqdn(name) and _suffix_match(name, dom)
                and after.answer_class == ONPREM and before.answer_class != ONPREM):
            traps.append("broad-FORWARD-sweep")

    # 3. flag-AND mismatch: VPCE private DNS enabled but the SNVA gate leaves the
    #    AWS FQDN override uninstalled (still public), so the intended private
    #    resolution silently does not take effect.
    if (change.get("type") in ("enable_vpce_private_dns", "set_snva_preference")
            and _is_aws_fqdn(name)
            and any(v.private_dns and _suffix_match(name, v.service_apex) for v in model_after.vpces)
            and after.answer_class == PUBLIC):
        traps.append("flag-AND-mismatch")

    # 4. DNS-Firewall-block: a firewall change newly blocks a name.
    if (change.get("type") == "associate_dns_firewall"
            and after.answer_class in (NXDOMAIN, BLOCKED)
            and "DNS Firewall" in after.winner
            and before.answer_class not in (NXDOMAIN, BLOCKED)):
        traps.append("DNS-Firewall-block")

    # 5. Profile-union shift: a Profile associate/disassociate changes the winner
    #    or its source for this name.
    if _change_touches_profile(change) and (
        before.answer_class != after.answer_class or before.source != after.source
    ):
        traps.append("Profile-union-shift")

    # 6. resolver-disabled: a set_dhcp_dns change turns the VPC resolver dark and
    #    the name breaks. Labels the cause instead of a bare break->high.
    if (change.get("type") == "set_dhcp_dns"
            and not model_after.dns_support
            and after.answer_class == NXDOMAIN
            and before.answer_class != NXDOMAIN):
        traps.append("resolver-disabled")

    return traps


# ---- severity + orchestration --------------------------------------------

def _severity(before: Resolution, after: Resolution, traps: list[str], volume: int) -> str:
    breaks = after.answer_class in (NXDOMAIN, BLOCKED) and before.answer_class not in (NXDOMAIN, BLOCKED)
    changed = before.answer_class != after.answer_class
    # A triggered trap is a known-bad pattern -> high, EXCEPT a Profile-union
    # shift that only changed the source (same answer_class) is a benign
    # ownership move -> medium, so it does not over-alert (L4).
    if traps:
        source_only = (traps == ["Profile-union-shift"] and not changed and not breaks)
        return "medium" if source_only else "high"
    if breaks:
        base = "high"
    elif changed:
        base = "medium"
    else:
        base = "none"
    # Volume can escalate a medium to high when a lot of traffic is affected.
    if base == "medium" and volume >= 1000:
        base = "high"
    return base


@dataclass
class NameImpact:
    name: str
    before: Resolution
    after: Resolution
    traps: list[str]
    severity: str
    volume: int = 0


def simulate(model: EffectiveModel, change: dict,
             candidate_names: list[str],
             volumes: dict[str, int] | None = None) -> list[NameImpact]:
    """Resolve each candidate through current and post-change models, diff, run
    trap detectors, and rank by severity then volume. Returns impacts that
    changed OR triggered a trap, most severe first."""
    volumes = volumes or {}
    after_model = apply_change(model, change)
    impacts: list[NameImpact] = []
    for name in candidate_names:
        b = resolve(name, model)
        a = resolve(name, after_model)
        traps = detect_traps(name, b, a, model, after_model, change)
        if b.answer_class == a.answer_class and b.source == a.source and not traps:
            continue  # no delta, no trap -> omit
        vol = volumes.get(name.rstrip(".").lower(), 0)
        impacts.append(NameImpact(name, b, a, traps, _severity(b, a, traps, vol), vol))

    rank = {"high": 0, "medium": 1, "none": 2}
    impacts.sort(key=lambda i: (rank.get(i.severity, 3), -i.volume))
    return impacts
