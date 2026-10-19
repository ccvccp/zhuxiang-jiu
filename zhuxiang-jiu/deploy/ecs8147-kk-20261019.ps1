# 金刚区SVG图标部署(图片接口退化为占位图后改SVG, 经云助手执行)
# 流程: 备份 -> OSS 下载 -> 解压覆盖 C:\www\dist -> 本机验证(127.0.0.1)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }

$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/kk-icons-20261019.tgz'
$TGZ = 'C:\tmp8147\kk-icons-20261019.tgz'
$BAK = 'C:\tmp8147\www-dist-bak-kk-20261019.tgz'
$DIST = 'C:\www\dist'
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/4] 备份 index.html 与旧图标目录'
Push-Location $DIST
try { cmd /c "tar -czf $BAK index.html images/icons 2>nul" } finally { Pop-Location }
Info ('备份: ' + [math]::Round((Get-Item $BAK).Length / 1KB, 0) + 'KB -> ' + $BAK)

Step '[2/4] 下载部署包(OSS)'
Invoke-WebRequest $OSS -OutFile $TGZ -UseBasicParsing
Info ('下载: ' + [math]::Round((Get-Item $TGZ).Length / 1KB, 0) + 'KB')

Step '[3/4] 解压覆盖 C:\www\dist'
Push-Location $DIST
try { cmd /c "tar -xzf `"$TGZ`" -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Info '!! 解压失败'; exit 1 }
$svgs = @(Get-ChildItem "$DIST\images\icons\kk-*.svg")
Info ('kk svg 数(应8): ' + $svgs.Count + '; 首个大小: ' + $svgs[0].Length + 'B')
$idx = [IO.File]::ReadAllText("$DIST\index.html")
Info ('index 引用 svg 数(应8): ' + ([regex]::Matches($idx, 'kk-\w+\.svg')).Count + '; 残留 jpg(应0): ' + ([regex]::Matches($idx, 'kk-\w+\.jpg')).Count)

Step '[4/4] 本机验证(nginx 静态直出)'
Start-Sleep -Seconds 1
$code1 = curl.exe -s -o NUL -w '%{http_code}' -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/
$code2 = curl.exe -s -o NUL -w '%{http_code}' -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/images/icons/kk-products.svg
$body = curl.exe -s -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/
$hasSvg = ($body | Select-String 'kk-products\.svg' -Quiet)
Info ("index: $code1  icon: $code2  首页含svg引用: $hasSvg")
if ($code1 -ne '200' -or $code2 -ne '200' -or -not $hasSvg) { Info '!! 验证未全过'; exit 1 }
Write-Host 'KK-ICONS-DEPLOY-DONE'
