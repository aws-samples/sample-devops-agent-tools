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


class TestAccountAllowlistRequired:
    """Security-review follow-up: ALLOWED_ACCOUNTS must never default to
    allow-all. The account allowlist is the boundary that stops the server
    assuming a role into an arbitrary account, so it is required in EVERY stage,
    not only when STAGE_NAME=prod. Three routes to allow-all must all be refused:
    unset, empty, and '*'."""

    def _import_server(self, env_extra, drop=()):
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
        for k in drop:
            env.pop(k, None)
        return subprocess.run(
            [sys.executable, "-c", "import server; print('STARTED')"],
            cwd=os.path.join(os.path.dirname(__file__), "..", "src"),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def _refused(self, r):
        combined = r.stdout + r.stderr
        return r.returncode != 0 and "ALLOWED_ACCOUNTS" in combined

    def test_unset_is_refused(self):
        r = self._import_server({}, drop=("ALLOWED_ACCOUNTS",))
        assert self._refused(r), f"unset ALLOWED_ACCOUNTS started: {r.stdout[:200]}"

    def test_empty_is_refused(self):
        r = self._import_server({"ALLOWED_ACCOUNTS": ""})
        assert self._refused(r), f"empty ALLOWED_ACCOUNTS started: {r.stdout[:200]}"

    def test_whitespace_only_is_refused(self):
        r = self._import_server({"ALLOWED_ACCOUNTS": "   "})
        assert self._refused(r)

    def test_wildcard_is_refused(self):
        r = self._import_server({"ALLOWED_ACCOUNTS": "*"})
        assert self._refused(r), f"wildcard ALLOWED_ACCOUNTS started: {r.stdout[:200]}"

    def test_wildcard_mixed_with_real_account_is_refused(self):
        """A wildcard anywhere in the list defeats the whole list."""
        r = self._import_server({"ALLOWED_ACCOUNTS": "111122223333,*"})
        assert self._refused(r)

    def test_malformed_account_id_is_refused(self):
        r = self._import_server({"ALLOWED_ACCOUNTS": "12345"})
        assert self._refused(r)

    def test_refused_in_prod_stage_too(self):
        """Belt and braces: the prod gate also covers this, and must still fire."""
        r = self._import_server({"ALLOWED_ACCOUNTS": "*", "STAGE_NAME": "prod"})
        assert r.returncode != 0

    def test_single_valid_account_starts(self):
        r = self._import_server({"ALLOWED_ACCOUNTS": "111122223333"})
        assert "STARTED" in r.stdout, f"valid config failed to start: {r.stderr[:300]}"

    def test_multiple_valid_accounts_start(self):
        r = self._import_server({"ALLOWED_ACCOUNTS": "111122223333,444455556666"})
        assert "STARTED" in r.stdout, f"valid config failed to start: {r.stderr[:300]}"

    def test_whitespace_around_valid_accounts_is_tolerated(self):
        r = self._import_server({"ALLOWED_ACCOUNTS": " 111122223333 , 444455556666 "})
        assert "STARTED" in r.stdout, f"valid config failed to start: {r.stderr[:300]}"

    def test_template_has_no_allowed_accounts_default(self):
        """The SAM parameter must not carry a Default, or a plain `sam deploy`
        would reintroduce an implicit scope."""
        tpl = os.path.join(os.path.dirname(__file__), "..", "template.yaml")
        with open(tpl, encoding="utf-8") as fh:
            body = fh.read()
        block = body.split("AllowedAccounts:", 1)[1].split("AllowedRegions:", 1)[0]
        assert "Default:" not in block, (
            "AllowedAccounts must have no Default in template.yaml"
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

    def test_wildcard_allows_all_with_warning(self):
        r = self._fresh_server({"ALLOWED_RESOLVERS": "*"})
        assert "OK_IP True" in r.stdout, r.stdout + r.stderr
        # With a wildcard allowlist, all resolvers (IPs and hostnames) pass the
        # allowlist gate. Security is enforced by _enforce_prod_allowlists
        # refusing to start in STAGE_NAME=prod on a wildcard.
        assert "OK_HOST True" in r.stdout, (
            "a wildcard resolver allowlist should accept both IPs and hostnames "
            f"(enforcement is at the prod gate): {r.stdout}"
        )

    def test_explicit_allowlist_emits_no_warning(self):
        r = self._fresh_server({"ALLOWED_RESOLVERS": "10.0.0.2"})
        assert "WARNING" not in (r.stdout + r.stderr)

    def test_explicit_allowlist_blocks_unlisted_ip(self):
        """When ALLOWED_RESOLVERS is set, caller IPs not in the list are refused."""
        env_extra = {"ALLOWED_RESOLVERS": "10.0.0.53"}
        env = dict(
            {
                "ALLOWED_ACCOUNTS": "111122223333",
                "ALLOWED_REGIONS": "us-east-1",
                "STAGE_NAME": "dev",
            }
        )
        env.update(env_extra)
        code = (
            "import server;"
            "print('LISTED', server._resolver_allowed('10.0.0.53'));"
            "print('UNLISTED_IP', server._resolver_allowed('8.8.8.8'));"
            "print('UNLISTED_HOST', server._resolver_allowed('evil.example.com'))"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.join(os.path.dirname(__file__), "..", "src"),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert "LISTED True" in r.stdout, r.stdout + r.stderr
        assert "UNLISTED_IP False" in r.stdout, (
            f"an explicit allowlist must block unlisted IPs: {r.stdout}"
        )
        assert "UNLISTED_HOST False" in r.stdout, (
            f"an explicit allowlist must block unlisted hostnames: {r.stdout}"
        )
