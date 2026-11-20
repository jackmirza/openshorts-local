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
    Stop-Process -Id $clipPids
    Wait-Process -Id $clipPids -Timeout 5 -ErrorAction SilentlyContinue
    if (Get-ClippingListeners) { throw 'The local clipping worker is still listening. Try stopping it again.' }
    Write-Host 'Local clipping worker stopped. Your recordings and exports were kept.'
} finally {
    $clipLock.Dispose()
}
