# 76/84 号·微短剧付费档对标四条落地部署(3 文件 -> C:\backend)
# 改动: 84 号 SHORT_DURATION 45→30 微档 / 标签矩阵五标签 / 单集标题爽文钩;
#       76 号 release 端点 free_episodes 卡点弹性
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/3] 备份 + 下载覆盖(3 文件)'
$tag = 'paytier-20261017'
foreach ($f in @('services\drama84_script_service.py', 'services\drama76_series_service.py', 'routes\drama76_routes.py')) {
    $src = "C:\backend\$f"
    if (Test-Path $src) { Copy-Item $src "C:\tmp8147\$(Split-Path $f -Leaf).bak-$tag" -Force }
}
$hc = & curl.exe -s -o C:\tmp8147\drama-paytier.tgz -w '%{http_code}' "https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/drama-paytier-20261017.tgz"
if ($hc -ne '200') { Write-Host "PAYTIER-DEPLOY-FAIL download http=$hc"; exit 1 }
New-Item -ItemType Directory -Force C:\tmp8147\d84-paytier | Out-Null
tar -xzf C:\tmp8147\drama-paytier.tgz -C C:\tmp8147\d84-paytier
Copy-Item C:\tmp8147\d84-paytier\services\drama84_script_service.py C:\backend\services\ -Force
Copy-Item C:\tmp8147\d84-paytier\services\drama76_series_service.py C:\backend\services\ -Force
Copy-Item C:\tmp8147\d84-paytier\routes\drama76_routes.py C:\backend\routes\ -Force
Info ("svc84: " + (Get-Item C:\backend\services\drama84_script_service.py).Length + "B")
Info ("svc76: " + (Get-Item C:\backend\services\drama76_series_service.py).Length + "B")
Info ("rt76 : " + (Get-Item C:\backend\routes\drama76_routes.py).Length + "B")

Step '[2/3] 重启后端(nssm restart + sc start 兜底)'
cmd /c "C:\nginx\nssm.exe restart backend8147" | Out-Null
Start-Sleep -Seconds 12
$h = & curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/decision/health
if ($h -ne '200') {
    $st = Get-Service backend8147 -ErrorAction SilentlyContinue
    if ($st -and $st.Status -ne 'Running') {
        & sc.exe start backend8147 | Out-Null
        Info "sc start fallback (state was: $($st.Status))"
        Start-Sleep -Seconds 15
    }
    $h = & curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/decision/health
}
Info "/api/decision/health: $h"
if ($h -ne '200') { Write-Host 'PAYTIER-DEPLOY-FAIL health'; exit 1 }

Step '[3/3] E2E'
$pub = & curl.exe -s -o NUL -w '%{http_code}' -m 10 http://127.0.0.1/api/v1/dramas
Info "nginx dramas: $pub"
if ($pub -ne '200') { Write-Host 'PAYTIER-DEPLOY-FAIL nginx'; exit 1 }
# free_episodes 字段已接线: 对首个在线剧发越界值 → 409(校验生效, 零状态变更)
$dramas = & curl.exe -s -m 10 http://127.0.0.1:8000/api/v1/dramas | ConvertFrom-Json
$first = $dramas.data | Select-Object -First 1
if ($first) {
    $body = '{"free_episodes": 99999}'
    $code = & curl.exe -s -o NUL -w '%{http_code}' -m 10 -X POST -H "x-role: admin" -H "Content-Type: application/json" -d $body "http://127.0.0.1:8000/api/v1/dramas/$($first.drama_id)/release"
    Info ("release free_episodes=99999 on $($first.drama_id): $code (expect 409)")
    if ($code -ne '409') { Write-Host 'PAYTIER-DEPLOY-FAIL free-field'; exit 1 }
}
Write-Host 'PAYTIER-DEPLOY-DONE'
