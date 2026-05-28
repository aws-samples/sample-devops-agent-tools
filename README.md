# DevOps Agent Skills

A repository for AWS DevOps Agent skills. Each skill is packaged as a sub-directory under `skills/` and includes its definition, reference documentation, and evaluation data.
The skills are meant to be used either as samples for writing your own skills, or as they are in DevOps Agent. If you intend to use the skills in this repo in DevOps Agent, please first use them in a non-production small environment.

## Skills

| Skill | Agent Types | Author | Docs |
|-------|-------------|--------|------|
| [aws-health-events](skills/aws-health-events/) | Chat tasks, Incident RCA | [udid-aws](https://github.com/udid-aws) | [README](skills/aws-health-events/README.md) |
| [support-cases](skills/support-cases/) | Chat tasks, Incident RCA | [udid-aws](https://github.com/udid-aws) | [README](skills/support-cases/README.md) |
| [eks-operation-review](skills/eks-operation-review/) | Chat tasks, Prevention | [yakiratz-aws](https://github.com/yakiratz-aws) | [README](skills/eks-operation-review/README.md) |

## How to Use

Each skill lives in its own directory under `skills/`. To use a skill:

1. Clone the repository:
   ```bash
   git clone https://github.com/aws-samples/sample-code-for-devops-agent-skills.git
   cd sample-code-for-devops-agent-skills
   ```
2. Browse the skills table above and read the skill's `README.md` for details on its purpose, prerequisites, and sample prompts.
3. Zip the skill directory (see the zip command in each skill's README) and [upload it via the AWS DevOps Agent Operator Web App](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills). More detailed instructions can be found in each skill's README.md file.

### Skill directory structure

```
skills/<skill-name>/
├── SKILL.md          # Main skill instructions (required)
├── README.md         # Documentation, prerequisites, and upload guide
├── CHANGELOG.md      # Version history
├── evals/            # Evaluation queries and benchmarks (required)
├── assets/           # Images, diagrams, data files (optional)
└── references/       # Supplementary reference docs (optional)
```

The `SKILL.md`, `references/`, and `assets/` directories are what AWS DevOps Agent reads at runtime. Everything else supports development, testing, and documentation.

## Contributing

See [CONTRIBUTING](CONTRIBUTING.md) for more information.

## References

* AWS documentation
  * [DevOps Agent product page](https://aws.amazon.com/devops-agent/)
  * [DevOps Agent user guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html)
  * [DevOps Agent API reference](https://docs.aws.amazon.com/devopsagent/latest/APIReference/Welcome.html)
  * [DevOps Agent skills user guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html)

* AWS blog posts and articles
  * [AWS Builder Center article - DevOps Agent skills](https://builder.aws.com/content/3BDdQAFY2bSmtjecZC7vbOQGSEV/extend-aws-devops-agent-with-custom-skills-for-your-operational-workflows)
  * [AWS blog post - Building an end-to-end agentic SRE using AWS DevOps Agent](https://aws.amazon.com/blogs/devops/building-an-end-to-end-agentic-sre-using-aws-devops-agent/)
  * [AWS blog post - Best Practices for Deploying AWS DevOps Agent in Production](https://aws.amazon.com/blogs/devops/best-practices-for-deploying-aws-devops-agent-in-production/)
  * [AWS blog post - Leverage Agentic AI for Autonomous Incident Response with AWS DevOps Agent](https://aws.amazon.com/blogs/devops/leverage-agentic-ai-for-autonomous-incident-response-with-aws-devops-agent/)

* Other
  * [Agent Skills specification](https://agentskills.io/home)
  * [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval)

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
