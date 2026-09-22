# -*- coding: utf-8 -*-
"""核验: 手机端修复(窄屏排版 + 互跳链接 v2 破缓存) 两页全项"""
import io
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("TEMP", "/tmp")

def load(name):
    return io.open(os.path.join(BASE, name), encoding="utf-8").read()

def scripts(html, tag):
    blocks = re.findall(
        r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html, re.I)
    for i, b in enumerate(blocks):
        io.open(os.path.join(OUT, "m_%s%d.js" % (tag, i)), "w",
                encoding="utf-8").write(b)
    return len(blocks)

checks = []
lst = load("activity.html")
det = load("activity-detail.html")

# ---- 列表页 ----
checks += [
    ("L1 窄屏CSS: reg-row wrap", "@media (max-width: 480px)" in lst
     and ".reg-row { flex-wrap: wrap; row-gap: 6px; }" in lst
     and ".reg-row .info { flex: 1 1 100%; }" in lst),
    ("L2 活动卡跳详情 v2", lst.count("activity-detail.html?v=2&id=") == 2),
    ("L3 无残留旧跳转", "activity-detail.html?id=" not in lst),
    ("L4 回归: 返回按钮+goBack 在",
     'id="backBtn"' not in lst and "window.goBack" in lst),  # 列表页是 goBack 不是 backBtn
    ("L5 回归: 跳详情仍带 API_QS", "API_QS + '\\''" in lst or "+ API_QS" in lst),
]
# ---- 详情页 ----
checks += [
    ("D1 窄屏CSS: prize-row wrap", "@media (max-width: 480px)" in det
     and ".prize-row { flex-wrap: wrap; row-gap: 6px; }" in det
     and ".reg-status-row { flex-wrap: wrap; row-gap: 6px; }" in det),
    ("D2 goList v2", "location.href = 'activity.html?v=2' + API_QS" in det),
    ("D3 backFooter v2", "$('backFooter').href = 'activity.html?v=2' + API_QS" in det),
    ("D4 版本化链接 v2 x3(错误卡x2+footer)", det.count('href="activity.html?v=2"') == 3),
    ("D5 无残留旧链接", 'href="activity.html"' not in det),
    ("D6 回归: 返回按钮绑定",
     "$('backBtn').onclick = goList" in det and "$('backBrand').onclick = goList" in det),
    ("D7 id 正则兼容 ?v=2&id=", "/[?&]id=(\\d+)/".replace("\\\\", "\\") in det
     or re.search(r"/\[\?&\]id=\(\\d\+\)/", det) is not None),
]
fails = [n for n, ok in checks if not ok]
print("scripts: list=%d detail=%d" % (scripts(lst, "lst"), scripts(det, "det")))
for n, ok in checks:
    print(("  PASS " if ok else "  FAIL ") + n)
if fails:
    raise SystemExit("FAILED: %d" % len(fails))
print("ALL PASS (%d)" % len(checks))
