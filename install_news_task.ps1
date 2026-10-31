# Two background jobs on this PC, while you are signed in:
#   MM Dashboard Liquidity Watch  every 5 minutes  python -m engines.news_local announce
#       reads new Bangladesh news through the local proxy, then AI Studio, then the free rule reader,
#       and opens a GitHub issue for any liquidity event that matters.
#   MM Dashboard Calendar Watch   every hour       python -m engines.calendar_notices
#       reads BB's bank-closure circulars (e.g. the Puja closure) and, when the dates are proven,
#       adds them to the holiday calendar and re-rolls every maturity/settlement they touch.
#       It runs here because BB's firewall shows GitHub's servers a CAPTCHA for these pages.
#
#   MM Dashboard Data Refresh     every hour       python weekly_fetch.py (light)
#       reads Bangladesh Bank's data (FX, repo, call money, OMO, yields, ...) into the live database.
#   MM Dashboard Data Refresh Full  daily at 19:00  python weekly_fetch.py (full, adds secondary-market trades)
#       BB's firewall shows GitHub's servers a CAPTCHA, so the refresh jobs there fail and email you;
#       this PC is the one machine BB lets in. GitHub keeps running them as a backup.
#
# Run once, from this folder:   powershell -ExecutionPolicy Bypass -File .\install_news_task.ps1
# Remove them later with:       Unregister-ScheduledTask -TaskName "MM Dashboard Liquidity Watch" -Confirm:$false
#                               Unregister-ScheduledTask -TaskName "MM Dashboard Calendar Watch" -Confirm:$false
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot
$py = (Get-Command python).Source
$logdir = Join-Path $env:LOCALAPPDATA "mm_dashboard"
New-Item -ItemType Directory -Force $logdir | Out-Null

function Register-Job($name, $module, $moduleArgs, $minutes, $logName, $envSet = "", $limitMinutes = 20, $dailyAt = "") {
    $log = Join-Path $logdir $logName
    # 2>&1 | ForEach-Object { [string]$_ } keeps Python's harmless INFO lines on stderr from being reported by
    # Windows PowerShell as a failed run (exit 1) and from being wrapped in error decoration in the log;
    # the exit code is Python's own.
    if (Test-Path $log) { Remove-Item $log -Force }
    $cmd = "Set-Location '$dir'; $envSet `$env:PYTHONIOENCODING='utf-8'; Add-Content -Encoding utf8 '$log' ('--- ' + (Get-Date -Format s)); & '$py' -m $module $moduleArgs 2>&1 | ForEach-Object { [string]`$_ } | Add-Content -Encoding utf8 '$log'; exit `$LASTEXITCODE"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $dir `
        -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
    if ($dailyAt) {
        $trigger = New-ScheduledTaskTrigger -Daily -At $dailyAt
    } else {
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $minutes)
    }
    # IgnoreNew: a run still going when the next is due is not doubled up. StartWhenAvailable: a run
    # missed while the PC was asleep happens when it wakes.
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $limitMinutes) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Force | Out-Null
    $when = if ($dailyAt) { "daily at $dailyAt" } else { "every $minutes minutes" }
    Write-Host "Installed '$name': $when while you are signed in. Log: $log"
}

Register-Job "MM Dashboard Liquidity Watch" "engines.news_local" "announce" 5 "news_local.log"
Register-Job "MM Dashboard Calendar Watch" "engines.calendar_notices" "" 60 "calendar_notices.log"

$light = "`$env:FETCH_MODE='light'; `$env:OMO_DAYS_BACK='35'; `$env:OMO_MAX_FILES='35'; `$env:TREASURY_MONTHS='1'; `$env:CALLMONEY_DAYS='20'; `$env:REFRATE_DAYS='20'; `$env:IBFX_MONTHS='1';"
Register-Job "MM Dashboard Data Refresh" "weekly_fetch" "" 60 "data_refresh.log" $light 40
Register-Job "MM Dashboard Data Refresh Full" "weekly_fetch" "" 0 "data_refresh_full.log" "" 120 "19:00"
