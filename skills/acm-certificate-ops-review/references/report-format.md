# ACM Findings Report Format Reference

Use this layout for the report produced in SKILL.md Step 4.

## 1. Executive summary

One short paragraph:

- Overall posture: RED / AMBER / GREEN (see rollup in `acm-thresholds.md`).
- Count of certificates scanned, and counts by risk level.
- The single most urgent item and its deadline.
- Scope covered (accounts, regions), and any accounts skipped
  (`AccessDenied`, throttled).

## 2. Prioritized findings table

Sort by risk (RED first), then in-use before unused, then ascending days to
expiry.

| Account | Region | Certificate / Domain | Type | Status | Days to expiry | In use | Risk | Recommendation |
|---|---|---|---|---|---|---|---|---|
| 1111... | us-east-1 | www.example.com | AMAZON_ISSUED | ISSUED | 9 | Yes | RED | Renewal FAILED - fix DNS CNAME, then re-validate |
| 4444... | eu-west-1 | api.example.com | IMPORTED | ISSUED | 27 | Yes | RED | Imported cert cannot auto-renew - migrate to ACM-managed |

Include the certificate ARN in a detail section or as a tooltip/footnote
rather than in the main row, to keep the table readable.

## 3. Remediation list

Ordered by priority. For each item: what to do, which certificate/account it
applies to, and the AWS action or console path involved. Example ordering:

1. Certificates expired or expiring within 14 days and in use.
2. Failed or stuck renewals (fix validation, then renewal proceeds).
3. Imported in-use certificates without a renewal path.
4. Weak-key certificates.
5. In-use certificates missing a `DaysToExpiry` alarm.
6. Unused certificates to clean up.

## 4. Disclaimer

End the report with an AI-generated-content disclaimer, for example:

> This report was generated with AI assistance from ACM and CloudWatch data at
> the time of the scan. Validate findings against the live environment before
> acting.

## Formatting notes

- Every number must be computed from retrieved values, never estimated.
- If an interactive HTML dashboard is requested, mirror the same sections and
  the same risk color coding (RED / AMBER / GREEN).
