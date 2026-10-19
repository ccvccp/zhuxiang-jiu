# ECS app.html 官网入口修正v2(固定回商城首页)
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/appfix2-20261019.tgz' -OutFile C:\tmp8147\appfix2.tgz -UseBasicParsing
Push-Location $DIST
try { cmd /c "tar -xzf C:\tmp8147\appfix2.tgz -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
$c = [IO.File]::ReadAllText("$DIST\app.html")
Write-Host ('v2 修正(/index.html): ' + ($c -match "href = '/index.html'"))
$code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' http://127.0.0.1/app.html
Write-Host ('app.html: ' + $code)
if ($code -ne '200' -or -not ($c -match "href = '/index.html'")) { Write-Host '!! 未过'; exit 1 }
Write-Host 'APPFIX2-ECS-DONE'
