# ASCII-only. Register task via PS cmdlet (bypass codepage mangling) and fire it.
$cmdFile = Join-Path (Get-Location).Path 'channels-bot\run_dy_fetch.cmd'
$logFile = Join-Path (Get-Location).Path 'channels-bot\dy_fetch_run.log'
Unregister-ScheduledTask -TaskName 'zyjiu-dy-fetch-urls' -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $cmdFile + '"')
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10)
Register-ScheduledTask -TaskName 'zyjiu-dy-fetch-urls' -Action $action -Trigger $trigger -Force | Out-Null
Start-ScheduledTask -TaskName 'zyjiu-dy-fetch-urls'
Write-Host 'fired'
for ($i = 0; $i -lt 36; $i++) {
    Start-Sleep -Seconds 5
    if (Test-Path $logFile) {
        $t = Get-Content $logFile -Raw -ErrorAction SilentlyContinue
        if ($t -match 'items:|FAIL') { break }
    }
}
Start-Sleep -Seconds 2
Get-Content $logFile -ErrorAction SilentlyContinue | Select-Object -First 40
