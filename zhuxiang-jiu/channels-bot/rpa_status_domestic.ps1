# 国内生产 RPA 队列查询(login -> /api/promo/rpa/pending)
$ErrorActionPreference = 'Stop'
$BASE = 'https://zyjiu.com'
# ---- 生产 API 凭据(禁止硬编码; 依据 .trae/rules/security-hard-coded.md 一.2) ----
# 读取顺序: 环境变量 ZX_API_PHONE / ZX_API_PASSWORD
#           -> ZX_CRED_FILE 指向的 JSON(仓外, 形如 {"phone":"","password":""})
# 缺失即 throw(fail-closed): 不把口令写进源码/日志/提交历史。
# 读取顺序(2026-11-24 fix placeholder incident):
#   (1) ZX_CRED_FILE -> external JSON with locked ACL (highest priority)
#   (2) env ZX_API_PHONE / ZX_API_PASSWORD (only when neither is a template placeholder)
# Background: services.exe caches the process env block; after clearing the registry,
#   SYSTEM scheduled tasks could still read placeholders ("your phone/password"), which
#   caused 12 consecutive days of login 404. File-first plus a placeholder guard removes
#   that failure mode. fail-closed: never hardcode a secret; throw when no valid credential.
function Get-Sv73Cred {
    $ph = $null; $pw = $null
    # Placeholder guard: empty value or template wording is treated as invalid
    $isPlaceholder = { param($v) (-not $v) -or ($v -match '你的|手机号|密码|xxx|XXX') }
    if ($env:ZX_CRED_FILE -and (Test-Path $env:ZX_CRED_FILE)) {
        $o = ([Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($env:ZX_CRED_FILE)) | ConvertFrom-Json)
        $ph = $o.phone; $pw = $o.password
    }
    if (-not ($ph -and $pw) -and -not (& $isPlaceholder $env:ZX_API_PHONE) -and -not (& $isPlaceholder $env:ZX_API_PASSWORD)) {
        $ph = $env:ZX_API_PHONE; $pw = $env:ZX_API_PASSWORD
    }
    if (-not ($ph -and $pw)) { throw 'ZX_CRED_FILE missing or credential invalid (env is placeholder), fail-closed' }
    return @{ phone = $ph; password = $pw }
}
$login = (Get-Sv73Cred) | ConvertTo-Json
$tok = $null
try {
    $r = Invoke-RestMethod -Uri "$BASE/api/auth/login" -Method Post -ContentType 'application/json' -Body $login -TimeoutSec 15
    $tok = $r.accessToken
} catch { Write-Host ("login FAIL: " + $_.Exception.Message); exit 1 }
if (-not $tok) { Write-Host 'login no token'; exit 1 }
Write-Host 'login OK'

$h = @{ Authorization = "Bearer $tok"; 'X-Role' = 'admin' }
$pend = Invoke-RestMethod -Uri "$BASE/api/promo/rpa/pending" -Headers $h -TimeoutSec 15
Write-Host '--- rpa pending ---'
($pend.data | ConvertTo-Json -Depth 5 -Compress).Substring(0, [Math]::Min(900, ($pend.data | ConvertTo-Json -Depth 5 -Compress).Length))

# 全量 content 状态(75-94)
Write-Host '--- contents 75-94 status ---'
$contents = Invoke-RestMethod -Uri "$BASE/api/promo/contents?limit=100" -Headers $h -TimeoutSec 20
foreach ($c in @($contents.data)) {
    $cid = $c.contentId
    if ($cid -ge 75 -and $cid -le 94) {
        $mode = $c.receipt.mode
        $url = $c.receipt.url
        $pub = $c.publishedAt
        Write-Host ("cid=$cid status=$($c.status) receipt=$mode pubAt=$pub url=$($url)")
    }
}
