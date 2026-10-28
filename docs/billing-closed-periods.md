# Verified prospective billing periods

T50 establishes a clean billing start using real provider observations. It
does not reconstruct unavailable pre-enrollment counters or Swift history.
Existing issued monthly statements remain immutable and provisional.

## Verified period and result

For tenant A, the period **2026-10-28 18:13:36.675115 to
18:13:46.231517 UTC** was measured, closed and independently reconciled.
The native Horizon workflow issued a **complete INR 2.76 demo statement with
22 lines across eight meters**. Every quantity/rate/rounded line and the summed
total matched an independent Decimal calculation. Owner CSV/JSON returned 200;
tenant B received 404. Both earlier monthly statements retained provisional
status and null totals. No payment was executed.

[Sanitized result, rates and source lineage](evidence/closed-billing-period-20261028.json).
Raw immutable snapshots, object inventories, mutation records and the operator
receipt remain in protected runtime storage. The real workload ran bounded
guest shell computation and HTTP traffic, then overwrote and deleted a new
Swift object. Independently measured deltas were 1.15 domain-process CPU-seconds,
19,816 combined interface bytes and 39,549.424592 object byte-seconds. Only the
new test container/object was removed.

## Measurement and charging policy

- CPU and network use exact cumulative differences between recorded domain
  source endpoints. CPU includes QEMU/hypervisor process overhead. Network is
  received plus transmitted interface bytes, not internet egress. Per-domain
  observation timestamps are retained; acquisition offset is bounded at two
  seconds. Counter deltas are never prorated across an unknown boundary.
- Active vCPU/RAM, local disk, volume and snapshot allocations retain the
  provider SQL/event boundary policy. Source timestamps and clock checks are
  recorded. The test covers running guests and known one-GiB volume history.
- Swift object byte-hours integrate account-owned object sizes at **successful
  proxy-response completion**. An authenticated API inventory establishes the
  baseline; durable PUT/overwrite/DELETE events change it, and a terminal
  inventory must reconcile exactly. This policy does not claim a physical
  backend write timestamp or replicated-device byte measurement.
- The journal records requests before execution and immutable outcomes after
  completion. Missing outcomes and uncertain server failures block completeness.
  Usage belongs to the storage account owner, including an explicitly authorized
  ACL writer from another project. Denied writes create no charged mutation.
- Source snapshots and period certificates are immutable and keyed by hashes.
  Identical observations are idempotent; conflicting duplicates, reset/generation
  changes, missing source samples over 45 seconds, missing terminal counters
  after resource stop/deletion, unknown Swift sizes/COPY/versioning/large-object
  cases and inventory races prevent a complete period. Missing usage is never
  filled with zero or a guessed delta. Source windows have explicit request,
  row and memory limits and fail closed when exceeded.

## Trust and browser behavior

Only a root peer can request period certification from the private meter socket.
It must reference actual stored start/end snapshot IDs and provide reconciled
API inventory evidence. Tenants and the Horizon collector cannot create a
certificate. The source verifies project, time, clock and completeness before
persisting an immutable certificate. The collector remains project-scoped and
Swift HEAD-only. Historical open-month reads remain provisional.

The billing domain continues to support calendar-month statements. A bounded
non-month period is accepted only when the trusted source attests an observed,
complete, already-closed period. A fixture, arbitrary dates or a certificate
with another project/interval cannot bypass that guard. Certified immutable
closed evidence does not become inaccurate merely because it is read later;
unverified stale observations still fail completeness.

Admin and tenant tables display exact UTC interval endpoints, status, rates,
amounts and downloads. A complete total applies only to that displayed period.
New immutable demonstration rate versions never rewrite earlier statements.
Staging must repeat this acceptance against its actual selected sources and
reviewed service profile; this local result does not certify whole-month history,
unsupported resource transitions or a production invoicing policy.
