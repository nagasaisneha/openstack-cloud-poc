# Provisioning API contract

## Scope

This document and `openstack_cloud_poc/api_contract.py` define framework-independent request/response contracts and local validation types. They do **not** establish a deployed HTTP server or live provider integration. The legacy T13 service remains synchronous validation-only. `AsynchronousLifecycleManagementService` connects the fixture job tracker, bounded local execution engine, and in-memory `FixtureInstanceProvider`; this is simulated local execution, not Nova/Neutron operation. Creating a lifecycle accepted-response value alone does not register or execute work. POC-016 (OpenStack SDK integration) remains deferred.

The request and response examples below use synthetic fixture references. Every response example is marked `simulated`; none represents a Keystone login, real authorization, cloud resource, or provider result.

## Operation and project scope

The existing `ProvisioningRequest` used by T12/T13 supports `compute.create` and requires an explicit `project_id` plus `image_id`, `flavor_id`, and `network_id`. The added `LifecycleRequest` contract supports `compute.create`, `compute.inspect`, `compute.start`, `compute.stop`, and `compute.delete`. Create requires the three resource references; the other operations require `instance_id`. References are opaque, non-empty, single-line strings. These schemas validate shape only; they do not discover resources or inspect/change instance state.

```json
{
  "operation": "compute.create",
  "project_id": "fixture:project-alpha",
  "image_id": "fixture:image.linux-example",
  "flavor_id": "fixture:flavor.small",
  "network_id": "fixture:network.private-demo"
}
```

Credentials, tokens, user passwords, and internal fixture identity/authentication objects are not fields in the public request schema. An implementation must authenticate and authorize the caller separately and enforce that authorization for the requested project before returning an accepted response. The T12 validator does not perform authentication or authorization; the accepted-response factory requires an allowed authorization result from its caller.

## Fixture authorization policy

`openstack_cloud_poc/identity.py` enforces project equality and an explicit operation-to-role allowlist. `catalog.read` requires `catalog.viewer`; `compute.inspect` allows `compute.viewer` or `compute.operator`; `compute.create`, `compute.start`, `compute.stop`, and `compute.delete` require `compute.operator`; fixture `usage.read` requires the separate `usage.viewer` role. Unknown operations, missing roles, unauthenticated identities, and project mismatches are denied. This helper authorizes the requested project and operation; the lifecycle service must also verify that a referenced instance belongs to that project before disclosing or changing it. That object-ownership check is not part of request-schema validation. The local execution engine rechecks authorization from the registered job's actor, project, and operation before invoking a callable. This fixture policy is simulated and does not verify real Keystone role changes. The fixture identities are examples, not Keystone identity evidence.

## Lifecycle and evidence

Lifecycle and evidence mode are separate fields:

- `status`: `accepted`, `completed`, `rejected`, or `failed`.
- `evidence_mode`: `simulated` or `real`.
- `source`: required provenance for the response.

`accepted` means request validation and authorization passed. It does **not** mean the request was queued, executed, or created a resource. An accepted response has no `resource_id` or provider evidence:

```json
{
  "operation": "compute.create",
  "project_id": "fixture:project-alpha",
  "status": "accepted",
  "evidence_mode": "simulated",
  "source": "fixture:T12 contract example"
}
```

`completed` is valid only when a provider supplies both a resource reference and evidence. The following is synthetic test data, not a cloud result:

```json
{
  "operation": "compute.create",
  "project_id": "fixture:project-alpha",
  "status": "completed",
  "evidence_mode": "simulated",
  "source": "test:synthetic provider fixture",
  "resource_id": "synthetic:resource-1",
  "provider_evidence": "synthetic test evidence; no cloud resource exists"
}
```

T12 produces no completed provisioning result. `real` is reserved for a future separately authorized provider path and must not be used for fixture output.

**Identifier semantics:** The T12/T13 `ProvisioningResponse.accepted` remains a validation/authorization response and carries neither a job ID nor a resource ID; it does not mean work was queued. For the asynchronous lifecycle contract, `LifecycleAcceptedResponse` includes a `job_id` and `status: accepted`, but no instance/resource ID. The integrated local service returns this response only after the executor successfully accepts the registered operation; failed submissions return an error and never an accepted response. The schema type itself does not prove that submission happened. Later job status may include a `fixture-instance-*` ID when available; that is a synthetic fixture identifier, not a Nova server ID. A future Nova integration may expose a Nova server ID only when actually returned by Nova. Never manufacture a Nova ID. All current lifecycle contract examples and evidence remain simulated.

Example asynchronous acceptance shape (illustrative contract only; no job has been submitted):

```json
{
  "operation": "compute.create",
  "project_id": "fixture:project-alpha",
  "status": "accepted",
  "job_id": "fixture-job-001",
  "evidence_mode": "simulated",
  "source": "fixture:lifecycle contract example"
}
```

## Local job tracking

The fixture tracker accepts `compute.create`, `compute.start`, `compute.stop`, and `compute.delete`; `compute.inspect` is read-only and is not registered as a job. A record contains a synthetic job ID, project ID, initiating fixture actor ID, operation, state, timezone-aware creation/update timestamps, optional fixture instance ID, optional allowlisted failure code, and simulated provenance.

Allowed state changes are `pending` → `running` or `failed`, then `running` → `succeeded` or `failed`. Terminal states cannot change. The tracker has no public unrestricted transition method: state changes use its module-internal execution path, which is used by the local executor and is not exposed through the management API. This boundary is a Python module convention, not a security boundary against arbitrary in-process code.

Job inspection requires an authenticated `compute.viewer` or `compute.operator` identity scoped to the requested project. Missing, unauthorized, and cross-project job lookups all return the same not-found error. Registration checks project equality and the role required for the requested operation. `LocalExecutionEngine` rechecks authorization before invoking the callable. The fixture tracker does not itself verify instance ownership against a provider; instance/project ownership remains the lifecycle service's responsibility.

An optional idempotency key is scoped by project and operation. The tracker computes a SHA-256 fingerprint from canonical JSON of the validated request. The same key and same fingerprint return the existing job with `created: false`; the same key with a different fingerprint returns `idempotency_conflict`. Calls without a key are not deduplicated. The key index and records exist only in process memory, so restart loses both and does not provide durable idempotency.

## Local asynchronous execution

`LocalExecutionEngine` uses Python's `ThreadPoolExecutor`. `max_workers` sets the active worker limit and `max_pending` sets the queued-operation allowance; accepted in-flight work is capped at their sum. The default configuration is two workers plus eight queued operations. A nonblocking bounded semaphore rejects work at capacity. It also reserves each `(project_id, fixture instance_id)` while a mutation is active; a conflicting mutation is rejected before executor acceptance and its job is failed with a safe conflict code. `submit(job_id, callable)` returns a non-cancellable execution handle only after the pool accepts the callable. Capacity exhaustion, an instance conflict, shutdown, invalid input, or a pool submission exception raises a sanitized error; where the job is still pending, the tracker records a safe failure instead of leaving false acceptance.

The worker changes `pending` to `running` only after it starts, rechecks authorization using the job's stored actor/project/operation, invokes the supplied local callable, and marks it `succeeded` only after it returns. Exceptions become safe allowlisted failure codes; exception text and traces are not stored in job records. A returned instance ID must use the fixture-only format. For start/stop/delete, the registered fixture instance ID is retained when the callable returns no new ID. These callables are controlled local test functions; this engine does not invoke Nova, Neutron, AWS, or other network services.

`AsynchronousLifecycleManagementService` validates/authenticates requests, synchronously inspects instances, verifies project ownership before mutations, registers jobs, submits work, and returns the accepted response only after successful submission. Its submission lock makes concurrent duplicate idempotency requests observe the completed registration/submission decision; only the first request schedules work. `get_job` delegates to project-scoped job inspection. The legacy `SynchronousManagementService` remains create-only and does not create jobs or resources.

`close()` stops new submissions and waits for accepted running and queued work to finish. Submissions attempted after shutdown are rejected and pending jobs are marked failed where possible. Cancellation is not exposed. The engine, tracker, and instance provider are process-local: process termination can lose records and interrupt accepted work, with no recovery or durable queue. The integrated service is fixture-only and does not claim Nova/Neutron behavior.

For fixture provenance, instance associations must match `fixture-instance-` followed by decimal digits. Nova server IDs are not accepted as fixture IDs and are never synthesized. The legacy wire field `failure_category` remains for compatibility; the record field is named `failure_code` and is restricted to the existing safe enum values.

**Compatibility migration:** The former unscoped `create(operation)`, `get(job_id)`, and public `transition(...)` calls are replaced by authorized `register(LifecycleRequest, authentication, idempotency_key)`, scoped `get(job_id, project_id, authentication)`, and the internal execution path. Callers must not bypass these checks. The create-only T12 `ProvisioningRequest` and T13 synchronous validator are unchanged.

## Validation and safe errors

The standard-library lifecycle validator accepts exactly the fields declared for the selected operation, requires a project scope, and rejects empty or multiline references. The legacy T12 validator remains create-only. Validators do not echo submitted values in error messages.

| Error code | Meaning |
|---|---|
| `invalid_request` | Missing, extra, or malformed fields |
| `unsupported_operation` | Operation name is not supported by this contract |
| `project_scope_required` | Request omitted its project scope |
| `unauthorized_project` | Caller-supplied authorization result denied the project |
| `provider_unavailable` | Reserved for a future provider response |
| `provider_error` | Reserved for a safe future provider failure |
| `provider_evidence_required` | Completion lacks provider evidence or a resource reference |

Errors serialize as a stable `{ "error": { "code": ..., "message": ... } }` object. Request values, credentials, and identity details are not included.

## Boundaries

The contract, tracker, local engine, and asynchronous management service model request validation, fixture authorization, response shapes, process-local job records, and bounded execution against `FixtureInstanceProvider`. They do not define a deployed HTTP path, real authentication mechanism, persistent idempotency store, crash recovery, cancellation, endpoint configuration, or cloud operation. The T13 `SynchronousManagementService` remains validation-only and does not use the asynchronous workflow. Any real endpoint, credentials, SDK, Nova, or Neutron operation requires separate explicit approval.
