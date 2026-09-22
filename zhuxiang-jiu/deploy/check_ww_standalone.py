# -*- coding: utf-8 -*-
"""本地核验: 语音页 ⚙️ 唤醒词入口独立页可见改造

1. 提取 xiaozhu-voice.html 全部内联 <script>(无 src), 写 /tmp 供 node --check
2. 静态断言: 按钮默认可见/启动块不再控制 wakeWordBtn/embed 逻辑保留/文案就位
"""
import io
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(BASE, "backend", "xiaozhu-voice.html")
OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "xz_inline")

html = io.open(PAGE, encoding="utf-8").read()

# ---- 1. 提取内联脚本 ----
blocks = re.findall(
    r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html, re.I)
if not blocks:
    raise SystemExit("FAIL: no inline script found")
if not os.path.isdir(OUT):
    os.makedirs(OUT)
for i, b in enumerate(blocks):
    io.open(os.path.join(OUT, "s%d.js" % i), "w", encoding="utf-8").write(b)
print("scripts-extracted: %d -> %s" % (len(blocks), OUT))

# ---- 2. 静态断言 ----
checks = [
    # ⚙️ 按钮默认可见(无 display:none)
    ('id="wakeWordBtn" onclick="showWakeWordSet()" '
     'style="cursor:pointer"', True),
    # 启动块只控制唤醒开关, 不再触碰唤醒词按钮
    ('document.getElementById("wakeWordBtn").style.display', None),
    # 唤醒开关仍 embed 态隐藏(独立启动块)
    ('document.getElementById("wakeBtn").style.display = "";', True),
    # saveWakeWord embed 感知(独立页不 postMessage + 差异化提示)
    ('var embed = document.body.classList.contains("embed");', True),
    ('"——在主站任意页呼唤即可唤出"', True),
    # embed 识别保留
    ('/[?&]embed=1/.test(location.search)', True),
    # 说明文案覆盖双态
    ('独立页修改保存后，下次打开主站任意页生效', True),
]
fails = []
for pat, expect in checks:
    n = html.count(pat)
    if expect is True and n < 1:
        fails.append("missing: %s" % pat[:60])
    elif expect is None and n != 0:
        fails.append("should-be-gone: %s (x%d)" % (pat[:60], n))
if fails:
    raise SystemExit("FAIL:\n  " + "\n  ".join(fails))
print("static-checks: ALL PASS (%d)" % len(checks))
