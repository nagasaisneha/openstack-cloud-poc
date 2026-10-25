---
name: virtualization-capacity
description: Assess Linux/KVM/libvirt readiness, compute capacity, resource oversubscription, and workload placement constraints.
---

# Virtualization and capacity engineering

Assess usable capacity, not just installed hardware. Apply quantitative reasoning and account for noisy neighbors, failure reserve, and workload variability.

## Workflow

1. Inventory CPU topology/features, RAM, NUMA, disk IOPS/capacity, NIC bandwidth, accelerators, kernel modules, hypervisor version, and actual device access. Mark missing observations unknown.
2. Derive workload demand and headroom in explicit units. Separate nominal allocation from allocatable capacity, utilization, overcommit policy, and failover reserve.
3. Evaluate KVM/libvirt compatibility, nested virtualization, passthrough/IOMMU, NUMA locality, CPU pinning, huge pages, and live migration only when the workload requires them.
4. Model a node or rack failure and determine whether remaining capacity can meet the target SLO. State assumptions and sensitivity to growth or skew.
5. Recommend a bounded feasibility experiment with success thresholds, rollback, and no unintended workload impact.

Never infer usable KVM from a CLI or module alone if `/dev/kvm` or required permissions are missing. Do not configure passthrough, change kernel/system settings, or run stress tests without explicit authorization. GPU/MIG capabilities are hardware- and driver-specific.
