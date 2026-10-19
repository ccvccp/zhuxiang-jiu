# ECS 功能恢复包部署(index宫格 + app.html + main.js返回钮 + 全站v号)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
$PKG = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/features-20261019.tgz'
$MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49'

Write-Host '==> [1/4] 备份'
New-Item -ItemType Directory -Force C:\tmp8147\features-bak-20261019 | Out-Null
Copy-Item "$DIST\index.html" C:\tmp8147\features-bak-20261019\ -ErrorAction SilentlyContinue
Copy-Item "$DIST\js\main.js" C:\tmp8147\features-bak-20261019\ -ErrorAction SilentlyContinue
Write-Host '    OK'

Write-Host '==> [2/4] 下载解压'
Invoke-WebRequest $PKG -OutFile C:\tmp8147\features.tgz -UseBasicParsing
Push-Location $DIST
try { cmd /c "tar -xzf C:\tmp8147\features.tgz -C $DIST 2>&1" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
Write-Host ('    app.html: ' + (Get-Item "$DIST\app.html").Length + 'B; index kk-ball-sm: ' + ([regex]::Matches([IO.File]::ReadAllText("$DIST\index.html"), 'kk-ball-sm').Count))

Write-Host '==> [3/4] 全站 main.js v 号升 20261020'
$utf8 = [Text.UTF8Encoding]::new($false)
$n = 0
Get-ChildItem "$DIST\*.html" | ForEach-Object {
    $c = [IO.File]::ReadAllText($_.FullName)
    $orig = $c
    $c = [regex]::Replace($c, 'js/main\.js\?v=\d+', 'js/main.js?v=20261020')
    $c = $c -replace 'js/main\.js"', 'js/main.js?v=20261020"'
    if ($c -ne $orig) { [IO.File]::WriteAllText($_.FullName, $c, $utf8); $n++ }
}
Write-Host ("    改写 html 数: $n")

Write-Host '==> [4/4] 验证'
foreach ($u in @('index.html', 'app.html', 'products.html', 'my-card.html')) {
    $code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' "http://127.0.0.1/$u"
    Write-Host ("    ${u}: $code")
}
$mj = [IO.File]::ReadAllText("$DIST\js\main.js")
Write-Host ('    main.js 返回钮: ' + ([regex]::Matches($mj, 'globalBackBtn').Count))
if ((curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' 'http://127.0.0.1/app.html') -ne '200') { Write-Host '!! app.html 未过'; exit 1 }
Write-Host 'FEATURES-ECS-DONE'
