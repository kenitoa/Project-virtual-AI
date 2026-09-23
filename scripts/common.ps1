Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectPython = Join-Path $ProjectRoot '.venv/Scripts/python.exe'

function Invoke-Checked {
    param([string]$Program, [string[]]$CommandArgs)
    & $Program @CommandArgs
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
}

function Resolve-ProjectConfig {
    param([string]$Config)
    if (-not [IO.Path]::IsPathRooted($Config)) { $Config = Join-Path $ProjectRoot $Config }
    return (Resolve-Path -LiteralPath $Config -ErrorAction Stop).Path
}

function Start-OwnedSession {
    param([string[]]$AppArgs)
    if (-not (Test-Path -LiteralPath $ProjectPython)) { throw 'Run bootstrap.ps1 first.' }
    $sessionTag = [guid]::NewGuid().ToString()
    $ownedFolder = Join-Path $ProjectRoot '.local/owned-processes'
    New-Item -ItemType Directory -Force -Path $ownedFolder | Out-Null
    $arguments = @('-u', '-m', 'virtual_ai', '--operator-session-id', $sessionTag) + $AppArgs
    # Quote individual Windows CRT arguments; never invoke a command shell.
    $quoted = foreach ($argument in $arguments) {
        '"' + [regex]::Replace([regex]::Replace($argument, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
    }
    $ownedProcess = Start-Process -FilePath $ProjectPython -ArgumentList ($quoted -join ' ') -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru
    $null = $ownedProcess.Handle
    $record = Join-Path $ownedFolder ($sessionTag + '.json')
    try {
        $ownedProcess.Refresh()
        if (-not $ownedProcess.HasExited) {
            # Windows venv redirectors may create a second interpreter carrying our nonce.
            $children = @()
            for ($attempt = 0; $attempt -lt 5; $attempt++) {
                $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($ownedProcess.Id)" | Where-Object { $_.CommandLine -match [regex]::Escape($sessionTag) } | ForEach-Object {
                    $child = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
                    if ($child) { @{ pid=$child.Id; started=$child.StartTime.ToUniversalTime().ToString('o'); executable=$child.Path } }
                })
                if ($children.Count -gt 0 -or $ownedProcess.HasExited) { break }
                Start-Sleep -Milliseconds 100
            }
            @{ pid=$ownedProcess.Id; started=$ownedProcess.StartTime.ToUniversalTime().ToString('o'); executable=$ProjectPython; session=$sessionTag; root=$ProjectRoot; children=$children } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $record -Encoding UTF8
            Write-Host 'Use /quit for normal shutdown. Emergency only: stop-owned-processes.ps1 -Force'
        }
        $ownedProcess.WaitForExit()
        if ($ownedProcess.ExitCode -ne 0) { throw "Application exited with code $($ownedProcess.ExitCode)" }
    } catch {
        if (-not $ownedProcess.HasExited) { Stop-Process -InputObject $ownedProcess -Force }
        throw
    } finally {
        if (Test-Path -LiteralPath $record) { Remove-Item -LiteralPath $record }
    }
}
