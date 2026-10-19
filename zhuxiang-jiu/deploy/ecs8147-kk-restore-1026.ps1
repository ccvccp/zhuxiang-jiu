# 精确恢复到 v20261026(用户确认 PC 正常的最终状态)
$ErrorActionPreference = 'Stop'
$BAK = 'C:\tmp8147\www-dist-bak-kk-v6-20261019.tgz'   # v6 部署前备份 = v5+fs(20261026)
$DIST = 'C:\www\dist'
$MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49'

Write-Host '==> 解压恢复 v20261026 状态'
Push-Location $DIST
try { cmd /c "tar -xzf `"$BAK`" -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 恢复失败'; exit 1 }
$idx = [IO.File]::ReadAllText("$DIST\index.html")
$css = [IO.File]::ReadAllText("$DIST\css\style.css")
Write-Host ('    css v: ' + ([regex]::Match($idx, 'style\.css\?v=(\d+)').Groups[1].Value) + ' (应 20261026)')
Write-Host ('    26px 规则(应 True): ' + ($css -match 'font-size:\s*26px') + '; 30px 规则(应 True): ' + ($css -match 'font-size:\s*30px'))
Write-Host ('    html 无 span(应 True): ' + (-not ($idx -match 'icon\"\>\<span')))

Write-Host '==> 验证'
$mCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
$cssCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' 'http://127.0.0.1/css/style.css?v=20261026'
Write-Host ('    /: ' + $mCode + '  css v20261026: ' + $cssCode)
if ($mCode -ne '200' -or $cssCode -ne '200') { Write-Host '!! 验证未过'; exit 1 }
Write-Host 'RESTORE-20261026-DONE'
