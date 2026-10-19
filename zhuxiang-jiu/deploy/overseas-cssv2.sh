#!/bin/bash
# 海外二轮: 无版本号 css 引用也升 20261027
DIST=/var/www/zxjiu/dist
n=0
for f in $DIST/*.html; do
  if grep -q 'css/style.css"' "$f"; then
    sed -i 's|css/style\.css"|css/style.css?v=20261027"|g' "$f"
    n=$((n+1))
  fi
done
echo "二轮升号 html 数: $n"
echo "残留无版本引用: $(grep -l 'css/style.css"' $DIST/*.html | wc -l)"
echo CSSV2-OVERSEAS-DONE
