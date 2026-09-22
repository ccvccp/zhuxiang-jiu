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
    ("W4 双源回写(所有存在源)",
     'if (localStorage.getItem("zhuxiang.auth")) {' in w
     and 'if (localStorage.getItem("auth_session")) {' in w
     and '"续期成功却再喊不醒"死循环' in w),
    ("W5 WS error 挂自愈", "tryRefreshToken();\n        teardownSeg();" in w),
    ("W6 成功后 30s 退避", "refOkAt < 30000" in w),
    ("W7 回归: 唤醒匹配器", "buildWakeMatcher" in w),
    ("W8 回归: postMessage 协议", "xz-wake-on" in w and "xz-wake-word" in w),
]
fails = [n for n, ok in checks if not ok]
for n, ok in checks:
    print(("  PASS " if ok else "  FAIL ") + n)

# 麦克风独占交接(X5 死锁修复)
v = io.open(os.path.join(BASE, "js", "voice-wake-widget.js"),
            encoding="utf-8").read()
p = io.open(os.path.join(BASE, "backend", "xiaozhu-voice.html"),
            encoding="utf-8").read()
checks2 = [
    ("M1 widget releaseMic 定义", "function releaseMic() {" in v),
    ("M2 openPanel 释放麦克风", "stopWake(); /* 面板打开期间暂停唤醒监听(面板内即语音会话) */" in v
     and "releaseMic(); /* X5 麦克风独占" in v),
    ("M3 closePanel 重拿麦克风", "enableWakeQuiet(); /* 关面板 → 重新拿麦克风恢复监听" in v),
    ("M4 xz-wake-on 不抢麦克风", "if (eng.stream) {\n        startWake();\n      }\n      return;" in v
     and "enableWakeQuiet();\n      }\n      return;" not in v),
    ("M5 xz-wake-off 用 releaseMic", "stopWake();\n      releaseMic();" in v),
    ("M6 widget VER v=10", 'var VER = "v=10";' in v),
    ("M7 语音页隐藏无条件释放(pauseVoiceForHidden)",
     "cloudActive = false;\n  hardStopCloud(); /* 无条件硬停+释放麦克风(幂等安全) */" in p
     and "S.hidden = true;\n  clearTimeout(S.hfTimer); /* 停续听排程 */\n  cloudActive = false;" in p),
]
for n, ok in checks2:
    print(("  PASS " if ok else "  FAIL ") + n)
fails += [n for n, ok in checks2 if not ok]
# 启动双发麦克风竞态修复(iframe 初始 hide + 启动延迟 + 交互重试)
checks3 = [
    ("S1 iframe onload 初始 hide 通知",
     'fr.onload = function () {' in v
     and 'if (!panel.classList.contains("open")) { notifyFrame("hide"); }' in v),
    ("S2 启动延迟 1.2s", "setTimeout(enableWakeQuiet, 1200);" in v),
    ("S3 失败交互重试", "function armRetryOnInteract() {" in v
     and 'document.addEventListener("touchend", h, true);' in v
     and "麦克风暂不可用（可能被占用）——点一下屏幕任意处即恢复唤醒" in v),
    ("S4 suspended ctx 交互 resume",
     'if (eng.ctx.state === "suspended") { armCtxResume(); }' in v
     and "function armCtxResume() {" in v),
    ("S5 续期提示再喊一次",
     'showTip("登录已自动续期——再喊一声「小竹、小竹」即唤醒");' in v),
]
for n, ok in checks3:
    print(("  PASS " if ok else "  FAIL ") + n)
fails += [n for n, ok in checks3 if not ok]
if fails:
    raise SystemExit("FAILED: %d" % len(fails))
print("ALL PASS (%d)" % (len(checks) + len(checks2) + len(checks3)))
