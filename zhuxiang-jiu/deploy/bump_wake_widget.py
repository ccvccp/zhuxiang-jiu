"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=17 -> v=18: 段/连接生命周期解耦(X5 握手慢于段长竞态——静音
时 WS 仍 CONNECTING 被直接拆, auth 从未发出 recv=empty; 静音只
结束采集, 保活连接回补识别)随 widget VER v=17 双 bump
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=17"
NEW = "src=/js/voice-wake-widget.js?v=18"


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
