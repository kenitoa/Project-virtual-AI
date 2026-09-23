param([string]$Config = 'configs/app.yaml')
. "$PSScriptRoot/common.ps1"
$resolved = Resolve-ProjectConfig $Config
Push-Location $ProjectRoot
try {
    Invoke-Checked $ProjectPython @('-m','virtual_ai.diagnostics','--config',$resolved)
} finally { Pop-Location }
