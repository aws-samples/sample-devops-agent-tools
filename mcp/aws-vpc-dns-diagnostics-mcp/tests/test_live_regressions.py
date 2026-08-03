# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Regression tests for issues found during live validation against real AWS
fixtures (2026-07-27). These were not caught by the pre-existing unit tests.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dns_model import (  # noqa: E402
    EffectiveModel,
    Phz,
    ResolverRule,
    apply_change,
    resolve,
)


def _model(**kw):
    kw.setdefault("vpc_id", "vpc-test")
    return EffectiveModel(**kw)


class TestAddResolverRuleTargetShape:
    """apply_change accepted only 'target', so a caller using the documented
    'target_ips' shape built a rule whose target rendered empty in the report."""

    def test_target_ips_list_is_rendered(self):
        m = apply_change(
            _model(),
            {
                "type": "add_resolver_rule",
                "domain": ".",
                "rule_type": "FORWARD",
                "target_ips": ["10.99.0.53"],
            },
        )
        assert m.resolver_rules[-1].target == "10.99.0.53"

    def test_multiple_target_ips_are_joined(self):
        m = apply_change(
            _model(),
            {
                "type": "add_resolver_rule",
                "domain": "onprem.corp.",
                "target_ips": ["10.99.0.53", "10.99.1.53"],
            },
        )
        assert m.resolver_rules[-1].target == "10.99.0.53, 10.99.1.53"

    def test_target_ips_as_bare_string_is_accepted(self):
        m = apply_change(
            _model(),
            {"type": "add_resolver_rule", "domain": ".", "target_ips": "10.99.0.53"},
        )
        assert m.resolver_rules[-1].target == "10.99.0.53"

    def test_explicit_target_still_wins(self):
        m = apply_change(
            _model(),
            {
                "type": "add_resolver_rule",
                "domain": ".",
                "target": "onprem",
                "target_ips": ["10.99.0.53"],
            },
        )
        assert m.resolver_rules[-1].target == "onprem"

    def test_no_target_does_not_crash(self):
        m = apply_change(_model(), {"type": "add_resolver_rule", "domain": "."})
        assert m.resolver_rules[-1].target == ""


class TestForwardTargetRendering:
    """An empty target used to render as a dangling '-> ' in the impact table."""

    def test_unspecified_target_is_labelled(self):
        m = _model(
            resolver_rules=(ResolverRule(".", "FORWARD", "", "direct"),),
            phzs=(Phz("internal.corp.", "direct"),),
        )
        r = resolve("db.internal.corp", m)
        assert "(target unspecified)" in r.winner

    def test_populated_target_renders_normally(self):
        m = _model(
            resolver_rules=(ResolverRule(".", "FORWARD", "10.99.0.53", "direct"),),
            phzs=(Phz("internal.corp.", "direct"),),
        )
        r = resolve("db.internal.corp", m)
        assert "10.99.0.53" in r.winner
        assert "unspecified" not in r.winner


class TestProbeRoleReadRequirements:
    """dns_probe_context's DHCP discovery calls DescribeVpcs and
    DescribeDhcpOptions. The probe role originally granted neither, so Mode A
    failed live with UnauthorizedOperation. Guard the IAM template."""

    def test_probe_role_grants_dhcp_discovery_reads(self):
        tpl = os.path.join(
            os.path.dirname(__file__), "..", "scoped-roles.yaml"
        )
        with open(tpl, encoding="utf-8") as fh:
            body = fh.read()
        probe = body.split("DnsDiagnosticProbeRole:", 1)[1]
        for action in ("ec2:DescribeVpcs", "ec2:DescribeDhcpOptions"):
            assert action in probe, f"probe role missing {action}"
