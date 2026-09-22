# -*- coding: utf-8 -*-
"""核验: activity-dashboard.html 返回按钮 + 手机排版修复"""
import io
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(BASE, "activity-dashboard.html")
OUT = os.environ.get("TEMP", "/tmp")

html = io.open(PAGE, encoding="utf-8").read()
blocks = re.findall(
    r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html, re.I)
for i, b in enumerate(blocks):
    io.open(os.path.join(OUT, "dsh%d.js" % i), "w", encoding="utf-8").write(b)
print("scripts-extracted: %d" % len(blocks))

checks = [
    ("D1 返回按钮 CSS", ".back-btn {" in html
     and ".back-btn:hover" in html),
    ("D2 banner 返回按钮", '<button class="back-btn" onclick="goBack()"' in html),
    ("D3 goBack 函数 + 兜底 v2", "window.goBack = function () {" in html
     and "location.href = 'activity.html?v=2'" in html),
    ("D4 手机 CSS 增强", ".ov-cells { grid-template-columns: repeat(2, 1fr)" in html
     and ".dash-banner h1 { font-size: 19px; }" in html
     and ".sub-table { font-size: 11px; }" in html),
    ("D5 sub-table 横滑包装", 'overflow-x:auto"><table class="sub-table"' in html),
    ("D6 回归: 深链 focus", "focusPending" in html),
    ("D7 回归: banner-link 不误伤", '.banner-link { display: none; }' in html
     and 'class="banner-link" href="module-test.html"' in html),
]
fails = [n for n, ok in checks if not ok]
for n, ok in checks:
    print(("  PASS " if ok else "  FAIL ") + n)
if fails:
    raise SystemExit("FAILED: %d" % len(fails))
print("ALL PASS (%d)" % len(checks))
