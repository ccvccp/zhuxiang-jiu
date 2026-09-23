"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=32 -> v=33: 78号P2·H2 观察期——[LAT] 四段上报链(开关
/voices 下发, 带 nextHopProtocol 协议探测) + h2-watch.html
监控看板; widget VER v=29
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=32"
NEW = "src=/js/voice-wake-widget.js?v=33"


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
