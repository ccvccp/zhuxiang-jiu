"""生产注入活动中心入口挂件到商城 index.html(幂等)

用法(服务器): python3 inject_activity_widget.py
锚点: voice-entry-widget script 标签后追加 activity-entry-widget
"""
import io

PATH = "/var/www/zxjiu/dist/index.html"
ANCHOR = '<script defer src=/js/voice-entry-widget.js?v=25></script>'
TAG = '<script defer src=/js/activity-entry-widget.js></script>'


def main():
    s = io.open(PATH, encoding="utf-8").read()
    if "activity-entry-widget" in s:
        print("already-present")
        return
    assert ANCHOR in s, "anchor(voice-entry-widget) not found"
    s = s.replace(ANCHOR, ANCHOR + TAG)
    io.open(PATH, "w", encoding="utf-8").write(s)
    print("injected")


if __name__ == "__main__":
    main()
