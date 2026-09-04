# ==========================================================================
# 47号·L2/L3 信值验真风控模块 部署脚本
# (构建 + 启动 + 冒烟验收 + 先验回流开关切换 + 全量实机验收)
#
# 用法:
#   .\scripts\deploy-trust-risk-47.ps1                  # 默认: 构建+启动+冒烟
#   .\scripts\deploy-trust-risk-47.ps1 -Action smoke    # 仅冒烟(零写入, 生产可用)
#   .\scripts\deploy-trust-risk-47.ps1 -Action verify   # 全量实机验收×1轮(写测试数据, 仅验收环境!)
#   .\scripts\deploy-trust-risk-47.ps1 -Action prior-on # 开启先验回流(RISK_PRIOR_MODE=on, 重建容器)
#   .\scripts\deploy-trust-risk-47.ps1 -Action prior-off# 关闭先验回流(重建容器)
#   .\scripts\deploy-trust-risk-47.ps1 -Action status   # 模块状态速览(看板+开关态)
#
# 配套文档:
#   zhuxiang-jiu\docs\47号_验真风控运维手册.md     (日常运维口径)
#   zhuxiang-jiu\docs\47号_完整交付总结文档.md     (交付全景)
#
# 注意:
#   - 先验回流默认 off(画像只沉淀不干预); prior-on 前请对照
#     运维手册 §七启用检查单(画像观察期/复核清零/公平性桥接)
#   - verify 动作会清理 zhuxiang:trust47:*/trust45 存证事件/
#     ai46:* 并灌入测试数据(幂等但破坏现有画像)——仅验收环境
#   - verify 动作收尾会把 RISK_PRIOR_MODE 翻回 off(P3 脚本行为)
# ==========================================================================

param(
    [ValidateSet("all", "smoke", "verify", "prior-on", "prior-off", "status")]
    [string]$Action = "all",
    [string]$BaseUrl = "http://127.0.0.2:8000"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "zhuxiang-jiu\backend"
$ComposeFile = Join-Path $ProjectRoot "docker-compose.yml"
$AdminHeaders = @{ "X-Role" = "admin"; "Content-Type" = "application/json" }

function Write-Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "[ERR]  $msg" -ForegroundColor Red }

function Get-HttpStatus($method, $url, $headers, $body) {
    # 返回状态码; 请求异常(连接拒绝等)返回 0
    try {
        $params = @{ Uri = $url; Method = $method; TimeoutSec = 15 }
        if ($headers) { $params.Headers = $headers }
        if ($body -ne $null) { $params.Body = $body }
        $null = Invoke-RestMethod @params
        return 200
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            return [int]$_.Exception.Response.StatusCode
        }
        return 0
    } catch {
        return 0
    }
}

function Wait-BackendHealthy($maxSeconds = 120) {
    Write-Step "等待 backend healthcheck 收敛(最长 ${maxSeconds}s)"
    $deadline = (Get-Date).AddSeconds($maxSeconds)
    while ((Get-Date) -lt $deadline) {
        $status = docker ps --filter "name=zhuxiang-jiu-backend-1" --format "{{.Status}}" 2>$null
        if ($status -match "healthy") {
            Start-Sleep -Seconds 2   # 再等一拍保 uvicorn 就绪
            Write-Ok "backend healthy: $status"
            return $true
        }
        Start-Sleep -Seconds 5
    }
    Write-Err "backend 未在 ${maxSeconds}s 内转 healthy(当前: $status)"
    Write-Host "  排查: .\scripts\docker-start.ps1 -Action logs" -ForegroundColor DarkGray
    return $false
}

function Invoke-ModuleSmoke {
    # 冒烟验收: 只读端点 + 鉴权 + 路由顺序——零写入, 生产环境安全
    Write-Step "47号冒烟验收(零写入)"
    $pass = 0; $fail = 0

    # 1. 基础健康
    $code = Get-HttpStatus "GET" "$BaseUrl/api/decision/health"
    if ($code -eq 200) { $pass++; Write-Ok "决策中枢健康 200" }
    else { $fail++; Write-Err "决策中枢健康 $code" }

    # 2. 看板五区块
    try {
        $dash = Invoke-RestMethod -Uri "$BaseUrl/api/trust/risk/dashboard" -Headers $AdminHeaders -TimeoutSec 30
        $zoneCount = ($dash.zones | Get-Member -MemberType NoteProperty).Count
        if ($dash.success -and $zoneCount -eq 5) {
            $pass++
            Write-Ok "看板五区块聚合(zones=$zoneCount, priorMode=$($dash.priorMode))"
        } else { $fail++; Write-Err "看板区块异常: zones=$zoneCount" }
        $script:CurrentPriorMode = $dash.priorMode
    } catch {
        $fail++; Write-Err "看板请求失败: $($_.Exception.Message)"
    }

    # 3. 团伙视图(纯读)
    $code = Get-HttpStatus "GET" "$BaseUrl/api/trust/risk/collusion" $AdminHeaders
    if ($code -eq 200) { $pass++; Write-Ok "团伙视图 200" }
    else { $fail++; Write-Err "团伙视图 $code" }

    # 4. 路由顺序(画像 404 不被字面路由抢匹配)
    $code = Get-HttpStatus "GET" "$BaseUrl/api/trust/risk/99999" $AdminHeaders
    if ($code -eq 404) { $pass++; Write-Ok "画像未知档案 404(路由顺序正确)" }
    else { $fail++; Write-Err "画像未知档案应 404, 实得 $code" }

    # 5. 鉴权(缺 X-Role 拒绝)
    $code = Get-HttpStatus "GET" "$BaseUrl/api/trust/risk/dashboard"
    if ($code -eq 403) { $pass++; Write-Ok "看板缺 Role 403" }
    else { $fail++; Write-Err "看板缺 Role 应 403, 实得 $code" }

    $code = Get-HttpStatus "POST" "$BaseUrl/api/trust/risk/dashboard/fairness-bridge"
    if ($code -eq 403) { $pass++; Write-Ok "桥接缺 Role 403" }
    else { $fail++; Write-Err "桥接缺 Role 应 403, 实得 $code" }

    # 6. 前端面板静态文件
    $panelHtml = Join-Path $ProjectRoot "zhuxiang-jiu\trust-risk-dashboard.html"
    $panelJs = Join-Path $ProjectRoot "zhuxiang-jiu\js\trust-risk-dashboard.js"
    if ((Test-Path $panelHtml) -and (Test-Path $panelJs)) {
        $pass++; Write-Ok "前端面板文件就位(trust-risk-dashboard.html/js)"
    } else { $fail++; Write-Err "前端面板文件缺失" }

    Write-Host ""
    if ($fail -eq 0) {
        Write-Ok "冒烟验收通过: $pass/$($pass+$fail)"
        return $true
    } else {
        Write-Err "冒烟验收: $pass 通过 / $fail 失败"
        return $false
    }
}

function Invoke-VerifyAll {
    # 全量实机验收: P0-P4 五套脚本各 ×1 轮
    # !!! 会清理 zhuxiang:trust47:* / trust45 存证事件 / ai46:* 并灌测试数据 !!!
    Write-Step "全量实机验收(验收环境专用——将清理并重灌模块数据)"
    Write-Warn "此动作破坏现有画像/存证留痕/公平性采样数据, 生产环境禁止执行"
    $confirm = Read-Host "确认继续? 输入 yes 执行, 其他任意键取消"
    if ($confirm -ne "yes") {
        Write-Host "已取消"
        return
    }

    $scripts = @(
        "verify_trust_risk_p0_live.py",
        "verify_trust_risk_p1_live.py",
        "verify_trust_risk_p2_live.py",
        "verify_trust_risk_p3_live.py",
        "verify_trust_risk_p4_live.py"
    )
    $failed = @()
    Push-Location $BackendDir
    try {
        foreach ($s in $scripts) {
            Write-Host "`n--- $s ---" -ForegroundColor Cyan
            python $s
            if ($LASTEXITCODE -ne 0) { $failed += $s }
        }
    } finally {
        Pop-Location
    }
    Write-Host ""
    if ($failed.Count -eq 0) {
        Write-Ok "五套实机验收全部通过(P3 含模式翻转, 收尾已回 off)"
    } else {
        Write-Err "失败套件: $($failed -join ', ')"
    }
    Write-Warn "P3 脚本收尾已将 RISK_PRIOR_MODE 翻回 off; 如需 on 请重跑 prior-on"
}

function Invoke-PriorToggle($mode) {
    Write-Step "切换先验回流 RISK_PRIOR_MODE=$mode(compose 环境变量 → 容器重建)"
    if ($mode -eq "on") {
        Write-Warn "启用前检查单(运维手册 §七): 画像运行≥2周 / 复核清零 / 公平性桥接无 flagged / watchlist 复核"
        $confirm = Read-Host "确认开启? 输入 yes 执行"
        if ($confirm -ne "yes") { Write-Host "已取消"; return }
    }
    $env:RISK_PRIOR_MODE = $mode
    # docker compose 把进度写 stderr——临时降级 Stop, 否则
    # PS5.1 会把 stderr 行当作异常终止脚本(docker-start.ps1 同款处理)
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker compose -p zhuxiang-jiu -f $ComposeFile up -d backend 2>&1 | ForEach-Object {
        if ($_ -match "Recreat|Start|Healthy") { Write-Host $_ }
    }
    $upExit = $LASTEXITCODE
    $ErrorActionPreference = $prevPref
    if ($upExit -ne 0) { Write-Err "容器重建失败(exit=$upExit)"; exit 1 }
    if (-not (Wait-BackendHealthy)) { exit 1 }

    # 验证开关生效
    try {
        $dash = Invoke-RestMethod -Uri "$BaseUrl/api/trust/risk/dashboard" -Headers $AdminHeaders -TimeoutSec 30
        if ($dash.priorMode -eq ($mode -eq "on")) {
            Write-Ok "先验回流已${mode}(priorMode=$($dash.priorMode))"
            if ($mode -eq "on") {
                Write-Host "  生效语义: v2 验真起点折扣 + L2/L3 入分守门(restricted ×0.5/watched ×0.8/trusted ×1.1)" -ForegroundColor DarkGray
            } else {
                Write-Host "  已回零影响态: 画像只沉淀不干预" -ForegroundColor DarkGray
            }
        } else {
            Write-Err "开关未生效(priorMode=$($dash.priorMode)); 检查 .env 是否覆盖"
        }
    } catch {
        Write-Err "开关验证请求失败: $($_.Exception.Message)"
    }
    Remove-Item Env:\RISK_PRIOR_MODE -ErrorAction SilentlyContinue
}

function Invoke-Status {
    Write-Step "47号模块状态速览"
    try {
        $dash = Invoke-RestMethod -Uri "$BaseUrl/api/trust/risk/dashboard" -Headers $AdminHeaders -TimeoutSec 30
        $rk = $dash.zones.ranking
        $ht = $dash.zones.hits
        $co = $dash.zones.collusion
        $rv = $dash.zones.reviews
        $tier = { param($n) $(if ($rk.byTier.$n) { $rk.byTier.$n } else { 0 }) }
        Write-Host "  先验回流: $(if ($dash.priorMode) {'ON(通道收窄生效)'} else {'OFF(纯沉淀观察·默认)'})"
        Write-Host "  画像总数: $($rk.total)  分层: trusted=$(& $tier 'trusted') standard=$(& $tier 'standard') watched=$(& $tier 'watched') restricted=$(& $tier 'restricted')"
        Write-Host "  命中总数: $(($ht.totals.PSObject.Properties | Measure-Object).Count) 类信号  画像事件: $($ht.totalEvents)"
        Write-Host "  团伙嫌疑: $($co.totals.suspects) 档案 / $($co.totals.mutualPairs) 互证对"
        Write-Host "  待复核申诉: $($rv.pendingCount)"
        Write-Ok "看板: $BaseUrl/api/trust/risk/dashboard"
        Write-Host "  面板: zhuxiang-jiu\trust-risk-dashboard.html(文件直开配后端地址)" -ForegroundColor DarkGray
    } catch {
        Write-Err "状态读取失败(backend 未启动?): $($_.Exception.Message)"
        Write-Host "  启动: .\scripts\docker-start.ps1 -Action all" -ForegroundColor DarkGray
    }
}

# ---- 前置检查 ----
Write-Step "前置检查"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err "docker 命令未找到"; exit 1
}
if (-not (Test-Path $ComposeFile)) {
    Write-Err "docker-compose.yml 不存在: $ComposeFile"; exit 1
}
Set-Location $ProjectRoot
Write-Ok "项目根: $ProjectRoot"

# ---- 主流程 ----
switch ($Action) {
    "smoke" {
        if (-not (Wait-BackendHealthy 30)) { exit 1 }
        if (-not (Invoke-ModuleSmoke)) { exit 1 }
    }
    "verify" {
        if (-not (Wait-BackendHealthy 30)) { exit 1 }
        Invoke-VerifyAll
    }
    "prior-on"  { Invoke-PriorToggle "on" }
    "prior-off" { Invoke-PriorToggle "off" }
    "status"    { Invoke-Status }
    default {
        # all: 构建 + 启动 + 冒烟
        Write-Step "构建镜像(含 47号 P0-P4 代码)"
        $prevPref = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $buildOutput = docker compose -p zhuxiang-jiu -f $ComposeFile build backend 2>&1
        $buildExit = $LASTEXITCODE
        $ErrorActionPreference = $prevPref
        if ($buildExit -ne 0) {
            Write-Err "构建失败, 完整日志:"
            $buildOutput | ForEach-Object { Write-Host $_ -ForegroundColor DarkGray }
            exit 1
        }
        Write-Ok "镜像构建成功"

        Write-Step "启动服务(backend+redis)"
        $prevPref = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $upOutput = docker compose -p zhuxiang-jiu -f $ComposeFile up -d 2>&1
        $upExit = $LASTEXITCODE
        $ErrorActionPreference = $prevPref
        if ($upExit -ne 0) {
            Write-Err "启动失败:"; $upOutput | ForEach-Object { Write-Host $_ -ForegroundColor DarkGray }
            exit 1
        }
        Write-Ok "服务已启动"

        if (-not (Wait-BackendHealthy)) { exit 1 }
        if (-not (Invoke-ModuleSmoke)) { exit 1 }

        Write-Host "`n后续操作:" -ForegroundColor Cyan
        Write-Host "  日常状态:   .\scripts\deploy-trust-risk-47.ps1 -Action status"
        Write-Host "  开启回流:   .\scripts\deploy-trust-risk-47.ps1 -Action prior-on   (对照运维手册§七检查单)"
        Write-Host "  全量验收:   .\scripts\deploy-trust-risk-47.ps1 -Action verify    (仅验收环境)"
        Write-Host "  运维手册:   zhuxiang-jiu\docs\47号_验真风控运维手册.md`n"
    }
}
