# ECS 本地商品图部署(zz-*.jpg 13 张 -> C:\www\dist\images\products)
$ErrorActionPreference = 'Stop'
$DIST = 'C:\www\dist'
New-Item -ItemType Directory -Force -Path C:\tmp8147 | Out-Null
Invoke-WebRequest 'https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/zzimgs-20261019.tgz' -OutFile C:\tmp8147\zz.tgz -UseBasicParsing
cmd /c "tar -xzf C:\tmp8147\zz.tgz -C $DIST 2>&1"
if ($LASTEXITCODE -ne 0) { Write-Host '!! 解压失败'; exit 1 }
$n = (Get-ChildItem "$DIST\images\products" -File).Count
Write-Host ("images/products 文件数: " + $n)
$code = curl.exe -s -o NUL -w '%{http_code}' -m 10 -H 'Host: zxjiu.com' "http://127.0.0.1/images/products/zz-classic42.jpg"
Write-Host ("zz-classic42.jpg http=" + $code)
Write-Host 'ZZIMGS-ECS-DONE'
