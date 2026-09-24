"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=33 -> v=34: G3 遗留定案——流式块 return_sample_rate 动态跟随(24kHz 三重印证); widget VER v=30
v=34 -> v=35: token 预检——beginSegment 开 WS 前检 authToken(),
    登出/换设备 wake 残留时空 token 不再报「识别通道: 鉴权失败」,
    直接提示「请先登录后再唤醒」(省一次 WS 往返)
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=34"
NEW = "src=/js/voice-wake-widget.js?v=35"


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
