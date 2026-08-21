# ============================================================
#  注册 Windows 计划任务: website-arch-monitor
#  功能: 每 10 分钟运行一次监控脚本
#  要求: 管理员权限运行
# ============================================================

$taskName = "website-arch-monitor"
$scriptPath = "d:\网站架构设计\scripts\monitor-and-alert.ps1"
$workDir = "d:\网站架构设计"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  注册 Windows 计划任务" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "任务名称:   $taskName"
Write-Host "监控脚本:   $scriptPath"
Write-Host "工作目录:   $workDir"
Write-Host "运行频率:   每 10 分钟"
Write-Host ""

# 1. 检查管理员权限
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] 需要管理员权限才能注册计划任务" -ForegroundColor Red
    Write-Host ""
    Write-Host "请用以下方式之一运行此脚本:" -ForegroundColor Yellow
    Write-Host "  1. 右键 PowerShell -> 以管理员身份运行 -> 执行此脚本"
    Write-Host "  2. 或在 PowerShell(管理员) 中执行:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "     powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\register-scheduled-task.ps1`"" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
Write-Host "[OK] 管理员权限检查通过" -ForegroundColor Green

# 2. 验证监控脚本存在
if (-not (Test-Path $scriptPath)) {
    Write-Host "[ERROR] 监控脚本不存在: $scriptPath" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] 监控脚本存在" -ForegroundColor Green

# 3. 检查任务是否已存在, 如存在则先删除
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "[INFO] 任务已存在, 先删除旧任务..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "[OK] 旧任务已删除" -ForegroundColor Green
}

# 4. 创建任务参数
Write-Host "[INFO] 创建计划任务..." -ForegroundColor Yellow

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`"" `
    -WorkingDirectory $workDir

# 触发器: 启动后立即开始, 每 10 分钟重复一次, 持续无限期
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 10)

# 设置: 允许任务重复运行, 运行时间不超过 5 分钟, 失败后重试
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# 主体: 使用 SYSTEM 账户以最高权限运行
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

# 5. 注册任务
try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null

    Write-Host "[OK] 计划任务注册成功" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] 注册失败: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# 6. 立即运行一次(测试)
Write-Host ""
Write-Host "[INFO] 立即运行一次测试..." -ForegroundColor Yellow
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3

# 7. 查看任务状态
$task = Get-ScheduledTask -TaskName $taskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $taskName

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  任务详情" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "任务名称:     $($task.TaskName)"
Write-Host "状态:         $($task.State)"
Write-Host "上次运行时间: $($taskInfo.LastRunTime)"
Write-Host "上次结果:     $($taskInfo.LastTaskResult)"
Write-Host "下次运行时间: $($taskInfo.NextRunTime)"
Write-Host "运行账户:     $($task.Principal.UserId)"
Write-Host "运行级别:     $($task.Principal.RunLevel)"
Write-Host ""
Write-Host "触发器详情:"
$task.Triggers | Format-List
Write-Host "============================================" -ForegroundColor Cyan

# 8. 输出管理命令
Write-Host ""
Write-Host "常用管理命令:" -ForegroundColor Cyan
Write-Host "  查看任务状态:   Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo"
Write-Host "  立即运行:       Start-ScheduledTask -TaskName `"$taskName`""
Write-Host "  停止运行:       Stop-ScheduledTask -TaskName `"$taskName`""
Write-Host "  禁用任务:       Disable-ScheduledTask -TaskName `"$taskName`""
Write-Host "  启用任务:       Enable-ScheduledTask -TaskName `"$taskName`""
Write-Host "  删除任务:       Unregister-ScheduledTask -TaskName `"$taskName`" -Confirm:`$false"
Write-Host ""
Write-Host "查看监控结果:"
Write-Host "  归档日志:       d:\网站架构设计\logs\archive\"
Write-Host "  告警文件:       d:\网站架构设计\logs\alerts\"
Write-Host "  监控报表:       d:\网站架构设计\logs\reports\"
Write-Host ""
