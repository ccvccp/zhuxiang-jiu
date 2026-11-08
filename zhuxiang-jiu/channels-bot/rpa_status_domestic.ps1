# 国内生产 RPA 队列查询(login -> /api/promo/rpa/pending)
$ErrorActionPreference = 'Stop'
$BASE = 'https://zyjiu.com'
# ---- 生产 API 凭据(禁止硬编码; 依据 .trae/rules/security-hard-coded.md 一.2) ----
# 读取顺序: 环境变量 ZX_API_PHONE / ZX_API_PASSWORD
#           -> ZX_CRED_FILE 指向的 JSON(仓外, 形如 {"phone":"","password":""})
# 缺失即 throw(fail-closed): 不把口令写进源码/日志/提交历史。
function Get-Sv73Cred {
    $ph = $env:ZX_API_PHONE; $pw = $env:ZX_API_PASSWORD
    if (-not ($ph -and $pw) -and $env:ZX_CRED_FILE -and (Test-Path $env:ZX_CRED_FILE)) {
        $o = ([Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($env:ZX_CRED_FILE)) | ConvertFrom-Json)
        $ph = $o.phone; $pw = $o.password
    }
    if (-not ($ph -and $pw)) { throw 'ZX_API_PHONE/ZX_API_PASSWORD 未配置(ZX_CRED_FILE 亦缺失), fail-closed 退出' }
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
