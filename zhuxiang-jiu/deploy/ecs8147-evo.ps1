# 进化观测面部署(9 文件 -> C:\backend, nssm 重启, 自检事件+三 API E2E)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/4] 备份 + 下载覆盖'
Push-Location C:\backend
try { tar -czf C:\tmp8147\backend-bak-evo.tgz routes/__init__.py core/auth_middleware.py main.py services/agent75_evolution_service.py services/voice82_evolution_service.py services/knowledge_service.py services/ai_learning_scheduler.py } finally { Pop-Location }
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/moodwine/evo-20261018.tgz' -OutFile C:\tmp8147\evo.tgz -UseBasicParsing
Push-Location C:\backend
try { tar -xzf C:\tmp8147\evo.tgz -C C:\backend } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Info '!! 解压失败'; exit 1 }
Info ('chronicle svc: ' + (Get-Item C:\backend\services\evolution_chronicle_service.py).Length + 'B; evolution routes: ' + (Get-Item C:\backend\routes\evolution_routes.py).Length + 'B')

Step '[2/4] 重启后端'
cmd /c "C:\nginx\nssm.exe restart backend8147"
Start-Sleep -Seconds 12
$code = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/decision/health
Info ('/api/decision/health: ' + $code)
if ($code -ne '200') {
    Info '!! 后端未就绪, stderr 尾部:'
    Get-Content C:\backend\logs\service-err.log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
    exit 1
}

Step '[3/4] 注入自检事件(redis 持久态直调 record)'
$py = @'
import asyncio, os, sys
os.environ['STORE_MODE'] = 'redis'; os.environ['LOCK_MODE'] = 'redis'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6379/0?protocol=2'
sys.path.insert(0, r'C:\backend')
from services.evolution_chronicle_service import record
async def main():
    e1 = await record('kb57', 'publish', 'deploy-test', '部署自检事件(编年史上线)')
    e2 = await record('agent75', 'reject', 'deploy-test', '部署自检事件(Elo 负例)')
    print('INJECT', (e1 or {}).get('id'), (e2 or {}).get('id'))
asyncio.run(main())
'@
[IO.File]::WriteAllText('C:\backend\_evo_check.py', $py, (New-Object Text.UTF8Encoding $false))
Push-Location C:\backend
$out = & C:\Python312\python.exe 'C:\backend\_evo_check.py' 2>&1 | Out-String
Pop-Location
Info ($out.Trim())
Remove-Item C:\backend\_evo_check.py -Force -ErrorAction SilentlyContinue
if (-not $out.Contains('INJECT')) { Info '!! 自检事件注入失败'; exit 1 }

Step '[4/4] 三 API E2E(游客无鉴权 GET)'
try {
    $c = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/evolution/chronicle?limit=5' -TimeoutSec 15
    Info ('chronicle: count=' + $c.count + ' 首条 domain=' + $c.data[0].domain + ' type=' + $c.data[0].type)
    $a = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/evolution/aging' -TimeoutSec 15
    Info ('aging: overall=' + $a.data.overall + ' 域数=' + $a.data.domains.Count)
    $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/evolution/arena' -TimeoutSec 15
    Info ('arena: 榜行数=' + $r.count + ' 首行 domain=' + $r.data[0].domain + ' Elo=' + $r.data[0].challengerElo)
    $c2 = Invoke-RestMethod -Uri 'http://127.0.0.1/api/evolution/chronicle?limit=2' -TimeoutSec 15
    Info ('nginx 链路 chronicle: count=' + $c2.count)
    if ($c.count -lt 2 -or $r.count -lt 2) { Info '!! 观测面数据不足'; exit 1 }
} catch {
    Info ('!! E2E 异常: ' + $_.Exception.Message)
    exit 1
}
Write-Host 'EVO-DEPLOY-DONE'
