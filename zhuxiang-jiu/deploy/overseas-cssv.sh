#!/bin/bash
# 海外: css v 全站升 20261027 + 验证公告条单行样式
set -e
DIST=/var/www/zxjiu/dist
n=0
for f in $DIST/*.html; do
  if grep -q 'css/style.css' "$f"; then
    sed -i 's|style\.css?v=[0-9]*|style.css?v=20261027|g' "$f"
    n=$((n+1))
  fi
done
echo "升号 html 数: $n"
echo "css 含 nowrap 规则: $(grep -c 'white-space: nowrap' $DIST/css/style.css)"
echo "css md5: $(md5sum $DIST/css/style.css | cut -c1-10)"
echo "抽查 css 200: $(curl -sk -o /dev/null -w '%{http_code}' -H 'Host: zyjiu.com' --resolve zyjiu.com:443:127.0.0.1 https://127.0.0.1/css/style.css?v=20261027)"
echo NOTICEBAR-OVERSEAS-DONE
