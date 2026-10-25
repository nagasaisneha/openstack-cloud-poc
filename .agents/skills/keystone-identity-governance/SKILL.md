---
name: keystone-identity-governance
description: Design or audit Keystone authentication, project-scoped authorization, roles, service identities, and tenant isolation.
---

# Keystone identity and governance

Treat identity and scope as security-critical controls. Use release-appropriate Keystone documentation and the deployment's actual policy configuration; defaults can differ.

## Workflow

1. Identify actor, project/domain/system scope, requested action, credential source, token lifetime, and trust boundary. Distinguish authentication (who) from authorization (what, where).
2. Build an action-to-role/scope matrix. Apply least privilege, deny unknown operations, and test cross-project access explicitly. Avoid broad admin credentials for application workflows.
3. Handle service users, federation, MFA, token validation, rotation, revocation, and audit trails according to the deployment and threat model.
4. Ensure authorization happens before returning sensitive resource metadata or performing mutations. Avoid logging tokens, passwords, authorization headers, or credential-bearing URLs.
5. Verify allowed and denied cases, including unknown users, missing roles, disabled projects, stale tokens, and scope mismatch. Label fixture identity as simulated, never as Keystone evidence.

Do not create identities, change assignments, contact Keystone, or alter policy without explicit approval naming the scope and intended change. Make the impact of role changes and rollback plan clear.
