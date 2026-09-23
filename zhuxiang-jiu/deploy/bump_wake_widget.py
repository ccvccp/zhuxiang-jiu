"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=22 -> v=23: 唤醒引擎故障兜底——球常显(ballMini: 开启唤醒后
缩小半透明小点, 点击直达面板, 不再 display:none 死锁 UX)
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=22"
NEW = "src=/js/voice-wake-widget.js?v=23"


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
