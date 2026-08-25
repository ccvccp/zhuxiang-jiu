# ============================================================
#  Frontend integration test - Simulate complete order flow
#
#  Flow:
#    1. Health check
#    2. Member login (get JWT token)
#    3. Browse product list + product detail
#    4. Submit checkout (create order)
#    5. Inventory deduct
#    6. Create payment order
#    7. Payment callback (simulate success)
#    8. Logistics create order
#    9. Logistics query (waybill detail)
#
#  Usage:
#    Ensure backend running at http://127.0.0.1:8000
#    cd d:\网站架构设计\zhuxiang-jiu\backend
#    .\scripts\sim-order-flow.ps1
# ============================================================

param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$ProductId = "ZX42-2026L07",
    [int]$Quantity = 2
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ============================================================
#  Helpers
# ============================================================

$script:StepNo = 0
$script:PassCount = 0
$script:FailCount = 0
$script:Context = @{}

function Write-Step {
    param([string]$Title)
    $script:StepNo++
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  [$script:StepNo] $Title" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Write-Ok  { param([string]$m) Write-Host "  [OK]   $m" -ForegroundColor Green; $script:PassCount++ }
function Write-Err { param([string]$m) Write-Host "  [ERR]  $m" -ForegroundColor Red;   $script:FailCount++ }
function Write-Info{ param([string]$m) Write-Host "  [INFO] $m" -ForegroundColor Yellow }

function Invoke-Api {
    param(
        [string]$Name,
        [string]$Method,
        [string]$Path,
        [object]$BodyObj = $null,
        [hashtable]$ExtraHeaders = @{},
        [int]$ExpectStatus = 0
    )
    $headers = @{ "Content-Type" = "application/json" }
    foreach ($k in $ExtraHeaders.Keys) { $headers[$k] = $ExtraHeaders[$k] }

    $uri = "$BaseUrl$Path"
    $bodyStr = $null
    if ($BodyObj) { $bodyStr = $BodyObj | ConvertTo-Json -Compress -Depth 10 }

    try {
        $params = @{
            Uri         = $uri
            Method      = $Method
            Headers     = $headers
            ErrorAction = "Stop"
        }
        if ($bodyStr) { $params["Body"] = $bodyStr }
        $resp = Invoke-RestMethod @params
        Write-Ok "$Name -> $($resp | ConvertTo-Json -Compress -Depth 5)"
        return @{ success = $true; data = $resp }
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        $msg  = $_.ErrorDetails.Message
        if ($ExpectStatus -ne 0 -and $code -eq $ExpectStatus) {
            Write-Ok "$Name -> (expected $ExpectStatus) $msg"
        } else {
            Write-Err "$Name -> [HTTP $code] $msg"
        }
        return @{ success = $false; data = $null; statusCode = $code }
    }
}

# ============================================================
#  Step 1: Health check
# ============================================================
Write-Step "Health Check"

$r = Invoke-Api "GET /api/health" "GET" "/api/health"
if (-not $r.success) {
    Write-Host "Backend not running. Start it: py -3 -m uvicorn main:app --port 8000" -ForegroundColor Red
    exit 1
}
if ($r.data.lockMode -ne "memory") {
    Write-Info "lockMode=$($r.data.lockMode) (recommend memory mode: LOCK_MODE=memory)"
}

# ============================================================
#  Step 2: Member login
# ============================================================
Write-Step "Member Login"

$loginBody = [PSCustomObject]@{
    phone    = "13800000001"
    password = "test123456"
}
$r = Invoke-Api "POST /api/auth/login" "POST" "/api/auth/login" $loginBody
if (-not $r.success) {
    Write-Info "Login failed, fallback to X-Member-Id: 1"
    $script:Context.MemberId = "1"
    $script:Context.Token = $null
} else {
    $script:Context.MemberId = $r.data.memberId.ToString()
    $script:Context.Token = $r.data.accessToken
    Write-Info "MemberId: $($script:Context.MemberId)"
}

$memberHeaders = @{ "X-Member-Id" = $script:Context.MemberId }
if ($script:Context.Token) {
    $memberHeaders["Authorization"] = "Bearer $($script:Context.Token)"
}

# ============================================================
#  Step 3: Browse product list
# ============================================================
Write-Step "Browse Product List"

$r = Invoke-Api "GET /api/product/list" "GET" "/api/product/list"
if ($r.success -and $r.data.success) {
    $products = $r.data.products
    Write-Info "Products total: $($products.Count)"
    $target = $products | Where-Object { $_.product_id -eq $ProductId } | Select-Object -First 1
    if ($target) {
        Write-Info "Target product: $($target.name) price=$($target.price)"
    } else {
        Write-Info "Product $ProductId not found, fallback to first"
        $ProductId = $products[0].product_id
    }
}

# ============================================================
#  Step 4: Product detail
# ============================================================
Write-Step "Product Detail"

$r = Invoke-Api "GET /api/product/{id}" "GET" "/api/product/$ProductId"

# ============================================================
#  Step 5: Submit checkout (create order)
# ============================================================
Write-Step "Submit Checkout (Create Order)"

$checkoutBody = [PSCustomObject]@{
    items = @(
        [PSCustomObject]@{
            productId = $ProductId
            quantity  = $Quantity
            price     = 199.00
        }
    )
    consignee = [PSCustomObject]@{
        name     = "Zhang San"
        phone    = "13800000001"
        province = "Shandong"
        city     = "Taian"
        district = "Taishan"
        detail   = "Zhuxiang Rd 1"
    }
    payment = [PSCustomObject]@{
        method  = "wechat"
        channel = "wechat"
    }
}

$r = Invoke-Api "POST /api/checkout/submit" "POST" "/api/checkout/submit" $checkoutBody
if ($r.success -and $r.data.success) {
    $script:Context.OrderId = $r.data.orderId
    Write-Info "OrderId: $($script:Context.OrderId)"
}

# ============================================================
#  Step 6: Inventory deduct
# ============================================================
Write-Step "Inventory Deduct"

$deductBody = [PSCustomObject]@{
    productId = $ProductId
    quantity  = $Quantity
}
$r = Invoke-Api "POST /api/inventory/deduct" "POST" "/api/inventory/deduct" $deductBody
if ($r.success -and $r.data.success) {
    Write-Info "StockAfter: $($r.data.stockAfter)"
}

# ============================================================
#  Step 7: Create payment order
# ============================================================
Write-Step "Create Payment Order"

$totalAmount = 199.00 * $Quantity
$payBody = [PSCustomObject]@{
    orderId      = $script:Context.OrderId
    orderType    = "retail"
    totalAmount  = $totalAmount
    payChannel   = "wechat"
    payMethod    = "jsapi"
    sceneType    = "order_pay"
}

$r = Invoke-Api "POST /api/payment/pay" "POST" "/api/payment/pay" $payBody -ExtraHeaders $memberHeaders
if ($r.success -and $r.data.success) {
    $script:Context.PayNo = $r.data.data.payNo
    Write-Info "PayNo: $($script:Context.PayNo)"
}

# ============================================================
#  Step 8: Payment callback (simulate success)
# ============================================================
Write-Step "Payment Callback (Simulate Success)"

if ($script:Context.PayNo) {
    $callbackBody = [PSCustomObject]@{
        channelTradeNo   = "CB$([DateTime]::Now.Ticks)"
        payNo            = $script:Context.PayNo
        callbackContent  = [PSCustomObject]@{
            status = "SUCCESS"
            amount = $totalAmount
        }
    }
    $r = Invoke-Api "POST /api/payment/callback/pay" "POST" "/api/payment/callback/pay" $callbackBody
}

# ============================================================
#  Step 9: Logistics create order
# ============================================================
Write-Step "Logistics Create Order (Ship)"

if ($script:Context.OrderId) {
    $logisticsBody = [PSCustomObject]@{
        orderId     = $script:Context.OrderId
        orderType   = "retail"
        carrier     = "SF"
        serviceType = "standard"
        sender = [PSCustomObject]@{
            name    = "Zhuxiang Warehouse"
            phone   = "0538-1234567"
            address = "Taishan Zhuxiang Rd 1 Warehouse"
        }
        receiver = [PSCustomObject]@{
            name     = "Zhang San"
            phone    = "13800000001"
            address  = "Taishan Zhuxiang Rd 1"
            province = "Shandong"
            city     = "Taian"
        }
        weight       = 1.5
        pieceCount   = $Quantity
        volume       = 0.01
        insuredValue = $totalAmount
        settleMode   = "monthly"
    }
    $r = Invoke-Api "POST /api/logistics/order" "POST" "/api/logistics/order" $logisticsBody -ExtraHeaders $memberHeaders
    if ($r.success -and $r.data.success) {
        $script:Context.WaybillNo = $r.data.data.waybillNo
        Write-Info "WaybillNo: $($script:Context.WaybillNo)"
    }
}

# ============================================================
#  Step 10: Logistics query (waybill detail)
# ============================================================
Write-Step "Logistics Query (Waybill Detail)"

if ($script:Context.WaybillNo) {
    $r = Invoke-Api "GET /api/logistics/order/{waybillNo}" "GET" "/api/logistics/order/$($script:Context.WaybillNo)"
}

# ============================================================
#  Summary report
# ============================================================
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Order Flow Integration Test Summary" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
$color = if ($script:FailCount -eq 0) { "Green" } else { "Yellow" }
Write-Host "  Passed: $script:PassCount  Failed: $script:FailCount" -ForegroundColor $color
Write-Host "  MemberId:  $($script:Context.MemberId)"
Write-Host "  OrderId:   $($script:Context.OrderId)"
Write-Host "  PayNo:     $($script:Context.PayNo)"
Write-Host "  WaybillNo: $($script:Context.WaybillNo)"
Write-Host "========================================" -ForegroundColor Cyan

if ($script:FailCount -gt 0) { exit 1 }
