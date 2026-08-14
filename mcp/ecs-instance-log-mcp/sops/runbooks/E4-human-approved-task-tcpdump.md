---
title: "E4 — Human-Approved Task-Scoped Packet Capture"
description: "Safely capture packets inside one ECS EC2 task network namespace"
status: active
severity: HIGH
triggers:
  - "packet.*capture"
  - "tcpdump"
  - "task.*network.*namespace"
owner: devops-agent
objective: "Collect minimum necessary packet evidence without host-wide capture"
context: "Packet data can contain credentials and customer payloads. Capture requires explicit confirmation and native SSM human approval."
---

## Phase 1 — Authorize and Triage

MUST:
- Confirm the requester is authorized to inspect traffic for the exact ECS task.
- Minimize `durationSeconds` and use the narrowest safe BPF `filter`; never collect unrelated traffic.
- Verify the task uses ECS EC2 launch type. Fargate and host-network tasks are unsupported.
- Verify tcpdump is already installed on the container instance; this tool never installs packages.
- Call `tcpdump_capture` with exact `instanceId`, task ID or ARN, and `confirmCapture=true`.
- Provide `containerName` when more than one RUNNING application container is eligible.
- Share `approvalConsoleUrl` with an authorized approver; do not submit duplicate capture requests.

## Phase 2 — Approve, Poll, and Analyze

MUST:
- Review task, container, interface, duration, and filter in the SSM approval request before approving.
- Poll `tcpdump_capture` with `executionId` until it returns a `commandId`, then poll that command.
- Stop if approval is denied or expires; request a fresh approval only if the capture is still necessary.
- Stop if task/container resolution, PID revalidation, or namespace validation fails; never fall back to host capture.
- Call `tcpdump_analyze` with the exact `instanceId` and `commandId`; latest-capture lookup is not allowed.

SHOULD:
- Use decoded text/statistics first and download raw pcap only when necessary.
- Treat the short-lived pcap URL and downloaded file as sensitive data.

## Phase 3 — Report and Retain

MUST:
- Cite `executionId`, `commandId`, `instanceId`, task ID, resolved container, and capture time.
- Separate observed packet evidence from inference and document capture gaps.
- Store or delete downloaded pcap data according to the applicable retention policy.

## Troubleshooting

- `pending_approval`: approve in SSM or wait; do not create another request.
- denied/expired: no capture ran; obtain fresh authorization before retrying.
- parameter pattern rejection: verify exact task ID/ARN, container name, interface, and restricted BPF syntax.
- tcpdump missing: install it through the approved node-image or configuration-management process, then retry.
- ambiguous container: provide the exact RUNNING application `containerName`.
- PID or namespace changed: the task restarted during dispatch; verify placement and request a new capture.
- host network namespace: unsupported by design because it would expose host-wide traffic.
- stale analysis warning: network conditions may have changed; request a fresh approved capture if needed.
