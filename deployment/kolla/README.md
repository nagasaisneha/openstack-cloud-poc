# Kolla Ansible staging deployment package

This package is a target-specific staging deployment candidate, separate from the existing Ubuntu 22.04 WSL2 / Caracal POC. It does not provision infrastructure, install packages, change host state, generate secrets, or deploy OpenStack by itself.

## Version and host baseline

- OpenStack: 2026.2 Hibiscus.
- Kolla Ansible: 23.0.0.
- Ansible Core: 2.20.10.
- Initial host candidate: dedicated Ubuntu 24.04 LTS nodes.
- Kolla's quick start documents 8 GiB RAM, two network interfaces, and 40 GiB disk as minimum deployment host requirements. These are minimums, not production sizing. Size control, compute, storage, monitoring, and application resources from approved workload and availability requirements.
- The [Kolla 2026.2 support matrix](https://docs.openstack.org/kolla-ansible/2026.2/user/support-matrix.html) lists Ubuntu Noble, Rocky Linux 10 and Debian Trixie host support. The OpenStack 2026.2 release is maintained. Ubuntu 24.04 is selected as a design candidate; final choice belongs to Gate 0/1.

Kolla's latest release images are published for evaluation. Before a staging release, curate and scan images, pin their immutable digests, verify their source/signatures, and maintain a tested rollback set. Do not treat floating tags or unreviewed upstream images as production-ready artifacts.

At this review, the [Kolla Ansible 23.0.0 PyPI files](https://pypi.org/project/kolla-ansible/23.0.0/) did not publish a PyPI provenance attestation. The lock pins artifact hashes, which checks downloaded bytes but does not independently authenticate the publisher. Before Gate 2, verify the signed upstream Kolla release tag and source/package correspondence through the OpenStack release process, and record the verification evidence. If it cannot be verified, build from the reviewed signed source tag or select an artifact with verifiable provenance.

## Files

- `lock-tools.in` / `lock-tools.lock`: pinned uv lock compiler and its hash-locked dependencies.
- `requirements.in`: exact top-level deployment tool versions. Generate, review, and commit a full transitive, hash-locked `requirements.lock` on Python 3.12 before a real deployment; the lock is software metadata and contains no secrets.
- `inventory/multinode.example.ini`: topology example with RFC 5737 documentation addresses. Replace it only with Gate 0/1 approved hostnames and addresses in the ignored `inventory/local.ini`.
- `globals.example.yml`: secret-free Kolla configuration template. Copy to protected `/etc/kolla/globals.yml` after replacing each `REPLACE_*` value.
- `globals.extended.example.yml`: optional Cinder, Swift, metering, and monitoring service profile. Resolve every storage/backend and resource-budget decision before enabling.
- `preflight.yml`: read-only Ansible assertions for OS, memory, interfaces, storage mount, review/change IDs and optional-service decisions. It never installs or modifies a service.

Kolla passwords, clouds.yaml, private inventories, generated Swift rings, TLS material, image digests, and local configuration belong in protected deployment storage and are excluded by `.gitignore`. Use a dedicated deployment account and SSH agent/key provisioned by the operator. Never copy administrative cloud credentials into a repository file.

## Review and execution flow

1. At Gate 0, approve the target, operators, workload, data classification, network ownership, budget, and change process.
2. At Gate 1, settle node roles/counts, addressing/VIPs, DNS/NTP/MTU, control-plane HA, compute headroom, storage hardware and drivers, TLS, image provenance, SLOs, backup and rollback. Replace TEST-NET addresses with approved values in ignored local files.
3. Create a Python 3.12 virtual environment on a dedicated deployment host. Install the pinned lock compiler with `pip install --require-hashes -r lock-tools.lock`; compile `requirements.in` with `uv pip compile --python-version 3.12 --generate-hashes --output-file requirements.lock requirements.in`, review and commit the lock, then install with `pip install --require-hashes -r requirements.lock`. Archive the lock plus software bill of materials with the candidate. Verify Kolla release tag/package and image provenance before use.
4. Copy the official inventory skeleton from the pinned Kolla package, then add the reviewed node assignments. Do not assume the abbreviated example is a complete Kolla inventory for every topology.
5. Copy the example global configuration into `/etc/kolla`, set permissions, review it, and generate `passwords.yml` locally with Kolla's generator. Keep both files outside Git.
6. Run the read-only preflight against every target host and require all checks to pass, for example `ansible-playbook -i inventory/local.ini -e @/etc/kolla/globals.yml preflight.yml`. Run Kolla's own prechecks before any bootstrap/deploy operation.
7. Deploy only after the external change record explicitly approves the exact target and actions. Never run these playbooks against the existing WSL POC as a shortcut.
8. Capture immutable software/image/config identifiers; run service, tenant, storage, metering, security, recovery and application smoke tests; archive sanitized evidence.

Kolla bootstrap/deploy and package changes mutate hosts. They are deliberately not invoked in this workspace. The production upgrade path, rollback, and data migration must be tested on staging first; this package does not declare an in-place Jammy/Caracal upgrade safe.
