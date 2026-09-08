# ============================================================
# verify-deploy-local.ps1 · zxjiu.com 部署产物本地全链路验证
# ============================================================
# 用途: 上服务器前, 本机 Docker 起一次性 nginx 容器, 加载真实
#       nginx-zxjiu.conf + 真实 dist 产物, 反代本机后端容器,
#       端到端预演生产部署链路:
#         [0] deploy-zxjiu.sh 部署脚本语法(sh -n)
#         [1] H5 首页 200 且标题正确
#         [2] /api 反代后端容器(health 200 + JSON 体)
#         [3] SPA 前端路由回退(非根路径返回 index.html)
#         [4] JS 静态资源 gzip 压缩生效
#         [5] JS 静态资源 immutable 长缓存头
#         [6] 入口页 no-cache(重部署立即取最新哈希清单)
#
# 用法: npm run verify:deploy
# 依赖: 本机 Docker + 后端容器运行中(0.0.0.0:8000)
#
# 说明: 容器内 127.0.0.1 是容器自身, 测试态仅将 proxy_pass 目标改写为
#       host.docker.internal(不通时自动回退宿主 LAN IP), 其余配置与
#       生产 conf 逐字节一致——验证结论对服务器部署直接有效
$ErrorActionPreference = 'Stop'

$PORT        = 8090
$NAME        = 'zxjiu-nginx-verify'
$NGINX_IMAGE = 'docker.m.daocloud.io/library/nginx:alpine'
$ALPINE      = 'docker.m.daocloud.io/library/alpine:latest'

$appRoot  = Split-Path -Parent $PSScriptRoot                 # taro-app/
$dist     = Join-Path $appRoot 'dist'
$confSrc  = Join-Path (Split-Path -Parent $appRoot) 'zhuxiang-jiu\deploy\nginx-zxjiu.conf'
$distWin  = $dist -replace '\\', '/'
$depWin   = (Split-Path -Parent $confSrc) -replace '\\', '/'
$tempConf = Join-Path $env:TEMP 'zxjiu-nginx-verify.conf'
$tempWin  = $tempConf -replace '\\', '/'
$idxFile  = Join-Path $env:TEMP 'zxjiu-verify-index.html'
$spaFile  = Join-Path $env:TEMP 'zxjiu-verify-spa.html'

$passed = 0; $failed = 0
function Record($name, $ok, $detail = '') {
  if ($ok) { $script:passed++; Write-Host "  [OK]   $name" }
  else {
    $script:failed++
    if ($detail) { Write-Host "  [FAIL] $name → $detail" } else { Write-Host "  [FAIL] $name" }
  }
}
function Clear-VerifyContainer {
  $id = docker ps -aq --filter "name=^/${NAME}$"
  if ("$id") { docker rm -f $NAME | Out-Null }
}

# ---------- 前置 ----------
if (-not (Test-Path (Join-Path $dist 'index.html'))) { throw "未找到 $dist — 请先 npm run build:h5" }
if (-not (Test-Path $confSrc)) { throw "未找到 $confSrc" }

Write-Host 'zxjiu.com 部署链路本地验证(nginx 容器预演服务器)'
Write-Host ('=' * 60)

# [0] 生产部署脚本语法
docker run --rm -v "${depWin}:/deploy:ro" $ALPINE sh -n /deploy/deploy-zxjiu.sh | Out-Null
Record '[0] deploy-zxjiu.sh 语法检查(sh -n)' ($LASTEXITCODE -eq 0)

# ---------- 一次性 nginx(仅改写 proxy_pass 目标) ----------
Clear-VerifyContainer
$confText = [IO.File]::ReadAllText($confSrc)
$lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { $_.IPAddress -like '192.168.*' } | Select-Object -First 1).IPAddress
$targets = @('host.docker.internal')
if ($lanIp) { $targets += $lanIp }

$apiOk = $false; $apiBody = ''
foreach ($t in $targets) {
  Clear-VerifyContainer
  [IO.File]::WriteAllText($tempConf, $confText.Replace(
    'proxy_pass http://127.0.0.1:8000;', "proxy_pass http://${t}:8000;"))
  docker run -d --name $NAME -p "127.0.0.1:${PORT}:80" `
    -v "${distWin}:/var/www/zxjiu/dist:ro" `
    -v "${tempWin}:/etc/nginx/conf.d/default.conf:ro" `
    $NGINX_IMAGE | Out-Null

  # 就绪等待(nginx 启动 + 端口映射生效)
  $up = $false
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    $c = curl.exe -s -o NUL -w '%{http_code}' "http://127.0.0.1:${PORT}/"
    if ("$c" -eq '200') { $up = $true; break }
  }
  if (-not $up) { continue }

  $apiBody = curl.exe -s "http://127.0.0.1:${PORT}/api/decision/health"
  if ("$apiBody" -match '^\s*\{') { $apiOk = $true; Write-Host "(反代目标: ${t})"; break }
}

if (-not $apiOk) {
  Write-Host '--- 诊断: nginx 容器状态与日志 ---'
  docker ps -a --filter "name=$NAME" --format '{{.Names}} | {{.Status}}'
  docker logs --tail 30 $NAME
}

# ---------- 断言 ----------
# [1] H5 首页
curl.exe -s "http://127.0.0.1:${PORT}/" -o "$idxFile"
$idxHtml = ''
if (Test-Path $idxFile) { $idxHtml = [IO.File]::ReadAllText($idxFile, [Text.Encoding]::UTF8) }
Record '[1] H5 首页可达且标题正确' ($idxHtml -match '<title>竹香酒</title>')

# [2] API 反代
Record '[2] /api 反代后端(health 200 JSON)' ($apiOk -and ("$apiBody" -match '^\s*\{'))

# [3] SPA 回退
curl.exe -s "http://127.0.0.1:${PORT}/pages/trace-view" -o "$spaFile"
$spaHtml = ''
if (Test-Path $spaFile) { $spaHtml = [IO.File]::ReadAllText($spaFile, [Text.Encoding]::UTF8) }
Record '[3] SPA 前端路由回退(非根路径返回 index)' ($spaHtml -match '<title>竹香酒</title>')

# [4][5] 静态资源 gzip + 长缓存
$jsPath = ''
if ($idxHtml -match 'src="(/js/[^"]+\.js)"') { $jsPath = $Matches[1] }
if ($jsPath) {
  $hdr = (curl.exe -s -D - -o NUL -H 'Accept-Encoding: gzip' "http://127.0.0.1:${PORT}${jsPath}") -join ' '
  Record '[4] JS gzip 压缩生效' ($hdr -match 'Content-Encoding:\s*gzip')
  Record '[5] JS immutable 长缓存头' ($hdr -match 'Cache-Control:\s*public,\s*immutable')
} else {
  Record '[4] JS gzip 压缩生效' $false 'index.html 未解析到入口 JS'
  Record '[5] JS immutable 长缓存头' $false 'index.html 未解析到入口 JS'
}

# [6] 入口页 no-cache
$hdrIdx = (curl.exe -s -D - -o NUL "http://127.0.0.1:${PORT}/index.html") -join ' '
Record '[6] 入口页 no-cache(重部署即取新清单)' ($hdrIdx -match 'Cache-Control:\s*no-cache')

# ---------- 清理与汇总 ----------
Clear-VerifyContainer
Remove-Item $tempConf, $idxFile, $spaFile -Force -ErrorAction SilentlyContinue

Write-Host ('=' * 60)
$total = $passed + $failed
if ($failed -eq 0) {
  Write-Host ("部署链路本地验证: {0}/{0} PASS" -f $passed)
  exit 0
} else {
  Write-Host ("部署链路本地验证: {0}/{1} FAIL" -f $passed, $total)
  exit 1
}
