---
name: cloud-platform-architecture
description: Design or review a multi-tenant cloud hosting platform, OpenStack management plane, service boundaries, capacity model, or technical roadmap.
---

# Cloud platform architecture

Approach architecture as a principal engineer accountable for reliability, security, and cost at production scale. Do not claim personal tenure; demonstrate senior judgment through evidence, explicit assumptions, quantitative reasoning, and trade-off analysis.

## Workflow

1. Establish the user journey, operators, tenant boundaries, deployment model, SLOs, data classification, workload shape, growth assumptions, budget, and constraints. Separate verified facts from estimates and open decisions.
2. Map control plane and data plane, trust boundaries, dependencies, state ownership, failure domains, and external services. Prefer existing OpenStack capabilities where they meet the need; keep provider adapters replaceable.
3. Quantify expected request rate, concurrency, resource footprint, storage growth, recovery objectives, and capacity headroom. State formulas, units, baselines, and uncertainty; do not present invented benchmarks as measurements.
4. Compare viable choices using reliability, security, complexity, portability, operational burden, cost, and reversibility. Identify the first bottleneck and the scale threshold that changes the recommendation.
5. Define failure behavior, degraded modes, observability, migration/upgrade path, rollback, and ownership. Document decision records for material choices and keep unresolved owner decisions visible.

Do not add queues, databases, Kubernetes, GPU scheduling, billing, or external identity by default. Add each only when a demonstrated workflow requires it. In this repository, fixtures are simulated, and real Nova/Neutron work remains gated by endpoint, scope, and task approval. Deliver diagrams and tables that distinguish current, proposed, and unverified components.
