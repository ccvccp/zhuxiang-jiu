"""生产 index.html: voice-wake-widget 版本号 bump(幂等)

v=1 -> v=2: 唤醒词可配置(⚙️ 设置 + buildWakeRe 重建)
widget 内容更新须同步 bump ?v=N 破 /js/ immutable 缓存。
"""
import io
import os

INDEX = os.environ.get("WAKE_INDEX", "/var/www/zxjiu/dist/index.html")
OLD = "src=/js/voice-wake-widget.js?v=1"
NEW = "src=/js/voice-wake-widget.js?v=2"


def main():
    s = io.open(INDEX, encoding="utf-8").read()
    if NEW in s:
        print("already-bumped")
        return
    assert OLD in s, "anchor v=1 not found"
    io.open(INDEX, "w", encoding="utf-8").write(s.replace(OLD, NEW, 1))
    print("bumped v=1 -> v=2")


if __name__ == "__main__":
    main()
