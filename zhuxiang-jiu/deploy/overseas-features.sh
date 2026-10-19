#!/bin/bash
# 功能恢复包部署: index(全部功能宫格) + app.html(SPA入口) + main.js(返回钮) + 全站 v 号升级
set -e
DIST=/var/www/zxjiu/dist
PKG=https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/frontend/features-20261019.tgz

echo '== [1/4] 备份 =='
mkdir -p /tmp/features-bak-20261019
cp -f $DIST/index.html $DIST/js/main.js /tmp/features-bak-20261019/ 2>/dev/null || true
ls /tmp/features-bak-20261019/

echo '== [2/4] 下载解压 =='
curl -sSL -o /tmp/features.tgz "$PKG"
cd $DIST
tar -xzf /tmp/features.tgz
echo "app.html: $(ls -la app.html | awk '{print $5}')B"
echo "index kk-ball-sm: $(grep -c kk-ball-sm index.html)"

echo '== [3/4] 全站 main.js v 号升 20261020(js 30d immutable 缓存, 换URL强制刷新) =='
n=0
for f in $DIST/*.html; do
  if grep -q 'js/main.js' "$f"; then
    sed -i 's|js/main\.js?v=[0-9]*|js/main.js?v=20261020|g; s|js/main\.js"|js/main.js?v=20261020"|g' "$f"
    n=$((n+1))
  fi
done
echo "改写 html 数: $n"
echo "v20261020 引用数: $(grep -l 'main.js?v=20261020' $DIST/*.html | wc -l)"

echo '== [4/4] 验证 =='
for u in index.html app.html products.html; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: zxjiu.com' http://127.0.0.1/$u)
  echo "$u: $code"
done
echo "main.js 返回钮: $(grep -c globalBackBtn js/main.js)"
echo "main.js 响应头: $(curl -sI -H 'Host: zxjiu.com' 'http://127.0.0.1/js/main.js?v=20261020' | grep -i content-length)"
echo FEATURES-OVERSEAS-DONE
