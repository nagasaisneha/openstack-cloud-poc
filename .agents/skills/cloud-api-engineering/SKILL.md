---
name: cloud-api-engineering
description: Build or review Python APIs and management services for cloud provisioning, resource catalogs, identity boundaries, and provider adapters.
---

# Cloud API engineering

Design for explicit contracts, tenant isolation, compatibility, and predictable failure under load. Apply the judgment of a principal backend engineer without claiming literal work history.

## Workflow

1. Read the existing contract, callers, tests, and repository conventions before changing behavior. Identify whether this is a schema/validation boundary, a synchronous service, or a real provider execution path.
2. Specify exact fields, units, identifier provenance, status semantics, authorization context, idempotency behavior, and error categories. Keep acceptance distinct from execution and provider-evidenced completion.
3. Validate at trust boundaries. Enforce project scope and action authorization before disclosing catalog or resource state. Return stable, sanitized errors; never reflect credentials, tokens, raw request secrets, or unsafe exception text.
4. Keep domain logic independent from HTTP/framework and provider SDKs. Use narrow interfaces and adapters; make time, IDs, provider responses, and failure injection controllable in tests.
5. Analyze concurrency, duplicate submissions, timeouts, retries, partial provider success, API versioning, and client compatibility. Do not claim idempotency unless a durable or otherwise adequate mechanism enforces it.
6. Verify invariants and report which behavior is simulated, locally tested, provider-backed, or unverified. Include migration and rollback implications for contract changes.

Do not create an HTTP server, queue, persistence layer, or provider integration merely because the project has an API contract. Require a demonstrated workflow and user authorization for endpoint contact or cloud mutations. Keep secrets out of source, logs, error payloads, and examples.
