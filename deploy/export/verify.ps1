$ErrorActionPreference='Stop'
$pslcRoot=[IO.Path]::GetFullPath($PSScriptRoot)+[IO.Path]::DirectorySeparatorChar
$pslcExpected=@{}
foreach ($pslcLine in Get-Content -LiteralPath (Join-Path $PSScriptRoot 'SHA256SUMS')) {
 $pslcPair=$pslcLine -split '  ',2
 if ($pslcPair.Count -ne 2 -or $pslcPair[0] -notmatch '^[a-f0-9]{64}$') { throw 'Invalid checksum list' }
 $pslcFile=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot $pslcPair[1]))
 if (-not $pslcFile.StartsWith($pslcRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe package path' }
 if ((Get-FileHash -LiteralPath $pslcFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pslcPair[0]) { throw 'File integrity failed' }
 $pslcExpected[$pslcFile]=$true
}
foreach ($pslcFile in Get-ChildItem -LiteralPath $PSScriptRoot -File -Recurse) {
 if ($pslcFile.Name -ne 'SHA256SUMS' -and -not $pslcExpected.ContainsKey($pslcFile.FullName)) { throw 'Unexpected package file' }
}
Write-Output 'SHA256 contents verified. Native signature verified by exporting server. QA-only trust is not corporate approval.'
