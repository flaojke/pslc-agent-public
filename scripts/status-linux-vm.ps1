param([ValidatePattern('^[a-z][a-z0-9-]{0,31}$')][string]$Profile='main')
$ErrorActionPreference='Stop'
& (Join-Path $PSScriptRoot 'start-linux-vm.ps1') -Action status -Profile $Profile
