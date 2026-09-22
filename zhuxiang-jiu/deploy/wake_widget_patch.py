"""生产注入: 语音入口 widget 替换(voice-entry → voice-wake, 幂等)

变更(2026-09-22):
    index.html: <script defer src=/js/voice-entry-widget.js?v=25>
              → <script defer src=/js/voice-wake-widget.js?v=1>
    (悬浮球常驻 → 「小竹、小竹」语音唤醒; 旧 widget 文件保留供回滚)

用法(服务器): python3 wake_widget_patch.py
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = '<script defer src=/js/voice-entry-widget.js?v=25></script>'
NEW = '<script defer src=/js/voice-wake-widget.js?v=1></script>'


def main():
    s = io.open(INDEX, encoding="utf-8").read()
    if "voice-wake-widget" in s:
        print("already-patched")
        return
    assert OLD in s, "anchor(voice-entry-widget) not found"
    backup = INDEX + ".bak-wake"
    if not os.path.exists(backup):
        with open(INDEX, "rb") as f:
            data = f.read()
        with open(backup, "wb") as f:
            f.write(data)
    s = s.replace(OLD, NEW, 1)
    io.open(INDEX, "w", encoding="utf-8").write(s)
    print("patched: voice-entry-widget.js?v=25 -> voice-wake-widget.js?v=1")


if __name__ == "__main__":
    main()
