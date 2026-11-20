$ErrorActionPreference = 'Stop'
$clipRepo = Split-Path -Parent $PSScriptRoot
$clipPython = Join-Path $clipRepo '.venv-local\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $clipPython)) {
    & python -m venv (Join-Path $clipRepo '.venv-local')
    if ($LASTEXITCODE -ne 0) { throw 'Python could not create the local environment.' }
}
& $clipPython -m pip install -r (Join-Path $clipRepo 'requirements-local.txt')
if ($LASTEXITCODE -ne 0) { throw 'The local dependencies could not be installed.' }
Write-Host 'Setup complete. Run scripts\Start-Clipping.ps1 to open the local service.'
