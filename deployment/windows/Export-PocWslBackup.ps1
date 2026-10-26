<#
.SYNOPSIS
Plan or export a NEW protected backup of the existing, already stopped WSL distro.
.DESCRIPTION
Default is read-only planning. -Execute requires an explicit quiescence assertion
and an already stopped distro. Never stops, unregisters, imports or overwrites a
distribution. A checksum-valid export does not prove successful restore or boot.
#>
[CmdletBinding()]
param(
    [ValidateSet('Ubuntu')][string]$Distribution = 'Ubuntu',
    [Parameter(Mandatory = $true)][string]$BackupRoot,
    [switch]$Execute,
    [switch]$ConfirmServicesQuiesced
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-RunningDistributions {
    $names = @(& wsl.exe --list --running --quiet)
    if ($LASTEXITCODE -ne 0) { throw 'WSL running-state observation failed' }
    return @($names | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
}

function Write-NewJson([string]$Path, $Value) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 8))
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}

function Set-PrivateDirectory([string]$Path) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetOwner($identity.User)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($identity.User, (New-Object Security.Principal.SecurityIdentifier('S-1-5-18')),
                      (New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

$items = @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' |
    ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object DistributionName -eq $Distribution)
if ($items.Count -ne 1 -or $items[0].Version -ne 2) { throw 'Expected one existing WSL2 registration' }
$sourceRoot = [IO.Path]::GetFullPath($items[0].BasePath)
$vhdName = 'ext4.vhdx'
if ($items[0].PSObject.Properties.Name -contains 'VhdFileName') { $vhdName = $items[0].VhdFileName }
if ([IO.Path]::GetFileName($vhdName) -ne $vhdName) { throw 'Unexpected registered VHD filename' }
$source = Join-Path $sourceRoot $vhdName
$sourceFile = Get-Item -LiteralPath $source
if (($sourceFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Source VHD reparse point refused' }
if (-not [IO.Path]::IsPathRooted($BackupRoot) -or $BackupRoot.StartsWith('\\')) { throw 'Use an absolute local Windows backup root' }
$root = [IO.Path]::GetFullPath($BackupRoot).TrimEnd('\')
if ($root.Equals($sourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $root.StartsWith($sourceRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Backup must be outside the source distribution storage'
}
$driveRoot = [IO.Path]::GetPathRoot($root)
$drive = New-Object IO.DriveInfo($driveRoot)
if ($drive.DriveType -ne [IO.DriveType]::Fixed -or $drive.DriveFormat -ne 'NTFS') { throw 'Expected local NTFS backup storage' }
# Reserve one independent export file and 10 GiB headroom. The export remains
# unregistered; this procedure never creates a second WSL distribution.
$required = [int64]($sourceFile.Length + 10GB)
$runningNames = @(Get-RunningDistributions)
$running = $runningNames -contains $Distribution
$plan = [ordered]@{
    schema = 1; distribution = $Distribution; action = 'export_existing_distribution_only';
    execution_requested = [bool]$Execute; source_running = $running;
    other_running_distribution_count = @($runningNames | Where-Object { $_ -ne $Distribution }).Count;
    source_vhd_bytes = $sourceFile.Length; backup_drive_free_bytes = $drive.AvailableFreeSpace;
    required_free_bytes = $required; capacity_sufficient = ($drive.AvailableFreeSpace -ge $required);
    services_quiesced_operator_assertion = [bool]$ConfirmServicesQuiesced;
    restore_or_boot_verified = $false; host_upgrade_performed = $false
}
if (-not $Execute) { $plan | ConvertTo-Json -Depth 4; exit 0 }
if (-not $ConfirmServicesQuiesced -or $running) { throw 'Approved maintenance must quiesce services and stop Ubuntu before export; no action performed' }
if ($drive.AvailableFreeSpace -lt $required) { throw 'Insufficient backup capacity' }
# Reject pre-existing reparse points anywhere in the destination path.
$ancestor = $root
while ($ancestor) {
    if (Test-Path -LiteralPath $ancestor) {
        $item = Get-Item -LiteralPath $ancestor
        if (-not $item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw 'Destination non-directory or reparse point refused'
        }
    }
    $ancestor = [IO.Path]::GetDirectoryName($ancestor)
}
if (-not (Test-Path -LiteralPath $root)) {
    New-Item -ItemType Directory -Path $root | Out-Null
    Set-PrivateDirectory $root
}
$trustedSids = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18', 'S-1-5-32-544')
$writeRights = [Security.AccessControl.FileSystemRights]'Write,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
foreach ($rule in (Get-Acl -LiteralPath $root).Access) {
    $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $writeRights) -and $sid -notin $trustedSids) {
        throw 'Existing backup root permits untrusted writes; select a private root'
    }
}
$destination = Join-Path $root ('wsl-recovery-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $destination | Out-Null
Set-PrivateDirectory $destination
$before = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
if ((Get-RunningDistributions) -contains $Distribution) { throw 'Source started during backup preparation; private partial directory retained' }
$output = Join-Path $destination 'Ubuntu.vhdx'
& wsl.exe --export $Distribution $output --vhd
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output)) { throw 'Export failed; preserve partial evidence' }
if ((Get-RunningDistributions) -contains $Distribution) { throw 'Source unexpectedly running; export not accepted' }
$after = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
if ($before -ne $after) { throw 'Source VHD changed; export not accepted' }
$backupHash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash
if ((Get-RunningDistributions) -contains $Distribution) { throw 'Source started during final backup verification; export not accepted' }
$report = [ordered]@{
    schema = 1; created_at = [DateTime]::UtcNow.ToString('o'); distribution = $Distribution;
    export_file = 'Ubuntu.vhdx'; export_sha256 = $backupHash;
    export_bytes = (Get-Item -LiteralPath $output).Length; source_vhd_sha256 = $before;
    source_unchanged = $true; exported_from_stopped_distribution = $true;
    services_quiesced_operator_assertion = $true; restore_or_boot_verified = $false;
    source_registered_storage_path = $sourceRoot; source_registration = $items[0].PSChildName;
    no_unregistration_or_import_performed = $true
}
Write-NewJson (Join-Path $destination 'export-report.json') $report
[ordered]@{ status = 'export_created_and_hashed'; private_directory = $destination;
            restore_or_boot_verified = $false; source_unchanged = $true } | ConvertTo-Json
