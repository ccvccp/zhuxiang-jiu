"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=8 -> v=9: X5 启动双发麦克风竞态修复(iframe 初始 hide 通知 +
启动延迟 + 失败交互重试)随 widget VER v=8 双 bump(①index 引用
v=9 刷 widget 本体; 语音页本轮未动)
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=8"
NEW = "src=/js/voice-wake-widget.js?v=9"


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
