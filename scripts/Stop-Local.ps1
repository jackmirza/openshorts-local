$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
try {
    & docker compose -f compose.local.yml stop
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the local clipping stack.' }
} finally { Pop-Location }
