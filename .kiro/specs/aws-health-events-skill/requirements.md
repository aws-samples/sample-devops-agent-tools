# Requirements Document

## Introduction

The AWS Health Events skill enables the AWS DevOps Agent to retrieve and analyze AWS Health events during incident investigation, root cause analysis, and operational troubleshooting. AWS Health events provide visibility into service disruptions, scheduled maintenance, and account-specific notifications that may explain or correlate with observed operational issues. This skill should always be activated at the start of any incident investigation alongside the support-cases skill, and also supports standalone chat-based health event reporting.

## Glossary

- **Health_Events_Skill**: The AWS DevOps Agent skill that retrieves and analyzes AWS Health events
- **AWS_Health_API**: The AWS Health API service providing event data via DescribeEvents, DescribeEventDetails, and DescribeAffectedEntities operations
- **Health_Event**: A notification from AWS Health representing a service issue, scheduled change, or account notification
- **Event_Type_Category**: The classification of a Health event as one of: issue, scheduledChange, or accountNotification
- **Event_Status**: The lifecycle state of a Health event: open, closed, or upcoming
- **Affected_Entity**: An AWS resource (identified by ARN or resource ID) impacted by a Health event
- **Event_Scope**: Whether an event is account-specific (ACCOUNT_SPECIFIC) or publicly visible across all accounts (PUBLIC)
- **Agent**: The AWS DevOps Agent that executes this skill
- **Incident_Context**: The set of details describing the current operational issue including affected services, timeframe, error patterns, and impacted resources
- **Health_Report**: A structured summary of AWS Health events over a specified time period, organized by service, category, and impact

## Requirements

### Requirement 1: Skill Activation for Incident Investigation

**User Story:** As an operator investigating an incident, I want the Health Events skill to automatically activate at the start of any investigation, so that AWS-side service events are immediately surfaced as potential root causes.

#### Acceptance Criteria

1. WHEN an incident investigation, root cause analysis, or operational troubleshooting session begins, THE Health_Events_Skill SHALL activate and retrieve AWS Health events from the past 7 days filtered by the AWS service codes and region associated with the affected resources
2. WHEN the Agent observes service degradation, elevated error rates, latency spikes, connection failures, throttling, capacity issues, deployment-related failures, alarms, or any operational event, THE Health_Events_Skill SHALL retrieve Health events from the past 7 days filtered by the affected AWS services and region
3. THE Health_Events_Skill SHALL retrieve both account-specific events (via AWS Health API `DescribeEvents` with `eventScopeCode=ACCOUNT_SPECIFIC`) and public service events (with `eventScopeCode=PUBLIC`) filtered by the AWS service codes and region of the affected resources
4. IF the Health_Events_Skill retrieves zero matching AWS Health events, THEN THE Health_Events_Skill SHALL report that no active or recent AWS Health events were found for the affected services and continue the investigation without blocking
5. IF the AWS Health API call fails or times out within 30 seconds, THEN THE Health_Events_Skill SHALL report that Health event data is unavailable and continue the investigation using other available data sources

### Requirement 2: Event Retrieval and Filtering

**User Story:** As an operator, I want to retrieve AWS Health events filtered by service, time range, event type, and status, so that I can quickly find events relevant to my investigation.

#### Acceptance Criteria

1. THE Health_Events_Skill SHALL use the `health:DescribeEvents` API to retrieve Health events matching specified filter criteria
2. WHEN filtering by time range, THE Health_Events_Skill SHALL accept ISO 8601 timestamps for start and end times and pass them as `startTime` and `endTime` filter parameters
3. WHEN filtering by AWS service, THE Health_Events_Skill SHALL filter events using the `services` parameter with valid AWS service codes
4. WHEN filtering by event type category, THE Health_Events_Skill SHALL accept one or more of: issue, scheduledChange, accountNotification
5. WHEN filtering by event status, THE Health_Events_Skill SHALL accept one or more of: open, closed, upcoming
6. WHEN no explicit time range is provided, THE Health_Events_Skill SHALL default to retrieving events from the past 7 days
7. THE Health_Events_Skill SHALL handle paginated responses by following the `nextToken` until all matching events are retrieved or a maximum of 500 events have been collected
8. IF the `health:DescribeEvents` API call fails due to throttling, authorization error, or service unavailability, THEN THE Health_Events_Skill SHALL return an error message indicating the failure reason and the filter parameters that were attempted
9. IF the provided time range has a start time later than the end time, THEN THE Health_Events_Skill SHALL return an error message indicating the invalid time range
10. IF the filter criteria match zero events, THEN THE Health_Events_Skill SHALL return an empty result set with a message indicating no events matched the specified filters

### Requirement 3: Event Detail Retrieval

**User Story:** As an operator, I want to see detailed information about specific Health events, so that I can understand the scope, timeline, and nature of each event.

#### Acceptance Criteria

1. WHEN relevant Health events are identified, THE Health_Events_Skill SHALL use the `health:DescribeEventDetails` API to retrieve full event descriptions, status timelines, and affected services, batching requests in groups of up to 10 event ARNs per API call as required by the API limit
2. THE Health_Events_Skill SHALL extract and present the following fields from event details: event ARN, service, event type category, event type code, status, start time, end time, last updated time, region, availability zone, and event description
3. IF the `health:DescribeEventDetails` API returns an error for a specific event ARN in the `failedSet` response field, THEN THE Health_Events_Skill SHALL report the failed event ARN and error message to the operator and continue processing remaining events from the `successfulSet`
4. IF the total number of events to detail exceeds 10, THEN THE Health_Events_Skill SHALL issue multiple batched `DescribeEventDetails` calls of up to 10 event ARNs each until all events are detailed

### Requirement 4: Affected Entity Identification

**User Story:** As an operator, I want to identify which of my resources are affected by a Health event, so that I can correlate the event with the resources experiencing issues.

#### Acceptance Criteria

1. WHEN a Health event with event scope ACCOUNT_SPECIFIC is identified, THE Health_Events_Skill SHALL use the `health:DescribeAffectedEntities` API to retrieve the list of affected resources
2. THE Health_Events_Skill SHALL present affected entities with their entity value (ARN or resource ID), status (IMPAIRED, UNIMPAIRED, UNKNOWN, or PENDING), and last updated time
3. WHEN the Incident_Context includes specific resource identifiers, THE Health_Events_Skill SHALL perform exact string matching of each affected entity's entity value against the Incident_Context resource identifiers and present matched entities in a separate section before non-matched entities
4. THE Health_Events_Skill SHALL handle paginated entity responses by following the `nextToken` until all affected entities are retrieved, up to a maximum of 500 entities per event
5. IF the `health:DescribeAffectedEntities` API returns an error for a specific event ARN, THEN THE Health_Events_Skill SHALL report the event ARN for which entity retrieval failed and continue processing remaining events

### Requirement 5: Incident Correlation

**User Story:** As an operator, I want the skill to correlate Health events with my current incident, so that I can determine whether an AWS-side event is the root cause or a contributing factor.

#### Acceptance Criteria

1. WHEN Health events are retrieved during an incident investigation, THE Health_Events_Skill SHALL score each event for relevance by evaluating four factors against the Incident_Context: matching AWS service, overlapping timeframe (the event's active period between its start time and end time or present if still open intersects with the incident's start time through its end time or present), matching region or availability zone, and matching affected resources
2. THE Health_Events_Skill SHALL classify event relevance as High (matching service, overlapping timeframe, and at least one matching affected resource), Medium (matching service and overlapping timeframe without matching resources), or Low (matching service only without overlapping timeframe)
3. WHEN presenting correlated events, THE Health_Events_Skill SHALL order results by relevance classification (High first, then Medium, then Low), and within the same classification SHALL order by event start time descending (most recent first)
4. WHEN one or more open events are classified as High or Medium relevance, THE Health_Events_Skill SHALL label those events as likely contributing factors in addition to their relevance classification
5. IF the Incident_Context does not include specific resource identifiers, THEN THE Health_Events_Skill SHALL score relevance using only service, timeframe, and region factors, classifying as High (matching service, overlapping timeframe, and matching region or availability zone), Medium (matching service and overlapping timeframe), or Low (matching service only)

### Requirement 6: Chat-Based Health Event Reporting

**User Story:** As an operator using chat, I want to generate summary reports of AWS Health events over a configurable time period, so that I can review the health posture of my account.

#### Acceptance Criteria

1. WHEN a user requests a health events report or summary via chat, THE Health_Events_Skill SHALL retrieve all Health events within the user-specified time period, up to a maximum lookback of 90 days
2. THE Health_Events_Skill SHALL organize the report by event type category (issues, scheduled changes, account notifications) with counts for each category
3. THE Health_Events_Skill SHALL include a breakdown of events by AWS service and by event status (open, closed, upcoming)
4. WHEN the user specifies a particular AWS service, THE Health_Events_Skill SHALL filter the report to only include events for that service, matching against the AWS Health service namespace
5. IF the user specifies a service name that does not match any known AWS Health service namespace, THEN THE Health_Events_Skill SHALL inform the user that the service was not recognized and list available services with events in the requested time period
6. THE Health_Events_Skill SHALL present event timelines showing when events started, when they were resolved, and total duration for closed events, with durations expressed in days, hours, and minutes
7. WHEN no time period is specified for a chat report, THE Health_Events_Skill SHALL default to the past 30 days
8. IF no Health events are found within the specified time period, THEN THE Health_Events_Skill SHALL inform the user that no events were found and confirm the time period that was searched
9. IF the AWS Health API is unavailable or returns an error during report generation, THEN THE Health_Events_Skill SHALL inform the user that the report could not be generated and indicate the reason for the failure

### Requirement 7: Structured Output

**User Story:** As an operator, I want the skill to present findings in a clear, structured format, so that I can quickly understand the health event landscape and take action.

#### Acceptance Criteria

1. THE Health_Events_Skill SHALL present a summary section listing the total number of events found, broken down by event type category and status, with events ordered by relevance (High before Medium before Low) and then by start time descending
2. WHEN presenting individual events, THE Health_Events_Skill SHALL include: event type category, service, region, status, start time in ISO 8601 format, end time in ISO 8601 format, and a description of no more than 256 characters summarizing the event
3. WHEN presenting correlation results during incident investigation, THE Health_Events_Skill SHALL include the relevance classification (High, Medium, or Low), the matching criteria that determined the classification, and at least one actionable next step per correlated event such as checking a specific resource, reviewing related service limits, or verifying configuration changes
4. IF no relevant Health events are found, THEN THE Health_Events_Skill SHALL explicitly state that no matching AWS Health events were identified and recommend checking other potential causes such as recent deployments, configuration changes, or resource limits
5. WHEN presenting multiple events, THE Health_Events_Skill SHALL group events by event type category (issues first, then scheduled changes, then account notifications) and within each group sort by status (open first, then upcoming, then closed)

### Requirement 8: Error Handling and Prerequisites

**User Story:** As an operator, I want clear feedback when the skill cannot retrieve Health events, so that I can resolve access issues and continue my investigation.

#### Acceptance Criteria

1. IF the Agent lacks `health:Describe*` permissions, THEN THE Health_Events_Skill SHALL report the missing permissions and specify the required IAM actions: `health:DescribeEvents`, `health:DescribeEventDetails`, `health:DescribeAffectedEntities`, and `health:DescribeEventTypes`
2. IF the AWS Health API returns a throttling error (HTTP 429 or Throttling exception), THEN THE Health_Events_Skill SHALL retry with exponential backoff starting at 1 second and doubling each attempt, up to 3 retries with a maximum delay of 8 seconds, before reporting the failure to the operator with the message that the Health API is currently rate-limited
3. IF the AWS Health API is unreachable or returns a service error (HTTP 5xx), THEN THE Health_Events_Skill SHALL report the error code and recommend the operator check the AWS Health Dashboard at https://health.aws.amazon.com directly as a fallback
4. THE Health_Events_Skill SHALL call the AWS Health API using the `us-east-1` endpoint, as the Health API is only available in that region for commercial accounts
5. IF the AWS Health API call does not respond within 30 seconds, THEN THE Health_Events_Skill SHALL abort the request and report a timeout error to the operator

### Requirement 9: Decision Tree for Event Search Strategy

**User Story:** As an operator, I want the skill to follow an intelligent search strategy, so that the most relevant events are surfaced efficiently without excessive API calls.

#### Acceptance Criteria

1. WHEN the affected AWS service is known from the Incident_Context, THE Health_Events_Skill SHALL first search for events filtered by that service within the past 7 days
2. WHEN no events are found for the specific service, THE Health_Events_Skill SHALL broaden the search to up to 3 services that share infrastructure dependencies with the affected service (for example, if ELB is affected, also check EC2 and VPC; if RDS is affected, also check EC2 and EBS)
3. WHEN the incident involves a specific availability zone, THE Health_Events_Skill SHALL filter events by that availability zone in addition to the service filter
4. WHEN both the service-specific search and the related-services search return no results, THE Health_Events_Skill SHALL expand the time window to 14 days and retry the same search sequence before reporting no events found
5. THE Health_Events_Skill SHALL sort results by presenting open events before closed events, and within each status group, presenting issue events before scheduledChange events and scheduledChange events before accountNotification events
6. IF no affected AWS service can be determined from the Incident_Context, THEN THE Health_Events_Skill SHALL search across all services for events within the past 7 days filtered by the incident region and availability zone when available
