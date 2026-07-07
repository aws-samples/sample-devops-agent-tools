# Project Conventions

This repository consolidates AWS DevOps Agent skills. Follow these conventions when contributing.

## Key References

- [Agent Skills spec](https://agentskills.io/home) — the open standard this project follows for skill structure
- [AWS DevOps Agent Skills documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html) — official AWS docs on creating and uploading skills
- [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval) — evaluation framework for testing skills

## Repository Structure

```
sample-devops-agent-tools/
├── README.md                 # Project overview with skills table
├── .gitignore                # Root-level ignores
├── skills/
│   ├── .gitignore            # Allowlist for DevOps Agent supported extensions only
│   └── <skill-name>/
│       ├── SKILL.md          # Required: main skill instructions with frontmatter
│       ├── README.md         # Skill documentation (purpose, prompts, upload instructions)
│       ├── CHANGELOG.md      # Version history
│       ├── .skilleval.yaml   # Evaluation configuration for Agent Skill Eval
│       ├── evals/            # Required: evaluation queries and benchmarks
│       ├── assets/           # Optional: images, diagrams, data files
│       └── references/       # Optional: supplementary reference docs
```

## Writing Skills

Skills should follow both the [Agent Skills spec](https://agentskills.io/home) best practices and [AWS DevOps Agent best practices](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html).

### SKILL.md Requirements

- Must include valid frontmatter with `name` and `description` fields.
- `name`: lowercase letters, numbers, and hyphens only (max 64 characters, no leading/trailing hyphens).
- `description`: written from the agent's perspective, specifying when and why the skill should activate. Be specific about scenarios, services, error types, or symptoms that should trigger the skill. Minimum 100 characters recommended.
- Instructions should be step-by-step, actionable, and include decision trees for different scenarios.
- Include expected outputs and success criteria.
- Reference specific AWS APIs, CLI commands, or tools the agent should use.
- Use tables for structured data (e.g., filtering strategies, relevance scoring).

### SKILL.md Frontmatter Example

```yaml
---
name: my-skill-name
description: Use this skill when investigating [specific scenarios].
  Activate when you observe [specific symptoms, error patterns, or conditions].
  This skill [what it does] by [how it does it] to [outcome].
metadata:
  author: github-username
  version: "1.0.0"
---
```

The `metadata` block with `author` and `version` fields is required. Initial version should be `"1.0.0"`.

### Skill README.md Structure

Each skill must have a README.md following this structure (see `skills/support-cases/README.md` as reference):

1. **Title** — skill name as heading
2. **Purpose** — what the skill does and why it's useful
3. **Key Capabilities** — bullet list of what the skill enables
4. **Prerequisites** — what's needed before using the skill (IAM permissions, service plans, etc.)
5. **Limitations** — known constraints or boundaries
6. **Agent Types** — which DevOps Agent types use this skill
7. **Uploading to AWS DevOps Agent** — zip command and upload steps
8. **How to Use This Skill** — sample prompts organized by agent type/use-case

### Changelog

Every skill must include a `CHANGELOG.md` tracking version history. Use semantic versioning:

```markdown
# Changelog

## 1.1.0

- Added backfill logic for missing data
- Improved error handling for API timeouts

## 1.0.0

- Initial version
```

### Evaluation Tests

Every skill should include evaluation tests using the [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval) framework:

- Add a `.skilleval.yaml` configuration file in the skill root with the following content:
  ```yaml
  audit:
    ignore:
      - STR-016    # README alongside SKILL.md is intentional
  ```
- Add evaluation queries and benchmarks in the `evals/` directory:
  - `evals.json` — functional tests (scenarios with assertions)
  - `eval_queries.json` — trigger tests (only `"should_trigger": false` tests are required; activation is implied by successful functional tests)
- Tests should cover both audit (structural quality) and functional (runtime behavior) evaluations
- Skills should achieve a passing score before being merged
- Run evaluations locally and test with DevOps Agent before submitting changes

## Allowed File Extensions

Only these extensions are permitted inside skill directories (enforced by `skills/.gitignore` and the DevOps Agent upload validator):

.md, .txt, .json, .yaml, .yml, .xml, .csv, .tsv, .html, .htm, .png, .jpg, .jpeg, .gif, .svg, .webp, .pdf

## Disallowed Content

- `scripts/` directories are not supported by DevOps Agent.
- `.claude/` directories should not be committed (except CLAUDE.md).
- `.DS_Store` and other OS files should not be committed.

## Adding a New Skill

1. Create a new directory under `skills/` with the skill name.
2. Add a `SKILL.md` with frontmatter and step-by-step instructions following the writing guidelines above.
3. Add a `README.md` following the structure described above.
4. Add a `CHANGELOG.md` starting at version 1.0.0.
5. Add evaluation tests (`.skilleval.yaml` and `evals/` directory).
6. Test the skill with DevOps Agent before submitting.
7. Update the root `README.md` skills table with the new skill's name, description, agent types, author, and docs link.
8. Update the `llms.txt` file at the repo root — add the new skill to the "Available Skills" section following the existing format: `- [Skill Name](skills/<name>/SKILL.md): One-line description`.

## Maintaining llms.txt

The `llms.txt` file at the repo root provides AI tools with a structured overview of this repository. Keep it in sync:

- **Available Skills section**: Update whenever a skill is added, removed, or renamed (step 8 above).
- **Repository Structure section**: Update whenever the directory structure conventions change (e.g., new required files, new directories, renamed paths).

## Zipping for Upload

When zipping a skill for upload to DevOps Agent, include only allowed extensions and exclude non-skill files:

```bash
cd skills
zip -r <skill-name>.zip <skill-name>/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
```

## Git Conventions

- Push to a new branch for changes; use merge requests for review.
- Commit messages should be concise and descriptive.
- Do not commit zip files (they are gitignored).
