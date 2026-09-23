"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=30 -> v=31: 78号P1·竹语流式 TTS 首声——/tts/stream SSE
端点 + 前端首子句 PCM 分片直灌(首声 60~110ms) + 三级回退
链 + /voices 携带 ttsStream 灰度开关; widget VER v=27
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=30"
NEW = "src=/js/voice-wake-widget.js?v=31"


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
