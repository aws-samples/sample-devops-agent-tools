# ACM Certificate Operations Review

## Purpose

This skill gives AWS DevOps Agent a repeatable procedure for investigating and
reviewing AWS Certificate Manager (ACM) and ACM Private CA certificate health
across one or more accounts and regions. Certificate expiry is a leading cause
of avoidable customer-facing outages, and the CA/Browser Forum is progressively
shortening TLS certificate validity (398 to 47 days by March 2029), which makes
manual certificate management increasingly risky. The skill helps the agent
catch expiring, failing, and mismanaged certificates before they cause an
incident, and prepare a strategic readiness view for the shorter validity
windows.

## Key Capabilities

- Inventories ACM and ACM Private CA certificates across accounts and regions
  (always including us-east-1 for CloudFront).
- Detects expiring/expired certificates, failed or stuck managed renewals,
  `PENDING_VALIDATION` and DNS/email validation failures, imported in-use
  certificates that cannot auto-renew, weak/legacy key algorithms, unused
  certificates, and missing CloudWatch `DaysToExpiry` monitoring.
- Flags ACM Private CA problems (disabled, expired, or nearing expiry).
- Classifies findings by risk (RED / AMBER / GREEN) and produces a prioritized
  findings report with an executive summary and remediation list.
- Optionally assesses CA/Browser Forum validity-reduction readiness, DV vs
  OV/EV strategy, renewal automation options, and cost impact.

## Prerequisites

The skill is read-only. The DevOps Agent role for your Agent Space needs:

- `acm:ListCertificates`
- `acm:DescribeCertificate`
- `acm-pca:ListCertificateAuthorities`
- `acm-pca:DescribeCertificateAuthority`
- `cloudwatch:DescribeAlarmsForMetric`
- `organizations:ListAccounts` (only for org-wide scans, from the management or
  delegated administrator account)

Most of these may already be covered by the `AIDevOpsAgentAccessPolicy` managed
policy attached to the DevOps Agent role; add any that are missing.

## Limitations

- Read-only: the skill inventories and assesses certificates but never creates,
  imports, deletes, or renews them.
- Public trust and validity-reduction dates change over time; the skill directs
  the agent to confirm current CA/Browser Forum and ACM figures against
  authoritative sources before quoting them.
- GovCloud and China partitions are scanned only on explicit request and are
  reported separately from commercial-partition findings.
- Cost figures must be computed from current pricing at run time; the skill does
  not embed pricing.
- This is sample code (see disclaimer below).

## Agent Types

- **Chat tasks** - posture reviews and reports (for example "review the ACM
  certificate posture for account 1111...").
- **Incident RCA** and **Incident Triage** - certificate-related incidents (for
  example "investigate why example.com is serving an expired certificate").

Select these agent types when uploading the skill to your Agent Space.

## Uploading to AWS DevOps Agent

From the repository root, zip the skill (allowed extensions only, excluding
non-skill files) and upload it to your Agent Space, selecting the agent types
listed above:

```bash
cd skills
zip -r acm-certificate-ops-review.zip acm-certificate-ops-review/ \
  -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' \
  -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
```

Then, in the Agent Space Operator Web App, go to Knowledge > Skills > upload the
zip, and grant the role the IAM permissions listed under Prerequisites if they
are not already present.

## How to Use This Skill

Operators do not need to name the skill; it activates from the description.
Sample prompts:

**Chat tasks**
- "Review the ACM certificate posture for account 111122223333."
- "Which certificates across my org expire in the next 30 days?"
- "Are we ready for the CA/Browser Forum certificate validity reductions?"
- "Do any of my in-use certificates lack an expiry alarm?"

**Incident RCA / Incident Triage**
- "Investigate why www.example.com is serving an expired certificate."
- "A managed renewal failed for api.example.com; find the root cause."
- "An endpoint is still presenting the old certificate after renewal."

## Disclaimer

This skill is **sample code**. It is **not intended for production use without
additional review and testing**. Validate it in a **non-production
environment** first, review the IAM permissions and behavior against your
organization's security policies, and confirm the findings against your live
environment before acting on them. Certificate validity dates and CA/Browser
Forum timelines change over time - verify current values against the
authoritative AWS and CA/Browser Forum sources before relying on them.

## License

Apache-2.0. See the repository `LICENSE` file.
