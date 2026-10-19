# 金刚区 v6 部署(球体纯CSS盒+字符绝对定位钉球心, 经云助手执行)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/kk-v6-20261019.tgz'
$TGZ = 'C:\tmp8147\kk-v6-20261019.tgz'
$BAK = 'C:\tmp8147\www-dist-bak-kk-v6-20261019.tgz'
$DIST = 'C:\www\dist'
$MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49'
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Write-Host '==> [1/3] 备份'
Push-Location $DIST
try { cmd /c "tar -czf $BAK index.html css/style.css 2>nul" } finally { Pop-Location }
Write-Host ('    备份: ' + [math]::Round((Get-Item $BAK).Length / 1KB, 0) + 'KB')

Write-Host '==> [2/3] 下载并解压覆盖'
Invoke-WebRequest $OSS -OutFile $TGZ -UseBasicParsing
Push-Location $DIST
try { cmd /c "tar -xzf `"$TGZ`" -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '    !! 解压失败'; exit 1 }
$idx = [IO.File]::ReadAllText("$DIST\index.html")
$css = [IO.File]::ReadAllText("$DIST\css\style.css")
Write-Host ('    html span 包裹: ' + ($idx -match 'class=\\"icon\\"><span>') + '; css v: ' + ([regex]::Match($idx, 'style\.css\?v=(\d+)').Groups[1].Value))
Write-Host ('    css 绝对定位规则: ' + ($css -match '\.service-entry \.icon span') + '; translate: ' + ($css -match 'translate\(-50%, -50%\)'));

Write-Host '==> [3/3] 手机 UA 验证'
$mCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
$cssCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' 'http://127.0.0.1/css/style.css?v=20261027'
$mb = curl.exe -s -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
Write-Host ('    mobile /: ' + $mCode + ' 含span包裹: ' + ($mb | Select-String 'class=\"icon\"><span>' -Quiet) + '; css v20261027: ' + $cssCode)
if ($mCode -ne '200' -or $cssCode -ne '200') { Write-Host '    !! 验证未全过'; exit 1 }
Write-Host 'KK-V6-DEPLOY-DONE'
