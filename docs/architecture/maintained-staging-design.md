# Maintained local POC and staging design

Design candidate, 2026-10-26; no target has been provisioned or approved. Both final environments use Hibiscus under the [release policy](../../deployment/release-policy.json), with separate inventories, credentials, data and acceptance. Preserve current Ubuntu 22.04 WSL2/Caracal as the migration source described in the [migration decision](maintained-release-migration.md).

## Topology and capacity estimates

These are planning estimates for the mandatory service set, not measured performance guarantees. Confirm CPU virtualization, disks, service RSS, burst/retention and recovery headroom before approving a target.

| Role | Local maintained POC design | Staging design candidate |
|---|---|---|
| Control/API | One separate supported Ubuntu 24.04 Linux host/VM, 8 vCPU, at least 32 GiB RAM and 200 GiB free SSD for bounded full-scope tests; no HA claim | Three controller nodes in separate host/power failure domains, each 8 vCPU/32 GiB/100+ GiB SSD; Keystone, API services, scheduler/conductor, MariaDB quorum, RabbitMQ and OVN NB/SB quorum; API VIPs via HAProxy/Keepalived |
| Compute/gateway | KVM verified on target; two small task-owned application VMs plus test storage; reserve host headroom | At least two KVM compute/gateway nodes, initial estimate 16 vCPU/64 GiB/500 GiB SSD each; enough spare capacity to evacuate bounded test workload; gateway chassis on two nodes |
| Block/image storage | New bounded dedicated test backend; source Cinder VG/image is never reused writable | Reviewed Ceph RBD candidate for Cinder/Glance, at least three independent storage failure domains and dedicated devices; capacity from logical data × replicas + recovery reserve |
| Object storage | New bounded device for functionality; explicitly no physical durability result | Swift with three reviewed storage failure domains, distinct devices/rings, three replicas and periodic audit/replication; do not assign a device simultaneously to Swift and Ceph |
| Monitoring/logs | Bounded retention with explicit service overhead; host minimum alone does not size telemetry | Separate monitoring capacity or explicit controller reservation; initial 4 vCPU/8–16 GiB plus disk sized from ingest rate × retention |
| Recovery | Independent protected target copies and isolated restore workspace | Backup repository outside the failure domains being backed up; access-controlled credentials and restore host capacity |

The current WSL memory allocation (7.7 GiB total) cannot meet this design's 32-GiB full-scope guard. Physical Windows memory and the availability of another local host are unknown. A supported local target requires an explicit environment decision; no OS/WSL change is performed automatically.

## Networks and ingress

| Plane | Design and tests |
|---|---|
| Management/API | Dedicated private management addresses, reserved internal API VIP, separate customer-facing private TLS endpoint; route only approved clients. No API or database wildcard publication to the internet. |
| Overlay | Dedicated interface/VLAN for Geneve underlay. Configure unique host tunnel IPs; verify bidirectional UDP encapsulation across both compute nodes and controller/OVN state. |
| Provider/external | Operator-owned physnet/interface/VLAN mapping, IPAM allocation pool/gateway and upstream route. Provider NIC is reviewed/unaddressed as required by deployment design. Never attach a Windows/WSL host NIC as a shortcut. |
| Tenant | Non-overlapping project CIDRs, DHCP, metadata path, router attachments, security groups and approved egress. Validate east-west connectivity and denied cross-project packets. |
| Storage/backup | Distinct VLANs/interfaces or explicit bandwidth reservation; storage endpoints inaccessible to customer networks. Measure recovery traffic and latency under a bounded workload. |
| DNS/NTP | Forward/reverse names and VIP certificate SANs resolve from deployment host, nodes, guests and intended customer client; common time sources, measured skew within the selected operational budget. |
| MTU | Tenant MTU = verified underlay path MTU minus actual encapsulation budget. Test largest nonfragmenting IPv4/IPv6 packets, TCP transfer and metadata access across hosts and FIP/VIP paths. Do not assume an interface's configured MTU proves the end-to-end path. |

Actual CIDRs, VLAN IDs, addresses, DNS names, gateways and certificates remain operator inputs. Inventory TEST-NET addresses are documentation only. Floating-IP acceptance requires an HTTP response through the approved routed client path to a VM and to the Octavia VIP; metadata-namespace or host-loopback access alone is insufficient.

## Octavia and health recovery

Use a matching Hibiscus Octavia API/driver-agent and OVN provider, configured through `globals.extended.example.yml`; enable the Horizon Octavia panel. Confirm the exact image includes the intended driver and matches Neutron/OVN dependencies. Provider availability, TCP listener/pool/monitor support, quota/policy and health-check flows require live tests.

The OVN approach avoids Amphora VM overhead. The selected initial service is IPv4 TCP with `SOURCE_IP_PORT`; use unique response markers and many fresh connections to test traffic distribution. This provides TCP backend reachability monitoring. HTTP status/path checks, L7 routing and TLS termination at the LB would require a separately reviewed provider/profile, such as Amphora with management networking and added VM capacity. Do not advertise these through the initial profile. [OVN provider capabilities](https://docs.openstack.org/ovn-octavia-provider/2024.1/admin/driver.html), [Hibiscus provider changes](https://docs.openstack.org/releasenotes/ovn-octavia-provider/2026.2.html), [Kolla provider configuration](https://docs.openstack.org/kolla-ansible/latest/reference/networking/octavia.html).

T44 acceptance creates two real Nova application VMs, then verifies both unique responses through the VIP. It stops one owned backend service while leaving member admin state/weight unchanged, records monitor convergence, requires only the survivor to answer 100 subsequent fresh connections, starts the service again, and requires both backend markers and healthy states. The test's initial exclusion/recovery bound is 60 seconds and must be reconciled with configured monitor delay/timeout/retry settings. A transient failure is recorded; failure after convergence fails acceptance. Provider ACTIVE alone never proves traffic or health recovery.

Distributed OVN flows do not prove physical HA. Staging must exercise controller/quorum loss, gateway loss, compute loss and storage-device/node loss without relying on co-located VMs as independent hardware.

## Secrets, certificates and identity

Use dedicated deployment and service identities, project-scoped users and separate billing collectors. Keep passwords, cloud auth, rings/hash secrets, CA/private keys, local inventories and ledgers in protected target storage or the approved secrets manager. Record rotation/revocation and recovery of each secret class. Service database access remains private; the application uses supported APIs.

Issue trusted endpoint certificates for real DNS/VIP SANs, verify CA chain/hostname/expiry, and define renewal alerts. The source laptop's localhost CA is not the staging CA. Apply TLS to user/API paths and reviewed service transport; validate whether individual backend links require mTLS or network isolation. Test tenant/admin roles, quota limits, session/CSRF controls, object/volume/LB ownership, usage/statement IDOR denial, log redaction and complete release secret scanning.

## Metering, pricing and audit

Select release-matched Ceilometer/Gnocchi or independently verified equivalent sources for compute, volume/object and network metrics. Record source units, resource/project attribution, source/collection timestamps, retention, reset handling and complete/missing coverage. The preserved Nova existence-based allocation query cannot certify active-only charging or network/storage consumption.

Configure versioned demo rates, currency, rounding and charging-state policy. Compare an independently calculated statement against a controlled real workload. Incomplete coverage remains provisional with no invented complete total. Preserve project statements, audit events and source lineage after resource cleanup. Deploy and test the billing/statement browser route and backend roles; existing Python domain tests are not a deployed billing UI.

## Backup, migration and rollback

Proposed targets for owner review: resource/config backup RPO 24 hours; ledger/audit RPO 15 minutes; bounded full-service recovery RTO 4 hours. Proposed new-target retention is 7 daily, 4 weekly and 3 monthly recovery points, subject to data classification/capacity approval. These are design inputs, not achieved SLOs. No retention/deletion automation applies to any existing source backup.

Back up deployment config, version/image/config IDs, credentials via protected procedures, service/application databases, rings, representative VM/volume/object contents, billing/rate/audit state and recovery metadata. Define a consistency point for related stores and verify checksums before and after an isolated restore. Measure actual RPO/RTO. Restore old schema backups only to compatible old software before supported migrations; downgrading migrated databases is not rollback.

Before a deployment change, retain the previous image/config set plus consistent data recovery point. Exercise an upgrade and failure on a disposable target. Roll back access/artifacts only through the reviewed change record and compatible recovery procedure; reconcile writes and task-owned provider resources. Preserve the Caracal source until migrated functionality, contents, target recovery and owner acceptance pass.

## Operations, tests and release artifacts

Monitor service API health, Keystone auth failures, Nova scheduling/build errors, Neutron/OVN agents/tunnels, Octavia member/VIP health, Cinder pool capacity, Swift errors/audits, collector freshness, billing gaps, disk/memory, certificate expiry and backup age/results. Dashboards must show freshness/source/units. Trigger and resolve an owned app/backend, collector and backup failure; verify alert delivery, timestamps and runbook links. Record structured action/request/actor/project/provider IDs with redacted errors.

Incident runbook: acknowledge alert → identify target/release and affected tenant scope → confirm API and actual data-plane state → preserve redacted logs/evidence → contain only the approved scope → apply documented recovery/rollback under incident authority → verify customer path/tenant denial → reconcile resources/data and record recovery timings. Escalation contacts and authority must be assigned before handover.

CI runs offline regression and policy checks plus pinned inventory/playbook syntax. A separate explicitly authorized integration run uses protected target credentials and owns its test resources. It records the release/image digests, all fourteen workflow results, cross-host networking, real traffic/health, meter/bill reconciliation, recovery and cleanup. CI configuration is not proof that a remote CI run or cloud test passed.

Candidate package includes Git SHA, hash-locked deployment tools, reviewed image digests/SBOM/signatures, environment config templates with no secrets, migration/rollback plan, test/failure reports, unresolved findings, operator runbooks and gate decisions. Deployment, reconfigure and upgrade commands are executed only against the separately approved inventory after preflight and Kolla prechecks; they are not run here.

## Ownership and gates

Gate 0 names product/release owner, platform operator, security approver, acceptance owner and on-call contact; selects workload, target, budget and data classification. Gate 1 approves supported software/images, topology/network/storage/capacity, threat model, SLO/RPO/RTO and migration design. Gate 2 requires actual foundation/secret/TLS/backup validation. Gate 3 validates integrations, isolation and customer resource/LB lifecycle. Gate 4 requires complete real customer workflow, load/health, recovery, upgrade/rollback and security disposition. Gate 5 authorizes the rollout change; Gate 6 records handover and scheduled maintenance/restore/access reviews. None of those external gates is passed by this document.
