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
| Cinder/Swift access and data isolation | Services/backends not established locally | Dedicated staging backend, two-project positive/negative object/volume tests and recovery evidence |
| Secret rotation and external secret storage | No browser service account deployed | Secret-manager design, rotation test and repository scan for the release diff |
| Staging security review | Target and approver are unselected | Gate 1 threat model and independent Gate 3 security acceptance |

## Required follow-up

1. Preserve the current Caracal installation; collect fresh read-only service, port, TLS and firewall evidence from the protected Ubuntu operator shell.
2. Complete T32–T39 before browser/billing authorization can be validated.
3. Run T40's two-project positive and negative cases against real services and archive sanitized responses.
4. Repeat security and secret-handling validation on the separately approved staging release; obtain the named security approver's Gate 3 sign-off.

No credentials, tokens, private keys, or billing data were included in this report. Passing the offline suite does not pass local live security acceptance or a staging gate.
