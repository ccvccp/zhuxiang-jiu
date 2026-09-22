# -*- coding: utf-8 -*-
"""核验: widget WS 鉴权 token 过期自愈"""
import io
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
w = io.open(os.path.join(BASE, "js", "voice-wake-widget.js"),
            encoding="utf-8").read()

checks = [
    ("W1 tryRefreshToken 定义", "function tryRefreshToken() {" in w),
    ("W2 单飞+退避", "refBusy || now - refFailAt < 60000" in w),
    ("W3 refresh 端点+双源", "/api/auth/refresh" in w
     and 'localStorage.getItem("zhuxiang.auth"' in w
     and 'localStorage.getItem("auth_session"' in w),
    ("W4 双源回写", 'localStorage.setItem("zhuxiang.auth"' in w
     and 'localStorage.setItem("auth_session"' in w),
    ("W5 WS error 挂自愈", "tryRefreshToken();\n        teardownSeg();" in w),
    ("W6 VER v=6", 'var VER = "v=6";' in w),
    ("W7 回归: 唤醒匹配器", "buildWakeMatcher" in w),
    ("W8 回归: postMessage 协议", "xz-wake-on" in w and "xz-wake-word" in w),
]
fails = [n for n, ok in checks if not ok]
for n, ok in checks:
    print(("  PASS " if ok else "  FAIL ") + n)
if fails:
    raise SystemExit("FAILED: %d" % len(fails))
print("ALL PASS (%d)" % len(checks))
