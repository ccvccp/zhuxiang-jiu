# -*- coding: utf-8 -*-
"""核验: activity-detail.html 顶栏返回活动中心按钮"""
import io
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(BASE, "activity-detail.html")
OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "xz_inline")

html = io.open(PAGE, encoding="utf-8").read()

blocks = re.findall(
    r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html, re.I)
if not blocks:
    raise SystemExit("FAIL: no inline script")
if not os.path.isdir(OUT):
    os.makedirs(OUT)
for i, b in enumerate(blocks):
    io.open(os.path.join(OUT, "actd%d.js" % i), "w", encoding="utf-8").write(b)
print("scripts-extracted: %d" % len(blocks))

checks = [
    # 顶栏圆形返回按钮(id 绑定, 非 history.back 而是回列表页)
    ('<button class="back-btn" id="backBtn"', True),
    # goList 绑定: 按钮 + 品牌区 + footer 三入口
    ("var goList = function () { location.href = 'activity.html' + API_QS; };", True),
    ("$('backBtn').onclick = goList", True),
    ("$('backBrand').onclick = goList", True),
    # CSS 就位(与列表页同款)
    ('.back-btn {', True),
    ('.topbar-left {', True),
    # 旧文字箭头已去(避免与按钮重复)
    ('← 活动中心', None),
]
fails = [p[:60] for p, want in checks if (html.count(p) > 0) != bool(want)]
if fails:
    raise SystemExit("FAIL:\n  " + "\n  ".join(fails))
print("static-checks: ALL PASS (%d)" % len(checks))
