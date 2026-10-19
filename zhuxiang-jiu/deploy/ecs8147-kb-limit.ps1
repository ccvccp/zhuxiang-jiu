﻿﻿﻿# kb57 list_entries 截断修复部署(1 文件, restart+sc 兜底, 全量计数验证)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/3] 备份 + 下载覆盖'
if (Test-Path C:\backend\repositories\knowledge_repository.py) {
    Copy-Item C:\backend\repositories\knowledge_repository.py C:\tmp8147\knowledge_repository.py.bak-20261017-limit -Force
}
$hc = & curl.exe -s -o C:\tmp8147\kb-limit.tgz -w '%{http_code}' "https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/kb-limit-20261017.tgz"
if ($hc -ne '200') { Write-Host "KBLIMIT-DEPLOY-FAIL download http=$hc"; exit 1 }
New-Item -ItemType Directory -Force C:\tmp8147\kbl-extract | Out-Null
tar -xzf C:\tmp8147\kb-limit.tgz -C C:\tmp8147\kbl-extract
Copy-Item C:\tmp8147\kbl-extract\knowledge_repository.py C:\backend\repositories\ -Force
Info ("repo: " + (Get-Item C:\backend\repositories\knowledge_repository.py).Length + "B")

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
if ($h -ne '200') { Write-Host 'KBLIMIT-DEPLOY-FAIL health'; exit 1 }

Step '[3/3] E2E(全量计数)'
$py = @'
import asyncio, os, sys, base64
os.environ['STORE_MODE'] = 'redis'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6379/0?protocol=2'
sys.path.insert(0, r'C:\backend')
from repositories.knowledge_repository import KnowledgeRepository
async def main():
    repo = KnowledgeRepository()
    full = await repo.list_entries(limit=0)
    pub = await repo.list_entries(status='published', limit=0)
    d100 = await repo.list_entries()
    s = await repo.stats()
    out = [f'full={len(full)}', f'published={len(pub)}',
           f'default100={len(d100)}', f'stats.total={s["totalEntries"]}',
           f'byStatus={s["byStatus"]}']
    print(base64.b64encode(chr(10).join(out).encode('utf-8')).decode())
asyncio.run(main())
'@
$py | Out-File -FilePath C:\tmp8147\kbl_check.py -Encoding utf8
$b64 = & C:\Python312\python.exe C:\tmp8147\kbl_check.py 2>&1 | Select-Object -Last 1
$report = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
$report.Split([char]10) | ForEach-Object { Info $_ }
$pubN = [int]($report -replace '(?s).*published=(\d+).*', '$1')
if ($pubN -lt 124) { Write-Host "KBLIMIT-DEPLOY-FAIL published=$pubN (expect >=124)"; exit 1 }
Write-Host 'KBLIMIT-DEPLOY-DONE'
