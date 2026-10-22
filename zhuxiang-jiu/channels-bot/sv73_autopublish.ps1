# sv73 自动发布编排器——无人值守核心(2026-10-23, 迁新机 7x24)
# 每日 18:05 计划任务: 消费生产 API 的 rpa_pending 队列
#   wechat_channels → bot.js(视频号) / douyin → douyin-bot.js(抖音), 串行
# 失败自动回执 mark_failed(防重发留痕, 人工修复后可重试)
# 成功记日志待 manage 核验(URL 回执二版); 无视频跳过不回执(下轮再试)
# 视频供给约定: $PSScriptRoot\videos\ 下按 shortCode 优先/contentId 兜底匹配 mp4
# 用法: powershell -File sv73_autopublish.ps1
$ErrorActionPreference = 'Continue'
$dir = $PSScriptRoot
$log = Join-Path $dir 'autopublish.log'
function Log($m) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $m"
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

# ---- 单实例锁(2 小时内重复触发视为上轮未完, 放弃本轮) ----
$autoDir = Join-Path $dir 'auto'
New-Item -ItemType Directory $autoDir -Force | Out-Null
$lock = Join-Path $autoDir 'RUNNING.lock'
if ((Test-Path $lock) -and ((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes -lt 120) {
    Log 'SKIP: 上一轮仍在运行(2h 锁), 退出'
    exit 0
}
Set-Content $lock (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

try {
    # ---- 生产 API 登录(与 sv73_publish_process.ps1 同凭据) ----
    $BASE = 'https://zyjiu.com'
    $login = Invoke-RestMethod -Uri "$BASE/api/auth/login" -Method Post -ContentType 'application/json' -Body (@{ phone='13800000002'; password='test123456' } | ConvertTo-Json) -TimeoutSec 20
    $h = @{ Authorization = "Bearer $($login.accessToken)"; 'X-Role' = 'admin' }

    # ---- 待发布清单 ----
    $pend = Invoke-RestMethod -Uri "$BASE/api/promo/rpa/pending" -Headers $h -TimeoutSec 30
    $items = @($pend.data)
    Log "pending 共 $($items.Count) 条"
    if ($items.Count -eq 0) { exit 0 }

    # ---- node 定位(相对 nodejs 目录递归探测, 兼容版本子目录; PATH 兜底) ----
    $node = $null
    foreach ($nd in @((Join-Path $dir '..\nodejs'), 'D:\nodejs', 'C:\nodejs', (Join-Path $dir 'nodejs'))) {
        if (Test-Path $nd) {
            $hit = Get-ChildItem $nd -Recurse -Filter node.exe -Depth 2 -EA SilentlyContinue | Select-Object -First 1
            if ($hit) { $node = $hit.FullName; break }
        }
    }
    if (-not $node) { $node = (Get-Command node -EA SilentlyContinue).Source }
    if (-not $node) { Log 'FAIL: node.exe 未找到'; exit 1 }

    $noVideo = @(); $okCnt = 0; $failCnt = 0
    foreach ($c in $items) {
        $cid = $c.contentId
        $pf = $c.platform
        # 平台分流(xhs 按平台规则停用; weibo/ks/tt 属二阶段)
        $bot = $null
        if ($pf -eq 'wechat_channels') { $bot = 'bot.js' }
        elseif ($pf -eq 'douyin') { $bot = 'douyin-bot.js' }
        else { Log "[$cid/$pf] SKIP: 平台不在第一版自动范围"; continue }

        # 视频定位(shortCode 优先, contentId 兜底)
        $sc = [string]$c.shortCode
        $vdir = Join-Path $dir 'videos'
        $mp4 = $null
        if ($sc) { $mp4 = Get-ChildItem $vdir -Filter "*$sc*.mp4" -File -EA SilentlyContinue | Select-Object -First 1 }
        if (-not $mp4) { $mp4 = Get-ChildItem $vdir -Filter "*$cid*.mp4" -File -EA SilentlyContinue | Select-Object -First 1 }
        if (-not $mp4) { Log "[$cid/$pf] NO-VIDEO: videos\ 无匹配(shortCode=$sc), 留待下轮"; $noVideo += $cid; continue }

        # desc 组装(body + hashtags, 历史格式)
        $desc = "$($c.body) $($c.hashtags)".Trim()
        $cfgPath = Join-Path $autoDir "config_auto_$($pf)_$cid.json"
        $cfg = @{ action='publish'; mp4=$mp4.FullName; desc=$desc; waitLoginMinutes=3 }
        if ($pf -eq 'wechat_channels' -and $c.title) { $cfg.shortTitle = $c.title }
        # 无 BOM 写入(PS5 Set-Content UTF8 带 BOM 会炸 bot 的 JSON.parse)
        [IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json -Depth 3), [Text.UTF8Encoding]::new($false))
        Log "[$cid/$pf] PUBLISH: $($mp4.Name) desc=$($desc.Length)字"

        # 串行跑 bot
        Push-Location $dir
        & $node $bot $cfgPath *>&1 | Tee-Object -FilePath (Join-Path $autoDir "run_${pf}_$cid.log") | ForEach-Object { $_ }
        $rc = $LASTEXITCODE
        Pop-Location
        if ($rc -eq 0) {
            Log "[$cid/$pf] SUCCESS: 发布动作完成(待 manage 核验回执)"
            $okCnt++
        } else {
            $err = "bot exit=$rc(详 run_${pf}_$cid.log)"
            Log "[$cid/$pf] FAIL: $err"
            try {
                $body = @{ error = $err.Substring(0, [Math]::Min(190, $err.Length)) } | ConvertTo-Json
                $null = Invoke-RestMethod -Uri "$BASE/api/promo/rpa/$cid/receipt" -Method Post -Headers $h -ContentType 'application/json' -Body $body -TimeoutSec 20
                Log "[$cid/$pf] RECEIPT-FAILED: mark_failed 已登记(防重发)"
            } catch { Log "[$cid/$pf] RECEIPT-FAILED 失败: $($_.Exception.Message)" }
            $failCnt++
        }
    }
    Log "===== 汇总 ok=$okCnt fail=$failCnt 无视频待供=$($noVideo.Count)($($noVideo -join ',')) ====="
    if ($noVideo.Count) {
        Add-Content -Path (Join-Path $dir 'PENDING_NO_VIDEO.flag') -Value "[$(Get-Date -Format 'MM-dd HH:mm')] $($noVideo -join ',')" -Encoding UTF8
    }
} catch {
    Log "FAIL: $($_.Exception.Message)"
    Add-Content -Path (Join-Path $dir 'PUBLISH_FAIL_ALERT.flag') -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] autopublish FAILED: $($_.Exception.Message)" -Encoding UTF8
    exit 1
} finally {
    Remove-Item $lock -Force -EA SilentlyContinue
}
