# ECS 竹奕佳酿真实图同步部署
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/hotimg-index-20261019.tgz' -OutFile C:\tmp8147\hi.tgz -UseBasicParsing
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/hotimg-official-20261019.tgz' -OutFile C:\tmp8147\ho.tgz -UseBasicParsing
Push-Location $DIST
try {
    cmd /c "tar -xzf C:\tmp8147\hi.tgz -C $DIST 2>&1"
    cmd /c "tar -xzf C:\tmp8147\ho.tgz -C $DIST 2>&1"
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
foreach ($f in @('index.html', 'official.html')) {
    $c = [IO.File]::ReadAllText("$DIST\$f")
    Write-Host ($f + ': api渲染=' + ($c -match 'api/product/list'))
    $code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' "http://127.0.0.1/$f"
    Write-Host ('  http: ' + $code)
}
Write-Host 'HOTIMG-ECS-DONE'
