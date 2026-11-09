param([string]$PythonExecutable="py",[switch]$Resume,[string]$Wheelhouse="")
$ErrorActionPreference='Stop'
$pslcReleaseRoot=Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $pslcReleaseRoot
try {
    $pslcPythonArguments=if([System.IO.Path]::GetFileNameWithoutExtension($PythonExecutable) -eq 'py'){@('-3')}else{@()}
    & $PythonExecutable @pslcPythonArguments -c "import sys,struct,sysconfig;sys.exit(0 if sys.version_info >= (3,12) and struct.calcsize('P')==8 and sysconfig.get_platform().lower()=='win-amd64' else 2)"
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ x64 is required. No environment was created.' }
    $pslcInstallArguments=@('scripts/install_bootstrap.py','--root',$pslcReleaseRoot)
    if ($Resume) { $pslcInstallArguments+='--resume' }
    if ($Wheelhouse) { $pslcInstallArguments+=@('--wheelhouse',$Wheelhouse) }
    & $PythonExecutable @pslcPythonArguments @pslcInstallArguments
    if ($LASTEXITCODE -ne 0) { throw 'Installation was not completed. Existing files retained; use -Resume only for an owned interrupted installation. See the error code above.' }
    Write-Host 'Ready. Run ./scripts/start-release.ps1; open http://127.0.0.1:8787. A new empty installation creates admin / admin; existing account passwords are retained.'
} finally { Pop-Location }
