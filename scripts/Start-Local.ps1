[CmdletBinding()]
param([switch]$Build, [switch]$InstallModel)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'clipping-process.ps1')
if (Get-ClippingListeners) {
    throw 'Stop the Windows clipper before starting the Docker stack. They share the local recording store.'
}
$clipRepo = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $clipRepo
try {
    foreach ($clipFolder in @('output', 'uploads', 'local-data', 'shorts-inbox', 'shorts-ready')) {
        New-Item -ItemType Directory -Path (Join-Path $clipRepo $clipFolder) -Force | Out-Null
    }
    & docker info --format '{{.ServerVersion}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop and wait for its Linux engine, then retry.' }
    $clipResolved = & docker compose -f compose.local.yml config --format json
    if ($LASTEXITCODE -ne 0) { throw 'Docker could not read compose.local.yml.' }
    $clipServices = (($clipResolved -join [Environment]::NewLine) | ConvertFrom-Json).services
    $clipModel = $clipServices.backend.environment.LLM_MODEL
    if (-not $clipModel) { throw 'Set the local picker model in compose.local.yml.' }

    # Check the picker before the much larger backend build. Compose being
    # healthy only means the model server is reachable, not that it has weights.
    if (-not $InstallModel) {
        $clipImage = & docker image ls --quiet --filter "reference=$($clipServices.ollama.image)"
        if ($LASTEXITCODE -ne 0) { throw 'Could not check the local Ollama image.' }
        if (-not $clipImage) {
            throw 'Ollama is not installed for this profile. Run Start-Local.ps1 -InstallModel for the first setup.'
        }
    }
    & docker compose -f compose.local.yml up -d --wait --wait-timeout 60 ollama
    if ($LASTEXITCODE -ne 0) { throw 'Ollama did not become ready. Check docker compose -f compose.local.yml logs ollama.' }
    & docker compose -f compose.local.yml exec -T ollama ollama show $clipModel *> $null
    if ($LASTEXITCODE -ne 0) {
        if (-not $InstallModel) {
            throw "The local model $clipModel is not installed. Run Start-Local.ps1 -InstallModel to download it."
        }
        & docker compose -f compose.local.yml exec -T ollama ollama pull $clipModel
        if ($LASTEXITCODE -ne 0) { throw "Could not download the local model $clipModel. Try the installer again." }
    }

    $clipComposeArgs = @('compose', '-f', 'compose.local.yml', 'up', '-d')
    if ($Build) { $clipComposeArgs += '--build' }
    & docker @clipComposeArgs
    if ($LASTEXITCODE -ne 0) { throw 'Local clipping containers did not start. Check Docker output above.' }
    $clipReady = $false
    for ($clipAttempt = 0; $clipAttempt -lt 30; $clipAttempt++) {
        try {
            $clipResponse = Invoke-RestMethod -Uri 'http://127.0.0.1:5175/api/config' -TimeoutSec 3
            if ($clipResponse.localClipper -and -not $clipResponse.timestampOnly) { $clipReady = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $clipReady) { throw 'The dashboard is not ready. Check docker compose -f compose.local.yml logs frontend backend.' }
    Write-Host 'Gameplay clipper: http://localhost:5175/local-clips'
    Write-Host 'OpenShorts dashboard: http://localhost:5175'
    Write-Host "Shorts inbox  $(Join-Path $clipRepo 'shorts-inbox')"
    Write-Host "Ready Shorts  $(Join-Path $clipRepo 'shorts-ready')"
} finally {
    Pop-Location
}
