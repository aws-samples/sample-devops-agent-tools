# DevOps Agent alarm investigations

Forwards **one** Amazon CloudWatch alarm to an AWS DevOps Agent generic webhook so that
alarm opens an investigation.

**One stack = one alarm.** Deploy it again for each alarm you want forwarded.

There is no AWS Lambda function and no code: an Amazon EventBridge rule matches the
alarm, an input transformer builds the webhook payload, and an EventBridge API
destination posts it with the webhook's API key.

## Deployment sequence

The webhook is created manually in the console (there is no `CreateWebhook` API), and
its API key is shown only once at creation — so it must exist **before** this stack.

```
1. Create the Agent Space (console / CLI / CloudFormation).
2. Console → Capabilities → Agent Space Webhook → Generate webhook.   ← manual; no API
   For "Webhook authentication type" choose API key.
   Copy the webhook URL and the API key. The key is not retrievable later.
3. Deploy this stack with the webhook URL, the API key, and the alarm ARN.
   Deploy once per alarm.
```

If you lose the key, rotate the webhook from the Capabilities tab. Rotation keeps the
same URL and issues a new key; update this stack with the new value afterwards.

## What it creates

| Resource | Purpose |
|----------|---------|
| Amazon EventBridge rule | Matches `CloudWatch Alarm State Change` events with `state.value = ALARM` **for the one configured alarm ARN** — the rule itself is the filter. Its input transformer builds the incident payload. |
| Amazon EventBridge connection | Holds the API key. EventBridge stores it in a Secrets Manager secret it creates and owns, and adds `Authorization: Bearer <key>` to every request. |
| Amazon EventBridge API destination | The webhook endpoint, invoked at a capped rate. |
| AWS IAM role | Lets the rule invoke that one API destination (`events:InvokeApiDestination`). Nothing else. |

## How it works

```
CloudWatch alarm ──ALARM──▶ EventBridge rule (this alarm ARN only)
                                    │ input transformer builds the incident JSON
                                    ▼
                            API destination + connection
                                    │ POST, Authorization: Bearer <api key>
                                    ▼
                    DevOps Agent generic webhook ──▶ investigation
```

The payload the transformer produces:

```json
{
  "eventType": "incident",
  "incidentId": "<EventBridge event id>",
  "action": "created",
  "priority": "HIGH",
  "title": "CloudWatch alarm in ALARM state",
  "description": "CloudWatch alarm <alarm ARN> entered ALARM state.",
  "timestamp": "<EventBridge event time>",
  "data": { "metadata": { "alarmArn": "<alarm ARN>", "state": "ALARM" } }
}
```

Only the alarm ARN and the raised state are forwarded — no alarm name, reason, or metric
data. DevOps Agent enriches from the ARN.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `WebhookUrl` | *(required)* | HTTPS URL of the generic webhook. |
| `WebhookApiKey` | *(required)* | The API key (bearer token) from webhook creation. `NoEcho`, so it is masked in stack events, the console, and `describe-stacks`. |
| `AgentName` | *(required)* | Name/label of the target DevOps Agent. Used only to tag the taggable resources (`DevOpsAgent=<name>`) for identification and cost allocation; it does not affect routing. |
| `AlarmArn` | *(required)* | ARN of the single CloudWatch alarm to forward. |
| `InvocationRateLimitPerSecond` | `1` | Cap on webhook invocations per second. Raise only if one stack must absorb a burst. |

## Deploy

```bash
aws cloudformation deploy \
  --template-file cloudformation/devops-agent-alarm-investigations/devops-agent-alarm-investigations.yaml \
  --stack-name devops-agent-alarm-investigations-<alarm-name> \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      WebhookUrl="https://<your-webhook-url>" \
      WebhookApiKey="<your-webhook-api-key>" \
      AgentName="<your-agent-name>" \
      AlarmArn="arn:aws:cloudwatch:<region>:<account>:alarm:<alarm-name>"
```

## Cross-region and cross-account alarms

Deploy this stack **once, in the same account and Region as the DevOps Agent**. Alarms in
*other* Regions or accounts reach it by **forwarding their state-change events** to the
agent Region's event bus; the rule matches on the alarm ARN, so a forwarded remote event
is handled exactly like a local one. Set `AlarmArn` to the alarm's real (possibly remote)
ARN.

You create the forwarding rule in the alarm's own Region/account — ordinary EventBridge
bus-to-bus delivery:

1. In the **alarm's Region/account**, create an EventBridge rule on the default bus
   matching the alarm, targeting the **agent account/Region's default event bus**, with
   an IAM role that grants `events:PutEvents` to that bus:
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
   (`events:PutPermission`) allowing the source account to `PutEvents` — a bus accepts
   events from another account only if its policy grants it. (Same-account, cross-Region
   needs only the put-events role above.)

The forwarded event lands on the agent Region's default bus still carrying
`resources: ["<alarm ARN>"]`, so this stack's rule matches it.

## Notes

- **Region availability.** EventBridge API destinations to public HTTPS endpoints are not
  available in every AWS Region. Check
  [API destinations as targets](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-api-destinations.html#eb-api-destination-regions)
  before deploying into a less common Region.
- **Deduplication.** `incidentId` is the EventBridge event id, which is constant across
  retries, so redeliveries of the same event reuse the same `incidentId` and DevOps Agent
  correlates them instead of opening duplicates. Repeat/flapping alarms are also
  correlated natively; control flapping at the alarm's datapoints-to-alarm setting.
- **Delivery retries and stale events.** The retry policy allows up to 32 attempts over
  8 hours, and `MaximumEventAgeInSeconds` means EventBridge stops trying once an event is
  older than that — so a long-delayed redelivery cannot open a stale investigation.
- **Endpoint timeout.** API destinations require the endpoint to respond within 5 seconds.
  EventBridge retries timeouts within the retry policy above.
- **Monitoring delivery failures.** There is no function log group to read. The rule
  publishes `InvocationAttempts`, `SuccessfulInvocationAttempts`,
  `RetryInvocationAttempts` and `FailedInvocations` in the `AWS/Events` namespace,
  dimensioned by `RuleName` — alarm on `FailedInvocations`, and watch
  `RetryInvocationAttempts` for an endpoint that is struggling but still succeeding. Add
  a dead-letter queue to the target if you need to inspect events that never landed.
- **Rotating the API key.** The connection resolves its key when the stack is created or
  when the connection resource itself changes. After rotating the webhook, update the
  stack with the new `WebhookApiKey` value; the key is not re-read automatically.
- **Keeping the key in your own secret.** If you already store the key in Secrets Manager,
  replace the `ApiKeyValue` line in the template with a dynamic reference so the key never
  passes through a stack parameter:
  ```yaml
  ApiKeyValue: '{{resolve:secretsmanager:MyWebhookSecret}}'
  ```
  Substitute your secret's name (or full ARN for a cross-account secret), and drop the
  `WebhookApiKey` parameter. The same rotation caveat applies: CloudFormation re-resolves
  the reference only when the resource is updated.
- **API key versus HMAC.** The DevOps Agent generic webhook also supports HMAC
  authentication, which adds payload integrity and replay protection. EventBridge
  connections support only Basic, API key, and OAuth, and cannot compute a per-request
  signature, so HMAC would require a signing Lambda between the rule and the webhook.
  This template takes the API key path to stay code-free; over HTTPS to an AWS endpoint
  the bearer token is the simpler trade.
