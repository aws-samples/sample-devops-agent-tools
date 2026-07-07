# Getting Started

This guide walks you through deploying skills and custom agents from this repository to your AWS DevOps Agent Space.

## Prerequisites

!!! info "Before you begin"
    - An [AWS DevOps Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html) set up with your target AWS account as a cloud source
    - The skill-specific prerequisites documented on each skill's page (IAM permissions, etc.)
    - The agent-specific prerequisites documented on each custom agent's page (IAM permissions, tools, skills, etc.)

---

## Skills

### Deploy a Skill

#### 1. Choose a Skill

Browse the [Skills Catalog](skills/index.md) and select a skill that matches your use case.

#### 2. Follow the Skill's README

Each skill's page includes prerequisites, deployment instructions, and sample prompts. Follow the instructions in the skill's README to deploy it to your Agent Space.

#### 3. Verify

In the DevOps Agent Chat, try one of the sample prompts listed on the skill's page. The agent should automatically activate the skill based on the context of your request.

### Directory Structure

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

### Writing Your Own Skills

For guidance on creating custom skills for your operational workflows, see the [AWS DevOps Agent skills documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html).

You can also use the skills in this repository as templates.

---

## Custom Agents

### Deploy a Custom Agent

#### 1. Choose a Custom Agent

Browse the [Custom Agents Catalog](custom-agents/index.md) and select an agent that matches your workflow.

#### 2. Follow the Agent's README

Each custom agent's page includes prerequisites, step-by-step creation instructions, and execution guidance. Follow the instructions in the agent's README to create and configure it in your Agent Space.

#### 3. Verify

Execute the custom agent on-demand from its page in the DevOps Agent web app. Check that the agent produces the expected output (report artifact, recommendations, etc.).

### Directory Structure

Each custom agent follows a consistent structure:

```
custom-agents/<agent-name>/
├── SYSTEM_PROMPT.md  # The system prompt to paste into the agent configuration
├── README.md         # Documentation, prerequisites, creation steps, execution guide
└── CHANGELOG.md      # Version history
```

The `SYSTEM_PROMPT.md` is what you paste into the DevOps Agent web app when creating the agent. The README provides the full setup walkthrough.

### Creating Your Own Custom Agents

For guidance on building custom agents for your operational workflows, see the [AWS DevOps Agent custom agents documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html).

You can also use the agents in this repository as templates — they demonstrate how to structure a system prompt, assign tools, and compose skills into a purpose-built workflow.
