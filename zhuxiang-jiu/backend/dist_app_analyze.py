"""dist app 包分析: refreshSession 调用点 / 请求包装器响应处理 / 会话存储键"""
import re

SRC = open("/var/www/zxjiu/dist/js/app.eebb43ae.js",
           encoding="utf-8", errors="ignore").read()

print("=== 1. refreshSession 所有出现点(前后 160 字符) ===")
for m in re.finditer(r"refreshSession", SRC):
    s = max(0, m.start() - 160)
    print("...", SRC[s:m.end() + 160].replace("\n", " "), "...\n")

print("=== 2. 请求包装器(WY)定义: 含 url/method/data 的函数体 ===")
for m in re.finditer(r"WY[:=]\s*function", SRC):
    s = m.start()
    print("...", SRC[s:s + 700].replace("\n", " "), "...\n")

print("=== 3. 401 字面量上下文 ===")
for m in re.finditer(r"401", SRC):
    ctx = SRC[max(0, m.start() - 80):m.end() + 120]
    if "status" in ctx or "code" in ctx:
        print("...", ctx.replace("\n", " "), "...\n")
