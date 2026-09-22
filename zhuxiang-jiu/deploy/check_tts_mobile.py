# -*- coding: utf-8 -*-
"""核验: 小竹 TTS 手机端云端分流修复 + 语音页内联语法"""
import io
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(BASE, "backend", "xiaozhu-voice.html")
OUT = os.environ.get("TEMP", "/tmp")

html = io.open(PAGE, encoding="utf-8").read()
blocks = re.findall(
    r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html, re.I)
for i, b in enumerate(blocks):
    io.open(os.path.join(OUT, "vv%d.js" % i), "w", encoding="utf-8").write(b)
print("scripts-extracted: %d" % len(blocks))

checks = [
    # 手机(触屏或微信)一律云端 TTS
    ("V1 分流条件 IS_WX || IS_TOUCH",
     "if (IS_WX || IS_TOUCH) return speakCloud(text, bubbleEl);" in html),
    ("V2 注释更新", "云端为可靠主轨" in html),
    # IS_TOUCH 定义仍存在(1733 行, var 提升运行时安全)
    ("V3 IS_TOUCH 定义在", 'window.matchMedia("(pointer:coarse)")' in html),
    # speakCloud 完整链路仍在
    ("V4 speakCloud 在", "function speakCloud(text, bubbleEl) {" in html),
    ("V5 ttsUrl 在", '"/api/xiaozhu/tts?text="' in html),
    # 回归: 竞态修复守卫仍在
    ("V6 stWS 实例守卫", "if (stWS !== ws)" in html),
    # 回归: 唤醒词设置全态
    ("V7 ⚙️ 全态", 'id="wakeWordBtn" onclick="showWakeWordSet()" style="cursor:pointer"' in html),
]
fails = [n for n, ok in checks if not ok]
for n, ok in checks:
    print(("  PASS " if ok else "  FAIL ") + n)
if fails:
    raise SystemExit("FAILED: %d" % len(fails))
print("ALL PASS (%d)" % len(checks))
