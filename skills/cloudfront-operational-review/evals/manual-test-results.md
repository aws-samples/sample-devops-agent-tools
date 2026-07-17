# Manual DevOps Agent Test Results — cloudfront-operational-review

Manual validation in an AWS DevOps Agent Space against a live CloudFront distribution,
complementing the automated Agent Skill Eval results (`report.json`, root `"passed": true`).

- **Date:** 2026-07-17
- **Environment:** DevOps Agent Space, On-demand (Chat) agent type
- **Target:** one live CloudFront distribution (ID redacted as `E1XXXXXXXXXXXX`)
- **Method:** each prompt run in a fresh chat; skill never named explicitly (auto-invocation only)

## Positive cases (skill expected to load and run)

| Prompt | Skill loaded? | Behavior | Result |
|--------|:-------------:|----------|:------:|
| "Review my CloudFront distribution for best practices" | Yes | Ran the review end-to-end as expected | ✅ Pass |
| "Do a health check on our CDN — is it secure and cached well?" | Yes | Loaded the skill and ran the review as expected | ✅ Pass |

## Negative cases (skill expected NOT to hijack the response)

| Prompt | Skill loaded? | Behavior | Result |
|--------|:-------------:|----------|:------:|
| "Set up a new CloudFront distribution with an S3 origin and OAC" | Yes | Gave correct setup guidance/instructions, did not force a review, then *offered* to run a COR | ✅ Acceptable |
| "Create a CloudFront invalidation for /images/*" | No | Attempted the tool call and failed as expected (read-only scope); no skill load | ✅ Pass |
| "How much does CloudFront data transfer out cost per GB in the US?" | No | Answered as a pricing question; did not trigger the skill | ✅ Pass |
| "My CloudFront distribution returns 403 on one path — help me fix the bucket policy" | No | Handled as targeted debugging; did not trigger the skill | ✅ Pass |

## Notes

- **"Set up a new distribution…"** — the skill loaded, but behavior was correct: the agent
  delivered setup guidance for the user's actual request and only *offered* a CloudFront
  Operational Review as an opt-in follow-up. It did not force a review or cross the read-only
  boundary. This is consistent with the Agent Skills progressive-disclosure model (load when
  possibly relevant, then decide how to act). Treated as acceptable, not a hijack.
- **"Create a CloudFront invalidation…"** — the agent attempted the operation and failed as
  expected; the skill is strictly read-only and does not perform mutating actions such as
  `CreateInvalidation`. No skill load occurred.
- **Environment difference vs. automated eval:** in the Claude-CLI eval harness the
  "set up a new distribution" query scored as not-triggered (`signal=none`); the DevOps Agent
  loader is more eager to load the skill but still behaved correctly. Both environments
  produced correct outcomes.

## Overall

Auto-invocation is reliable on genuine review/health-check prompts, and near-miss prompts
(create/provision, invalidation, pricing, single-path debugging) do not cause the skill to
hijack the response. Behavior is consistent with the automated trigger eval (pass rate 1.0).
No changes required; ship as-is.

## Automated Agent Skill Eval — summary

Run with the [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval)
framework (`skill-eval report`, functional + trigger with `--runs-trigger 1`):

| Section | Result |
|---------|--------|
| Audit | ✅ Passed — 0 critical, 0 warning (INFO only: external AWS doc links; SKILL.md ~5.4k tokens vs 5k guideline) |
| Functional | ✅ Passed — classified PARETO_BETTER (quality improves while cost drops) |
| Trigger | ✅ Passed — pass rate 1.0 across the should-not-trigger near-misses |
| **Root** | ✅ **`"passed": true`** |

The generated eval artifacts (`benchmark.json`, `report.json`, `trigger_report.json`) are
intentionally not committed: they record raw model transcripts from the eval environment and
local filesystem paths. Only the eval inputs (`evals.json`, `eval_queries.json`, `files/`) are
committed, matching the convention of the other operation-review skills. Maintainers can
reproduce the run from those inputs.
