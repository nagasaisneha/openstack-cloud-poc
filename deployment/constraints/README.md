# Reproducible constraints and security review

The two `*-upper-2026-10-26.txt` files preserve upstream OpenStack upper-constraint snapshots. Do not edit them to conceal a dependency exception. SHA256:

| Snapshot | SHA256 |
|---|---|
| Epoxy | `bf8d5397f8d20d4895d6f93f9b55c5a07d686c75f944391780e95a8e43060bcb` |
| Gazpacho | `778a2873b3aee59110af8d00aa6f0b659fb98e51a86650840e1a8604719a2f37` |

The [official constraints service](https://releases.openstack.org/constraints/) supplies release constraints. These are compatibility baselines, not a promise that every old dependency remains free of published vulnerabilities. The 2026-10-27 review found matches in new locks compiled against these baselines; the existing Kolla lock had no matches in that query.

`security-overrides-2026-10-27.txt` replaces affected cryptography, idna, lxml, msgpack, PyJWT, requests, urllib3, WebOb and Eventlet versions. cffi advances to satisfy the new cryptography dependency. This is a project security overlay outside the original upstream test combination; isolated tests must pass and live authentication/storage acceptance remains mandatory. It is not permission to change installed services.

Reproduce with **uv 0.13.5**, using Python 3.12 for Swift and the universal Python >=3.10 test lock:

```bash
python3 -B scripts/render_security_constraints.py --series gazpacho --output /tmp/poc-gazpacho-effective.txt
python3 -B scripts/render_security_constraints.py --series epoxy --output /tmp/poc-epoxy-effective.txt
uv pip compile --universal --python-version 3.10 --generate-hashes --no-header --no-build -c /tmp/poc-gazpacho-effective.txt -o requirements-test.lock requirements-test.in
uv pip compile --python-version 3.12 --generate-hashes --no-header --no-build -c /tmp/poc-gazpacho-effective.txt -o deployment/swift-maintained/requirements.lock deployment/swift-maintained/requirements.in
uv pip compile --python-version 3.12 --generate-hashes --no-header --no-build -c /tmp/poc-epoxy-effective.txt -o deployment/swift-maintained/epoxy-requirements.lock deployment/swift-maintained/epoxy-requirements.in
python3 -B scripts/audit_dependency_advisories.py
```

Use a temporary virtual environment for compilation/verification. Install locks with `--require-hashes --only-binary :all:`. Never run global pip against the working OpenStack installation. A lock freezes artifacts, not future advisory status; rerun the query before each release and refresh only through review.

The scanner sends only public PyPI names/versions to the [OSV batch API](https://google.github.io/osv.dev/api/). Incomplete, paginated, malformed or unavailable results fail closed. All four locks, including the intermediate Swift runtime and staging Kolla tools, are scanned. Raw findings remain in the report.

One exact, expiring disposition covers two aliases for **Swift 2.35.5 only**: GHSA-h8rc-w822-q8vq / PYSEC-2026-4173. [OSSA-2026-024](https://security.openstack.org/ossa/OSSA-2026-024.html) identifies the Epoxy fix in 2.35.3; [Swift release notes](https://docs.openstack.org/releasenotes/swift/2025.1.html) confirm the backport. The aggregated range incorrectly includes later Epoxy versions below 2.37.2. Signed source/module comparison and real Gatekeeper filtering are checked separately. The disposition expires 2026-11-26 and does not suppress other versions/advisories. A reviewed match is never reported as “no published matches.” No published matches or reviewed backports constitute a complete security assessment.
