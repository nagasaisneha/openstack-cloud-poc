---
name: cloud-jobs-data
description: Design asynchronous provisioning workflows, job state machines, durable state, queues, retries, and database behavior for cloud control planes.
---

# Cloud jobs and data

Introduce asynchronous machinery only for a demonstrated long-running workflow. Model durable truth, duplicate delivery, partial completion, and recovery before selecting technology.

## Workflow

1. Establish why synchronous execution is inadequate, expected duration/concurrency, user-visible status needs, retention, consistency, and availability objectives.
2. Define request acceptance versus execution, job ID versus provider resource ID, state transitions, terminal states, cancellation semantics, failure categories, and provider evidence.
3. Specify transactional boundaries, outbox/inbox or equivalent delivery guarantees, idempotency keys, deduplication, retry policy, backoff, poison messages, leases, and reconciliation.
4. Choose queue, worker, and database based on measured throughput, failure semantics, operational expertise, and cost. Model queue depth, backpressure, connection limits, and database growth.
5. Define migration, retention, encryption, backup/restore, observability, and behavior during worker or broker outage. Test duplicate, reordered, delayed, and lost-message scenarios.

At-least-once delivery is common; do not promise exactly-once effects without a defensible end-to-end design. A simulated in-memory job tracker is not a queue, durable job service, or provider operation. Never add Redis/Celery/PostgreSQL by default.
