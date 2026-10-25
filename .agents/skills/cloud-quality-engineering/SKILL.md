---
name: cloud-quality-engineering
description: Plan or implement meaningful tests for cloud APIs, tenant isolation, lifecycle workflows, adapters, failure handling, and scale behavior.
---

# Cloud quality engineering

Build confidence in observable behavior, not test counts. Reason like a senior quality engineer for distributed, multi-tenant systems.

## Workflow

1. Derive testable invariants from requirements and contracts. Include authorization, project boundaries, resource ownership, status truthfulness, provenance, cleanup safety, and failure behavior.
2. Choose the narrowest useful test layer: pure unit tests, adapter contract tests, service integration tests, then authorized cloud acceptance tests. Never treat fixtures as evidence of real cloud behavior.
3. Cover success, malformed input, denied access, missing dependencies, timeout, retry, duplicate request, partial completion, stale state, concurrency, and cleanup. Prioritize based on impact and likelihood.
4. For scale-sensitive paths, quantify representative workload assumptions and test the relevant bottleneck: latency distributions, throughput, queue depth, connection limits, memory, or contention. Avoid unrealistic load against live systems.
5. Use deterministic clocks/IDs and controlled failure injection when useful. Assert public behavior and security invariants rather than private implementation details.
6. Run only tests authorized by the task and repository policy. Report commands, results, coverage limits, and untested real-provider paths accurately.

Do not add tests or run load tests unless requested or required by the active task. Obtain explicit scope before tests that contact endpoints, create resources, incur cost, or stress shared systems. Never delete resources not created by the test.
