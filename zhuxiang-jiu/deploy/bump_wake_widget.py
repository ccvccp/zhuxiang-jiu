"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=23 -> v=24: V3.1 低能量自适应软增益(widget segFeed)随语音页
streamFeed 同款增益(VER v=22 双 bump)——治 X5 增益档不稳导致的
唤醒不中与"没听清"
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=23"
NEW = "src=/js/voice-wake-widget.js?v=24"


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
