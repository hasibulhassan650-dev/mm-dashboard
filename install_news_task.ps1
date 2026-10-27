# Scan Bangladesh news for liquidity events every 5 minutes while you are signed in to this PC.
# Run once, from this folder:   powershell -ExecutionPolicy Bypass -File .\install_news_task.ps1
# Remove it later with:         Unregister-ScheduledTask -TaskName "MM Dashboard Liquidity Watch" -Confirm:$false
#
# What it runs: python -m engines.news_local announce
#   reads new articles through the local proxy, then AI Studio, then the free rule reader,
#   and opens a GitHub issue for any event that matters. Nothing runs while you are signed out.
$ErrorActionPreference = "Stop"
$name = "MM Dashboard Liquidity Watch"
$dir = $PSScriptRoot
$py = (Get-Command python).Source
$logdir = Join-Path $env:LOCALAPPDATA "mm_dashboard"
New-Item -ItemType Directory -Force $logdir | Out-Null
$log = Join-Path $logdir "news_local.log"

$cmd = "Set-Location '$dir'; Add-Content '$log' ('--- ' + (Get-Date -Format s)); & '$py' -m engines.news_local announce *>> '$log'"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $dir `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
# IgnoreNew: a run still going when the next is due is not doubled up. StartWhenAvailable: a run
# missed while the PC was asleep happens when it wakes.
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null
Write-Host "Installed '$name': every 5 minutes while you are signed in."
Write-Host "Log: $log"
