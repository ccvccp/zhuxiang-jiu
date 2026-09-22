"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=13 -> v=14: 关面板恢复监听竞态修复(延迟+退避自动重试3次,
静默重试不再误弹提示)随 widget VER v=13 双 bump(①index 引用
v=14 刷 widget 本体)
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=13"
NEW = "src=/js/voice-wake-widget.js?v=14"


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
