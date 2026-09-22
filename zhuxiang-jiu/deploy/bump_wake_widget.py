"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=18 -> v=19: 连测熔断误伤修复(段计数关面板清零 + 上限 6→10 +
提示改唤醒频率保护口径)随 widget VER v=18 双 bump
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=18"
NEW = "src=/js/voice-wake-widget.js?v=19"


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
