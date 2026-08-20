#!/usr/bin/env powershell
# ==========================================================================
# Docker Compose 启动脚本(构建 + 启动 + 日志查看)
# 项目: 醉象模块29 - FastAPI + Redis
# 用法:
#   .\scripts\docker-start.ps1           # 默认: 构建并启动, 然后查看日志
#   .\scripts\docker-start.ps1 -Action build    # 仅构建
#   .\scripts\docker-start.ps1 -Action up       # 仅启动(不重新构建)
#   .\scripts\docker-start.ps1 -Action logs     # 仅查看日志
#   .\scripts\docker-start.ps1 -Action down     # 停止并清理
#   .\scripts\docker-start.ps1 -Action status   # 查看容器状态 + healthcheck
#   .\scripts\docker-start.ps1 -Action restart  # 重启服务
#   .\scripts\docker-start.ps1 -Action verify   # 验证 healthcheck 端点
# ==========================================================================

param(
    [ValidateSet("all", "build", "up", "logs", "down", "status", "restart", "verify")]
    [string]$Action = "all"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Write-Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "[ERR]  $msg" -ForegroundColor Red }

# ---- 前置检查 ----
Write-Step "前置检查"
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Write-Err "docker 命令未找到, 请先安装 Docker Desktop 并加入 PATH"
    Write-Host "  下载: https://www.docker.com/products/docker-desktop"
    exit 1
}
Write-Ok "docker 已安装: $($dockerCmd.Source)"

Set-Location $ProjectRoot
$composeFile = Join-Path $ProjectRoot "docker-compose.yml"
if (-not (Test-Path $composeFile)) {
    Write-Err "docker-compose.yml 不存在: $composeFile"
    exit 1
}
Write-Ok "docker-compose.yml 存在"

# 检查 Docker 引擎是否运行
$engineCheck = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err "Docker 引擎未运行, 请启动 Docker Desktop"
    Write-Host "  提示: 启动后等待 Docker Desktop 状态变为 'running' 再重试"
    exit 1
}
Write-Ok "Docker 引擎运行中"

# ---- 构建镜像 ----
function Invoke-Build {
    Write-Step "构建镜像(docker compose build)"
    docker compose build 2>&1 | ForEach-Object {
        if ($_ -match "DONE|ERROR|WARN|naming|exporting") { Write-Host $_ }
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Err "构建失败"
        exit 1
    }
    Write-Ok "镜像构建成功"
}

# ---- 启动服务 ----
function Invoke-Up {
    Write-Step "启动服务(docker compose up -d)"
    docker compose up -d 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-Err "启动失败"
        exit 1
    }
    Write-Ok "服务已启动"
}

# ---- 查看日志 ----
function Invoke-Logs {
    Write-Step "查看日志(实时, Ctrl+C 退出)"
    Write-Host "提示: 'redis' / 'backend' 标签区分服务`n" -ForegroundColor DarkGray
    docker compose logs -f --tail=50
}

# ---- 停止并清理 ----
function Invoke-Down {
    Write-Step "停止并清理(docker compose down)"
    docker compose down 2>&1 | ForEach-Object { Write-Host $_ }
    Write-Ok "已停止并清理"
}

# ---- 查看状态 + healthcheck ----
function Invoke-Status {
    Write-Step "容器状态"
    docker compose ps
    Write-Host ""

    Write-Step "Healthcheck 状态"
    $services = @("redis", "backend")
    foreach ($svc in $services) {
        $container = docker compose ps -q $svc 2>$null
        if (-not $container) {
            Write-Warn "$svc : 容器未运行"
            continue
        }
        $health = docker inspect --format='{{.State.Health.Status}}' $container 2>$null
        $status = docker inspect --format='{{.State.Status}}' $container 2>$null
        if ($health) {
            Write-Host "  $svc : status=$status, health=$health" -ForegroundColor $(if ($health -eq "healthy") {"Green"} else {"Yellow"})
        } else {
            Write-Host "  $svc : status=$status (无 healthcheck)" -ForegroundColor DarkGray
        }
    }
}

# ---- 重启 ----
function Invoke-Restart {
    Write-Step "重启服务(docker compose restart)"
    docker compose restart 2>&1 | ForEach-Object { Write-Host $_ }
    Write-Ok "已重启"
    Write-Host "等待 15 秒让 healthcheck 收敛..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 15
    Invoke-Status
}

# ---- 验证 healthcheck 端点 ----
function Invoke-Verify {
    Write-Step "验证 healthcheck 端点"
    $container = docker compose ps -q backend 2>$null
    if (-not $container) {
        Write-Err "backend 容器未运行, 请先执行: .\scripts\docker-start.ps1 -Action up"
        exit 1
    }
    Write-Host "容器ID: $container`n" -ForegroundColor DarkGray

    Write-Host "[1/2] 容器内 curl(模拟 docker healthcheck)"
    docker compose exec -T backend curl -fsS http://localhost:8000/api/decision/health 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "容器内 healthcheck 通过(200)"
    } else {
        Write-Err "容器内 healthcheck 失败(可能 backend 尚未就绪, 等待 10s 后重试)"
        Start-Sleep -Seconds 10
        docker compose exec -T backend curl -fsS http://localhost:8000/api/decision/health 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -eq 0) { Write-Ok "重试通过" } else { Write-Err "重试仍失败"; exit 1 }
    }

    Write-Host "`n[2/2] 宿主机 curl(验证端口映射)"
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8000/api/decision/health" -TimeoutSec 5
        Write-Ok "宿主机访问成功: success=$($resp.success), status=$($resp.details.status)"
    } catch {
        Write-Warn "宿主机 curl 失败: $($_.Exception.Message)"
        Write-Host "  这可能是 WSL2 端口映射问题, 不影响容器内 healthcheck"
    }
}

# ---- 主流程 ----
switch ($Action) {
    "build"   { Invoke-Build }
    "up"      { Invoke-Up; Invoke-Status }
    "logs"    { Invoke-Logs }
    "down"    { Invoke-Down }
    "status"  { Invoke-Status }
    "restart" { Invoke-Restart }
    "verify"  { Invoke-Verify }
    default   {
        Invoke-Build
        Invoke-Up
        Write-Host "`n等待 20 秒让 backend healthcheck 收敛(start_period=15s + 首次检查 10s)..." -ForegroundColor DarkGray
        Start-Sleep -Seconds 20
        Invoke-Status
        Write-Host "`n提示: 查看实时日志执行 .\scripts\docker-start.ps1 -Action logs" -ForegroundColor DarkGray
        Write-Host "提示: 验证 health 执行 .\scripts\docker-start.ps1 -Action verify`n" -ForegroundColor DarkGray
    }
}
