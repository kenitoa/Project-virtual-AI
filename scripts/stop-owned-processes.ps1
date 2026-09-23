param([switch]$Force, [string]$SessionId)
. "$PSScriptRoot/common.ps1"
$folder = Join-Path $ProjectRoot '.local/owned-processes'
if (-not (Test-Path -LiteralPath $folder)) { return }
foreach ($file in Get-ChildItem -LiteralPath $folder -Filter '*.json' -File) {
    if ($SessionId -and $file.BaseName -ne $SessionId) { continue }
    try {
        $record = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
        if ($record.root -ne $ProjectRoot -or $record.executable -ne $ProjectPython -or $file.BaseName -ne $record.session) { throw 'Ownership mismatch' }
        $tag = [guid]::Parse($record.session).ToString()
        $process = Get-Process -Id $record.pid -ErrorAction Stop
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$record.pid)"
        if ($process.Path -ne $ProjectPython -or $process.StartTime.ToUniversalTime().ToString('o') -ne $record.started -or $cim.CommandLine -notmatch [regex]::Escape($tag) -or $cim.CommandLine -notmatch 'virtual_ai') { throw 'Process identity changed; refusing stop' }
        $verifiedChildren = @()
        foreach ($childRecord in $record.children) {
            $child = Get-Process -Id $childRecord.pid -ErrorAction Stop
            $childCim = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$childRecord.pid)"
            if ($child.Path -ne $childRecord.executable -or $child.StartTime.ToUniversalTime().ToString('o') -ne $childRecord.started -or $childCim.ParentProcessId -ne $record.pid -or $childCim.CommandLine -notmatch [regex]::Escape($tag)) { throw 'Child ownership mismatch' }
            $verifiedChildren += $child
        }
        if (-not $Force) { Write-Host "Owned PID $($record.pid): use /quit, or -Force for emergency termination."; continue }
        foreach ($child in $verifiedChildren) { Stop-Process -InputObject $child -Force -ErrorAction Stop }
        try {
            if (-not $process.HasExited) { Stop-Process -InputObject $process -Force -ErrorAction Stop }
        } catch {
            # A venv redirector can exit automatically when its child exits.
            if (Get-Process -Id $record.pid -ErrorAction SilentlyContinue) { throw }
        }
        if (Test-Path -LiteralPath $file.FullName) { Remove-Item -LiteralPath $file.FullName }
    } catch {
        Write-Warning "Skipped ownership record $($file.Name): identity unavailable or mismatch."
    }
}
