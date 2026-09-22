"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=6 -> v=7: widget WS 鉴权 token 过期自愈随 widget 内容更新
双 bump(①index 引用 v=7 刷 widget 本体 /js/ immutable; 语音页
本轮未动, VER 仅为统一链路)
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=6"
NEW = "src=/js/voice-wake-widget.js?v=7"


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
