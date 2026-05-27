# AWS Health API Reference

Quick reference for the AWS Health API operations used by this skill.

## DescribeEvents

Returns a list of Health events matching specified filter criteria.

### Key Parameters

| Parameter | Type | Description | Constraints |
|-----------|------|-------------|-------------|
| `filter.services` | List of strings | AWS service codes to filter by (e.g., `EC2`, `RDS`) | Max 10 values |
| `filter.regions` | List of strings | AWS regions to filter by | Max 10 values |
| `filter.availabilityZones` | List of strings | Availability zones to filter by | Max 10 values |
| `filter.startTimes` | List of DateTimeRange | Filter by event start time (from/to) | ISO 8601 timestamps |
| `filter.endTimes` | List of DateTimeRange | Filter by event end time (from/to) | ISO 8601 timestamps |
| `filter.lastUpdatedTimes` | List of DateTimeRange | Filter by last updated time | ISO 8601 timestamps |
| `filter.eventTypeCategories` | List of strings | Event type categories to filter by | `issue`, `scheduledChange`, `accountNotification` |
| `filter.eventStatusCodes` | List of strings | Event statuses to filter by | `open`, `closed`, `upcoming` |
| `filter.eventTypeCodes` | List of strings | Specific event type codes | e.g., `AWS_EC2_OPERATIONAL_ISSUE` |
| `filter.entityValues` | List of strings | Resource ARNs or IDs to filter by | Max 100 values |
| `filter.eventArns` | List of strings | Specific event ARNs to retrieve | Max 10 values |
| `maxResults` | Integer | Max events per page | 10–100 (default 10) |
| `nextToken` | String | Pagination token | From previous response |

### Response Fields

| Field | Description |
|-------|-------------|
| `events[].arn` | Unique event identifier (ARN) |
| `events[].service` | AWS service namespace (e.g., `EC2`, `RDS`) |
| `events[].eventTypeCode` | Specific event type (e.g., `AWS_EC2_OPERATIONAL_ISSUE`) |
| `events[].eventTypeCategory` | Category: `issue`, `scheduledChange`, or `accountNotification` |
| `events[].region` | AWS region where the event occurred |
| `events[].availabilityZone` | Specific AZ if applicable |
| `events[].startTime` | When the event started (ISO 8601) |
| `events[].endTime` | When the event ended (ISO 8601); null if ongoing |
| `events[].lastUpdatedTime` | Last update timestamp (ISO 8601) |
| `events[].statusCode` | Current status: `open`, `closed`, or `upcoming` |
| `events[].eventScopeCode` | Scope: `ACCOUNT_SPECIFIC` or `PUBLIC` |
| `nextToken` | Pagination token for next page (null if no more results) |

---

## DescribeEventDetails

Returns detailed information for one or more Health events, including full descriptions.

### Key Parameters

| Parameter | Type | Description | Constraints |
|-----------|------|-------------|-------------|
| `eventArns` | List of strings (required) | Event ARNs to retrieve details for | **Max 10 per request** |
| `locale` | String | Language for event descriptions | e.g., `en` (default) |

### Response Fields

| Field | Description |
|-------|-------------|
| `successfulSet[].event` | The event object (same fields as DescribeEvents response) |
| `successfulSet[].eventDescription.latestDescription` | Full text description of the event |
| `successfulSet[].eventMetadata` | Additional metadata key-value pairs |
| `failedSet[].eventArn` | ARN of the event that failed to retrieve |
| `failedSet[].errorName` | Error code for the failure |
| `failedSet[].errorMessage` | Human-readable error message |

---

## DescribeAffectedEntities

Returns a list of resources (entities) affected by a specific Health event.

### Key Parameters

| Parameter | Type | Description | Constraints |
|-----------|------|-------------|-------------|
| `filter.eventArns` | List of strings (required) | Event ARNs to get affected entities for | Max 10 values |
| `filter.entityValues` | List of strings | Filter by specific resource ARNs or IDs | Max 100 values |
| `filter.entityArns` | List of strings | Filter by entity ARNs | Max 100 values |
| `filter.lastUpdatedTimes` | List of DateTimeRange | Filter by entity last updated time | ISO 8601 timestamps |
| `filter.statusCodes` | List of strings | Filter by entity status | `IMPAIRED`, `UNIMPAIRED`, `UNKNOWN`, `PENDING` |
| `maxResults` | Integer | Max entities per page | 10–100 (default 10) |
| `nextToken` | String | Pagination token | From previous response |

### Response Fields

| Field | Description |
|-------|-------------|
| `entities[].entityValue` | Resource ARN or ID |
| `entities[].eventArn` | Associated event ARN |
| `entities[].awsAccountId` | Account owning the resource |
| `entities[].lastUpdatedTime` | Last status update (ISO 8601) |
| `entities[].statusCode` | Entity status: `IMPAIRED`, `UNIMPAIRED`, `UNKNOWN`, or `PENDING` |
| `entities[].tags` | Resource tags (key-value map) |
| `nextToken` | Pagination token for next page (null if no more results) |

---

## Common Service Codes

| Service | Health API Code |
|---------|----------------|
| EC2 | `EC2` |
| RDS | `RDS` |
| Lambda | `LAMBDA` |
| ELB | `ELASTICLOADBALANCING` |
| S3 | `S3` |
| CloudFront | `CLOUDFRONT` |
| DynamoDB | `DYNAMODB` |
| ECS | `ECS` |
| EKS | `EKS` |
| VPC | `VPC` |
| Route 53 | `ROUTE53` |
| IAM | `IAM` |
| CloudWatch | `CLOUDWATCH` |
| SNS | `SNS` |
| SQS | `SQS` |
| EBS | `EBS` |
| ElastiCache | `ELASTICACHE` |
| Kinesis | `KINESIS` |
| API Gateway | `APIGATEWAY` |
| SageMaker | `SAGEMAKER` |

---

## Event Type Categories

| Category | Description |
|----------|-------------|
| `issue` | An AWS service issue or outage affecting resources |
| `scheduledChange` | Planned maintenance or infrastructure change |
| `accountNotification` | Account-specific notification (e.g., certificate expiry, abuse report) |

---

## Event Statuses

| Status | Description |
|--------|-------------|
| `open` | Event is currently active and ongoing |
| `closed` | Event has been resolved |
| `upcoming` | Scheduled event that has not yet started |

---

## Entity Status Codes

| Status | Description |
|--------|-------------|
| `IMPAIRED` | Resource is confirmed impaired by the event |
| `UNIMPAIRED` | Resource is confirmed not impaired |
| `UNKNOWN` | Impact status cannot be determined |
| `PENDING` | Impact assessment is in progress |

---

## Important Constraints

- **API endpoint**: The AWS Health API is available only in the `us-east-1`
  region. All API calls must target this region regardless of where the affected
  resources are located.
- **Batch size**: `DescribeEventDetails` accepts a maximum of **10 event ARNs**
  per request. For more than 10 events, issue multiple batched calls.
- **Pagination**: All list operations are paginated. Use `nextToken` from the
  response to retrieve subsequent pages until `nextToken` is null.
- **Rate limiting**: Standard AWS API rate limits apply. Use exponential backoff
  for retries on throttling errors (HTTP 429).
- **Event scope**: Use `eventScopeCode` to distinguish between account-specific
  events (`ACCOUNT_SPECIFIC`) and public service events (`PUBLIC`).
- **Data availability**: Health events are available for up to 90 days. Events
  older than 90 days cannot be retrieved via the API.
- **IAM permissions required**: `health:DescribeEvents`,
  `health:DescribeEventDetails`, `health:DescribeAffectedEntities`,
  `health:DescribeEventTypes`.
- **Affected entities**: Only events with `eventScopeCode=ACCOUNT_SPECIFIC` have
  affected entities. Public events do not return entity data.
