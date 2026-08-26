﻿# ============================================================
#  自动化日志轮转与告警脚本
#  项目: 竹香酒 AI 决策模块 (website-arch)
#  功能:
#    1. 日志轮转: 定期导出容器日志, 按日期归档, 自动清理过期日志
#    2. 健康监控: 检查容器状态、健康端点、资源占用
#    3. 异常告警: 检测到异常时生成告警文件并发送到指定目录
#    4. 报表生成: 每次运行生成监控报表
#
#  用法:
#    手动运行: .\scripts\monitor-and-alert.ps1
#    定时运行: 注册为 Windows 计划任务(见脚本末尾说明)
#
#  退出码:
#    0 = 全部正常
#    1 = 检测到告警(需关注)
#    2 = 脚本执行错误
# ============================================================

param(
    # Docker Compose 项目名(因路径含中文, 必须显式指定)
    [string]$ProjectName = "website-arch",

    # 日志归档目录
    [string]$LogArchiveDir = "d:\网站架构设计\logs\archive",

    # 告警目录
    [string]$AlertDir = "d:\网站架构设计\logs\alerts",

    # 监控报表目录
    [string]$ReportDir = "d:\网站架构设计\logs\reports",

    # 日志保留天数(超过则自动清理)
    [int]$LogRetentionDays = 7,

    # 告警保留天数
    [int]$AlertRetentionDays = 30,

    # 健康检查超时(秒)
    [int]$HealthTimeout = 10,

    # 内存使用率告警阈值(百分比)
    [int]$MemoryAlertThreshold = 80,

    # CPU 使用率告警阈值(百分比)
    [int]$CpuAlertThreshold = 90,

    # 是否启用邮件告警(需配置 SMTP)
    [bool]$EnableEmailAlert = $false,

    # 邮件接收人(逗号分隔)
    [string]$AlertEmailTo = "",

    # SMTP 服务器
    [string]$SmtpServer = "",

    # SMTP 端口
    [int]$SmtpPort = 587,

    # SMTP 用户名
    [string]$SmtpUser = "",

    # SMTP 密码
    [string]$SmtpPassword = ""
)

$ErrorActionPreference = "Continue"
$startTime = Get-Date
$exitCode = 0
$alerts = @()
$checks = @()

# ============================================================
#  辅助函数
# ============================================================

function Write-Step($msg) {
    Write-Host "`n[STEP] $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "[OK]   $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

function Write-Err($msg) {
    Write-Host "[ERR]  $msg" -ForegroundColor Red
}

function Write-Info($msg) {
    Write-Host "[INFO] $msg" -ForegroundColor DarkGray
}

function Ensure-Dir($path) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}

function Add-Alert {
    param(
        [string]$Level,      # CRITICAL / WARNING / INFO
        [string]$Category,   # Container / Health / Resource / Log
        [string]$Message,
        [string]$Detail = ""
    )
    $alert = [PSCustomObject]@{
        Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Level     = $Level
        Category  = $Category
        Message   = $Message
        Detail    = $Detail
    }
    $script:alerts += $alert
    if ($Level -eq "CRITICAL") {
        Write-Err "[$Level] $Message"
        if ($Detail) { Write-Host "       $Detail" -ForegroundColor DarkRed }
        $script:exitCode = 1
    } elseif ($Level -eq "WARNING") {
        Write-Warn "[$Level] $Message"
        if ($Detail) { Write-Host "       $Detail" -ForegroundColor DarkYellow }
        if ($script:exitCode -eq 0) { $script:exitCode = 1 }
    }
}

function Add-Check {
    param(
        [string]$Name,
        [string]$Status,    # PASS / FAIL / SKIP
        [string]$Detail = ""
    )
    $script:checks += [PSCustomObject]@{
        Timestamp = Get-Date -Format "HH:mm:ss"
        Name      = $Name
        Status    = $Status
        Detail    = $Detail
    }
    switch ($Status) {
        "PASS" { Write-Ok "$Name" }
        "FAIL" { Write-Err "$Name - $Detail" }
        "SKIP" { Write-Info "$Name (skipped)" }
    }
}

# ============================================================
#  阶段 1: 准备工作
# ============================================================

Write-Step "阶段 1: 准备工作"

# 创建目录
Ensure-Dir $LogArchiveDir
Ensure-Dir $AlertDir
Ensure-Dir $ReportDir
Write-Ok "目录已就绪"

# 检查 docker 命令
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Add-Alert -Level "CRITICAL" -Category "System" -Message "docker 命令未找到" -Detail "请安装 Docker Desktop 并加入 PATH"
    Add-Check -Name "Docker 可用性" -Status "FAIL" -Detail "docker 命令未找到"
    # 无法继续, 直接退出
    exit 2
}
Add-Check -Name "Docker 可用性" -Status "PASS"

# 检查 Docker 引擎是否就绪
$engineReady = $false
try {
    $null = docker ps --format "{{.Names}}" 2>$null
    if ($LASTEXITCODE -eq 0) { $engineReady = $true }
} catch {}

if (-not $engineReady) {
    Add-Alert -Level "CRITICAL" -Category "Docker" -Message "Docker 引擎未就绪" -Detail "请启动 Docker Desktop 并等待托盘图标变绿"
    Add-Check -Name "Docker 引擎就绪" -Status "FAIL"
    exit 2
}
Add-Check -Name "Docker 引擎就绪" -Status "PASS"

$dateStr = Get-Date -Format "yyyyMMdd"
$timeStr = Get-Date -Format "HHmmss"
$reportFile = Join-Path $ReportDir "monitor-report-$dateStr-$timeStr.md"

# ============================================================
#  阶段 2: 日志轮转
# ============================================================

Write-Step "阶段 2: 日志轮转"

# 导出 backend 日志
$backendLogFile = Join-Path $LogArchiveDir "backend-$dateStr-$timeStr.log"
try {
    docker compose -p $ProjectName logs --timestamps --tail 5000 backend 2>&1 | Out-File -Encoding utf8 $backendLogFile
    $logSize = (Get-Item $backendLogFile).Length
    Add-Check -Name "导出 backend 日志" -Status "PASS" -Detail "$([math]::Round($logSize/1KB, 1)) KB -> $backendLogFile"
} catch {
    Add-Check -Name "导出 backend 日志" -Status "FAIL" -Detail $_.Exception.Message
}

# 导出 redis 日志
$redisLogFile = Join-Path $LogArchiveDir "redis-$dateStr-$timeStr.log"
try {
    docker compose -p $ProjectName logs --timestamps --tail 1000 redis 2>&1 | Out-File -Encoding utf8 $redisLogFile
    $logSize = (Get-Item $redisLogFile).Length
    Add-Check -Name "导出 redis 日志" -Status "PASS" -Detail "$([math]::Round($logSize/1KB, 1)) KB -> $redisLogFile"
} catch {
    Add-Check -Name "导出 redis 日志" -Status "FAIL" -Detail $_.Exception.Message
}

# 清理过期日志
Write-Info "清理 $LogRetentionDays 天前的归档日志..."
$cutoffDate = (Get-Date).AddDays(-$LogRetentionDays)
$deletedCount = 0
Get-ChildItem $LogArchiveDir -Filter "*.log" -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $cutoffDate } | ForEach-Object {
    try {
        Remove-Item $_.FullName -Force
        $deletedCount++
    } catch {}
}
Add-Check -Name "清理过期日志" -Status "PASS" -Detail "删除 $deletedCount 个过期日志文件"

# 清理过期告警
$cutoffAlert = (Get-Date).AddDays(-$AlertRetentionDays)
$deletedAlerts = 0
Get-ChildItem $AlertDir -Filter "*.json" -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $cutoffAlert } | ForEach-Object {
    try {
        Remove-Item $_.FullName -Force
        $deletedAlerts++
    } catch {}
}
Add-Check -Name "清理过期告警" -Status "PASS" -Detail "删除 $deletedAlerts 个过期告警文件"

# ============================================================
#  阶段 3: 容器状态检查
# ============================================================

Write-Step "阶段 3: 容器状态检查"

$containers = @()
try {
    $containers = docker compose -p $ProjectName ps -a --format "{{.Name}}|{{.Service}}|{{.Status}}|{{.Ports}}" 2>&1
} catch {
    Add-Alert -Level "CRITICAL" -Category "Container" -Message "无法获取容器列表" -Detail $_.Exception.Message
    Add-Check -Name "获取容器列表" -Status "FAIL"
}

if ($containers -and $containers.Count -gt 0) {
    Add-Check -Name "获取容器列表" -Status "PASS" -Detail "$($containers.Count) 个容器"

    foreach ($line in $containers) {
        if ($line -match "^(\S+)\|(\S+)\|(.+?)\|(.*)$") {
            $name = $matches[1]
            $service = $matches[2]
            $status = $matches[3]
            $ports = $matches[4]

            # 检查容器状态
            if ($status -match "Up \(healthy\)") {
                Add-Check -Name "容器状态: $name" -Status "PASS" -Detail $status
            } elseif ($status -match "Up \(health: starting\)") {
                Add-Alert -Level "WARNING" -Category "Container" -Message "容器 $name 启动中" -Detail $status
                Add-Check -Name "容器状态: $name" -Status "FAIL" -Detail "启动中: $status"
            } elseif ($status -match "Up \(unhealthy\)") {
                Add-Alert -Level "CRITICAL" -Category "Container" -Message "容器 $name 不健康" -Detail $status
                Add-Check -Name "容器状态: $name" -Status "FAIL" -Detail "不健康: $status"
            } elseif ($status -match "Exited") {
                Add-Alert -Level "CRITICAL" -Category "Container" -Message "容器 $name 已退出" -Detail $status
                Add-Check -Name "容器状态: $name" -Status "FAIL" -Detail "已退出: $status"
            } elseif ($status -match "Restarting") {
                Add-Alert -Level "CRITICAL" -Category "Container" -Message "容器 $name 反复重启" -Detail $status
                Add-Check -Name "容器状态: $name" -Status "FAIL" -Detail "重启中: $status"
            } else {
                Add-Check -Name "容器状态: $name" -Status "PASS" -Detail $status
            }
        }
    }
} else {
    Add-Alert -Level "CRITICAL" -Category "Container" -Message "没有运行中的容器" -Detail "请启动服务: docker compose -p $ProjectName up -d backend"
    Add-Check -Name "获取容器列表" -Status "FAIL" -Detail "无容器"
}

# ============================================================
#  阶段 4: 健康端点检查
# ============================================================

Write-Step "阶段 4: 健康端点检查"

$healthUrl = "http://localhost:8000/api/decision/health"
$healthOk = $false
try {
    $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec $HealthTimeout
    if ($response.StatusCode -eq 200) {
        $healthOk = $true
        Add-Check -Name "健康端点检查" -Status "PASS" -Detail "HTTP 200 - $($response.Content)"
    } else {
        Add-Alert -Level "CRITICAL" -Category "Health" -Message "健康端点返回异常状态码" -Detail "HTTP $($response.StatusCode)"
        Add-Check -Name "健康端点检查" -Status "FAIL" -Detail "HTTP $($response.StatusCode)"
    }
} catch {
    Add-Alert -Level "CRITICAL" -Category "Health" -Message "健康端点不可访问" -Detail $_.Exception.Message
    Add-Check -Name "健康端点检查" -Status "FAIL" -Detail $_.Exception.Message
}

# 检查 Swagger UI
try {
    $swaggerResp = Invoke-WebRequest -Uri "http://localhost:8000/docs" -UseBasicParsing -TimeoutSec $HealthTimeout
    if ($swaggerResp.StatusCode -eq 200) {
        Add-Check -Name "Swagger UI 检查" -Status "PASS" -Detail "HTTP 200"
    } else {
        Add-Check -Name "Swagger UI 检查" -Status "SKIP" -Detail "HTTP $($swaggerResp.StatusCode)"
    }
} catch {
    Add-Check -Name "Swagger UI 检查" -Status "SKIP" -Detail "不可访问"
}

# ============================================================
#  阶段 5: 资源占用检查
# ============================================================

Write-Step "阶段 5: 资源占用检查"

# 获取容器资源占用
try {
    $stats = docker stats --no-stream --format "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}" 2>&1
    if ($stats) {
        Add-Check -Name "获取资源占用" -Status "PASS" -Detail "$($stats.Count) 个容器"

        foreach ($line in $stats) {
            if ($line -match "^(\S+)\|(\S+)\|(\S+)\|(\S+)") {
                $name = $matches[1]
                $cpuPerc = $matches[2] -replace "%", ""
                $memUsage = $matches[3]
                $memPerc = $matches[4] -replace "%", ""

                $cpuNum = [double]::Parse($cpuPerc, [System.Globalization.CultureInfo]::InvariantCulture)
                $memNum = [double]::Parse($memPerc, [System.Globalization.CultureInfo]::InvariantCulture)

                # CPU 告警
                if ($cpuNum -gt $CpuAlertThreshold) {
                    Add-Alert -Level "WARNING" -Category "Resource" -Message "容器 $name CPU 使用率过高" -Detail "CPU: $cpuPerc% (阈值: $CpuAlertThreshold%)"
                }

                # 内存告警
                if ($memNum -gt $MemoryAlertThreshold) {
                    Add-Alert -Level "WARNING" -Category "Resource" -Message "容器 $name 内存使用率过高" -Detail "内存: $memPerc% ($memUsage), 阈值: $MemoryAlertThreshold%"
                }

                Add-Check -Name "资源: $name" -Status "PASS" -Detail "CPU: $cpuPerc%, 内存: $memPerc% ($memUsage)"
            }
        }
    } else {
        Add-Check -Name "获取资源占用" -Status "SKIP" -Detail "无容器"
    }
} catch {
    Add-Check -Name "获取资源占用" -Status "FAIL" -Detail $_.Exception.Message
}

# 检查 WSL2 内存
try {
    $wslMem = wsl -d docker-desktop free -h 2>&1
    if ($wslMem) {
        Add-Check -Name "WSL2 内存检查" -Status "PASS" -Detail ($wslMem | Out-String).Trim()
    } else {
        Add-Check -Name "WSL2 内存检查" -Status "SKIP" -Detail "无法获取"
    }
} catch {
    Add-Check -Name "WSL2 内存检查" -Status "SKIP" -Detail $_.Exception.Message
}

# ============================================================
#  阶段 6: 日志错误检查
# ============================================================

Write-Step "阶段 6: 日志错误检查"

# 检查 backend 日志中的错误
if (Test-Path $backendLogFile) {
    $logContent = Get-Content $backendLogFile -ErrorAction SilentlyContinue
    $errorCount = 0
    $errorLines = @()

    foreach ($line in $logContent) {
        if ($line -match "ERROR|Traceback|Exception|CRITICAL|FATAL") {
            $errorCount++
            if ($errorLines.Count -lt 10) {
                $errorLines += $line
            }
        }
    }

    if ($errorCount -eq 0) {
        Add-Check -Name "backend 日志错误检查" -Status "PASS" -Detail "未发现错误"
    } elseif ($errorCount -le 3) {
        Add-Alert -Level "WARNING" -Category "Log" -Message "backend 日志发现少量错误" -Detail "$errorCount 个错误"
        Add-Check -Name "backend 日志错误检查" -Status "FAIL" -Detail "$errorCount 个错误"
    } else {
        Add-Alert -Level "CRITICAL" -Category "Log" -Message "backend 日志发现大量错误" -Detail "$errorCount 个错误"
        Add-Check -Name "backend 日志错误检查" -Status "FAIL" -Detail "$errorCount 个错误"
    }
} else {
    Add-Check -Name "backend 日志错误检查" -Status "SKIP" -Detail "日志文件不存在"
}

# 检查 OOMKilled
try {
    $cid = docker compose -p $ProjectName ps -q backend 2>&1
    if ($cid) {
        $oomStatus = docker inspect $cid --format "{{.State.OOMKilled}}" 2>&1
        if ($oomStatus -match "true") {
            Add-Alert -Level "CRITICAL" -Category "Container" -Message "backend 容器被 OOMKilled" -Detail "内存不足, 检查 .wslconfig 和 mem_limit"
            Add-Check -Name "OOMKilled 检查" -Status "FAIL" -Detail "OOMKilled=true"
        } else {
            Add-Check -Name "OOMKilled 检查" -Status "PASS" -Detail "无 OOM"
        }
    } else {
        Add-Check -Name "OOMKilled 检查" -Status "SKIP" -Detail "容器未运行"
    }
} catch {
    Add-Check -Name "OOMKilled 检查" -Status "SKIP" -Detail $_.Exception.Message
}

# ============================================================
#  阶段 7: Redis 检查
# ============================================================

Write-Step "阶段 7: Redis 检查"

try {
    $redisPing = docker compose -p $ProjectName exec -T redis redis-cli ping 2>&1
    if ($redisPing -match "PONG") {
        Add-Check -Name "Redis 连接检查" -Status "PASS" -Detail "PONG"

        # 检查 Key 数量
        $keyCount = docker compose -p $ProjectName exec -T redis redis-cli DBSIZE 2>&1
        Add-Check -Name "Redis Key 数量" -Status "PASS" -Detail "$keyCount 个 Key"

        # 检查内存
        $redisMem = docker compose -p $ProjectName exec -T redis redis-cli INFO memory 2>&1
        $usedMemLine = $redisMem | Select-String "used_memory_human"
        if ($usedMemLine) {
            Add-Check -Name "Redis 内存" -Status "PASS" -Detail $usedMemLine.ToString().Trim()
        }
    } else {
        Add-Alert -Level "CRITICAL" -Category "Redis" -Message "Redis 连接失败" -Detail $redisPing
        Add-Check -Name "Redis 连接检查" -Status "FAIL" -Detail $redisPing
    }
} catch {
    Add-Alert -Level "CRITICAL" -Category "Redis" -Message "Redis 检查异常" -Detail $_.Exception.Message
    Add-Check -Name "Redis 连接检查" -Status "FAIL" -Detail $_.Exception.Message
}

# ============================================================
#  阶段 8: 生成告警文件
# ============================================================

Write-Step "阶段 8: 生成告警文件"

if ($alerts.Count -gt 0) {
    $alertFile = Join-Path $AlertDir "alert-$dateStr-$timeStr.json"
    $alertJson = $alerts | ConvertTo-Json -Depth 5
    $alertJson | Out-File -Encoding utf8 $alertFile
    Write-Warn "生成告警文件: $alertFile ($($alerts.Count) 条告警)"

    # 邮件告警
    if ($EnableEmailAlert -and $AlertEmailTo -and $SmtpServer) {
        try {
            $subject = "[告警] 系统监控告警 - $dateStr $timeStr"
            $body = $alerts | Format-Table -AutoSize | Out-String
            $securePwd = ConvertTo-SecureString $SmtpPassword -AsPlainText -Force
            $cred = New-Object System.Management.Automation.PSCredential($SmtpUser, $securePwd)

            Send-MailMessage `
                -From $SmtpUser `
                -To $AlertEmailTo.Split(",") `
                -Subject $subject `
                -Body $body `
                -SmtpServer $SmtpServer `
                -Port $SmtpPort `
                -UseSsl `
                -Credential $cred

            Write-Ok "邮件告警已发送至 $AlertEmailTo"
        } catch {
            Write-Err "邮件发送失败: $($_.Exception.Message)"
        }
    } else {
        Write-Info "邮件告警未启用(EnableEmailAlert=false 或未配置 SMTP)"
    }
} else {
    Write-Ok "无告警"
}

# ============================================================
#  阶段 9: 生成监控报表
# ============================================================

Write-Step "阶段 9: 生成监控报表"

$endTime = Get-Date
$duration = ($endTime - $startTime).TotalSeconds

$report = @()
$report += "# 系统监控报表"
$report += ""
$report += "**生成时间**: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$report += "**执行耗时**: $([math]::Round($duration, 2)) 秒"
$report += "**项目名**: $ProjectName"
$report += ""
$report += "## 检查结果汇总"
$report += ""
$passCount = ($checks | Where-Object Status -eq "PASS").Count
$failCount = ($checks | Where-Object Status -eq "FAIL").Count
$skipCount = ($checks | Where-Object Status -eq "SKIP").Count
$report += "| 状态 | 数量 |"
$report += "|------|------|"
$report += "| PASS | $passCount |"
$report += "| FAIL | $failCount |"
$report += "| SKIP | $skipCount |"
$report += "| **合计** | **$($checks.Count)** |"
$report += ""
$report += "## 告警汇总"
$report += ""
if ($alerts.Count -gt 0) {
    $criticalCount = ($alerts | Where-Object Level -eq "CRITICAL").Count
    $warningCount = ($alerts | Where-Object Level -eq "WARNING").Count
    $infoCount = ($alerts | Where-Object Level -eq "INFO").Count
    $report += "| 级别 | 数量 |"
    $report += "|------|------|"
    $report += "| CRITICAL | $criticalCount |"
    $report += "| WARNING | $warningCount |"
    $report += "| INFO | $infoCount |"
    $report += "| **合计** | **$($alerts.Count)** |"
    $report += ""
    $report += "## 告警详情"
    $report += ""
    $report += "| 时间 | 级别 | 类别 | 消息 | 详情 |"
    $report += "|------|------|------|------|------|"
    foreach ($a in $alerts) {
        $report += "| $($a.Timestamp) | $($a.Level) | $($a.Category) | $($a.Message) | $($a.Detail) |"
    }
} else {
    $report += "无告警"
}
$report += ""
$report += "## 检查详情"
$report += ""
$report += "| 时间 | 检查项 | 状态 | 详情 |"
$report += "|------|--------|------|------|"
foreach ($c in $checks) {
    $detail = $c.Detail -replace "\|", "\\|"
    $report += "| $($c.Timestamp) | $($c.Name) | $($c.Status) | $detail |"
}
$report += ""
$report += "## 归档日志"
$report += ""
$report += "- backend 日志: $backendLogFile"
$report += "- redis 日志: $redisLogFile"
$report += ""
$report += "---"
$report += "*本报表由 monitor-and-alert.ps1 自动生成*"

$report | Out-File -Encoding utf8 $reportFile
Write-Ok "监控报表已生成: $reportFile"

# ============================================================
#  最终汇总
# ============================================================

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  监控完成" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  检查项:  $passCount PASS / $failCount FAIL / $skipCount SKIP (共 $($checks.Count) 项)"
Write-Host "  告警数:  $($alerts.Count) 条"
Write-Host "  耗时:    $([math]::Round($duration, 2)) 秒"
Write-Host "  报表:    $reportFile"
if ($alerts.Count -gt 0) {
    Write-Host "  告警:    $AlertDir\alert-$dateStr-$timeStr.json"
}
Write-Host "========================================" -ForegroundColor Cyan

exit $exitCode

# ============================================================
#  定时任务配置说明
# ============================================================
#
#  将此脚本注册为 Windows 计划任务, 每 10 分钟运行一次:
#
#  1. 打开"任务计划程序"(taskschd.msc)
#  2. 创建任务:
#     - 常规: 名称 "website-arch-monitor", 使用最高权限运行
#     - 触发器: 按需设定(建议每 10 分钟)
#     - 操作: 启动程序
#       程序: powershell.exe
#       参数: -NoProfile -ExecutionPolicy Bypass -File "d:\网站架构设计\scripts\monitor-and-alert.ps1"
#       起始位置: d:\网站架构设计
#     - 条件: 取消"只有在计算机使用交流电源时才启动"
#
#  或使用 PowerShell 命令注册:
#
#  $action = New-ScheduledTaskAction `
#      -Execute "powershell.exe" `
#      -Argument '-NoProfile -ExecutionPolicy Bypass -File "d:\网站架构设计\scripts\monitor-and-alert.ps1"' `
#      -WorkingDirectory "d:\网站架构设计"
#  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
#      -RepetitionInterval (New-TimeSpan -Minutes 10)
#  Register-ScheduledTask -TaskName "website-arch-monitor" `
#      -Action $action -Trigger $trigger -RunLevel Highest
#
#  启用邮件告警(可选):
#  .\scripts\monitor-and-alert.ps1 `
#      -EnableEmailAlert $true `
#      -AlertEmailTo "admin@example.com" `
#      -SmtpServer "smtp.example.com" `
#      -SmtpUser "user@example.com" `
#      -SmtpPassword "password"
