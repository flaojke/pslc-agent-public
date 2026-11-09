param([string]$PythonExecutable="py")
$ErrorActionPreference='Stop'
$pslcReleaseRoot=Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $pslcReleaseRoot
try {
    if (Test-Path -LiteralPath '.venv') { throw 'Existing .venv retained. For update follow docs/INSTALL_RELEASE.md.' }
    $pslcPythonArguments=if([System.IO.Path]::GetFileNameWithoutExtension($PythonExecutable) -eq 'py'){@('-3')}else{@()}
    & $PythonExecutable @pslcPythonArguments scripts/verify_release.py .
    if ($LASTEXITCODE -ne 0) { throw 'Verify the original extracted release before installing; see START_HERE.md.' }
    & $PythonExecutable @pslcPythonArguments -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
    & ./.venv/Scripts/python.exe -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12+ is required'"
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
    & ./.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; start was not attempted.' }
    & ./.venv/Scripts/python.exe scripts/setup_dev.py
    if ($LASTEXITCODE -ne 0) { throw 'Local setup failed.' }
    Write-Host 'Ready. Run ./scripts/start-release.ps1; open http://127.0.0.1:8787. First login: admin / admin.'
} finally { Pop-Location }
