# SAFETY INVARIANTS — Load First, Keep in Context

> This file defines non-negotiable safety rules for the EKS Upgrade Readiness
> skill. Every rule here is a hard constraint. Violating any Hard Rule is a
> critical defect in the assessment.

## Knowledge Hierarchy (highest authority wins)

```
1. Live API response from THIS session (eks:Describe*, ec2:Describe*, kubectl get)
   ▼ overrides
2. Config files in this skill (YAML references, check registry)
   ▼ overrides
3. Fetched documentation (EKS docs, upstream K8s release notes)
   ▼ overrides
4. Training data / model knowledge
```

When sources conflict, the higher-numbered source is WRONG. Live API responses
are ground truth. Never override a live API response with cached or trained
knowledge.

**Example:** If `DescribeAddonVersions` returns a version list, use ONLY those
versions — never suggest a version remembered from training data.

## Hard Rules

| # | Rule | Rationale |
|---|------|-----------|
| H1 | NEVER select an add-on version not returned by `DescribeAddonVersions` in THIS session | Stale version data causes upgrade failures |
| H2 | NEVER execute a mutating API call — this skill is READ-ONLY | All mutations are in the Remediation Playbook for operator approval |
| H3 | NEVER skip a required check from the registry — mark it `SKIPPED` with reason | Evidence Completeness must be accurate |
| H4 | NEVER mark a gate PASS when data is missing or access was denied | UNKNOWN is the only valid verdict for missing data |
| H5 | NEVER produce a READY verdict while any gate is UNKNOWN | Operator must investigate unknowns before proceeding |
| H6 | ALWAYS document rollback classification before recommending any step | Operator must know recovery options |
| H7 | ALWAYS paginate API results to exhaustion — partial results produce LOW confidence | Incomplete data is dangerous |
| H8 | NEVER claim the control plane upgrade is irreversible without checking rollback eligibility | EKS supports 7-day rollback (since July 2026) under specific conditions |
| H9 | NEVER operate on a cluster without first confirming target identity (name, account, region) | Wrong-cluster assessments are useless |
| H10 | NEVER assume node group AMI type without checking `DescribeNodegroup` | Custom AMIs have unpredictable behavior |

## Operation Classification

This skill is **read-only by design**. All operations are Tier 3 (Allowed):

| Tier | Operations | This Skill |
|------|-----------|------------|
| Tier 3: ALLOWED | `describe*`, `list*`, `get*`, `kubectl get/describe` | ✅ All assessment work |
| Tier 2: REVIEW-REQUIRED | `update-addon`, `patch`, `helm upgrade` | ❌ In Remediation Playbook only |
| Tier 1: BLOCKED | `update-cluster-version`, `delete-*`, `drain` | ❌ In Remediation Playbook only |

## Uncertainty Handling

```
Agent encounters unknown condition
     │
     ▼
Can it be verified via live API call?
     │
     ├─ Yes → Call API, use response as ground truth
     │
     └─ No → Mark gate as UNKNOWN
              Report: what was encountered, what was attempted, why uncertain
              Overall verdict: CANNOT DETERMINE
```

- NEVER guess at version compatibility — verify via API or declare UNKNOWN
- NEVER infer cluster configuration from naming conventions alone
- NEVER assume an add-on is EKS-managed without checking `ListAddons`
- Empty API results ≠ PASS (means "no data" not "no problem")
- Pagination exhausted without `nextToken` = complete. Stopped early = LOW confidence.

## Scope Restrictions

- All operations scoped to the cluster(s) the operator specified
- CloudWatch queries (if any) target only `/aws/eks/<cluster-name>/cluster`
- Cross-cluster operations forbidden unless explicitly requested
- Cross-account operations require explicit account list from operator

## AccessDenied Protocol

When any API returns `AccessDeniedException` or `Forbidden`:
1. Log which permission is missing
2. Mark the affected gate as **UNKNOWN**
3. Continue with remaining checks (do not abort entire assessment)
4. Include in report: "Gate X: UNKNOWN — AccessDenied on `<API>`"
5. Overall verdict: CANNOT DETERMINE (UNKNOWN gates exist)
