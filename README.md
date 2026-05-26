# AWS DevOps Agent Skill Central

A central repository for AWS DevOps Agent skills. Each skill is packaged as a sub-directory under `skills/` and includes its definition, reference documentation, and evaluation data.

## Skills

| Skill | Agent Types | Author | Docs |
|-------|-------------|--------|------|
| [support-cases](skills/support-cases/) | Chat tasks, Incident RCA | [udid-aws](https://github.com/udid-aws) | [README](skills/support-cases/README.md) |
| [eks-operation-review](skills/eks-operation-review/) | Chat tasks, Prevention | yakiratz | [README](skills/eks-operation-review/README.md) |

## How to Use

Each skill lives in its own directory under `skills/`. To use a skill:

1. Clone the repository:
   ```bash
   git clone https://github.com/aws-samples/sample-code-for-devops-agent-skills.git
   cd sample-code-for-devops-agent-skills
   ```
2. Browse the skills table above and read the skill's `README.md` for details on its purpose, prerequisites, and sample prompts.
3. Zip the skill directory (see the zip command in each skill's README) and upload it via the [AWS DevOps Agent Operator Web App](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills).

### Skill directory structure

```
skills/<skill-name>/
├── SKILL.md          # Main skill instructions (required)
├── README.md         # Documentation, prerequisites, and upload guide
├── CHANGELOG.md      # Version history
├── references/       # Supplementary reference docs (optional)
├── assets/           # Images, diagrams, data files (optional)
└── evals/            # Evaluation queries and benchmarks (optional)
```

The `SKILL.md`, `references/`, and `assets/` directories are what AWS DevOps Agent reads at runtime. Everything else supports development, testing, and documentation.
