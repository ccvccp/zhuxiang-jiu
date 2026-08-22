# ============================================================
# Redis + Prometheus 指标采集一键验证脚本
# 用途: 验证 Redis Exporter 和 Prometheus 指标采集是否正常
# 用法: .\verify-metrics.ps1
# 编码: UTF-8 with BOM (PowerShell 5.1 兼容)
# ============================================================

#Requires -Version 5.1
$ErrorActionPreference = "Continue"

# ============================================================
# 计数器
# ============================================================
$script:PASS = 0
$script:FAIL = 0
$script:WARN = 0

function Log-Pass {
    param([string]$Msg)
    $script:PASS++
    Write-Host "  [PASS] $Msg" -ForegroundColor Green
}

function Log-Fail {
    param([string]$Msg)
    $script:FAIL++
    Write-Host "  [FAIL] $Msg" -ForegroundColor Red
}

function Log-Warn {
    param([string]$Msg)
    $script:WARN++
    Write-Host "  [WARN] $Msg" -ForegroundColor Yellow
}

function Log-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "========== $Title ==========" -ForegroundColor Cyan
}

# ============================================================
# 辅助函数: 安全的 HTTP 请求 (兼容 PowerShell 5.1 和 7+)
# ============================================================

function Invoke-SafeRequest {
    param(
        [string]$Uri,
        [int]$TimeoutSec = 5
    )
    try {
        # PowerShell 5.1 使用 Invoke-WebRequest,需要 -UseBasicParsing
        # PowerShell 7+ 不需要 -UseBasicParsing,但兼容性参数会被忽略
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        return $response
    } catch {
        return $null
    }
}

function Invoke-SafeRest {
    param(
        [string]$Uri,
        [int]$TimeoutSec = 5
    )
    try {
        $response = Invoke-RestMethod -Uri $Uri -TimeoutSec $TimeoutSec -ErrorAction Stop
        return $response
    } catch {
        return $null
    }
}

# ============================================================
# 辅助函数: 查询 Prometheus 指标
# ============================================================

function Query-PromMetric {
    param([string]$Query)
    $response = Invoke-SafeRest -Uri "http://localhost:9090/api/v1/query?query=$Query"
    if ($response -and $response.status -eq "success" -and $response.data.result.Count -gt 0) {
        return $response.data.result[0].value[1]
    }
    return $null
}

# ============================================================
# 辅助函数: Redis 密码读取 (含异常兜底)
# 优先级: 环境变量 > .env 文件 > 默认值
# ============================================================

function Get-RedisPassword {
    $password = $null

    # 1. 环境变量(空字符串/纯空白/null 均视为未设置)
    if ($env:REDIS_PASSWORD) {
        $password = $env:REDIS_PASSWORD.Trim()
    }

    # 2. .env 文件(读取异常时静默降级, 不中断脚本)
    if (-not $password) {
        $envFile = Join-Path $PSScriptRoot "..\..\..\.env"
        try {
            if (Test-Path $envFile -ErrorAction SilentlyContinue) {
                $envContent = Get-Content $envFile -Encoding UTF8 -ErrorAction Stop
                $redisLine = $envContent | Where-Object { $_ -match "^REDIS_PASSWORD=" } | Select-Object -First 1
                if ($redisLine) {
                    $password = ($redisLine -replace "^\s*REDIS_PASSWORD=", "").Trim()
                }
            }
        } catch {
            # .env 读取失败(权限/编码/占用等), 降级默认值
            Write-Host "  [WARN] .env 文件读取失败, 使用默认密码: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    # 3. 默认密码
    if (-not $password) {
        $password = "zhuxiang123"
    }
    return $password
}

# ============================================================
# 主流程
# ============================================================

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Redis + Prometheus 指标采集验证" -ForegroundColor Cyan
Write-Host " 时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host "==========================================" -ForegroundColor Cyan

# Redis 密码 (与 docker-compose.monitoring.yml 中的 REDIS_PASSWORD 一致)
$RedisPassword = Get-RedisPassword

# ============================================================
# 1. Redis 直连验证
# ============================================================

Log-Section "1. Redis 直连验证"

# PING
$pingResult = docker exec redis redis-cli -a $RedisPassword ping 2>$null
if ($pingResult -and $pingResult -contains "PONG") {
    Log-Pass "Redis PING: PONG"
} else {
    Log-Fail "Redis PING 失败 (可能 Redis 未启动或密码错误)"
}

# 版本
$serverInfo = docker exec redis redis-cli -a $RedisPassword INFO server 2>$null
if ($serverInfo) {
    $versionLine = $serverInfo | Where-Object { $_ -match "^redis_version:" }
    if ($versionLine) {
        $version = ($versionLine -split ":")[1].Trim()
        Log-Pass "Redis 版本: $version"
    } else {
        Log-Warn "无法获取 Redis 版本"
    }
} else {
    Log-Fail "无法获取 Redis INFO server"
}

# 内存
$memInfo = docker exec redis redis-cli -a $RedisPassword INFO memory 2>$null
if ($memInfo) {
    $usedLine = $memInfo | Where-Object { $_ -match "^used_memory_human:" }
    $maxLine = $memInfo | Where-Object { $_ -match "^maxmemory_human:" }
    $fragLine = $memInfo | Where-Object { $_ -match "^mem_fragmentation_ratio:" }
    if ($usedLine -and $maxLine) {
        $used = ($usedLine -split ":")[1].Trim()
        $max = ($maxLine -split ":")[1].Trim()
        Log-Pass "Redis 内存: $used / $max"
    }
    if ($fragLine) {
        $frag = ($fragLine -split ":")[1].Trim()
        $fragVal = [double]$frag
        if ($fragVal -lt 1.5) {
            Log-Pass "内存碎片率: $frag (<1.5)"
        } else {
            Log-Warn "内存碎片率: $frag (>1.5, 建议关注)"
        }
    }
} else {
    Log-Fail "无法获取 Redis INFO memory"
}

# 键数量
$dbsizeResult = docker exec redis redis-cli -a $RedisPassword DBSIZE 2>$null
if ($dbsizeResult) {
    $keyCount = $dbsizeResult | Select-String "^\d+" | ForEach-Object { $_.Matches[0].Value }
    if ($keyCount) {
        Log-Pass "Redis 键数量: $keyCount"
    }
} else {
    Log-Warn "无法获取 Redis DBSIZE"
}

# ============================================================
# 2. Redis Exporter 验证
# ============================================================

Log-Section "2. Redis Exporter 验证"

$exporterResponse = Invoke-SafeRequest -Uri "http://localhost:9121/metrics"
if ($exporterResponse -and $exporterResponse.StatusCode -eq 200) {
    Log-Pass "Exporter 可访问 (HTTP 200)"

    $metrics = $exporterResponse.Content

    # redis_up 指标
    if ($metrics -match "redis_up\s+1") {
        Log-Pass "redis_up = 1"
    } elseif ($metrics -match "redis_up\s+0") {
        Log-Fail "redis_up = 0 (Redis 连接失败)"
    } else {
        Log-Warn "redis_up 指标不存在"
    }

    # redis_memory_used_bytes 指标
    if ($metrics -match "redis_memory_used_bytes") {
        Log-Pass "redis_memory_used_bytes 指标存在"
    } else {
        Log-Fail "redis_memory_used_bytes 指标不存在"
    }

    # redis_connected_clients 指标
    if ($metrics -match "redis_connected_clients") {
        Log-Pass "redis_connected_clients 指标存在"
    } else {
        Log-Fail "redis_connected_clients 指标不存在"
    }
} else {
    Log-Fail "Redis Exporter 不可访问 (http://localhost:9121/metrics)"
}

# ============================================================
# 3. Prometheus Targets 验证
# ============================================================

Log-Section "3. Prometheus Targets 验证"

$targetsResponse = Invoke-SafeRest -Uri "http://localhost:9090/api/v1/targets"
if ($targetsResponse -and $targetsResponse.status -eq "success") {
    $activeTargets = $targetsResponse.data.activeTargets
    $upCount = 0
    $totalCount = $activeTargets.Count

    foreach ($target in $activeTargets) {
        $jobName = $target.labels.job
        $health = $target.health
        if ($health -eq "up") {
            $upCount++
            Log-Pass "Job '$jobName': up"
        } else {
            Log-Fail "Job '$jobName': $health"
        }
    }

    Write-Host ""
    Write-Host "  UP Targets: $upCount / $totalCount" -ForegroundColor Yellow
} else {
    Log-Fail "Prometheus API 不可访问 (http://localhost:9090)"
}

# ============================================================
# 4. 关键指标查询验证
# ============================================================

Log-Section "4. 关键指标查询验证"

$metricQueries = @(
    @{ Name = "redis_up"; Query = "redis_up" },
    @{ Name = "redis_memory_used"; Query = "redis_memory_used_bytes" },
    @{ Name = "redis_connected_clients"; Query = "redis_connected_clients" },
    @{ Name = "redis_commands_rate"; Query = "rate(redis_commands_processed_total[5m])" }
)

foreach ($metric in $metricQueries) {
    $value = Query-PromMetric -Query $metric.Query
    if ($value -ne $null) {
        Log-Pass "$($metric.Name) = $value"
    } else {
        Log-Warn "$($metric.Name): 无数据 (可能刚启动,等待 15s 后重试)"
    }
}

# ============================================================
# 5. 告警规则验证
# ============================================================

Log-Section "5. 告警规则验证"

$rulesResponse = Invoke-SafeRest -Uri "http://localhost:9090/api/v1/rules"
if ($rulesResponse -and $rulesResponse.status -eq "success") {
    $groups = $rulesResponse.data.groups
    $ruleCount = 0
    foreach ($group in $groups) {
        $ruleCount += $group.rules.Count
    }

    if ($ruleCount -gt 0) {
        Log-Pass "告警规则总数: $ruleCount"
    } else {
        Log-Fail "告警规则数: 0 (规则未加载)"
    }

    # 检查关键告警规则
    $criticalAlerts = @("RedisDown", "RedisMemoryHigh", "BackendDown", "HostHighCpuLoad")
    $allRules = @()
    foreach ($group in $groups) {
        $allRules += $group.rules
    }

    foreach ($alertName in $criticalAlerts) {
        $exists = $false
        foreach ($rule in $allRules) {
            if ($rule.name -eq $alertName) {
                $exists = $true
                break
            }
        }
        if ($exists) {
            Log-Pass "告警规则 '$alertName': 已配置"
        } else {
            Log-Fail "告警规则 '$alertName': 未配置"
        }
    }
} else {
    Log-Fail "无法获取 Prometheus 告警规则"
}

# ============================================================
# 汇总报告
# ============================================================

Log-Section "汇总报告"

$total = $script:PASS + $script:FAIL + $script:WARN
Write-Host ""
Write-Host "  检查项总数: $total" -ForegroundColor Cyan
Write-Host "  通过: $($script:PASS)" -ForegroundColor Green
Write-Host "  失败: $($script:FAIL)" -ForegroundColor Red
Write-Host "  警告: $($script:WARN)" -ForegroundColor Yellow
Write-Host ""

if ($script:FAIL -eq 0) {
    if ($script:WARN -eq 0) {
        Write-Host "  ========================================" -ForegroundColor Green
        Write-Host "    指标采集完全正常" -ForegroundColor Green
        Write-Host "  ========================================" -ForegroundColor Green
    } else {
        Write-Host "  ========================================" -ForegroundColor Yellow
        Write-Host "    基本正常, 有 $($script:WARN) 个警告" -ForegroundColor Yellow
        Write-Host "  ========================================" -ForegroundColor Yellow
    }
    $exitCode = 0
} else {
    Write-Host "  ========================================" -ForegroundColor Red
    Write-Host "    有 $($script:FAIL) 项失败, 请检查" -ForegroundColor Red
    Write-Host "  ========================================" -ForegroundColor Red
    $exitCode = 1
}

Write-Host ""
Write-Host "  访问地址:" -ForegroundColor Cyan
Write-Host "    Prometheus:  http://localhost:9090/targets"
Write-Host "    Prometheus:  http://localhost:9090/graph"
Write-Host "    Grafana:     http://localhost:3000 (admin / admin)"
Write-Host "    Alertmanager: http://localhost:9093"
Write-Host ""

exit $exitCode
