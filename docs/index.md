---
hide:
  - navigation
  - toc
---

<div class="hero">
  <div class="hero-image">
    <img src="assets/devops-agent-icon.svg" alt="AWS DevOps Agent">
  </div>
  <h1>AWS DevOps Agent Skills</h1>
  <p class="hero-subtitle">Extend your agent with open-source skills for incident response, root cause analysis, and operational reviews.</p>
  <div class="hero-buttons">
    <a href="getting-started/" class="md-button md-button--primary">Get Started</a>
    <a href="skills/" class="md-button">Browse Skills</a>
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
    <h3>🧩 Composable</h3>
    <p>Upload multiple skills and the agent activates whichever ones are relevant. Combine investigation and review skills for end-to-end workflows.</p>
  </div>
</div>

---

## What Are AWS DevOps Agent Skills?

AWS DevOps Agent skills are structured instruction sets that teach the agent how to investigate specific operational scenarios. Skills follow the open [Agent Skills specification](https://agentskills.io/home) and can be uploaded to your DevOps Agent deployment to extend its knowledge beyond built-in capabilities.

Skills enable DevOps Agent to:

- **Specialize** with investigation procedures, best practices, and organizational knowledge specific to your infrastructure
- **Automatically load** relevant instructions during investigations, eliminating repetitive guidance
- **Compose** multiple skills for end-to-end investigation workflows
- **Guide** the agent in using your custom MCP server tools effectively for infrastructure-specific workflows

## Learn More

- [Getting Started](getting-started.md) — how to deploy skills to your Agent Space
- [Writing your own skills](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html) — official AWS documentation
- [Agent Skills specification](https://agentskills.io/home) — the open standard these skills follow
- [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval) — evaluation framework for testing skills
