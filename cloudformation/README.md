# DevOps Agent CloudFormation Templates

This folder holds the CloudFormation automation for AWS DevOps Agent. There are two
independent templates — deploy either, both, or neither:

| Template | Category | Creates infrastructure? | Purpose |
|----------|----------|-------------------------|---------|
| [`devops-agent-skill-policies.yaml`](devops-agent-skill-policies.yaml) | IAM policies | No | Adds least-privilege IAM policies to a DevOps Agent role, per skill. |
| [`devops-agent-alarm-investigations.yaml`](devops-agent-alarm-investigations.yaml) | Automation addon | Yes | Forwards a single Amazon CloudWatch alarm to a DevOps Agent webhook so it opens an investigation. |

They are kept separate on purpose: the skill-policies template only attaches IAM to
the agent role and creates no resources, while the alarm-investigations addon creates
real infrastructure (EventBridge, Lambda) and does not touch the agent role.

---

## 1. `devops-agent-skill-policies.yaml` — skill IAM policies

Adds the extra IAM permissions individual skills need, on top of the AWS managed
policy `AIDevOpsAgentAccessPolicy`. Attach them to an existing DevOps Agent role, or
let the template create a new role.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ExistingRoleName` | `''` | Attach policies to this existing role. Empty creates a new role (`DevOpsAgentRole-AgentSpace`). |
| `AllowedRegions` | `''` | Optional. Restrict the agent to these Regions. Empty means all Regions. |
| `EnableAwsHealthEvents` | `true` | `health:DescribeEventTypes`. |
| `EnableSupportCases` | `true` | `support:DescribeCommunications`. |
| `EnableRdsOperationReview` | `true` | `rds:DownloadDBLogFilePortion`, `logs:GetLogEvents`. |
| `EnableEksOperationReview` | `true` | Covered by the managed policy. |
| `EnableInvestigationCostGuardrail` | `true` | `pricing:GetProducts`. |
| `EnableEnrichWithSecurityAgent` | `true` | Covered by the managed policy. |
| `EnableCrmInvestigationGuidelines` | `true` | Covered by the managed policy. |
| `EnableSkipScheduledMaintenance` | `true` | No IAM required. |

> **Multiple Agent Spaces:** the role this template creates trusts all Agent Spaces
> in the account (`agentspace/*`), so one role can serve several spaces. Because the
> new-role name is fixed, the create-new path can only run once per account/Region;
> to give different spaces different permission sets, pre-create the roles and deploy
> once per role with `ExistingRoleName`.

### Deploy

```bash
aws cloudformation deploy \
  --template-file cloudformation/devops-agent-skill-policies.yaml \
  --stack-name devops-agent-skill-policies \
  --parameter-overrides ExistingRoleName=<YOUR-DEVOPS-AGENT-ROLE-NAME> \
  --capabilities CAPABILITY_NAMED_IAM
```

---

## 2. `devops-agent-alarm-investigations.yaml` — alarm investigations addon

Forwards **one** Amazon CloudWatch alarm to an AWS DevOps Agent generic (HMAC)
webhook so that alarm opens an investigation. **One stack = one alarm.** Deploy it
again for each alarm you want forwarded (the same secret ARN can be reused across
stacks).

### Deployment sequence

The webhook is created manually in the console (there is no `CreateWebhook` API), and
its HMAC key is never returned by any API — so it must be created **before** this
stack. The key is supplied via a Secrets Manager secret you own (by ARN); it never
passes through CloudFormation.

```
1. Create the Agent Space (console / CLI / CloudFormation).
2. Console → Capabilities → Add a generic (HMAC) webhook.   ← manual; no API
   Copy the webhook URL and the HMAC signing key.
3. Store the HMAC key in an AWS Secrets Manager secret (any name).
4. Deploy this stack, passing the webhook URL, the secret ARN, and the alarm ARN.
   Deploy once per alarm; reuse the same secret ARN across stacks if you like.
```

### What it creates

| Resource | Purpose |
|----------|---------|
| Amazon EventBridge rule | Matches `CloudWatch Alarm State Change` events with `state.value = ALARM` **for the one configured alarm ARN** — the rule itself is the filter. |
| AWS Lambda function (inline Python) | HMAC-signs the payload and POSTs it to the webhook. Runs at reserved concurrency 1. |
| AWS Lambda execution role | Least-privilege: write its own logs + read the one secret. Nothing else. |
| Amazon CloudWatch log group | Pre-created (30-day retention) so the role can scope logging to it. |

### How it works

```
CloudWatch alarm ──ALARM──▶ EventBridge rule (this alarm ARN only) ──▶ Lambda
                                                                        │ HMAC-SHA256 sign → POST
                                                                        ▼
                                              DevOps Agent generic (HMAC) webhook ──▶ investigation
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `WebhookUrl` | *(required)* | HTTPS URL of the generic (HMAC) webhook. Must be a valid AWS DevOps Agent webhook. |
| `WebhookSecretArn` | *(required)* | ARN of a Secrets Manager secret holding the webhook HMAC key (`SecretString` or UTF-8 `SecretBinary`). You create/own it; it can be shared across stacks. |
| `AgentName` | *(required)* | Name/label of the target DevOps Agent. Used only to tag the created resources (`DevOpsAgent=<name>`) for identification/cost allocation; does not affect routing. |
| `AlarmArn` | *(required)* | ARN of the single CloudWatch alarm to forward. |
| `LogKmsKeyArn` | `''` | Optional customer-managed KMS key ARN to encrypt the Lambda's log group. Empty = AWS-managed key. |
| `ReservedConcurrency` | `1` | Reserved concurrency for the signing Lambda (serializes forwarding). Leave **empty** to use the account's unreserved pool — do that if a stack create fails because no reserved concurrency can be allocated. |

### Deploy

```bash
aws cloudformation deploy \
  --template-file cloudformation/devops-agent-alarm-investigations.yaml \
  --stack-name devops-agent-alarm-investigations-<alarm-name> \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      WebhookUrl="https://<your-webhook-url>" \
      WebhookSecretArn="arn:aws:secretsmanager:<region>:<account>:secret:<name>" \
      AlarmArn="arn:aws:cloudwatch:<region>:<account>:alarm:<alarm-name>"
```

### Cross-region and cross-account alarms

Deploy this stack **once, in the same account and Region as the DevOps Agent** (where
the webhook and its secret live). The Lambda and the HMAC secret never leave that
account/Region — so there is no cross-account secret sharing to set up. Alarms in
*other* Regions or accounts reach it by **forwarding their state-change events** to
the agent Region's event bus; the stack matches on the alarm ARN, so a forwarded
remote event is handled exactly like a local one. Set `AlarmArn` to the alarm's real
(possibly remote) ARN.

You create the forwarding rule in the alarm's own Region/account — it is ordinary
EventBridge bus-to-bus delivery:

1. In the **alarm's Region/account**, create an EventBridge rule on the default bus
   matching the alarm, targeting the **agent account/Region's default event bus**,
   with an IAM role that grants `events:PutEvents` to that bus:
   ```yaml
   ForwardToAgentBus:
     Type: AWS::Events::Rule
     Properties:
       EventPattern:
         source: [aws.cloudwatch]
         detail-type: [CloudWatch Alarm State Change]
         detail: { state: { value: [ALARM] } }
       Targets:
         - Id: AgentBus
           Arn: arn:aws:events:<agent-region>:<agent-account>:event-bus/default
           RoleArn: !GetAtt ForwardRole.Arn   # role with events:PutEvents on that bus
   ```
2. **Cross-account only:** also add a resource policy on the agent bus
   (`events:PutPermission`) allowing the source account to `PutEvents` — a bus only
   accepts events from another account if its policy grants it. (Same-account,
   cross-Region needs only the put-events role above.)

The forwarded event lands on the agent Region's default bus still carrying
`resources: ["<alarm ARN>"]`, so this stack's rule matches it and forwards to the
webhook — no change to this stack.

### Notes

- **Delivery retries.** EventBridge retries the Lambda for up to **32 attempts over
  8 hours**. Add a CloudWatch alarm on the Lambda's `Errors` metric to be notified of
  delivery failures.
- **Stale-event cutoff.** Events whose alarm state-change timestamp is older than
  8 hours (matching the retry window) are skipped, so a delayed redelivery does not
  open a stale investigation.
- **Log group is deleted with the stack**, so redeploying the same stack name works
  cleanly. The group name is fixed (so the execution role can be scoped to it);
  switch the log group's `DeletionPolicy`/`UpdateReplacePolicy` to `Retain` if you
  need the forwarding record to survive stack deletion — then delete the retained
  group before redeploying the same stack name.
- **Deduplication.** `incidentId` is derived from the EventBridge event id (stable
  across the 8h / 32-attempt retry window), so retries of the same event reuse the
  same `incidentId` and DevOps Agent correlates them instead of opening duplicates.
  Repeat/flapping alarms are also correlated natively; control flapping at the alarm's
  datapoints-to-alarm setting.
- **Event authenticity.** The function is given the configured alarm ARN and drops
  (and logs) any event whose `resources[0]` is missing or does not equal it, so a
  stray or forged invocation cannot open an investigation for a different alarm. On
  the cross-account path above, an account you allow to `PutEvents` can still send a
  correctly-formed event carrying *your* alarm ARN — granting `PutEvents` is a trust
  decision.
- **Reserved concurrency.** Defaults to 1 (serialized). Set `ReservedConcurrency`
  empty to use the account's unreserved pool if a stack create fails because no
  reserved concurrency can be allocated.
- **Customer-managed KMS keys.** Set `LogKmsKeyArn` to encrypt the log group with a
  CMK (the key policy must allow the CloudWatch Logs service). Separately, if your
  webhook **secret** uses a CMK, add `kms:Decrypt` to the execution role or the
  Lambda will fail with `AccessDenied`.
