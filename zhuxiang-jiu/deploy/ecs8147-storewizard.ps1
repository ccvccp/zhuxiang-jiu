# 一句话开店向导部署(后端 4 文件+nssm 重启, 前端 alliance-mall.html, 双 E2E)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/5] 备份'
Push-Location C:\backend
try { tar -czf C:\tmp8147\backend-bak-wizard.tgz routes/__init__.py core/auth_middleware.py main.py } finally { Pop-Location }
Copy-Item C:\www\dist\alliance-mall.html C:\tmp8147\alliance-mall.bak-wizard.html -Force
Info '已备份后端 3 件 + alliance-mall.html'

Step '[2/5] 下载部署包'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/moodwine/storewizard-api-20261018.tgz' -OutFile C:\tmp8147\wizard-api.tgz -UseBasicParsing
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/storewizard-web-20261018.tgz' -OutFile C:\tmp8147\wizard-web.tgz -UseBasicParsing
Info ('api 包: ' + [math]::Round((Get-Item C:\tmp8147\wizard-api.tgz).Length / 1KB, 0) + 'KB / web 包: ' + [math]::Round((Get-Item C:\tmp8147\wizard-web.tgz).Length / 1KB, 0) + 'KB')

Step '[3/5] 后端覆盖 + 重启'
Push-Location C:\backend
try { tar -xzf C:\tmp8147\wizard-api.tgz -C C:\backend } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Info '!! 后端解压失败'; exit 1 }
Info ('store_wizard_routes.py: ' + (Get-Item C:\backend\routes\store_wizard_routes.py).Length + 'B')
cmd /c "C:\nginx\nssm.exe restart backend8147"
Start-Sleep -Seconds 12
$code = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1:8000/api/decision/health
Info ('/api/decision/health: ' + $code)
if ($code -ne '200') {
    Info '!! 后端未就绪, stderr 尾部:'
    Get-Content C:\backend\logs\service-err.log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Info $_ }
    exit 1
}

Step '[4/5] 前端覆盖(alliance-mall.html)'
Push-Location C:\www\dist
try { tar -xzf C:\tmp8147\wizard-web.tgz -C C:\www\dist } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Info '!! 前端解压失败'; exit 1 }
$mall = curl.exe -s -m 8 http://127.0.0.1/alliance-mall.html
$hitWiz = ($mall | Select-String 'wizBox' -Quiet)
$hitApi = ($mall | Select-String '/api/store/wizard' -Quiet)
Info ("alliance-mall 含 wizBox: $hitWiz / 含 wizard api: $hitApi")
if (-not $hitWiz -or -not $hitApi) { Info '!! 前端验证未过'; exit 1 }

Step '[5/5] E2E(游客生成方案, 直连+nginx)'
try {
    $q = [Text.Encoding]::UTF8.GetBytes('{"idea":"我想开一家卖武夷山岩茶的店，走礼盒路线"}')
    $r = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/store/wizard' -ContentType 'application/json' -Body $q -TimeoutSec 20
    $d = $r.data
    Info ('直连: category=' + $d.category + ' / 店名首选=' + $d.shopNames[0] + ' / 选品 ' + $d.picks.Count + ' 条 / 清单 ' + $d.checklist.Count + ' 条')
    $r2 = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1/api/store/wizard' -ContentType 'application/json' -Body $q -TimeoutSec 20
    Info ('nginx 链路: category=' + $r2.data.category + ' / 定位长度 ' + $r2.data.positioning.Length)
    if (-not $d.shopNames -or -not $d.picks) { Info '!! 方案字段缺失'; exit 1 }
} catch {
    Info ('!! E2E 异常: ' + $_.Exception.Message)
    exit 1
}
Write-Host 'STOREWIZARD-DEPLOY-DONE'
