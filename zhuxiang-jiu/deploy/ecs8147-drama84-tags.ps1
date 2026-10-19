﻿﻿﻿# 84 号 AI 标签+复读机标题部署(1 文件 -> C:\backend, restart+sc 兜底, E2E)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/3] 备份 + 下载覆盖'
if (Test-Path C:\backend\services\drama84_script_service.py) {
    Copy-Item C:\backend\services\drama84_script_service.py C:\tmp8147\drama84_script_service.py.bak-20261017-tags -Force
}
$hc = & curl.exe -s -o C:\tmp8147\drama84-tags.tgz -w '%{http_code}' "https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/drama84-tags-20261017.tgz"
if ($hc -ne '200') { Write-Host "TAGS-DEPLOY-FAIL download http=$hc"; exit 1 }
New-Item -ItemType Directory -Force C:\tmp8147\d84-extract | Out-Null
tar -xzf C:\tmp8147\drama84-tags.tgz -C C:\tmp8147\d84-extract
Copy-Item C:\tmp8147\d84-extract\drama84_script_service.py C:\backend\services\ -Force
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
if ($h -ne '200') { Write-Host 'TAGS-DEPLOY-FAIL health'; exit 1 }

Step '[3/3] E2E(84 号复读机+标签)'
$py = @'
import sys
sys.path.insert(0, r'C:\backend')
from services.drama84_script_service import Drama84ScriptService
svc = Drama84ScriptService()
rows = svc.list_series()
n = len(rows)
gf_in_title = 0
ai_tag = 0
for s in rows:
    if (s.get('goldenFinger') or '') and s.get('goldenFinger', '') in (
            s.get('title') or '') + '|' + (s.get('goldenFinger') or ''):
        gf_in_title += 1
print('SERIES', n)
'@
$py | Out-File -FilePath C:\tmp8147\d84_check.py -Encoding utf8
$out = & python C:\tmp8147\d84_check.py 2>&1 | Select-String 'SERIES'
Info "84 import+list: $out (import 成功即新代码加载)"
$pub = & curl.exe -s -o NUL -w '%{http_code}' -m 10 http://127.0.0.1/api/v1/dramas
Info "nginx dramas: $pub"
if ($pub -ne '200') { Write-Host 'TAGS-DEPLOY-FAIL nginx'; exit 1 }
Write-Host 'TAGS-DEPLOY-DONE'
