---
name: cloud-security-engineering
description: Threat-model, review, and harden cloud hosting systems, APIs, tenant boundaries, secrets, and operational controls.
---

# Cloud security engineering

Reason from assets, actors, trust boundaries, attack paths, and impact. Prioritize controls by risk and evidence; avoid security theater and unapproved active probing.

## Workflow

1. Identify protected assets, data sensitivity, tenant boundaries, privileged actors, entry points, dependencies, and attacker capabilities.
2. Trace authentication, authorization, network reachability, secret flows, control-plane access, build/deploy supply chain, and audit evidence.
3. Assess threats such as cross-tenant disclosure, privilege escalation, SSRF, credential leakage, exposed management endpoints, insecure defaults, and resource exhaustion.
4. Recommend least-privilege controls, TLS, secret rotation, secure defaults, input validation, isolation, patching, logging, and incident procedures appropriate to the threat.
5. State residual risk and verify the control with safe evidence. Separate a design claim from a tested property and a production attestation.

Do not print secrets, perform external scans, exploit systems, alter access policy, or expose services without explicit authorization. Scope security testing and avoid collecting unnecessary tenant data. Consult current OpenStack security guidance and the deployment's actual policy.
