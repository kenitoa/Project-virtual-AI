param(
    [string]$Config = 'configs/app.yaml',
    [ValidateSet('mock','fake','koboldcpp')][string]$Backend = 'koboldcpp',
    [string]$ObsConfig,
    [switch]$SkipWarmup
)
. "$PSScriptRoot/common.ps1"
$resolved = Resolve-ProjectConfig $Config
$reportFolder = Join-Path $ProjectRoot '.local/session-reports'
New-Item -ItemType Directory -Force -Path $reportFolder | Out-Null
$reportName = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N') + '.json'
$arguments = @('--config',$resolved,'--backend',$Backend,'--local-only','--dialogue','--responsive', '--check-startup','--operator-panel','--session-report',(Join-Path $reportFolder $reportName))
if (-not $SkipWarmup) { $arguments += '--warmup-tts' }
if ($ObsConfig) { $arguments += @('--obs-config',(Resolve-ProjectConfig $ObsConfig)) }
Write-Host 'Local rehearsal: external chat disabled. Start LLM/TTS/OBS/VTS separately before this launcher.'
Start-OwnedSession $arguments
