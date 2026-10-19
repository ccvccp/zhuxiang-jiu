#!/bin/bash
# 海外 关键文件清单+MD5(与国内核对)
DIST=/var/www/zxjiu/dist
echo "HTML_COUNT: $(ls -1 $DIST/*.html | wc -l)"
echo "JS_COUNT: $(ls -1 $DIST/js/*.js | wc -l)"
echo "CSS_COUNT: $(ls -1 $DIST/css/*.css | wc -l)"
for k in index.html app.html official.html my-card.html xiaozhu-voice.html alliance-mall.html wine-advisor.html video.html service.html js/main.js css/style.css js/app.b984e24b.js js/taro.6f3e5fe0.js; do
  if [ -f "$DIST/$k" ]; then
    echo "MD5 $k $(md5sum "$DIST/$k" | cut -c1-10) $(stat -c%s "$DIST/$k")"
  else
    echo "MD5 $k MISSING"
  fi
done
echo "FEATURE index: kk-ball-sm=$(grep -c kk-ball-sm $DIST/index.html) kk-ball=$(grep -c kk-ball $DIST/index.html)"
echo "FEATURE app: officialEntry拦截=$(grep -c officialEntry $DIST/app.html)"
echo "FEATURE main.js: globalBackBtn=$(grep -c globalBackBtn $DIST/js/main.js)"
echo "FEATURE v号: main.js?v=20261020 html数=$(grep -l 'main.js?v=20261020' $DIST/*.html | wc -l)"
