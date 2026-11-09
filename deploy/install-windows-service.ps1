param([Parameter(Mandatory=$true)][string]$AgentExe,[switch]$Update)
$ErrorActionPreference='Stop'
$pslcExe=(Resolve-Path -LiteralPath $AgentExe).Path
if (-not [IO.Path]::IsPathFullyQualified($pslcExe) -or [IO.Path]::GetFileName($pslcExe) -ne 'pslc-agent.exe' -or $pslcExe.Contains('"')) { throw 'Explicit verified absolute binary path required' }
if (-not (Test-Path -LiteralPath 'C:\ProgramData\PSLC-Agent\agent-config.json')) { throw 'Provision least-privilege config/state/CA permissions first' }
$pslcService=Get-Service -Name PSLCAgent -ErrorAction SilentlyContinue
$pslcBin='"'+$pslcExe+'" --service'
if ($Update) {
 if (-not $pslcService -or $pslcService.Status -ne 'Stopped') { throw 'Stop the existing agent and reconcile active tasks before update' }
 & sc.exe config PSLCAgent binPath= $pslcBin
} else {
 if ($pslcService) { throw 'Existing service preserved; use explicit stopped update' }
 & sc.exe create PSLCAgent binPath= $pslcBin start= demand obj= 'NT AUTHORITY\LocalService' DisplayName= 'PSLC Agent'
}
if ($LASTEXITCODE -ne 0) { throw 'SCM configuration failed' }
Write-Output 'Service configured, not started. Verify signed release and service-account permissions, then start explicitly.'
