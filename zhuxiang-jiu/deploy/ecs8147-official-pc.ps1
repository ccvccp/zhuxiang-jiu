# ECS official.html PC 功能宫格部署
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/official-pc-20261019.tgz' -OutFile C:\tmp8147\official-pc.tgz -UseBasicParsing
Push-Location $DIST
try { cmd /c "tar -xzf C:\tmp8147\official-pc.tgz -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
$c = [IO.File]::ReadAllText("$DIST\official.html")
Write-Host ('kk-ball-sm: ' + ([regex]::Matches($c, 'kk-ball-sm').Count) + '; 商城全部: ' + ($c -match '商城全部') + '; 小竹语音: ' + ($c -match 'xiaozhu-voice'))
$code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' http://127.0.0.1/official.html
Write-Host ('official.html: ' + $code)
if ($code -ne '200' -or -not ($c -match '商城全部')) { Write-Host '!! 未过'; exit 1 }
Write-Host 'OFFICIAL-PC-ECS-DONE'
