# ============================================================
# Redis 配置一键验证脚本 (PowerShell 版)
# 适用: Windows PowerShell 5.1+ / PowerShell Core 7+
# 用法: .\redis-check.ps1
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

function Invoke-RedisCli {
    param([string[]]$Args)
    $result = docker exec redis redis-cli @Args 2>$null
    return $result
}

function Get-RedisConfig {
    param([string]$Key)
    $result = docker exec redis redis-cli CONFIG GET $Key 2>$null
    # 返回格式: 第1行是键名, 第2行是值
    if ($result -and $result.Count -ge 2) {
        return $result[-1].Trim()
    }
    return $null
}

# ============================================================
# 主流程
# ============================================================

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " 竹香酒网站 - Redis 配置一键验证脚本" -ForegroundColor Cyan
Write-Host " 生成时间: 2026-08-22" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# ------------------------------------------------------------
Log-Section "1. Redis 连接验证"
# ------------------------------------------------------------

$ping = docker exec redis redis-cli ping 2>$null
if ($ping -eq "PONG") {
    Log-Pass "Redis 连接正常 (PONG)"
} else {
    Log-Fail "Redis 连接失败 (期望 PONG, 实际 $ping)"
    Log-Info "请检查 Redis 容器是否启动: docker ps | Select-String redis"
}

# Redis 版本
$serverInfo = docker exec redis redis-cli INFO server 2>$null
$versionLine = $serverInfo | Where-Object { $_ -match "^redis_version:" }
if ($versionLine) {
    $version = $versionLine.Split(":")[1].Trim()
    Log-Pass "Redis 版本: $version"
} else {
    Log-Fail "无法获取 Redis 版本"
}

# ------------------------------------------------------------
Log-Section "2. 环境变量验证 (Backend 容器)"
# ------------------------------------------------------------

$backendEnv = docker exec backend env 2>$null
$redisUrl = ($backendEnv | Where-Object { $_ -match "^REDIS_URL=" }) -replace "REDIS_URL=", ""
$lockMode = ($backendEnv | Where-Object { $_ -match "^LOCK_MODE=" }) -replace "LOCK_MODE=", ""
$storeMode = ($backendEnv | Where-Object { $_ -match "^STORE_MODE=" }) -replace "STORE_MODE=", ""

if ($redisUrl) {
    Log-Pass "REDIS_URL = $redisUrl"
    if ($redisUrl -match "^redis://.+:\d+/\d+$") {
        Log-Pass "REDIS_URL 格式正确"
    } elseif ($redisUrl -match "^rediss://.+:\d+/\d+$") {
        Log-Pass "REDIS_URL 使用 TLS 加密"
    } else {
        Log-Warn "REDIS_URL 格式可能不正确"
    }
} else {
    Log-Fail "REDIS_URL 未配置"
}

if ($lockMode -eq "redis") {
    Log-Pass "LOCK_MODE = redis (生产模式)"
} else {
    Log-Fail "LOCK_MODE = $lockMode (期望 redis)"
}

if ($storeMode -eq "redis") {
    Log-Pass "STORE_MODE = redis (生产模式)"
} else {
    Log-Fail "STORE_MODE = $storeMode (期望 redis)"
}

if ($lockMode -eq $storeMode) {
    Log-Pass "LOCK_MODE 与 STORE_MODE 一致"
} else {
    Log-Fail "LOCK_MODE ($lockMode) 与 STORE_MODE ($storeMode) 不一致"
}

# ------------------------------------------------------------
Log-Section "3. Redis 持久化配置"
# ------------------------------------------------------------

$appendonly = Get-RedisConfig "appendonly"
if ($appendonly -eq "yes") {
    Log-Pass "AOF 持久化已开启 (appendonly = yes)"
} else {
    Log-Fail "AOF 持久化未开启 (appendonly = $appendonly)"
    Log-Info "建议: redis-server --appendonly yes"
}

$appendfsync = Get-RedisConfig "appendfsync"
if ($appendfsync -eq "everysec" -or $appendfsync -eq "always") {
    Log-Pass "AOF 刷新策略: $appendfsync"
} else {
    Log-Warn "AOF 刷新策略: $appendfsync (建议 everysec)"
}

# 数据卷挂载验证
$mounts = docker inspect redis --format='{{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}' 2>$null
if ($mounts -match ":/data") {
    Log-Pass "数据卷挂载到 /data"
} else {
    Log-Warn "未检测到数据卷挂载 (/data)"
}

# ------------------------------------------------------------
Log-Section "4. Redis 内存配置"
# ------------------------------------------------------------

$maxmemory = Get-RedisConfig "maxmemory"
if ($maxmemory -and $maxmemory -ne "0") {
    $maxmemoryMB = [math]::Round([int64]$maxmemory / 1024 / 1024)
    Log-Pass "最大内存限制: ${maxmemoryMB}MB"
} else {
    Log-Warn "未设置内存上限 (maxmemory=0, 可能导致 OOM)"
}

$maxmemoryPolicy = Get-RedisConfig "maxmemory-policy"
if ($maxmemoryPolicy -eq "allkeys-lru" -or $maxmemoryPolicy -eq "allkeys-lfu") {
    Log-Pass "内存淘汰策略: $maxmemoryPolicy"
} else {
    Log-Warn "内存淘汰策略: $maxmemoryPolicy (建议 allkeys-lru)"
}

# 当前内存使用
$memoryInfo = docker exec redis redis-cli INFO memory 2>$null
$usedMemoryHuman = ($memoryInfo | Where-Object { $_ -match "^used_memory_human:" }) -replace "used_memory_human:", ""
if ($usedMemoryHuman) {
    Log-Pass "当前内存使用: $($usedMemoryHuman.Trim())"
}

# ------------------------------------------------------------
Log-Section "5. Redis 连接数"
# ------------------------------------------------------------

$clientsInfo = docker exec redis redis-cli INFO clients 2>$null
$connectedClients = ($clientsInfo | Where-Object { $_ -match "^connected_clients:" }) -replace "connected_clients:", ""
if ($connectedClients) {
    $clients = [int]$connectedClients.Trim()
    Log-Pass "当前连接数: $clients"
    if ($clients -gt 100) {
        Log-Warn "连接数较高 (>100)"
    }
} else {
    Log-Fail "无法获取连接数"
}

$maxclients = Get-RedisConfig "maxclients"
if ($maxclients) {
    Log-Info "最大连接数配置: $maxclients"
}

# ------------------------------------------------------------
Log-Section "6. 博主模块 Key 验证"
# ------------------------------------------------------------

$influencerKeys = docker exec redis redis-cli KEYS "zhuxiang:traffic:influencer*" 2>$null
if ($influencerKeys -and $influencerKeys.Count -gt 0) {
    $count = $influencerKeys.Count
    if ($influencerKeys -is [string]) { $count = 1 }
    Log-Pass "博主相关 Key: $count 个"
} else {
    Log-Warn "未发现博主相关 Key (可能尚未创建数据)"
}

$platformKeys = docker exec redis redis-cli KEYS "zhuxiang:traffic:influencer_platform*" 2>$null
if ($platformKeys -and $platformKeys.Count -gt 0) {
    $count = $platformKeys.Count
    if ($platformKeys -is [string]) { $count = 1 }
    Log-Pass "平台账号 Key: $count 个"
}

$codeKeys = docker exec redis redis-cli KEYS "zhuxiang:traffic:influencer_code*" 2>$null
if ($codeKeys -and $codeKeys.Count -gt 0) {
    $count = $codeKeys.Count
    if ($codeKeys -is [string]) { $count = 1 }
    Log-Pass "推广码 Key: $count 个"
}

# 序列号验证
$seq = docker exec redis redis-cli GET "zhuxiang:traffic:traffic_influencer:seq" 2>$null
if ($seq -and $seq -ne "(nil)") {
    Log-Pass "博主序列号: $($seq.Trim())"
} else {
    Log-Warn "博主序列号未初始化 (seed 未执行?)"
}

# ------------------------------------------------------------
Log-Section "7. 锁机制验证"
# ------------------------------------------------------------

$lockKeys = docker exec redis redis-cli KEYS "lock:traffic:*" 2>$null
if ($lockKeys -and $lockKeys.Count -gt 0) {
    $count = $lockKeys.Count
    if ($lockKeys -is [string]) { $count = 1 }
    Log-Pass "流量锁 Key: $count 个 (活跃中)"
} else {
    Log-Pass "流量锁 Key: 0 个 (无活跃锁)"
}

$allLocks = docker exec redis redis-cli KEYS "lock:*" 2>$null
$allLockCount = if ($allLocks) { $allLocks.Count } else { 0 }
if ($allLocks -is [string]) { $allLockCount = 1 }
Log-Info "所有锁 Key: $allLockCount 个"

# 锁 TTL 验证
$firstLock = if ($allLocks -is [string]) { $allLocks } else { $allLocks[0] }
if ($firstLock) {
    $ttl = docker exec redis redis-cli TTL $firstLock 2>$null
    if ($ttl -gt 0) {
        Log-Pass "锁 TTL 验证: ${ttl}s (key=$firstLock)"
    } elseif ($ttl -eq -1) {
        Log-Warn "锁无过期时间: $firstLock (可能导致死锁)"
    }
}

# ------------------------------------------------------------
Log-Section "8. Key 前缀规范验证"
# ------------------------------------------------------------

$dbsize = docker exec redis redis-cli DBSIZE 2>$null
$zhuxiangKeys = docker exec redis redis-cli KEYS "zhuxiang:*" 2>$null
$lockPrefixKeys = docker exec redis redis-cli KEYS "lock:*" 2>$null

$zhuxiangCount = if ($zhuxiangKeys) { if ($zhuxiangKeys -is [string]) { 1 } else { $zhuxiangKeys.Count } } else { 0 }
$lockPrefixCount = if ($lockPrefixKeys) { if ($lockPrefixKeys -is [string]) { 1 } else { $lockPrefixKeys.Count } } else { 0 }

if ($dbsize) {
    $totalKeys = [int]$dbsize.Trim()
    $expected = $zhuxiangCount + $lockPrefixCount
    if ($expected -ge $totalKeys) {
        Log-Pass "Key 前缀规范: 全部使用 zhuxiang: 或 lock: 前缀"
    } else {
        $nonStandard = $totalKeys - $expected
        Log-Warn "发现非标准前缀的 Key: $nonStandard 个"
    }
    Log-Info "Key 总数: $totalKeys (zhuxiang: $zhuxiangCount, lock: $lockPrefixCount)"
}

# ------------------------------------------------------------
Log-Section "9. 慢查询日志"
# ------------------------------------------------------------

$slowlogLen = docker exec redis redis-cli SLOWLOG LEN 2>$null
if ($slowlogLen -ne $null) {
    Log-Pass "慢查询日志条数: $($slowlogLen.Trim())"
    if ([int]$slowlogLen.Trim() -gt 0) {
        $slowlogSample = docker exec redis redis-cli SLOWLOG GET 1 2>$null
        if ($slowlogSample) {
            Log-Warn "存在慢查询, 最近一条:"
            $slowlogSample | ForEach-Object { Write-Host "    $_" }
        }
    }
} else {
    Log-Fail "无法获取慢查询日志"
}

$slowlogThreshold = Get-RedisConfig "slowlog-log-slower-than"
if ($slowlogThreshold) {
    $thresholdMs = [math]::Round([int64]$slowlogThreshold / 1000)
    Log-Info "慢查询阈值: ${thresholdMs}ms"
}

# ------------------------------------------------------------
Log-Section "10. 安全配置验证"
# ------------------------------------------------------------

# 密码验证
$requirepass = Get-RedisConfig "requirepass"
if (-not $requirepass -or $requirepass -eq "") {
    Log-Fail "Redis 未设置密码 (requirepass 为空)"
    Log-Info "生产环境必须设置密码: CONFIG SET requirepass 'your_password'"
} elseif ($requirepass -eq "your_strong_password_here") {
    Log-Warn "Redis 密码为示例值, 请修改"
} else {
    Log-Pass "Redis 已设置密码"
}

# 绑定地址
$bind = Get-RedisConfig "bind"
if ($bind -eq "0.0.0.0") {
    Log-Warn "Redis 绑定 0.0.0.0 (生产建议绑定内网)"
} elseif ($bind -eq "127.0.0.1") {
    Log-Pass "Redis 绑定 127.0.0.1 (仅本地访问)"
} elseif ($bind) {
    Log-Pass "Redis 绑定: $bind"
}

# protected-mode
$protected = Get-RedisConfig "protected-mode"
if ($protected -eq "yes") {
    Log-Pass "protected-mode 已开启"
} else {
    Log-Warn "protected-mode 未开启 (建议开启)"
}

# ------------------------------------------------------------
Log-Section "11. 容器健康状态"
# ------------------------------------------------------------

$redisStatus = docker inspect redis --format='{{.State.Status}}' 2>$null
if ($redisStatus -eq "running") {
    Log-Pass "Redis 容器状态: running"
} else {
    Log-Fail "Redis 容器状态: $redisStatus"
}

$redisHealth = docker inspect redis --format='{{.State.Health.Status}}' 2>$null
if ($redisHealth -eq "healthy") {
    Log-Pass "Redis 健康检查: healthy"
} elseif ($redisHealth -eq "none" -or -not $redisHealth) {
    Log-Warn "Redis 未配置健康检查"
} else {
    Log-Fail "Redis 健康检查: $redisHealth"
}

$backendStatus = docker inspect backend --format='{{.State.Status}}' 2>$null
if ($backendStatus -eq "running") {
    Log-Pass "Backend 容器状态: running"
} else {
    Log-Fail "Backend 容器状态: $backendStatus"
}

$backendHealth = docker inspect backend --format='{{.State.Health.Status}}' 2>$null
if ($backendHealth -eq "healthy") {
    Log-Pass "Backend 健康检查: healthy"
} elseif ($backendHealth -eq "none" -or -not $backendHealth) {
    Log-Warn "Backend 未配置健康检查"
} else {
    Log-Fail "Backend 健康检查: $backendHealth"
}

# ------------------------------------------------------------
Log-Section "12. Backend 接口验证"
# ------------------------------------------------------------

try {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/api/health" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Log-Pass "Backend 健康检查接口: 200 OK"
    } else {
        Log-Fail "Backend 健康检查接口: HTTP $($response.StatusCode)"
    }
} catch {
    Log-Fail "Backend 健康检查接口: 无法访问 (期望 200)"
}

# 博主列表接口
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/api/traffic/influencer/list" -Headers @{"X-Member-Id"="1"} -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Log-Pass "博主列表接口: 200 OK"
    } else {
        Log-Fail "博主列表接口: HTTP $($response.StatusCode)"
    }
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 401) {
        Log-Warn "博主列表接口: 401 (需 X-Member-Id 头)"
    } else {
        Log-Fail "博主列表接口: HTTP $statusCode"
    }
}

# ------------------------------------------------------------
Log-Section "13. Uvicorn workers 配置"
# ------------------------------------------------------------

$workerCount = (docker exec backend ps aux 2>$null | Select-String "[u]vicorn").Count
if ($workerCount -eq 1) {
    Log-Pass "Uvicorn workers = 1 (单进程, 避免锁竞争)"
} elseif ($workerCount -gt 1) {
    Log-Warn "Uvicorn 进程数: $workerCount (建议 1, 避免多进程锁问题)"
} else {
    Log-Info "无法检测 Uvicorn 进程数"
}

# ------------------------------------------------------------
Log-Section "14. Redis 主从复制(如适用)"
# ------------------------------------------------------------

$replicationInfo = docker exec redis redis-cli INFO replication 2>$null
$role = ($replicationInfo | Where-Object { $_ -match "^role:" }) -replace "role:", ""
if ($role) {
    $role = $role.Trim()
    if ($role -eq "master") {
        Log-Pass "Redis 角色: master"
        $slaveCount = ($replicationInfo | Where-Object { $_ -match "^connected_slaves:" }) -replace "connected_slaves:", ""
        if ($slaveCount) {
            $slaveCount = $slaveCount.Trim()
            if ([int]$slaveCount -gt 0) {
                Log-Pass "从节点数量: $slaveCount"
            } else {
                Log-Info "无从节点 (单机模式)"
            }
        }
    } elseif ($role -eq "slave") {
        Log-Pass "Redis 角色: slave"
        $masterLinkStatus = ($replicationInfo | Where-Object { $_ -match "^master_link_status:" }) -replace "master_link_status:", ""
        if ($masterLinkStatus) {
            if ($masterLinkStatus.Trim() -eq "up") {
                Log-Pass "主从连接状态: up"
            } else {
                Log-Fail "主从连接状态: $($masterLinkStatus.Trim())"
            }
        }
    }
} else {
    Log-Warn "无法确定 Redis 角色"
}

# ------------------------------------------------------------
Log-Section "15. Redis 延迟测试"
# ------------------------------------------------------------

$latency = docker exec redis redis-cli --latency -n 10 2>$null | Select-Object -Last 1
if ($latency) {
    Log-Pass "Redis 延迟: $($latency.Trim())"
} else {
    Log-Warn "无法测试 Redis 延迟"
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
        Write-Host "    Redis 配置检查全部通过!" -ForegroundColor Green
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
Write-Host "    - Redis INFO: docker exec redis redis-cli INFO"
Write-Host "    - 慢查询: docker exec redis redis-cli SLOWLOG GET 10"
Write-Host "    - Key 分析: docker exec redis redis-cli KEYS '*' | Select-Object -First 20"
Write-Host ""

exit $exitCode
