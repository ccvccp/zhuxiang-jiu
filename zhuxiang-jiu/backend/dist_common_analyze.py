"""定位 common chunk 中 i.W 封装的模块定义与 401 处理"""
import re

SRC = open("/var/www/zxjiu/dist/js/common.83a43381.js",
           encoding="utf-8", errors="ignore").read()

# 1. 找调用点上下文中 i 的模块号: "i=w(1234)" 形式
m = re.search(r"var\s+i\s*=\s*w\((\d+)\)", SRC)
print("i = w(module) →", m.group(1) if m else "未找到(可能其他形式)")

# 2. 直接搜该文件内请求封装函数(含 statusCode 处理的 request 定义)
for pat in (r"statusCode", r"401"):
    hits = [mm.start() for mm in re.finditer(pat, SRC)]
    print(f"\n'{pat}' 出现 {len(hits)} 次")
    for h in hits[:3]:
        print("...", SRC[max(0, h - 100):h + 200].replace("\n", " "), "...\n")
