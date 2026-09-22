"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=7 -> v=8: X5 麦克风独占死锁修复(面板/唤醒引擎麦克风交接)
随 widget VER v=7 双 bump(①index 引用 v=8 刷 widget 本体; ②
VER v=7 破 iframe 语音页缓存——语音页 pauseVoiceForHidden 同步改)
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=7"
NEW = "src=/js/voice-wake-widget.js?v=8"


def main():
    s = io.open(INDEX, encoding="utf-8").read()
    if NEW in s:
        print("already-bumped")
        return
    old_ver = OLD.rsplit("=", 1)[-1]
    new_ver = NEW.rsplit("=", 1)[-1]
    assert OLD in s, f"anchor {old_ver} not found"
    io.open(INDEX, "w", encoding="utf-8").write(s.replace(OLD, NEW, 1))
    print(f"bumped {old_ver} -> {new_ver}")


if __name__ == "__main__":
    main()
