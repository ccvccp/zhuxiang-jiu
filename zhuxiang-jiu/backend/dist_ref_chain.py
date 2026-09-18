"""chunk 引用链分析: index.html 引用 + chunk 名交叉引用"""
import glob
import re

html = open("/var/www/zxjiu/dist/index.html", encoding="utf-8").read()
print("=== index.html script 引用 ===")
for m in re.finditer(r'<script[^>]*src="([^"]+)"', html):
    print(" ", m.group(1))

print("\n=== 'app.eebb43ae' 字符串出现的 js ===")
for js in sorted(glob.glob("/var/www/zxjiu/dist/js/*.js")):
    if ".bak-" in js:
        continue
    src = open(js, encoding="utf-8", errors="ignore").read()
    if "app.eebb43ae" in src:
        for m in re.finditer(r".{60}app\.eebb43ae.{30}", src):
            print(f" {js.split('/')[-1]}: ...{m.group(0)}...")

print("\n=== 'taro.1560daa0' 字符串出现的 js ===")
for js in sorted(glob.glob("/var/www/zxjiu/dist/js/*.js")):
    if ".bak-" in js:
        continue
    src = open(js, encoding="utf-8", errors="ignore").read()
    if "taro.1560daa0" in src:
        for m in re.finditer(r".{60}taro\.1560daa0.{30}", src):
            print(f" {js.split('/')[-1]}: ...{m.group(0)}...")
