# Existing Ubuntu cold backup and reboot procedure

Prepared 2026-10-27. **Execution update:** Existing backup approval covers continuation. Service quiescence passed, but the Windows worker failed on a source-VHDX sharing violation before invoking export. It left one empty destination directory and no backup. The seven original bundle hashes still pass; guest SHUTOFF, no start intent. Ubuntu was reopened after failure and its services restarted. Preserve the failed attempt and follow the corrected handoff below. No upgrade is authorized.

## Current continuation handoff

Reuse /root/openstack-poc-acceptance/cold-recovery-qg9_6571/checkpoint.json, phase prepared_for_operator_poweroff. The retained VM is SHUTOFF with no start intent. Of 27 originally active services, 26 restarted; Cinder volume is inactive because Swift mounted its image on /dev/loop0, reserved by unchanged Cinder configuration. Database/OVSDB, guest disk and application-journal snapshots are already prepared. Do not rerun preparation, snapshots or VM stop.

The failed Windows attempt remains at C:\Users\ACER\AppData\Local\OpenStackPocContinuation-20261027-4c29e0d1; its Linux report is quiesced_for_missing_export. Do not relaunch or reset it. Preserve the empty C:\OpenStackPocBackups\wsl-recovery-8b54ec8a118644a886948fbf61895209 directory. [Failure observations](../evidence/cold-export-lock-failure-2026-10-27.json) supersede the earlier preparation-only observations.

The corrected Windows continuation lives at C:\Users\ACER\AppData\Local\OpenStackPocContinuation-20261027-unlock-9b821e65, with state.json, export.log, linux-quiesce.log and linux-resume.log. Its protected Linux report is /root/openstack-poc-acceptance/cold-export-continuation-20261027-unlock-9b821e65/report.json. Inspect these records before any action. An existing state file forbids relaunch.

After read-only plans and a clean repository check, this worker pauses only restarted original services and powers Ubuntu off. Once every distribution is observed stopped, it shuts down the shared WSL utility VM before directly reading its VHDX. A bounded read-only file-open gate retries only sharing violations for up to 60 seconds, and hashing denies concurrent writes. Source hash success is required before creating a new export destination. The worker preserves the old directory, independently verifies the new export hash, checks the original registration/storage path, then boots that registration and resumes the original checkpoint. Keep Ubuntu stopped throughout export/hash verification; opening it or issuing agent tools then can invalidate the export.

The failed worker's empty running-distribution list did not establish that the VHDX was released. Microsoft directs users to [shut WSL down before direct VHDX access](https://learn.microsoft.com/en-us/windows/wsl/wsl2-mount-disk). The exact lock-owning process at failure was not established. This corrective shutdown occurs after renewed service quiescence; snapshots and VM stop are not repeated, and power/export operations are never automatically retried.

After verified export, a bounded storage correction may stop only the six original Swift services and cleanly unmount their owned image, start Cinder's existing attachment dependency, verify the original PV/VG, and remount the same Swift image on a free loop device. No persistent configuration changes, formatting, restore or deletion occur. Foreign devices and Cinder volumes/snapshots block correction. Original checkpoint resume starts only missing original services and the retained VM once. Completion also requires KVM, pinned-key guest identity/network/MTU/CPU checks, service health and unchanged bundle hashes/packages. Future boot ordering remains a separate issue.

Require Windows state completed with independent hash verification, Linux report verified and original checkpoint source_resumed. A failed or ambiguous intent requires inspection; no automatic retries or replacement backup. This does not prove full-image restore, same-instance OS rollback, representative nonempty storage/ledger recovery, upgrade or final POC acceptance.

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

3. Before stopping Ubuntu, copy `Export-PocWslBackup.ps1` from the repository into a protected Windows-local folder. Loading it through `\\wsl.localhost\Ubuntu\...` after poweroff can start Ubuntu. In Windows PowerShell, first verify Ubuntu is running and every other WSL distribution is stopped. This check must allow the expected running Ubuntu; it must not require an empty running list before poweroff:

   ```powershell
   $running = @(& wsl.exe --list --running --quiet)
   if ($LASTEXITCODE -ne 0) { throw 'WSL running-state observation failed' }
   $runningNames = @($running | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
   $otherRunning = @($runningNames | Where-Object { $_ -ne 'Ubuntu' })
   if ($runningNames -notcontains 'Ubuntu' -or $otherRunning.Count -ne 0) { throw 'Expected Ubuntu only to be running' }
   & wsl.exe --distribution Ubuntu --user root --exec /sbin/poweroff
   if ($LASTEXITCODE -ne 0) { throw 'Ubuntu poweroff request failed' }
   $deadline = (Get-Date).AddMinutes(2)
   do {
       Start-Sleep -Seconds 2
       $running = @(& wsl.exe --list --running --quiet)
       if ($LASTEXITCODE -ne 0) { throw 'WSL running-state observation failed after poweroff' }
       $runningNames = @($running | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
       if ($runningNames.Count -eq 0) { break }
   } while ((Get-Date) -lt $deadline)
   if ($runningNames.Count -ne 0) { throw 'A WSL distribution is still running; do not export or shut down WSL' }
   ```

   Wait for it to stop. Do not shut down WSL if another distribution starts. The Windows export helper must independently observe Ubuntu stopped before proceeding.

4. Run the copied Windows-local helper first in read-only mode, inspect source size and free NTFS capacity, and use a new protected destination outside WSL. Confirm that **all** WSL distributions are stopped; the helper now refuses export if any distribution is running. Only the approved export may use its `-Execute -ConfirmServicesQuiesced` options:

   ```powershell
   .\Export-PocWslBackup.ps1 -BackupRoot C:\OpenStackPocBackups
   .\Export-PocWslBackup.ps1 -BackupRoot C:\OpenStackPocBackups -Execute -ConfirmServicesQuiesced
   ```

   Require a new export report, source VHDX unchanged before/after export, export SHA256 recorded, and a second independently calculated export hash matching. Keep the VHDX and report in the protected directory. Do not run any WSL import command. This verifies the export's integrity only.

5. With no WSL distribution running and the independent export safely outside WSL, shut down the **shared utility VM** and boot only the original registration. Verify the registration still points to its original storage path before starting it.

   ```powershell
   $running = @(& wsl.exe --list --running --quiet)
   if ($LASTEXITCODE -ne 0 -or @($running | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ }).Count -ne 0) { throw 'A WSL distribution is running' }
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
