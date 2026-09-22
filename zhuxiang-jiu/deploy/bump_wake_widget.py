"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=19 -> v=20: X5 哑流修复(AEC 统一关——on/off 流交替触发音频路由
错乱致后续流静音; final 空转写提示"没听清"可见化)随 widget
VER v=19 双 bump
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=19"
NEW = "src=/js/voice-wake-widget.js?v=20"


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
