# ECS 返回钮批量注入部署
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
New-Item -ItemType Directory -Force C:\tmp8147\backbtn-bak-20261019 | Out-Null
foreach ($f in @('zjian.html', 'alliance-mall.html', 'login.html')) { if (Test-Path "$DIST\$f") { Copy-Item "$DIST\$f" C:\tmp8147\backbtn-bak-20261019\ } }
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/backbtn-public-20261019.tgz' -OutFile C:\tmp8147\bb1.tgz -UseBasicParsing
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/backbtn-zx-20261019.tgz' -OutFile C:\tmp8147\bb2.tgz -UseBasicParsing
Push-Location $DIST
try {
    cmd /c "tar -xzf C:\tmp8147\bb1.tgz -C $DIST 2>&1"
    cmd /c "tar -xzf C:\tmp8147\bb2.tgz -C $DIST 2>&1"
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
$n = @(Get-ChildItem $DIST -Filter *.html | Where-Object { [IO.File]::ReadAllText($_.FullName) -match 'globalBackBtn' }).Count
Write-Host ('含返回钮页面数: ' + $n)
Write-Host ('zjian.html: ' + ([regex]::Matches([IO.File]::ReadAllText("$DIST\zjian.html"), 'globalBackBtn').Count))
$code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' http://127.0.0.1/zjian.html
Write-Host ('zjian: ' + $code)
if ($code -ne '200' -or $n -lt 50) { Write-Host '!! 验证未过'; exit 1 }
Write-Host 'BACKBTN-ECS-DONE'
