# 金刚区 v7 重生版部署到国内 ECS(与海外服务器对齐)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/kk-v7-20261019.tgz'
$TGZ = 'C:\tmp8147\kk-v7-20261019.tgz'
$DIST = 'C:\www\dist'
$MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49'
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Write-Host '==> [1/3] 备份'
cmd /c "tar -czf C:\tmp8147\www-dist-bak-kk-v7-20261019.tgz -C $DIST index.html official.html css/style.css"

Write-Host '==> [2/3] 下载解压覆盖'
Invoke-WebRequest $OSS -OutFile $TGZ -UseBasicParsing
Push-Location $DIST
try { cmd /c "tar -xzf `"$TGZ`" -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
foreach ($f in @('index.html', 'official.html')) {
    $c = [IO.File]::ReadAllText("$DIST\$f")
    Write-Host ('    ' + $f + ': kk-ball=' + ([regex]::Matches($c, 'kk-ball').Count) + ' img模板残留=' + ([regex]::Matches($c, [regex]::Escape('images/icons/${k.img}')).Count) + ' cssV=' + ([regex]::Match($c, 'style\.css\?v=(\d+)').Groups[1].Value))
}

Write-Host '==> [3/3] 双 UA 验证'
$mCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/index.html
$pCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' http://127.0.0.1/official.html
$mi = curl.exe -s -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/index.html
$pi = curl.exe -s -m 10 -H 'Host: zxjiu.com' http://127.0.0.1/official.html
Write-Host ('    index(mobile): ' + $mCode + ' kkball: ' + ($mi | Select-String 'kk-ball' -Quiet) + '; official(PC): ' + $pCode + ' kkball: ' + ($pi | Select-String 'kk-ball' -Quiet))
if ($mCode -ne '200' -or $pCode -ne '200' -or -not ($mi | Select-String 'kk-ball' -Quiet) -or -not ($pi | Select-String 'kk-ball' -Quiet)) { Write-Host '!! 验证未全过'; exit 1 }
Write-Host 'KK-V7-ECS-DEPLOY-DONE'
