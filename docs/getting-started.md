# Getting Started

This guide walks you through deploying a skill from this repository to your AWS DevOps Agent Space.

## Prerequisites

!!! info "Before you begin"
    - An [AWS DevOps Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html) set up with your target AWS account as a cloud source
    - The skill-specific prerequisites documented on each skill's page (IAM permissions, service plans, etc.)

---

## Deploy a Skill

### 1. Clone the Repository

```bash
git clone https://github.com/aws-samples/sample-code-for-devops-agent-skills.git
cd sample-code-for-devops-agent-skills
```

### 2. Choose a Skill

Browse the [available skills](skills/index.md) and review the skill's page for details on its purpose, prerequisites, and sample prompts.

### 3. Package the Skill

From the `skills/` directory, zip the skill for upload:

```bash
cd skills
zip -r <skill-name>.zip <skill-name>/ \
  -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' \
     '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' \
  -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' \
     '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
```

!!! note
    The zip excludes evaluation files, READMEs, and changelogs since those are development artifacts — only `SKILL.md`, `references/`, and `assets/` are used by the agent at runtime.

### 4. Upload to AWS DevOps Agent

Upload the zip file via the Operator Web App and select the appropriate agent types. See [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#:~:text=To%20create%20a%20skill%20via%20zip%20upload,add%20the%20skill%20to%20your%20Agent%20Space.) in the AWS DevOps Agent User Guide for detailed steps.

### 5. Verify

In the DevOps Agent Chat, try one of the sample prompts listed on the skill's documentation page. The agent should automatically activate the skill based on the context of your request.

## Skill Directory Structure

Each skill follows a consistent structure based on the [Agent Skills specification](https://agentskills.io/home):

```
skills/<skill-name>/
├── SKILL.md          # Main skill instructions with frontmatter (required)
├── README.md         # Documentation, prerequisites, and upload guide
├── CHANGELOG.md      # Version history
├── evals/            # Evaluation queries and benchmarks
├── assets/           # Images, diagrams, data files (optional)
└── references/       # Supplementary reference docs (optional)
```

The `SKILL.md`, `references/`, and `assets/` directories are what AWS DevOps Agent reads at runtime. Everything else supports development, testing, and documentation.

## Writing Your Own Skills

For guidance on creating custom skills for your operational workflows, see the [AWS DevOps Agent skills documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html).

You can also use the skills in this repository as templates.
