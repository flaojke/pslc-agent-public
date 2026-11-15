# Withdrawn by user10.11.2026. Refuse before Python/Docker/state operations.
[CmdletBinding()]
param([ValidateSet('prepare','preview','start','status','stop','update')][string]$Action='start',[string]$Profile='main',[int]$Port=0,[switch]$NewVolume,[switch]$ImportState,[switch]$FreshState)
$ErrorActionPreference='Stop'
throw 'vm_deployment_withdrawn: VM creation was rejected. Use scripts/start-release.ps1 for native Windows startup. No Docker operation was attempted.'
