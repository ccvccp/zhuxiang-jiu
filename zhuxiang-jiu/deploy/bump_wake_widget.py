"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=16 -> v=17: 识别通道提示截断放宽至 80 字符(配合后端鉴权失败
结构化日志定位"刷新成功仍失败"根因)随 widget VER v=16 双 bump
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=16"
NEW = "src=/js/voice-wake-widget.js?v=17"


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
