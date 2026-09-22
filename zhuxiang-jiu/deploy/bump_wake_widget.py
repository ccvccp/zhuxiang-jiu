"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=15 -> v=16: 关面板恢复链手势过期修复(closePanel 手势栈内先试,
退避重试扩至 5 次, suspended 挂激活时明确提示)随 widget VER v=15
双 bump(①index 引用 v=16 刷 widget 本体)
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=15"
NEW = "src=/js/voice-wake-widget.js?v=16"


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
