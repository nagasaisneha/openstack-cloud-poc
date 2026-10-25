---
name: cloud-repository-engineering
description: Maintain cloud-platform repositories with safe configuration, secret hygiene, documentation, version control, and reviewable task records.
---

# Cloud repository engineering

Preserve the user's work and make changes traceable. Repository workflow is part of platform reliability and security.

## Workflow

1. Read repository instructions and current Git status before editing. Identify unrelated modifications and preserve them.
2. Keep environment-specific values and secrets out of tracked files. Use safe examples, explicit config schemas, and ignore rules; never print credential-bearing URLs or tokens.
3. Make focused changes that match the authorized task. Update docs with actual behavior, prerequisites, verification, limitations, and cleanup.
4. Review the full diff for accidental secrets, generated files, unrelated edits, and unsupported claims. Run only task-authorized verification and report exact outcomes.
5. Stage only approved paths. Commit/push only when authorized and configured; never invent remotes or claim success without evidence.

Do not reset, clean, overwrite, stage broadly, or remove user files. Treat generated state and local credentials as sensitive. Distinguish planned, implemented, tested, simulated, and production-verified work.
