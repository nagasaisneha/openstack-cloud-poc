# Existing Ubuntu cold backup and reboot procedure

Prepared 2026-10-27. **Procedure preparation only. Maintenance, poweroff, export, recovery and upgrades have not been authorized or run by this procedure.** It operates only on the already registered `Ubuntu` WSL distribution containing the POC. The independent VHDX export is a backup file; this procedure never imports, registers, clones, or boots it as a distribution.

## What this procedure can verify

It can verify a fresh whole-distro export's checksum and source stability, then cleanly reboot the **original registered Ubuntu** and check its OpenStack services, original guest, and existing protected recovery checkpoint. The existing repo's cold-recovery helper rehearses matched service/database snapshots before shutdown and resumes the same cloud after the reboot.

It cannot boot the exported backup file or restore that file into the original registration. Microsoft documents WSL export as backup creation and import as registration; replacing the registered distro or its VHDX has no verified safe in-place restore method here. Under the no-additional-distribution constraint, full WSL restore-and-boot acceptance remains **unverified and blocked**. A checksum-valid backup plus a successful reboot of the still-installed original proves backup integrity and original boot readiness, not that the backup itself can be restored.

Earlier scoped rehearsals passed: actual eight-SQL/three-OVSDB isolated restore and application-journal restore. They do not overwrite live databases or prove restoration of the whole WSL image. Cinder's current source inventory has no volumes; the cold helper refuses to proceed if any appear. Full nonzero Cinder/Swift data and deployed billing-ledger recovery remain separate acceptance gaps.

## Downtime and risks

The approval window must cover stopping the sole retained demo VM and 25 observed active POC services, copying the VHDX to external Windows storage, then booting the original distribution and its services/VM. During this period OpenStack APIs, Horizon, Swift, Cinder, and the demo guest are unavailable. All WSL services stop during the WSL shutdown. No duration has been measured; it depends on disk-copy speed and service/guest readiness. Do not schedule customer work in this window.

The Windows `wsl --shutdown` command affects the shared WSL utility VM. The last read-only plan saw no other running WSL distributions; repeat that observation immediately before shutdown and stop if anything else is running. Save Windows/WSL work and close software that auto-starts Ubuntu. The export needs approximately 43.1 GB plus 10 GiB headroom (about 53.9 GB using the observed source size); recheck free space and the exact VHDX size on the day.

If preparation, poweroff, export hashing, boot, service readiness, or guest recovery fails, stop. Keep the original registration and all backups intact; do not unregister, replace, import, or repair the original from the export as part of this procedure. Preserve the protected failure report for review. Exported WSL data contains OpenStack credentials and tenant data; keep its Windows ACL restricted and never place it in Git or a shared folder.

## Preparation already completed

- Protected source API inventory passed: one ACTIVE VM and image, Cinder and Swift responding; Octavia, external network, router, and floating IP absent.
- Eight service schemas plus all three OVS/OVN databases were freshly restored in isolated temporary processes. The actual application-journal database was independently backed up and restored.
- The Linux cold plan found the retained guest and 25 active POC services without stopping them. It refuses foreign volumes/databases and records stop/start intent; its maintenance paths have not been executed.
- The Windows export helper's PowerShell syntax, read-only capacity plan, and rejection of an export request without its maintenance confirmation passed before its capacity formula was changed from two VHD-sized files to one export plus 10 GiB. The revised Windows capacity plan has not been rerun. No export or ACL mutation occurred.

See the [single status checklist](../poc-completion-checklist.md), [T47 evidence](../tasks/T47.md), and [maintained-release service matrix](../architecture/service-upgrade-matrix.md).

## Commands for a separately approved window

The following are exact procedure steps, **not authorization to run them now**. Recheck the repository is committed and clean before the maintenance window. Save all stdout and protected report paths. Do not paste credentials.

1. In the existing Ubuntu terminal, rerun the read-only source plan and confirm it still identifies the same sole guest, exactly the known POC service set, and zero volumes or foreign databases:

   ```bash
   sudo --preserve-env=WSL_DISTRO_NAME /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/prepare_cold_recovery.py --plan
   ```

2. If the plan is unchanged and the maintenance window is approved, prepare the protected service-level recovery checkpoint. This stops the retained guest and its recorded active POC services, saves eight SQL schemas, three OVS/OVN databases, guest disk, and application journal; then it quiesces the remaining recorded infrastructure. It does not power off or upgrade Ubuntu.

   ```bash
   sudo --preserve-env=WSL_DISTRO_NAME /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/prepare_cold_recovery.py --prepare --confirm-recovery-maintenance
   ```

   Require `prepared_for_operator_poweroff`. Stop on unknown workloads, volumes, databases, broker consumers/messages, checksum errors or failed service stops. Record the precise `checkpoint.json` path. Do not replay `--prepare` after any partial failure; inspect the checkpoint and use the guarded resume if the original remains available.

3. Before stopping Ubuntu, copy `Export-PocWslBackup.ps1` from the repository into a protected Windows-local folder. Loading it through `\\wsl.localhost\Ubuntu\...` after poweroff can start Ubuntu. In Windows PowerShell, verify **no** WSL distributions are running, then cleanly power off the existing distribution:

   ```powershell
   $running = @(& wsl.exe --list --running --quiet)
   if ($LASTEXITCODE -ne 0 -or @($running | Where-Object { ($_ -replace "`0", '').Trim() }).Count -ne 0) { throw 'A WSL distribution is running' }
   & wsl.exe --distribution Ubuntu --user root --exec /sbin/poweroff
   ```

   Wait for it to stop. Do not shut down WSL if another distribution starts. The Windows export helper must independently observe Ubuntu stopped before proceeding.

4. Run the copied Windows-local helper first in read-only mode, inspect source size and free NTFS capacity, and use a new protected destination outside WSL. Only a later separately approved export may use its `-Execute -ConfirmServicesQuiesced` options:

   ```powershell
   .\Export-PocWslBackup.ps1 -BackupRoot C:\OpenStackPocBackups
   .\Export-PocWslBackup.ps1 -BackupRoot C:\OpenStackPocBackups -Execute -ConfirmServicesQuiesced
   ```

   Require a new export report, source VHDX unchanged before/after export, export SHA256 recorded, and a second independently calculated export hash matching. Keep the VHDX and report in the protected directory. Do not run any WSL import command. This verifies the export's integrity only.

5. With no other WSL distribution running and the independent export safely outside WSL, shut down the **shared utility VM** and boot only the original registration. Verify the registration still points to its original storage path before starting it.

   ```powershell
   $running = @(& wsl.exe --list --running --quiet)
   if ($LASTEXITCODE -ne 0 -or @($running | Where-Object { ($_ -replace "`0", '').Trim() }).Count -ne 0) { throw 'A WSL distribution is running' }
   & wsl.exe --shutdown
   if ($LASTEXITCODE -ne 0) { throw 'WSL shutdown failed' }
   & wsl.exe --distribution Ubuntu --user sneha --exec /bin/true
   if ($LASTEXITCODE -ne 0) { throw 'Original Ubuntu boot failed' }
   ```

   This reboots the existing distro; it does not boot or restore the backup. A systemd-started service may come up before the explicit recovery command. Do not retry power actions after an ambiguous result.

6. In that same Ubuntu distribution, resume the checkpoint exactly once:

   ```bash
   sudo --preserve-env=WSL_DISTRO_NAME /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/prepare_cold_recovery.py --resume /root/openstack-poc-acceptance/cold-recovery-REPLACE_WITH_SAVED_ID/checkpoint.json
   ```

   Replace the placeholder with the exact checkpoint ID. Require the script's `source_resumed` result and checks for the recorded cloud, Cinder and Swift services and retained VM. Confirm same Ubuntu registration, same OpenStack resource IDs, VM ACTIVE/KVM/guest reachability, and API catalog/auth. Compare the protected checkpoint bundle hashes and the independent Windows export hash. Preserve any failure report; do not replay an uncertain VM start.

## Verification limits and remaining release gate

Passing these steps establishes a cold export with matching checksums and that the **existing Ubuntu installation** rebooted with its recorded POC services and retained guest. It does not verify restoration or boot of the exported VHDX, full same-instance OS rollback, Cinder volume or Swift object recovery from nonempty data, a deployed billing-ledger restore, broker version migration, OpenStack schema migration, staging, or the mandatory Octavia/customer workflow. No RPO/RTO can be claimed before timings and an actual recovery point are recorded.

The full-upgrade approval gate must stay blocked until the owner accepts these recovery limits or provides a supported method to restore and boot the independent backup while keeping this no-additional-distribution constraint. Do not silently treat the existing-distro reboot as a backup restore test.
