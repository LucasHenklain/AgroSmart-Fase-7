# Runner local do pipeline DevSecOps (Windows/PowerShell).
# Repassa todos os argumentos para scripts/pipeline_local.py, ex.:
#   .\scripts\pipeline_local.ps1
#   .\scripts\pipeline_local.ps1 --estagios qualidade,testes,sast
#   .\scripts\pipeline_local.ps1 --sem-docker
# Evidência em outputs\pipeline\<AAAAMMDDTHHMMSS>\ e outputs\pipeline\ultimo\.
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
$python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
Push-Location $raiz
try {
    & $python (Join-Path $PSScriptRoot 'pipeline_local.py') @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
