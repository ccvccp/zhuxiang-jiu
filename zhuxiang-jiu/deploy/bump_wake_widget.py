"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=20 -> v=21: AHM 音频健康监测(链路感知驱动——零值帧哑流检出/
硬复位一次/30s 退避告警/复发检测)随 widget VER v=20 双 bump
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=20"
NEW = "src=/js/voice-wake-widget.js?v=21"


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
