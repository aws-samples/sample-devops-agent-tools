# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Mode B core: resolver engine, change application, trap detectors.

These are pure/deterministic and need no AWS. They encode the documented DNS
traps (VPCE PHZ shadow, broad FORWARD sweep, SNVA flag AND-ing, DNS Firewall
block, Route 53 Profile union shift) as regression cases.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dns_model import (  # noqa: E402
    EffectiveModel, FirewallRule, ResolverRule, Phz, Vpce,
    resolve, apply_change, simulate,
    BLOCKED, VPCE_PRIVATE, PHZ_PRIVATE, ONPREM, PUBLIC, NXDOMAIN,
)


def _base(**kw) -> EffectiveModel:
    defaults = dict(vpc_id="vpc-abc123")
    defaults.update(kw)
    return EffectiveModel(**defaults)


class TestResolverEngine:
    def test_default_public(self):
        r = resolve("www.example.com", _base())
        assert r.answer_class == PUBLIC

    def test_phz_private(self):
        m = _base(phzs=(Phz("internal.corp."),))
        assert resolve("db.internal.corp", m).answer_class == PHZ_PRIVATE

    def test_vpce_private_dns(self):
        m = _base(vpces=(Vpce("secretsmanager.us-east-1.amazonaws.com", True),),
                  snva_preference="ALL_DOMAINS")
        assert resolve("secretsmanager.us-east-1.amazonaws.com", m).answer_class == VPCE_PRIVATE

    def test_snva_gate_blocks_aws_override(self):
        # VERIFIED_DOMAINS_ONLY should NOT override an AWS FQDN -> stays public.
        m = _base(vpces=(Vpce("secretsmanager.us-east-1.amazonaws.com", True),),
                  snva_preference="VERIFIED_DOMAINS_ONLY")
        assert resolve("secretsmanager.us-east-1.amazonaws.com", m).answer_class == PUBLIC

    def test_specific_forward_beats_root(self):
        m = _base(resolver_rules=(
            ResolverRule(".", "FORWARD", "onprem"),
            ResolverRule("internal.corp.", "FORWARD", "onprem"),
        ))
        r = resolve("db.internal.corp", m)
        assert r.answer_class == ONPREM
        assert "internal.corp" in r.winner

    def test_onprem_zone_nxdomain_without_forward(self):
        # Declared on-prem zone but no FORWARD path -> .2 NXDOMAINs (correct).
        m = _base(onprem_zones=("internal.corp",))
        assert resolve("db.internal.corp", m).answer_class == NXDOMAIN

    def test_firewall_block_wins(self):
        m = _base(firewall_rules=(FirewallRule(("bad.example.",), "BLOCK", "NXDOMAIN"),))
        assert resolve("bad.example", m).answer_class == NXDOMAIN

    def test_dns_support_off_darkens_resolver(self):
        m = _base(phzs=(Phz("internal.corp."),), dns_support=False)
        assert resolve("db.internal.corp", m).answer_class == NXDOMAIN

    def test_dns_support_off_darkens_forward_and_firewall(self):
        # PY-H2: a dark VPC resolver must darken FORWARD and DNS Firewall paths
        # too, not just PHZ/VPCE. Previously these returned before the check.
        m = _base(dns_support=False,
                  resolver_rules=(ResolverRule("onprem.corp.", "FORWARD", "onprem"),),
                  firewall_rules=(FirewallRule(("bad.example.",), "BLOCK", "NXDOMAIN"),))
        assert resolve("host.onprem.corp", m).answer_class == NXDOMAIN
        assert resolve("bad.example", m).answer_class == NXDOMAIN
        assert "enableDnsSupport=false" in resolve("bad.example", m).winner

    def test_snva_specified_domains_only(self):
        # M1: SPECIFIED_DOMAINS_ONLY overrides an AWS FQDN only for a specified
        # domain, and blocks (public) otherwise - not allow-all.
        base_kw = dict(
            vpces=(Vpce("secretsmanager.us-east-1.amazonaws.com", True, "direct",
                        ("secretsmanager.us-east-1.amazonaws.com",)),),
            snva_preference="SPECIFIED_DOMAINS_ONLY",
        )
        m_in = _base(specified_domains=("secretsmanager.us-east-1.amazonaws.com",), **base_kw)
        assert resolve("secretsmanager.us-east-1.amazonaws.com", m_in).answer_class == VPCE_PRIVATE
        m_out = _base(specified_domains=("other.example",), **base_kw)
        assert resolve("secretsmanager.us-east-1.amazonaws.com", m_out).answer_class == PUBLIC

    def test_snva_verified_and_specified(self):
        # VERIFIED_DOMAINS_AND_SPECIFIED_DOMAINS behaves like SPECIFIED for an
        # AWS FQDN: override only when the name is in the specified set.
        base_kw = dict(
            vpces=(Vpce("secretsmanager.us-east-1.amazonaws.com", True, "direct",
                        ("secretsmanager.us-east-1.amazonaws.com",)),),
            snva_preference="VERIFIED_DOMAINS_AND_SPECIFIED_DOMAINS",
        )
        m_in = _base(specified_domains=("secretsmanager.us-east-1.amazonaws.com",), **base_kw)
        assert resolve("secretsmanager.us-east-1.amazonaws.com", m_in).answer_class == VPCE_PRIVATE
        m_out = _base(specified_domains=("other.example",), **base_kw)
        assert resolve("secretsmanager.us-east-1.amazonaws.com", m_out).answer_class == PUBLIC

    def test_recursive_rule_is_not_an_override(self):
        # The default '.' RECURSIVE Internet Resolver rule means "resolve
        # normally" - it must NOT be treated as a FORWARD/override. A public name
        # with only a RECURSIVE '.' rule still resolves PUBLIC; a PHZ name still
        # resolves via the PHZ.
        m = _base(
            resolver_rules=(ResolverRule(".", "RECURSIVE", ""),),
            phzs=(Phz("internal.corp."),),
        )
        assert resolve("www.example.com", m).answer_class == PUBLIC
        assert resolve("db.internal.corp", m).answer_class == PHZ_PRIVATE

    def test_forward_beats_system_equal_specificity(self):
        # Documented tie-break (L5): at equal specificity FORWARD wins over SYSTEM.
        m = _base(resolver_rules=(
            ResolverRule("amazonaws.com.", "SYSTEM", ""),
            ResolverRule("amazonaws.com.", "FORWARD", "onprem"),
        ))
        assert resolve("sts.us-east-1.amazonaws.com", m).answer_class == ONPREM

    def test_opaque_firewall_rule_is_opaque(self):
        # A cross-account shared firewall group whose domains are unreadable ->
        # any name's effect is unpredictable -> OPAQUE (not silently inert).
        from dns_model import FirewallRule, OPAQUE
        m = _base(firewall_rules=(
            FirewallRule(domains=(), action="BLOCK", block_response="NXDOMAIN",
                         source="direct", opaque=True),))
        r = resolve("anything.example.com", m)
        assert r.answer_class == OPAQUE
        assert "not readable" in r.winner

    def test_opaque_resolver_rule_is_opaque_when_no_concrete_match(self):
        # An opaque profile-delivered rule -> OPAQUE if nothing concrete claims
        # the name; a concrete match still wins over it.
        from dns_model import OPAQUE
        m = _base(resolver_rules=(
            ResolverRule("", "FORWARD", "", source="profile:rp-x", opaque=True),))
        assert resolve("whatever.example", m).answer_class == OPAQUE
        # Concrete PHZ match takes precedence over the opaque rule (firewall/rule
        # steps only OPAQUE-out when no concrete resolver rule matched; PHZ is
        # below resolver rules, so verify a concrete FORWARD wins):
        m2 = _base(resolver_rules=(
            ResolverRule("corp.example.", "FORWARD", "onprem", source="direct"),
            ResolverRule("", "FORWARD", "", source="profile:rp-x", opaque=True),))
        assert resolve("db.corp.example", m2).answer_class == ONPREM

    def test_opaque_firewall_precedes_everything(self):
        # Opaque firewall evaluates first (like any firewall) - even a name a PHZ
        # would answer is reported OPAQUE because the hidden block list might
        # cover it.
        from dns_model import FirewallRule, OPAQUE
        m = _base(
            phzs=(Phz("internal.corp."),),
            firewall_rules=(FirewallRule(domains=(), action="BLOCK",
                                         source="direct", opaque=True),),
        )
        assert resolve("db.internal.corp", m).answer_class == OPAQUE

    def test_ungated_resource_endpoint_shadow_ignores_snva_gate(self):
        # A Lattice resource-endpoint shadow over a CUSTOM domain is ungated:
        # the SNVA PrivateDnsPreference does not apply (it governs AWS FQDNs).
        # Even with the default VERIFIED_DOMAINS_ONLY gate, a custom-domain
        # shadow resolves private.
        from dns_model import Shadow
        m = _base(
            vpces=(Shadow("app.internal.example", True, "resource-endpoint:re-1",
                          ("app.internal.example",), gated=False),),
            snva_preference="VERIFIED_DOMAINS_ONLY",
        )
        assert resolve("app.internal.example", m).answer_class == VPCE_PRIVATE
        # Unserved subdomain under the resource-endpoint shadow -> NXDOMAIN.
        assert resolve("x.app.internal.example", m).answer_class == NXDOMAIN

    def test_ungated_shadow_on_aws_fqdn_still_ignores_gate(self):
        # Ungated endpoint shadows are not AWS FQDNs in practice, but confirm the
        # gate is bypassed purely by the gated flag, not the domain check.
        from dns_model import Shadow
        m = _base(
            vpces=(Shadow("svc.example.com", True, "resource-endpoint:re-2",
                          ("svc.example.com",), gated=False),),
            snva_preference="VERIFIED_DOMAINS_ONLY",
        )
        assert resolve("svc.example.com", m).answer_class == VPCE_PRIVATE

    def test_gated_interface_vpce_still_respects_gate(self):
        # Regression: the gated interface-VPCE path still honors the SNVA gate.
        m = _base(
            vpces=(Vpce("secretsmanager.us-east-1.amazonaws.com", True, "direct",
                        ("secretsmanager.us-east-1.amazonaws.com",)),),  # gated defaults True
            snva_preference="VERIFIED_DOMAINS_ONLY",
        )
        assert resolve("secretsmanager.us-east-1.amazonaws.com", m).answer_class == PUBLIC


class TestTrapDetectors:
    def test_vpce_shadow_nxdomain(self):
        # Real pipeline: with the private-DNS override active (ALL_DOMAINS), the
        # endpoint's shadow PHZ captures the whole apex. An unserved subdomain
        # -> NXDOMAIN. Driven through simulate(), NOT a hand-built model, so it
        # proves the trap fires on real input.
        m = _base(snva_preference="ALL_DOMAINS")
        change = {"type": "enable_vpce_private_dns",
                  "service_apex": "oidc.eks.us-east-1.amazonaws.com",
                  "served_names": ["oidc.eks.us-east-1.amazonaws.com"]}
        name = "abc123.oidc.eks.us-east-1.amazonaws.com"  # not a served record
        impacts = simulate(m, change, [name])
        assert impacts, "expected an impact for the shadowed subdomain"
        assert impacts[0].after.answer_class == NXDOMAIN
        assert "VPCE-shadow-NXDOMAIN" in impacts[0].traps
        assert impacts[0].severity == "high"

    def test_vpce_apex_still_resolves_private(self):
        # The exact apex (and served names) still resolve VPCE_PRIVATE - shadow
        # only NXDOMAINs unserved subdomains.
        m = _base()
        change = {"type": "enable_vpce_private_dns",
                  "service_apex": "secretsmanager.us-east-1.amazonaws.com",
                  "served_names": ["secretsmanager.us-east-1.amazonaws.com"]}
        after = apply_change(m, change)
        r = resolve("secretsmanager.us-east-1.amazonaws.com", after)
        # SNVA default VERIFIED_DOMAINS_ONLY blocks the AWS-FQDN override -> public
        assert r.answer_class == PUBLIC
        # With ALL_DOMAINS the apex resolves private:
        from dns_model import EffectiveModel as EM
        after2 = EM(**{**after.__dict__, "snva_preference": "ALL_DOMAINS"})
        assert resolve("secretsmanager.us-east-1.amazonaws.com", after2).answer_class == VPCE_PRIVATE

    def test_broad_forward_sweep(self):
        m = _base()
        change = {"type": "add_resolver_rule", "rule_type": "FORWARD",
                  "domain": ".", "target": "onprem"}
        impacts = simulate(m, change, ["sts.us-east-1.amazonaws.com"])
        assert impacts
        assert "broad-FORWARD-sweep" in impacts[0].traps
        assert impacts[0].after.answer_class == ONPREM
        assert impacts[0].severity == "high"

    def test_flag_and_mismatch(self):
        # VPCE private DNS present but SNVA gate leaves the AWS FQDN public.
        m = _base(vpces=(Vpce("secretsmanager.us-east-1.amazonaws.com", True),),
                  snva_preference="VERIFIED_DOMAINS_ONLY")
        change = {"type": "set_snva_preference", "preference": "VERIFIED_DOMAINS_ONLY"}
        from dns_model import detect_traps
        name = "secretsmanager.us-east-1.amazonaws.com"
        after_model = apply_change(m, change)
        b = resolve(name, m)
        a = resolve(name, after_model)
        traps = detect_traps(name, b, a, m, after_model, change)
        assert "flag-AND-mismatch" in traps

    def test_dns_firewall_block(self):
        m = _base()
        change = {"type": "associate_dns_firewall", "domains": ["evil.example."],
                  "action": "BLOCK", "block_response": "NXDOMAIN"}
        impacts = simulate(m, change, ["evil.example"])
        assert impacts and "DNS-Firewall-block" in impacts[0].traps

    def test_profile_union_shift(self):
        m = _base()
        change = {
            "type": "associate_profile", "profile_id": "rp-123",
            "resources": {"resolver_rules": [
                {"domain": "internal.corp.", "rule_type": "FORWARD", "target": "onprem"}
            ]},
        }
        impacts = simulate(m, change, ["db.internal.corp"])
        assert impacts
        assert "Profile-union-shift" in impacts[0].traps
        assert impacts[0].after.source == "profile:rp-123"

    def test_resolver_disabled(self):
        # M5: set_dhcp_dns that darkens the VPC resolver labels the break.
        m = _base(phzs=(Phz("internal.corp."),))
        change = {"type": "set_dhcp_dns", "dns_support": False}
        impacts = simulate(m, change, ["db.internal.corp"])
        assert impacts
        assert "resolver-disabled" in impacts[0].traps
        assert impacts[0].after.answer_class == NXDOMAIN

    def test_profile_source_only_shift_is_medium(self):
        # L4: a Profile-union-shift trap that only changed the source (answer
        # unchanged) is a benign ownership move -> medium, not high. Tested at
        # the _severity level since the additive model keeps a direct construct
        # winning, so a pure source-only shift is a severity-policy concern.
        from dns_model import _severity, Resolution, PHZ_PRIVATE
        before = Resolution("db.internal.corp", PHZ_PRIVATE, "PHZ 'internal.corp.'", "direct")
        after = Resolution("db.internal.corp", PHZ_PRIVATE, "PHZ 'internal.corp.'", "profile:rp-9")
        assert _severity(before, after, ["Profile-union-shift"], 0) == "medium"
        # A Profile shift that also changes the answer stays high.
        after_break = Resolution("db.internal.corp", NXDOMAIN, "x", "profile:rp-9")
        assert _severity(before, after_break, ["Profile-union-shift"], 0) == "high"
        # Any other trap remains high regardless.
        assert _severity(before, after, ["broad-FORWARD-sweep"], 0) == "high"


class TestSimulateRanking:
    def test_ranks_high_before_medium_and_omits_no_delta(self):
        m = _base(phzs=(Phz("internal.corp."),))
        change = {"type": "add_resolver_rule", "rule_type": "FORWARD",
                  "domain": ".", "target": "onprem"}
        names = ["sts.us-east-1.amazonaws.com", "www.example.com", "unchanged.internal.corp"]
        # internal.corp is PHZ before; '.' FORWARD sweeps it to onprem too -> changes.
        impacts = simulate(m, change, names, volumes={"sts.us-east-1.amazonaws.com": 5000})
        # All three change (root FORWARD sweeps everything), highest severity first.
        assert impacts[0].severity == "high"
        sev_order = [i.severity for i in impacts]
        assert sev_order == sorted(sev_order, key=lambda s: {"high": 0, "medium": 1, "none": 2}[s])
