---
hide:
  - navigation
  - toc
---

<div class="hero">
  <div class="hero-image">
    <img src="assets/devops-agent-icon.svg" alt="AWS DevOps Agent">
  </div>
  <h1>AWS DevOps Agent Tools</h1>
  <p class="hero-subtitle">Open-source skills, custom agents, and infrastructure templates that extend AWS DevOps Agent for incident response, root cause analysis, and operational reviews.</p>
  <div class="hero-buttons">
    <a href="getting-started/" class="md-button md-button--primary">Get Started</a>
    <a href="skills/" class="md-button">Browse Skills</a>
    <a href="custom-agents/" class="md-button">Browse Custom Agents</a>
  </div>
</div>

<div class="features">
  <div class="feature">
    <h3>🔍 Incident Investigation</h3>
    <p>Skills that search AWS Health events and Support case history to surface root causes and correlations during active incidents.</p>
  </div>
  <div class="feature">
    <h3>🛡️ Operational Reviews</h3>
    <p>Comprehensive best-practices assessments for EKS clusters and RDS databases, generating actionable reports aligned with AWS frameworks.</p>
  </div>
  <div class="feature">
    <h3>🤖 Custom Agents</h3>
    <p>Ready-to-use custom agent configurations that combine skills and tools into purpose-built workflows like scheduled health reports.</p>
  </div>
  <div class="feature">
    <h3>🧩 Composable</h3>
    <p>Upload multiple skills and the agent activates whichever ones are relevant. Combine investigation and review skills for end-to-end workflows.</p>
  </div>
</div>

---

## What's in This Repository?

This repository provides open-source tools that extend [AWS DevOps Agent](https://aws.amazon.com/devops-agent/) beyond its built-in capabilities:

- **Skills** — Structured instruction sets that teach the agent how to investigate specific operational scenarios. Skills follow the open [Agent Skills specification](https://agentskills.io/home) and the [DevOps Agent skills](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html) guidance, and can be uploaded to your Agent Space.
- **Custom Agents** — Pre-built agent configurations with system prompts and tool assignments for specific operational workflows (e.g., generating periodic health reports). Custom agents follow the [AGENTS.md specification](https://agents.md/) and the [DevOps Agent custom agents guidance](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html).
- **CloudFormation Templates** — Infrastructure-as-code for provisioning IAM permissions and resources that skills require.

### What Can Skills Do?

Skills enable DevOps Agent to:

- **Specialize** with investigation procedures, best practices, and organizational knowledge specific to your infrastructure
- **Automatically load** relevant instructions during investigations, eliminating repetitive guidance
- **Compose** multiple skills for end-to-end investigation workflows
- **Guide** the agent in using your custom MCP server tools effectively for infrastructure-specific workflows

### What Can Custom Agents Do?

Custom agents are user-defined AI agents that automate operational tasks specific to your infrastructure. You define a system prompt, assign tools and skills, and run them on demand or on a schedule. Common use cases include:

- **Operational reporting** — Generate daily or weekly health summaries, deployment reports, or compliance audits across your infrastructure
- **Configuration auditing** — Periodically check resource configurations against your organization's standards and produce findings
- **Trend analysis** — Analyze metrics, error patterns, or cost trends over time and surface actionable insights
- **Multi-step workflows** — Orchestrate sequences of tool calls across multiple integrations to complete complex operational procedures
- **Cross-tool correlation** — Combine data from observability platforms, CI/CD pipelines, and AWS services to answer complex operational questions

## Learn More

- [Getting Started](getting-started.md) — how to deploy skills and custom agents to your Agent Space
- [Writing your own skills](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html) — official AWS documentation
- [Creating custom agents](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-custom-agents-index.html) — official AWS documentation
- [Agent Skills specification](https://agentskills.io/home) — the open standard these skills follow
- [AGENTS.md specification](https://agents.md/) — the open standard for custom agent definitions
- [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval) — evaluation framework for testing skills
