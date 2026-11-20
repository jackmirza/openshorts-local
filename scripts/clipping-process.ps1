$clipWorkerPath = Join-Path $PSScriptRoot 'run_clipping.py'
$clipWorkerPattern = '*"' + [WildcardPattern]::Escape($clipWorkerPath) + '"*'
$clipChecks = Join-Path (Split-Path -Parent $PSScriptRoot) '.local-checks'

function Open-ClippingLauncherLock {
    New-Item -ItemType Directory -Path $clipChecks -Force | Out-Null
    try {
        [IO.File]::Open((Join-Path $clipChecks 'clipping-launch.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    } catch [IO.IOException] {
        throw 'Another clipping launcher is running, or the launcher lock cannot be opened. Try again.'
    }
}

function Get-ClippingListeners {
    # On Windows the virtual environment launcher and the listening Python
    # process have different PIDs. Match the worker to this checkout's path.
    $clipWorkerIds = @(Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like $clipWorkerPattern } |
        Select-Object -ExpandProperty ProcessId)
    if ($clipWorkerIds.Count) {
        Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -OwningProcess $clipWorkerIds -ErrorAction SilentlyContinue
    }
}
