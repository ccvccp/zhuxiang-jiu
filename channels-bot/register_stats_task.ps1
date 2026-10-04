# 注册双平台数据监控计划任务(每 3 小时)
$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "d:\网站架构设计\channels-bot\monitor_stats.ps1"'
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) `
  -RepetitionInterval (New-TimeSpan -Hours 3)
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName 'zxjiu-stats-monitor' `
  -Action $act -Trigger $trg -Settings $set -Force
Write-Host 'registered: zxjiu-stats-monitor (every 3h)'
