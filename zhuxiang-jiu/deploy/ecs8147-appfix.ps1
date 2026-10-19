# ECS app.html 官网入口修正部署
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/appfix-20261019.tgz' -OutFile C:\tmp8147\appfix.tgz -UseBasicParsing
Push-Location $DIST
try { cmd /c "tar -xzf C:\tmp8147\appfix.tgz -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
$c = [IO.File]::ReadAllText("$DIST\app.html")
Write-Host ('app.html 大小: ' + (Get-Item "$DIST\app.html").Length + 'B; officialEntry 拦截器: ' + ($c -match 'officialEntry'))
$code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' http://127.0.0.1/app.html
Write-Host ('app.html: ' + $code)
if ($code -ne '200' -or -not ($c -match 'officialEntry')) { Write-Host '!! 未过'; exit 1 }
Write-Host 'APPFIX-ECS-DONE'
