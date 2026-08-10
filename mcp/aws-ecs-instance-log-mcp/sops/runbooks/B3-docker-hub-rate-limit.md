---
title: "B3 — Docker Hub Rate Limit"
description: "Diagnose image pull failures due to Docker Hub rate limiting"
status: active
severity: HIGH
triggers:
  - "toomanyrequests.*Too Many Requests"
  - "You have reached your pull rate limit"
owner: devops-agent
objective: "Mitigate Docker Hub rate limiting and restore image pulls"
context: "Docker Hub enforces pull rate limits: 100 pulls/6h for anonymous, 200 pulls/6h for authenticated. ECS tasks pulling public images can hit these limits quickly."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical
- Use `search` tool with instanceId and query=`toomanyrequests|rate limit|Too Many Requests` to confirm rate limiting

SHOULD:
- Use `search` tool with query=`docker.io|hub.docker.com|registry-1.docker.io` to identify which images are from Docker Hub

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to check frequency of pull attempts
- Determine if multiple tasks/services are pulling the same public images

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- Recommend migrating public images to ECR (pull-through cache or manual copy)
- Recommend configuring Docker Hub authentication for higher limits

## Common Issues

- symptoms: "toomanyrequests: Too Many Requests"
  diagnosis: "Anonymous Docker Hub pull rate limit exceeded"
  resolution: "Use ECR pull-through cache for Docker Hub images, or authenticate with Docker Hub credentials via Secrets Manager"
