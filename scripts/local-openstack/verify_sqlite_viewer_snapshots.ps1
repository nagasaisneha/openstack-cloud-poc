# Verify completed SQLite exports, normalize owner, and mark snapshots read-only.
# No live databases, service settings, or permissions outside this folder change.
#Requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$Directory)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$allowed = @($identity.User.Value, 'S-1-5-18')
$resolved = [IO.Path]::GetFullPath($Directory).TrimEnd('\')
if ((Split-Path $resolved) -ne $env:LOCALAPPDATA -or
    (Split-Path $resolved -Leaf) -notmatch '^OpenStackPocSnapshots-\d{8}T\d{6}Z-[0-9a-f]{8}$' -or
    ((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Expected the prepared private Windows snapshot directory'
}
function Assert-PrivateAcl([string]$Path, [bool]$IsDirectory) {
    $acl = Get-Acl -LiteralPath $Path
    $rules = @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if ($IsDirectory -and !$acl.AreAccessRulesProtected) { throw 'Directory inherits permissions' }
    if ($rules.Count -ne 2 -or @($rules | Where-Object {
        $_.IdentityReference.Value -notin $allowed -or $_.AccessControlType -ne 'Allow' -or
        $_.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl
    }).Count) { throw 'Unexpected snapshot access permissions' }
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $identity.User.Value) {
        # WSL sudo exports can use the Windows token's default owner group.
        # Preserve the verified DACL and assign ownership to the operator.
        $acl.SetOwner($identity.User)
        Set-Acl -LiteralPath $Path -AclObject $acl
        $owner = (Get-Acl -LiteralPath $Path).GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($owner -ne $identity.User.Value) { throw 'Windows snapshot ownership verification failed' }
    }
}
Assert-PrivateAcl $resolved $true
$manifest = Get-Content -LiteralPath (Join-Path $resolved 'snapshot-manifest.json') -Raw | ConvertFrom-Json
$expected = @('usage.sqlite','billing.sqlite','application-jobs.sqlite')
if ($manifest.method -ne 'sqlite3.Connection.backup' -or
    !$manifest.live_sources_opened_read_only -or !$manifest.source_identity_and_permissions_unchanged -or
    $manifest.snapshots.Count -ne 3 -or (Compare-Object $expected @($manifest.snapshots.filename))) {
    throw 'Export manifest verification failed'
}
$verified = @(foreach ($entry in $manifest.snapshots) {
    $path = Join-Path $resolved $entry.filename
    $file = Get-Item -LiteralPath $path
    Assert-PrivateAcl $path $false
    if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $file.Length -ne $entry.bytes -or $entry.integrity_check -ne 'ok' -or !$entry.standalone -or
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) {
        throw 'Snapshot file verification failed'
    }
    foreach ($suffix in @('-wal','-shm','-journal')) {
        if (Test-Path -LiteralPath ($path+$suffix)) { throw 'Unexpected snapshot sidecar' }
    }
    $stream = [IO.File]::OpenRead($path)
    try {
        $header = New-Object byte[] 16
        if ($stream.Read($header,0,16) -ne 16 -or
            [Text.Encoding]::ASCII.GetString($header) -ne "SQLite format 3`0") {
            throw 'Invalid SQLite file header'
        }
    } finally { $stream.Dispose() }
    [IO.File]::SetAttributes($path, $file.Attributes -bor [IO.FileAttributes]::ReadOnly)
    [ordered]@{path=$path;bytes=$file.Length;integrity_check='ok';sha256=$entry.sha256;
        acl_operator_and_system_only=$true;read_only=$true;standalone=$true}
})
[ordered]@{verified_at=[DateTime]::UtcNow.ToString('o');windows_directory=$resolved;
    directory_acl_protected=$true;files=$verified;
    monitored_services_unchanged=$manifest.monitored_services_unchanged} | ConvertTo-Json -Depth 5
