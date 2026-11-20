$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'clipping-process.ps1')
$clipLock = Open-ClippingLauncherLock
try {
    $clipListeners = @(Get-ClippingListeners)
    if (-not $clipListeners.Count) {
        Write-Host 'The local clipping worker is already stopped.'
        return
    }
    $clipPids = @($clipListeners.OwningProcess | Select-Object -Unique)
    # The listening Python process can have an FFmpeg encode in progress.
    foreach ($clipPid in $clipPids) {
        & taskkill.exe /PID $clipPid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Windows could not stop the clipping worker and its encoder (PID $clipPid)." }
    }
    Wait-Process -Id $clipPids -Timeout 5 -ErrorAction SilentlyContinue
    if (Get-ClippingListeners) { throw 'The local clipping worker is still listening. Try stopping it again.' }
    Write-Host 'Local clipping worker stopped. Your recordings and finished exports were kept.'
} finally {
    $clipLock.Dispose()
}
