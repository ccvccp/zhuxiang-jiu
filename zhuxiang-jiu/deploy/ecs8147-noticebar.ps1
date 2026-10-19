# ECS: 公告条单行样式 + css 全站升号 20261027
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/noticebar-20261019.tgz' -OutFile C:\tmp8147\nb.tgz -UseBasicParsing
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/noticebar-off-20261019.tgz' -OutFile C:\tmp8147\nbo.tgz -UseBasicParsing
Push-Location $DIST
try {
    cmd /c "tar -xzf C:\tmp8147\nb.tgz -C $DIST 2>&1"
    cmd /c "tar -xzf C:\tmp8147\nbo.tgz -C $DIST 2>&1"
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
$utf8 = [Text.UTF8Encoding]::new($false)
$n = 0
Get-ChildItem "$DIST\*.html" | ForEach-Object {
    $c = [IO.File]::ReadAllText($_.FullName)
    $orig = $c
    $c = [regex]::Replace($c, 'style\.css\?v=\d+', 'style.css?v=20261027')
    if ($c -ne $orig) { [IO.File]::WriteAllText($_.FullName, $c, $utf8); $n++ }
}
Write-Host ("升号 html 数: $n")
$css = [IO.File]::ReadAllText("$DIST\css\style.css")
Write-Host ('css 含公告条 nowrap 规则: ' + ($css -match '\.notice-bar \{\s*font-size: 12px;\s*padding: 6px 12px;\s*white-space: nowrap'))
$code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' 'http://127.0.0.1/css/style.css?v=20261027'
Write-Host ('css v20261027: ' + $code)
if ($code -ne '200') { Write-Host '!! 未过'; exit 1 }
Write-Host 'NOTICEBAR-ECS-DONE'
