[CmdletBinding()]
param([switch]$Build, [switch]$InstallModel)
$ErrorActionPreference = 'Stop'
$clipRepo = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $clipRepo
try {
    foreach ($clipFolder in @('output', 'uploads', 'local-data')) {
        New-Item -ItemType Directory -Path (Join-Path $clipRepo $clipFolder) -Force | Out-Null
    }
    & docker info --format '{{.ServerVersion}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop and wait for its Linux engine, then retry.' }
    $clipComposeArgs = @('compose', '-f', 'compose.local.yml', 'up', '-d')
    if ($Build) { $clipComposeArgs += '--build' }
    & docker @clipComposeArgs
    if ($LASTEXITCODE -ne 0) { throw 'Local clipping containers did not start. Check Docker output above.' }
    if ($InstallModel) {
        & docker compose -f compose.local.yml exec -T ollama ollama pull qwen2.5:7b
        if ($LASTEXITCODE -ne 0) { throw 'Local model download failed. Timestamp clipping still works without it.' }
    }
    $clipReady = $false
    for ($clipAttempt = 0; $clipAttempt -lt 30; $clipAttempt++) {
        try {
            $clipResponse = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health/ready' -TimeoutSec 3
            if ($clipResponse.StatusCode -eq 200) { $clipReady = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $clipReady) { throw 'The backend is still starting. Check docker compose -f compose.local.yml logs backend.' }
    Write-Host 'Gameplay clipper: http://localhost:5175/local-clips'
    Write-Host 'OpenShorts dashboard: http://localhost:5175'
} finally {
    Pop-Location
}
