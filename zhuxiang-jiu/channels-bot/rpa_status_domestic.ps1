# 国内生产 RPA 队列查询(login -> /api/promo/rpa/pending)
$ErrorActionPreference = 'Stop'
$BASE = 'https://zyjiu.com'
$login = @{ phone = '13800000002'; password = 'test123456' } | ConvertTo-Json
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
