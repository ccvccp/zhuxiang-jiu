#!/bin/bash
# 服务器侧: 打包 ECS 迁移全家桶并直传 OSS (deploy/ecs8147/)
set -e
AKID="${ALIYUN_AK_ID:?export ALIYUN_AK_ID first}"
SECRET="${ALIYUN_AK_SECRET:?export ALIYUN_AK_SECRET first}"
BKT=zxjiu-videos
EP=https://$BKT.oss-cn-hangzhou.aliyuncs.com
DIR=/tmp/ecs_bundle
mkdir -p "$DIR"

oss_put() { # $1=file $2=object $3=ctype $4=acl_header(optional 'private')
  local f="$1" obj="/$BKT/deploy/ecs8147/$2" ctype="$3" acl="$4"
  local date=$(date -u "+%a, %d %b %Y %H:%M:%S GMT")
  local osshdr=""
  [ -n "$acl" ] && osshdr="x-oss-object-acl: $acl\n"
  local sts=$(printf "PUT\n\n%s\n%s\n%s%s" "$ctype" "$date" "$osshdr" "$obj")
  local sig=$(printf "PUT\n\n%s\n%s\n%s%s" "$ctype" "$date" "$osshdr" "$obj" \
    | openssl dgst -sha1 -hmac "$SECRET" -binary | base64)
  local extra=()
  [ -n "$acl" ] && extra=(-H "x-oss-object-acl: $acl")
  curl -s -X PUT -H "Date: $date" -H "Content-Type: $ctype" "${extra[@]}" \
    -H "Authorization: OSS $AKID:$sig" --data-binary "@$f" "$EP/deploy/ecs8147/$2"
  echo "PUT $2 ($(stat -c%s "$f") bytes) done"
}

cd "$DIR"

# 1. dist(剔 videos, 已走 CDN)
tar czf dist.tgz -C /var/www/zxjiu --exclude='dist/videos' dist

# 2. 证书
cp /etc/letsencrypt/live/zxjiu.com/fullchain.pem cert.pem
cp /etc/letsencrypt/live/zxjiu.com/privkey.pem  key.pem

# 3. nginx win + nssm
[ -s nginx.zip ] || curl -sL -o nginx.zip http://nginx.org/download/nginx-1.24.0.zip
if [ ! -s nssm.exe ]; then
  curl -sL -o nssm24.zip https://nssm.cc/release/nssm-2.24.zip || true
  if [ -s nssm24.zip ]; then
    python3 - << 'PYEOF'
import zipfile
z = zipfile.ZipFile('/tmp/ecs_bundle/nssm24.zip')
for n in z.namelist():
    if n.endswith('win64/nssm.exe'):
        open('/tmp/ecs_bundle/nssm.exe','wb').write(z.read(n)); break
PYEOF
  fi
fi

# 4. 配置与安装脚本(由本地 scp 至 /tmp/)
cp /tmp/ecs8147-nginx.conf nginx.conf
cp /tmp/ecs8147-site-proxy.conf site-proxy.conf
cp /tmp/ecs8147-install.ps1 install-ecs.ps1

ls -la

# 5. 上传(公开件; key.pem 单独 private ACL)
oss_put dist.tgz         dist.tgz         application/octet-stream
oss_put nginx.zip        nginx.zip        application/octet-stream
oss_put nssm.exe         nssm.exe         application/octet-stream
oss_put cert.pem         cert.pem         text/plain
oss_put nginx.conf       nginx.conf       text/plain
oss_put site-proxy.conf  site-proxy.conf  text/plain
oss_put install-ecs.ps1  install-ecs.ps1  text/plain
oss_put key.pem          key.pem          text/plain private

# 6. 校验: 公开件匿名 HEAD 200; key.pem 匿名 403
echo "--- verify ---"
for o in dist.tgz nginx.zip nssm.exe cert.pem nginx.conf site-proxy.conf install-ecs.ps1; do
  echo "$o HEAD: $(curl -s -o /dev/null -w '%{http_code}' -I "$EP/deploy/ecs8147/$o")"
done
echo "key.pem HEAD(anon): $(curl -s -o /dev/null -w '%{http_code}' -I "$EP/deploy/ecs8147/key.pem")"
