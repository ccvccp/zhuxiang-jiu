﻿﻿﻿# 心境·酒签后端部署(4 文件 -> C:\backend, nssm 重启 backend8147, E2E 验证)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }

$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/moodwine/moodwine-api-20261018.tgz'
$TGZ = 'C:\tmp8147\moodwine-api-20261018.tgz'
$BAK = 'C:\tmp8147\backend-bak-moodwine.tgz'
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/4] 备份将被覆盖的 3 个存量文件'
Push-Location C:\backend
try { tar -czf $BAK routes/__init__.py core/auth_middleware.py main.py } finally { Pop-Location }
Info ('备份: ' + [math]::Round((Get-Item $BAK).Length / 1KB, 0) + 'KB')

Step '[2/4] 下载并覆盖(4 文件)'
Invoke-WebRequest $OSS -OutFile $TGZ -UseBasicParsing
Info ('下载: ' + [math]::Round((Get-Item $TGZ).Length / 1KB, 0) + 'KB')
Push-Location C:\backend
try { tar -xzf $TGZ -C C:\backend } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Info '!! 解压失败'; exit 1 }
Info ('mood_routes.py: ' + (Get-Item C:\backend\routes\mood_routes.py).Length + 'B; main.py: ' + (Get-Item C:\backend\main.py).Length + 'B')

Step '[3/4] 重启后端服务(nssm backend8147)'
cmd /c "C:\nginx\nssm.exe restart backend8147"
Start-Sleep -Seconds 12
$code = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/decision/health
Info ('/api/decision/health: ' + $code)
if ($code -ne '200') {
    Info '!! 后端未就绪, stderr 尾部:'
    Get-Content C:\backend\logs\service-err.log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
    exit 1
}

Step '[4/4] 游客 E2E(无鉴权打 /api/mood/wine)'
try {
    $q = [Text.Encoding]::UTF8.GetBytes('{"mood":"今晚想一个人小酌放松，帮我推荐一款适合的竹奕酒？"}')
    $r = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/mood/wine' -ContentType 'application/json' -Body $q -TimeoutSec 30
    $d = $r.data
    Info ('reply 长度: ' + $d.reply.Length + ' / mode: ' + $d.mode + ' / confidence: ' + $d.confidence)
    $r2 = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1/api/mood/wine' -ContentType 'application/json' -Body $q -TimeoutSec 30
    Info ('nginx 链路 reply 长度: ' + $r2.data.reply.Length)
    if (-not $d.reply -or -not $r2.data.reply) { Info '!! reply 为空'; exit 1 }
} catch {
    Info ('!! E2E 异常: ' + $_.Exception.Message)
    exit 1
}
Write-Host 'MOODWINE-API-DONE'
