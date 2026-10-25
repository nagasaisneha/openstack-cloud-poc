---
name: openstack-compute-operations
description: Plan or operate Nova instance lifecycle, images, flavors, quotas, scheduling constraints, and safe compute cleanup.
---

# OpenStack compute operations

Use Nova documentation for the target cloud release and verify actual policy, quota, image, flavor, and host capability. Never equate API acceptance with a running instance.

## Workflow

1. Confirm tenant/project, image provenance, flavor resources, network, key/access method, quota, availability zone, cost, and cleanup scope.
2. Check authorization and capacity evidence before create. Identify dependencies on Glance, Neutron, Placement/scheduler, compute hosts, hypervisor, and storage.
3. Track provider state transitions and asynchronous failures accurately. Distinguish request acceptance, Nova server ID, task state, power state, and final status.
4. Make retries safe: inspect for prior task-created resources before creating again; do not claim idempotency without enforcing it. Capture sanitized operation IDs and timestamps.
5. Verify inspect, stop/start, delete, and dependent-resource cleanup. Delete only resources created by the approved task and confirm deletion outcome.

If the endpoint, quota, backend, or approval is missing, report the operation blocked/unavailable with evidence. A fixture instance ID/state is simulated and is not a Nova resource. Do not contact or mutate Nova without task-specific authorization.
