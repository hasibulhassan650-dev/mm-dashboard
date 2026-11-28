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
# The interpreter must be one that HAS this project's packages: a second Python (3.12 was installed on 28-Nov and went first on PATH)
# has none of them, and tasks registered against it would fail at their first import. Pick the first candidate that can import them.
function Find-ProjectPython {
    $candidates = @()
    $candidates += (Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"          # a Python without the packages writes a traceback to stderr: not an install failure
    try {
        foreach ($c in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
            & $c -c "import sqlalchemy, selenium, bs4, pdfplumber" 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { return $c }
        }
    } finally { $ErrorActionPreference = $saved }
    throw "No Python with this project's packages (sqlalchemy, selenium, bs4, pdfplumber) was found: run 'pip install -r requirements.txt' first."
}
$py = Find-ProjectPython
Write-Host "Using Python: $py"
$logdir = Join-Path $env:LOCALAPPDATA "mm_dashboard"
New-Item -ItemType Directory -Force $logdir | Out-Null

function Register-Job($name, $module, $moduleArgs, $minutes, $logName, $envSet = "", $limitMinutes = 20, $dailyAt = "") {
    $log = Join-Path $logdir $logName
    # 2>&1 | ForEach-Object { [string]$_ } keeps Python's harmless INFO lines on stderr from being reported by
    # Windows PowerShell as a failed run (exit 1) and from being wrapped in error decoration in the log;
    # the exit code is Python's own.
    # Keep the log across re-installs (it is the evidence of what the last run did); only trim one that has grown large, and never
    # fail the install because a running job holds its log open.
    try { if ((Test-Path $log) -and ((Get-Item $log).Length -gt 8MB)) { Remove-Item $log -Force } } catch { }
    $cmd = "Set-Location '$dir'; $envSet `$env:PYTHONIOENCODING='utf-8'; Add-Content -Encoding utf8 '$log' ('--- ' + (Get-Date -Format s)); & '$py' -m $module $moduleArgs 2>&1 | ForEach-Object { [string]`$_ } | Add-Content -Encoding utf8 '$log'; exit `$LASTEXITCODE"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $dir `
        -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
    if ($dailyAt) {
        # One or more times, comma-separated ("12:00,19:00"): a run cut short by a shutdown or sign-out (the PC is turned off at
        # times) would otherwise leave a whole day without its full read. The one-refresh-at-a-time lock stops overlaps and the
        # daily backup runs once, so an extra time costs nothing when the earlier run finished.
        $times = $dailyAt -split ","
        $trigger = New-ScheduledTaskTrigger -Daily -At $times[0].Trim()
    } else {
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $minutes)
    }
    # IgnoreNew: a run still going when the next is due is not doubled up. StartWhenAvailable: a run
    # missed while the PC was asleep happens when it wakes.
    # WakeToRun wakes the PC from sleep for the run (wake timers are on in the power plan); RunOnlyIfNetworkAvailable keeps a run
    # from burning its time limit offline. After a shutdown, StartWhenAvailable runs the missed job as soon as the PC is back, and
    # the repeating jobs also start the moment you sign in, so a PC that was off for hours catches up straight away.
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -WakeToRun -RunOnlyIfNetworkAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $limitMinutes) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $triggers = @($trigger)
    if ($dailyAt) {
        foreach ($t in ($dailyAt -split ",")[1..99]) { if ($t) { $triggers += New-ScheduledTaskTrigger -Daily -At $t.Trim() } }
    }
    if (-not $dailyAt) {
        $logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $logon.Delay = "PT2M"          # let the network and the desktop settle before Chrome starts
        $triggers += $logon
    }
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings `
        -Principal $principal -Force | Out-Null
    $when = if ($dailyAt) { "daily at $dailyAt" } else { "every $minutes minutes" }
    Write-Host "Installed '$name': $when while you are signed in. Log: $log"
}

Register-Job "MM Dashboard Liquidity Watch" "engines.news_local" "announce" 5 "news_local.log"
Register-Job "MM Dashboard Calendar Watch" "engines.calendar_notices" "" 60 "calendar_notices.log"

$light = "`$env:FETCH_MODE='light'; `$env:OMO_DAYS_BACK='35'; `$env:OMO_MAX_FILES='35'; `$env:TREASURY_MONTHS='1'; `$env:CALLMONEY_DAYS='20'; `$env:REFRATE_DAYS='20'; `$env:IBFX_MONTHS='1';"
Register-Job "MM Dashboard Data Refresh" "weekly_fetch" "" 60 "data_refresh.log" $light 40
Register-Job "MM Dashboard Data Refresh Full" "weekly_fetch" "" 0 "data_refresh_full.log" "" 120 "12:00,19:00"
