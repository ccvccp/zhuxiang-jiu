"""index.html v72 SPA chunk 注入(带备份 .bak-v72spa)

锚点: voice-wake-widget.js 引用后、</body> 前插入
<script defer src=/js/v72-variant.js?v=1></script>
index.html 为 no-cache no-store——改完即生效
"""
import shutil
import sys

PATH = "/var/www/zxjiu/dist/index.html"
ANCHOR = '<script defer src=/js/voice-wake-widget.js?v=91></script>'
TAG = '<script defer src="/js/v72-variant.js?v=1"></script>'

with open(PATH, encoding="utf-8") as f:
    html = f.read()

if "v72-variant.js" in html:
    print("ALREADY INJECTED — skip")
    sys.exit(0)
if html.count(ANCHOR) != 1:
    print("ANCHOR MISS/NOT-UNIQUE:", html.count(ANCHOR))
    sys.exit(1)

shutil.copyfile(PATH, PATH + ".bak-v72spa")
html = html.replace(
    ANCHOR, ANCHOR + TAG)
with open(PATH, "w", encoding="utf-8") as f:
    f.write(html)
print("INJECTED OK:",
      "v72-variant.js" in html)
