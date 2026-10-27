# Synthetic local files only. Never invokes the helper's main body or wsl.exe.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '../../deployment/windows/Export-PocWslBackup.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    [IO.Path]::GetFullPath($source), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Export helper syntax failed' }
$functions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-OfflineVhdHash'
}, $true))
if ($functions.Count -ne 1) { throw 'Expected exactly one offline source hash function' }
Invoke-Expression $functions[0].Extent.Text
$script:fakeRunning = @()
function Get-RunningDistributions { return $script:fakeRunning }
function Assert-True($Value, [string]$Reason) { if (-not $Value) { throw $Reason } }
function Assert-Throws($Action, [string]$Expected) {
    try { & $Action | Out-Null } catch {
        if ($_.Exception.Message -notlike ('*' + $Expected + '*')) { throw }
        return
    }
    throw ('Expected refusal: ' + $Expected)
}
$directory = Join-Path ([IO.Path]::GetTempPath()) ('poc-vhd-read-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $directory | Out-Null
$path = Join-Path $directory 'synthetic.vhdx'
$handle = $null
$background = $null
$held = $null
$passed = 0
try {
    [IO.File]::WriteAllBytes($path, [Text.Encoding]::UTF8.GetBytes('synthetic offline VHD read guard'))
    $expected = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    $before = (Get-Item -LiteralPath $path).LastWriteTimeUtc
    Assert-True ((Get-OfflineVhdHash $path 1) -eq $expected) 'Offline hash mismatch'
    Assert-True ((Get-Item -LiteralPath $path).LastWriteTimeUtc -eq $before) 'Source file was modified'
    $passed++

    $handle = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $timer = [Diagnostics.Stopwatch]::StartNew()
    Assert-Throws { Get-OfflineVhdHash $path 1 } 'Source VHD remains locked'
    Assert-True ($timer.Elapsed.TotalSeconds -ge 1 -and $timer.Elapsed.TotalSeconds -lt 5) 'Lock wait was not bounded'
    $handle.Dispose()
    $handle = $null
    $passed++

    $script:fakeRunning = @('another-distro')
    Assert-Throws { Get-OfflineVhdHash (Join-Path $directory 'absent.vhdx') 1 } 'A WSL distribution is running'
    $script:fakeRunning = @()
    $passed++

    $timer = [Diagnostics.Stopwatch]::StartNew()
    Assert-Throws { Get-OfflineVhdHash (Join-Path $directory 'absent.vhdx') 3 } 'Could not find file'
    Assert-True ($timer.Elapsed.TotalSeconds -lt 2) 'Non-sharing error was retried'
    $passed++

    $held = New-Object Threading.ManualResetEvent($false)
    $background = [PowerShell]::Create()
    [void]$background.AddScript({
        param($File, $Signal)
        $stream = [IO.File]::Open($File, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        try {
            [void]$Signal.Set()
            Start-Sleep -Milliseconds 800
        } finally { $stream.Dispose() }
    }).AddArgument($path).AddArgument($held)
    $invocation = $background.BeginInvoke()
    Assert-True ($held.WaitOne(5000)) 'Synthetic lock holder did not start'
    Assert-True ((Get-OfflineVhdHash $path 3) -eq $expected) 'Transient-lock read did not recover'
    [void]$background.EndInvoke($invocation)
    Assert-True ($background.Streams.Error.Count -eq 0) 'Synthetic lock holder failed'
    $passed++
    [ordered]@{ synthetic_source_read_tests_passed = $passed; live_wsl_actions = $false;
        source_file_unchanged = ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $expected) } |
        ConvertTo-Json
} finally {
    if ($null -ne $handle) { $handle.Dispose() }
    if ($null -ne $background) { $background.Dispose() }
    if ($null -ne $held) { $held.Dispose() }
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    Remove-Item -LiteralPath $directory
}
