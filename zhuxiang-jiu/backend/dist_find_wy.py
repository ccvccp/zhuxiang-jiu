"""全 dist 定位 WY 底层封装定义(所有请求的公共出口)"""
import glob
import re

for js in sorted(glob.glob("/var/www/zxjiu/dist/js/*.js")):
    try:
        src = open(js, encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    for m in re.finditer(r'["\']?WY["\']?\s*[:=]\s*function', src):
        s = m.start()
        print(f"### {js.split('/')[-1]} @{s}")
        print(src[s:s + 900].replace("\n", " "), "\n")
