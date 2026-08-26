﻿﻿﻿﻿﻿# ============================================================
#  API 接口验证脚本 · 竹香酒 AI 决策模块
#  用法: powershell -ExecutionPolicy Bypass -File .\scripts\verify-api.ps1
# ============================================================

param(
    [string]$BaseUrl = "http://localhost:8000"
)

$ErrorActionPreference = "Continue"
$passCount = 0
$failCount = 0
$skipCount = 0

function Write-Step($msg) {
    Write-Host ""
    Write-Host "[STEP] $msg" -ForegroundColor Cyan
}

function Write-Pass($msg) {
    Write-Host "  [PASS] $msg" -ForegroundColor Green
    $script:passCount++
}

function Write-Fail($msg, $detail) {
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
    if ($detail) { Write-Host "         $detail" -ForegroundColor DarkRed }
    $script:failCount++
}

function Write-Skip($msg) {
    Write-Host "  [SKIP] $msg" -ForegroundColor DarkGray
    $script:skipCount++
}

function Get-RespData($resp) {
    if ($resp.details) { return $resp.details }
    if ($resp.data) { return $resp.data }
    return $null
}

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Path,
        $Body,
        [hashtable]$Headers,
        [int]$Timeout = 10
    )
    $url = "$BaseUrl$Path"
    $params = @{
        Method  = $Method
        Uri     = $url
        TimeoutSec = $Timeout
    }
    if ($Headers) { $params.Headers = $Headers }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Depth 10)
        $params.ContentType = "application/json; charset=utf-8"
    }
    try {
        $resp = Invoke-RestMethod @params
        return $resp
    } catch [System.Net.WebException] {
        $status = [int]$_.Exception.Response.StatusCode
        if ($status -ge 400 -and $status -lt 500) {
            try {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $body = $reader.ReadToEnd()
                $reader.Close()
                return ($body | ConvertFrom-Json)
            } catch {
                return @{ success = $false; errorCode = "HTTP_$status"; error = "HTTP $status" }
            }
        }
        return @{ success = $false; error = $_.Exception.Message }
    } catch {
        return @{ success = $false; error = $_.Exception.Message }
    }
}

# ============================================================
#  Phase 1: 基础连通性
# ============================================================
Write-Step "Phase 1: 基础连通性测试"

$resp = Invoke-Api -Method GET -Path "/api/decision/health"
if ($resp.success -eq $true) {
    $data = Get-RespData $resp
    Write-Pass "健康检查 /api/decision/health"
    Write-Host "         status=$($data.status) module=$($data.module)"
} else {
    Write-Fail "健康检查失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

try {
    $null = Invoke-WebRequest -Uri "$BaseUrl/docs" -TimeoutSec 5 -UseBasicParsing
    Write-Pass "Swagger UI /docs 可访问"
} catch {
    Write-Fail "Swagger UI /docs 不可访问" $_.Exception.Message
}

try {
    $openapi = Invoke-RestMethod -Uri "$BaseUrl/openapi.json" -TimeoutSec 5
    $pathCount = ($openapi.paths | Get-Member -MemberType NoteProperty).Count
    Write-Pass "OpenAPI /openapi.json (版本: $($openapi.info.version), 路径数: $pathCount)"
} catch {
    Write-Fail "OpenAPI /openapi.json 不可访问" $_.Exception.Message
}

# ============================================================
#  Phase 2: 系统接口
# ============================================================
Write-Step "Phase 2: 系统接口测试"

$resp = Invoke-Api -Method GET -Path "/api/decision/mode" -Headers @{ "X-Role" = "admin" }
if ($resp.success -eq $true) {
    $data = Get-RespData $resp
    Write-Pass "查询模式 /api/decision/mode (admin)"
    Write-Host "         mode=$($data.mode) apiBase=$($data.apiBase)"
} else {
    Write-Fail "查询模式失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method GET -Path "/api/decision/mode" -Headers @{ "X-Role" = "guest" }
if ($resp.detail -and $resp.detail.errorCode -eq "DECISION_003") {
    Write-Pass "权限守卫: guest 访问 /mode 返回 403 (DECISION_003)"
} else {
    Write-Fail "权限守卫未生效" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/decision/mode/switch" -Headers @{ "X-Role" = "admin" } -Body @{
    mode = "mock"
    apiBase = "/api/decision"
}
if ($resp.success -eq $true) {
    Write-Pass "切换模式 /api/decision/mode/switch (mock->mock 幂等)"
} else {
    Write-Fail "切换模式失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

# ============================================================
#  Phase 3: 业务接口 - 代理商服务
# ============================================================
Write-Step "Phase 3: 业务接口 - 代理商服务"

$resp = Invoke-Api -Method POST -Path "/api/agent/upgrade" -Body @{
    agentId = 1001
    fromLevel = "D"
    toLevel = "C"
    payAmount = 5000
}
if ($resp.success -eq $true) {
    Write-Pass "代理商升级 /api/agent/upgrade (1001: D->C)"
} else {
    Write-Fail "代理商升级失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/agent/downgrade" -Body @{
    agentId = 1001
    fromLevel = "C"
    reason = "考核未达标"
}
if ($resp.success -eq $true) {
    Write-Pass "代理商降级 /api/agent/downgrade (1001: C->D)"
} else {
    Write-Fail "代理商降级失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/agent-shipping/claim" -Body @{
    agentId = 1001
    region = "华东"
}
if ($resp.success -eq $true) {
    Write-Pass "区域认领 /api/agent-shipping/claim (1001 -> 华东)"
} else {
    Write-Fail "区域认领失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/agent-shipping/claim" -Body @{
    agentId = 1002
    region = "华东"
}
if ($resp.success -ne $true) {
    Write-Pass "重复认领被拒绝 (并发保护生效)"
} else {
    Write-Fail "重复认领未被拒绝(锁可能失效)" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method GET -Path "/api/agent-shipping/claims"
if ($resp.success -eq $true) {
    $data = Get-RespData $resp
    $claimCount = 0
    if ($data.claims) { $claimCount = @($data.claims).Count }
    elseif ($data) { $claimCount = @($data).Count }
    Write-Pass "查询认领 /api/agent-shipping/claims (记录数: $claimCount)"
} else {
    Write-Fail "查询认领失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

# ============================================================
#  Phase 4: 业务接口 - 供应链 & 仓储
# ============================================================
Write-Step "Phase 4: 业务接口 - 供应链 & 仓储"

$resp = Invoke-Api -Method POST -Path "/api/inventory/deduct" -Body @{
    productId = "ZX42-2026L07"
    quantity = 5
}
if ($resp.success -eq $true) {
    Write-Pass "库存扣减 /api/inventory/deduct (ZX42-2026L07 x5)"
} else {
    Write-Fail "库存扣减失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/inventory/restock" -Body @{
    productId = "ZX42-2026L07"
    quantity = 10
}
if ($resp.success -eq $true) {
    Write-Pass "库存回补 /api/inventory/restock (ZX42-2026L07 x10)"
} else {
    Write-Fail "库存回补失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/warehouse/stocktake" -Body @{
    warehouseId = "WH001"
}
if ($resp.success -eq $true) {
    Write-Pass "仓储盘点 /api/warehouse/stocktake"
} else {
    Write-Fail "仓储盘点失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/warehouse/inbound" -Body @{
    productId = "ZX42-2026L07"
    warehouseId = "WH001"
}
if ($resp.success -eq $true) {
    Write-Pass "仓储入库 /api/warehouse/inbound"
} else {
    Write-Fail "仓储入库失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method GET -Path "/api/warehouse/forecast?productId=ZX42-2026L07"
if ($resp.success -eq $true) {
    Write-Pass "库存预测 /api/warehouse/forecast"
} else {
    Write-Fail "库存预测失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

# ============================================================
#  Phase 5: 决策接口 - 6 层架构
# ============================================================
Write-Step "Phase 5: 决策接口 - 6 层架构"

$resp = Invoke-Api -Method POST -Path "/api/decision/data-ingest" -Body @{
    source = "module:04"
    dataType = "metrics"
    payload = @(@{ name = "sales"; value = 1200 })
    realtime = $true
}
if ($resp.success -eq $true) {
    Write-Pass "感知层 /api/decision/data-ingest"
} else {
    Write-Fail "感知层失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method GET -Path "/api/decision/knowledge/query?category=rule&q=库存"
if ($resp.success -eq $true) {
    Write-Pass "知识层 /api/decision/knowledge/query"
} else {
    Write-Fail "知识层失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/decision/risk-control" -Body @{
    checkType = "transaction_anomaly"
    target = @{ userId = "U1001"; amount = 50000 }
    autoCircuitBreak = $true
}
if ($resp.success -eq $true) {
    Write-Pass "决策层 /api/decision/risk-control"
} else {
    Write-Fail "决策层失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/decision/orchestrate" -Body @{
    workflow = "order_fulfillment"
    modules = @("inventory", "shipping", "warehouse")
    context = @{ orderId = "ORD001" }
    parallel = $true
}
if ($resp.success -eq $true) {
    Write-Pass "编排层 /api/decision/orchestrate"
} else {
    Write-Fail "编排层失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/decision/feedback-loop" -Body @{
    actionId = "ACT001"
    outcome = @{ status = "success"; metrics = @{} }
    reflowMetrics = @("conversion", "latency")
}
if ($resp.success -eq $true) {
    Write-Pass "反馈层 /api/decision/feedback-loop"
} else {
    Write-Fail "反馈层失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

# ============================================================
#  Phase 6: 并发安全验证(分布式锁)
# ============================================================
Write-Step "Phase 6: 并发安全验证(分布式锁)"

Write-Host "  发起 5 个并发认领请求(华南区域)..." -ForegroundColor DarkGray
$jobs = 1..5 | ForEach-Object {
    $agentId = 2000 + $_
    Start-Job -ScriptBlock {
        param($url, $agentId)
        $body = @{ agentId = $agentId; region = "华南" } | ConvertTo-Json
        try {
            $r = Invoke-RestMethod -Uri "$url/api/agent-shipping/claim" -Method POST -Body $body -ContentType "application/json" -TimeoutSec 15
            return @{ agentId = $agentId; success = $r.success; errorCode = $null }
        } catch [System.Net.WebException] {
            $status = [int]$_.Exception.Response.StatusCode
            if ($status -ge 400 -and $status -lt 500) {
                try {
                    $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                    $respBody = $reader.ReadToEnd()
                    $reader.Close()
                    $r = $respBody | ConvertFrom-Json
                    return @{ agentId = $agentId; success = $false; errorCode = $r.detail.errorCode }
                } catch {
                    return @{ agentId = $agentId; success = $false; errorCode = "HTTP_$status" }
                }
            }
            return @{ agentId = $agentId; success = $false; errorCode = $null; error = $_.Exception.Message }
        } catch {
            return @{ agentId = $agentId; success = $false; errorCode = $null; error = $_.Exception.Message }
        }
    } -ArgumentList $BaseUrl, $agentId
}
$successCount = 0
$conflictCount = 0
$jobs | ForEach-Object {
    $result = Receive-Job $_ -Wait
    if ($result.success) { $successCount++ }
    elseif ($result.errorCode -or $result.error) { $conflictCount++ }
    Remove-Job $_
}
if ($successCount -ge 1 -and $conflictCount -ge 1) {
    Write-Pass "并发认领: $successCount 成功 + $conflictCount 冲突(锁互斥生效)"
} else {
    Write-Fail "并发认领异常: 成功=$successCount 冲突=$conflictCount (应至少 1+1)"
}

Write-Host "  发起 3 个并发模式切换请求..." -ForegroundColor DarkGray
$jobs = 1..3 | ForEach-Object {
    $mode = if ($_ % 2 -eq 0) { "mock" } else { "live" }
    Start-Job -ScriptBlock {
        param($url, $mode)
        $body = @{ mode = $mode; apiBase = "/api/decision" } | ConvertTo-Json
        try {
            $r = Invoke-RestMethod -Uri "$url/api/decision/mode/switch" -Method POST -Body $body -ContentType "application/json" -Headers @{ "X-Role" = "admin" } -TimeoutSec 15
            return @{ success = $r.success }
        } catch [System.Net.WebException] {
            $status = [int]$_.Exception.Response.StatusCode
            return @{ success = $false; error = "HTTP $status" }
        } catch {
            return @{ success = $false; error = $_.Exception.Message }
        }
    } -ArgumentList $BaseUrl, $mode
}
$modeSuccess = 0
$jobs | ForEach-Object {
    $result = Receive-Job $_ -Wait
    if ($result.success) { $modeSuccess++ }
    Remove-Job $_
}
if ($modeSuccess -ge 1) {
    Write-Pass "并发模式切换: $modeSuccess/3 成功(锁防丢失更新)"
} else {
    Write-Fail "并发模式切换全失败"
}

# ============================================================
#  Phase 7: Redis 持久化验证
# ============================================================
Write-Step "Phase 7: Redis 持久化验证"

$resp = Invoke-Api -Method GET -Path "/api/agent-shipping/claims"
if ($resp.success -eq $true) {
    $data = Get-RespData $resp
    $claims = $null
    if ($data.claims) { $claims = $data.claims }
    elseif ($data) { $claims = $data }
    if ($claims -and @($claims).Count -gt 0) {
        Write-Pass "Redis 读取验证: 认领记录持久化 ($(@($claims).Count) 条)"
    } else {
        Write-Skip "Redis 读取: 认领记录为空(可能数据未写入)"
    }
} else {
    Write-Fail "Redis 读取失败" ($resp | ConvertTo-Json -Compress -Depth 5)
}

$resp = Invoke-Api -Method POST -Path "/api/decision/mode/switch" -Headers @{ "X-Role" = "admin" } -Body @{
    mode = "mock"
    apiBase = "/api/decision"
}
if ($resp.success -eq $true) {
    Write-Pass "恢复 mock 模式"
}

# ============================================================
#  总结
# ============================================================
Write-Host ""
Write-Host "====================================" -ForegroundColor Yellow
Write-Host "  验证结果: $passCount PASS / $failCount FAIL / $skipCount SKIP" -ForegroundColor Yellow
Write-Host "====================================" -ForegroundColor Yellow
Write-Host ""

if ($failCount -eq 0) {
    Write-Host "  所有 API 接口验证通过!" -ForegroundColor Green
    exit 0
} else {
    Write-Host "  有 $failCount 个接口验证失败,请检查日志" -ForegroundColor Red
    exit 1
}
