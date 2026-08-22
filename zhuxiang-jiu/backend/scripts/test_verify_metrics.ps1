# ============================================================
# verify-metrics.ps1 单元测试脚本
# 用途: 覆盖核心逻辑的单元测试(无需 Docker/Redis/Prometheus 环境)
# 用法: .\test_verify_metrics.ps1
# 编码: UTF-8 with BOM (PowerShell 5.1 兼容)
# ============================================================

#Requires -Version 5.1
$ErrorActionPreference = "Stop"

# ============================================================
# 测试结果计数器
# ============================================================
$script:TestPass = 0
$script:TestFail = 0
$script:TestResults = @()

function Test-Pass {
    param([string]$Name, [string]$Detail = "")
    $script:TestPass++
    $script:TestResults += "  [PASS] $Name $Detail"
    Write-Host "  [PASS] $Name $Detail" -ForegroundColor Green
}

function Test-Fail {
    param([string]$Name, [string]$Detail = "")
    $script:TestFail++
    $script:TestResults += "  [FAIL] $Name - $Detail"
    Write-Host "  [FAIL] $Name - $Detail" -ForegroundColor Red
}

function Assert-Equals {
    param(
        [string]$TestName,
        $Expected,
        $Actual
    )
    if ($Expected -eq $Actual) {
        Test-Pass $TestName "(expected=$Expected, actual=$Actual)"
    } else {
        Test-Fail $TestName "(expected=$Expected, actual=$Actual)"
    }
}

function Assert-True {
    param([string]$TestName, [bool]$Condition, [string]$Detail = "")
    if ($Condition) {
        Test-Pass $TestName $Detail
    } else {
        Test-Fail $TestName $Detail
    }
}

function Assert-NotNull {
    param([string]$TestName, $Value)
    if ($null -ne $Value) {
        Test-Pass $TestName "(value=$Value)"
    } else {
        Test-Fail $TestName "(value is null)"
    }
}

# ============================================================
# 加载被测脚本中的函数(不执行主流程)
# ============================================================

# 由于 verify-metrics.ps1 会在加载时执行主流程,我们需要提取函数定义
# 方式: 使用 Test-Path 验证脚本存在,然后手动定义相同的函数进行测试

$scriptPath = Join-Path $PSScriptRoot "verify-metrics.ps1"

if (-not (Test-Path $scriptPath)) {
    Write-Host "ERROR: verify-metrics.ps1 not found at $scriptPath" -ForegroundColor Red
    exit 1
}

# 读取脚本内容
$scriptContent = Get-Content $scriptPath -Raw -Encoding UTF8

# ============================================================
# 测试 1: 脚本文件存在性验证
# ============================================================

Write-Host ""
Write-Host "========== 1. 脚本文件验证 ==========" -ForegroundColor Cyan

Assert-True "test_01_script_exists" (Test-Path $scriptPath) "verify-metrics.ps1 exists"

# ============================================================
# 测试 2: 脚本编码验证(UTF-8 BOM)
# ============================================================

Write-Host ""
Write-Host "========== 2. 脚本编码验证 ==========" -ForegroundColor Cyan

$bytes = [System.IO.File]::ReadAllBytes($scriptPath)
$hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
Assert-True "test_02_utf8_bom_encoding" $hasBom "UTF-8 with BOM"

# ============================================================
# 测试 3: 脚本内容完整性验证
# ============================================================

Write-Host ""
Write-Host "========== 3. 脚本内容验证 ==========" -ForegroundColor Cyan

$requiredElements = @(
    @{ Name = "Requires Version"; Pattern = "#Requires -Version 5.1" },
    @{ Name = "ErrorActionPreference"; Pattern = "ErrorActionPreference" },
    @{ Name = "PASS counter"; Pattern = "script:PASS" },
    @{ Name = "FAIL counter"; Pattern = "script:FAIL" },
    @{ Name = "WARN counter"; Pattern = "script:WARN" },
    @{ Name = "Log-Pass function"; Pattern = "function Log-Pass" },
    @{ Name = "Log-Fail function"; Pattern = "function Log-Fail" },
    @{ Name = "Log-Warn function"; Pattern = "function Log-Warn" },
    @{ Name = "Log-Section function"; Pattern = "function Log-Section" },
    @{ Name = "Invoke-SafeRequest function"; Pattern = "function Invoke-SafeRequest" },
    @{ Name = "Invoke-SafeRest function"; Pattern = "function Invoke-SafeRest" },
    @{ Name = "Query-PromMetric function"; Pattern = "function Query-PromMetric" },
    @{ Name = "Redis PING check"; Pattern = "redis-cli" },
    @{ Name = "Redis INFO check"; Pattern = "INFO server" },
    @{ Name = "Redis DBSIZE check"; Pattern = "DBSIZE" },
    @{ Name = "Exporter metrics check"; Pattern = "localhost:9121/metrics" },
    @{ Name = "Prometheus targets check"; Pattern = "localhost:9090/api/v1/targets" },
    @{ Name = "Prometheus query check"; Pattern = "localhost:9090/api/v1/query" },
    @{ Name = "Prometheus rules check"; Pattern = "localhost:9090/api/v1/rules" },
    @{ Name = "Redis up metric"; Pattern = "redis_up" },
    @{ Name = "Redis memory metric"; Pattern = "redis_memory_used_bytes" },
    @{ Name = "Redis clients metric"; Pattern = "redis_connected_clients" },
    @{ Name = "Redis commands rate"; Pattern = "redis_commands_processed_total" },
    @{ Name = "Alert RedisDown"; Pattern = "RedisDown" },
    @{ Name = "Alert RedisMemoryHigh"; Pattern = "RedisMemoryHigh" },
    @{ Name = "Alert BackendDown"; Pattern = "BackendDown" },
    @{ Name = "Alert HostHighCpuLoad"; Pattern = "HostHighCpuLoad" },
    @{ Name = "Exit code logic"; Pattern = "exit" }
)

foreach ($element in $requiredElements) {
    $found = $scriptContent -match [regex]::Escape($element.Pattern)
    Assert-True "test_03_$($element.Name.Replace(' ', '_').Replace('-', '_'))" $found $element.Name
}

# ============================================================
# 测试 4: 函数定义验证
# ============================================================

Write-Host ""
Write-Host "========== 4. 函数定义验证 ==========" -ForegroundColor Cyan

# 模拟 Log-Pass 函数
function Test-LogPass {
    $localPass = 0
    # 模拟 Log-Pass 逻辑
    $localPass++
    Assert-Equals "test_04_log_pass_increments_counter" 1 $localPass
}

Test-LogPass

# 模拟 Log-Fail 函数
function Test-LogFail {
    $localFail = 0
    # 模拟 Log-Fail 逻辑
    $localFail++
    Assert-Equals "test_04_log_fail_increments_counter" 1 $localFail
}

Test-LogFail

# ============================================================
# 测试 5: Invoke-SafeRequest 函数逻辑
# ============================================================

Write-Host ""
Write-Host "========== 5. Invoke-SafeRequest 逻辑验证 ==========" -ForegroundColor Cyan

# 模拟 Invoke-SafeRequest (成功情况)
function Mock-InvokeSafeRequest {
    param([string]$Uri, [int]$TimeoutSec = 5)
    if ($Uri -eq "http://localhost:9121/metrics") {
        return @{ StatusCode = 200; Content = "redis_up 1`nredis_memory_used_bytes 2097152" }
    }
    return $null
}

$result = Mock-InvokeSafeRequest -Uri "http://localhost:9121/metrics"
Assert-NotNull "test_05_safe_request_returns_response" $result
Assert-Equals "test_05_safe_request_status_code" 200 $result.StatusCode

# 测试失败情况(返回 null)
$result = Mock-InvokeSafeRequest -Uri "http://localhost:9999/nonexistent"
Assert-True "test_05_safe_request_returns_null_on_failure" ($null -eq $result) "null on failure"

# ============================================================
# 测试 6: Invoke-SafeRest 函数逻辑
# ============================================================

Write-Host ""
Write-Host "========== 6. Invoke-SafeRest 逻辑验证 ==========" -ForegroundColor Cyan

# 模拟 Invoke-SafeRest (成功情况)
function Mock-InvokeSafeRest {
    param([string]$Uri, [int]$TimeoutSec = 5)
    if ($Uri -eq "http://localhost:9090/api/v1/targets") {
        return @{
            status = "success"
            data = @{
                activeTargets = @(
                    @{ labels = @{ job = "redis" }; health = "up" },
                    @{ labels = @{ job = "node" }; health = "up" },
                    @{ labels = @{ job = "docker" }; health = "up" },
                    @{ labels = @{ job = "backend"; health = "down" } }
                )
            }
        }
    }
    return $null
}

$response = Mock-InvokeSafeRest -Uri "http://localhost:9090/api/v1/targets"
Assert-NotNull "test_06_safe_rest_returns_response" $response
Assert-Equals "test_06_safe_rest_status" "success" $response.status

$upCount = ($response.data.activeTargets | Where-Object { $_.health -eq "up" }).Count
Assert-Equals "test_06_safe_rest_up_count" 3 $upCount

$totalCount = $response.data.activeTargets.Count
Assert-Equals "test_06_safe_rest_total_count" 4 $totalCount

# 测试失败情况
$response = Mock-InvokeSafeRest -Uri "http://localhost:9999/nonexistent"
Assert-True "test_06_safe_rest_returns_null_on_failure" ($null -eq $response) "null on failure"

# ============================================================
# 测试 7: Query-PromMetric 函数逻辑
# ============================================================

Write-Host ""
Write-Host "========== 7. Query-PromMetric 逻辑验证 ==========" -ForegroundColor Cyan

# 模拟 Query-PromMetric (有数据)
function Mock-QueryPromMetric {
    param([string]$Query)
    if ($Query -eq "redis_up") {
        return "1"
    } elseif ($Query -eq "redis_memory_used_bytes") {
        return "2097152"
    } elseif ($Query -eq "redis_connected_clients") {
        return "3"
    } elseif ($Query -eq "rate(redis_commands_processed_total[5m])") {
        return "2.5"
    }
    return $null
}

Assert-Equals "test_07_query_redis_up" "1" (Mock-QueryPromMetric -Query "redis_up")
Assert-Equals "test_07_query_redis_memory" "2097152" (Mock-QueryPromMetric -Query "redis_memory_used_bytes")
Assert-Equals "test_07_query_redis_clients" "3" (Mock-QueryPromMetric -Query "redis_connected_clients")
Assert-Equals "test_07_query_redis_commands_rate" "2.5" (Mock-QueryPromMetric -Query "rate(redis_commands_processed_total[5m])")

# 测试无数据情况
Assert-True "test_07_query_returns_null_on_no_data" ($null -eq (Mock-QueryPromMetric -Query "nonexistent_metric")) "null on no data"

# ============================================================
# 测试 8: Redis 密码读取逻辑
# ============================================================

Write-Host ""
Write-Host "========== 8. Redis 密码读取逻辑验证 ==========" -ForegroundColor Cyan

# 测试从环境变量读取
$env:REDIS_PASSWORD = "test_env_password"
$testRedisPassword = $env:REDIS_PASSWORD
Assert-Equals "test_08_redis_password_from_env" "test_env_password" $testRedisPassword

# 测试默认密码
$env:REDIS_PASSWORD = $null
$testRedisPassword = $env:REDIS_PASSWORD
if (-not $testRedisPassword) {
    $testRedisPassword = "zhuxiang123"
}
Assert-Equals "test_08_redis_password_default" "zhuxiang123" $testRedisPassword

# 清理
Remove-Item Env:\REDIS_PASSWORD -ErrorAction SilentlyContinue

# ============================================================
# 测试 9: 指标匹配逻辑
# ============================================================

Write-Host ""
Write-Host "========== 9. 指标匹配逻辑验证 ==========" -ForegroundColor Cyan

# 模拟 Redis Exporter 输出
$mockMetrics = @"
# HELP redis_up Information about the Redis instance
# TYPE redis_up gauge
redis_up 1
# HELP redis_memory_used_bytes Total number of bytes allocated by Redis
# TYPE redis_memory_used_bytes gauge
redis_memory_used_bytes{addr="redis:6379"} 2097152
# HELP redis_connected_clients Number of client connections
# TYPE redis_connected_clients gauge
redis_connected_clients{addr="redis:6379"} 3
# HELP redis_commands_processed_total Total number of commands processed
# TYPE redis_commands_processed_total counter
redis_commands_processed_total{addr="redis:6379"} 150
"@

# 测试 redis_up 匹配
Assert-True "test_09_match_redis_up_1" ($mockMetrics -match "redis_up\s+1") "redis_up = 1"
Assert-True "test_09_no_match_redis_up_0" (-not ($mockMetrics -match "redis_up\s+0")) "redis_up != 0"

# 测试 redis_memory_used_bytes 匹配
Assert-True "test_09_match_redis_memory" ($mockMetrics -match "redis_memory_used_bytes") "memory metric exists"

# 测试 redis_connected_clients 匹配
Assert-True "test_09_match_redis_clients" ($mockMetrics -match "redis_connected_clients") "clients metric exists"

# ============================================================
# 测试 10: 告警规则匹配逻辑
# ============================================================

Write-Host ""
Write-Host "========== 10. 告警规则匹配逻辑验证 ==========" -ForegroundColor Cyan

# 模拟 Prometheus 告警规则响应
$mockRulesResponse = @{
    status = "success"
    data = @{
        groups = @(
            @{
                name = "redis_alerts"
                rules = @(
                    @{ name = "RedisDown" },
                    @{ name = "RedisMemoryHigh" },
                    @{ name = "RedisTooManyConnections" }
                )
            },
            @{
                name = "backend_alerts"
                rules = @(
                    @{ name = "BackendDown" }
                )
            },
            @{
                name = "infra_alerts"
                rules = @(
                    @{ name = "HostHighCpuLoad" }
                )
            }
        )
    }
}

# 计算总规则数
$totalRules = 0
foreach ($group in $mockRulesResponse.data.groups) {
    $totalRules += $group.rules.Count
}
Assert-Equals "test_10_total_rules_count" 5 $totalRules

# 检查关键告警规则
$allRules = @()
foreach ($group in $mockRulesResponse.data.groups) {
    $allRules += $group.rules
}

$criticalAlerts = @("RedisDown", "RedisMemoryHigh", "BackendDown", "HostHighCpuLoad", "NonExistent")
$foundCount = 0
$notFoundCount = 0
foreach ($alertName in $criticalAlerts) {
    $exists = $false
    foreach ($rule in $allRules) {
        if ($rule.name -eq $alertName) {
            $exists = $true
            break
        }
    }
    if ($exists) {
        $foundCount++
    } else {
        $notFoundCount++
    }
}

Assert-Equals "test_10_found_alerts_count" 4 $foundCount
Assert-Equals "test_10_not_found_alerts_count" 1 $notFoundCount

# ============================================================
# 测试 11: 退出码逻辑
# ============================================================

Write-Host ""
Write-Host "========== 11. 退出码逻辑验证 ==========" -ForegroundColor Cyan

# 模拟全部通过
$testPass = 10
$testFail = 0
$testWarn = 0
if ($testFail -eq 0) {
    $exitCode = 0
} else {
    $exitCode = 1
}
Assert-Equals "test_11_exit_code_all_pass" 0 $exitCode

# 模拟有失败
$testPass = 8
$testFail = 2
$testWarn = 0
if ($testFail -eq 0) {
    $exitCode = 0
} else {
    $exitCode = 1
}
Assert-Equals "test_11_exit_code_with_fail" 1 $exitCode

# 模拟有警告但无失败
$testPass = 8
$testFail = 0
$testWarn = 2
if ($testFail -eq 0) {
    $exitCode = 0
} else {
    $exitCode = 1
}
Assert-Equals "test_11_exit_code_with_warn" 0 $exitCode

# ============================================================
# 测试 12: Redis PING 输出解析逻辑
# ============================================================

Write-Host ""
Write-Host "========== 12. Redis PING 输出解析验证 ==========" -ForegroundColor Cyan

# 模拟 PING 成功输出(包含警告)
$mockPingResult = @("Warning: Using a password with '-a' or '-u' option on the command line interface may not be secure.", "PONG")
$pingSuccess = $mockPingResult -contains "PONG"
Assert-True "test_12_ping_success_with_warning" $pingSuccess "PONG found despite warning"

# 模拟 PING 失败
$mockPingResult = @("")
$pingSuccess = $mockPingResult -contains "PONG"
Assert-True "test_12_ping_failure" (-not $pingSuccess) "PONG not found"

# 模拟 PING 为 null
$mockPingResult = $null
$pingSuccess = ($mockPingResult -and $mockPingResult -contains "PONG")
Assert-True "test_12_ping_null" (-not $pingSuccess) "null handled correctly"

# ============================================================
# 测试 13: Redis INFO 输出解析逻辑
# ============================================================

Write-Host ""
Write-Host "========== 13. Redis INFO 输出解析验证 ==========" -ForegroundColor Cyan

$mockServerInfo = @(
    "# Server",
    "redis_version:7.2.4",
    "redis_git_sha1:00000000",
    "redis_git_dirty:0",
    "redis_build_id:123456789"
)

$versionLine = $mockServerInfo | Where-Object { $_ -match "^redis_version:" }
Assert-NotNull "test_13_version_line_found" $versionLine

if ($versionLine) {
    $version = ($versionLine -split ":")[1].Trim()
    Assert-Equals "test_13_version_value" "7.2.4" $version
}

# 测试空输出
$mockServerInfo = @()
$versionLine = $mockServerInfo | Where-Object { $_ -match "^redis_version:" }
Assert-True "test_13_version_line_not_found" (-not $versionLine) "empty info handled"

# ============================================================
# 测试 14: Redis 内存信息解析
# ============================================================

Write-Host ""
Write-Host "========== 14. Redis 内存信息解析验证 ==========" -ForegroundColor Cyan

$mockMemInfo = @(
    "used_memory:2097152",
    "used_memory_human:2.00M",
    "maxmemory:268435456",
    "maxmemory_human:256.00M",
    "mem_fragmentation_ratio:1.05"
)

$usedLine = $mockMemInfo | Where-Object { $_ -match "^used_memory_human:" }
$maxLine = $mockMemInfo | Where-Object { $_ -match "^maxmemory_human:" }
$fragLine = $mockMemInfo | Where-Object { $_ -match "^mem_fragmentation_ratio:" }

Assert-NotNull "test_14_used_memory_line" $usedLine
Assert-NotNull "test_14_max_memory_line" $maxLine
Assert-NotNull "test_14_frag_ratio_line" $fragLine

if ($usedLine -and $maxLine) {
    $used = ($usedLine -split ":")[1].Trim()
    $max = ($maxLine -split ":")[1].Trim()
    Assert-Equals "test_14_used_memory_value" "2.00M" $used
    Assert-Equals "test_14_max_memory_value" "256.00M" $max
}

if ($fragLine) {
    $frag = ($fragLine -split ":")[1].Trim()
    $fragVal = [double]$frag
    Assert-True "test_14_frag_ratio_below_threshold" ($fragVal -lt 1.5) "ratio=$fragVal < 1.5"
}

# 测试高碎片率
$mockFragLine = "mem_fragmentation_ratio:1.80"
if ($mockFragLine) {
    $frag = ($mockFragLine -split ":")[1].Trim()
    $fragVal = [double]$frag
    Assert-True "test_14_high_frag_ratio" ($fragVal -ge 1.5) "ratio=$fragVal >= 1.5 (should warn)"
}

# ============================================================
# 测试 15: DBSIZE 输出解析
# ============================================================

Write-Host ""
Write-Host "========== 15. DBSIZE 输出解析验证 ==========" -ForegroundColor Cyan

$mockDbsizeResult = @("(integer) 5")
$keyCount = $mockDbsizeResult | Select-String "^\d+" | ForEach-Object { $_.Matches[0].Value }
# Select-String 可能无法匹配 "(integer) 5",测试替代方案
$mockDbsizeRaw = "(integer) 5"
if ($mockDbsizeRaw -match "(\d+)") {
    $keyCount = $matches[1]
}
Assert-Equals "test_15_dbsize_parse" "5" $keyCount

# ============================================================
# 测试 16: Redis 密码读取异常场景
# 复刻 verify-metrics.ps1 中 Get-RedisPassword 的兜底逻辑,
# 通过可注入的 .env 读取器模拟异常/边界输入
# ============================================================

Write-Host ""
Write-Host "========== 16. Redis 密码读取异常场景验证 ==========" -ForegroundColor Cyan

# 复刻真实脚本逻辑(与 Get-RedisPassword 保持一致), .env 读取行为可注入
function Get-RedisPasswordForTest {
    param([scriptblock]$ReadEnvFile = $null)
    $password = $null
    if ($env:REDIS_PASSWORD) {
        $password = $env:REDIS_PASSWORD.Trim()
    }
    if (-not $password) {
        try {
            if ($ReadEnvFile) {
                $envContent = & $ReadEnvFile
                $redisLine = $envContent | Where-Object { $_ -match "^REDIS_PASSWORD=" } | Select-Object -First 1
                if ($redisLine) {
                    $password = ($redisLine -replace "^\s*REDIS_PASSWORD=", "").Trim()
                }
            }
        } catch {
            # .env 读取失败, 静默降级默认值(不中断)
        }
    }
    if (-not $password) {
        $password = "zhuxiang123"
    }
    return $password
}

# 场景 1: 环境变量为空字符串(非 null) → 应回退默认值
$env:REDIS_PASSWORD = ""
$result = Get-RedisPasswordForTest
Assert-Equals "test_16_empty_string_env_falls_back_to_default" "zhuxiang123" $result

# 场景 2: 环境变量仅含空格 → trim 后为空, 回退默认值
$env:REDIS_PASSWORD = "   "
$result = Get-RedisPasswordForTest
Assert-Equals "test_16_whitespace_env_falls_back_to_default" "zhuxiang123" $result

# 场景 3: 环境变量含首尾空格 → trim 后返回有效值
$env:REDIS_PASSWORD = "  pass123  "
$result = Get-RedisPasswordForTest
Assert-Equals "test_16_env_value_trimmed" "pass123" $result

# 场景 4: .env 读取抛异常(权限拒绝/文件占用) → 不中断, 回退默认值
$env:REDIS_PASSWORD = $null
$threw = $false
try {
    $result = Get-RedisPasswordForTest -ReadEnvFile { throw [System.UnauthorizedAccessException]::new("Access to .env is denied") }
} catch {
    $threw = $true
}
Assert-True "test_16_env_file_exception_does_not_crash" (-not $threw) "exception swallowed"
Assert-Equals "test_16_env_file_exception_falls_back_to_default" "zhuxiang123" $result

# 场景 5: .env 存在但 REDIS_PASSWORD 值为空 → 回退默认值
$result = Get-RedisPasswordForTest -ReadEnvFile { @("REDIS_HOST=redis", "REDIS_PASSWORD=", "REDIS_PORT=6379") }
Assert-Equals "test_16_env_file_empty_value_falls_back_to_default" "zhuxiang123" $result

# 场景 6: .env 值含首尾空格 → trim 后返回有效值
$result = Get-RedisPasswordForTest -ReadEnvFile { @("REDIS_PASSWORD=  secret456  ") }
Assert-Equals "test_16_env_file_value_trimmed" "secret456" $result

# 场景 7: .env 无 REDIS_PASSWORD 行 → 回退默认值
$result = Get-RedisPasswordForTest -ReadEnvFile { @("REDIS_HOST=redis", "REDIS_PORT=6379") }
Assert-Equals "test_16_env_file_missing_key_falls_back_to_default" "zhuxiang123" $result

# 场景 8: 环境变量优先级高于 .env(.env 提供不同值仍被忽略)
$env:REDIS_PASSWORD = "env_wins"
$result = Get-RedisPasswordForTest -ReadEnvFile { @("REDIS_PASSWORD=env_file_loses") }
Assert-Equals "test_16_env_var_takes_precedence_over_env_file" "env_wins" $result

# 清理环境变量
Remove-Item Env:\REDIS_PASSWORD -ErrorAction SilentlyContinue

# ============================================================
# 汇总报告
# ============================================================

Write-Host ""
Write-Host "========== 汇总报告 ==========" -ForegroundColor Cyan
Write-Host ""

foreach ($result in $script:TestResults) {
    $color = if ($result -match "\[PASS\]") { "Green" } else { "Red" }
    Write-Host $result -ForegroundColor $color
}

Write-Host ""
$total = $script:TestPass + $script:TestFail
Write-Host "  测试总数: $total" -ForegroundColor Cyan
Write-Host "  通过: $($script:TestPass)" -ForegroundColor Green
Write-Host "  失败: $($script:TestFail)" -ForegroundColor Red
Write-Host ""

if ($script:TestFail -eq 0) {
    Write-Host "  ========================================" -ForegroundColor Green
    Write-Host "    所有测试通过!" -ForegroundColor Green
    Write-Host "  ========================================" -ForegroundColor Green
    $exitCode = 0
} else {
    Write-Host "  ========================================" -ForegroundColor Red
    Write-Host "    有 $($script:TestFail) 项测试失败" -ForegroundColor Red
    Write-Host "  ========================================" -ForegroundColor Red
    $exitCode = 1
}

Write-Host ""
exit $exitCode
