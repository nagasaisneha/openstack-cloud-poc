---
name: cloud-infrastructure-automation
description: Build or review repeatable, idempotent cloud deployment and configuration automation with safe plans, rollout, and rollback.
---

# Cloud infrastructure automation

Prefer declarative, reviewable changes with predictable reruns. Treat automation as a production change system, not a shortcut around authorization.

## Workflow

1. Establish desired state, authoritative inventory, environment boundaries, credentials, resource ownership, supported tool/provider versions, and approval scope.
2. Design idempotent operations, explicit dependencies, drift handling, bounded retries, timeouts, and safe handling of partial failure. Protect state and secrets.
3. Separate plan/preview from apply. Show the exact resources and operations affected, expected cost/quota impact, and cleanup or rollback path.
4. Use staged rollout, canary or small-scope validation, health gates, and clear stop conditions for high-blast-radius changes.
5. Verify the resulting state from independent evidence and keep versioned configuration, logs, and change records sanitized.

Use Heat, Terraform, Ansible, or other tools only when compatible and justified. Never run apply, install software, reconfigure hosts, or mutate cloud resources without explicit task-specific approval. Do not store credentials in state, Git, logs, or artifacts.
