# 紧急回滚: 恢复到 v5+字号调整(用户确认 PC 正常)的状态
$ErrorActionPreference = 'Stop'
$BAK = 'C:\tmp8147\www-dist-bak-kk-fs-20261019.tgz'
$DIST = 'C:\www\dist'
$MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49'

if (-not (Test-Path $BAK)) { Write-Host '!! 备份不存在'; exit 1 }
Write-Host '==> 回滚前再备份当前(v6)状态'
cmd /c "tar -czf C:\tmp8147\www-dist-bak-kk-v6-rollback-snapshot.tgz -C $DIST index.html css/style.css 2>nul"

Write-Host '==> 解压恢复 v5+fs 备份'
Push-Location $DIST
try { cmd /c "tar -xzf `"$BAK`" -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 恢复失败'; exit 1 }
$idx = [IO.File]::ReadAllText("$DIST\index.html")
$css = [IO.File]::ReadAllText("$DIST\css\style.css")
Write-Host ('    css v: ' + ([regex]::Match($idx, 'style\.css\?v=(\d+)').Groups[1].Value) + ' (应 20261026)')
Write-Host ('    html 无 span 包裹(应 True): ' + (-not ($idx -match 'class=\"icon\"\>\<span\>')))
Write-Host ('    css 无 icon span 规则(应 True): ' + (-not ($css -match '\.service-entry \.icon span')))
Write-Host ('    css 含 line-height 48px(应 True): ' + ($css -match 'line-height:\s*48px'))

Write-Host '==> 验证'
$mCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
$cssCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' 'http://127.0.0.1/css/style.css?v=20261026'
Write-Host ('    /: ' + $mCode + '  css v20261026: ' + $cssCode)
if ($mCode -ne '200' -or $cssCode -ne '200') { Write-Host '!! 验证未过'; exit 1 }
Write-Host 'ROLLBACK-DONE: PC-confirmed v5+fs state restored'
