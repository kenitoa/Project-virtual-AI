param([string]$Config = 'configs/app.example.yaml', [ValidateSet('mock','fake','koboldcpp')][string]$Backend = 'mock', [string]$Once, [string]$MemoryDb, [string]$MemoryUser = 'local')
. "$PSScriptRoot/common.ps1"
$resolved = Resolve-ProjectConfig $Config
$arguments = @('--config',$resolved,'--backend',$Backend,'--local-only')
if ($Once) { $arguments += @('--once',$Once) }
if ($MemoryDb) { $arguments += @('--memory-db',$MemoryDb,'--memory-user',$MemoryUser) }
Start-OwnedSession $arguments
