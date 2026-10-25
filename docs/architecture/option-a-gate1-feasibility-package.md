# Option A Gate 1 feasibility and decision package — deferred historical evaluation

**Status:** Superseded for current project scope by the final decision to continue the original local OpenStack POC and select staging independently. Retained for traceability only; Gate 1 is not an active AWS feasibility or implementation task. AWS-hosted OpenStack, AWS-native tenant provisioning, AWS resource creation, and OpenStack deployment are outside the approved implementation scope.

The active POC-to-staging relationship is defined in [`../staging-release-plan.md`](../staging-release-plan.md). This package records a former Option A evaluation and does not authorize cloud access or deployment.

Sections below preserve the decisions and questions from the earlier AWS-hosted OpenStack proposal. They are not active Gate 1 requirements under the current project scope.

**Historical purpose:** Turn the then-current Option A technical reference design into a decision and evidence checklist. This package records the former proposal's facts and open items; it does not select or approve the current staging environment.

## 1. Gate 1 outcome sought

Gate 1 should end with an owner-approved, environment-specific staging design and evidence record that establishes:

- what staging will validate for the production-oriented OpenStack architecture;
- what AWS and OpenStack configurations are required and who owns them;
- which capabilities are included, deferred, or blocked;
- the evidence supporting nested virtualization, Neutron, storage, security, capacity, and cost feasibility;
- what must be revalidated on physical data-center infrastructure; and
- whether a later, separate request for staging resources is justified.

**Gate 1 is not complete.** This package is based on repository documentation and public documentation only. No AWS account, Region, quota, offering, endpoint, or OpenStack environment was inspected. Gate 0 authorization for any account-specific read-only discovery is not recorded in the reviewed repository. Gate 1 must not be marked passed until its owners and evidence are recorded.

## 2. Architecture baseline entering Gate 1

| Topic | Former Option A proposal | Evidence recorded in this historical package |
|---|---|---|
| Staging substrate | AWS temporarily hosts an OpenStack environment, subject to feasibility and approval | No AWS account/Region or instance type selected or verified |
| Tenant compute | Nova manages tenant VMs; Proxmox is deprecated | Nova integration and operation remain unverified |
| Virtualization | Evaluate Linux/libvirt/KVM nested on EC2 compute hosts | AWS announced expanded family support and lists it in the user guide; its launch-template API reference remains narrower/inconsistent, and Nova-on-EC2 is not validated |
| Tenant networking | Neutron provides tenant networks and isolation | Mechanism, tunnel, address plan, north-south path, and AWS flow rules remain undecided and unverified |
| Production | OpenStack on organization-owned physical data-center infrastructure | Hardware, fabric, storage, facility, and capacity details are not recorded |
| Application boundary | Provider-independent management/API layer where practical; AWS infrastructure operations isolated from tenant VM operations | Existing provider integration remains fixture-backed; design intent is documented, implementation is not established |
| Approval state at the time | Reference design approved for planning; deployment not approved | This historical approval does not define current project scope or authorize implementation |

The OpenStack installation guide lists Keystone, Glance, Placement, Nova, and Neutron as the minimum service set. Nova documents those services as dependencies for basic compute function; database and message-queue requirements apply to the selected service deployment. Cinder and Horizon are follow-on services in the install guide, not mandatory for the first VM-lifecycle slice. See [OpenStack service installation order](https://docs.openstack.org/install-guide/openstack-services.html) and [Nova 2026.1 service requirements](https://docs.openstack.org/nova/2026.1/).

## 3. Staging scope proposed for owner decision

The following is a **recommended scope for review, not an approved deployment specification**:

- Core services: Keystone, Glance, Placement, Nova, Neutron, and the selected service databases/message broker.
- Workload: one non-sensitive Linux CPU image, one test project, small disposable Nova VMs, and basic lifecycle/cleanup.
- Networking: private tenant networks and security-group isolation; no public tenant ingress in the first validation slice.
- Storage: Glance images and Nova ephemeral instance storage. Defer Cinder until a persistent tenant-volume use case and production storage mapping are approved.
- Compute count: two nested-KVM compute hosts if Gate 1 must assess cross-host overlay behavior. A one-compute-host smoke test can check basic lifecycle but cannot validate cross-host Neutron tunnels or host-to-host placement.
- Exclusions for the first slice: GPU, public floating-IP ingress, multi-AZ/high availability, production-scale performance claims, billing/telemetry services, and persistent tenant volumes unless an owner makes them release requirements.

The owner must confirm or change each boundary. If a capability is excluded, the staging acceptance record should say so and avoid claims about it.

## 4. Owner decision register

No choice below is silently settled by this package. Record the decision, approver, date, rationale, and affected acceptance criteria in the release record.

| Decision | Options or information needed | Recommended planning position | Owner / approver | Gate 1 status |
|---|---|---|---|---|
| User/workload scope | User groups, tenant/project model, workload, data classification, expected VM sizes/count/concurrency, SLOs | Use a disposable, non-sensitive CPU VM workload for initial feasibility; define actual limits before sizing | Product/release owner | Open |
| OpenStack release and deployment method | Supported release/distribution, deployment tool, operator, patch/upgrade lifecycle | Choose a supported release and method with a named operator; do not infer from a diagram | Platform operator | Open |
| AWS boundary | Account owner, Region, VPC owner, permitted AZs, administrative access path, policy constraints | Keep API/control-plane hosts private; select the Region and account before account-specific discovery | Cloud/account owner and security | Open |
| EC2 compute candidate | Exact type, size, count, CPU options, nested-virtualization enablement, capacity/quota, purchase/runtime model | Consider only candidates whose exact nested-virtualization support is reconciled and verified for the selected Region/account | Platform operator and budget owner | Open |
| Control-plane topology | Co-located single controller versus separated or redundant roles; failure tolerance | A single controller is acceptable only for a bounded validation with its availability limitation documented | Platform operator and operations owner | Open |
| Compute topology | One or two nested-KVM compute hosts; AZ placement; host-failure acceptance | Two hosts if cross-host Neutron validation is a release objective; one host means cross-host networking remains unvalidated | Product/release owner and budget owner | Open |
| Neutron mechanism and overlay | Supported mechanism (for example OVN or ML2/OVS), encapsulation, agents, tunnel endpoints and ports, network CIDRs, DHCP/metadata | Select only after deployment compatibility and AWS underlay flows are documented; preserve private tenant isolation | OpenStack network operator and security | Open |
| External traffic | Private-only, tenant egress, public ingress, floating IPs, DNS, routing/NAT | Exclude public tenant ingress initially; separately decide whether controlled egress is necessary | Product owner and security | Open |
| MTU and underlay | AWS path MTU for selected instance/network path; overlay overhead; guest MTU and path testing | Derive from the chosen tunnel and measured/verified underlay; do not copy a value from another environment | Network operator | Open |
| Storage | Ephemeral only or Cinder; image store; host disk; volume backend, backup and restore needs | Start ephemeral unless persistent tenant data is a named acceptance requirement; map production storage before including Cinder | Storage/platform operator and product owner | Open |
| Identity and security | Keystone projects/roles/service identities, AWS IAM roles, operator access, secret handling, audit retention, threat model | Private control plane, least privilege, distinct AWS and OpenStack credentials, explicit tenant-isolation tests | Security approver and platform operator | Open |
| Management API scope | Whether a custom API is required; provider adapter; sync/async API behavior; durable app job state | Keep the OpenStack provider boundary; add application persistence/queue only for a demonstrated requirement | Product/API owner | Open |
| Cost and lifecycle | Runtime, cost ceiling, EC2/EBS/network/logging assumptions, tags, expiry, stop conditions, cleanup owner | Produce a Region-specific estimate only after scope and sizing inputs are supplied | Budget owner and operations owner | Open |
| Production correspondence | Physical compute, CPU compatibility, switch/fabric, IPAM, storage, external network, support owners | Record AWS-only differences and production evidence still required | Data-center/platform owner | Open |

## 5. Evidence plan and dependencies

### G1-A — Establish owner inputs

**Inputs:** Product/release owner, cloud/account owner, OpenStack operator, network operator, storage owner, security approver, budget owner, and operations owner.

**Collect:** workload and data classification; intended staging duration; availability/recovery expectations; workload capacity; access model; budget range; cleanup and support ownership.

**Acceptance evidence:** signed/recorded scope; named decision owners; explicit read-only discovery authorization, including which account/Region and permitted calls if account-specific inspection is required. Without that authorization, limit findings to public documentation and label account-specific facts unknown.

### G1-B — Reconcile nested-virtualization eligibility and host capacity

**Public evidence currently available:** AWS's [nested virtualization guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html) says KVM is a supported L1 hypervisor and lists M7i/M7i-flex, M8i variants, C7i/C7i-flex, C8i variants, R7i/R7iz, R8i variants, X8i, I7i, and I7ie families. AWS's June 18, 2026 [What's New announcement](https://aws.amazon.com/about-aws/whats-new/2026/06/nested-virtualization-intel-us-gov-cloud/) confirms expanded 7th- and 8th-generation Intel family support and added U.S. GovCloud Regions. The [LaunchTemplateCpuOptionsRequest reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_LaunchTemplateCpuOptionsRequest.html) still limits support to 8th-generation Intel C8i, M8i, R8i, and their flex variants. This API reference appears stale or incomplete relative to AWS's June announcement; that is an inference from the conflicting AWS sources. The guide and announcement also differ on individual entries (`R7iz`/`I7ie` appear only in the guide; `C7id` appears only in the announcement).

**Required next evidence:**

1. Reconcile the launch-template API reference discrepancy with AWS documentation/support for the exact launch API and feature path intended for the selected account.
2. After authorized read-only discovery, query exact instance-type feature support and regional instance offerings; record date, Region, exact type, nested-virtualization feature, vCPU/RAM, EBS/network limits, quota, and available capacity.
3. Check that launch configuration enables nested virtualization and that the planned Linux/KVM/libvirt stack can expose and use the acceleration interface.
4. Estimate guest demand and reserve CPU/RAM/disk/network headroom for the L1 operating system, Nova compute, Neutron, and observability. Record overcommit assumptions and host-failure limits.

**Acceptance evidence:** exact candidate support is reconciled against current AWS evidence; approved candidates have Region offering and quota evidence plus an explicit capacity-assurance plan or documented lack of a reservation guarantee; a sizing model is tied to approved workload and explicit uncertainty. An instance type being listed for a Region does not prove launch capacity at the required time. This is a planning evidence gate; actual KVM/Nova operation requires a later approved staging validation.

### G1-C — Select and review the Neutron network design

**Required design artifacts:**

- selected Neutron mechanism, tunnel/encapsulation, agent layout, and release compatibility;
- VPC/subnet/address plan, non-overlapping tenant CIDRs, route tables, AWS security-group and network ACL requirements;
- flow matrix for API/control plane, compute-to-compute overlay, DNS/DHCP/metadata, guest east-west traffic, operator access, and any approved north-south traffic;
- MTU calculation from the chosen overlay and verified end-to-end underlay MTU;
- source/destination-check decision for any routing/NAT/firewall host;
- tenant isolation threat model and allowed/denied test cases;
- failure/degraded behavior for tunnel loss, network-agent failure, and host loss.

**Acceptance evidence:** network operator and security approve the diagram and flow matrix; every exposure has a purpose, peer, protocol/port, route and policy owner; CIDRs do not overlap required corporate/data-center networks; the acceptance plan distinguishes same-host from cross-host proof. AWS states that packets using MAC/IP values not assigned to an ENI are dropped and that source/destination checks may need to be disabled for routing/NAT/firewall roles; these are design constraints to account for, not proof a particular Neutron design works. See [AWS EC2 network isolation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/infrastructure-security.html).

### G1-D — Decide image and storage scope

**Collect:** test image format/architecture/boot mode, Glance store and retention, Nova ephemeral disk location/size, EBS host-storage assumptions and performance envelope, and whether persistent tenant volumes are actually required.

If Cinder is selected, record its supported driver/backend, attachment path, failure domain, snapshots/backups, restore exercise, quotas, and production analogue. Cinder volumes backed by staging-specific storage do not establish a production backend.

**Acceptance evidence:** storage owner approves the in-scope model and data-retention/deletion behavior; capacity/performance assumptions and limitations are explicit; production storage gap is recorded.

### G1-E — Complete security and operations review

**Collect:** trust boundaries and data flows; private endpoint reachability; Keystone project/role/service account design; AWS IAM permissions; credential storage/rotation; TLS; patching ownership for L1/L2; audit/log sources and retention; vulnerability response; incident escalation; backup/restore responsibilities; cleanup procedure.

**Acceptance evidence:** security and operations owners approve the threat model and responsibility matrix; no public endpoint or broad network rule is implicit; cleanup identifies every resource class and owner; open risks have mitigation, owner, due date, or explicit scope exclusion.

### G1-F — Complete capacity, quota, and cost model

**Inputs:** selected Region and exact host types/count; runtime; tenant VM demand and concurrency; EBS volume type/size/performance and retention; overlay/control-plane/guest traffic; egress and NAT requirements; public IPv4 and load balancers if any; log/metric retention; backup scope.

**Checks:** AWS account/Region quotas for EC2 vCPU/families, EBS, interfaces/addresses, VPC/network resources and security-group rules; OpenStack project quotas for instances, cores, RAM, networks, ports, security groups, and Cinder resources if enabled. AWS publishes VPC quotas by account and Region; actual account quota and capacity must be checked separately ([AWS VPC quotas](https://docs.aws.amazon.com/vpc/latest/userguide/amazon-vpc-limits.html)).

**Acceptance evidence:** Region-specific estimate with pricing date and assumptions, cost ceiling, alerts/stop conditions, owner, resource expiry, and cleanup evidence plan. Do not calculate or present a number before these inputs are approved.

### G1-G — Map staging evidence to production

Create a portability matrix for the application/API contracts, Keystone/Nova/Neutron workflows, images, ephemeral/persistent storage, deployment automation, secrets, endpoints, network policy, capacity, observability, and runbooks. Mark each item `reusable`, `reconfigure`, `replace`, or `not validated in AWS staging`, with the production owner and evidence needed.

**Acceptance evidence:** the data-center owner confirms the target differences and identifies required production verification for physical CPU/KVM, network fabric/MTU/routing, storage, failure domains, facility/operations, and capacity. AWS staging results must not close these production checks.

## 6. Proposed Gate 1 exit criteria

Gate 1 may be recommended for approval only when all applicable items are recorded:

1. Scope, workload, data classification, tenant model, service owner, SLO/recovery targets, and named approvers are approved.
2. OpenStack release/deployment method and operating ownership are identified.
3. AWS Region/account boundary, read-only discovery authority, candidate capacity, quotas, and nested-virtualization support are evidenced or explicitly block the proposal.
4. Neutron mechanism and packet-flow model are approved, including MTU, isolation, administrative access, and north-south scope.
5. Image, ephemeral storage, and any Cinder requirements/backends are decided.
6. Security, IAM/Keystone, secrets, audit, incident, backup, cleanup, and support responsibilities are reviewed.
7. A capacity/cost model, budget ceiling, resource expiry, and cost/cleanup controls are accepted.
8. AWS-to-data-center portability gaps and production-only verification are owned and scheduled.
9. Capabilities that cannot be verified are excluded from the staging claim or recorded as blockers.
10. Staging resource provisioning remains a separate approval, with exact resources, spend cap, access, rollback, and cleanup scope.

**Gate 1 status after this package:** Open. No owner decisions, account-specific checks, deployment evidence, or feasibility results are inferred from this document.

## 7. Historical next-step sequence — superseded

1. Requirements owner and technical owners review the decision register and select scope/owners; do not infer account, Region, budget, or unresolved network/storage choices.
2. Obtain Gate 0 authorization for any specific read-only AWS account/Region discovery that is required.
3. Gather the approved public/account-specific evidence and fill this package's open fields with date, source, result, and limitations.
4. Complete the detailed Neutron, storage, security, capacity, cost, and data-center portability records.
5. Present the completed Gate 1 decision package for owner approval.
6. Only after Gate 1 acceptance, prepare a separate staging resource request. Gate 1 approval alone does not authorize AWS resource creation.

## Public references

- [AWS: Nested virtualization in Amazon EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html)
- [AWS What's New: Expanded nested virtualization support, June 18, 2026](https://aws.amazon.com/about-aws/whats-new/2026/06/nested-virtualization-intel-us-gov-cloud/)
- [AWS EC2 API: NestedVirtualization launch option](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_LaunchTemplateCpuOptionsRequest.html)
- [AWS: EC2 infrastructure security and network isolation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/infrastructure-security.html)
- [AWS: VPC quotas](https://docs.aws.amazon.com/vpc/latest/userguide/amazon-vpc-limits.html)
- [OpenStack: Minimum service deployment](https://docs.openstack.org/install-guide/openstack-services.html)
- [OpenStack Nova 2026.1: Service dependencies and API](https://docs.openstack.org/nova/2026.1/)
- [OpenStack Neutron 2026.1: ML2 configuration reference](https://docs.openstack.org/neutron/2026.1/configuration/ml2-conf.html)
- [OpenStack Neutron: MTU considerations](https://docs.openstack.org/neutron/latest/admin/config-mtu.html)
- [AWS EC2 pricing](https://aws.amazon.com/ec2/pricing/on-demand/)
- [AWS VPC pricing](https://aws.amazon.com/vpc/pricing/)
