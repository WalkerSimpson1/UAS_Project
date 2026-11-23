# Registers the "UAS weekly" Task Scheduler task for the current user (docs/DECISIONS.md P13).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 [-Publish]
#   Unregister-ScheduledTask -TaskName "UAS weekly" -Confirm:$false     # to remove
#
# Triggers: daily at 19:00 and 10 minutes after logon; "run as soon as possible
# after a missed start". weekly_run.py exits at once unless 7+ days have passed
# since the last good run, so the daily trigger is only a catch-up net.
# Runs as you, only while you're logged on: Docker Desktop and the Claude
# login both live in your session. Docker Desktop should start at login.
param([switch]$Publish)

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$taskArgs = "scripts\weekly_run.py" + $(if ($Publish) { " --publish" } else { "" })

$action = New-ScheduledTaskAction -Execute $python -Argument $taskArgs -WorkingDirectory $repo
$daily = New-ScheduledTaskTrigger -Daily -At 7pm
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$logon.Delay = "PT10M"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "UAS weekly" -Description "UAS policy tracker weekly pipeline (UAS_Project)" `
    -Action $action -Trigger @($daily, $logon) -Settings $settings -Principal $principal -Force | Out-Null
Get-ScheduledTask -TaskName "UAS weekly" | Select-Object TaskName, State
Write-Output "Runs: $python $taskArgs"
