"""v80 面板 JS 语法验证(抽 script 块 esprima parse)"""
import esprima
import re

html = open(
    r"backend\xiaozhu-voice.html", encoding="utf-8").read()
blocks = re.findall(
    r"<script[^>]*>(.*?)</script>", html, re.S)
ok = 0
for i, b in enumerate(blocks):
    b = b.strip()
    if not b:
        continue
    esprima.parse(b)
    ok += 1
print(f"script blocks parsed OK: {ok}/{len(blocks)}")
