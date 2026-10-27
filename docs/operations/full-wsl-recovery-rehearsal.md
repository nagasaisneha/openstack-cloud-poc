# Existing Ubuntu cold backup and reboot rehearsal

**Execution update 2026-10-27:** The user directed continuation under existing backup approval. The checkpoint is prepared and Ubuntu reboot is verified, but the planned backup root is absent and no completed export is evidenced. The stopped retained VM and missing Cinder service still need recovery. Follow the [current handoff](existing-ubuntu-cold-recovery.md#current-continuation-handoff), inspect external reports and reuse the existing checkpoint. Do not repeat preparation/snapshots/VM stop or assume an export exists. No upgrade or backup restore is authorized/performed.

Use only the existing Ubuntu WSL registration. No additional distribution may be created/imported/registered. The sections below describe the original rehearsal plan; the current handoff supersedes their earlier preparation-only status.

The [detailed procedure](existing-ubuntu-cold-recovery.md) quiesces the sole retained demo and its active POC services, creates a protected service-level recovery checkpoint, cleanly stops Ubuntu, exports the distro to a new protected Windows file, then boots the same Ubuntu registration and resumes its services/guest from the checkpoint. The export is an independent backup file and remains unregistered.

A read-only source audit passed: one ACTIVE VM/image; Cinder and Swift responding; no Octavia, external network, router, or floating IP. Fresh actual eight-schema and three OVS/OVN isolated restores passed, as did the application-journal restore. The Linux `--plan` identified the sole guest and 25 active POC units. The Windows export helper syntax, capacity plan and rejection of an unconfirmed export passed. No service, guest, WSL registration, backup file, or host setting changed during those checks. Detailed reports are listed in the [status checklist](../poc-completion-checklist.md) and [T47](../tasks/T47.md).

## What the rehearsal can establish

If executed and passed, it will establish that a fresh external WSL export has a matching SHA256, the original source VHDX stayed unchanged through export, and the **existing Ubuntu installation** cold-boots after a WSL shutdown with its recorded POC services and retained VM recovered. It will measure outage duration for that run.

It will **not** establish that the exported backup can be restored and booted. WSL export creates a backup; booting an imported copy registers another distribution, which is forbidden by the current instruction. There is no reviewed supported method to replace the VHDX of the existing registered distro and boot it in place. Therefore whole-image restore/boot and same-instance OS rollback remain blocked. Do not report the original distro's reboot as a restore test.

The existing cold helper requires zero Cinder volumes; it stops before service/guest changes if a volume exists. Representative nonzero storage and an actual billing ledger still need a separately designed recovery test. Isolated database/job restore results are scoped and do not close those gaps.

## Downtime and risks

The approved window must include stopping the retained guest, stopping 25 observed active POC services, powering down the one Ubuntu distro, exporting about 43.1 GB of VHDX data to Windows, booting the original, and resuming its services and guest. OpenStack APIs, Horizon, Cinder, Swift and the guest will be unavailable. The export duration is not measured; it depends on Windows storage throughput. No downtime estimate is claimed.

The Windows capacity observation recorded about 765 GiB free on C:. The older read-only plan reserved two VHD-sized files (97.0 GB). The helper's capacity formula is now prepared for one export plus 10 GiB headroom (about 53.9 GB using the observed source size), but this revised Windows plan has not been rerun. Recheck immediately before any approved maintenance. `wsl --shutdown` affects the shared WSL utility VM, so check that no distro is running immediately beforehand; the last read-only check saw none. Close tools that might automatically start Ubuntu. Never run a source export while the distro is running.

If any step fails or its result is ambiguous, stop and preserve the original registration and all backups. Do not unregister, import, replace, or repair the original from the backup. The export includes credentials and tenant data; protect it with Windows ACLs and keep it outside Git/shared directories.

## Authorization boundary

Existing backup approval covers this scoped cold export and existing-checkpoint recovery, preserving completed work. The Windows script accepts only a stopped source and creates a new export directory; it does not import or restore a distro. OS/OpenStack upgrade approval remains a later gate after recovery and compatibility review.
