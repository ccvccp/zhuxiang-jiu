# ECS 图片同步部署(index/products/official)
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/syncimg-20261019.tgz' -OutFile C:\tmp8147\si.tgz -UseBasicParsing
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/syncimg-off-20261019.tgz' -OutFile C:\tmp8147\so.tgz -UseBasicParsing
Push-Location $DIST
try {
    cmd /c "tar -xzf C:\tmp8147\si.tgz -C $DIST 2>&1"
    cmd /c "tar -xzf C:\tmp8147\so.tgz -C $DIST 2>&1"
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
foreach ($f in @('index.html', 'official.html', 'products.html')) {
    $c = [IO.File]::ReadAllText("$DIST\$f")
    $mark = if ($f -eq 'products.html') { 'api/product/list' } else { 'matchMallImg' }
    $code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' "http://127.0.0.1/$f"
    Write-Host ($f + ': 标记=' + ($c -match $mark) + ' http=' + $code)
}
Write-Host 'SYNCIMG-ECS-DONE'
