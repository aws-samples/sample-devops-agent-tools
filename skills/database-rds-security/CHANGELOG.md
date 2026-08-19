```markdown
# Changelog

## 1.0.0

- Initial release: 58-gap catalog across 8 categories (Encryption at Rest, Encryption
  in Transit, Network Isolation, Authentication & Identity, Access Control &
  Authorization, Audit & Logging, Data Protection & Privacy, Compliance Alignment)
- 4-dimension scoring (Encryption, Network Isolation, Authentication & Access, Audit &
  Compliance) summing to 100
- Read-only, AWS CLI only — no MCP, no database connection
- Fixed an unclosed YAML fence in the Detection Rules section that caused the
  Assessment Scoring Matrix, remediation playbooks, and report output format to render
  as a single collapsed code block on GitHub
- Moved the 58-gap catalog and remediation playbooks to `references/` to keep
  `SKILL.md` under the repository's ~500-line guideline
- Added an explicit prerequisite note to the Secrets Manager rotation remediation:
  `rotate-secret --rotation-rules` requires a rotation Lambda already associated with
  the secret (via RDS-managed rotation or a custom function) or the command fails
