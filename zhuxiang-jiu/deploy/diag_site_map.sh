#!/bin/bash
# 站点结构侦察: 官网 vs Taro H5 商城入口 + 注入清单实况
echo "=== A. nginx location 结构 ==="
grep -rE "location|root|try_files" /etc/nginx/sites-enabled/ 2>/dev/null | grep -v "^#" | head -25
echo ""
echo "=== B. dist 里商城/Taro H5 痕迹 ==="
ls /var/www/zxjiu/dist/ | grep -iE "mall|app|shop" | head -8
ls /var/www/zxjiu/dist/js/ 2>/dev/null | grep -iE "voice-wake|scene-widget|v72" | head -6
echo ""
echo "=== C. 官网 index.html 尾部(注入区) ==="
tail -c 800 /var/www/zxjiu/dist/index.html
echo ""
echo ""
echo "=== D. 各候选首页的注入痕迹 ==="
for f in index.html mall.html app.html shop.html h5/index.html; do
  p=/var/www/zxjiu/dist/$f
  [ -f $p ] && echo "$f: $(grep -c 'voice-wake-widget\|scene-widget' $p) hits"
done
echo ""
echo "=== E. 小程序侧(若有独立部署目录) ==="
ls /var/www/zxjiu/ 2>/dev/null | head -10
