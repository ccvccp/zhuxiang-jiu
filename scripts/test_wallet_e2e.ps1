# ============================================================
# 钱包盈利模块 端到端测试(PowerShell + curl)
# 覆盖 22 个接口, 验证完整业务流
# 运行: .\scripts\test_wallet_e2e.ps1
# 前置: Docker 容器已启动并健康
# ============================================================

param(
    [string]$BaseUrl = "http://localhost:8000"
)

$ErrorActionPreference = "Continue"
$PassCount = 0
$FailCount = 0
$Results = @()

function Write-TestHeader($name) {
    Write-Host "`n========== $name ==========" -ForegroundColor Cyan
}

function Test-Endpoint {
    param(
        [string]$Name,
        [string]$Method = "GET",
        [string]$Path,
        [string]$Body = "",
        [hashtable]$Headers = @{},
        [int]$ExpectStatus = 200,
        [scriptblock]$Validate = $null
    )
    Write-Host "`n[Test] $Name" -ForegroundColor Yellow

    $uri = "$BaseUrl$Path"
    $params = @{
        Method  = $Method
        Uri     = $uri
        Headers = $Headers
        TimeoutSec = 15
    }
    if ($Body -and $Method -ne "GET") {
        $params.Body = $Body
        $params.ContentType = "application/json; charset=utf-8"
    }

    try {
        $resp = Invoke-RestMethod @params -ResponseHeadersVariable respHeaders -StatusCodeVariable respStatus
        $status = $respStatus
        $data = $resp
    } catch {
        $status = $_.Exception.Response.StatusCode.value__
        try {
            $data = $_.ErrorDetails.Message | ConvertFrom-Json
        } catch {
            $data = @{ error = $_.Exception.Message }
        }
    }

    $statusOk = ($status -eq $ExpectStatus)
    $validateOk = $true
    $validateMsg = ""
    if ($statusOk -and $Validate) {
        $result = & $Validate $data
        $validateOk = $result.Ok
        $validateMsg = $result.Msg
    }

    $pass = $statusOk -and $validateOk
    if ($pass) {
        $PassCount++
        Write-Host "  [PASS] HTTP $status" -ForegroundColor Green
        if ($data.success) { Write-Host "  success=$($data.success)" }
    } else {
        $FailCount++
        Write-Host "  [FAIL] HTTP $status (期望 $ExpectStatus)" -ForegroundColor Red
        if (-not $statusOk) {
            Write-Host "  状态码不匹配: 实际 $status, 期望 $ExpectStatus"
        }
        if ($validateMsg) {
            Write-Host "  校验失败: $validateMsg"
        }
        Write-Host "  响应: $($data | ConvertTo-Json -Depth 5 -Compress)"
    }

    $script:Results += [PSCustomObject]@{
        Name = $Name
        Method = $Method
        Path = $Path
        Status = $status
        ExpectStatus = $ExpectStatus
        Pass = $pass
    }
    return $data
}

# ============================================================
# 0. 前置: 健康检查 + 会员成长值提升
# ============================================================

Write-TestHeader "前置: 健康检查"
Test-Endpoint -Name "健康检查" -Method GET -Path "/api/decision/health" `
    -Validate { @{ Ok = $args[0].status -eq "ok"; Msg = "" } }

Write-TestHeader "前置: 提升会员成长值(消费 500 元 → 成长值 ≥ 500)"
$memberId = "1"
$memberHeaders = @{ "X-Member-Id" = $memberId }

# 查询当前等级
$levelResp = Test-Endpoint -Name "查询会员等级(前置)" -Method GET -Path "/api/member/level" -Headers $memberHeaders
Write-Host "  当前成长值: $($levelResp.growthValue), 等级: L$($levelResp.level)"

# 如果成长值 < 500, 消费补足差额
if ($levelResp.growthValue -lt 500) {
    $need = 500 - $levelResp.growthValue
    Write-Host "  需消费 $need 元补足成长值"
    Test-Endpoint -Name "消费补足成长值" -Method POST -Path "/api/member/consume" `
        -Body (@{amount = $need} | ConvertTo-Json) -Headers $memberHeaders | Out-Null
}

# ============================================================
# 1. 钱包账户(2 端点)
# ============================================================

Write-TestHeader "1. 钱包账户"

Test-Endpoint -Name "1.1 开通钱包" -Method POST -Path "/api/wallet/open" -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.status -eq "active"); Msg = "success/status 校验" }
    }

# 重复开通应 409
Test-Endpoint -Name "1.2 重复开通(应 409)" -Method POST -Path "/api/wallet/open" -Headers $memberHeaders -ExpectStatus 409 | Out-Null

Test-Endpoint -Name "1.3 钱包首页" -Method GET -Path "/api/wallet/info" -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.statusName -eq "正常"); Msg = "" }
    }

# ============================================================
# 2. 充值提现(4 端点)
# ============================================================

Write-TestHeader "2. 充值提现"

Test-Endpoint -Name "2.1 充值 ¥5000(支付宝)" -Method POST -Path "/api/wallet/deposit" `
    -Body (@{amount = 5000; payChannel = "alipay"} | ConvertTo-Json) -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.balanceAfter -eq 5000); Msg = "balanceAfter 应为 5000" }
    }

# 充值低于 100 应 409
Test-Endpoint -Name "2.2 充值低于 ¥100(应 409)" -Method POST -Path "/api/wallet/deposit" `
    -Body (@{amount = 50; payChannel = "alipay"} | ConvertTo-Json) -Headers $memberHeaders -ExpectStatus 409 | Out-Null

Test-Endpoint -Name "2.3 提现 ¥1000(自动通过)" -Method POST -Path "/api/wallet/withdraw" `
    -Body (@{amount = 1000; payChannel = "bank"; bankAccount = "6222000112345678"} | ConvertTo-Json) -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.autoApproved -eq $true); Msg = "应自动通过" }
    }

$withdrawNo = $null  # 保存后续使用

Test-Endpoint -Name "2.4 提现 ¥8000(待审核)" -Method POST -Path "/api/wallet/withdraw" `
    -Body (@{amount = 8000; payChannel = "bank"; bankAccount = "6222000112345678"} | ConvertTo-Json) -Headers $memberHeaders `
    -Validate {
        $script:withdrawNo = $args[0].withdrawNo
        @{ Ok = ($args[0].success -eq $true -and $args[0].status -eq "pending"); Msg = "应待审核" }
    }

# ============================================================
# 3. 提现审批(2 端点 · admin)
# ============================================================

Write-TestHeader "3. 提现审批"

$adminHeaders = @{ "X-Role" = "admin" }

Test-Endpoint -Name "3.1 待审核提现列表" -Method GET -Path "/api/wallet/withdrawals/pending" -Headers $adminHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.count -ge 1); Msg = "应至少 1 条待审核" }
    }

# 提现单详情(用户查询本人)
Test-Endpoint -Name "3.2 提现单详情" -Method GET -Path "/api/wallet/withdrawal/$withdrawNo" -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.withdrawal.withdrawNo -eq $script:withdrawNo); Msg = "" }
    }

Test-Endpoint -Name "3.3 审核拒绝提现" -Method POST -Path "/api/wallet/withdrawal/$withdrawNo/approve" `
    -Body (@{decision = "rejected"; auditor = "admin"; auditRemark = "测试拒绝"} | ConvertTo-Json) -Headers $adminHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.status -eq "rejected"); Msg = "" }
    }

# 再申请一个提现用于审核通过测试
$wd2 = Test-Endpoint -Name "3.4 再次提现 ¥6000(待审核)" -Method POST -Path "/api/wallet/withdraw" `
    -Body (@{amount = 6000; payChannel = "bank"; bankAccount = "6222000112345678"} | ConvertTo-Json) -Headers $memberHeaders
$withdrawNo2 = $wd2.withdrawNo

Test-Endpoint -Name "3.5 审核通过提现" -Method POST -Path "/api/wallet/withdrawal/$withdrawNo2/approve" `
    -Body (@{decision = "approved"; auditor = "admin"; auditRemark = "测试通过"} | ConvertTo-Json) -Headers $adminHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.status -eq "approved"); Msg = "" }
    }

Test-Endpoint -Name "3.6 标记打款完成" -Method POST -Path "/api/wallet/withdrawal/$withdrawNo2/paid" -Headers $adminHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.status -eq "paid"); Msg = "" }
    }

# ============================================================
# 4. 消费退款(3 端点)
# ============================================================

Write-TestHeader "4. 消费退款"

Test-Endpoint -Name "4.1 消费支付 ¥1000(返利 1%)" -Method POST -Path "/api/wallet/pay" `
    -Body (@{amount = 1000; orderId = "ORD-E2E-001"} | ConvertTo-Json) -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.rebate -eq 10.0); Msg = "返利应为 10" }
    }

Test-Endpoint -Name "4.2 退款 ¥500" -Method POST -Path "/api/wallet/refund" `
    -Body (@{amount = 500; orderId = "ORD-E2E-001"} | ConvertTo-Json) -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.amount -eq 500); Msg = "" }
    }

Test-Endpoint -Name "4.3 交易明细" -Method GET -Path "/api/wallet/transactions" -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.count -ge 4); Msg = "应至少 4 条交易" }
    }

# ============================================================
# 5. 收益计算(3 端点)
# ============================================================

Write-TestHeader "5. 收益计算"

Test-Endpoint -Name "5.1 日补贴预估" -Method GET -Path "/api/wallet/interest/daily" -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.dailyInterest -gt 0); Msg = "日补贴应 > 0" }
    }

Test-Endpoint -Name "5.2 收益规则" -Method GET -Path "/api/wallet/interest/rules" `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.current.annualRate -eq 0.03); Msg = "活期年化应为 3%" }
    }

# 月结付 - 先手动写入 pending_interest(通过 Redis 或直接调用 service)
# 此接口期望 pending_interest > 0, 若为 0 应返回 409
$settleResult = Test-Endpoint -Name "5.3 月度结付(无待入账应 409)" -Method POST -Path "/api/wallet/interest/settle-monthly" -Headers $memberHeaders -ExpectStatus 409
# 如果状态码是 200 也可以(说明有待入账)
if ($settleResult -and $script:Results[-1].Status -eq 200) {
    Write-Host "  [INFO] 月结实际返回 200, 说明有待入账补贴" -ForegroundColor Yellow
    $script:Results[-1].Pass = $true
    $script:FailCount--
    $script:PassCount++
}

# ============================================================
# 6. 定期管理(4 端点)
# ============================================================

Write-TestHeader "6. 定期管理"

# 先充值确保余额充足
Test-Endpoint -Name "6.0 充值 ¥10000(为转定期)" -Method POST -Path "/api/wallet/deposit" `
    -Body (@{amount = 10000; payChannel = "bank"} | ConvertTo-Json) -Headers $memberHeaders | Out-Null

$transferResult = Test-Endpoint -Name "6.1 转定期 ¥5000/12 月" -Method POST -Path "/api/wallet/transfer-regular" `
    -Body (@{amount = 5000; period = 12} | ConvertTo-Json) -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.expectedInterest -eq 150.0); Msg = "12 月补贴应为 150" }
    }
$depositNo = $transferResult.depositNo

Test-Endpoint -Name "6.2 定期列表" -Method GET -Path "/api/wallet/deposits" -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.count -ge 1); Msg = "应至少 1 条定期" }
    }

# 到期取出 - 需要修改到期日为过去(通过容器内 python 脚本)
# 这里先测试未到期应 409
Test-Endpoint -Name "6.3 定期未到期取出(应 409)" -Method POST -Path "/api/wallet/deposit/$depositNo/settle" -Headers $memberHeaders -ExpectStatus 409 | Out-Null

# 提前取出(应成功)
Test-Endpoint -Name "6.4 定期提前取出" -Method POST -Path "/api/wallet/deposit/$depositNo/early-settle" -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.fee -eq 50.0); Msg = "手续费应为 50(5000×1%)" }
    }

# ============================================================
# 7. 奖品管理(2 端点)
# ============================================================

Write-TestHeader "7. 奖品管理"

# 先创建一个到期定期 → 取出 → 产生奖品
Test-Endpoint -Name "7.0 充值 ¥5000(为到期测试)" -Method POST -Path "/api/wallet/deposit" `
    -Body (@{amount = 5000; payChannel = "bank"} | ConvertTo-Json) -Headers $memberHeaders | Out-Null

$transferResult2 = Test-Endpoint -Name "7.1 转定期 ¥5000/12 月(用于到期)" -Method POST -Path "/api/wallet/transfer-regular" `
    -Body (@{amount = 5000; period = 12} | ConvertTo-Json) -Headers $memberHeaders
$depositNo2 = $transferResult2.depositNo

# 通过 docker exec 修改到期日为昨天
Write-Host "`n[INFO] 修改定期到期日为昨天(模拟到期)..." -ForegroundColor Yellow
docker exec website-arch-backend-1 python -c @"
import asyncio, redis.asyncio as redis, os
async def main():
    c = redis.from_url(os.environ.get('REDIS_URL','redis://redis:6379/0'), decode_responses=True)
    import json
    key = 'zhuxiang:wallet:deposit:$depositNo2'
    data = json.loads(await c.get(key))
    from datetime import datetime, timedelta
    data['endDate'] = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    await c.set(key, json.dumps(data, ensure_ascii=False))
    print('endDate updated to', data['endDate'])
    await c.aclose()
asyncio.run(main())
"@ 2>&1 | ForEach-Object { Write-Host "  $_" }

$settleResult2 = Test-Endpoint -Name "7.2 定期到期取出(产生奖品)" -Method POST -Path "/api/wallet/deposit/$depositNo2/settle" -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.rewardNo -ne ""); Msg = "应产生奖品" }
    }
$rewardNo = $settleResult2.rewardNo

Test-Endpoint -Name "7.3 奖品列表" -Method GET -Path "/api/wallet/rewards" -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.count -ge 1); Msg = "应至少 1 个奖品" }
    }

Test-Endpoint -Name "7.4 领取奖品" -Method POST -Path "/api/wallet/reward/$rewardNo/claim" `
    -Body (@{addressId = 1} | ConvertTo-Json) -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.status -eq "claimed"); Msg = "" }
    }

# ============================================================
# 8. 奖品履约(2 端点)
# ============================================================

Write-TestHeader "8. 奖品履约"

Test-Endpoint -Name "8.1 奖品发货(admin)" -Method POST -Path "/api/wallet/reward/$rewardNo/ship" `
    -Body (@{waybillNo = "SF1234567890"} | ConvertTo-Json) -Headers $adminHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.status -eq "shipped"); Msg = "" }
    }

Test-Endpoint -Name "8.2 奖品签收" -Method POST -Path "/api/wallet/reward/$rewardNo/sign" -Headers $memberHeaders `
    -Validate {
        $d = $args[0]
        @{ Ok = ($d.success -eq $true -and $d.status -eq "signed"); Msg = "" }
    }

# ============================================================
# 汇总报告
# ============================================================

Write-Host "`n`n" -ForegroundColor White
Write-Host ("=" * 60) -ForegroundColor White
Write-Host "  钱包盈利模块端到端测试报告" -ForegroundColor White
Write-Host ("=" * 60) -ForegroundColor White
Write-Host "  通过: $PassCount  失败: $FailCount  总计: $($PassCount + $FailCount)" -ForegroundColor $(if ($FailCount -eq 0) { "Green" } else { "Red" })
Write-Host ("=" * 60) -ForegroundColor White

# 输出详细结果
Write-Host "`n详细结果:"
$Results | ForEach-Object {
    $icon = if ($_.Pass) { "[PASS]" } else { "[FAIL]" }
    $color = if ($_.Pass) { "Green" } else { "Red" }
    Write-Host ("  {0} {1,-40} {2} {3} → {4}" -f $icon, $_.Name, $_.Method, $_.Path, $_.Status) -ForegroundColor $color
}

if ($FailCount -gt 0) {
    Write-Host "`n失败用例详情:" -ForegroundColor Red
    $Results | Where-Object { -not $_.Pass } | ForEach-Object {
        Write-Host "  - $($_.Name): HTTP $($_.Status) (期望 $($_.ExpectStatus))"
    }
    exit 1
} else {
    Write-Host "`n全部测试通过!" -ForegroundColor Green
    exit 0
}
