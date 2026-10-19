# v4 部署校验(修正 PS5 正则兼容)
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
$MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49'
$idx = [IO.File]::ReadAllText("$DIST\index.html")
$css = [IO.File]::ReadAllText("$DIST\css\style.css")
$emojiCount = 0
foreach ($ch in $idx.ToCharArray()) { $cp = [int]$ch; if (($cp -ge 0x1F300 -and $cp -le 0x1FAFF) -or ($cp -ge 0x2600 -and $cp -le 0x27BF)) { $emojiCount++ } }
Write-Host ('html emoji 字符数(应8): ' + $emojiCount)
Write-Host ('残留内联svg(应0): ' + ([regex]::Matches($idx, '<svg xmlns')).Count)
Write-Host ('css v: ' + ([regex]::Match($idx, 'style\.css\?v=(\d+)').Groups[1].Value))
Write-Host ('css 含 32px emoji 规则: ' + ($css -match 'font-size:\s*32px'))
$mb = curl.exe -s -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
$mCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/
$cssCode = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' 'http://127.0.0.1/css/style.css?v=20261024'
Write-Host ('mobile /: ' + $mCode + ' 含KK_ICON: ' + ($mb | Select-String 'KK_ICON' -Quiet) + '; css v20261024: ' + $cssCode)
if ($emojiCount -ne 8 -or $mCode -ne '200' -or $cssCode -ne '200') { Write-Host '!! 校验未全过'; exit 1 }
Write-Host 'KK-EMOJI-VERIFY-DONE'
