---
name: cloud-metering-commercial
description: Design cloud usage collection, unit normalization, cost attribution, demonstration pricing, and billing controls.
---

# Cloud metering and commercial systems

Treat usage and money as auditable data. Separate raw observations, normalized usage, price calculation, invoice output, and payment execution.

## Workflow

1. Identify the authoritative meter, collection interval, source service/release, tenant/resource identifiers, units, timestamps, missing-data behavior, and retention.
2. Define normalization and aggregation precisely. Preserve source lineage and distinguish measured, estimated, imputed, and simulated values.
3. Model pricing rules, currency precision, rounding, effective dates, discounts, minimums, taxes, and versioning. Calculate examples and reconcile totals independently.
4. Analyze late, duplicate, corrected, or out-of-order usage; define idempotent ingestion and adjustment/audit procedures.
5. Keep demo calculations clearly labeled non-production. For real billing, require owner-approved policy, legal/tax review, customer terms, reconciliation, dispute handling, and payment security.

Do not call simulated or estimated usage real billing. Do not integrate payment providers or move funds without explicit authorization. Do not invent unavailable meters; document gaps and limitations.
