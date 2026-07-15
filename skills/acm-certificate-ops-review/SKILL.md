---
name: acm-certificate-ops-review
description: "Investigation and review procedures for AWS Certificate Manager (ACM)\
  \ and ACM Private CA certificate health across one or more accounts and regions.\
  \ Use this skill when investigating TLS/SSL certificate problems or reviewing certificate\
  \ posture - certificates expiring soon or already expired, failed or stuck managed\
  \ renewals, certificates stuck in PENDING_VALIDATION, DNS or email domain validation\
  \ failures, endpoints still serving an old certificate after renewal, imported certificates\
  \ that ACM cannot auto-renew, weak or legacy key algorithms (RSA_1024), unused or\
  \ in-use certificates, missing CloudWatch DaysToExpiry expiry monitoring, ACM Private\
  \ CA issues, and readiness for the CA/Browser Forum TLS certificate validity reductions\
  \ (398 to 47 days by March 2029). Also use it for questions about certificate expiry\
  \ as a cause of outages, certificate renewal automation, or DV vs OV/EV certificate\
  \ strategy."
metadata:
  author: majamuda, vmgaddam
  version: "1.0.0"
  aws-devops-agent-skills.agent-types: "Chat tasks, Incident RCA, Incident Triage"
  aws-devops-agent-skills.aws-services: "AWS Certificate Manager, AWS Private CA"
  aws-devops-agent-skills.technical-domains: "Security"
---

> **Important:** ACM and related services evolve frequently (new features,
> protocol support, pricing changes, validity rules). Before making any
> recommendation, verify the current service capabilities against the official
> AWS documentation. Do not rely on cached assumptions from prior runs or from
> this skill's reference files alone. If a feature status is uncertain,
> check the ACM What's New page and user guide for the latest updates before
> advising.

# ACM Certificate Operations Review

Use this skill when investigating or reviewing AWS Certificate Manager (ACM)
and ACM Private CA certificate health. Certificate expiry is a leading cause
of avoidable customer-facing outages, and the CA/Browser Forum has mandated
progressive reductions in TLS certificate validity (398 -> 198 -> 100 -> 47
days by March 2029), which makes manual certificate management increasingly
risky.

This skill has two phases:

- **Phase 1 - Operational review (always applies).** Inventory certificates,
  detect health problems, and produce a prioritized findings report. This is
  the core investigation runbook.
- **Phase 2 - CA/Browser Forum readiness (apply when the operator asks about
  strategic impact, validity reductions, automation readiness, or cost).**
  See `references/cab-forum-readiness.md`.

Threshold values, risk levels, and the required report layout are defined in
the reference files. Read them when you reach the step that needs them.

## When to use this skill

Load and follow this skill when the task involves any of the following:

- A certificate is expiring soon, has expired, or an operator wants to know
  what will expire in the next N days.
- A managed renewal failed, is stuck, or a certificate is stuck in
  `PENDING_VALIDATION`.
- An endpoint is still presenting an old certificate after a renewal or
  re-issue.
- Domain validation (DNS `CNAME` or email) is failing.
- An imported certificate is in use and ACM cannot manage its renewal.
- A certificate uses a weak or legacy key algorithm.
- There is no CloudWatch alarm on the `DaysToExpiry` metric for an important
  certificate.
- An ACM Private CA is expiring, disabled, or its certificates are affected.
- An operator wants a certificate posture review across an account or an
  organization.

## Scope of investigation

Before scanning, establish scope:

1. **Accounts** - a specific account, a list of accounts, or (only when
   explicitly requested) all linked accounts in the organization discovered
   via `organizations:ListAccounts` from the management/delegated account.
2. **Regions** - always scan `us-east-1` first, because CloudFront
   certificates must live there, then the remaining commercial regions in
   scope. If the operator names regions, honor them but still include
   `us-east-1`.

Guardrails:

- Default to the specific account(s) in the incident or request. Do **not**
  scan the whole organization unless the operator explicitly asks for an
  org-wide review, and confirm first if the scan will span many accounts.
- Treat GovCloud and China partitions separately. Do not scan them unless
  explicitly requested, and never mix their findings with commercial-partition
  findings in the same output - they have separate compliance requirements.
- Scan sequentially (one account at a time). On `TooManyRequestsException`,
  back off exponentially (2s -> 4s -> 8s, max 3 retries). If several
  consecutive accounts return `AccessDenied`, stop and report only the
  accounts you could access.

## Step 1: Inventory certificates

For each account and region in scope:

1. Call `acm:ListCertificates` and paginate with `NextToken`. If you filter by
   key type, be aware new key types (for example post-quantum algorithms) can
   be missed - when in doubt, omit the key-type filter so every certificate is
   returned regardless of algorithm.
2. For each certificate ARN, call `acm:DescribeCertificate` to retrieve
   `Status`, `NotAfter`, `NotBefore`, `Type` (`AMAZON_ISSUED` vs `IMPORTED`),
   `RenewalEligibility`, `RenewalSummary`, `KeyAlgorithm`, `InUseBy`,
   `DomainValidationOptions`, and `SubjectAlternativeNames`.
3. For imported certificates, `InUseBy` and `NotAfter` are still available;
   note that ACM cannot auto-renew imported certificates.
4. Present a short inventory summary (account, region, certificate count)
   before moving on, and skip account/region pairs that return zero
   certificates.

## Step 2: Detect issues

Evaluate every certificate against the checks below. Use the thresholds and
risk levels in `references/acm-thresholds.md`.

1. **Expiry** - compute days until `NotAfter`. Flag expired and
   soon-to-expire certificates, weighted higher when `InUseBy` is non-empty.
2. **Renewal health** - inspect `RenewalSummary.RenewalStatus`. Flag
   `PENDING_VALIDATION`, `FAILED`, and `PENDING_AUTO_RENEWAL` that is not
   progressing. Capture `RenewalStatusReason` and the per-domain
   `ValidationStatus`.
3. **Validation failures** - for `PENDING_VALIDATION`, check
   `DomainValidationOptions`: for DNS validation confirm the required `CNAME`
   `ResourceRecord` exists and resolves; for email validation note that it
   blocks automated renewal.
4. **Imported certificates in use** - flag `Type = IMPORTED` with a non-empty
   `InUseBy`, since these will not auto-renew and are an outage risk.
5. **Weak or legacy keys** - flag `KeyAlgorithm` of `RSA_1024` (and any
   algorithm below current best practice).
6. **Unused certificates** - flag issued certificates with an empty `InUseBy`
   as cleanup or cost-optimization candidates (do not auto-delete).
7. **Missing expiry monitoring** - for in-use certificates, check for a
   CloudWatch alarm on the ACM `DaysToExpiry` metric
   (`AWS/CertificateManager`, dimension `CertificateArn`) via
   `cloudwatch:DescribeAlarmsForMetric`. Flag certificates with no alarm.
8. **Stale endpoint after renewal** - if a certificate was renewed or
   re-issued but a dependent endpoint still serves the old certificate,
   confirm the resource references the new certificate ARN and that the
   distribution/load balancer has finished deploying.
9. **ACM Private CA** - where relevant, call `acm-pca:ListCertificateAuthorities`
   and `acm-pca:DescribeCertificateAuthority`; flag CAs that are `DISABLED`,
   `EXPIRED`, or nearing expiry, since a CA problem affects every certificate
   it issued.

## Step 3: Classify and prioritize

Assign each finding a risk level (RED / AMBER / GREEN) using the criteria in
`references/acm-thresholds.md`. Rank by risk, then by whether the certificate
is in use, then by days to expiry. In-use certificates always outrank unused
ones at the same expiry distance.

## Step 4: Produce the findings report

Generate a prioritized findings report using the structure in
`references/report-format.md`. It must include:

- A one-paragraph executive summary (overall posture, count by risk level,
  most urgent item).
- A prioritized findings table (account, region, certificate/domain, type,
  status, days to expiry, in-use, risk, recommendation).
- A remediation list ordered by priority.

Reporting rules:

- Compute every number (days to expiry, counts, any cost estimate) in code or
  by explicit arithmetic on retrieved values - never estimate figures.
- Include an AI-generated-content disclaimer on the report.
- If this report may be shared outside the operations team, keep it factual
  and free of internal-only identifiers.

## Step 5 (optional): CA/Browser Forum readiness

If the operator asks about the CA/Browser Forum validity reductions, strategic
posture, renewal automation readiness, DV vs OV/EV strategy, or cost impact,
continue with the procedure in `references/cab-forum-readiness.md`, which uses
the Phase 1 inventory as its input.

## Required IAM permissions

Read-only. See `README.md` for the full list. Core actions:
`acm:ListCertificates`, `acm:DescribeCertificate`,
`acm-pca:ListCertificateAuthorities`, `acm-pca:DescribeCertificateAuthority`,
`cloudwatch:DescribeAlarmsForMetric`, and, for org-wide scans,
`organizations:ListAccounts`.
