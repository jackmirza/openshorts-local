$ErrorActionPreference = 'Stop'
$clipRepo = Split-Path -Parent $PSScriptRoot
$clipStatePath = Join-Path $clipRepo '.local-checks\clipping-process.json'
if (-not (Test-Path -LiteralPath $clipStatePath)) { Write-Host 'No local clipping worker was recorded.'; return }
$clipState = Get-Content -LiteralPath $clipStatePath -Raw | ConvertFrom-Json
$clipWorker = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$clipState.pid)"
if (-not $clipWorker) { Write-Host 'The local clipping worker is already stopped.'; return }
if ($clipWorker.CommandLine -notlike '*scripts\run_clipping.py*') {
    throw 'The recorded process belongs to something else. It was left running.'
}
Stop-Process -Id $clipWorker.ProcessId
Write-Host 'Local clipping worker stopped. Your recordings and exports were kept.'
