# Two background jobs on this PC, while you are signed in:
#   MM Dashboard Liquidity Watch  every 5 minutes  python -m engines.news_local announce
#       reads new Bangladesh news through the local proxy, then AI Studio, then the free rule reader,
#       and opens a GitHub issue for any liquidity event that matters.
#   MM Dashboard Calendar Watch   every hour       python -m engines.calendar_notices
#       reads BB's bank-closure circulars (e.g. the Puja closure) and, when the dates are proven,
#       adds them to the holiday calendar and re-rolls every maturity/settlement they touch.
#       It runs here because BB's firewall shows GitHub's servers a CAPTCHA for these pages.
#
# Run once, from this folder:   powershell -ExecutionPolicy Bypass -File .\install_news_task.ps1
# Remove them later with:       Unregister-ScheduledTask -TaskName "MM Dashboard Liquidity Watch" -Confirm:$false
#                               Unregister-ScheduledTask -TaskName "MM Dashboard Calendar Watch" -Confirm:$false
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot
$py = (Get-Command python).Source
$logdir = Join-Path $env:LOCALAPPDATA "mm_dashboard"
New-Item -ItemType Directory -Force $logdir | Out-Null

function Register-Job($name, $module, $moduleArgs, $minutes, $logName) {
    $log = Join-Path $logdir $logName
    $cmd = "Set-Location '$dir'; Add-Content '$log' ('--- ' + (Get-Date -Format s)); & '$py' -m $module $moduleArgs *>> '$log'"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $dir `
        -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $minutes)
    # IgnoreNew: a run still going when the next is due is not doubled up. StartWhenAvailable: a run
    # missed while the PC was asleep happens when it wakes.
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Force | Out-Null
    Write-Host "Installed '$name': every $minutes minutes while you are signed in. Log: $log"
}

Register-Job "MM Dashboard Liquidity Watch" "engines.news_local" "announce" 5 "news_local.log"
Register-Job "MM Dashboard Calendar Watch" "engines.calendar_notices" "" 60 "calendar_notices.log"
