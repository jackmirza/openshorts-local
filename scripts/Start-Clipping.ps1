[CmdletBinding()]
param([ValidateRange(1024,65535)][int]$Port = 5175)
$ErrorActionPreference = 'Stop'
$clipRepo = Split-Path -Parent $PSScriptRoot
$clipPython = Join-Path $clipRepo '.venv-local\Scripts\python.exe'
$clipChecks = Join-Path $clipRepo '.local-checks'
if (-not (Test-Path -LiteralPath $clipPython)) {
    throw 'Run scripts\Setup-Clipping.ps1 once to install the small local environment.'
}
$clipFfmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($clipFfmpeg) {
    $clipBinary = $clipFfmpeg.Source
} else {
    # Also supports this workspace's existing FFmpeg installation; never downloads
    # or executes another binary behind the user's back.
    $clipSiblingRoot = Join-Path (Split-Path -Parent $clipRepo) 'ffmpeg'
    $clipBinary = Get-ChildItem -LiteralPath $clipSiblingRoot -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $clipBinary) { throw 'FFmpeg is missing. Install FFmpeg and add its bin folder to PATH.' }
$env:FFMPEG_BINARY = $clipBinary
$env:FFPROBE_BINARY = Join-Path (Split-Path -Parent $clipBinary) 'ffprobe.exe'
$env:PATH = (Split-Path -Parent $clipBinary) + [IO.Path]::PathSeparator + $env:PATH
$env:FFMPEG_ENCODER = 'auto'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:LOCAL_CLIP_DIR = Join-Path $clipRepo 'local-data'
New-Item -ItemType Directory -Path $clipChecks -Force | Out-Null
try {
    $clipExisting = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/ready" -TimeoutSec 2
    if ($clipExisting.application -eq 'openshorts-local') {
        Write-Host "Gameplay clipper is ready: http://localhost:$Port/local-clips"
        return
    }
} catch { }
@{ffmpeg=$clipBinary; ffprobe=$env:FFPROBE_BINARY; port=$Port} | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $clipChecks 'clipping-config.json') -Encoding UTF8
# The Windows process service launches the hidden worker independently of the
# setup terminal. Closing that terminal must not kill an active clip export.
$clipStartup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
    ShowWindow=[uint16]0
    CreateFlags=[uint32]8
}
$clipWorker = Join-Path $PSScriptRoot 'run_clipping.py'
$clipLaunch = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine=('"' + $clipPython + '" -X utf8 "' + $clipWorker + '"')
    CurrentDirectory=$clipRepo
    ProcessStartupInformation=$clipStartup
}
if ($clipLaunch.ReturnValue -ne 0) { throw "Windows could not start the clipping worker (code $($clipLaunch.ReturnValue))." }
@{pid=$clipLaunch.ProcessId; port=$Port} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $clipChecks 'clipping-process.json')
for ($clipAttempt = 0; $clipAttempt -lt 20; $clipAttempt++) {
    Start-Sleep -Milliseconds 500
    if (-not (Get-Process -Id $clipLaunch.ProcessId -ErrorAction SilentlyContinue)) {
        throw 'Clipping server could not start. Check .local-checks\clipping.stderr.log.'
    }
    try {
        $clipReady = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/ready" -TimeoutSec 2
        if ($clipReady.application -eq 'openshorts-local') {
            Write-Host "Gameplay clipper is ready: http://localhost:$Port/local-clips"
            return
        }
    } catch { }
}
throw 'The clipper did not become ready. Check .local-checks\clipping.stderr.log.'
