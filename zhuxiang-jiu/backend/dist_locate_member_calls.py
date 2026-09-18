"""定位 member/profile 调用点所在 chunk 与所用请求封装"""
import glob
import re

targets = ["/api/member/profile", "/api/points/account"]
for js in sorted(glob.glob("/var/www/zxjiu/dist/js/*.js")):
    try:
        src = open(js, encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    for t in targets:
        for m in re.finditer(re.escape(t), src):
            s = max(0, m.start() - 220)
            print(f"### {js.split('/')[-1]} @ {t}")
            print("...", src[s:m.end() + 60].replace("\n", " "), "...\n")
            break  # 每文件每目标只看第一处
