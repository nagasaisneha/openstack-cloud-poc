---
name: openstack-service-engineering
description: Map OpenStack services and design integrations across Keystone, Nova, Glance, Neutron, Cinder, and related APIs.
---

# OpenStack service engineering

Use current documentation for the installed OpenStack release. Treat API microversions, policy defaults, deployment topology, and backend support as release- and operator-dependent.

## Workflow

1. Identify the cloud release, enabled services, endpoint catalog, authentication scope, policy configuration, SDK/client version, and supported API microversions from approved evidence. Do not infer remote service availability from local package presence.
2. Map the request path and ownership: Keystone identity and project policy; Glance image metadata; Nova compute lifecycle; Neutron ports and connectivity; Cinder volume lifecycle; optional telemetry or orchestration only where needed.
3. Define the least-privilege operations and service/user credentials required. Check quotas, resource limits, service policy, and dependency health before proposing mutations.
4. Use official service and operator documentation matching the deployment release. Distinguish API guarantees from implementation details and local configuration.
5. For each integration, record evidence source/time, scope, response provenance, failure semantics, cleanup, and what remains unknown. Prefer read-only discovery before approved mutations.

In this project, real SDK integration and Nova lifecycle are conditional. Fixtures must remain visibly simulated. Do not contact endpoints, reveal credentials, or mutate cloud resources without explicit task-specific authorization.
