"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=31 -> v=32: 78号P2·O3/O4——decoded 旁路缓存(重复句零
decode 30~80ms) + 流式慢档垫场音(300ms 无首片起播「好的」,
体感首声慢档 950→~300ms); widget VER v=28
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=31"
NEW = "src=/js/voice-wake-widget.js?v=32"


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
