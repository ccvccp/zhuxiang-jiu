"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=27 -> v=28: 切音色提示改 toast 浮层(验收实证 setMicStatus
被紧随的 TTS 合成状态同步覆盖不可见); widget VER 同步 v=24
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=27"
NEW = "src=/js/voice-wake-widget.js?v=28"


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
