"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=29 -> v=30: 78号P1.5 补丁——桌面本地 TTS 路径补 [LAT]
play 打点([LAT-embed] 断链修复); widget VER v=26
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=29"
NEW = "src=/js/voice-wake-widget.js?v=30"


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
