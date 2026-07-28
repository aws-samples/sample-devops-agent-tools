# Changelog

All notable changes to the AWS VPC DNS Diagnostics MCP server are documented in
this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-07-27

Initial release.

### Added

- **Mode A, live DNS observation.** `dns_probe_context` reports the VPC-attribute
  precondition (`enableDnsSupport` / `enableDnsHostnames`), the instance's
  addressing family, and the DHCP option set's configured resolvers.
  `dns_probe_compare` runs a fixed read-only probe set inside the instance via SSM
  and returns a per-resolver, per-family answer matrix including a
  `hostname.bind` resolver-identity lookup and the OS-effective `getent` result.
- **Mode B, symbolic pre-change validation.** `dns_simulate_effective_config`
  reports the VPC's effective DNS configuration as the union of directly-attached
  and Route 53 Profile-inherited constructs, each source-tagged.
  `dns_simulate_change` predicts which currently-resolving names a proposed change
  would break, using a seven-level resolution precedence engine.
- **Six trap detectors:** `VPCE-shadow-NXDOMAIN`, `broad-FORWARD-sweep`,
  `flag-AND-mismatch`, `DNS-Firewall-block`, `Profile-union-shift`, and
  `resolver-disabled`.
- **16 diagnostic runbooks** served at runtime via `list_sops` and `get_sop`,
  bundled into the deployment package and organized by diagnostic scenario.
- **Two-role security model.** The central Lambda's execution role holds only
  `sts:AssumeRole`. Mode A assumes a probe role whose sole privileged grant is a
  resource-scoped `ssm:SendCommand` to one diagnostic document; Mode B assumes a
  separate read-only role that never holds `ssm:SendCommand`.
- **On-instance enforcement boundary.** A purpose-built SSM document accepts three
  `allowedPattern`-validated parameters and renders a fixed read-only probe set.
  The server sends structured parameters, never a command string.
- **Fail-closed guards.** Wildcard allowlists are refused when `StageName=prod`.
  An empty resolver allowlist permits literal IPs only and rejects all hostnames,
  so resolver comparison cannot become an arbitrary-egress primitive.
- **Cross-account opacity handling.** RAM-shared and Profile-contained constructs
  whose detail reads are denied become `OPAQUE` markers rather than crashing the
  model build. An opaque firewall rule is evaluated first, because a hidden block
  list may cover any name.
- **DHCP option-set awareness.** The VPC-intended resolver is reported alongside
  the instance-actual `resolv.conf`, and DHCP-configured resolvers are added to
  the comparison set by default.
- **Dualstack support.** IPv4 and IPv6 resolver addresses and A/AAAA families are
  handled per instance addressing family.
- **CloudFormation test fixtures** under `test-infra/`, including a two-account
  provider/consumer pair for the cross-account opacity scenarios.
- 58 unit tests covering injection safety, the probe parameter boundary, the
  resolution engine, all six trap detectors, opaque-marker handling, and runbook
  catalogue integrity.

[1.0.0]: https://github.com/aws-samples/sample-devops-agent-tools
