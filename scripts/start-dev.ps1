$ErrorActionPreference = 'Stop'
$pslcProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $pslcProjectRoot
try {
    & node node_modules/typescript/bin/tsc --noEmit
    if ($LASTEXITCODE -ne 0) { throw 'TypeScript check failed' }
    & node node_modules/vite/bin/vite.js build
    if ($LASTEXITCODE -ne 0) { throw 'Web build failed' }
    & ./.venv/Scripts/python.exe scripts/setup_dev.py
    if ($LASTEXITCODE -ne 0) { throw 'Local setup failed' }
    & ./.venv/Scripts/python.exe -m backend
} finally { Pop-Location }
