# Implementation Plan: AWS Health Events Skill

## Overview

This implementation creates the AWS Health Events skill for the AWS DevOps Agent. The skill is a set of markdown-based instruction documents that guide the agent to retrieve and analyze AWS Health events during incident investigation and chat-based reporting. Each task creates one or more files in the `skills/aws-health-events/` directory, following the same structural patterns as the existing `support-cases` skill.

## Tasks

- [x] 1. Create the AWS Health API reference document
  - [x] 1.1 Create `skills/aws-health-events/references/health-api-reference.md`
    - Document the three AWS Health API operations: DescribeEvents, DescribeEventDetails, DescribeAffectedEntities
    - Include key parameters table for each operation (name, type, description, constraints)
    - Include response fields table for each operation
    - Add common service codes mapping (service name → Health API service namespace)
    - Add event type categories (issue, scheduledChange, accountNotification) and statuses (open, closed, upcoming)
    - Document important constraints: us-east-1 region only, pagination via nextToken, rate limits, batch size of 10 for DescribeEventDetails
    - Follow the same table-based format as `skills/support-cases/references/support-api-reference.md`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 4.1, 8.4_

- [x] 2. Create the main SKILL.md instruction document
  - [x] 2.1 Create `skills/aws-health-events/SKILL.md` with frontmatter and 6-step agent instruction flow
    - Add frontmatter: name `aws-health-events`, description covering incident investigation/service degradation/health event reporting activation scenarios, metadata with author `udid-aws` and version `"1.0.0"`
    - Write "When to Use This Skill" section covering incident investigation and chat reporting contexts
    - Write "Prerequisites" section: IAM permissions (`health:DescribeEvents`, `health:DescribeEventDetails`, `health:DescribeAffectedEntities`, `health:DescribeEventTypes`), us-east-1 endpoint requirement
    - Write Step 1: Gather Incident Context — extract affected services, timeframe, resources, region from the incident
    - Write Step 2: Search Health Events — use DescribeEvents with filters (service, time, region, status, eventScopeCode); handle pagination up to 500 events; include filtering strategies table
    - Write Step 3: Get Event Details — use DescribeEventDetails batching up to 10 ARNs per call; extract descriptions, timelines, status; handle failedSet gracefully
    - Write Step 4: Identify Affected Entities — use DescribeAffectedEntities for ACCOUNT_SPECIFIC events only; match entities against incident resources; handle pagination up to 500 entities
    - Write Step 5: Correlate with Incident — score relevance (High/Medium/Low) using service match, timeframe overlap, region/AZ match, and resource match; label High/Medium open events as likely contributing factors
    - Write Step 6: Present Structured Output — group by event type category (issues first, then scheduled changes, then account notifications); sort by relevance then start time descending; include actionable next steps
    - Add Decision Tree section: known service → search that service (7 days) → if empty, search related services → if empty, expand to 14 days; unknown service → search all filtered by region/AZ; chat report → specified period (default 30 days, max 90 days)
    - Add Service Dependency Map table for broadened searches (ELB→EC2/VPC/Route53, RDS→EC2/EBS, etc.)
    - Add Error Handling section: missing permissions, throttling (429 with exponential backoff 1s→2s→4s, max 3 retries), service errors (5xx), timeout (30s), zero results, invalid time range
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [x] 3. Checkpoint - Verify core skill files
  - Ensure SKILL.md has valid frontmatter and all 6 steps are complete. Ensure health-api-reference.md covers all three API operations. Ask the user if questions arise.

- [x] 4. Create supporting documentation files
  - [x] 4.1 Create `skills/aws-health-events/README.md`
    - Follow the project convention structure: Title, Purpose, Key Capabilities, Prerequisites, Limitations, Agent Types, Uploading to AWS DevOps Agent, How to Use This Skill
    - Key Capabilities: retrieve open/resolved health events, search by service/severity/time/region, identify affected resources, correlate events with incidents, generate health posture reports
    - Prerequisites: IAM permissions for `health:Describe*` actions, Health API access (available to all AWS accounts)
    - Limitations: Health API only available in us-east-1, event data retention period, rate limits
    - Agent Types: Chat tasks, Incident RCA
    - Include zip command for upload (following same pattern as support-cases README)
    - Include sample prompts for Chat use-case (health reports, service-specific queries) and Investigation use-case (incident correlation, degradation analysis)
    - _Requirements: 1.1, 1.2, 6.1, 6.7, 8.1, 8.4_

  - [x] 4.2 Create `skills/aws-health-events/CHANGELOG.md`
    - Add version 1.0.0 with "Initial version" entry
    - Follow the same format as existing skills' changelogs
    - _Requirements: (project convention)_

- [x] 5. Create evaluation framework files
  - [x] 5.1 Create `skills/aws-health-events/.skilleval.yaml`
    - Add audit configuration ignoring STR-016 (README alongside SKILL.md is intentional)
    - Follow the exact same format as `skills/support-cases/.skilleval.yaml`
    - _Requirements: (project convention)_

  - [x] 5.2 Create `skills/aws-health-events/evals/evals.json`
    - Create functional evaluation scenarios covering:
      - `incident-health-event-lookup`: Incident investigation triggers health event search with correct service filter
      - `event-detail-extraction`: Retrieve details for identified events with description, timeline, status
      - `affected-entity-matching`: Identify affected resources matching incident context
      - `relevance-correlation`: Correlate events with incident context, classify as High/Medium/Low
      - `chat-health-report`: Generate health event summary report organized by category
      - `no-events-found`: No matching events, suggests alternative investigation paths
      - `api-region-awareness`: Health API called from us-east-1 regardless of resource region
      - `broadened-search`: Initial search returns no results, expands to related services then time window
    - Each scenario must include: id, prompt, expected_output, assertions array
    - _Requirements: 1.1, 1.4, 2.1, 2.10, 3.1, 4.1, 5.1, 5.2, 6.1, 7.1, 8.4, 9.1, 9.2, 9.4_

  - [x] 5.3 Create `skills/aws-health-events/evals/eval_queries.json`
    - Create negative trigger tests (should_trigger: false only) for queries that should NOT activate this skill
    - Include: general AWS architecture questions, code/programming questions, configuration questions unrelated to health events or incidents, billing questions, IAM policy questions
    - Follow the same format as `skills/support-cases/evals/eval_queries.json`
    - _Requirements: 5.1 (Property 5: Negative activation boundary)_

- [x] 6. Checkpoint - Verify all skill files
  - Ensure all files are created in the correct directory structure. Verify evals.json covers all 8 test scenarios. Verify eval_queries.json has at least 5 negative trigger tests. Ask the user if questions arise.

- [x] 7. Update root README.md
  - [x] 7.1 Update `README.md` at the repository root
    - Add a new row to the skills table: `| [aws-health-events](skills/aws-health-events/) | Chat tasks, Incident RCA | [udid-aws](https://github.com/udid-aws) | [README](skills/aws-health-events/README.md) |`
    - Insert the row alphabetically or after the existing entries
    - _Requirements: (project convention — "Adding a New Skill" step 7)_

- [x] 8. Final checkpoint - Review complete skill package
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- This skill is a set of markdown instruction documents, not executable code. There are no property-based tests or unit tests — correctness is verified through the Agent Skill Eval framework (audit + functional evaluations).
- Each task references specific requirements for traceability.
- Checkpoints ensure incremental validation of the skill package.
- The implementation follows the same patterns as the existing `support-cases` skill for consistency.
- All files use only allowed extensions (.md, .yaml, .json) per DevOps Agent upload constraints.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "4.2", "5.1"] },
    { "id": 2, "tasks": ["4.1", "5.2", "5.3"] },
    { "id": 3, "tasks": ["7.1"] }
  ]
}
```
