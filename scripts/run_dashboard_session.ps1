<#
  PAPER TRADING - NO REAL ORDERS.
  Starts the local read-only dashboard, keeps Windows from sleeping until the
  cutoff time (IST), then stops the dashboard and lets Windows sleep again.

  Usage (normal PowerShell, no admin needed):
    powershell -ExecutionPolicy Bypass -File scripts\run_dashboard_session.ps1
    ... -Until 16:00 -Port 8000

  Limits (see docs/SECURITY.md): this does NOT stop a manual sleep/shutdown, a
  closed laptop lid (depends on your power settings), or a power cut. It is
  not a market-holiday calendar; the scheduled task only skips weekends.
#>
param(
    [string]$Until = "16:00",
    [int]$Port = 8000,
    [string]$Db = "data\paper_trading.db"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing $python - create the venv first (docs/WINDOWS_SETUP.md)." }

$ist = [TimeZoneInfo]::FindSystemTimeZoneById("India Standard Time")
$nowIst = [TimeZoneInfo]::ConvertTime([DateTimeOffset]::Now, $ist)
$cutoff = [DateTimeOffset]::new(
    $nowIst.Date.Add([TimeSpan]::Parse($Until)), $nowIst.Offset)
if ($nowIst -ge $cutoff) {
    Write-Host "It is already past $Until IST ($($nowIst.ToString('HH:mm'))). Nothing to do."
    exit 0
}

# ES_CONTINUOUS | ES_SYSTEM_REQUIRED: "stay awake" request that Windows drops
# automatically if this process dies, so a crash cannot leave the PC awake forever.
Add-Type -Namespace Win32 -Name Power -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint SetThreadExecutionState(uint esFlags);
'@
$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]1

$server = $null
try {
    [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
    $server = Start-Process -FilePath $python -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
        -ArgumentList @("-m", "app.cli", "serve", "--db", $Db, "--port", $Port)
    Write-Host "Dashboard http://127.0.0.1:$Port (PID $($server.Id)); staying awake until $Until IST."

    while ([TimeZoneInfo]::ConvertTime([DateTimeOffset]::Now, $ist) -lt $cutoff) {
        if ($server.HasExited) { throw "Dashboard exited early (code $($server.ExitCode))." }
        Start-Sleep -Seconds 15
    }
    Write-Host "Cutoff reached."
}
finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS)   # allow sleep again
    Write-Host "Dashboard stopped; sleep allowed again."
}
