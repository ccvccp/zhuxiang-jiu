﻿﻿﻿# 84 号命名句式 prompt 升级部署(1 文件 -> C:\backend, restart+sc 兜底, E2E)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/3] 备份 + 下载覆盖'
if (Test-Path C:\backend\services\drama84_script_service.py) {
    Copy-Item C:\backend\services\drama84_script_service.py C:\tmp8147\drama84_script_service.py.bak-20261017-naming -Force
}
$hc = & curl.exe -s -o C:\tmp8147\drama84-naming.tgz -w '%{http_code}' "https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/drama84-naming-20261017.tgz"
if ($hc -ne '200') { Write-Host "NAMING-DEPLOY-FAIL download http=$hc"; exit 1 }
New-Item -ItemType Directory -Force C:\tmp8147\d84-naming | Out-Null
tar -xzf C:\tmp8147\drama84-naming.tgz -C C:\tmp8147\d84-naming
Copy-Item C:\tmp8147\d84-naming\drama84_script_service.py C:\backend\services\ -Force
Info ("svc84: " + (Get-Item C:\backend\services\drama84_script_service.py).Length + "B")

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
if ($h -ne '200') { Write-Host 'NAMING-DEPLOY-FAIL health'; exit 1 }

Step '[3/3] E2E'
$pub = & curl.exe -s -o NUL -w '%{http_code}' -m 10 http://127.0.0.1/api/v1/dramas
Info "nginx dramas: $pub"
if ($pub -ne '200') { Write-Host 'NAMING-DEPLOY-FAIL nginx'; exit 1 }
Write-Host 'NAMING-DEPLOY-DONE'
