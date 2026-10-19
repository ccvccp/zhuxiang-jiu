# 金刚区图标 v3 部署(inline SVG 内联进 index.html, 经云助手执行)
# 流程: 备份 -> OSS 下载 -> 解压覆盖 -> 手机 UA + PC UA 双路验证
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }

$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/kk-inline-20261019.tgz'
$TGZ = 'C:\tmp8147\kk-inline-20261019.tgz'
$BAK = 'C:\tmp8147\www-dist-bak-kk-inline-20261019.tgz'
$DIST = 'C:\www\dist'
$MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49'
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/4] 备份 index.html / style.css'
Push-Location $DIST
try { cmd /c "tar -czf $BAK index.html css/style.css 2>nul" } finally { Pop-Location }
Info ('备份: ' + [math]::Round((Get-Item $BAK).Length / 1KB, 0) + 'KB')

Step '[2/4] 下载并解压覆盖'
Invoke-WebRequest $OSS -OutFile $TGZ -UseBasicParsing
Push-Location $DIST
try { cmd /c "tar -xzf `"$TGZ`" -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Info '!! 解压失败'; exit 1 }
$idx = [IO.File]::ReadAllText("$DIST\index.html")
Info ('内联 svg 数(应8): ' + ([regex]::Matches($idx, '<svg xmlns')).Count + '; 残留 img 引用(应0): ' + ([regex]::Matches($idx, 'images/icons/kk-')).Count + '; css v: ' + ([regex]::Match($idx, 'style\.css\?v=(\d+)').Groups[1].Value))

Step '[3/4] 手机 UA 验证(微信 X5 UA 走真实移动路径)'
$mb = curl.exe -s -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
$mCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
Info ('mobile /: ' + $mCode + ' 含KK_ICON: ' + ($mb | Select-String 'KK_ICON' -Quiet) + ' 含内联svg: ' + ($mb | Select-String '<svg xmlns' -Quiet) + ' 长度: ' + $mb.Length)

Step '[4/4] 旧外链 svg 的 MIME 取证(诊断 v2 为何真机不显)'
$mime = curl.exe -s -o NUL -w '%{content_type}' -m 8 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/images/icons/kk-products.svg
$icode = curl.exe -s -o NUL -w '%{http_code}' -m 8 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/images/icons/kk-products.svg
Info ('外链 svg: ' + $icode + ' Content-Type: ' + $mime)
$pc = curl.exe -s -o NUL -w '%{http_code}' -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/official.html
Info ('official.html(PC): ' + $pc)
if ($mCode -ne '200' -or -not ($mb | Select-String 'KK_ICON' -Quiet)) { Info '!! 验证未全过'; exit 1 }
Write-Host 'KK-INLINE-DEPLOY-DONE'
