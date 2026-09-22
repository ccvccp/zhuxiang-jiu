# -*- coding: utf-8 -*-
"""核验: activity.html 返回功能(顶栏返回按钮 + goBack 兜底逻辑)"""
import io
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(BASE, "activity.html")
OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "xz_inline")

html = io.open(PAGE, encoding="utf-8").read()

blocks = re.findall(
    r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html, re.I)
if not blocks:
    raise SystemExit("FAIL: no inline script")
if not os.path.isdir(OUT):
    os.makedirs(OUT)
for i, b in enumerate(blocks):
    io.open(os.path.join(OUT, "act%d.js" % i), "w", encoding="utf-8").write(b)
print("scripts-extracted: %d" % len(blocks))

checks = [
    # 顶栏返回按钮(在 topbar-left 内, aria 无障碍)
    ('<button class="back-btn" onclick="goBack()" aria-label="返回"', True),
    # goBack 挂 window(IIFE 内联 onclick 可达)
    ('window.goBack = function () {', True),
    # 同源来路 back / 外源兜底商城首页
    ("ref.indexOf(location.origin) === 0 && history.length > 1", True),
    ("location.href = '/'", True),
    # CSS 就位
    ('.back-btn {', True),
    ('.topbar-left {', True),
]
fails = [p[:60] for p, want in checks if (html.count(p) > 0) != bool(want)]
if fails:
    raise SystemExit("FAIL:\n  " + "\n  ".join(fails))
print("static-checks: ALL PASS (%d)" % len(checks))
