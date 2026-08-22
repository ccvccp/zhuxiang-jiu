﻿# ============================================================
# Redis 集群 + Prometheus 监控一键验证脚本 (PowerShell 版)
# 适用: Windows PowerShell 5.1+ / PowerShell Core 7+
# 用法: .\ha-monitor-check.ps1
# 编码: UTF-8 with BOM (PowerShell 5.1 兼容)
# ============================================================

#Requires -Version 5.1
$ErrorActionPreference = "SilentlyContinue"

# ============================================================
# 计数器与辅助函数
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

function Log-Info {
    param([string]$Msg)
    Write-Host "  [INFO] $Msg" -ForegroundColor Cyan
}

function Log-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host " $Title" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

function Check-Container {
    param([string]$Name)
    $status = docker inspect $Name --format='{{.State.Status}}' 2>$null
    if ($status -eq "running") {
        Log-Pass "$Name 容器: running"
    } else {
        Log-Fail "$Name 容器: $status"
    }
}

function Query-Prometheus {
    param([string]$Query)
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:9090/api/v1/query?query=$Query" -TimeoutSec 5
        if ($response.status -eq "success" -and $response.data.result.Count -gt 0) {
            return $response.data.result[0].value[1]
        }
        return $null
    } catch {
        return $null
    }
}

# ============================================================
# 主流程
# ============================================================

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Redis 集群 + Prometheus 监控验证脚本" -ForegroundColor Cyan
Write-Host " 生成时间: 2026-08-22" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# ------------------------------------------------------------
Log-Section "1. Redis 主从复制状态"
# ------------------------------------------------------------

$replication = docker exec redis redis-cli INFO replication 2>$null
if ($replication) {
    $role = ($replication | Where-Object { $_ -match "^role:" }) -replace "role:", ""
    if ($role) {
        $role = $role.Trim()
        if ($role -eq "master") {
            Log-Pass "Redis 角色: master"
            $slaveCount = ($replication | Where-Object { $_ -match "^connected_slaves:" }) -replace "connected_slaves:", ""
            if ($slaveCount) {
                $count = [int]$slaveCount.Trim()
                if ($count -gt 0) {
                    Log-Pass "从节点数量: $count"
                } else {
                    Log-Warn "无从节点 (单机模式)"
                }
            }
        } elseif ($role -eq "slave") {
            Log-Pass "Redis 角色: slave"
            $masterLink = ($replication | Where-Object { $_ -match "^master_link_status:" }) -replace "master_link_status:", ""
            if ($masterLink) {
                if ($masterLink.Trim() -eq "up") {
                    Log-Pass "主从连接状态: up"
                } else {
                    Log-Fail "主从连接状态: $($masterLink.Trim())"
                }
            }
        } else {
            Log-Warn "无法确定 Redis 角色"
        }
    }
} else {
    Log-Fail "无法获取 Redis 复制信息"
}

# ------------------------------------------------------------
Log-Section "2. Redis Sentinel 哨兵状态"
# ------------------------------------------------------------

$sentinelInfo = docker exec sentinel1 redis-cli -p 26379 SENTINEL masters 2>$null
if ($sentinelInfo) {
    Log-Pass "Sentinel1 可访问"
    
    # 查询 master 地址
    $masterAddr = docker exec sentinel1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster 2>$null
    if ($masterAddr) {
        Log-Pass "Sentinel master 地址: $($masterAddr[0]):$($masterAddr[1])"
    }
    
    # 检查 Sentinel 数量
    $sentinels = docker exec sentinel1 redis-cli -p 26379 SENTINEL sentinels mymaster 2>$null
    $sentinelCount = if ($sentinels) { ($sentinels | Select-String "ip" -SimpleMatch).Count + 1 } else { 1 }
    if ($sentinelCount -ge 3) {
        Log-Pass "Sentinel 节点数: $sentinelCount (≥3)"
    } else {
        Log-Warn "Sentinel 节点数: $sentinelCount (<3, 建议至少3个)"
    }
} else {
    Log-Warn "Sentinel 未部署 (单机模式可跳过)"
}

# ------------------------------------------------------------
Log-Section "3. Redis Cluster 集群状态"
# ------------------------------------------------------------

$clusterInfo = docker exec redis redis-cli CLUSTER INFO 2>$null
if ($clusterInfo -and $clusterInfo -notmatch "cluster support disabled") {
    $clusterState = ($clusterInfo | Where-Object { $_ -match "^cluster_state:" }) -replace "cluster_state:", ""
    if ($clusterState) {
        $state = $clusterState.Trim()
        if ($state -eq "ok") {
            Log-Pass "Cluster 状态: ok"
        } else {
            Log-Fail "Cluster 状态: $state"
        }
    }
    
    $slotsAssigned = ($clusterInfo | Where-Object { $_ -match "^cluster_slots_assigned:" }) -replace "cluster_slots_assigned:", ""
    if ($slotsAssigned) {
        $assigned = [int]$slotsAssigned.Trim()
        if ($assigned -eq 16384) {
            Log-Pass "Slot 分配: 16384/16384 (100%)"
        } else {
            Log-Fail "Slot 分配: $assigned/16384 (未完全分配)"
        }
    }
    
    $knownNodes = ($clusterInfo | Where-Object { $_ -match "^cluster_known_nodes:" }) -replace "cluster_known_nodes:", ""
    if ($knownNodes) {
        Log-Info "集群节点数: $($knownNodes.Trim())"
    }
} else {
    Log-Warn "Cluster 未启用 (单机或哨兵模式可跳过)"
}

# ------------------------------------------------------------
Log-Section "4. Redis 高可用指标"
# ------------------------------------------------------------

# 主从延迟
$replOffset = ($replication | Where-Object { $_ -match "^master_repl_offset:" }) -replace "master_repl_offset:", ""
$slaveLine = ($replication | Where-Object { $_ -match "^slave0:" })
if ($replOffset -and $slaveLine) {
    $slaveOffset = if ($slaveLine -match "offset=(\d+)") { $matches[1] } else { "0" }
    $offsetDiff = [int64]$replOffset.Trim() - [int64]$slaveOffset
    if ($offsetDiff -lt 1000000) {
        Log-Pass "主从延迟: $offsetDiff bytes (<1MB)"
    } else {
        Log-Warn "主从延迟: $offsetDiff bytes (>1MB)"
    }
}

# 内存碎片率
$memInfo = docker exec redis redis-cli INFO memory 2>$null
$fragRatio = ($memInfo | Where-Object { $_ -match "^mem_fragmentation_ratio:" }) -replace "mem_fragmentation_ratio:", ""
if ($fragRatio) {
    $ratio = [double]$fragRatio.Trim()
    if ($ratio -lt 1.5) {
        Log-Pass "内存碎片率: $ratio (<1.5)"
    } else {
        Log-Warn "内存碎片率: $ratio (>1.5, 建议重启 Redis)"
    }
}

# 键空间命中率
$statsInfo = docker exec redis redis-cli INFO stats 2>$null
$hits = ($statsInfo | Where-Object { $_ -match "^keyspace_hits:" }) -replace "keyspace_hits:", ""
$misses = ($statsInfo | Where-Object { $_ -match "^keyspace_misses:" }) -replace "keyspace_misses:", ""
if ($hits -and $misses) {
    $h = [int64]$hits.Trim()
    $m = [int64]$misses.Trim()
    $total = $h + $m
    if ($total -gt 0) {
        $hitRate = [math]::Round($h * 100 / $total)
        if ($hitRate -ge 90) {
            Log-Pass "键空间命中率: ${hitRate}% (≥90%)"
        } else {
            Log-Warn "键空间命中率: ${hitRate}% (<90%)"
        }
    }
}

# ------------------------------------------------------------
Log-Section "5. 监控组件容器状态"
# ------------------------------------------------------------

Check-Container "redis-exporter"
Check-Container "node-exporter"
Check-Container "cadvisor"
Check-Container "prometheus"
Check-Container "alertmanager"
Check-Container "grafana"

# ------------------------------------------------------------
Log-Section "6. Prometheus 采集 Targets"
# ------------------------------------------------------------

try {
    $targetsResponse = Invoke-RestMethod -Uri "http://localhost:9090/api/v1/targets" -TimeoutSec 5
    $activeTargets = $targetsResponse.data.activeTargets
    $upCount = ($activeTargets | Where-Object { $_.health -eq "up" }).Count
    $totalCount = $activeTargets.Count
    
    if ($upCount -eq $totalCount -and $totalCount -gt 0) {
        Log-Pass "Targets: $upCount/$totalCount 全部 UP"
    } else {
        Log-Warn "Targets: $upCount/$totalCount (有 DOWN 的 target)"
    }
    
    # 检查各 job 状态
    foreach ($job in @("redis", "node", "docker", "backend")) {
        $jobUp = ($activeTargets | Where-Object { $_.labels.job -eq $job -and $_.health -eq "up" }).Count
        if ($jobUp -gt 0) {
            Log-Pass "Job '$job': UP"
        } else {
            Log-Fail "Job '$job': DOWN 或不存在"
        }
    }
} catch {
    Log-Fail "无法访问 Prometheus API"
}

# ------------------------------------------------------------
Log-Section "7. Prometheus 指标验证"
# ------------------------------------------------------------

$metrics = @(
    @{ Name = "redis_up"; Query = "redis_up" },
    @{ Name = "redis_memory_used"; Query = "redis_memory_used_bytes" },
    @{ Name = "redis_connected_clients"; Query = "redis_connected_clients" },
    @{ Name = "redis_memory_fragmentation_ratio"; Query = "redis_memory_fragmentation_ratio" },
    @{ Name = "redis_commands_processed"; Query = "rate(redis_commands_processed_total[5m])" },
    @{ Name = "redis_keyspace_hits_rate"; Query = "rate(redis_keyspace_hits_total[5m])" }
)

foreach ($metric in $metrics) {
    $value = Query-Prometheus -Query $metric.Query
    if ($value -ne $null) {
        Log-Pass "$($metric.Name) = $value"
    } else {
        Log-Warn "$($metric.Name): 无数据"
    }
}

# ------------------------------------------------------------
Log-Section "8. Prometheus 告警规则"
# ------------------------------------------------------------

try {
    $rulesResponse = Invoke-RestMethod -Uri "http://localhost:9090/api/v1/rules" -TimeoutSec 5
    $groups = $rulesResponse.data.groups
    $ruleCount = 0
    foreach ($group in $groups) {
        $ruleCount += $group.rules.Count
    }
    
    if ($ruleCount -gt 0) {
        Log-Pass "告警规则数: $ruleCount"
    } else {
        Log-Fail "告警规则数: 0 (规则未加载)"
    }
    
    # 检查关键告警规则
    $criticalAlerts = @("RedisDown", "RedisMemoryHigh", "RedisTooManyConnections", "BackendDown", "HostHighCpuLoad")
    $allRules = @()
    foreach ($group in $groups) {
        $allRules += $group.rules
    }
    
    foreach ($alertName in $criticalAlerts) {
        $exists = $allRules | Where-Object { $_.name -eq $alertName }
        if ($exists) {
            Log-Pass "告警规则 '$alertName': 已配置"
        } else {
            Log-Fail "告警规则 '$alertName': 未配置"
        }
    }
} catch {
    Log-Fail "无法访问 Prometheus 告警规则 API"
}

# ------------------------------------------------------------
Log-Section "9. Alertmanager 状态"
# ------------------------------------------------------------

try {
    $alertStatus = Invoke-RestMethod -Uri "http://localhost:9093/api/v2/status" -TimeoutSec 5
    Log-Pass "Alertmanager 可访问"
    
    # 检查告警数
    $alerts = Invoke-RestMethod -Uri "http://localhost:9093/api/v2/alerts" -TimeoutSec 5
    $alertCount = $alerts.Count
    
    if ($alertCount -eq 0) {
        Log-Pass "当前活跃告警: 0 (正常)"
    } else {
        Log-Warn "当前活跃告警: $alertCount (需关注)"
    }
} catch {
    Log-Fail "Alertmanager 不可访问"
}

# ------------------------------------------------------------
Log-Section "10. Grafana 状态"
# ------------------------------------------------------------

try {
    $grafanaStatus = Invoke-RestMethod -Uri "http://localhost:3000/api/health" -TimeoutSec 5
    if ($grafanaStatus.database -eq "ok") {
        Log-Pass "Grafana 健康: ok"
    } else {
        Log-Warn "Grafana 数据库状态: $($grafanaStatus.database)"
    }
    
    # 检查数据源
    try {
        $datasources = Invoke-RestMethod -Uri "http://localhost:3000/api/datasources" -Headers @{Authorization = "Basic YWRtaW46YWRtaW4="} -TimeoutSec 5
        $dsCount = $datasources.Count
        if ($dsCount -gt 0) {
            Log-Pass "Grafana 数据源: $dsCount 个"
        } else {
            Log-Fail "Grafana 数据源: 0 (未配置)"
        }
    } catch {
        Log-Warn "无法获取 Grafana 数据源 (可能需要认证)"
    }
} catch {
    Log-Fail "Grafana 不可访问"
}

# ------------------------------------------------------------
Log-Section "11. Redis Exporter 指标"
# ------------------------------------------------------------

try {
    $exporterMetrics = Invoke-WebRequest -Uri "http://localhost:9121/metrics" -UseBasicParsing -TimeoutSec 5
    Log-Pass "Redis Exporter 可访问"
    
    $content = $exporterMetrics.Content
    
    if ($content -match "^redis_up") {
        Log-Pass "redis_up 指标存在"
    } else {
        Log-Fail "redis_up 指标不存在"
    }
    
    if ($content -match "redis_memory_used_bytes") {
        Log-Pass "redis_memory_used_bytes 指标存在"
    } else {
        Log-Fail "redis_memory_used_bytes 指标不存在"
    }
    
    if ($content -match "redis_connected_clients") {
        Log-Pass "redis_connected_clients 指标存在"
    } else {
        Log-Fail "redis_connected_clients 指标不存在"
    }
} catch {
    Log-Fail "Redis Exporter 不可访问"
}

# ------------------------------------------------------------
Log-Section "12. Node Exporter 指标"
# ------------------------------------------------------------

try {
    $nodeMetrics = Invoke-WebRequest -Uri "http://localhost:9100/metrics" -UseBasicParsing -TimeoutSec 5
    Log-Pass "Node Exporter 可访问"
    
    $content = $nodeMetrics.Content
    
    if ($content -match "node_cpu_seconds_total") {
        Log-Pass "node_cpu 指标存在"
    } else {
        Log-Fail "node_cpu 指标不存在"
    }
    
    if ($content -match "node_memory_MemAvailable_bytes") {
        Log-Pass "node_memory 指标存在"
    } else {
        Log-Fail "node_memory 指标不存在"
    }
} catch {
    Log-Fail "Node Exporter 不可访问"
}

# ------------------------------------------------------------
Log-Section "13. cAdvisor 容器指标"
# ------------------------------------------------------------

try {
    $cadvisorMetrics = Invoke-WebRequest -Uri "http://localhost:8080/metrics" -UseBasicParsing -TimeoutSec 5
    Log-Pass "cAdvisor 可访问"
    
    if ($cadvisorMetrics.Content -match "container_memory_usage_bytes") {
        Log-Pass "container_memory 指标存在"
    } else {
        Log-Fail "container_memory 指标不存在"
    }
} catch {
    Log-Fail "cAdvisor 不可访问"
}

# ------------------------------------------------------------
Log-Section "14. 故障转移测试(哨兵模式)"
# ------------------------------------------------------------

if ($sentinelInfo) {
    Log-Info "执行故障转移测试(可选, 需手动确认)"
    Log-Info "测试命令:"
    Write-Host "    1. docker stop redis-master"
    Write-Host "    2. Start-Sleep -Seconds 30"
    Write-Host "    3. docker exec sentinel1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster"
    Write-Host "    4. Invoke-WebRequest http://localhost:8000/api/health"
    Write-Host "    5. docker start redis-master"
    Log-Warn "故障转移测试需手动执行(避免影响生产)"
} else {
    Log-Info "哨兵未部署, 跳过故障转移测试"
}

# ============================================================
# 汇总报告
# ============================================================

Log-Section "汇总报告"

$total = $script:PASS + $script:FAIL + $script:WARN
Write-Host ""
Write-Host "  检查项总数: $total"
Write-Host "  通过: $($script:PASS)" -ForegroundColor Green
Write-Host "  失败: $($script:FAIL)" -ForegroundColor Red
Write-Host "  警告: $($script:WARN)" -ForegroundColor Yellow
Write-Host ""

if ($script:FAIL -eq 0) {
    if ($script:WARN -eq 0) {
        Write-Host "  ========================================" -ForegroundColor Green
        Write-Host "    集群与监控配置检查全部通过!" -ForegroundColor Green
        Write-Host "  ========================================" -ForegroundColor Green
    } else {
        Write-Host "  ========================================" -ForegroundColor Yellow
        Write-Host "    基本配置通过, 有 $($script:WARN) 个警告" -ForegroundColor Yellow
        Write-Host "  ========================================" -ForegroundColor Yellow
    }
    $exitCode = 0
} else {
    Write-Host "  ========================================" -ForegroundColor Red
    Write-Host "    有 $($script:FAIL) 项检查失败, 请修复后再上线" -ForegroundColor Red
    Write-Host "  ========================================" -ForegroundColor Red
    $exitCode = 1
}

Write-Host ""
Write-Host "  详细日志:" -ForegroundColor Cyan
Write-Host "    - Prometheus Targets: http://localhost:9090/targets"
Write-Host "    - Prometheus 告警: http://localhost:9090/rules"
Write-Host "    - Prometheus 查询: http://localhost:9090/graph"
Write-Host "    - Alertmanager: http://localhost:9093"
Write-Host "    - Grafana: http://localhost:3000"
Write-Host "    - Redis Exporter: http://localhost:9121/metrics"
Write-Host ""

exit $exitCode
