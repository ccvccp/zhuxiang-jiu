# ============================================================
# 上线前检查清单 · 第三节 Docker 容器联调 自动化脚本
# 对应: 上线前检查清单.md 第 3.1 ~ 3.8 项
#
# 用法(用户终端执行, 非 IDE 沙箱):
#   powershell -ExecutionPolicy Bypass -File scripts\docker-prelaunch-check.ps1
#
# 前提: 已安装 Docker Desktop; 未运行时脚本会尝试启动并等待就绪
# 退出码: 0 = 全部通过; 1 = 存在失败项(见汇总)
#
# 说明:
#   - 3.8 重启持久化用"测试键写入→重启→读取"验证 Redis AOF;
#     backend 启动命令本身会重跑幂等 seed(先清后写),
#     因此冒烟产生的业务数据在重启后被重置为初始态属设计行为
# ============================================================

$ErrorActionPreference = "Continue"

$Root    = Split-Path -Parent $PSScriptRoot     # 仓库根(docker-compose.yml 所在)
$Compose = Join-Path $Root "docker-compose.yml"
# 显式项目名: 仓库目录名"网站架构设计"全中文, compose v2 从目录名派生项目名时
# 清洗非法字符后为空 → "project name must not be empty" 报错, 必须显式指定
$Project = "zhuxiang-jiu"

$script:Pass = 0
$script:Fail = 0
$script:FailItems = @()

function Step($name) { Write-Host "`n========== $name ==========" -ForegroundColor Cyan }
function Ok($msg)    { $script:Pass++; Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Bad($msg)   { $script:Fail++; $script:FailItems += $msg; Write-Host "  [FAIL] $msg" -ForegroundColor Red }

function Wait-Docker([int]$TimeoutSec = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Wait-Health([int]$TimeoutSec = 150) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri "http://localhost:8000/api/decision/health" -TimeoutSec 3 | Out-Null
            return $true
        } catch { Start-Sleep -Seconds 5 }
    }
    return $false
}

# PS 5.1 中文 JSON 安全提交(UTF-8 字节流)
function Post-Json($Uri, $Body) {
    $json = $Body | ConvertTo-Json -Depth 5
    return Invoke-RestMethod -Method Post -Uri $Uri -ContentType "application/json" `
        -Body ([Text.Encoding]::UTF8.GetBytes($json))
}

# 兼容 {data:{stock}} 与顶层 {stock} 两种响应形状
function Get-Stock($Pid2) {
    $r = Invoke-RestMethod -Uri "http://localhost:8000/api/inventory/stock?productId=$Pid2" -TimeoutSec 10
    if ($r.PSObject.Properties["data"]) { return [int]$r.data.stock }
    return [int]$r.stock
}

if (-not (Test-Path $Compose)) { Write-Host "未找到 $Compose" -ForegroundColor Red; exit 1 }
Push-Location $Root

# ---------- 3.1 Docker daemon + WSL2 配置 ----------
Step "3.1 Docker Desktop / WSL2 内存配置"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Docker daemon 未运行, 尝试启动 Docker Desktop(WSL2 引擎初始化约 1-3 分钟)..."
    $dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dd) { Start-Process $dd } else { Write-Host "  未找到 Docker Desktop 安装路径" -ForegroundColor Red }
    if (Wait-Docker 180) { Ok "Docker daemon 已就绪" }
    else { Bad "Docker daemon 180 秒内未就绪(请手动启动后重跑)" }
} else { Ok "Docker daemon 已就绪" }

$wslcfg = Join-Path $env:USERPROFILE ".wslconfig"
if (Test-Path $wslcfg) {
    if ((Get-Content $wslcfg -Raw) -match "memory\s*=\s*8GB") { Ok "WSL2 内存配置 8GB" }
    else { Bad ".wslconfig 存在但 memory 非 8GB" }
} else { Bad "未找到 $wslcfg" }

# ---------- 3.2 compose 配置校验 ----------
Step "3.2 docker compose config 校验"
docker compose -p $Project -f $Compose config --quiet
if ($LASTEXITCODE -eq 0) { Ok "compose 配置合法(env_file 缺失不阻断)" } else { Bad "compose config 校验失败" }

# ---------- 3.3 启动全栈 + healthcheck ----------
Step "3.3 docker compose up -d + healthcheck"
Write-Host "  构建并启动(首次构建较慢, 后续命中缓存)..."
docker compose -p $Project -f $Compose up -d --build 2>&1 | Select-Object -Last 4
if ($LASTEXITCODE -ne 0) { Bad "docker compose up 失败"; Pop-Location; exit 1 }

$backendId = docker compose -p $Project -f $Compose ps -q backend
if (Wait-Health 150) { Ok "健康探测 200(/api/decision/health)" }
else { Bad "150 秒内健康探测未通过" }
$hc = docker inspect --format "{{.State.Health.Status}}" $backendId
if ("$hc" -eq "healthy") { Ok "容器 healthcheck 状态: healthy" } else { Bad "容器 healthcheck 状态: $hc" }
$redisPing = docker compose -p $Project -f $Compose exec -T redis redis-cli ping
if ("$redisPing".Trim() -eq "PONG") { Ok "Redis 容器 PONG" } else { Bad "Redis ping 异常: $redisPing" }

# ---------- 3.4 容器内 seed ----------
Step "3.4 容器内执行 seed_redis.py"
$seedOut = docker compose -p $Project -f $Compose exec -T backend python scripts/seed_redis.py 2>&1
$seedOut | Select-Object -Last 10 | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -eq 0) { Ok "seed 脚本退出码 0" } else { Bad "seed 脚本执行失败" }

# 供应链 19 域抽查(直接查 Redis 键)
$wh   = docker compose -p $Project -f $Compose exec -T redis redis-cli LLEN zhuxiang:sc:supply_warehouses
$loc  = docker compose -p $Project -f $Compose exec -T redis redis-cli LLEN zhuxiang:sc:warehouse_locations
$stk  = docker compose -p $Project -f $Compose exec -T redis redis-cli LLEN zhuxiang:sc:warehouse_stock
$cpn  = docker compose -p $Project -f $Compose exec -T redis redis-cli HLEN zhuxiang:sc:checkout_coupons
$pts  = docker compose -p $Project -f $Compose exec -T redis redis-cli HLEN zhuxiang:sc:checkout_points
if ("$wh".Trim() -eq "4" -and "$loc".Trim() -eq "180" -and "$stk".Trim() -eq "14" `
    -and "$cpn".Trim() -eq "2" -and "$pts".Trim() -eq "5") {
    Ok "供应链域: 4仓/180库位/14仓储库存/2券/5等级积分"
} else {
    Bad "供应链域抽查不符: wh=$wh loc=$loc stk=$stk cpn=$cpn pts=$pts(预期 4/180/14/2/5)"
}

# ---------- 3.5 内存占用 ----------
Step "3.5 backend 容器内存占用(限额 1g)"
$stats = docker compose -p $Project -f $Compose stats --no-stream --format "{{.Name}} {{.MemUsage}} {{.MemPerc}}"
$stats | ForEach-Object { Write-Host "  $_" }
$memLine = "$stats" | Select-String "backend"
if ($memLine -match "([0-9.]+)GiB") {
    if ([double]$Matches[1] -ge 1.0) { Bad "内存占用达 $($Matches[1])GiB(接近/超过 1g 硬限)" }
    else { Ok "内存占用 $($Matches[1])GiB" }
} elseif ($memLine -match "([0-9.]+)MiB") { Ok "内存占用 $($Matches[1])MiB" }
else { Bad "未解析到 backend 内存数据" }

# ---------- 3.6 业务链冒烟 ----------
Step "3.6 业务链冒烟: 登录→checkout→库存核验→认领→结算"
$login = $null
try { $login = Post-Json "http://localhost:8000/api/auth/login" @{ phone = "13800000002"; password = "test123456" } } catch {}
if ($login -and $login.accessToken) { Ok "管理员登录换 JWT(role=$($login.role))" }
else { Bad "登录失败, 跳过后续业务冒烟"; $login = $null }

if ($login) {
    $stock0 = Get-Stock "ZX42-2026L05"
    $co = $null
    try {
        $co = Post-Json "http://localhost:8000/api/checkout/submit" @{
            items = @(@{ id = "ZX42-2026L05"; name = "竹韵佳酿"; price = 299; qty = 1 })
            memberLevel = "L3"
        }
    } catch { Bad "checkout 请求异常: $($_.Exception.Message)" }
    if ($co -and $co.success) { Ok "checkout 9 阶段事务成功(库存 $($stock0) → $($stock0 - 1))" }
    elseif ($co) { Bad "checkout 返回 success=false: $($co | ConvertTo-Json -Compress)" }

    try {
        $stock1 = Get-Stock "ZX42-2026L05"
        if ($stock1 -eq ($stock0 - 1)) { Ok "库存扣减核验一致($stock0 → $stock1)" }
        else { Bad "库存核验不符: $stock0 → $stock1(预期 $($stock0 - 1))" }
    } catch { Bad "库存查询异常: $($_.Exception.Message)" }

    $claim = $null
    try { $claim = Post-Json "http://localhost:8000/api/agent-shipping/claim" @{ agentId = 1; region = "taian" } } catch {}
    if ($claim -and $claim.success) { Ok "agent-shipping 认领成功" }
    else { Bad "认领失败: $($claim | ConvertTo-Json -Compress)" }

    try {
        $settle = Invoke-RestMethod -Uri "http://localhost:8000/api/agent-shipping/settlement?agentId=1" -TimeoutSec 10
        Ok "服务费结算统计可访问(HTTP 200)"
        Write-Host "    settlement: $($settle | ConvertTo-Json -Compress -Depth 5)"
    } catch { Bad "settlement 查询异常: $($_.Exception.Message)" }
}

# ---------- 3.7 Prometheus 指标 ----------
Step "3.7 GET /metrics Prometheus 文本格式"
try {
    $m = (Invoke-WebRequest -Uri "http://localhost:8000/metrics" -UseBasicParsing -TimeoutSec 10).Content
    if ($m -match "# HELP" -and $m -match "# TYPE") { Ok "Prometheus 文本格式完整(HELP/TYPE 存在, $($m.Length) 字符)" }
    else { Bad "/metrics 缺少 HELP/TYPE 行" }
} catch { Bad "/metrics 访问异常: $($_.Exception.Message)" }

# ---------- 3.8 重启持久化 ----------
Step "3.8 重启持久化验证(Redis AOF)"
docker compose -p $Project -f $Compose exec -T redis redis-cli SET prelaunch:persist-test ok | Out-Null
Write-Host "  重启 backend + redis..."
docker compose -p $Project -f $Compose restart backend redis 2>&1 | Out-Null
if (Wait-Health 150) {
    $v = docker compose -p $Project -f $Compose exec -T redis redis-cli GET prelaunch:persist-test
    if ("$v".Trim() -eq "ok") { Ok "Redis AOF 持久化生效(测试键重启后仍在)" }
    else { Bad "测试键重启后丢失: $v" }
    docker compose -p $Project -f $Compose exec -T redis redis-cli DEL prelaunch:persist-test | Out-Null
    Write-Host "  注: backend 重启时重跑幂等 seed, 冒烟业务数据重置为初始态属设计行为"
} else { Bad "重启后健康探测未恢复" }

# ---------- 汇总 ----------
Pop-Location
Write-Host "`n============================================================"
Write-Host "汇总: 通过 $script:Pass 项 / 失败 $script:Fail 项"
if ($script:Fail -gt 0) {
    $script:FailItems | ForEach-Object { Write-Host "  [FAIL] $_" -ForegroundColor Red }
    Write-Host "结论: 存在失败项, 请修复后重跑" -ForegroundColor Red
    exit 1
} else {
    Write-Host "结论: 第三节 Docker 联调 8 项全部通过" -ForegroundColor Green
    exit 0
}
