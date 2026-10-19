# ECS 关键文件清单+MD5(与海外核对用)
$ErrorActionPreference = 'Continue'
$DIST = 'C:\www\dist'
Write-Host ('HTML_COUNT: ' + @(Get-ChildItem $DIST -Filter *.html).Count)
Write-Host ('JS_COUNT: ' + @(Get-ChildItem "$DIST\js" -Filter *.js).Count)
Write-Host ('CSS_COUNT: ' + @(Get-ChildItem "$DIST\css" -Filter *.css).Count)
$keys = @('index.html', 'app.html', 'official.html', 'my-card.html', 'xiaozhu-voice.html',
          'alliance-mall.html', 'wine-advisor.html', 'video.html', 'service.html',
          'js\main.js', 'css\style.css', 'js\app.b984e24b.js', 'js\taro.6f3e5fe0.js')
foreach ($k in $keys) {
    $p = "$DIST\$k"
    if (Test-Path $p) {
        $md5 = (Get-FileHash $p -Algorithm MD5).Hash.Substring(0, 10)
        Write-Host ("MD5 $k $md5 " + (Get-Item $p).Length)
    } else { Write-Host "MD5 $k MISSING" }
}
$idx = [IO.File]::ReadAllText("$DIST\index.html")
Write-Host ('FEATURE index: kk-ball-sm=' + ([regex]::Matches($idx, 'kk-ball-sm').Count) + ' kk-ball=' + ([regex]::Matches($idx, 'kk-ball').Count))
$app = [IO.File]::ReadAllText("$DIST\app.html")
Write-Host ('FEATURE app: officialEntry拦截=' + ($app -match 'officialEntry'))
$mj = [IO.File]::ReadAllText("$DIST\js\main.js")
Write-Host ('FEATURE main.js: globalBackBtn=' + ([regex]::Matches($mj, 'globalBackBtn').Count))
Write-Host ('FEATURE v号: main.js?v=20261020 html数=' + @(Get-ChildItem $DIST -Filter *.html | Where-Object { [IO.File]::ReadAllText($_.FullName) -match 'main\.js\?v=20261020' }).Count)
