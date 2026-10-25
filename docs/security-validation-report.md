# Security validation report

**Assessment date:** 2026-10-25  
**Scope:** Repository implementation and previously recorded local Task 11 evidence. This is not a fresh host, endpoint, penetration, or staging assessment.

## Result

**Partial; release security acceptance is not met.** The repository has substantial offline authorization, project isolation, error-redaction, backup-protection, and configuration-hardening tests. The current execution shell cannot inspect the systemd service manager or authenticate to the local Keystone endpoint, so the active service exposure, TLS configuration, firewall, live role policy, and audit behavior were not revalidated. No public scan or unauthorised endpoint test was run.

## Evidence reviewed

- `tests/test_real_management.py`, `tests/test_real_tenants.py`, `tests/test_isolation.py`, `tests/test_network_policy.py`, and `tests/test_policy_validation.py` exercise project scope, role checks, denied operations, and tenant boundary behavior with SDK fakes or fixture records.
- `tests/test_api_hardening.py`, `tests/test_rabbitmq_hardening.py`, and `tests/test_apache_legacy_route_hardening.py` exercise bounded configuration changes, backups, syntax gates, allowlisted diagnostics, and preservation of unrelated API routes. These are implementation tests, not evidence that the present host configuration currently matches the reviewed state.
- `tests/test_application_backup.py`, `tests/test_recovery_rehearsal.py`, and `tests/test_wsl_restart.py` cover protected recovery artifacts and isolated restore/restart logic. Operator-supplied Task 11 evidence records a successful isolated restore and WSL restart; this report does not repeat that rehearsal.
- The full local test command `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` passed **522 tests**, with **2 opt-in daemon tests skipped**. Tests are offline; they do not validate a browser session or live service policy.
- The current audit records a missing Horizon package, no billing web endpoint, no live usage source, and an unavailable systemd bus in this shell.

## Controls not validated for release

| Control | Current disposition | Required evidence |
|---|---|---|
| Horizon login, secure cookies, CSRF, HTTPS, TLS renewal | Not deployed or verified | Install/configure on the approved host; test admin and two tenant identities over the intended browser path |
| Live Keystone/service role policy | Historical Task 11 evidence only for the existing core slice | Fresh allow/deny tests for project, role, quota and billing operations against the target release |
| Listening ports, host firewall and ingress | Current shell cannot inspect host services | Operator-collected listener/firewall inventory and approved network-rule review |
| Application audit events and retention | No complete browser/billing audit stream | Verify actor, project, action, target, result, time and correlation ID; test redaction, backup and retention |
| Cinder/Swift access and data isolation | T18 real local volume lifecycle, guest I/O, snapshot-content restore and cleanup passed. Cross-project storage denial and Swift remain unverified. | Dedicated staging backend, two-project positive/negative object/volume tests and independent recovery evidence |
| Secret rotation and external secret storage | No browser service account deployed | Secret-manager design, rotation test and repository scan for the release diff |
| Staging security review | Target and approver are unselected | Gate 1 threat model and independent Gate 3 security acceptance |

## Required follow-up

1. Preserve the current Caracal installation; collect fresh read-only service, port, TLS and firewall evidence from the protected Ubuntu operator shell.
2. Complete T32–T39 before browser/billing authorization can be validated.
3. Run T40's two-project positive and negative cases against real services and archive sanitized responses.
4. Repeat security and secret-handling validation on the separately approved staging release; obtain the named security approver's Gate 3 sign-off.

No credentials, tokens, private keys, or billing data were included in this report. Passing the offline suite does not pass local live security acceptance or a staging gate.

## T18 addendum — 2026-10-26

Operator evidence confirms that local Cinder created a marked volume and Nova's attachment failed during iSCSI login. The daemon response was `02/00`, with login exit 19 and no remaining Nova/Cinder attachments after rollback. The configured initiator offers only SHA3-256/SHA256, while installed TGT 1.0.80 accepts MD5/SHA1. This is a protocol compatibility gap; backend readiness does not establish storage security or a successful volume attachment.

The first compatibility trial used explicit `--loopback-chap-sha1` on the owned loopback node, but attachment failed again. The source review then identified mismatched SHA1 protocol identifiers: Open-iSCSI 2.1.5 uses 6, while TGT 1.0.80 uses 7. The helper now rejects that choice for this version pair. Both versions use 5 for MD5, and a corrected local plan requires explicit `--loopback-chap-md5` for only task-owned nodes at `127.0.0.1:3260`, including a subsequently created snapshot clone. It checks backend and both package versions, listener, resource ownership, absence of an existing session, and active crypto-provider support before changes; preserves the global initiator configuration; and verifies temporary-node removal after detachment. It does not enable MD5 if the active crypto policy rejects it.

MD5 CHAP is a documented legacy compatibility exception confined to these laptop test resources. CHAP does not encrypt the volume data path. The staging backend must meet the approved authentication, encryption, isolation and recovery requirements; this local exception does not pass a staging security gate.

The helper and acceptance guards pass offline tests, including global-file preservation, refusal of unrelated nodes/active sessions, crypto-policy refusal, and output redaction. The operator's final MD5 run passed real attachment, guest write/read, snapshot/clone content recovery and owned cleanup; its protected report is `/root/openstack-poc-acceptance/cinder-volume-acceptance.json`. The source runner also gates final PASS on removal of the temporary node/session, matching global-configuration hashes and restored guest disk inventory. Its reviewed-request guard preserved the previous count and SHA1 preparation history. These are local functional results with an explicit legacy authentication exception. Two-project denial tests, independent backend recovery and staging storage security acceptance remain pending. See [T18](tasks/T18.md); no guard was manually reset.
