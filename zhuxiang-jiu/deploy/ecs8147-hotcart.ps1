# ECS 热销区入车直达商城(index/official)
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/hotcart-20261019.tgz' -OutFile C:\tmp8147\hc.tgz -UseBasicParsing
cmd /c "tar -xzf C:\tmp8147\hc.tgz -C $DIST 2>&1"
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
foreach ($f in @('index.html', 'official.html')) {
    $c = [IO.File]::ReadAllText("$DIST\$f")
    $code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' "http://127.0.0.1/$f"
    Write-Host ($f + ': 入车标记=' + ($c -match 'data-hadd') + ' 详情直达=' + ($c -match 'product-detail/index\?id=') + ' http=' + $code)
}
Write-Host 'HOTCART-ECS-DONE'
