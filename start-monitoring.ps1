﻿﻿﻿﻿# ============================================================
# 本地监控栈一键启动脚本 (PowerShell)
# 用途: 快速拉起 Redis + Prometheus + Grafana 监控栈
# 用法:
#   .\start-monitoring.ps1           # 启动
#   .\start-monitoring.ps1 -Action down    # 停止
#   .\start-monitoring.ps1 -Action status  # 查看状态
#   .\start-monitoring.ps1 -Action logs    # 查看日志
#   .\start-monitoring.ps1 -Action verify  # 执行验证
# 编码: UTF-8 with BOM (PowerShell 5.1 兼容)
# ============================================================

#Requires -Version 5.1
$ErrorActionPreference = "Stop"

# ============================================================
# 参数
# ============================================================

param(
    [ValidateSet("up", "down", "status", "logs", "verify", "build", "restart", "clean")]
    [string]$Action = "up"
)

# ============================================================
# 配置
# ============================================================

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$ComposeFile = "docker-compose.monitoring.yml"
$EnvFile = ".env"

# ============================================================
# 辅助函数
# ============================================================

function Write-Header {
    param([string]$Title)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host " $Title" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

function Write-Step {
    param([string]$Msg)
    Write-Host "  [STEP] $Msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Msg)
    Write-Host "  [WARN] $Msg" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Msg)
    Write-Host "  [ERROR] $Msg" -ForegroundColor Red
}

function Test-Command {
    param([string]$Cmd)
    $null = Get-Command $Cmd -ErrorAction SilentlyContinue
    return $?
}

function Wait-Healthy {
    param(
        [string]$ContainerName,
        [int]$TimeoutSeconds = 60
    )
    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        $status = docker inspect $ContainerName --format='{{.State.Health.Status}}' 2>$null
        if ($status -eq "healthy") {
            return $true
        }
        Start-Sleep -Seconds 2
        $elapsed += 2
        Write-Host "." -NoNewline
    }
    Write-Host ""
    return $false
}

# ============================================================
# 环境检查
# ============================================================

function Test-Environment {
    Write-Header "1. 环境检查"
    
    # Docker
    if (-not (Test-Command "docker")) {
        Write-Err "Docker 未安装或未启动"
        Write-Host "  请安装 Docker Desktop: https://www.docker.com/products/docker-desktop"
        exit 1
    }
    
    $dockerVersion = docker version --format '{{.Server.Version}}' 2>$null
    if ($dockerVersion) {
        Write-Step "Docker 版本: $dockerVersion"
    } else {
        Write-Err "Docker daemon 未启动, 请启动 Docker Desktop"
        exit 1
    }
    
    # Docker Compose
    $composeCmd = "docker-compose"
    if (-not (Test-Command $composeCmd)) {
        # 尝试 docker compose (v2)
        $composeCmd = "docker compose"
        $testResult = docker compose version 2>$null
        if (-not $testResult) {
            Write-Err "Docker Compose 未安装"
            exit 1
        }
    }
    Write-Step "Docker Compose 可用: $composeCmd"
    
    # .env 文件
    if (-not (Test-Path $EnvFile)) {
        Write-Step "创建 .env 文件 (默认密码)"
        @"
REDIS_PASSWORD=zhuxiang123
GRAFANA_PASSWORD=admin
"@ | Out-File -FilePath $EnvFile -Encoding utf8 -NoNewline
        Write-Warn "使用默认密码, 生产环境请修改 .env 文件"
    } else {
        Write-Step ".env 文件已存在"
    }
    
    # 配置文件检查
    $requiredFiles = @(
        "prometheus/prometheus.yml",
        "prometheus/alert_rules.yml",
        "prometheus/alertmanager.yml",
        "grafana/provisioning/datasources/datasources.yml",
        "grafana/provisioning/dashboards/dashboards.yml"
    )
    
    foreach ($file in $requiredFiles) {
        if (Test-Path $file) {
            Write-Step "配置文件存在: $file"
        } else {
            Write-Err "配置文件缺失: $file"
            exit 1
        }
    }
    
    return $composeCmd
}

# ============================================================
# 启动监控栈
# ============================================================

function Start-Monitoring {
    param([string]$ComposeCmd)
    
    Write-Header "2. 启动监控栈"
    
    # 拉取镜像
    Write-Step "拉取最新镜像..."
    & $ComposeCmd -f $ComposeFile pull 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    
    # 启动服务
    Write-Step "启动服务..."
    & $ComposeCmd -f $ComposeFile up -d 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    
    # 等待 Redis 健康
    Write-Step "等待 Redis 健康检查..."
    if (Wait-Healthy -ContainerName "redis" -TimeoutSeconds 60) {
        Write-Step "Redis 健康"
    } else {
        Write-Warn "Redis 健康检查超时 (继续执行)"
    }
    
    # 等待 Prometheus 启动
    Write-Step "等待 Prometheus 启动..."
    Start-Sleep -Seconds 5
    
    # 等待 Backend 健康
    Write-Step "等待 Backend 健康检查..."
    if (Wait-Healthy -ContainerName "backend" -TimeoutSeconds 90) {
        Write-Step "Backend 健康"
    } else {
        Write-Warn "Backend 健康检查超时 (继续执行)"
    }
}

# ============================================================
# 停止监控栈
# ============================================================

function Stop-Monitoring {
    param([string]$ComposeCmd)
    
    Write-Header "停止监控栈"
    Write-Step "停止所有服务..."
    & $ComposeCmd -f $ComposeFile down 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    Write-Step "监控栈已停止"
}

# ============================================================
# 查看状态
# ============================================================

function Show-Status {
    param([string]$ComposeCmd)
    
    Write-Header "监控栈状态"
    & $ComposeCmd -f $ComposeFile ps 2>&1 | ForEach-Object { Write-Host "  $_" }
    
    Write-Header "服务访问地址"
    Write-Host "  Redis:           localhost:6379" -ForegroundColor Cyan
    Write-Host "  Backend API:     http://localhost:8000" -ForegroundColor Cyan
    Write-Host "  Redis Exporter:  http://localhost:9121/metrics" -ForegroundColor Cyan
    Write-Host "  Node Exporter:   http://localhost:9100/metrics" -ForegroundColor Cyan
    Write-Host "  cAdvisor:        http://localhost:8080" -ForegroundColor Cyan
    Write-Host "  Prometheus:      http://localhost:9090" -ForegroundColor Cyan
    Write-Host "  Alertmanager:    http://localhost:9093" -ForegroundColor Cyan
    $grafanaPwd = if ($env:GRAFANA_PASSWORD) { $env:GRAFANA_PASSWORD } else { "admin" }
    Write-Host "  Grafana:         http://localhost:3000 (admin / $grafanaPwd)" -ForegroundColor Cyan
}

# ============================================================
# 查看日志
# ============================================================

function Show-Logs {
    param([string]$ComposeCmd)
    
    Write-Header "监控栈日志 (最近 50 行)"
    & $ComposeCmd -f $ComposeFile logs --tail 50 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}

# ============================================================
# 执行验证
# ============================================================

function Invoke-Verify {
    Write-Header "执行验证脚本"
    
    $verifyScript = Join-Path $ProjectRoot "zhuxiang-jiu\backend\scripts\ha-monitor-check.ps1"
    if (Test-Path $verifyScript) {
        Write-Step "执行 ha-monitor-check.ps1..."
        & powershell -ExecutionPolicy Bypass -File $verifyScript
    } else {
        Write-Err "验证脚本不存在: $verifyScript"
    }
}

# ============================================================
# 构建镜像
# ============================================================

function Invoke-Build {
    param([string]$ComposeCmd)
    
    Write-Header "构建镜像"
    Write-Step "构建 Backend 镜像..."
    & $ComposeCmd -f $ComposeFile build backend 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    Write-Step "构建完成"
}

# ============================================================
# 重启服务
# ============================================================

function Invoke-Restart {
    param([string]$ComposeCmd)
    
    Write-Header "重启监控栈"
    Write-Step "重启所有服务..."
    & $ComposeCmd -f $ComposeFile restart 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    Write-Step "重启完成"
}

# ============================================================
# 清理
# ============================================================

function Invoke-Clean {
    param([string]$ComposeCmd)
    
    Write-Header "清理监控栈 (含数据卷)"
    $confirm = Read-Host "  确认清理? 所有数据将丢失 (y/N)"
    if ($confirm -eq "y" -or $confirm -eq "Y") {
        Write-Step "停止并删除容器和数据卷..."
        & $ComposeCmd -f $ComposeFile down -v 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        Write-Step "清理完成"
    } else {
        Write-Warn "取消清理"
    }
}

# ============================================================
# 主流程
# ============================================================

Write-Header "竹香酒网站 - 监控栈一键启动脚本"
Write-Host "  操作: $Action" -ForegroundColor Cyan
Write-Host "  时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray

$composeCmd = Test-Environment

switch ($Action) {
    "up" {
        Start-Monitoring -ComposeCmd $composeCmd
        Show-Status -ComposeCmd $composeCmd
    }
    "down" {
        Stop-Monitoring -ComposeCmd $composeCmd
    }
    "status" {
        Show-Status -ComposeCmd $composeCmd
    }
    "logs" {
        Show-Logs -ComposeCmd $composeCmd
    }
    "verify" {
        Invoke-Verify
    }
    "build" {
        Invoke-Build -ComposeCmd $composeCmd
    }
    "restart" {
        Invoke-Restart -ComposeCmd $composeCmd
    }
    "clean" {
        Invoke-Clean -ComposeCmd $composeCmd
    }
}

Write-Host ""
Write-Host "  完成!" -ForegroundColor Green
Write-Host ""
