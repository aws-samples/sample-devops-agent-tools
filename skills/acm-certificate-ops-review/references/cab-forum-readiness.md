# CA/Browser Forum Readiness Reference (Phase 2)

Apply this only when the operator asks about the CA/Browser Forum validity
reductions, automation readiness, or migration options for their certificate
inventory. It uses the Phase 1 inventory (SKILL.md Steps 1-2) as its input.

## CRITICAL: All facts are dynamic

Every factual claim in Phase 2 (pricing, feature support, validity dates,
protocol support) MUST be verified against authoritative sources at the time
of execution. Do NOT assume or hardcode any of the following:

- ACM's feature set (it evolves; e.g. ACME support was added July 2026)
- CA/Browser Forum validity dates (ballot updates can shift timelines)
- Which cert types ACM can issue (currently DV only)
- Which services support which cert types (e.g. ACME certs currently cannot
  be used with AWS integrated services)

Acceptable sources are ONLY:
- Official AWS documentation (docs.aws.amazon.com, aws.amazon.com/pricing)
- Official CA/Browser Forum ballots and guidelines (cabforum.org)
- OEM/vendor product documentation and user guides (e.g. digicert.com/docs)
- NOT blogs, third-party articles, community posts, or general web content

If you cannot verify a fact from an official source at runtime, state that
explicitly rather than guessing.

## Background: the validity reduction timeline

The CA/Browser Forum has approved progressive reductions to the maximum
validity of publicly trusted TLS certificates. As of the last known ballot:

- 398 days (previous baseline)
- 200 days (effective March 2026)
- 100 days (effective March 2027)
- 47 days (effective March 2029)

ALWAYS confirm current figures against CA/Browser Forum ballot records and
ACM documentation before presenting dates. Timelines can be amended.

## Step 1: Impact classification

For each in-use certificate from the Phase 1 inventory, classify automation
readiness:

| Class | Meaning |
|---|---|
| GREEN | Fully automated renewal in place. No manual action needed as validity shrinks. |
| AMBER | Partially automated or has a constraint that may require attention (e.g. email validation, ACME cert on a workload that may move to an integrated service). |
| RED | Manual renewal process. Shorter validity directly multiplies operational toil and outage risk. |

Summarize counts per class. RED items are the migration priority.

## Step 2: Migration path decision tree

For each RED or AMBER certificate, determine the correct migration path.
This is NOT as simple as "migrate to ACM." The decision depends on cert type
and usage:

### Decision logic

```
Is the imported certificate DV (domain-validated)?
  |
  +-- NO (OV or EV) --> CANNOT migrate to ACM-issued.
  |     ACM only issues DV certificates.
  |     Recommendation: keep on external CA.
  |     Check if the external CA supports ACME or other automation
  |     to reduce manual renewal toil.
  |
  +-- YES (DV) --> Is it used with an AWS integrated service?
        |           (ALB, CloudFront, API Gateway, Elastic Beanstalk, etc.)
        |
        +-- YES --> Migrate to ACM-managed certificate with DNS validation.
        |           (ACM handles renewal automatically for integrated services.
        |            ACME-issued certs CANNOT be used with integrated services
        |            per current ACM docs.)
        |
        +-- NO (self-managed: EC2, ECS, K8s, on-prem, multi-cloud)
              --> Migrate to ACM ACME endpoint.
                  (Use Certbot, cert-manager, acme.sh, or other ACMEv2 client.
                   Note: ACME certs have 45-day validity, verify current docs.)
```

### OV/EV certificates (cannot migrate)

For OV/EV certs that must remain on an external CA:
- Check if the external CA supports automated renewal (ACME, SCEP, REST API).
- If yes, recommend the operator implement that automation to reduce toil.
- If no, flag as a persistent manual-renewal risk that scales with shorter
  validity. Recommend evaluating whether the OV/EV requirement is regulatory
  (hard constraint) or preference (may be revisitable).
- In either case, ensure a `DaysToExpiry` CloudWatch alarm exists.

## Step 3: Gap identification (operator-focused)

Identify gaps that create operational risk as validity shrinks:

- **Imported DV certs on integrated services** - these should have been
  ACM-managed all along. Straightforward migration, highest ROI.
- **Imported DV certs on self-managed workloads without ACME** - these need
  ACME client setup or another automation mechanism.
- **OV/EV certs with no automation** - persistent risk. Quantify: "N certs
  will need manual renewal every X days by 2029."
- **Email-validated ACM certs** - renewal requires human action on the
  approval email. Recommend switching to DNS validation.
- **Missing expiry monitoring** - any in-use cert without a `DaysToExpiry`
  alarm is blind to approaching expiry.

## Step 4: Readiness summary

Produce a concise operator-focused summary:

1. Total certs scanned and readiness split (GREEN / AMBER / RED counts).
2. Top migration candidates (DV imported certs that can move to ACM).
3. Certs that cannot migrate and their automation status.
4. Concrete next actions ordered by risk reduction.

Do NOT produce:
- Detailed competitor commercial comparison tables
- Multi-year cost projection spreadsheets
- Customer-facing talking points or slide content
- Provider pricing model analysis

Keep the output actionable for an operator, not a strategic advisory document.

## Authoritative sources (official documentation ONLY)

Only cite from these categories. Never cite blogs, re:Post articles,
community posts, Medium, or third-party aggregators.

- ACM What's New: https://aws.amazon.com/about-aws/whats-new/ (filter by ACM)
- ACM ACME docs: https://docs.aws.amazon.com/acm/latest/userguide/acm-acme.html
- ACM pricing: https://aws.amazon.com/certificate-manager/pricing/
- ACM best practices:
  https://docs.aws.amazon.com/acm/latest/userguide/acm-bestpractices.html
- ACM certificate characteristics:
  https://docs.aws.amazon.com/acm/latest/userguide/acm-certificate-characteristics.html
- ACM integrated services:
  https://docs.aws.amazon.com/acm/latest/userguide/acm-services.html
- ACM certificate automation:
  https://docs.aws.amazon.com/acm/latest/userguide/acm-certificate-automation.html
- AWS Workload Credentials Provider:
  https://github.com/aws/aws-workload-credentials-provider
- CA/Browser Forum ballots:
  https://cabforum.org/working-groups/server/ballots/
