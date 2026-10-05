#!/bin/bash
# 首页丢失+唤醒浮球缺失·现场排查
echo "=== A. 首页可达性 ==="
curl -s -o /dev/null -w "index=%{http_code} size=%{size_download}\n" https://zxjiu.com/
curl -s -o /dev/null -w "index.html=%{http_code}\n" https://zxjiu.com/index.html
echo ""
echo "=== B. index.html 首部内容(标题/浮球痕迹) ==="
curl -s https://zxjiu.com/ | head -c 600
echo ""
echo ""
echo "=== C. 浮球痕迹(xiaozhu/widget/fab 相关) ==="
curl -s https://zxjiu.com/ | grep -c -iE "xiaozhu|widget|浮球|fab|voice" | xargs echo "index_hits:"
ls -la /var/www/zxjiu/dist/ | grep -iE "index|xiaozhu|widget" | head -8
echo ""
echo "=== D. 浮球 JS/资源存在性 ==="
for f in js/xiaozhu-widget.js js/xiaozhu.js js/voice-fab.js; do
  [ -f /var/www/zxjiu/dist/$f ] && echo "EXISTS: $f" || true
done
grep -rioE "xiaozhu[a-z-]*\.js" /var/www/zxjiu/dist/index.html 2>/dev/null | head -3
echo ""
echo "=== E. index.html 修改时间(近期是否被动过) ==="
stat -c '%y %n' /var/www/zxjiu/dist/index.html /var/www/zxjiu/dist/drama.html 2>/dev/null
ls -lt /var/www/zxjiu/dist/*.html | head -5
