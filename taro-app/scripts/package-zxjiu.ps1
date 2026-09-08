# ============================================================
# package-zxjiu.ps1 · zxjiu.com 部署包打包脚本(本机 Windows 执行)
# ============================================================
# 用途: 将 H5 产物(taro-app/dist) + nginx 配置 + 服务器部署脚本
#       打成单个 zxjiu-deploy.tar.gz, 上传服务器后一条命令完成部署
#
# 用法: powershell -File scripts/package-zxjiu.ps1
# 产出: taro-app/zxjiu-deploy.tar.gz
#       (内含 dist/ + nginx-zxjiu.conf + deploy-zxjiu.sh)
#
# 服务器侧执行(上传后):
#   bash deploy-zxjiu.sh zxjiu-deploy.tar.gz            # 基础部署
#   bash deploy-zxjiu.sh zxjiu-deploy.tar.gz --certbot  # 部署+HTTPS
#
# 注: 归档经 docker 容器内 tar 生成(挂载目录直写)——
#     PowerShell 管道 stdout 重定向会按文本重编码损坏 gzip 二进制流, 禁用
$ErrorActionPreference = 'Stop'

$appRoot = Split-Path -Parent $PSScriptRoot   # taro-app/
$dist = Join-Path $appRoot 'dist'
$deployDir = Join-Path (Split-Path -Parent $appRoot) 'zhuxiang-jiu\deploy'
$out = Join-Path $appRoot 'zxjiu-deploy.tar.gz'

# 前置校验: 产物存在且为最新域名感知版
if (-not (Test-Path (Join-Path $dist 'index.html'))) {
    throw "未找到 H5 产物 $dist — 请先执行 npm run build:h5"
}
$hit = Get-ChildItem $dist -Recurse -Filter 'app.*.js' |
    Select-String -Pattern 'zxjiu\.com' -List | Select-Object -First 1
if (-not $hit) {
    throw "产物不含域名感知逻辑(zxjiu.com) — dist 为旧构建, 请重新 build:h5"
}

# 调 docker 打包: 容器内 tar 直写挂载目录(避免 PS stdout 重编码损坏二进制)
# 注: Alpine 是 busybox tar, 不支持 GNU 式多段 -C 交错(只认最后一个 -C),
#     故先拷入 staging 目录再单一 -C 打包, 保证归档结构: dist/ + 2 个部署文件
$appWin = $appRoot -replace '\\', '/'
$deployWin = $deployDir -replace '\\', '/'

docker run --rm `
    -v "${appWin}:/in/app:ro" `
    -v "${deployWin}:/in/deploy:ro" `
    -v "${appWin}:/out" `
    docker.m.daocloud.io/library/alpine:latest sh -c `
    "mkdir -p /stage && cp -a /in/app/dist /stage/ && cp /in/deploy/nginx-zxjiu.conf /in/deploy/deploy-zxjiu.sh /stage/ && tar -czf /out/zxjiu-deploy.tar.gz -C /stage dist nginx-zxjiu.conf deploy-zxjiu.sh" | Out-Null

if (-not (Test-Path $out)) {
    throw "打包失败: $out 未生成"
}

$size = [math]::Round((Get-Item $out).Length / 1KB)
Write-Host "部署包已生成: $out ($size KB)"
Write-Host ''
Write-Host '后续步骤:'
Write-Host "  1. 上传 $out 到服务器(如 /tmp)"
Write-Host '  2. 服务器执行: bash deploy-zxjiu.sh zxjiu-deploy.tar.gz --certbot'
Write-Host '  3. 确认域名商 DNS A 记录已指向服务器公网 IP'
