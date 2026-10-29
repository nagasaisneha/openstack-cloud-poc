# Verify from Windows with HeidiSQL's own installed MariaDB client library.
# Password stays in process memory; output contains only sanitized evidence.
# Run in 64-bit Windows PowerShell. Does not change accounts or network settings.
#Requires -Version 5.1
param(
    [string]$HeidiDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\HeidiSQL')
)
$ErrorActionPreference = 'Stop'
$library = Join-Path $HeidiDirectory 'libmariadb.dll'
if (!(Test-Path $library) -or ![Environment]::Is64BitProcess) {
    throw '64-bit Windows PowerShell and installed HeidiSQL libmariadb.dll are required'
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ViewerMariaDB {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool SetDllDirectory(string directory);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr LoadLibrary(string filename);
    [DllImport("libmariadb.dll", CallingConvention=CallingConvention.Cdecl)]
    public static extern IntPtr mysql_init(IntPtr mysql);
    [DllImport("libmariadb.dll", CallingConvention=CallingConvention.Cdecl)]
    public static extern int mysql_options(IntPtr mysql, int option, ref uint value);
    [DllImport("libmariadb.dll", CallingConvention=CallingConvention.Cdecl, CharSet=CharSet.Ansi)]
    public static extern IntPtr mysql_real_connect(IntPtr mysql, string host, string user,
        string password, string database, uint port, string socket, uint flags);
    [DllImport("libmariadb.dll", CallingConvention=CallingConvention.Cdecl, CharSet=CharSet.Ansi)]
    public static extern int mysql_query(IntPtr mysql, string query);
    [DllImport("libmariadb.dll", CallingConvention=CallingConvention.Cdecl)]
    public static extern uint mysql_errno(IntPtr mysql);
    [DllImport("libmariadb.dll", CallingConvention=CallingConvention.Cdecl)]
    public static extern IntPtr mysql_store_result(IntPtr mysql);
    [DllImport("libmariadb.dll", CallingConvention=CallingConvention.Cdecl)]
    public static extern IntPtr mysql_fetch_row(IntPtr result);
    [DllImport("libmariadb.dll", CallingConvention=CallingConvention.Cdecl)]
    public static extern void mysql_free_result(IntPtr result);
    [DllImport("libmariadb.dll", CallingConvention=CallingConvention.Cdecl)]
    public static extern void mysql_close(IntPtr mysql);
}
'@

function Invoke-ViewerQuery([IntPtr]$Connection, [string]$Query) {
    if ([ViewerMariaDB]::mysql_query($Connection, $Query) -ne 0) {
        throw ('MariaDB verification query failed; code=' + [ViewerMariaDB]::mysql_errno($Connection))
    }
    $result = [ViewerMariaDB]::mysql_store_result($Connection)
    if ($result -eq [IntPtr]::Zero) { throw 'Expected SELECT result' }
    try {
        while (($row = [ViewerMariaDB]::mysql_fetch_row($result)) -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::PtrToStringAnsi(
                [Runtime.InteropServices.Marshal]::ReadIntPtr($row))
        }
    } finally { [ViewerMariaDB]::mysql_free_result($result) }
}

$connection = [IntPtr]::Zero
$private = $null
try {
    if (![ViewerMariaDB]::SetDllDirectory($HeidiDirectory) -or
        [ViewerMariaDB]::LoadLibrary($library) -eq [IntPtr]::Zero) {
        throw 'Unable to load installed HeidiSQL MariaDB client'
    }
    # No secret appears in arguments, PowerShell history or the report.
    $credentialJson = & wsl.exe --distribution Ubuntu --user root --exec /bin/cat `
        /root/openstack-poc-acceptance/mariadb-viewer/credentials.json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read protected viewer credential' }
    $private = ($credentialJson -join "`n") | ConvertFrom-Json
    $credentialJson = $null
    if ($private.username -ne 'openstack_viewer' -or $private.host -ne '127.0.0.1' -or $private.port -ne 3306) {
        throw 'Unexpected credential identity/endpoint'
    }
    $connection = [ViewerMariaDB]::mysql_init([IntPtr]::Zero)
    if ($connection -eq [IntPtr]::Zero) { throw 'MariaDB client initialization failed' }
    [uint32]$timeout = 5
    if ([ViewerMariaDB]::mysql_options($connection, 0, [ref]$timeout) -ne 0) {
        throw 'MariaDB client connect timeout setup failed'
    }
    # This existing server has have_ssl=DISABLED. Connector/C 3.4 defaults to
    # requiring TLS. Match HeidiSQL's unchecked "Use SSL" on loopback only.
    # Connector/C mysql.h: MYSQL_OPT_SSL_ENFORCE=38, SSL_VERIFY_SERVER_CERT=21.
    [uint32]$disabled = 0
    foreach ($option in @(38,21)) {
        if ([ViewerMariaDB]::mysql_options($connection, $option, [ref]$disabled) -ne 0) {
            throw 'MariaDB client local-only SSL option setup failed'
        }
    }
    $connected = [ViewerMariaDB]::mysql_real_connect($connection, '127.0.0.1',
        $private.username, $private.password, $null, 3306, $null, 0)
    if ($connected -eq [IntPtr]::Zero) {
        throw ('Windows authentication failed; code=' + [ViewerMariaDB]::mysql_errno($connection))
    }
    $identity = Invoke-ViewerQuery $connection 'SELECT CURRENT_USER()'
    if ($identity -ne 'openstack_viewer@127.0.0.1') { throw 'Unexpected authenticated identity' }
    $databases = @('cinder','glance','keystone','neutron','nova','nova_api','nova_cell0',
        'octavia','placement','poc_meter_sources')
    $visible = @(Invoke-ViewerQuery $connection 'SHOW DATABASES')
    if (Compare-Object ($databases + @('information_schema')) $visible) {
        throw 'Unexpected visible schema list'
    }
    foreach ($database in $databases) {
        $table = Invoke-ViewerQuery $connection (
            "SELECT TABLE_NAME FROM information_schema.tables WHERE TABLE_SCHEMA='$database' ORDER BY TABLE_NAME LIMIT 1")
        if (!$table) { throw 'Expected schema table missing' }
        $quotedTable = $table.Replace('`','``')
        Invoke-ViewerQuery $connection ('SELECT * FROM `' + $database + '`.`' + $quotedTable + '` LIMIT 0') | Out-Null
    }
    if ([ViewerMariaDB]::mysql_query($connection, 'UPDATE keystone.project SET id=id WHERE 1=0') -eq 0 -or
        [ViewerMariaDB]::mysql_errno($connection) -notin @(1044,1142,1143)) {
        throw 'Expected UPDATE privilege denial'
    }
    if ([ViewerMariaDB]::mysql_query($connection, 'SELECT User FROM mysql.user LIMIT 0') -eq 0 -or
        [ViewerMariaDB]::mysql_errno($connection) -notin @(1044,1142)) {
        throw 'Expected system-schema privilege denial'
    }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 3306)
    if (!$listeners -or @($listeners | Where-Object {$_.LocalAddress -notin @('127.0.0.1','::1')}).Count) {
        throw 'Windows 3306 forwarding listener is not exclusively loopback'
    }
    $wslAddresses = & wsl.exe --distribution Ubuntu --exec /bin/hostname -I
    if ($LASTEXITCODE -ne 0) { throw 'WSL address inspection failed' }
    $wslAddress = (($wslAddresses -join ' ').Trim() -split '\s+')[0]
    $probe = New-Object Net.Sockets.TcpClient
    $directConnection = $false
    try {
        $pending = $probe.ConnectAsync($wslAddress,3306)
        if ($pending.Wait(3000)) { $directConnection = $probe.Connected }
    } catch [System.AggregateException] {
        # A refusal is expected: MariaDB binds only to Linux loopback.
        if ($_.Exception.InnerException -isnot [Net.Sockets.SocketException]) { throw }
    } finally { $probe.Dispose() }
    if ($directConnection) { throw 'WSL non-loopback database port unexpectedly reachable' }
    [ordered]@{
        observed_at = [DateTime]::UtcNow.ToString('o')
        windows_localhost_connection = $true
        client_library = 'HeidiSQL libmariadb.dll'
        client_library_version = (Get-Item $library).VersionInfo.ProductVersion
        current_user = $identity
        server_version = (Invoke-ViewerQuery $connection 'SELECT VERSION()')
        ssl = 'disabled; existing server has no TLS; loopback transport only'
        databases = $databases
        select_each_database = $true
        update_denied = $true
        system_schema_denied = $true
        windows_listener_loopback_only = $true
        wsl_nonloopback_connection_denied = $true
        password_printed = $false
    } | ConvertTo-Json -Depth 4
} finally {
    if ($connection -ne [IntPtr]::Zero) { [ViewerMariaDB]::mysql_close($connection) }
    $private = $null
    $credentialJson = $null
    [ViewerMariaDB]::SetDllDirectory($null) | Out-Null
}
