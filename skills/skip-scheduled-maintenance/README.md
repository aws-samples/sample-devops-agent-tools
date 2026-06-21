# Skip Scheduled Maintenance Skill (Sample)

This is a **sample skill** demonstrating how to write an incident filtering skill for the AWS DevOps Agent **Incident Triage** agent type. It shows how to define skip criteria so the agent automatically filters low-priority incidents during a scheduled maintenance window, avoiding unnecessary investigations for expected disruptions.

This skill appears as the ["Example: Incident filtering skill"](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#example-incident-filtering-skill) in the AWS DevOps Agent User Guide.

> **Note:** This skill uses hardcoded dates and severity thresholds specific to a fictional maintenance scenario and cannot be reused as-is. Use it as a template for writing similar incident filtering skills for your own maintenance windows.

## Purpose

Skills targeted to the Incident Triage agent type can define criteria for automatically skipping incidents that don't require investigation. When a new incident matches the skip criteria, AWS DevOps Agent marks it as **Skipped** and provides a reason explaining why it was filtered.

During scheduled maintenance windows, monitoring systems often fire alarms for expected disruptions — service restarts, brief connectivity blips, or elevated error rates from rolling deployments. Without filtering guidance, the Incident Triage agent would investigate each of these as a genuine incident. This sample skill demonstrates how to encode maintenance-window awareness so the agent can skip low-priority alarms while still escalating critical ones.

## Key Capabilities (Demonstrated)

- Defining skip criteria that the Incident Triage agent evaluates for each incoming incident
- Combining multiple filtering conditions (time window AND severity) for skip decisions
- Preserving escalation paths for high-severity incidents regardless of maintenance status

## Prerequisites

- AWS DevOps Agent space

## Limitations

- This skill uses hardcoded dates (2025-03-15 02:00–06:00 UTC) — you must update the window for your actual maintenance schedule
- Only filters by severity and time; does not account for specific services or alarm sources
- Cannot be reused as-is — adapt the time window and criteria to your environment

## Agent Types

This skill is designed for:

- **Incident Triage** — initial incident assessment. When the skill's skip criteria match an incoming incident, the agent marks it as Skipped instead of launching an investigation.

## Uploading to AWS DevOps Agent

To deploy this skill (or your adapted version) to your Agent Space, you can use any of three ways:

**Option A: Import from GitHub (recommended)**

If you have a [GitHub connection configured](https://docs.aws.amazon.com/devopsagent/latest/userguide/connecting-to-cicd-pipelines-connecting-github.html) in your Agent Space, you can import this skill directly from the repository. In the DevOps Agent web app, go to Settings → Add Skill → Import from repository, then point to the `skills/skip-scheduled-maintenance` directory. See [Importing a skill from a repository](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) for full instructions.

> **Note:** You cannot connect the `aws-samples` GitHub organization directly because the GitHub connection setup requires admin rights on the organization. Instead, connect your personal GitHub account and select any repository from it during the connection setup. Once a GitHub connection is established, you can import skills from any public repository — including this one — even if it wasn't selected during the connection setup.

**Option B: Upload as a zip file**

1. Zip the skill directory (only including allowed extensions):

   ```bash
   cd skills
   zip -r skip-scheduled-maintenance.zip skip-scheduled-maintenance/ -i '*.md' '*.txt' '*.json' '*.yaml' '*.yml' '*.xml' '*.csv' '*.tsv' '*.html' '*.htm' '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.pdf' -x '*/.claude/*' '*/scripts/*' '*/README.md' '*/.skilleval.yaml' '*/.skilleval.yml' '*/CHANGELOG.md' '*/evals/*'
   ```

2. In the AWS DevOps Agent web app, navigate to the **Skills** page.
3. Click **Add skill** → **Upload skill**.
4. Drag and drop the zip file (max 6 MB).
5. Select the agent type: **Incident Triage**.
6. Click **Upload**.

**Option C: Upload via the Asset API**

Use the AWS DevOps Agent Asset API to programmatically manage skills — useful for CI/CD pipelines or automation workflows. Assign the skill to the `INCIDENT_TRIAGE` agent type. See [Managing a skill end-to-end](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-managing-assets.html#managing-a-skill-end-to-end) for the full API workflow.

For more details, see [Uploading a skill](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html#creating-skills) in the AWS DevOps Agent User Guide.

## How to Use This Skill

### As a Template

1. Copy the `SKILL.md` file as a starting point for your own incident filtering skill
2. Replace the hardcoded maintenance window with your actual planned maintenance dates/times
3. Adjust the severity threshold if your organization uses different severity levels
4. Add additional filtering criteria (e.g., specific services, alarm names, or AWS accounts)
5. Consider creating separate skills for different maintenance windows or filtering scenarios

### Sample Incident Triage Behavior (with this skill active)

When the skill is active, the Incident Triage agent evaluates each incoming incident against the skip criteria. Incidents that match are marked as **Skipped** with a reason:

- **Skipped:** A MEDIUM-severity alarm arriving at 2025-03-15 03:30 UTC
- **Skipped:** A LOW-severity alarm arriving at 2025-03-15 04:15 UTC
- **Investigated:** A CRITICAL-severity alarm arriving at 2025-03-15 03:00 UTC (high severity is never skipped)
- **Investigated:** A HIGH-severity alarm arriving at 2025-03-15 05:00 UTC (high severity is never skipped)
- **Investigated:** A MEDIUM-severity alarm arriving at 2025-03-15 07:00 UTC (outside maintenance window)
