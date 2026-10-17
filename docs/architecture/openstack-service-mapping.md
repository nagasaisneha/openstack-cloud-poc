# OpenStack service mapping

This mapping follows the diagram in [`architecture-diagram.png.jpeg`](architecture-diagram.png.jpeg) and the scope in [`requirements-specification.md`](../requirements/requirements-specification.md). The selected route is an OpenStack management POC first, using a deterministic fixture-backed adapter for early development. Its management contract is independent of the VM manager. A service is only considered available after a later read-only discovery task confirms it. Real OpenStack integration is conditional on a verified endpoint or a separately approved deployment plan; endpoint contact and cloud mutations require their own explicit approval.

| Diagram capability | POC mapping | Local / simulated / deferred |
|---|---|---|
| Users, tenants, roles, scoped access | Manager-independent management contract with deterministic fixture identity/data first; later Keystone users, projects, roles, application credentials where supported | Fixtures support early offline development and are always marked simulated; they do not prove Keystone authentication or security. Real Keystone integration requires a verified endpoint or separately approved deployment plan plus task-specific approval. |
| Web portal, CLI / SDK | Horizon if already present; OpenStackClient or SDK for scripts; optional thin POC API/UI after workflows are proven | CLI/API is local or remote-client access. Custom portal is deferred unless backlog evidence justifies it. Mobile app deferred. |
| Keycloak / Authentik, SSO / MFA | Keystone identity; federation only if an existing deployment supports it | Not an exact Keycloak feature replacement. External identity integration deferred. |
| Internet, DDoS, WAF, load balancer, API gateway | WSL/host firewall and Neutron security groups; optional Octavia only if already supported and required | Public ingress, Cloudflare, DDoS service, WAF, and Kong/NGINX gateway are external/deferred. Keep services local/private by default. |
| Resource catalog | Manager-independent catalog contract; deterministic fixtures first; later read Nova flavors, Glance images, Neutron networks, Cinder types/capabilities | Fixture entries are illustrative and marked simulated, never observed capacity. Real inventory requires verified endpoint evidence and separate approval for endpoint contact. Never advertise unavailable capacity. |
| VM provisioning | Nova compute API and lifecycle behind the adapter boundary (requirements §3 Layer 4 and POC-008) | Nova integration is conditional on a verified endpoint or separately approved deployment plan, and task-specific approval for scoped operations. The diagram's Proxmox VE (initial) stage is deferred; Proxmox integration requires explicit approval. Until then, simulate request/job state only and label it simulated. |
| VM images / image registry | Glance for VM images | Container/model registry (Harbor / OCI) is separate and deferred. |
| Virtual networking | Neutron networks, subnets, routers, ports, security groups | Conditional on deployment support. WSL2 networking is not a production tenant network; use fixtures for unimplemented topology. |
| Block storage | Cinder volumes, attachments, snapshots | Conditional on a compatible backend and spare disk. No Ceph/ZFS backend is presumed. Otherwise simulate catalog and mark operations unavailable. |
| Object/file storage | Swift if present and required | S3-compatible external object storage is optional; no offsite upload. Local files are not object storage. |
| GPU scheduling / MIG | No native equivalent assumed for this laptop. Nova PCI passthrough is a separate, unverified capability. | Docker CUDA container visibility for the RTX 2060 (6 GB) is verified and can support a bounded inference probe. Guest passthrough is unverified; H100 MIG and multi-tenant GPU scheduling are unsupported. |
| Kubernetes, JupyterHub, GPU Operator | Existing installation only if discovered | Not a default install. For this resource budget these are deferred; use a small mocked workflow or external cloud if needed. |
| Usage metering | Ceilometer/Gnocchi if present and compatible with release; otherwise OpenStack API snapshots and clearly scoped app-level measurements | No claims of complete billing-grade usage. Synthetic samples may exercise report formatting, clearly labelled simulated. |
| Billing, payments, subscriptions | Demonstration price calculation from identified usage data | Razorpay/Stripe and payment lifecycle are simulated or deferred; never accept/store real payment data. No real billing claims. |
| Control-plane database, job queue | Do not access OpenStack service databases. Add app DB/queue only if a custom service needs durable state/async jobs | In-memory/local SQLite or deterministic fake job adapter can support early prototypes. PostgreSQL/Redis/Celery are not default requirements. |
| Monitoring and logs | Inspect existing sources; use verified host/app/OpenStack sources if available | Prometheus/Grafana/Loki/Wazuh/Elasticsearch are optional and likely too costly as a full stack here. Use lightweight logs/fixtures when absent. |
| Automation | Terraform provider or Ansible only after compatibility and need are confirmed | Not installed as of audit. No bootstrap/config changes until approval. |
| Backup / recovery | Back up POC source/configuration with secrets excluded; restore test for any meaningful local data | No production DR, offsite upload, or backup of OpenStack internals. Demonstrate restore locally if implemented. |
| Notifications / DNS | Local logs or development mail catcher; local test hostname if needed | SMTP, public DNS, SMS, Slack, and external domain services deferred. |
| Physical infrastructure | None | H100 server, redundant network/power, facilities, future servers, and data center scope excluded. |

## Service realization order

1. Build the manager-independent management contract and deterministic fixture-backed adapter for offline development.
2. If approved, discover whether a usable OpenStack endpoint and services exist without displaying credentials; otherwise keep the fixture route.
3. Integrate real Keystone/catalog services only after a verified endpoint or separately approved deployment plan and task-specific endpoint-contact approval.
4. Validate Nova compute/network lifecycle only in a separately approved task after service and resource feasibility are verified; keep the adapter boundary independent of VM-manager choice. Proxmox remains deferred unless explicitly approved.
5. Add usage and operational reporting only with named, verified data sources or clearly labelled fixtures.

The POC should expose capability status (`available`, `simulated`, `unavailable`) in its own documentation and UI/API. A successful mock operation must never be presented as a real OpenStack resource operation.
