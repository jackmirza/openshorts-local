[CmdletBinding()]
param([ValidateRange(1024,65535)][int]$Port = 5175)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'clipping-process.ps1')
$clipRepo = Split-Path -Parent $PSScriptRoot
$clipLock = Open-ClippingLauncherLock
try {
    $clipExisting = @(Get-ClippingListeners)
    if ($clipExisting.Count) {
        if ($clipExisting.Count -ne 1 -or $clipExisting[0].LocalPort -ne $Port) {
            $clipPorts = $clipExisting.LocalPort -join ', '
            throw "Clipping is already running on port $clipPorts. Stop it before changing ports."
        }
        $clipReady = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/ready" -TimeoutSec 2
        if ($clipReady.application -ne 'openshorts-local') { throw 'The existing worker did not report ready.' }
        Write-Host "Gameplay clipper is ready: http://localhost:$Port/local-clips"
        return
    }
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is in use. Stop the other service or start the clipper with -Port and a free port."
    }
    $clipPython = Join-Path $clipRepo '.venv-local\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $clipPython -PathType Leaf)) {
        throw 'Run scripts\Setup-Clipping.ps1 once to install the local environment.'
    }
    $clipFfmpeg = Get-Command ffmpeg.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($clipFfmpeg) {
        $clipBinary = $clipFfmpeg.Source
    } else {
        $clipSiblingRoot = Join-Path (Split-Path -Parent $clipRepo) 'ffmpeg'
        $clipBinary = Get-ChildItem -LiteralPath $clipSiblingRoot -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $clipBinary) { throw 'FFmpeg is missing. Install FFmpeg and add its bin folder to PATH.' }
    $clipProbe = Join-Path (Split-Path -Parent $clipBinary) 'ffprobe.exe'
    if (-not (Test-Path -LiteralPath $clipProbe -PathType Leaf)) {
        throw 'FFprobe is missing. Install ffprobe.exe alongside ffmpeg.exe, then start the clipper again.'
    }
    @{ffmpeg=$clipBinary; ffprobe=$clipProbe; port=$Port} | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $clipChecks 'clipping-config.json') -Encoding UTF8

    # Launch the hidden worker independently of the setup terminal.
    $clipStartup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
        ShowWindow=[uint16]0
        CreateFlags=[uint32]8
    }
    $clipLaunch = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine=('"' + $clipPython + '" -X utf8 "' + $clipWorkerPath + '"')
        CurrentDirectory=$clipRepo
        ProcessStartupInformation=$clipStartup
    }
    if ($clipLaunch.ReturnValue -ne 0) { throw "Windows could not start the clipping worker (code $($clipLaunch.ReturnValue))." }
    for ($clipAttempt = 0; $clipAttempt -lt 20; $clipAttempt++) {
        Start-Sleep -Milliseconds 500
        if (-not (Get-Process -Id $clipLaunch.ProcessId -ErrorAction SilentlyContinue)) { break }
        try {
            $clipReady = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/ready" -TimeoutSec 2
        } catch { continue }
        if ($clipReady.application -eq 'openshorts-local' -and
            (Get-ClippingListeners | Where-Object LocalPort -eq $Port)) {
            Write-Host "Gameplay clipper is ready: http://localhost:$Port/local-clips"
            return
        }
    }
    throw 'The clipper did not become ready. Check .local-checks\clipping.stderr.log.'
} finally {
    $clipLock.Dispose()
}
