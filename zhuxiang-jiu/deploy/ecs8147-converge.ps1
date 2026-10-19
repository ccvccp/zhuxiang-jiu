# ECS 全量前端收敛(SPA 基座 + 静态覆盖, 只覆盖不删除)
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
$SPA = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/converge-20261019-spa.tgz'
$STATIC = 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/converge-20261019-static.tgz'
$MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49'

Write-Host '==> [1/4] 备份关键文件'
New-Item -ItemType Directory -Force C:\tmp8147\converge-bak-20261019 | Out-Null
foreach ($f in @('index.html', 'official.html', 'my-card.html', 'css\style.css')) {
    if (Test-Path "$DIST\$f") { Copy-Item "$DIST\$f" "C:\tmp8147\converge-bak-20261019\$(($f -replace '\\','_'))" -Force }
}
Write-Host '    OK'

Write-Host '==> [2/4] 下载两个包'
Invoke-WebRequest $SPA -OutFile C:\tmp8147\converge-spa.tgz -UseBasicParsing
Invoke-WebRequest $STATIC -OutFile C:\tmp8147\converge-static.tgz -UseBasicParsing
Write-Host ('    spa: ' + [math]::Round((Get-Item C:\tmp8147\converge-spa.tgz).Length / 1MB, 1) + 'MB; static: ' + [math]::Round((Get-Item C:\tmp8147\converge-static.tgz).Length / 1KB, 0) + 'KB')

Write-Host '==> [3/4] 解压覆盖(先 SPA 后静态)'
Push-Location $DIST
try {
    cmd /c "tar -xzf C:\tmp8147\converge-spa.tgz -C $DIST 2>&1"
    if ($LASTEXITCODE -ne 0) { Write-Host '!! SPA 解压失败'; exit 1 }
    cmd /c "tar -xzf C:\tmp8147\converge-static.tgz -C $DIST 2>&1"
    if ($LASTEXITCODE -ne 0) { Write-Host '!! 静态解压失败'; exit 1 }
} finally { Pop-Location }
Write-Host '    完成'

Write-Host '==> [4/4] 验证'
$htmls = @(Get-ChildItem $DIST -Filter *.html)
Write-Host ('    html总数: ' + $htmls.Count + ' (原33)')
foreach ($f in @('my-card.html', 'xiaozhu-voice.html', 'alliance-mall.html', 'wine-advisor.html')) {
    Write-Host ('    ' + $f + ': ' + (Test-Path "$DIST\$f"))
}
$idx = [IO.File]::ReadAllText("$DIST\index.html")
Write-Host ('    index kk-ball: ' + ([regex]::Matches($idx, 'kk-ball').Count) + '; jpg残留: ' + ([regex]::Matches($idx, 'kk-\w+\.jpg').Count))
$mc = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/my-card.html
$vc = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/xiaozhu-voice.html
$ic = curl.exe -s -o NUL -w '%{http_code}' -m 10 -A $MOBILE_UA -H 'Host: zxjiu.com' http://127.0.0.1/index.html
Write-Host ('    HTTP: my-card=' + $mc + ' voice=' + $vc + ' index=' + $ic)
if ($mc -ne '200' -or $vc -ne '200' -or $ic -ne '200') { Write-Host '!! 验证未全过'; exit 1 }
Write-Host 'CONVERGE-ECS-DONE'
