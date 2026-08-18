# Remediation Playbooks — RDS/Aurora Security

CLI templates for manual execution by an operator. None of these commands are run by the skill itself — it produces recommendations only.

## P1 — Enforce SSL/TLS (Requires Parameter Group Change + Reboot)

```bash
# PostgreSQL — force SSL
aws rds modify-db-cluster-parameter-group \
  --db-cluster-parameter-group-name {{PG_NAME}} \
  --parameters "ParameterName=rds.force_ssl,ParameterValue=1,ApplyMethod=pending-reboot"

# MySQL — require secure transport
aws rds modify-db-cluster-parameter-group \
  --db-cluster-parameter-group-name {{PG_NAME}} \
  --parameters "ParameterName=require_secure_transport,ParameterValue=ON,ApplyMethod=pending-reboot"

# Reboot to apply
aws rds reboot-db-instance --db-instance-identifier {{INSTANCE_ID}}

Impact: All cleartext connections rejected after reboot. Applications must use SSL. Requires a reboot — plan for a brief connection interruption.
P1 — Remove Public Access
aws rds modify-db-instance \
  --db-instance-identifier {{INSTANCE_ID}} \
  --no-publicly-accessible \
  --apply-immediately

Impact: Instance only accessible from within the VPC. Confirm application connectivity paths (VPN, peering, VPC endpoints) exist before applying, or connections will break.
P1 — Restrict Security Group
# Remove 0.0.0.0/0 rule
aws ec2 revoke-security-group-ingress \
  --group-id {{SG_ID}} \
  --protocol tcp \
  --port {{DB_PORT}} \
  --cidr 0.0.0.0/0

# Add specific CIDR
aws ec2 authorize-security-group-ingress \
  --group-id {{SG_ID}} \
  --protocol tcp \
  --port {{DB_PORT}} \
  --cidr {{APP_CIDR}}/32

Impact: Only specified CIDRs can connect. No downtime, but any client outside the new CIDR range loses access immediately.
P2 — Enable IAM Authentication
aws rds modify-db-instance \
  --db-instance-identifier {{INSTANCE_ID}} \
  --enable-iam-database-authentication \
  --apply-immediately

Impact: IAM-based token authentication becomes available alongside password auth. No existing connections are affected.
P2 — Enable Secrets Manager Rotation

Prerequisite: rotate-secret with --rotation-rules alone does not configure rotation on a secret that has never had it enabled — it only sets the schedule. The secret must already have a rotation Lambda associated (either RDS-managed rotation configured via the console/enable-rotation with a RotationLambdaARN, or a custom rotation function). If no rotation function is configured, this command will fail or silently do nothing on the next scheduled rotation.
# One-time setup (if rotation has never been enabled on this secret):
# use the RDS console "Configure automatic rotation" flow, or:
aws secretsmanager rotate-secret \
  --secret-id {{SECRET_ID}} \
  --rotation-lambda-arn {{ROTATION_LAMBDA_ARN}} \
  --rotation-rules "{\"AutomaticallyAfterDays\": 30}"

# If rotation is already configured and you only need to change the schedule:
aws secretsmanager rotate-secret \
  --secret-id {{SECRET_ID}} \
  --rotation-rules "{\"AutomaticallyAfterDays\": 30}"

Impact: Credentials rotate automatically every 30 days once rotation is fully configured.
P2 — Enable Deletion Protection
aws rds modify-db-instance \
  --db-instance-identifier {{INSTANCE_ID}} \
  --deletion-protection \
  --apply-immediately

Impact: Cannot delete the instance without first explicitly removing protection. No downtime.
P2 — Enable CloudWatch Log Exports
# Aurora PostgreSQL
aws rds modify-db-cluster \
  --db-cluster-identifier {{CLUSTER_ID}} \
  --cloudwatch-logs-export-configuration "{\"EnableLogTypes\":[\"postgresql\",\"upgrade\"]}" \
  --apply-immediately

# Aurora MySQL
aws rds modify-db-cluster \
  --db-cluster-identifier {{CLUSTER_ID}} \
  --cloudwatch-logs-export-configuration "{\"EnableLogTypes\":[\"audit\",\"error\",\"slowquery\"]}" \
  --apply-immediately

Impact: Logs exported to CloudWatch for centralized analysis and retention. No downtime.
P3 — Enable Activity Streams (Aurora)
aws rds start-activity-stream \
  --resource-arn {{CLUSTER_ARN}} \
  --mode async \
  --kms-key-id {{CMK_ARN}} \
  --apply-immediately

Impact: Near-real-time audit stream to Kinesis for SIEM integration. No downtime; adds a small amount of overhead to the database engine.
P3 — Enable KMS Key Rotation
aws kms enable-key-rotation --key-id {{KEY_ID}}

Impact: KMS automatically rotates key material annually. No downtime, no application changes required.
Report Output Format
# RDS/Aurora Security Posture Assessment Report
**Account:** {{ACCOUNT_ID}} | **Region:** {{REGION}} | **Date:** {{DATE}}

## Overall Score: {{SCORE}}/100 ({{RATING}})

## Infrastructure Inventory
|
 Resource 
|
 Engine 
|
 Encrypted 
|
 Public 
|
 IAM Auth 
|
 Logs 
|
 Deletion Protection 
|

|
----------
|
--------
|
-----------
|
--------
|
----------
|
------
|
---------------------
|


## Security Gaps Detected
|
 Severity 
|
 Gap ID 
|
 Resource 
|
 Description 
|
 Risk 
|

|
----------
|
--------
|
----------
|
-------------
|
------
|


## Critical Findings (Immediate Action Required)
### Public Exposure
### Unencrypted Data
### Missing Audit Trail

## Remediation Plan
### P1 — Immediate (24 hours)
- Remove public access
- Restrict security groups
- Enforce SSL/TLS

### P2 — This Week
- Enable IAM authentication
- Configure Secrets Manager rotation
- Enable deletion protection
- Export logs to CloudWatch

### P3 — 30 Days
- Enable Activity Streams
- Implement tag-based access control
- Configure AWS Config rules
- Enable KMS key rotation

## Compliance Summary
|
 Framework 
|
 Status 
|
 Gaps 
|

|
-----------
|
--------
|
------
|

|
 PCI-DSS 
|
 {{STATUS}} 
|
 {{GAPS}} 
|

|
 HIPAA 
|
 {{STATUS}} 
|
 {{GAPS}} 
|

|
 SOC2 
|
 {{STATUS}} 
|
 {{GAPS}} 
|

