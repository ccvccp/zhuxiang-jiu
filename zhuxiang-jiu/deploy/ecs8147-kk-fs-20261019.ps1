# 金刚区球内字大小调整部署(手机 26px / 桌面 30px, 经云助手执行)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/kk-fs-20261019.tgz'
$TGZ = 'C:\tmp8147\kk-fs-20261019.tgz'
$BAK = 'C:\tmp8147\www-dist-bak-kk-fs-20261019.tgz'
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
Write-Host ('    css v: ' + ([regex]::Match($idx, 'style\.css\?v=(\d+)').Groups[1].Value) + '; 26px 规则: ' + ($css -match 'font-size:\s*26px') + '; 30px 规则: ' + ($css -match 'font-size:\s*30px'))

Write-Host '==> [3/3] 手机 UA 验证'
$mCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
$cssCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' 'http://127.0.0.1/css/style.css?v=20261026'
Write-Host ('    mobile /: ' + $mCode + '; css v20261026: ' + $cssCode)
if ($mCode -ne '200' -or $cssCode -ne '200' -or -not ($css -match 'font-size:\s*26px')) { Write-Host '    !! 验证未全过'; exit 1 }
Write-Host 'KK-FS-DEPLOY-DONE'
