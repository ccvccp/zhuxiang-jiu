# sv73 自动发布编排器——无人值守核心(2026-10-23, 迁新机 7x24)
# 每日 18:05 计划任务: 消费生产 API 的 rpa_pending 队列
#   wechat_channels → bot.js(视频号) / douyin → douyin-bot.js(抖音), 串行
# 失败自动回执 mark_failed(防重发留痕, 人工修复后可重试)
# 成功记日志待 manage 核验(URL 回执二版); 无视频跳过不回执(下轮再试)
# 视频供给约定: $PSScriptRoot\videos\ 下按 shortCode 优先/contentId 兜底匹配 mp4
#               (token 边界精确匹配, 见下方 $rxSc/$rxCid; 禁子串通配, 防哈希段误命中)
# 用法: powershell -File sv73_autopublish.ps1
$ErrorActionPreference = 'Continue'
$dir = $PSScriptRoot
$log = Join-Path $dir 'autopublish.log'
function Log($m) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $m"
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

# ---- UTF-8 安全 REST ----
# PS5.1 的 Invoke-RestMethod 在响应头缺 charset 时按 ISO-8859-1 解码 body,
# 中文全变 latin1 乱码(2026-11-08 实证: 抖音 87 号已把乱码文案发布上线)。
# 这里取原始字节自行按 UTF-8 解, 请求体也一律以 UTF-8 字节发送。
function Invoke-Json([string]$Uri, [string]$Method = 'Get', $Headers = $null, $BodyObj = $null, [int]$TimeoutSec = 30) {
    $pa = @{ Uri = $Uri; Method = $Method; UseBasicParsing = $true; TimeoutSec = $TimeoutSec }
    if ($Headers) { $pa.Headers = $Headers }
    if ($null -ne $BodyObj) {
        $pa.ContentType = 'application/json; charset=utf-8'
        $pa.Body = [Text.Encoding]::UTF8.GetBytes(($BodyObj | ConvertTo-Json -Depth 5 -Compress))
    }
    $resp = Invoke-WebRequest @pa
    $raw = [Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())
    if (-not $raw.Trim()) { return $null }
    return ($raw | ConvertFrom-Json)
}

# ---- 乱码探针(fail-closed): 宁可不发, 也绝不把乱码发到线上作品 ----
function Test-Mojibake([string]$s) {
    if (-not $s) { return $false }
    $bad = ([regex]::Matches($s, '[\u0080-\u00FF]')).Count
    $cjk = ([regex]::Matches($s, '[\u4E00-\u9FFF]')).Count
    return ($bad -ge 6 -and $cjk -eq 0)
}

# ---- 生产 API 凭据(禁止硬编码; 依据 .trae/rules/security-hard-coded.md 一.2) ----
# 读取顺序: 环境变量 ZX_API_PHONE / ZX_API_PASSWORD
#           -> ZX_CRED_FILE 指向的 JSON(仓外, 形如 {"phone":"","password":""})
# 缺失即 throw(fail-closed): 宁可不发, 不把口令写进源码/日志/提交历史。
function Get-Sv73Cred {
    $ph = $env:ZX_API_PHONE; $pw = $env:ZX_API_PASSWORD
    if (-not ($ph -and $pw) -and $env:ZX_CRED_FILE -and (Test-Path $env:ZX_CRED_FILE)) {
        $o = ([Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($env:ZX_CRED_FILE)) | ConvertFrom-Json)
        $ph = $o.phone; $pw = $o.password
    }
    if (-not ($ph -and $pw)) { throw 'ZX_API_PHONE/ZX_API_PASSWORD 未配置(ZX_CRED_FILE 亦缺失), fail-closed 退出' }
    return @{ phone = $ph; password = $pw }
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
    $login = Invoke-Json "$BASE/api/auth/login" Post $null (Get-Sv73Cred) 20
    $h = @{ Authorization = "Bearer $($login.accessToken)"; 'X-Role' = 'admin' }

    # ---- 待发布清单 ----
    $pend = Invoke-Json "$BASE/api/promo/rpa/pending" Get $h $null 30
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
        # 2026-11-08 修子串误匹配: 原 `-Filter "*$cid*.mp4"` 是子串语义, cid=64 会命中
        # 哈希段 48f93"64"4010 → 64 号从 10-24 起连续拿 87 号的视频(实证文件名
        # sv73_48f936644010_mix_A-6I0GBD_87.mp4), 且 11-08 已真实发布上线。
        # 改为 token 边界精确匹配: 目标串必须被 _ - . 包围(或位于首尾), 数字/字母相邻不算。
        $sc = [string]$c.shortCode
        $vdir = Join-Path $dir 'videos'
        $all = @(Get-ChildItem $vdir -File -Filter '*.mp4' -EA SilentlyContinue)
        $rxSc = '(^|[_\-.])' + [regex]::Escape($sc) + '([_\-.]|$)'
        $rxCid = '(^|[_\-.])' + [regex]::Escape([string]$cid) + '([_\-.]|$)'
        $mp4 = $null
        if ($sc) { $mp4 = $all | Where-Object { $_.Name -match $rxSc } | Select-Object -First 1 }
        if (-not $mp4) { $mp4 = $all | Where-Object { $_.Name -match $rxCid } | Select-Object -First 1 }
        if (-not $mp4) { Log "[$cid/$pf] NO-VIDEO: videos\ 无精确匹配(shortCode=$sc, cid=$cid, 候选$($all.Count)个), 留待下轮"; $noVideo += $cid; continue }

        # desc 组装(body + hashtags, 历史格式)
        $desc = "$($c.body) $($c.hashtags)".Trim()
        # 乱码守卫(fail-closed): 文案已污染就拒发, 并登记 mark_failed 防重发
        if ((Test-Mojibake $desc) -or (Test-Mojibake ([string]$c.title))) {
            $merr = 'MOJIBAKE-GUARD: desc/title 含 latin1 乱码, 拒绝发布(防污染线上作品)'
            Log "[$cid/$pf] FAIL: $merr"
            try {
                $null = Invoke-Json "$BASE/api/promo/rpa/$cid/receipt" Post $h @{ error = $merr.Substring(0, [Math]::Min(190, $merr.Length)) } 20
                Log "[$cid/$pf] RECEIPT-FAILED: mark_failed 已登记(防重发)"
            } catch { Log "[$cid/$pf] RECEIPT-FAILED 失败: $($_.Exception.Message)" }
            $failCnt++; continue
        }
        $cfgPath = Join-Path $autoDir "config_auto_$($pf)_$cid.json"
        $cfg = @{ action='publish'; mp4=$mp4.FullName; desc=$desc; waitLoginMinutes=3 }
        # 视频号短标题硬上限 16 字, 超长平台直接拒发(2026-11-08 实证: 18字→"标题超过16字")
        if ($pf -eq 'wechat_channels' -and $c.title) {
            $st = ([string]$c.title).Trim()
            if ($st.Length -gt 16) { Log "[$cid/$pf] 短标题 $($st.Length)字 超限, 截断为 16字"; $st = $st.Substring(0, 16) }
            $cfg.shortTitle = $st
        }
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
                $eb = @{ error = $err.Substring(0, [Math]::Min(190, $err.Length)) }
                $null = Invoke-Json "$BASE/api/promo/rpa/$cid/receipt" Post $h $eb 20
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
