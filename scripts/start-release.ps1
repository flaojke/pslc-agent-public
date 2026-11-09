$ErrorActionPreference='Stop'
$pslcReleaseRoot=Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $pslcReleaseRoot
try {
    if (!(Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'Compiled frontend is missing.' }
    if (!(Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Run scripts/install-release.ps1 first.' }
    & ./.venv/Scripts/python.exe scripts/setup_dev.py
    if ($LASTEXITCODE -ne 0) { throw 'Local setup failed.' }
    & ./.venv/Scripts/python.exe -m backend
    if ($LASTEXITCODE -ne 0) { throw 'API stopped with error.' }
} finally { Pop-Location }
