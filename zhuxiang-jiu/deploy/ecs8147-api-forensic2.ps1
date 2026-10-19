# 取证: ECS 本机后端 pid 2508 的命令行/工作目录 + 现有后端安装目录
$p = Get-CimInstance Win32_Process -Filter 'ProcessId=2508'
Write-Host ('CmdLine: ' + $p.CommandLine)
Write-Host ('ExePath: ' + $p.ExecutablePath)
Write-Host '--- 疑似后端目录:'
foreach ($d in @('C:\www\backend','C:\backend','C:\app','C:\zhuxiang','C:\www\app','D:\backend')) {
    if (Test-Path $d) { Write-Host ($d + ' EXISTS'); Get-ChildItem $d -Name | Select-Object -First 8 | ForEach-Object { Write-Host ('  ' + $_) } }
}
Write-Host '--- 计划任务/服务中带 zhuxiang/backend:'
Get-ScheduledTask | Where-Object { $_.TaskName -match 'zhu|backend|uvicorn' } | ForEach-Object { Write-Host ($_.TaskName + ' state=' + $_.State) }
Write-Host 'PROBE2-DONE'
