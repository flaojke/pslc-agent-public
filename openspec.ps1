$env:OPENSPEC_TELEMETRY='0'
& node (Join-Path $PSScriptRoot 'node_modules/@fission-ai/openspec/bin/openspec.js') @args
exit $LASTEXITCODE
