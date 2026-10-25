---
name: cloud-sre-operations
description: Define reliability targets and operate cloud services through monitoring, incident response, capacity management, and recovery planning.
---

# Cloud SRE operations

Design operations around user-visible reliability. Use measurable service-level indicators, explicit error budgets, and tested recovery rather than uptime claims without evidence.

## Workflow

1. Identify critical user journeys and define availability, latency, correctness, and recovery indicators with measurement windows and exclusions.
2. Map dependencies and failure modes across Keystone, Nova, Neutron, Glance, Cinder, databases, messaging, DNS, networks, and hardware as applicable.
3. Define actionable alerts, dashboards, logs, traces, ownership, escalation, runbooks, and safe diagnostic steps. Minimize sensitive data in telemetry.
4. For incidents, establish impact and timeline from evidence, stabilize safely, preserve forensic context, communicate uncertainty, and verify recovery.
5. Plan maintenance, upgrades, capacity, backups, restore drills, and failure exercises with blast-radius limits and rollback criteria.
6. After incidents, identify systemic causes and track corrective actions; avoid blame and unsupported root-cause claims.

Do not restart services, fail over, drain nodes, or run load/fault tests without authorization. Use current release documentation; older operations guides may be useful background but not authoritative for current behavior.
