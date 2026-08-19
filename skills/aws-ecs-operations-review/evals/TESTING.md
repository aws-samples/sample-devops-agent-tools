# Multi-model testing

Because the skill competes for discovery against many other skills, the
`name`/`description` frontmatter and the knowledge evals should be run against
**every model the skill will be used with** (e.g. Claude Haiku, Sonnet, and
Opus) — what triggers reliably on Opus may need confirmation on a smaller
model. Re-run after any change to the frontmatter or the workflow steps.

## Suites

- `eval_queries.json` — routing checks: does the right query trigger the skill
  (and do off-topic queries correctly *not* trigger it)?
- `evals.json` — skill-knowledge evals (six pillars, read-only contract, ARN
  validation, Tier-1 foundation, coverage gate, alarm deliverable), run against
  `files/service-context.json`.

## Results matrix

Record real, observed pass rates here — never fabricate. Leave a cell blank
until that suite has actually been run on that model.

| Model | eval_queries.json (routing) | evals.json (knowledge) | Date | Notes |
|-------|-----------------------------|------------------------|------|-------|
| Claude Opus   | | | | |
| Claude Sonnet | | | | |
| Claude Haiku  | | | | |

## Method

1. Run each suite with your skill-eval runner, with and without the skill
   installed, on each target model.
2. For routing, confirm `should_trigger: true` queries select the skill and
   `should_trigger: false` queries do not.
3. For knowledge, confirm each assertion passes against the model's answer.
4. Record the pass rate and date in the matrix above; note any regressions and
   the change that caused them.
