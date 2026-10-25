---
name: openstack-storage-operations
description: Assess or operate Cinder volumes, storage backends, attachment, snapshots, performance, durability, and recovery.
---

# OpenStack storage operations

Treat persistence, data integrity, and recovery as first-class service properties. Use documentation and evidence for the actual Cinder release and configured backend.

## Workflow

1. Identify backend and driver, volume types, availability zones, encryption, replication, capacity, latency/IOPS characteristics, and project quotas.
2. Map the volume lifecycle: create, attach, guest discovery, filesystem use, detach, snapshot/backup, restore, and delete. Track attachment state across Nova and Cinder.
3. Define durability and recovery objectives, consistency requirements, failure domains, backup retention, encryption/key ownership, and data deletion behavior.
4. Analyze saturation and tail latency, not averages alone. Separate guest filesystem, network, backend, and control-plane symptoms.
5. Test on approved disposable data. Verify restore contents and cleanup; a successful snapshot call alone does not prove recoverability.

Do not assume Ceph, shared storage, encryption, or snapshots are available. Do not delete, detach, wipe, or overwrite data without explicit resource-level authorization and a verified recovery/rollback plan.
