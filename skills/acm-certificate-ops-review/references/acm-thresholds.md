# ACM Thresholds and Risk Levels Reference

Use these thresholds when detecting issues (SKILL.md Step 2) and when
classifying findings (SKILL.md Step 3). Values reflect common operational
best practice; adjust to a customer's own policy when one is documented.

## Expiry thresholds (in-use certificates)

| Days to expiry (`NotAfter` - now) | Risk |
|---|---|
| Already expired | RED |
| <= 14 days | RED |
| 15 - 30 days | AMBER |
| 31 - 45 days | AMBER |
| > 45 days | GREEN |

For certificates **not** in use (`InUseBy` empty), lower the risk by one level
(RED -> AMBER, AMBER -> GREEN) because there is no live traffic impact, but
still list them as cleanup candidates.

## Renewal status

| `RenewalSummary.RenewalStatus` | Risk | Notes |
|---|---|---|
| `FAILED` | RED | Capture `RenewalStatusReason`. |
| `PENDING_VALIDATION` | RED (in use) / AMBER | Domain validation is blocking renewal. |
| `PENDING_AUTO_RENEWAL` (progressing) | GREEN | Normal state ~60 days out. |
| `SUCCESS` | GREEN | |

## Certificate type

| Condition | Risk | Recommendation |
|---|---|---|
| `IMPORTED` and in use | AMBER (RED if <= 30 days) | ACM cannot auto-renew imported certs. Move to an ACM-managed (`AMAZON_ISSUED`) certificate where possible, or ensure an external renewal + re-import process exists. |
| `AMAZON_ISSUED` with DNS validation | GREEN | Eligible for managed renewal. |
| `AMAZON_ISSUED` with email validation | AMBER | Email validation does not support fully automated renewal. Recommend switching to DNS validation. |

## Key algorithm

| `KeyAlgorithm` | Risk |
|---|---|
| `RSA_1024` | RED (deprecated) |
| `RSA_2048`, `EC_prime256v1`, `EC_secp384r1` and stronger | GREEN |

## Monitoring

| Condition | Risk |
|---|---|
| In-use certificate with **no** CloudWatch alarm on `DaysToExpiry` | AMBER |
| In-use certificate with a `DaysToExpiry` alarm | GREEN |

The ACM expiry metric is `DaysToExpiry` in the `AWS/CertificateManager`
namespace, dimension `CertificateArn`.

## ACM Private CA

| Condition | Risk |
|---|---|
| CA `EXPIRED` or `DISABLED` with issued certs still in use | RED |
| CA expiring within 90 days | AMBER |
| CA healthy | GREEN |

## Overall posture rollup

- **RED** if any RED finding exists.
- **AMBER** if no RED but one or more AMBER findings.
- **GREEN** only if every finding is GREEN.
