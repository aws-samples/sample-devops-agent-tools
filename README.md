# AWS DevOps Agent Tools

![License: MIT-0](https://img.shields.io/badge/License-MIT--0-blue)
![AWS DevOps Agent](https://img.shields.io/badge/AWS-DevOps%20Agent-orange?logo=amazonaws)

Open-source skills, custom agents, and infrastructure templates for [AWS DevOps Agent](https://aws.amazon.com/devops-agent/) that extend its capabilities for incident response, root cause analysis, and operational troubleshooting.

## ⚠️ Important Notice

These tools are provided as sample code. If you intend to deploy them in production, start with a non-production environment first, and before deploying to production:

- Test thoroughly in a non-production environment first
- Review IAM permissions and security configurations against your organization's policies
- Validate that behavior meets your operational requirements

See the [LICENSE](LICENSE) file for terms of use.

---

This repository contains:

- **Skills** — Domain-specific knowledge, decision trees, and step-by-step runbooks that the agent follows during investigations. Use them as-is or as templates for writing your own. Browse the [Skills Catalog](https://aws-samples.github.io/sample-devops-agent-tools/skills/).
- **Custom Agents** — Pre-built agent configurations with system prompts and tool assignments for recurring operational workflows like health reports and operational reviews. Browse the [Custom Agents Catalog](https://aws-samples.github.io/sample-devops-agent-tools/custom-agents/).
- **CloudFormation Templates** — Infrastructure-as-code for provisioning IAM permissions that skills require.

All tools are contributed and tested according to the [contribution guidelines](CONTRIBUTING.md).

## What Are Skills and Custom Agents?

**Skills** are structured instruction sets that teach the agent how to investigate specific operational scenarios. They follow the open [Agent Skills specification](https://agentskills.io/home) and can be uploaded to your Agent Space to extend the agent's knowledge beyond built-in capabilities. See the [DevOps Agent skills documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html) for more information.

**Custom agents** are user-defined AI agents that automate operational tasks specific to your infrastructure. You define a system prompt, assign tools and skills, and run them on demand or on a schedule. See the [DevOps Agent custom agents documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html) for more information.

## Getting Started

See the [Getting Started guide](https://aws-samples.github.io/sample-devops-agent-tools/getting-started/) on the documentation site for step-by-step instructions on deploying skills and custom agents to your Agent Space.

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

## Contributing

We welcome contributions of new skills and improvements to existing ones. See [CONTRIBUTING](CONTRIBUTING.md) for guidelines.

## References

### AWS Documentation

- [AWS DevOps Agent product page](https://aws.amazon.com/devops-agent/)
- [AWS DevOps Agent User Guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent.html)
- [AWS DevOps Agent API Reference](https://docs.aws.amazon.com/devopsagent/latest/APIReference/Welcome.html)
- [AWS DevOps Agent Skills — Creating and uploading skills](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html)
- [AWS DevOps Agent Custom Agents](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html)

### Blog Posts and Articles

- [Extend AWS DevOps Agent with Custom Skills for Your Operational Workflows](https://builder.aws.com/content/3BDdQAFY2bSmtjecZC7vbOQGSEV/extend-aws-devops-agent-with-custom-skills-for-your-operational-workflows)
- [Resolve Incidents Faster with Skills in AWS DevOps Agent](https://repost.aws/articles/ARMSvRG3qeSVK0qEAoJNsRgQ/resolve-incidents-faster-with-skills-in-aws-devops-agent)
- [Building an End-to-End Agentic SRE Using AWS DevOps Agent](https://aws.amazon.com/blogs/devops/building-an-end-to-end-agentic-sre-using-aws-devops-agent/)
- [Best Practices for Deploying AWS DevOps Agent in Production](https://aws.amazon.com/blogs/devops/best-practices-for-deploying-aws-devops-agent-in-production/)
- [Leverage Agentic AI for Autonomous Incident Response with AWS DevOps Agent](https://aws.amazon.com/blogs/devops/leverage-agentic-ai-for-autonomous-incident-response-with-aws-devops-agent/)

### Specifications and Tools

- [Agent Skills specification](https://agentskills.io/home) — the open standard this project follows
- [AGENTS.md specification](https://agents.md/) — the open standard for custom agent definitions
- [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval) — evaluation framework for testing skills

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
