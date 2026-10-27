# Native Windows acceptance for the existing Ubuntu's local-only guest access.
# Captures credentials in memory only; writes tenant keys only into an ACL-private
# current-user directory. Never prints private keys or changes Windows routes.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$base = '/root/openstack-poc-acceptance/browser-poc-20261027'
$runtime = "$base/continuation-20261028"
$directory = Join-Path $env:LOCALAPPDATA 'OpenStackPocAccess-20261028'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User
$systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')

function Protect-LocalPath([string]$Path, [bool]$Directory) {
    if ($Directory) {
        $acl = [System.Security.AccessControl.DirectorySecurity]::new()
        $inherit = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    } else {
        $acl = [System.Security.AccessControl.FileSecurity]::new()
        $inherit = [System.Security.AccessControl.InheritanceFlags]::None
    }
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($principal in @($sid, $systemSid)) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $principal, 'FullControl', $inherit,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Read-UbuntuText([string]$Path) {
    $value = & wsl.exe --distribution Ubuntu --user root --exec /usr/bin/cat -- $Path
    if ($LASTEXITCODE -ne 0) { throw 'Protected Ubuntu file read failed.' }
    return ($value -join "`n").Replace("`r", '') + "`n"
}

function Save-OwnedText([string]$Path, [string]$Text) {
    if (Test-Path -LiteralPath $Path) {
        if ((Get-Item -LiteralPath $Path).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Access files must not be reparse points.'
        }
        if ((Get-Acl -LiteralPath $Path).GetOwner([Security.Principal.SecurityIdentifier]) -ne $sid) {
            throw 'Existing access file has a different owner; preserving it.'
        }
        if ([System.IO.File]::ReadAllText($Path) -ne $Text) {
            throw 'Existing local access file differs; preserving it.'
        }
    } else {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            # The private directory grants inheritable access only to this
            # user and SYSTEM, so the new file is private before its first byte.
            $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($Text)
            $stream.Write($bytes, 0, $bytes.Length)
        } finally { $stream.Dispose() }
    }
    Protect-LocalPath $Path $false
}

function Run-GuestSsh([string]$Key, [string]$Hosts, [int]$Port) {
    # Native errors are captured, so neither SSH diagnostics nor keys enter logs.
    $prior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & ssh.exe -T -F NUL -p $Port -i $Key -o BatchMode=yes `
            -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no `
            -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 `
            -o "UserKnownHostsFile=$Hosts" -o GlobalKnownHostsFile=NUL `
            cirros@127.0.0.1 'sudo -n cat /sys/class/dmi/id/product_uuid' 2>&1
        return @{ code = $LASTEXITCODE; output = ($output -join "`n") }
    } finally { $ErrorActionPreference = $prior }
}

$createdDirectory = $false
if (!(Test-Path -LiteralPath $directory)) {
    New-Item -ItemType Directory -Path $directory | Out-Null
    $createdDirectory = $true
}
if ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Access directory must not be a reparse point.'
}
if (!$createdDirectory -and (Get-Acl -LiteralPath $directory).GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) {
    throw 'Existing access directory has a different owner; preserving it.'
}
Protect-LocalPath $directory $true
$state = Read-UbuntuText "$runtime/connectivity.json" | ConvertFrom-Json
$files = @{}
foreach ($label in @('a', 'b')) {
    $keyFile = Join-Path $directory "tenant-$label-ed25519"
    $hostFile = Join-Path $directory "tenant-$label-known-hosts"
    Save-OwnedText $keyFile (Read-UbuntuText "$base/id_ed25519_$label")
    Save-OwnedText $hostFile (Read-UbuntuText "$runtime/known_hosts_$label")
    $files[$label] = @{ key = $keyFile; hosts = $hostFile }
}
$checks = @()
foreach ($label in @('a', 'b')) {
    $target = $state.access.$label
    $result = Run-GuestSsh $files[$label].key $files[$label].hosts $target.ssh_port
    if ($result.code -ne 0 -or $result.output.Trim().ToLowerInvariant() -ne $target.guest_id) {
        throw 'Native Windows SSH identity check failed.'
    }
    $other = if ($label -eq 'a') { 'b' } else { 'a' }
    $denied = Run-GuestSsh $files[$other].key $files[$label].hosts $target.ssh_port
    if ($denied.code -ne 255 -or $denied.output -notmatch 'Permission denied') {
        throw 'Foreign tenant SSH key was not rejected as expected.'
    }
    $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 `
        -Uri "http://127.0.0.1:$($target.http_port)/"
    $app = $response.Content | ConvertFrom-Json
    if ($response.StatusCode -ne 200 -or $app.tenant -ne $label -or $app.server_id -ne $target.guest_id) {
        throw 'Native Windows application identity check failed.'
    }
    $checks += @{ tenant = $label; ssh_identity_verified = $true;
        foreign_ssh_key_denied = $true; http_status = 200; routed_fip_path = $true }
}
$report = @{ phase = 'native_windows_guest_access_verified';
    checked_at = [DateTime]::UtcNow.ToString('o'); checks = $checks;
    local_only = $true; windows_routes_changed = $false; private_keys_logged = $false }
$reportPath = Join-Path $directory 'acceptance.json'
[IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
Protect-LocalPath $reportPath $false
$report | ConvertTo-Json -Depth 6
