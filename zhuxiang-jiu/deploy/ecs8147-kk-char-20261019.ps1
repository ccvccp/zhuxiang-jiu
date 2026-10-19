# 金刚区图标 v5 部署(圆球+汉字: 全/同/活/溯/会/购/客/品, 经云助手执行)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }

$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/kk-char-20261019.tgz'
$TGZ = 'C:\tmp8147\kk-char-20261019.tgz'
$BAK = 'C:\tmp8147\www-dist-bak-kk-char-20261019.tgz'
$DIST = 'C:\www\dist'
$MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49'
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/3] 备份'
Push-Location $DIST
try { cmd /c "tar -czf $BAK index.html css/style.css 2>nul" } finally { Pop-Location }
Info ('备份: ' + [math]::Round((Get-Item $BAK).Length / 1KB, 0) + 'KB')

Step '[2/3] 下载并解压覆盖'
Invoke-WebRequest $OSS -OutFile $TGZ -UseBasicParsing
Push-Location $DIST
try { cmd /c "tar -xzf `"$TGZ`" -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Info '!! 解压失败'; exit 1 }
$idx = [IO.File]::ReadAllText("$DIST\index.html")
$css = [IO.File]::ReadAllText("$DIST\css\style.css")
$kki = [regex]::Match($idx, "KK_ICON\s*=\s*\{([^}]+)\}").Groups[1].Value
Info ('KK_ICON 内容: ' + ($kki -replace '\s+', ' ').Trim())
Info ('css v: ' + ([regex]::Match($idx, 'style\.css\?v=(\d+)').Groups[1].Value) + '; css 含球内字规则(line-height:48px): ' + ($css -match 'line-height:\s*48px'))

Step '[3/3] 手机 UA 验证'
$mb = curl.exe -s -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
$mCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
$cssCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' 'http://127.0.0.1/css/style.css?v=20261025'
Info ('mobile /: ' + $mCode + ' 含KK_ICON: ' + ($mb | Select-String 'KK_ICON' -Quiet) + '; css v20261025: ' + $cssCode)
if ($mCode -ne '200' -or -not ($mb | Select-String 'KK_ICON' -Quiet) -or $cssCode -ne '200' -or -not ($css -match 'line-height:\s*48px')) { Info '!! 验证未全过'; exit 1 }
Write-Host 'KK-CHAR-DEPLOY-DONE'
