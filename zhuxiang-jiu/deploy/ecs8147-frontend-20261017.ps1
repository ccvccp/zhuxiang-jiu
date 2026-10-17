# 前端四批次再部署(国内 ECS 生效层, 经云助手执行)
# 流程: 备份 -> OSS 下载 -> 解压覆盖 C:\www\dist -> v号归一(UTF8 无BOM) -> 本机验证
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host "==> $m" }
function Info($m) { Write-Host "    $m" }

$OSS = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/frontend-20261017.tgz'
$TGZ = 'C:\tmp8147\frontend-20261017.tgz'
$BAK = 'C:\tmp8147\www-dist-bak-frontend-20261017.tgz'
$DIST = 'C:\www\dist'
New-Item -ItemType Directory -Force C:\tmp8147 | Out-Null

Step '[1/5] 备份将被覆盖的静态文件'
Push-Location $DIST
try { cmd /c "tar -czf $BAK *.html css/style.css css/module-test.css js/main.js 2>nul" } finally { Pop-Location }
Info ('备份: ' + [math]::Round((Get-Item $BAK).Length / 1KB, 0) + 'KB -> ' + $BAK)

Step '[2/5] 下载部署包(OSS)'
Invoke-WebRequest $OSS -OutFile $TGZ -UseBasicParsing
Info ('下载: ' + [math]::Round((Get-Item $TGZ).Length / 1KB, 0) + 'KB')

Step '[3/5] 解压覆盖 C:\www\dist'
Push-Location $DIST
try { cmd /c "tar -xzf `"$TGZ`" -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Info '!! 解压失败'; exit 1 }
Info ('products-live.js: ' + (Get-Item "$DIST\js\products-live.js").Length + 'B; official.html: ' + (Get-Item "$DIST\official.html").Length + 'B')

Step '[4/5] v号归一(style.css / main.js -> v=20261017, UTF8 无BOM 写回)'
$utf8 = [Text.UTF8Encoding]::new($false)
$n = 0
Get-ChildItem "$DIST\*.html" | ForEach-Object {
    $c = [IO.File]::ReadAllText($_.FullName)
    $orig = $c
    $c = $c -replace '(css/style\.css)\?v=\d+', '`$1?v=20261017'
    $c = $c -replace 'css/style\.css(")', 'css/style.css?v=20261017$1'
    $c = $c -replace '(js/main\.js)\?v=\d+', '`$1?v=20261017'
    $c = $c -replace 'js/main\.js(")', 'js/main.js?v=20261017$1'
    if ($c -ne $orig) { [IO.File]::WriteAllText($_.FullName, $c, $utf8); $n++ }
}
Info ("改写 ${n} 个 html")
$dblV = @(Select-String -Path "$DIST\*.html" -Pattern 'v=20261017\?v=' -List -ErrorAction SilentlyContinue)
Info ('双v污染(应0): ' + $dblV.Count)
$bomFiles = @(Get-ChildItem "$DIST\*.html" | Where-Object { [IO.File]::ReadAllBytes($_.FullName)[0] -eq 0xEF })
Info ('BOM 检查(应0): ' + $bomFiles.Count)

Step '[5/5] 本机验证(nginx 静态直出)'
Start-Sleep -Seconds 1
$code1 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1/official.html
$code2 = curl.exe -s -o NUL -w '%{http_code}' -m 8 http://127.0.0.1/js/products-live.js
$cssHit = (curl.exe -s -m 8 'http://127.0.0.1/css/style.css?v=20261017' | Select-String '8a8a8a' -Quiet)
$vHit = (curl.exe -s -m 8 http://127.0.0.1/official.html | Select-String 'v=20261017' -Quiet)
Info ("official: $code1  products-live.js: $code2  style含8a8a8a: $cssHit  official含v号: $vHit")
if ($code1 -ne '200' -or $code2 -ne '200' -or -not $vHit) { Info '!! 验证未全过'; exit 1 }
Write-Host 'FRONTEND-DEPLOY-DONE'
