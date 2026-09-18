"""读 taro chunk _request 主路径(非 jsonp 分支)定位响应分发点"""
import re

SRC = open("/var/www/zxjiu/dist/js/taro.1560daa0.js",
           encoding="utf-8", errors="ignore").read()

m = re.search(r'function _request\(\)\{var e=arguments', SRC)
start = m.start()
seg = SRC[start:start + 4200]
print(seg)
