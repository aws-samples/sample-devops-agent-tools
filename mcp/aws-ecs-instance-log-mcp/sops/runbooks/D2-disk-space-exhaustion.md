---
title: "D2 — Disk Space Exhaustion"
description: "Diagnose disk full errors on ECS container instances"
status: active
severity: CRITICAL
triggers:
  - "no.*space.*left.*device"
  - "disk.*full"
owner: devops-agent
objective: "Identify disk space consumers and restore available space"
context: "Disk exhaustion on ECS instances prevents new container creation, image pulls, and log writing. Common causes: accumulated container images, large log files, or container writable layers."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs
- Use `status` tool with executionId to poll until complete
- Use `errors` tool with instanceId and severity=critical
- Use `search` tool with instanceId and query=`no space left|disk full|disk.*pressure` to find disk errors

SHOULD:
- Use `search` tool with query=`docker.*prune|image.*size|overlay.*size` to check image storage usage

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to determine when disk filled up
- Use `validate` tool to check if disk-related logs are present

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids
- Recommend: docker system prune, increase EBS volume, or configure image cleanup

## Common Issues

- symptoms: "no space left on device during image pull"
  diagnosis: "Docker storage driver partition full from accumulated images"
  resolution: "Operator: run docker system prune, or enable ECS image cleanup (ECS_IMAGE_CLEANUP_INTERVAL)"
