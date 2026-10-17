#!/bin/bash
# 海外机生产物归档(停用前冷备): Redis RDB + 数据目录 -> OSS
set -e
AKID="${1:?need ak id}"; SECRET="${2:?need ak secret}"
BKT=zxjiu-videos
EP="https://$BKT.oss-cn-hangzhou.aliyuncs.com"
DIR=/tmp/overseas_archive
mkdir -p "$DIR"
cd "$DIR"

echo "==> [1/4] Redis BGSAVE"
docker exec zhuxiang-redis-1 redis-cli bgsave
for i in $(seq 1 30); do
  ok=$(docker exec zhuxiang-redis-1 redis-cli lastsave)
  sleep 2
  ok2=$(docker exec zhuxiang-redis-1 redis-cli lastsave)
  [ "$ok" = "$ok2" ] && [ -n "$ok" ] && break
done
docker exec zhuxiang-redis-1 redis-cli info persistence | grep -E 'rdb_last_bgsave_status|aof_enabled' || true
# redis 容器数据卷路径探测
RDB_HOST=$(docker inspect zhuxiang-redis-1 --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}')
echo "    rdb 卷: $RDB_HOST"
cp "$RDB_HOST/dump.rdb" ./redis-dump-20261017.rdb && ls -la redis-dump-20261017.rdb

echo "==> [2/4] 数据目录打包"
cd /opt/zhuxiang/zhuxiang-jiu/backend
tar czf $DIR/overseas-data-20261017.tgz assets data repositories media guard_baselines 2>/dev/null || true
tar czf $DIR/overseas-code-20261017.tgz main.py requirements.txt core routes services repositories scripts 2>/dev/null || true
cd $DIR
tar czf overseas-dist-20261017.tgz -C /var/www/zxjiu --exclude='dist/videos' dist 2>/dev/null || true
ls -la

oss_put() { # $1=file $2=object
  local f="$1" obj="/$BKT/deploy/overseas-archive/$2" ctype="application/octet-stream"
  local date=$(date -u "+%a, %d %b %Y %H:%M:%S GMT")
  local sig=$(printf "PUT\n\n%s\n%s\n%s" "$ctype" "$date" "$obj" \
    | openssl dgst -sha1 -hmac "$SECRET" -binary | base64)
  local code=$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H "Date: $date" \
    -H "Content-Type: $ctype" -H "Authorization: OSS $AKID:$sig" \
    --data-binary "@$f" "$EP/deploy/overseas-archive/$2")
  echo "PUT $2 -> $code ($(stat -c%s "$f") bytes)"
}

echo "==> [3/4] 上传 OSS(deploy/overseas-archive/)"
oss_put redis-dump-20261017.rdb redis-dump-20261017.rdb
oss_put overseas-data-20261017.tgz overseas-data-20261017.tgz
oss_put overseas-code-20261017.tgz overseas-code-20261017.tgz
oss_put overseas-dist-20261017.tgz overseas-dist-20261017.tgz

echo "==> [4/4] 证书归档(letsencrypt 两套)"
tar czf overseas-certs-20261017.tgz -C /etc/letsencrypt live 2>/dev/null || true
oss_put overseas-certs-20261017.tgz overseas-certs-20261017.tgz
echo "OVERSEAS-ARCHIVE-DONE"
