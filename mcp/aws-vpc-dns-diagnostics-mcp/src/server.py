# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
DNS Diagnostic MCP Server for AWS DevOps Agent.

Three tool families in one server:

  * list_sops / get_sop - bundled diagnostic runbooks carrying the decision
                    trees, precedence model, trap semantics, and reporting rules
                    for interpreting results. The agent fetches guidance at
                    runtime instead of relying on preloaded instructions.
  * dns_probe_*    (Mode A) - live, comparative, multi-resolver DNS diagnosis run
                    inside a target EC2 instance via SSM Run Command. Returns a
                    per-name, per-resolver, per-family answer matrix plus a
                    VPC-attribute precondition block.
  * dns_simulate_* (Mode B) - symbolic pre-change validation of the VPC's
                    effective (direct + Route 53 Profile-inherited) DNS
                    resolution, using control-plane reads only.

Safety model:
  * Enforced allowlists. Mode A exposes only parameterized probe templates with
    strict input validation - no free shell. Mode B is read-only describe/list.
  * Per-tool-family credential scoping. The function assumes a read-only role for
    simulate calls and a separate probe role (whose sole privileged grant is a
    resource-scoped ssm:SendCommand to one diagnostic document) for probe calls.
    A read-only call never rides on a role holding ssm:SendCommand.
  * Fail-closed production enforcement. Wildcard allowlists are refused when
    STAGE_NAME=prod.
  * Account / region / VPC / resolver allowlists enforced in one place.

Transport: Streamable HTTP (via Lambda Web Adapter + Function URL, SigV4 auth).
"""

import ipaddress
import json
import os
import re
import time

import boto3
from fastmcp import FastMCP

mcp = FastMCP(
    "aws-vpc-dns-diagnostics-mcp",
    instructions=(
        "DNS diagnostics for AWS VPCs. Three tool families: list_sops/get_sop "
        "return diagnostic runbooks with the decision trees, precedence model, "
        "and reporting rules for interpreting results - call list_sops first if "
        "you are unsure which procedure applies, or get_sop('Z-general-triage') "
        "for a vague symptom. dns_probe_* runs live, comparative multi-resolver "
        "DNS queries inside an EC2 instance via SSM (Mode A, ground truth); "
        "dns_simulate_* predicts what a proposed DNS control-plane change would "
        "break by symbolically resolving the VPC's effective config (Mode B, "
        "read-only). Only allowlisted probes and read-only describe calls are "
        "permitted."
    ),
)

sts_client = boto3.client("sts")

# The VPC Amazon-provided resolver, per family. In IPv6-only subnets only the
# IPv6 address is present; in dualstack both answer.
VPC_RESOLVER_IPV4 = "169.254.169.253"
VPC_RESOLVER_IPV6 = "fd00:ec2::253"


# ============================================================
# Configuration & allowlists
# ============================================================

def _load_allowlist(env_var: str) -> set[str]:
    """Load a comma-separated allowlist from an env var. '*' means allow-all.

    An absent env var is treated as '*' for regions, VPCs, and resolvers.
    ALLOWED_ACCOUNTS is exempt: _enforce_account_allowlist() runs before this and
    refuses to start the server when it is unset, empty, or '*', so accounts can
    never load as an allow-all empty set.
    """
    raw = os.environ.get(env_var, "*").strip()
    if raw == "*":
        return set()  # Empty set == allow all
    return {v.strip().lower() for v in raw.split(",") if v.strip()}


def _enforce_prod_allowlists():
    """Fail-closed: refuse to start if any allowlist is '*' in production."""
    stage = os.environ.get("STAGE_NAME", "").lower()
    if stage != "prod":
        return
    wildcards = [
        var
        for var in ("ALLOWED_ACCOUNTS", "ALLOWED_REGIONS", "ALLOWED_VPCS", "ALLOWED_RESOLVERS")
        if os.environ.get(var, "*").strip() == "*"
    ]
    if wildcards:
        raise RuntimeError(
            "SECURITY: Production deployment requires explicit allowlists. "
            f"The following are set to '*' (wildcard): {', '.join(wildcards)}. "
            "Set each to a comma-separated list of permitted values."
        )


def _warn_wildcard_resolvers():
    """Surface the one wildcard that widens the blast radius beyond read-only.

    An empty/wildcard ALLOWED_RESOLVERS lets a caller name any resolver IP, and
    dns_probe_compare then sends a DNS query from the target instance to that IP.
    The queried name is DNS-charset only and capped at 253 characters, so the
    channel is narrow, but it IS a caller-directed outbound query -- the only
    outbound path in this server a caller can point somewhere new.

    STAGE_NAME=prod refuses to start on this (see _enforce_prod_allowlists).
    Outside prod it is permitted, so log it loudly: a deployment attached to a
    DevOps Agent should set an explicit resolver allowlist regardless of stage.
    """
    if os.environ.get("ALLOWED_RESOLVERS", "*").strip() != "*":
        return
    stage = os.environ.get("STAGE_NAME", "(unset)")
    print(
        "WARNING: ALLOWED_RESOLVERS is '*' (wildcard) with STAGE_NAME="
        f"{stage}. dns_probe_compare will accept ANY caller-supplied resolver "
        "IP or hostname and query it from the target instance. Do NOT use a "
        "wildcard resolver allowlist in any deployment reachable by AWS DevOps "
        "Agent -- set ALLOWED_RESOLVERS to a comma-separated list of permitted "
        "resolver addresses.",
        flush=True,
    )


def _enforce_account_allowlist():
    """Fail-closed on ALLOWED_ACCOUNTS in EVERY stage, not just prod.

    The account allowlist is the boundary that stops the server assuming a role
    into an arbitrary account, so it is the one list that must never default to
    allow-all. _enforce_prod_allowlists() only guards STAGE_NAME=prod; a dev or
    staging deployment attached to a real Agent Space would otherwise accept any
    account_id a caller supplied.

    Refuses three ways to reach allow-all: unset, empty, and '*'.
    """
    raw = os.environ.get("ALLOWED_ACCOUNTS", "").strip()
    entries = [v.strip() for v in raw.split(",") if v.strip()]
    if not entries:
        raise RuntimeError(
            "SECURITY: ALLOWED_ACCOUNTS is required and must list at least one "
            "12-digit AWS account ID. It is unset or empty. This server will not "
            "start with an implicit allow-all account scope, in any stage."
        )
    if any(e == "*" for e in entries):
        raise RuntimeError(
            "SECURITY: ALLOWED_ACCOUNTS does not accept '*'. Set it to a "
            "comma-separated list of the specific account IDs the tools may "
            "inspect."
        )
    malformed = [e for e in entries if not re.fullmatch(r"[0-9]{12}", e)]
    if malformed:
        raise RuntimeError(
            "SECURITY: ALLOWED_ACCOUNTS entries must be 12-digit AWS account "
            f"IDs. Rejected: {', '.join(malformed)}."
        )


_enforce_prod_allowlists()
_enforce_account_allowlist()
_warn_wildcard_resolvers()

ALLOWED_ACCOUNTS = _load_allowlist("ALLOWED_ACCOUNTS")
ALLOWED_REGIONS = _load_allowlist("ALLOWED_REGIONS")
ALLOWED_VPCS = _load_allowlist("ALLOWED_VPCS")
ALLOWED_RESOLVERS = _load_allowlist("ALLOWED_RESOLVERS")

PROBE_ROLE_ARN_PATTERN = os.environ.get("PROBE_ROLE_ARN_PATTERN", "")
READONLY_ROLE_ARN_PATTERN = os.environ.get("READONLY_ROLE_ARN_PATTERN", "")
DIAGNOSTIC_DOCUMENT_NAME = os.environ.get("DIAGNOSTIC_DOCUMENT_NAME", "dns-diagnostic-probe")


def _validate(value: str, allowlist: set[str], label: str) -> tuple[bool, str]:
    """Generic allowlist check. Empty allowlist == allow all.

    NOTE: ALLOWED_ACCOUNTS can never reach this function empty --
    _enforce_account_allowlist() refuses to start the server in that state. The
    allow-all-on-empty behaviour here applies to regions and VPCs only.
    """
    if not allowlist:
        return True, ""
    if value.lower() not in allowlist:
        return False, (
            f"ERROR: {label} '{value}' is not in the allowed list. "
            f"Permitted: {', '.join(sorted(allowlist))}."
        )
    return True, ""


# ============================================================
# Per-tool-family credential scoping
# ============================================================

def _role_arn_for(account_id: str, pattern: str) -> str:
    """Resolve a per-account role ARN from a pattern containing a '*' account."""
    return pattern.replace("*", account_id, 1)


def _assume(account_id: str, region: str, pattern: str, session_name: str):
    """
    Assume the given scoped role in the target account and return a boto3
    session bound to that role and region. Mode A uses the probe-role pattern;
    Mode B uses the read-only pattern. The two are never interchangeable.
    """
    role_arn = _role_arn_for(account_id, pattern)
    resp = sts_client.assume_role(RoleArn=role_arn, RoleSessionName=session_name)
    c = resp["Credentials"]
    return boto3.Session(
        aws_access_key_id=c["AccessKeyId"],
        aws_secret_access_key=c["SecretAccessKey"],
        aws_session_token=c["SessionToken"],
        region_name=region,
    )


def _preflight(account_id: str, region: str, vpc_id: str | None) -> tuple[bool, str]:
    """Shared allowlist gate for both tool families."""
    for value, allowlist, label in (
        (account_id, ALLOWED_ACCOUNTS, "Account"),
        (region, ALLOWED_REGIONS, "Region"),
    ):
        ok, msg = _validate(value, allowlist, label)
        if not ok:
            return ok, msg
    if vpc_id is not None:
        ok, msg = _validate(vpc_id, ALLOWED_VPCS, "VPC")
        if not ok:
            return ok, msg
    return True, ""


# ============================================================
# Mode A - probe command allowlist + input validation
# ============================================================

# Strict input validators. These are the enforced hard boundary: the model
# cannot improvise beyond these parameterized templates.
_NAME_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9_-]{1,63}\.)*[a-zA-Z0-9_-]{1,63}\.?$")
_SHELL_META_RE = re.compile(r"[;&|`$(){}<>\n\r\\'\"* \t]")
_FAMILY_ENUM = {"A", "AAAA"}


def _valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name)) and not _SHELL_META_RE.search(name)


def _valid_resolver(resolver: str) -> bool:
    """Syntax check: resolver must be a literal IP or a well-formed hostname."""
    if _SHELL_META_RE.search(resolver):
        return False
    try:
        ipaddress.ip_address(resolver)
        return True
    except ValueError:
        pass
    # Not an IP - must be a well-formed hostname (no shell metacharacters, DNS
    # charset). Allowlist enforcement happens separately in _resolver_allowed().
    return bool(_NAME_RE.match(resolver))


def _resolver_allowed(resolver: str) -> bool:
    """Allowlist gate: is this resolver permitted by ALLOWED_RESOLVERS?

    When ALLOWED_RESOLVERS is non-empty, BOTH IPs and hostnames must appear in
    it. When empty (wildcard), all syntactically valid resolvers pass (gated at
    import time by _enforce_prod_allowlists / _warn_wildcard_resolvers).

    DHCP-discovered resolvers bypass this check because they originate from
    VPC infrastructure (operator-configured DHCP option set), not from the
    caller. The exemption is applied at the call site in dns_probe_compare,
    not here.
    """
    if not ALLOWED_RESOLVERS:
        return True
    return resolver.lower() in ALLOWED_RESOLVERS


def _valid_family(family: str) -> bool:
    return family in _FAMILY_ENUM


# The probe set is defined INSIDE the diagnostic SSM document, which accepts only
# the three pattern-validated parameters below and renders a fixed, read-only
# command set. The server does NOT send a command string - it sends structured
# parameters - so the document is the on-instance enforcement boundary and the
# server-side validators here are the first, independent layer.
PROBE_PARAM_NAMES = ("Name", "Resolver", "Family")


def _ssm_reachable(session, instance_id: str) -> bool:
    """Whether the SSM agent on the instance is registered/reachable. Checked
    once per dns_probe_compare (L3) - never fall back to a public path."""
    info = session.client("ssm").describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
    )
    return bool(info.get("InstanceInformationList"))


_SSM_UNREACHABLE_MSG = (
    "SSM is not reachable for instance {iid}. Ensure SSM VPC endpoints "
    "(ssm, ssmmessages, ec2messages) are in place and the instance role has "
    "AmazonSSMManagedInstanceCore. Not falling back to a public path."
)


def _ssm_run_probe(session, instance_id: str, name: str, resolver: str, family: str) -> dict:
    """
    Invoke the diagnostic SSM document for one (name, resolver, family) triple and
    poll for the result. Sends structured parameters only - never a command
    string. Read-only diagnostics. Assumes SSM reachability was already checked
    by the caller (see _ssm_reachable).
    """
    ssm = session.client("ssm")
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName=DIAGNOSTIC_DOCUMENT_NAME,
        Parameters={"Name": [name], "Resolver": [resolver], "Family": [family]},
    )
    command_id = resp["Command"]["CommandId"]
    for _ in range(30):
        time.sleep(2)
        try:
            inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            # SSM can briefly 404 the invocation right after send_command; retry.
            continue
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            return {
                "status": inv["Status"],
                "stdout": inv.get("StandardOutputContent", ""),
                "stderr": inv.get("StandardErrorContent", ""),
            }
    return {"error": "Timed out waiting for SSM command invocation."}


# ============================================================
# Mode A - tools (dns_probe_*)
# ============================================================

def _read_dhcp_dns(ec2, vpc_id: str) -> dict:
    """
    Read the VPC's associated DHCP option set and extract the DNS-relevant
    values: domain-name-servers and domain-name. Returns a dict with the raw
    server list and a normalized view.

    'AmazonProvidedDNS' is the sentinel meaning the VPC .2 resolver (no custom
    resolver). Anything else is a custom/explicit resolver configured VPC-wide.
    """
    vpcs = ec2.describe_vpcs(VpcIds=[vpc_id]).get("Vpcs", [])
    dhcp_id = vpcs[0].get("DhcpOptionsId", "") if vpcs else ""
    servers: list[str] = []
    domain_name = ""
    if dhcp_id:
        opts = ec2.describe_dhcp_options(DhcpOptionsIds=[dhcp_id]).get("DhcpOptions", [])
        for cfg in (opts[0].get("DhcpConfigurations", []) if opts else []):
            key = cfg.get("Key")
            vals = [v.get("Value", "") for v in cfg.get("Values", [])]
            if key == "domain-name-servers":
                servers = vals
            elif key == "domain-name":
                domain_name = vals[0] if vals else ""
    is_amazon = servers == ["AmazonProvidedDNS"]
    # Custom resolver IPs are the non-sentinel entries.
    custom = [s for s in servers if s and s != "AmazonProvidedDNS"]
    return {
        "dhcp_options_id": dhcp_id,
        "servers": servers,
        "domain_name": domain_name,
        "is_amazon_provided": is_amazon,
        "custom_servers": custom,
    }


@mcp.tool()
def dns_probe_context(account_id: str, region: str, instance_id: str) -> str:
    """
    Collect the VPC-attribute precondition block and host DNS context for an
    instance BEFORE interpreting any resolution result.

    Reports enableDnsSupport (gates the whole VPC resolver - if false, .2 and
    the IPv6 resolver do not answer at all), enableDnsHostnames, the instance's
    addressing (IPv4 / IPv6 / dualstack), and the VPC DHCP option set's
    domain-name-servers / domain-name (the VPC-INTENDED resolver, VPC-wide). Use
    dns_probe_compare to fetch the instance-ACTUAL /etc/resolv.conf and compare;
    a mismatch between the two means the instance is not using the resolver the
    VPC hands out via DHCP.

    Args:
        account_id: Target AWS account ID.
        region: Target region.
        instance_id: EC2 instance ID to inspect.

    Returns:
        A markdown precondition + context report.
    """
    ok, msg = _preflight(account_id, region, None)
    if not ok:
        return msg
    session = _assume(account_id, region, PROBE_ROLE_ARN_PATTERN, "dns-probe-context")
    ec2 = session.client("ec2")

    inst = ec2.describe_instances(InstanceIds=[instance_id])
    reservations = inst.get("Reservations", [])
    if not reservations or not reservations[0].get("Instances"):
        return f"ERROR: instance {instance_id} not found in {account_id}/{region}."
    instance = reservations[0]["Instances"][0]
    vpc_id = instance.get("VpcId", "")

    ok, msg = _validate(vpc_id, ALLOWED_VPCS, "VPC")
    if not ok:
        return msg

    dns_support = ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute="enableDnsSupport")
    dns_hostnames = ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute="enableDnsHostnames")
    support = dns_support["EnableDnsSupport"]["Value"]
    hostnames = dns_hostnames["EnableDnsHostnames"]["Value"]

    dhcp = _read_dhcp_dns(ec2, vpc_id)

    # Addressing family from the instance ENIs.
    has_v4 = bool(instance.get("PrivateIpAddress"))
    has_v6 = any(ni.get("Ipv6Addresses") for ni in instance.get("NetworkInterfaces", []))
    stack = "dualstack" if (has_v4 and has_v6) else ("ipv6-only" if has_v6 else "ipv4-only")

    lead = ""
    if not support:
        lead = (
            "> LEAD FINDING: enableDnsSupport=FALSE - the VPC resolver is "
            "intentionally dark. Neither the IPv4 (.2) nor IPv6 resolver will "
            "answer, and any custom/on-prem resolver that forwards back to the "
            "VPC resolver will also fail. Interpret all probe rows in this light.\n\n"
        )

    # DHCP DNS reporting: VPC-intended resolver, VPC-wide.
    if dhcp["is_amazon_provided"]:
        dhcp_line = "AmazonProvidedDNS (VPC .2 resolver - no custom resolver)"
    elif dhcp["custom_servers"]:
        dhcp_line = (
            f"CUSTOM: {', '.join(dhcp['custom_servers'])} "
            f"(VPC-intended resolver; compare against instance /etc/resolv.conf "
            f"via dns_probe_compare - a mismatch means the box is not using it)"
        )
    else:
        dhcp_line = "(no domain-name-servers in the DHCP option set)"
    dhcp_domain = dhcp["domain_name"] or "(none)"

    return (
        f"{lead}"
        f"**VPC-attribute precondition ({vpc_id})**\n\n"
        f"| attribute | value |\n| --- | --- |\n"
        f"| enableDnsSupport | {support} |\n"
        f"| enableDnsHostnames | {hostnames} |\n"
        f"| instance addressing | {stack} |\n"
        f"| DHCP option set | {dhcp['dhcp_options_id'] or '(none)'} |\n"
        f"| DHCP domain-name-servers | {dhcp_line} |\n"
        f"| DHCP domain-name | {dhcp_domain} |\n\n"
        f"Probe the VPC resolver at: "
        f"{VPC_RESOLVER_IPV4 if has_v4 else '(no IPv4)'} / "
        f"{VPC_RESOLVER_IPV6 if has_v6 else '(no IPv6)'}."
        + (f"\n\nAlso probe the DHCP-configured resolver(s): "
           f"{', '.join(dhcp['custom_servers'])}." if dhcp["custom_servers"] else "")
    )


@mcp.tool()
def dns_probe_compare(
    account_id: str,
    region: str,
    instance_id: str,
    name: str,
    resolvers: list[str] | None = None,
    families: list[str] | None = None,
    include_dhcp_dns: bool = True,
) -> str:
    """
    Run the allowlisted DNS probe set inside an instance against multiple
    resolvers and return a comparison. Use this to see what a name actually
    resolves to from the subnet, which resolver answered, and how a custom
    resolver's answer differs from the VPC .2 / IPv6 resolver.

    Only parameterized, validated probes run - no arbitrary commands.

    Resolver set assembly:
      * `resolvers` you pass explicitly, PLUS
      * when include_dhcp_dns is true (default), the VPC DHCP option set's
        domain-name-servers are auto-added (the VPC-INTENDED custom resolver, so
        you do not have to look it up). AmazonProvidedDNS is expanded to the VPC
        .2 resolver address. If no resolver is supplied or discoverable, the
        call errors rather than probing nothing.

    Args:
        account_id: Target AWS account ID.
        region: Target region.
        instance_id: EC2 instance ID to run probes from (via SSM).
        name: DNS name to resolve (validated against a strict DNS charset).
        resolvers: Optional resolver IPs (or allowlisted hostnames) to compare.
        families: Record families to probe; defaults to ["A", "AAAA"].
        include_dhcp_dns: Auto-add the VPC DHCP-configured resolver(s) (default true).

    Returns:
        A markdown comparison of each resolver's answer and identity.
    """
    ok, msg = _preflight(account_id, region, None)
    if not ok:
        return msg
    if not _valid_name(name):
        return f"ERROR: '{name}' is not a valid DNS name."
    fams = families or ["A", "AAAA"]
    for f in fams:
        if not _valid_family(f):
            return f"ERROR: invalid family '{f}'. Allowed: A, AAAA."

    session = _assume(account_id, region, PROBE_ROLE_ARN_PATTERN, "dns-probe-compare")
    ec2 = session.client("ec2")

    # Look up the instance's VPC + addressing once. Used for the ALLOWED_VPCS
    # gate (M2) and stack-aware resolver expansion (M3).
    inst = ec2.describe_instances(InstanceIds=[instance_id]).get("Reservations", [])
    if not inst or not inst[0].get("Instances"):
        return f"ERROR: instance {instance_id} not found in {account_id}/{region}."
    instance = inst[0]["Instances"][0]
    vpc_id = instance.get("VpcId", "")

    # M2: enforce the VPC allowlist here too (mirrors dns_probe_context) so the
    # allowlist is truly "enforced in one place" for both probe tools.
    ok, msg = _validate(vpc_id, ALLOWED_VPCS, "VPC")
    if not ok:
        return msg

    has_v4 = bool(instance.get("PrivateIpAddress"))
    has_v6 = any(ni.get("Ipv6Addresses") for ni in instance.get("NetworkInterfaces", []))

    # Assemble the resolver set: explicit + DHCP-discovered (dedup, order-stable).
    resolver_set: list[str] = list(resolvers or [])
    dhcp_note = ""
    if include_dhcp_dns and vpc_id:
        dhcp = _read_dhcp_dns(ec2, vpc_id)
        discovered = list(dhcp["custom_servers"])
        if dhcp["is_amazon_provided"]:
            # M3: expand AmazonProvidedDNS to the resolver address(es) that
            # actually exist for this instance's stack. IPv6-only subnets have
            # no 169.254.169.253 - only fd00:ec2::253.
            if has_v4:
                discovered.append(VPC_RESOLVER_IPV4)
            if has_v6:
                discovered.append(VPC_RESOLVER_IPV6)
        added = [r for r in discovered if r not in resolver_set]
        resolver_set.extend(added)
        if added:
            dhcp_note = f" (auto-added from DHCP option set: {', '.join(added)})"

    if not resolver_set:
        return (
            "ERROR: no resolvers to probe. Pass `resolvers` explicitly or leave "
            "include_dhcp_dns=true on a VPC whose DHCP option set names a resolver."
        )

    # Two-pass validation: caller-supplied resolvers are gated by both syntax
    # AND ALLOWED_RESOLVERS; DHCP-discovered resolvers are exempt from the
    # allowlist because they originate from VPC infrastructure, not the caller.
    caller_supplied = set(resolvers or [])
    for r in resolver_set:
        if not _valid_resolver(r):
            return (
                f"ERROR: resolver '{r}' is not a valid IP or hostname. "
                "The comparison feature must not become an "
                "arbitrary-egress primitive."
            )
        if r in caller_supplied and not _resolver_allowed(r):
            return (
                f"ERROR: resolver '{r}' is not in ALLOWED_RESOLVERS. "
                f"Permitted: {', '.join(sorted(ALLOWED_RESOLVERS))}."
            )

    # L3: check SSM reachability ONCE, not per (resolver, family) triple.
    if not _ssm_reachable(session, instance_id):
        return _SSM_UNREACHABLE_MSG.format(iid=instance_id)

    # M4: a single unreachable resolver must NOT abort the whole comparison -
    # capture its error into that section and keep probing the rest.
    sections = []
    for resolver in resolver_set:
        for family in fams:
            result = _ssm_run_probe(session, instance_id, name, resolver, family)
            if "error" in result:
                sections.append(
                    f"### resolver {resolver} ({family})\n"
                    f"status: ERROR\n\n"
                    f"```\n{result['error']}\n```"
                )
                continue
            sections.append(
                f"### resolver {resolver} ({family})\n"
                f"status: {result['status']}\n\n"
                f"```\n{result['stdout'].strip()}\n```"
            )
    header = (
        f"**DNS probe comparison for `{name}`** (instance {instance_id}, "
        f"{account_id}/{region})\n\n"
        f"Resolvers probed: {', '.join(resolver_set)}{dhcp_note}\n\n"
        "Note: agreement is not the goal - the *correct* answer is. Judge each "
        "resolver against the expected winner for the name's category "
        "(AWS-service FQDN / PrivateLink-backed / PHZ / on-prem corp zone / public).\n"
    )
    return header + "\n\n".join(sections)


# ============================================================
# Mode B - tools (dns_simulate_*)
# ============================================================

from dns_model import (  # noqa: E402
    EffectiveModel, FirewallRule, ResolverRule, Phz, Vpce,
    resolve, simulate, PROFILE_PROPAGATION_SECONDS,
)


def _build_effective_model(session, vpc_id: str, onprem_zones: list[str] | None = None) -> EffectiveModel:
    """
    Build the VPC's effective DNS model from live control-plane reads: the union
    of directly-attached resources and Route 53 Profile-inherited resources. Each
    construct is tagged with its source ("direct" or "profile:<id>"). Read-only.
    """
    r53r = session.client("route53resolver")
    r53p = session.client("route53profiles")
    r53 = session.client("route53")
    ec2 = session.client("ec2")
    lattice = session.client("vpc-lattice")
    onprem = tuple((onprem_zones or []))

    # --- VPC attribute: resolver on/off ---
    dns_support = ec2.describe_vpc_attribute(
        VpcId=vpc_id, Attribute="enableDnsSupport"
    )["EnableDnsSupport"]["Value"]

    # --- SNVA PrivateDnsPreference (live) ---
    # Read the VPC's service-network associations; the DNS-override gate is
    # driven by dnsOptions.privateDnsPreference (+ specified domains). Default to
    # VERIFIED_DOMAINS_ONLY when the VPC has no SNVA (the service default).
    snva_preference = "VERIFIED_DOMAINS_ONLY"
    specified_domains: tuple[str, ...] = ()
    try:
        assocs = lattice.list_service_network_vpc_associations(
            vpcIdentifier=vpc_id
        ).get("items", [])
        # Prefer an association that actually enables private DNS; else the first.
        chosen = next((a for a in assocs if a.get("privateDnsEnabled")), assocs[0] if assocs else None)
        if chosen:
            dns_opts = chosen.get("dnsOptions") or {}
            snva_preference = dns_opts.get("privateDnsPreference", "VERIFIED_DOMAINS_ONLY")
            specified_domains = tuple(dns_opts.get("privateDnsSpecifiedDomains", []) or [])
    except Exception:
        # vpc-lattice not available / no permission -> keep the service default.
        pass

    resolver_rules: list[ResolverRule] = []
    firewall_rules: list[FirewallRule] = []
    phzs: list[Phz] = []
    vpces: list[Vpce] = []

    # --- directly-attached Resolver rules ---
    for assoc in r53r.list_resolver_rule_associations(
        Filters=[{"Name": "VPCId", "Values": [vpc_id]}]
    ).get("ResolverRuleAssociations", []):
        rid = assoc["ResolverRuleId"]
        try:
            rule = r53r.get_resolver_rule(ResolverRuleId=rid)["ResolverRule"]
            resolver_rules.append(ResolverRule(
                domain=rule.get("DomainName", "."),
                rule_type=rule.get("RuleType", "FORWARD"),
                target=",".join(t.get("Ip", "") for t in rule.get("TargetIps", [])) or "onprem",
                source="direct",
            ))
        except Exception:
            # Associated rule not readable from this account -> opaque marker.
            resolver_rules.append(ResolverRule(
                domain="", rule_type="FORWARD", target="", source="direct", opaque=True))

    # --- directly-attached DNS Firewall rule groups ---
    for fga in r53r.list_firewall_rule_group_associations(
        VpcId=vpc_id
    ).get("FirewallRuleGroupAssociations", []):
        fgid = fga["FirewallRuleGroupId"]
        try:
            frules = r53r.list_firewall_rules(FirewallRuleGroupId=fgid).get("FirewallRules", [])
        except Exception:
            # Rule group associated but not readable (cross-account share) ->
            # record an opaque BLOCK marker so it is not silently dropped.
            firewall_rules.append(FirewallRule(
                domains=(), action="BLOCK", block_response="NXDOMAIN",
                priority=fga.get("Priority", 100), source="direct", opaque=True))
            continue
        for fr in frules:
            try:
                dl = r53r.list_firewall_domains(
                    FirewallDomainListId=fr["FirewallDomainListId"]
                ).get("Domains", [])
                opaque = False
            except Exception:
                # Domain list not readable from this account (RAM-shared group /
                # AWS Managed Domain List) -> present but opaque.
                dl, opaque = [], True
            firewall_rules.append(FirewallRule(
                domains=tuple(dl),
                action=fr.get("Action", "BLOCK"),
                block_response=fr.get("BlockResponse", "NXDOMAIN"),
                priority=fr.get("Priority", 100),
                source="direct",
                opaque=opaque,
            ))

    # --- associated PHZs ---
    for hz in r53.list_hosted_zones_by_vpc(
        VPCId=vpc_id, VPCRegion=session.region_name
    ).get("HostedZoneSummaries", []):
        phzs.append(Phz(zone=hz["Name"], source="direct"))

    # --- VPC endpoints: every DNS shadow the CONSUMER VPC sees, derived purely
    #     from DescribeVpcEndpoints (consumer-side, no provider visibility).
    #     DnsEntries[].DnsName is the name actually installed into this VPC.
    #     Endpoint type drives the gate:
    #       Interface        -> gated (AWS-service FQDN; SNVA preference applies)
    #       Resource / ServiceNetwork -> ungated custom-domain shadow
    #     This does NOT read resource configurations or gateways - those are
    #     provider-only constructs a consumer account cannot enumerate (a
    #     resource config may be RAM-shared and its gateway invisible here).
    for ep in ec2.describe_vpc_endpoints(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    ).get("VpcEndpoints", []):
        etype = ep.get("VpcEndpointType", "")
        private = ep.get("PrivateDnsEnabled", False)
        if etype == "Interface":
            svc = ep.get("ServiceName", "")
            # Convert ServiceName to the private-DNS FQDN the endpoint installs.
            # com.amazonaws.us-east-1.secretsmanager -> secretsmanager.us-east-1.amazonaws.com
            # com.amazonaws.cn.cn-north-1.s3 -> s3.cn-north-1.amazonaws.com.cn
            if "com.amazonaws." in svc:
                parts = svc.split(".")  # [com, amazonaws, us-east-1, secretsmanager] or [com, amazonaws, cn, cn-north-1, s3]
                if len(parts) >= 4 and parts[2] == "cn":
                    # China partition: com.amazonaws.cn.<region>.<service>
                    apex = f"{parts[-1]}.{parts[3]}.amazonaws.com.cn"
                elif len(parts) >= 4:
                    # Standard/GovCloud: com.amazonaws.<region>.<service>
                    apex = f"{parts[-1]}.{parts[2]}.amazonaws.com"
                else:
                    apex = svc
            else:
                apex = svc
            vpces.append(Vpce(service_apex=apex, private_dns=private,
                              source="direct", gated=True))
        elif etype in ("Resource", "ServiceNetwork"):
            # Custom-domain shadow the endpoint installs into the consumer VPC.
            # Prefer DnsEntries (fully consumer-side); Resource endpoints do not
            # populate DnsEntries, so fall back to a TARGETED Get on the ARN this
            # endpoint already references (a construct the consumer is associated
            # with - RAM-shared configs are readable). This is NOT provider-side
            # enumeration and never touches the resource gateway.
            src = ("resource-endpoint" if etype == "Resource" else "sn-endpoint") \
                + f":{ep.get('VpcEndpointId','')}"
            names = [(de.get("DnsName") or "").lstrip("*.") for de in ep.get("DnsEntries", [])]
            names = [n for n in names if n]
            if not names and etype == "Resource" and ep.get("ResourceConfigurationArn"):
                try:
                    rc = lattice.get_resource_configuration(
                        resourceConfigurationIdentifier=ep["ResourceConfigurationArn"]
                    )
                    dom = rc.get("customDomainName")
                    if dom:
                        names = [dom]
                except Exception:
                    pass  # config not readable from this account -> skip, no guess
            for name in names:
                vpces.append(Vpce(service_apex=name, private_dns=True,
                                  source=src, gated=False))

    # --- Route 53 Profiles inherited resources (the union) ---
    try:
        profile_assocs = r53p.list_profile_associations().get("ProfileAssociations", [])
    except Exception:
        profile_assocs = []
    for pa in profile_assocs:
        if pa.get("ResourceId") != vpc_id:
            continue
        pid = pa["ProfileId"]
        src = f"profile:{pid}"
        try:
            pras = r53p.list_profile_resource_associations(ProfileId=pid).get("ProfileResourceAssociations", [])
        except Exception:
            pras = []
        for pr in pras:
            rtype = pr.get("ResourceType", "")
            if rtype == "ResolverRule":
                # Profile-contained rules are typically owned by the sharing
                # account and NOT readable from the consumer -> opaque marker.
                try:
                    rule = r53r.get_resolver_rule(ResolverRuleId=pr["ResourceId"])["ResolverRule"]
                    resolver_rules.append(ResolverRule(
                        rule.get("DomainName", "."), rule.get("RuleType", "FORWARD"),
                        ",".join(t.get("Ip", "") for t in rule.get("TargetIps", [])) or "onprem", src,
                    ))
                except Exception:
                    resolver_rules.append(ResolverRule(
                        domain="", rule_type="FORWARD", target="", source=src, opaque=True))
            elif rtype == "PrivateHostedZone":
                try:
                    hz = r53.get_hosted_zone(Id=pr["ResourceId"])["HostedZone"]
                    phzs.append(Phz(zone=hz["Name"], source=src))
                except Exception:
                    # PHZ owned by the sharing account, not readable here. A PHZ
                    # with no readable name cannot be matched; record nothing
                    # resolvable but keep a note via an opaque resolver marker so
                    # the profile's influence is not silently zero.
                    resolver_rules.append(ResolverRule(
                        domain="", rule_type="FORWARD", target="", source=src, opaque=True))
            elif rtype == "FirewallRuleGroup":
                try:
                    frules = r53r.list_firewall_rules(FirewallRuleGroupId=pr["ResourceId"]).get("FirewallRules", [])
                except Exception:
                    firewall_rules.append(FirewallRule(
                        domains=(), action="BLOCK", block_response="NXDOMAIN",
                        priority=100, source=src, opaque=True))
                    continue
                for fr in frules:
                    try:
                        dl = r53r.list_firewall_domains(
                            FirewallDomainListId=fr["FirewallDomainListId"]
                        ).get("Domains", [])
                        opaque = False
                    except Exception:
                        dl, opaque = [], True
                    firewall_rules.append(FirewallRule(
                        tuple(dl), fr.get("Action", "BLOCK"),
                        fr.get("BlockResponse", "NXDOMAIN"), fr.get("Priority", 100), src,
                        opaque=opaque,
                    ))

    return EffectiveModel(
        vpc_id=vpc_id,
        firewall_rules=tuple(firewall_rules),
        resolver_rules=tuple(resolver_rules),
        phzs=tuple(phzs),
        vpces=tuple(vpces),
        snva_preference=snva_preference,
        specified_domains=specified_domains,
        dns_support=dns_support,
        onprem_zones=onprem,
    )


def _derive_candidate_names(model: EffectiveModel) -> list[str]:
    """Default candidate set from the config itself (no query logs needed)."""
    names: set[str] = set()
    for r in model.resolver_rules:
        if r.domain not in (".", ""):
            names.add(r.domain.rstrip("."))
    for p in model.phzs:
        names.add(p.zone.rstrip("."))
    for v in model.vpces:
        if v.service_apex:
            names.add(v.service_apex.rstrip("."))
    for f in model.firewall_rules:
        names.update(d.rstrip(".") for d in f.domains)
    return sorted(n for n in names if n)


@mcp.tool()
def dns_simulate_effective_config(
    account_id: str, region: str, vpc_id: str, onprem_zones: list[str] | None = None
) -> str:
    """
    Report the VPC's EFFECTIVE DNS resolution config: the union of directly
    attached resources and resources inherited via any associated Route 53
    Profile. Read-only. Each construct is tagged with its source (direct vs
    profile:<id>).

    Args:
        account_id: Target AWS account ID.
        region: Target region.
        vpc_id: VPC to inspect.
        onprem_zones: Optional operator-declared on-prem/corp zones (for
            correct category judgement of names that should resolve on-prem).

    Returns:
        A markdown summary of resolver rules, PHZ associations, VPCE private-DNS
        flags, DNS Firewall rule groups, and Profile-inherited resources.
    """
    ok, msg = _preflight(account_id, region, vpc_id)
    if not ok:
        return msg
    session = _assume(account_id, region, READONLY_ROLE_ARN_PATTERN, "dns-sim-config")
    m = _build_effective_model(session, vpc_id, onprem_zones)

    def _rows(items, fmt):
        return "\n".join(fmt(i) for i in items) if items else "_(none)_"

    return (
        f"**Effective DNS config for {vpc_id}** ({account_id}/{region})\n\n"
        f"enableDnsSupport: {m.dns_support} | SNVA preference: {m.snva_preference}"
        f"{(' | specified domains: ' + ', '.join(m.specified_domains)) if m.specified_domains else ''}\n\n"
        f"**Resolver rules** ({len(m.resolver_rules)}):\n"
        f"{_rows(m.resolver_rules, lambda r: f'- `{r.domain}` {r.rule_type} -> {r.target} [{r.source}]')}\n\n"
        f"**DNS Firewall rules** ({len(m.firewall_rules)}):\n"
        f"{_rows(m.firewall_rules, lambda f: f'- {f.action}/{f.block_response} p{f.priority} on {len(f.domains)} domains [{f.source}]')}\n\n"
        f"**PHZ associations** ({len(m.phzs)}):\n"
        f"{_rows(m.phzs, lambda p: f'- `{p.zone}` [{p.source}]')}\n\n"
        f"**Interface VPCEs** ({len(m.vpces)}):\n"
        f"{_rows(m.vpces, lambda v: f'- `{v.service_apex}` privateDns={v.private_dns} [{v.source}]')}"
    )


@mcp.tool()
def dns_simulate_change(
    account_id: str,
    region: str,
    vpc_id: str,
    change: dict,
    candidate_names: list[str] | None = None,
    onprem_zones: list[str] | None = None,
    volumes: dict | None = None,
) -> str:
    """
    Predict which currently-resolving names a proposed DNS control-plane change
    would alter or break, BEFORE it is applied. Symbolic and read-only.

    Candidate names default to API-derived (PHZ records, rule target domains,
    VPCE service apexes, DNS Firewall domain lists). Supply `volumes`
    (name -> query count) from Resolver Query Logs to rank by real traffic;
    this is optional enrichment, never required for correctness.

    Change descriptor (validated against a fixed schema, not free text), e.g.:
      {"type": "enable_vpce_private_dns", "service_apex": "secretsmanager.us-east-1.amazonaws.com"}
      {"type": "add_resolver_rule", "rule_type": "FORWARD", "domain": ".", "target": "onprem"}
      {"type": "associate_dns_firewall", "domains": ["bad.example."], "action": "BLOCK"}
      {"type": "associate_profile", "profile_id": "rp-...", "resources": {...}}
      {"type": "set_snva_preference", "preference": "ALL_DOMAINS"}

    Returns:
        A per-name impact report (current -> post-change, delta, traps,
        severity), Profile-sourced deltas annotated with the propagation window.
    """
    ok, msg = _preflight(account_id, region, vpc_id)
    if not ok:
        return msg
    ctype = (change or {}).get("type")
    known = {
        "enable_vpce_private_dns", "associate_phz", "add_resolver_rule",
        "associate_dns_firewall", "associate_profile", "set_snva_preference",
        "set_dhcp_dns",
    }
    if ctype not in known:
        return f"ERROR: unknown change type '{ctype}'. Supported: {', '.join(sorted(known))}."

    # Validate required fields per change type. Stable errors prevent KeyError
    # deep in dns_model.apply_change().
    _required_fields = {
        "enable_vpce_private_dns": ["service_apex"],
        "associate_phz": ["zone"],
        "add_resolver_rule": ["domain"],
        "associate_dns_firewall": ["domains", "action"],
        "associate_profile": ["profile_id"],
        "set_snva_preference": ["preference"],
        "set_dhcp_dns": ["servers"],
    }
    missing = [f for f in _required_fields.get(ctype, []) if f not in change]
    if missing:
        return (
            f"ERROR: change type '{ctype}' requires fields: "
            f"{', '.join(_required_fields[ctype])}. Missing: {', '.join(missing)}."
        )

    session = _assume(account_id, region, READONLY_ROLE_ARN_PATTERN, "dns-sim-change")
    model = _build_effective_model(session, vpc_id, onprem_zones)
    names = candidate_names or _derive_candidate_names(model)
    if not names:
        return (
            f"No candidate names to simulate for {vpc_id}. Supply `candidate_names` "
            "or enable Resolver Query Logging to derive them."
        )

    vols = {k.lower(): int(v) for k, v in (volumes or {}).items()}
    impacts = simulate(model, change, names, vols)

    is_profile = ctype == "associate_profile"
    lo, hi = PROFILE_PROPAGATION_SECONDS
    header = (
        f"**Pre-change simulation for {vpc_id}** ({account_id}/{region})\n\n"
        f"Change: `{json.dumps(change)}`\n"
        f"Candidate names: {len(names)} "
        f"({'operator-supplied' if candidate_names else 'API-derived'}"
        f"{', query-log-ranked' if vols else ''}); "
        f"{len(impacts)} impacted.\n"
    )
    if is_profile:
        header += (
            f"\n> Route 53 Profile change: deltas below propagate asynchronously "
            f"(~{lo}s service-side target, up to ~{hi}s negative-cache worst case). "
            f"Poll association status = COMPLETE before trusting resolution.\n"
        )
    if not impacts:
        source_label = "operator-supplied" if candidate_names else "API-derived from current config"
        return (
            header + f"\nNo currently-resolving names change or break within the "
            f"{len(names)}-name candidate set ({source_label}). "
            f"Names not in this set were not evaluated; supply `candidate_names` "
            f"or enable Resolver Query Logging for broader coverage."
        )

    lines = [
        "\n| name | before | after | traps | severity | vol |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    icon = {"high": "🔴", "medium": "🟡", "none": "🟢"}
    for i in impacts:
        lines.append(
            f"| `{i.name}` | {i.before.answer_class} ({i.before.winner}) "
            f"| {i.after.answer_class} ({i.after.winner}) "
            f"| {', '.join(i.traps) or '-'} | {icon.get(i.severity,'')} {i.severity} | {i.volume or '-'} |"
        )
    return header + "\n".join(lines)


# ============================================================
# SOP runbooks (bundled with the deployment package)
# ============================================================

SOP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sops")

# Slug -> one-line purpose. Kept explicit (rather than parsed from the files) so
# the catalogue is stable and cheap to return.
SOP_CATALOGUE: dict[str, str] = {
    "Z-general-triage": (
        "START HERE for a vague DNS symptom. Precondition checks, then routes to "
        "the specific failure-mode runbook."
    ),
    "A-critical-safety-rules": (
        "Non-negotiable rules constraining every recommendation. Read before "
        "advising any DNS change."
    ),
    "A-mode-a-live-resolver-comparison": (
        "Mode A workflow: how to run and read a live multi-resolver comparison "
        "inside an instance, and its SSM prerequisites."
    ),
    "A-name-category-classification": (
        "Judge correctness rather than agreement: expected winner and fault "
        "condition per name category."
    ),
    "A-custom-resolver-divergence": (
        "Instance is not using the VPC-handed resolver, or a custom/hybrid "
        "resolver returns different answers than the VPC resolver."
    ),
    "A-forward-vs-phz-precedence-collision": (
        "Internal zone name TIMES OUT while public names resolve: a FORWARD rule "
        "outranking a private hosted zone."
    ),
    "A-address-family-divergence": (
        "A resolves but AAAA is empty, or an IPv4 resolver is absent on an "
        "IPv6-only instance."
    ),
    "A-resolver-disabled-precondition": (
        "Every name fails from every instance: the enableDnsSupport / "
        "enableDnsHostnames VPC-attribute precondition."
    ),
    "B-mode-b-pre-change-validation": (
        "Mode B workflow: predict which resolving names a proposed DNS change "
        "would break, before applying it."
    ),
    "B-vpce-shadow-nxdomain": (
        "Enabling VPC endpoint private DNS shadows a service apex, so a name "
        "that resolved publicly now returns NXDOMAIN."
    ),
    "B-broad-forward-sweep": (
        "A '.' or broad-suffix FORWARD rule captures AWS FQDNs and PHZ names "
        "with no SYSTEM carve-out."
    ),
    "B-flag-and-mismatch": (
        "privateDnsEnabled AND PrivateDnsPreference leave a Lattice custom "
        "domain uninstalled; create-only flag caveats."
    ),
    "B-dns-firewall-block": (
        "A DNS Firewall rule blocks a name before resolution completes, "
        "including the cross-account opaque-domain-list case."
    ),
    "B-profile-propagation-timing": (
        "Route 53 Profile association shifts config in bulk and propagates "
        "asynchronously (~300-350s, up to ~900s negative cache)."
    ),
    "C-cross-account-opaque-constructs": (
        "RAM-shared and Profile-contained constructs that are enumerable but "
        "opaque; how to report 'cannot determine' correctly."
    ),
    "C-limitations-and-boundaries": (
        "What each mode cannot tell you, and the honest-reporting checklist to "
        "apply before concluding."
    ),
}


@mcp.tool()
def list_sops() -> str:
    """
    List the available DNS diagnostic runbooks (SOPs) with a one-line purpose
    for each. Call this first when you are unsure which procedure applies, then
    fetch the relevant one with get_sop.

    Slug prefixes: Z = start-here triage, A = live diagnosis / safety rules,
    B = pre-change validation, C = cross-cutting concerns.

    Returns:
        A markdown table of runbook slugs and their purpose.
    """
    lines = [
        "# Available DNS diagnostic runbooks",
        "",
        "Fetch one with `get_sop(slug)`. If the symptom is vague, start with "
        "`Z-general-triage`.",
        "",
        "| slug | purpose |",
        "| --- | --- |",
    ]
    for slug, purpose in SOP_CATALOGUE.items():
        lines.append(f"| `{slug}` | {purpose} |")
    return "\n".join(lines)


@mcp.tool()
def get_sop(slug: str) -> str:
    """
    Retrieve the full text of one DNS diagnostic runbook (SOP) by slug.

    Args:
        slug: Runbook slug exactly as returned by list_sops (for example
            'Z-general-triage'). Do not include a path or file extension.

    Returns:
        The runbook markdown, or an error listing the valid slugs.
    """
    # Allowlist lookup: the slug must be a known catalogue key. This rejects any
    # path traversal or absolute path outright - no filename is ever built from
    # unvalidated caller input.
    if slug not in SOP_CATALOGUE:
        valid = ", ".join(sorted(SOP_CATALOGUE))
        return (
            f"ERROR: unknown runbook slug '{slug}'.\n\n"
            f"Valid slugs: {valid}\n\n"
            "Call list_sops() for the catalogue with descriptions."
        )

    path = os.path.join(SOP_DIR, f"{slug}.md")
    # Defence in depth: confirm the resolved path stayed inside SOP_DIR.
    if os.path.commonpath([os.path.realpath(path), os.path.realpath(SOP_DIR)]) != os.path.realpath(SOP_DIR):
        return f"ERROR: refusing to read outside the runbook directory: '{slug}'"

    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return (
            f"ERROR: runbook '{slug}' is catalogued but its file is missing from "
            "the deployment package. This is a packaging bug."
        )
    except OSError as exc:
        return f"ERROR: could not read runbook '{slug}': {exc}"


# ============================================================
# Entry point (Streamable HTTP via Lambda Web Adapter)
# ============================================================

try:
    handler = mcp.streamable_http_handler()
except AttributeError:
    # FastMCP 3.x: use the ASGI app for the Lambda Web Adapter.
    handler = mcp.http_app()

if __name__ == "__main__":
    # Local testing. Bind loopback only: this path has no SigV4 boundary in
    # front of it, so binding 0.0.0.0 would expose the diagnostic tools to
    # anything that can reach this host.
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
