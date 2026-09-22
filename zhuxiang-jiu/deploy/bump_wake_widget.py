"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=21 -> v=22: 语音页 v3 低延迟对话(TTS 分句流水线/语义感知
VAD 断句/首包过渡音/[LAT] 延迟埋点)随 widget VER v=21 双 bump
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=21"
NEW = "src=/js/voice-wake-widget.js?v=22"


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
