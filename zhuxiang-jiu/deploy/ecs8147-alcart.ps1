# ECS 同盟入车+直达商品部署
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/alcart-20261019.tgz' -OutFile C:\tmp8147\ac.tgz -UseBasicParsing
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/alcart-off-20261019.tgz' -OutFile C:\tmp8147\aco.tgz -UseBasicParsing
Push-Location $DIST
try {
    cmd /c "tar -xzf C:\tmp8147\ac.tgz -C $DIST 2>&1"
    cmd /c "tar -xzf C:\tmp8147\aco.tgz -C $DIST 2>&1"
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
foreach ($f in @('index.html', 'official.html', 'alliance-mall.html')) {
    $c = [IO.File]::ReadAllText("$DIST\$f")
    $mark = if ($f -eq 'alliance-mall.html') { 'focus-flash' } else { 'data-alpid' }
    $code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' "http://127.0.0.1/$f"
    Write-Host ($f + ': 标记=' + ($c -match $mark) + ' http=' + $code)
}
Write-Host 'ALCART-ECS-DONE'
