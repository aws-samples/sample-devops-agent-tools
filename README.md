# AWS DevOps Agent Skills

![License: MIT-0](https://img.shields.io/badge/License-MIT--0-blue)
![AWS DevOps Agent](https://img.shields.io/badge/AWS-DevOps%20Agent-orange?logo=amazonaws)

Open-source skills for [AWS DevOps Agent](https://aws.amazon.com/devops-agent/) that extend its capabilities for incident response, root cause analysis, and operational troubleshooting.

## ⚠️ Important Notice

These skills are provided as sample code. If you intend to deploy these skills in production, start with a non-production environment first. Before deploying any skill to a production environment:

- Test thoroughly in a non-production environment first
- Review IAM permissions and security configurations against your organization's policies
- Validate that skill behavior meets your operational requirements

See the [LICENSE](LICENSE) file for terms of use.

---

Each skill provides domain-specific knowledge, decision trees, and step-by-step runbooks that the agent follows during investigations. Use them as-is to enhance AWS DevOps agent, or as templates for writing your own custom skills.

All skills were tested using [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval) and manually in DevOps Agent web app, for functionality without skill and with skill, and for effective triggering. The tests reports are available in each skill's `evals/` directory.

## What Are AWS DevOps Agent Skills?

AWS DevOps Agent skills are structured instruction sets that teach the agent how to investigate specific operational scenarios. Skills follow the open [Agent Skills specification](https://agentskills.io/home) and can be uploaded to your DevOps Agent deployment to extend its knowledge beyond built-in capabilities.

Skills enable DevOps Agent to:

- Specialize with investigation procedures, best practices, and organizational knowledge specific to your infrastructure
- Automatically load relevant instructions during investigations, eliminating repetitive guidance
- Compose multiple skills for end-to-end investigation workflows (e.g., retrieving deployments from your CI/CD pipeline and searching code repositories)
- Guide the agent in using your custom MCP server tools effectively for infrastructure-specific workflows

## Available Skills

| Skill | Description | Agent Types | Author | Docs |
|-------|-------------|-------------|--------|------|
| [aws-health-events](skills/aws-health-events/) | Retrieves and analyzes AWS Health events (service issues, scheduled changes, account notifications) to identify AWS-side events that correlate with operational issues | Chat tasks, Incident RCA | [udid-aws](https://github.com/udid-aws) | [README](skills/aws-health-events/README.md) |
| [support-cases](skills/support-cases/) | Searches and analyzes AWS Support cases to find historical incidents with similar symptoms, proven remediations, and recurring patterns | Chat tasks, Incident RCA | [udid-aws](https://github.com/udid-aws) | [README](skills/support-cases/README.md) |
| [eks-operation-review](skills/eks-operation-review/) | Performs comprehensive Amazon EKS operational reviews aligned with the AWS EKS Best Practices Guide covering security, reliability, networking, and scalability | Chat tasks, Prevention | [yakiratz-aws](https://github.com/yakiratz-aws) | [README](skills/eks-operation-review/README.md) |
| [rds-operation-review](skills/rds-operation-review/) | Performs comprehensive Amazon RDS operational reviews | Chat tasks, Prevention | [yakiratz-aws](https://github.com/yakiratz-aws) | [README](skills/rds-operation-review/README.md) |
| [crm-production-investigation-guidelines](skills/crm-production-investigation-guidelines/) | **Sample skill** demonstrating how to write investigation guidelines for the Incident Triage agent type using a fictional CRM application | Incident Triage | [jossaiaws](https://github.com/jossaiaws) | [README](skills/crm-production-investigation-guidelines/README.md) |
| [skip-scheduled-maintenance](skills/skip-scheduled-maintenance/) | **Sample skill** demonstrating how to skip low-priority incidents during a scheduled maintenance window for the Incident Triage agent type | Incident Triage | [dgorin6](https://github.com/dgorin6) | [README](skills/skip-scheduled-maintenance/README.md) |
| [enrich-with-aws-security-agent](skills/enrich-with-aws-security-agent/) | Queries AWS Security Agent CloudWatch logs to retrieve code-level security findings (file, line number, vulnerability type) during incident investigations with potential security root causes | Chat tasks, Incident RCA | [yakiratz-aws](https://github.com/yakiratz-aws) | [README](skills/enrich-with-aws-security-agent/README.md) |
| [investigation-cost-guardrail](skills/investigation-cost-guardrail/) | Estimates the AWS API cost of an incident investigation before any query runs, shows a per-step cost plan, and cancels if the estimate exceeds a configurable threshold | Incident RCA | [inesttia](https://github.com/inesttia) | [README](skills/investigation-cost-guardrail/README.md) |

## Getting Started

### 1. Clone the repository

Only needed if you plan to upload a skill as a zip file (Option B below) or via the Asset API (Option C below). If you're importing directly from GitHub (Option A), you can skip this step.

```bash
git clone https://github.com/aws-samples/sample-code-for-devops-agent-skills.git
cd sample-code-for-devops-agent-skills
```

### 2. Choose a skill

Browse the skills table above and read the skill's `README.md` for details on its purpose, prerequisites, and sample prompts.

### 3. Upload to AWS DevOps Agent

You can add skills to your Agent Space in three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import skills directly from this repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the skill directory in the repo. The skill stays in sync with the repository. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository — including this one — even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

Zip the skill directory (see the zip command in each skill's README) and [upload it via the DevOps Agent web app](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills). Detailed instructions are in each skill's README.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

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

## Skill Permissions

Each skill documents the IAM permissions it requires in its README under **Prerequisites**. The DevOps Agent role associated with your Agent Space must have these permissions for the skill to function fully.

Most permissions are already covered by the AWS managed policy [`AIDevOpsAgentAccessPolicy`](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-devops-agent-iam-permissions.html). For skills that need additional permissions, you can use the included CloudFormation template to automatically provision them:

```bash
aws cloudformation deploy \
  --template-file cloudformation/devops-agent-skill-policies.yaml \
  --stack-name devops-agent-skill-policies \
  --parameter-overrides ExistingRoleName=<YOUR-DEVOPS-AGENT-ROLE-NAME> \
  --capabilities CAPABILITY_NAMED_IAM
```

The template supports enabling/disabling policies per skill, optional region restrictions, and can either attach to an existing role or create a new one. See [`cloudformation/devops-agent-skill-policies.yaml`](cloudformation/devops-agent-skill-policies.yaml) for details.

## Writing Your Own Skills

Want to create custom skills for your operational workflows? See the [AWS DevOps Agent skills documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html) for the full guide, or use the skills in this repository as templates.

Key principles for effective skills (see also the [Agent Skills best practices](https://agentskills.io/skill-creation/best-practices)):

- Decide which agent types in DevOps Agent are relevant for your skill
- Write a description that specifies when and why the skill should activate — include specific symptoms, services, or error patterns that trigger it
- Ground instructions in real expertise — specific API patterns, edge cases, and project conventions, not generic advice
- Keep `SKILL.md` focused and under 500 lines; move detailed reference material to `references/`
- Add what the agent wouldn't know on its own — omit explanations of general concepts
- Favor step-by-step procedures over declarative statements so the approach generalizes across tasks
- Include decision trees for branching scenarios and checklists for multi-step workflows
- Provide defaults rather than menus — pick a recommended approach and mention alternatives briefly
- Include a gotchas section for non-obvious facts that defy reasonable assumptions
- Test with the [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval) framework, and manually using the DevOps Agent web app, without skill and with skill

## Contributing

We welcome contributions of new skills and improvements to existing ones. See [CONTRIBUTING](CONTRIBUTING.md) for guidelines.

## References

### AWS Documentation

- [AWS DevOps Agent product page](https://aws.amazon.com/devops-agent/)
- [AWS DevOps Agent User Guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html)
- [AWS DevOps Agent API Reference](https://docs.aws.amazon.com/devopsagent/latest/APIReference/Welcome.html)
- [AWS DevOps Agent Skills — Creating and uploading skills](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html)

### Blog Posts and Articles

- [Extend AWS DevOps Agent with Custom Skills for Your Operational Workflows](https://builder.aws.com/content/3BDdQAFY2bSmtjecZC7vbOQGSEV/extend-aws-devops-agent-with-custom-skills-for-your-operational-workflows)
- [Building an End-to-End Agentic SRE Using AWS DevOps Agent](https://aws.amazon.com/blogs/devops/building-an-end-to-end-agentic-sre-using-aws-devops-agent/)
- [Best Practices for Deploying AWS DevOps Agent in Production](https://aws.amazon.com/blogs/devops/best-practices-for-deploying-aws-devops-agent-in-production/)
- [Leverage Agentic AI for Autonomous Incident Response with AWS DevOps Agent](https://aws.amazon.com/blogs/devops/leverage-agentic-ai-for-autonomous-incident-response-with-aws-devops-agent/)

### Specifications and Tools

- [Agent Skills specification](https://agentskills.io/home) — the open standard this project follows
- [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval) — evaluation framework for testing skills

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
