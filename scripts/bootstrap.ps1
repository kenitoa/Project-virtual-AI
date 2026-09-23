param([string]$Python = 'python')
. "$PSScriptRoot/common.ps1"
Push-Location $ProjectRoot
try {
    Invoke-Checked $Python @('-m','uv','sync','--locked')
    Invoke-Checked $ProjectPython @('-m','pytest','-q')
    Invoke-Checked $ProjectPython @('-m','ruff','check','.')
    Invoke-Checked $ProjectPython @('-m','ruff','format','--check','.')
    Invoke-Checked $ProjectPython @('-m','virtual_ai','--backend','mock','--local-only','--once','hello')
} finally { Pop-Location }
