# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Guards for the findings raised by the MCP security review (2026-07-28).

These assert the IAM template stays least-privilege and the fail-closed resolver
behaviour holds. They exist so a later change cannot silently re-widen a grant
that was deliberately narrowed.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SCOPED_ROLES = os.path.join(os.path.dirname(__file__), "..", "scoped-roles.yaml")
SERVER_PY = os.path.join(os.path.dirname(__file__), "..", "src", "server.py")


def _roles_yaml() -> str:
    with open(SCOPED_ROLES, encoding="utf-8") as fh:
        return fh.read()


def _readonly_role_block() -> str:
    """The DnsDiagnosticReadOnlyRole resource, up to the next role."""
    body = _roles_yaml()
    start = body.index("DnsDiagnosticReadOnlyRole:")
    end = body.index("DnsDiagnosticProbeRole:")
    return body[start:end]


def _granted_actions(block: str) -> list[str]:
    """IAM actions granted in a role block.

    Only real grants: `- <service>:<Action>` list entries and `Action: <x>`
    scalars. Comments and ARN fields are excluded, so an explanatory comment
    mentioning a wildcard, or a region wildcard inside an ARN, is not mistaken
    for a permission.
    """
    actions = []
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if line.startswith("- ") and ":" in line:
            candidate = line[2:].strip()
            # An action is service:Action -- reject ARNs and key: value pairs.
            if candidate.startswith("arn:") or " " in candidate:
                continue
            if candidate.count(":") == 1:
                actions.append(candidate)
        elif line.startswith("Action: "):
            candidate = line[len("Action: "):].strip()
            if candidate and candidate.count(":") == 1:
                actions.append(candidate)
    return actions


class TestF4NoLatentLogsGrant:
    """F-4: logs:StartQuery / GetQueryResults / DescribeLogGroups were granted but
    never called. Query-log enrichment is unimplemented; the grant must stay out
    until the code that uses it lands."""

    def test_no_logs_actions_anywhere_in_template(self):
        offenders = [
            a for a in _granted_actions(_roles_yaml()) if a.startswith("logs:")
        ]
        assert offenders == [], f"unexpected CloudWatch Logs grant: {offenders}"

    def test_server_makes_no_logs_api_calls(self):
        with open(SERVER_PY, encoding="utf-8") as fh:
            src = fh.read()
        for call in ("start_query", "get_query_results", "describe_log_groups"):
            assert call not in src, (
                f"server calls {call} but the IAM grant was removed -- "
                "restore the grant in the same change that adds the call"
            )


class TestF5LatticeGrantsAreExplicit:
    """F-5: vpc-lattice:List*/Get* wildcards would pick up any future API with a
    List/Get prefix. Only the two APIs actually called may be granted."""

    def test_no_lattice_wildcards(self):
        granted = _granted_actions(_readonly_role_block())
        offenders = [
            a for a in granted if a.startswith("vpc-lattice:") and "*" in a
        ]
        assert offenders == [], f"lattice wildcard reintroduced: {offenders}"

    def test_the_two_used_apis_are_granted(self):
        granted = _granted_actions(_readonly_role_block())
        for action in (
            "vpc-lattice:ListServiceNetworkVpcAssociations",
            "vpc-lattice:GetResourceConfiguration",
        ):
            assert action in granted, f"missing required grant {action}"

    def test_granted_lattice_apis_match_the_code(self):
        """Every lattice call in server.py must have a matching grant."""
        with open(SERVER_PY, encoding="utf-8") as fh:
            src = fh.read()
        granted = _granted_actions(_readonly_role_block())
        for snake, iam in (
            ("list_service_network_vpc_associations", "ListServiceNetworkVpcAssociations"),
            ("get_resource_configuration", "GetResourceConfiguration"),
        ):
            if snake in src:
                assert f"vpc-lattice:{iam}" in granted, (
                    f"server calls {snake} with no vpc-lattice:{iam} grant"
                )


class TestProbeRoleStaysMinimal:
    """The probe role's only privileged grant must remain a resource-scoped
    ssm:SendCommand. Nothing mutating may creep in."""

    def _probe_block(self) -> str:
        body = _roles_yaml()
        return body[body.index("DnsDiagnosticProbeRole:"):]

    def test_no_mutating_ssm_actions(self):
        granted = _granted_actions(self._probe_block())
        for bad in (
            "ssm:CreateDocument",
            "ssm:UpdateDocument",
            "ssm:DeleteDocument",
            "ssm:StartSession",
            "ssm:StartAutomationExecution",
            "ssm:PutParameter",
            "ssm:*",
        ):
            assert bad not in granted, f"probe role gained {bad}"

    def test_only_sendcommand_is_privileged(self):
        """Every ssm grant must be SendCommand or a read."""
        allowed = {
            "ssm:SendCommand",
            "ssm:GetCommandInvocation",
            "ssm:ListCommandInvocations",
            "ssm:DescribeInstanceInformation",
        }
        granted = {a for a in _granted_actions(self._probe_block()) if a.startswith("ssm:")}
        assert granted <= allowed, f"unexpected ssm grants: {granted - allowed}"

    def test_sendcommand_is_document_scoped(self):
        block = self._probe_block()
        assert "document/${DiagnosticDocumentName}" in block, (
            "ssm:SendCommand must stay scoped to the single diagnostic document"
        )


class TestF1ResolverFailClosed:
    """F-1: an empty resolver allowlist must permit literal IPs only and refuse
    every hostname, and the wildcard case must warn at startup."""

    def _fresh_server(self, env_extra):
        """Import server.py in a subprocess with a controlled environment."""
        env = dict(os.environ)
        env.update(
            {
                "ALLOWED_ACCOUNTS": "111122223333",
                "ALLOWED_REGIONS": "us-east-1",
                "STAGE_NAME": "dev",
            }
        )
        env.update(env_extra)
        code = (
            "import server;"
            "print('OK_IP', server._valid_resolver('10.0.0.2'));"
            "print('OK_HOST', server._valid_resolver('resolver.example.com'))"
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.join(os.path.dirname(__file__), "..", "src"),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_wildcard_resolvers_emits_warning(self):
        r = self._fresh_server({"ALLOWED_RESOLVERS": "*"})
        combined = r.stdout + r.stderr
        assert "WARNING" in combined and "ALLOWED_RESOLVERS" in combined, (
            f"expected a wildcard-resolver warning, got: {combined[:400]}"
        )

    def test_wildcard_still_refuses_hostnames(self):
        r = self._fresh_server({"ALLOWED_RESOLVERS": "*"})
        assert "OK_IP True" in r.stdout, r.stdout + r.stderr
        assert "OK_HOST False" in r.stdout, (
            "a wildcard resolver allowlist must STILL refuse hostnames "
            f"(fail-closed): {r.stdout}"
        )

    def test_explicit_allowlist_emits_no_warning(self):
        r = self._fresh_server({"ALLOWED_RESOLVERS": "10.0.0.2"})
        assert "WARNING" not in (r.stdout + r.stderr)
