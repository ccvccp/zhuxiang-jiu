"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=25 -> v=26: v3.3 双写回归修复(AHM 版起 segFeed 与 pushRing
段内双 push → 帧卡顿复制流 → 百炼必崩, 唤醒全灭的真正根因;
segFeed 收敛为纯发送器, 增益并入 pushRing 唯一入队口)
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=25"
NEW = "src=/js/voice-wake-widget.js?v=26"


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
