# EOS Cost Analysis — Skill

## Purpose

Discovers AWS resources approaching or past End of Standard Support and calculates the Extended Support cost impact across your AWS environment. Covers EKS, RDS/Aurora, Lambda, ElastiCache, and OpenSearch — the services that incur Extended Support charges.

## Key Capabilities

- Discovers resources with deprecated versions across 5 AWS services (multi-account supported)
- Validates EOS dates and Extended Support pricing against live AWS documentation
- Calculates per-resource monthly cost with Year 1/2/3 tiered pricing escalation
- Handles RDS Multi-AZ cost doubling and instance class to vCPU mapping
- Flags deprecated Lambda runtimes as security risk (no ES charge but patching stops)
- Produces a structured report with per-resource breakdown, urgency levels, and upgrade recommendations

## Usage Options

### Option 1: Ad-Hoc via Regular Chat

Upload the skill and use it directly in the built-in DevOps Agent chat for on-demand analysis.

**Setup:**
1. Import the skill (choose "All agents" for Agent Type)
2. Go to the regular Chat interface
3. Ask any EOS-related question

**Example prompts:**
- "What's my EOS cost exposure?"
- "Check which EKS clusters are in Extended Support"
- "Full EOS analysis across all services and accounts"
- "What RDS instances are approaching end of support?"

### Option 2: Dedicated Custom Agent (Scheduled/Automated)

Create a custom agent for scheduled runs that produce reports on a weekly or monthly cadence — no human prompt needed.

**Setup:**
1. Import the skill (choose "All agents" for Agent Type)
2. Go to Agents → Create agent → Form
3. Name: `eos-cost-analysis`
4. System prompt: paste from [SYSTEM_PROMPT.md](../../custom-agents/eos-cost-analysis/SYSTEM_PROMPT.md)
5. Skills: select `eos-cost-analysis`
6. Add tools: `use_aws` and `verify_aws_claim`
7. Configure a schedule (weekly or monthly) under the agent's settings

**When to use this option:**
- You want automated, recurring EOS posture reports
- You want artifacts generated without manual prompting
- You want to track EOS cost drift over time (compare reports week over week)

## Prerequisites

- AWS DevOps Agent space with `use_aws` and `verify_aws_claim` tools available
- IAM permissions for resource discovery:
  - `eks:ListClusters`, `eks:DescribeCluster`
  - `rds:DescribeDBInstances`, `rds:DescribeDBClusters`
  - `lambda:ListFunctions`
  - `elasticache:DescribeCacheClusters`
  - `opensearch:ListDomainNames`, `opensearch:DescribeDomains`
- For multi-account analysis: associate secondary accounts in DevOps Agent settings
- AWS Support plan: Business, Enterprise On-Ramp, or Enterprise (required for DevOps Agent)

## Importing the Skill

1. In the DevOps Agent web app, go to "Skills" (left sidebar)
2. Click "Import skill"
3. Upload the zip file containing `SKILL.md`
4. In "Agent Type" field: select **"All agents"**
5. Click "Import"

## Related

- [eos-cost-analysis custom agent](../../custom-agents/eos-cost-analysis/) — system prompt and setup guide for the dedicated agent
- [aws-health-events skill](../aws-health-events/) — for correlating with Health Dashboard deprecation notifications
- [eks-operation-review skill](../eks-operation-review/) — comprehensive EKS operational review (broader than EOS)
- [AWS EKS Version Lifecycle](https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html)
- [Amazon RDS Extended Support](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/extended-support.html)
