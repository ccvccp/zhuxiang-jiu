#!/usr/bin/env bash
# ============================================================
# deploy-zxjiu.sh · zxjiu.com 服务器端一键部署脚本
# ============================================================
# 用途: 在服务器上执行, 自动完成部署 4 步(产物就位 → nginx 配置 →
#       重载 → HTTPS 证书), 与 deploy/nginx-zxjiu.conf 配套
#
# 用法:
#   1. 本机(Windows)运行 package-zxjiu.ps1 生成 zxjiu-deploy.tar.gz
#   2. 上传到服务器任意目录(如 /tmp)
#   3. 服务器执行: bash deploy-zxjiu.sh /tmp/zxjiu-deploy.tar.gz
#      (可加 --certbot 一并签发 HTTPS 证书)
#
# 幂等性: 可重复执行(产物覆盖式更新, nginx 配置强制链接)
set -euo pipefail

ARCHIVE="${1:?用法: bash deploy-zxjiu.sh <zxjiu-deploy.tar.gz> [--certbot]}"
RUN_CERTBOT="${2:-}"

WEB_ROOT="/var/www/zxjiu"
NGINX_CONF_SRC="nginx-zxjiu.conf"

echo "==> [1/4] 解压产物到 ${WEB_ROOT}/dist"
sudo mkdir -p "${WEB_ROOT}"
sudo tar -xzf "${ARCHIVE}" -C "${WEB_ROOT}"
# 压缩包内为 dist/ 目录结构, 校验关键文件
sudo test -f "${WEB_ROOT}/dist/index.html" || {
  echo "[FAIL] ${WEB_ROOT}/dist/index.html 不存在, 请检查压缩包结构"; exit 1; }
echo "    产物就位: $(sudo du -sh ${WEB_ROOT}/dist | cut -f1)"

echo "==> [2/4] 安装 nginx 站点配置"
if [ -d /etc/nginx/sites-available ]; then
  # Debian/Ubuntu 标准布局
  sudo install -m 644 "${WEB_ROOT}/${NGINX_CONF_SRC}" /etc/nginx/sites-available/zxjiu.conf
  sudo ln -sf /etc/nginx/sites-available/zxjiu.conf /etc/nginx/sites-enabled/zxjiu.conf
else
  # RHEL/CentOS 布局(单一 conf.d)
  sudo install -m 644 "${WEB_ROOT}/${NGINX_CONF_SRC}" /etc/nginx/conf.d/zxjiu.conf
fi

echo "==> [3/4] nginx 语法检查并重载"
sudo nginx -t
sudo systemctl reload nginx
echo "    http://<服务器IP> 已可访问(域名解析生效后即 http://zxjiu.com)"

if [ "${RUN_CERTBOT}" = "--certbot" ]; then
  echo "==> [4/4] 签发 HTTPS 证书(certbot)"
  # 前提: DNS A 记录已指向本服务器公网 IP
  sudo certbot --nginx -d zxjiu.com -d www.zxjiu.com --non-interactive --agree-tos
  echo "    https://zxjiu.com 已就绪(certbot 自动续期已注册)"
else
  echo "==> [4/4] 跳过 HTTPS(需要时加 --certbot 参数)"
  echo "    注意: 小程序 API 域名要求 https, 上线前必须执行 certbot"
fi

echo ""
echo "部署完成。验证命令:"
echo "  curl -sI http://127.0.0.1/api/decision/health   # 后端反代通"
echo "  curl -sI http://127.0.0.1/                       # H5 首页通"
