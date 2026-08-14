---
title: "K7 — EBS/EFS Volume Mount Failures"
description: "Diagnose and remediate ECS task failures related to EBS volume attachment or EFS file system mounting"
status: active
severity: HIGH
triggers:
  - "CannotCreateVolumeError"
  - "volume mount"
  - "EFS"
  - "EBS"
  - "mount failed"
  - "file system"
  - "access point"
  - "transit encryption"
owner: devops-agent
objective: "Identify volume mount failures and restore persistent storage access for ECS tasks"
context: "ECS tasks can use EBS volumes (configuredAtLaunch), EFS file systems, bind mounts, and Docker volumes. Failures include IAM permission issues, security group blocking NFS traffic, EFS access point misconfiguration, and EBS attachment limits."
---

## Phase 1 — Triage

MUST:
- Use `collect` tool with instanceId to gather logs from the affected container instance
- Use `status` tool with executionId to poll until collection completes
- Use `errors` tool with instanceId and severity=high to find volume-related errors
- Use `search` tool with instanceId and query=`CannotCreateVolume|volume.*mount|EFS|EBS|mount.*fail|file.*system|access.*point` to find volume failure evidence

SHOULD:
- Use `search` tool with query=`nfs|port 2049|security group|transit.*encryption|authorization` to check EFS connectivity
- Use `search` tool with query=`configuredAtLaunch|infrastructure.*role|ebs.*attach|volume.*type` to check EBS configuration

MAY:
- Use `network_diagnostics` tool with instanceId to check NFS port connectivity
- Use `search` tool with query=`disk.*space|inode|throughput|burst.*credit` to check EFS performance issues

## Phase 2 — Enrich

MUST:
- Use `correlate` tool with instanceId to build timeline of volume mount failures
- Determine if the issue is EBS attachment, EFS mount, permissions, or network
- Use `validate` tool with instanceId to confirm log bundle completeness

SHOULD:
- Use `search` tool with query=`iam.*role|elasticfilesystem|ebs:CreateVolume|ebs:AttachVolume` to check IAM permissions
- Use `search` tool with query=`subnet|availability.*zone|mount.*target` to check EFS mount target availability

## Phase 3 — Report

MUST:
- Use `summarize` tool with instanceId and finding_ids from the volume failure findings
- State root cause: IAM, security group, mount target, or configuration issue
- Recommend specific remediation

SHOULD:
- Include storage architecture showing volume configuration
- Recommend security group rules for NFS (port 2049) if EFS-related

## Guardrails

escalation_conditions:
  - "All tasks failing to mount shared EFS volume"
  - "EBS volume attachment limit reached on instance"
  - "Data loss risk from volume configuration changes"

safety_ratings:
  - "Log collection, search, errors, correlate: GREEN (read-only)"
  - "Security group rule changes: YELLOW — operator action"
  - "EFS/EBS configuration changes: RED — requires approval"

## Common Issues

- symptoms: "CannotCreateVolumeError: failed to create EBS volume"
  diagnosis: "ECS infrastructure IAM role lacks ebs:CreateVolume permission or volume quota exceeded"
  resolution: "Add EBS permissions to infrastructure role, check EBS volume limits in the region"

- symptoms: "ResourceInitializationError: failed to invoke EFS utils commands"
  diagnosis: "EFS mount target not available in the task's AZ or security group blocking port 2049"
  resolution: "Create EFS mount target in the task's subnet AZ, allow inbound TCP 2049 from task security group"

- symptoms: "EFS mount timeout"
  diagnosis: "Security group or network ACL blocking NFS traffic between task ENI and EFS mount target"
  resolution: "Allow TCP port 2049 inbound on EFS security group from task security group"

- symptoms: "EFS access denied"
  diagnosis: "EFS access point IAM authorization failing or task role missing elasticfilesystem:ClientMount"
  resolution: "Add elasticfilesystem:ClientMount and ClientWrite to task role, verify access point configuration"
