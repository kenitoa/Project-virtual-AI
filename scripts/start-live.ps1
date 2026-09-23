param([string]$Config = 'configs/app.yaml', [string]$MemoryDb, [string]$MemoryUser = 'local')
. "$PSScriptRoot/common.ps1"
$resolved = Resolve-ProjectConfig $Config
& "$PSScriptRoot/check-environment.ps1" -Config $resolved
$arguments = @('--config',$resolved,'--live','--check-startup')
if ($MemoryDb) { $arguments += @('--memory-db',$MemoryDb,'--memory-user',$MemoryUser) }
Start-OwnedSession $arguments
