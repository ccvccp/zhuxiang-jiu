﻿﻿﻿# 76/84 号边更边锁+时长双档部署(3 文件 -> C:\backend, nssm 重启, E2E)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/3] 备份 + 下载覆盖'
Push-Location C:\backend
$stamp = '20261017-rr'
foreach ($f in @('services\drama76_series_service.py', 'services\drama84_script_service.py', 'routes\drama76_routes.py')) {
    $dst = Join-Path 'C:\backend' $f
    if (Test-Path $dst) { Copy-Item $dst "C:\tmp8147\$(Split-Path $f -Leaf).bak-$stamp" -Force }
}
Pop-Location
$hc = & curl.exe -s -o C:\tmp8147\drama-rr.tgz -w '%{http_code}' "https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/drama-rr-20261017.tgz"
if ($hc -ne '200') { Write-Host "DRAMA-DEPLOY-FAIL download http=$hc"; exit 1 }
New-Item -ItemType Directory -Force C:\tmp8147\drama-rr-extract | Out-Null
tar -xzf C:\tmp8147\drama-rr.tgz -C C:\tmp8147\drama-rr-extract
Copy-Item C:\tmp8147\drama-rr-extract\drama76_series_service.py C:\backend\services\ -Force
Copy-Item C:\tmp8147\drama-rr-extract\drama84_script_service.py C:\backend\services\ -Force
Copy-Item C:\tmp8147\drama-rr-extract\drama76_routes.py C:\backend\routes\ -Force
$s1 = (Get-Item C:\backend\services\drama76_series_service.py).Length
$s2 = (Get-Item C:\backend\services\drama84_script_service.py).Length
$s3 = (Get-Item C:\backend\routes\drama76_routes.py).Length
Info "svc76: ${s1}B; svc84: ${s2}B; routes76: ${s3}B"

Step '[2/3] 重启后端(nssm restart + sc start 兜底)'
cmd /c "C:\nginx\nssm.exe restart backend8147" | Out-Null
Start-Sleep -Seconds 12
$h = & curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/decision/health
if ($h -ne '200') {
    # 兜底: nssm restart 后偶发 Stopped 不自愈(2026-10-17 实证) -> sc start 手动拉起
    $st = Get-Service backend8147 -ErrorAction SilentlyContinue
    if ($st -and $st.Status -ne 'Running') {
        & sc.exe start backend8147 | Out-Null
        Info "sc start fallback (state was: $($st.Status))"
        Start-Sleep -Seconds 15
    }
    $h = & curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/decision/health
}
Info "/api/decision/health: $h"
if ($h -ne '200') { Write-Host 'DRAMA-DEPLOY-FAIL health'; exit 1 }

Step '[3/3] E2E(边更边锁面)'
# a. 剧集清单/详情(公开 GET)不受影响
$ls = & curl.exe -s http://127.0.0.1:8000/api/v1/dramas
$cnt = if ($ls -match '"data":\[') { ($ls | Select-String -Pattern 'drama_id' -AllMatches).Matches.Count } else { -1 }
Info "dramas list: code_ok=$($ls -match '\"code\":0') drama_id_hits=$cnt"
# b. release 端点存在且受保护(无 admin 头 -> 403; strict 无 Bearer -> 401)
$code = & curl.exe -s -o NUL -w '%{http_code}' -X POST http://127.0.0.1:8000/api/v1/dramas/drama_x/release -H 'Content-Type: application/json' -d '{}'
Info "release no-auth: $code (401/403 均为受保护预期)"
# c. 84 号档位函数直调(进程内)
$py = @'
import sys; sys.path.insert(0, r'C:\backend')
from services.drama84_script_service import episode_length_plan
plan = [episode_length_plan(i) for i in range(1, 10)]
print('PLAN', ','.join(plan))
'@
$py | Out-File -FilePath C:\tmp8147\plan_check.py -Encoding utf8
$planOut = & python C:\tmp8147\plan_check.py 2>&1 | Select-String 'PLAN'
Info "84 length plan(1-9): $planOut"
# d. nginx 公网链路
$pub = & curl.exe -s -o NUL -w '%{http_code}' http://127.0.0.1/api/v1/dramas
Info "nginx dramas: $pub"
if ($planOut -notmatch 'long,short,short,short,long,short,short,short,long') { Write-Host 'DRAMA-DEPLOY-FAIL plan'; exit 1 }
Write-Host 'DRAMA-DEPLOY-DONE'
