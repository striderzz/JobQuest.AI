# Registers a Windows scheduled task that searches for new jobs every 12 hours and opens JobQuest.AI when it finds some.
#   powershell -ExecutionPolicy Bypass -File schedule_daily.ps1             # daily at 09:00
#   powershell -ExecutionPolicy Bypass -File schedule_daily.ps1 -Time 19:30
#   powershell -ExecutionPolicy Bypass -File schedule_daily.ps1 -Remove
param([string]$Time = "09:00", [switch]$Remove)
$name = "JobQuestAI"
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; Write-Host "Removed the '$name' daily task."; return }
$home_ = Join-Path $env:USERPROFILE ".jobhuntai"
New-Item -ItemType Directory -Force $home_ | Out-Null
$log = Join-Path $home_ "last_refresh.log"
$launcher = Join-Path $PSScriptRoot "refresh.cmd"
# cmd /c strips the outer quotes, so the inner quoted paths survive spaces.
$arg = "/c `"`"$launcher`" --open-if-new > `"$log`" 2>&1`""
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arg -WorkingDirectory $PSScriptRoot
$first = [datetime]::ParseExact($Time, "HH:mm", $null)
$trigger = @((New-ScheduledTaskTrigger -Daily -At $first), (New-ScheduledTaskTrigger -Daily -At $first.AddHours(12)))   # every 12 hours
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "JobQuest.AI: daily search for entry-level SDE/ML jobs in India." -Force | Out-Null
Write-Host "Scheduled '$name' at $Time and 12 hours later. Output of the last run: $log"
