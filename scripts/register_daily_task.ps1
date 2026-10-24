<#
  PAPER TRADING - NO REAL ORDERS.
  Registers a Windows scheduled task that wakes the PC and runs
  run_dashboard_session.ps1 on weekdays. Runs as YOU, only while you are
  logged in (no password stored). No admin needed.

    Register:    powershell -ExecutionPolicy Bypass -File scripts\register_daily_task.ps1
    Change:      ... register_daily_task.ps1 -At 09:10 -Until 15:45 -Days Mon,Tue,Wed,Thu,Fri
                 (re-running replaces the existing task; PC wakes at -At, sleeps again after -Until)
    Show:        ... register_daily_task.ps1 -Show
    Remove:      ... register_daily_task.ps1 -Remove
    Run now:     Start-ScheduledTask -TaskName SmartTraderDashboard
#>
param(
    [string]$At = "09:15",
    [string]$Until = "16:00",
    [ValidateSet("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")]
    [string[]]$Days = @("Mon", "Tue", "Wed", "Thu", "Fri"),
    [switch]$Show,
    [switch]$Remove
)

$name = "SmartTraderDashboard"
$dayNames = @{ Mon = "Monday"; Tue = "Tuesday"; Wed = "Wednesday"; Thu = "Thursday"; Fri = "Friday"; Sat = "Saturday"; Sun = "Sunday" }

$null = [TimeSpan]::Parse($At); $null = [TimeSpan]::Parse($Until)
if ([TimeSpan]::Parse($Until) -le [TimeSpan]::Parse($At)) { throw "-Until ($Until) must be later than -At ($At)." }

if ($Show) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) { Write-Host "Task $name is not registered."; exit 0 }
    $t = $task.Triggers[0]
    $until = ($task.Actions[0].Arguments -replace '.*-Until\s+(\S+).*', '$1')
    Write-Host "Task $name  state: $($task.State)"
    Write-Host "  wakes + starts : $(([datetime]$t.StartBoundary).ToString('HH:mm'))  on $($t.DaysOfWeek)"
    Write-Host "  stops at       : $until IST"
    Write-Host "  next run       : $((Get-ScheduledTaskInfo $name).NextRunTime)"
    exit 0
}
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Removed task $name."
    exit 0
}

$script = Join-Path $PSScriptRoot "run_dashboard_session.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument `
    "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Until $Until"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek ($Days | ForEach-Object { $dayNames[$_] }) -At $At
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered ${name}: $($Days -join ',') at $At, stays awake until $Until IST."
Write-Host "Check any time with: scripts\register_daily_task.ps1 -Show"
