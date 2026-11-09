param([switch]$Enroll,[switch]$Once)
$ErrorActionPreference='Stop'
& (Join-Path $PSScriptRoot 'verify.ps1')
$pslcArgs=@('--config',(Join-Path $PSScriptRoot 'config.json'))
if ($Enroll) { $pslcArgs+='--enroll' }
if ($Once) { $pslcArgs+='--once' }
& (Join-Path $PSScriptRoot 'native/pslc-agent.exe') @pslcArgs
if ($LASTEXITCODE -ne 0) { throw 'Agent stopped with an error; identity is preserved' }
