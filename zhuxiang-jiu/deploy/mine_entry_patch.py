"""商城(生产 dist)我的页活动中心入口补丁 + 悬浮球下线(幂等)

变更(2026-09-22, 对齐 deploy/zjcity_patch.py 产物补丁惯例):
    1. index.html:
       - 备份 index.html.bak-mineentry
       - 移除活动悬浮球挂件 <script ...activity-entry-widget...>
       - app 引用 zjcity6 → zjcity7(chunk 换名破 immutable 缓存)
    2. js/app.zjcity6.js → js/app.zjcity7.js:
       内容映射 "3367":"376ebe98" → "3367":"mine-entry"
    3. js/3367.376ebe98.js → js/3367.mine-entry.js:
       「经营活动」区块首位插入「活动中心」入口(H5 window.location
       跳静态全功能页 /activity.html, 复用商城登录态)
    4. 删除 js/activity-entry-widget.js(悬浮球下线, 源码保留 git 历史)

用法(服务器): python3 mine_entry_patch.py
"""
import io
import os

# 本地预演可用 MALL_DIST 指向副本目录
DIST = os.environ.get("MALL_DIST", "/var/www/zxjiu/dist")

OLD_APP = os.path.join(DIST, "js/app.zjcity6.js")
NEW_APP = os.path.join(DIST, "js/app.zjcity7.js")
OLD_CHUNK = os.path.join(DIST, "js/3367.376ebe98.js")
NEW_CHUNK = os.path.join(DIST, "js/3367.mine-entry.js")
INDEX = os.path.join(DIST, "index.html")
WIDGET = os.path.join(DIST, "js/activity-entry-widget.js")

# 经营活动 sectionTitle 编译锚点(唯一)
TITLE_ANCHOR = '(0,E.jsx)(l.G7,{"className":m.sectionTitle,"children":"经营活动"}),'

# 活动中心入口(编译形态——照抄市级网店 entry 结构, onClick 走 H5 跳转)
ENTRY_JS = (
    '(0,E.jsxs)(l.G7,{"className":m.adminEntry,'
    '"onClick":function onClick(){window.location.href="/activity.html"},'
    '"children":['
    '(0,E.jsx)(l.G7,{"className":m.adminEntryIcon,"children":"🎁"}),'
    '(0,E.jsxs)(l.G7,{"className":m.adminEntryInfo,"children":['
    '(0,E.jsx)(l.G7,{"className":m.adminEntryName,"children":"活动中心"}),'
    '(0,E.jsx)(l.G7,{"className":m.adminEntryDesc,'
    '"children":"报名 · 抽奖 · 擂台赛 · 查我的报名"})]}),'
    '(0,E.jsx)(l.G7,{"className":m.adminEntryArrow,"children":"›"})]}),'
)

CHUNK_MAP_OLD = '"3367":"376ebe98"'
CHUNK_MAP_NEW = '"3367":"mine-entry"'

WIDGET_TAG = '<script defer src=/js/activity-entry-widget.js></script>'


def read(path):
    return io.open(path, encoding="utf-8").read()


def write(path, s):
    io.open(path, "w", encoding="utf-8").write(s)


def main():
    done = []

    # 幂等标记: 新 chunk 已存在即视为已打
    if os.path.exists(NEW_CHUNK) and os.path.exists(NEW_APP):
        print("already-patched")
        _cleanup_widget()
        return

    # 1. 我的页 chunk: 复制 + 插入活动中心入口
    src = read(OLD_CHUNK)
    assert TITLE_ANCHOR in src, "经营活动锚点未找到"
    assert '/activity.html' not in src, "疑似已插入(幂等标记命中)"
    patched = src.replace(TITLE_ANCHOR, TITLE_ANCHOR + ENTRY_JS, 1)
    assert patched != src
    write(NEW_CHUNK, patched)
    done.append(f"chunk -> {os.path.basename(NEW_CHUNK)}")

    # 2. app chunk: 复制 + chunk 映射换名
    app = read(OLD_APP)
    assert CHUNK_MAP_OLD in app, "app chunk 3367 映射未找到"
    write(NEW_APP, app.replace(CHUNK_MAP_OLD, CHUNK_MAP_NEW, 1))
    done.append(f"app -> {os.path.basename(NEW_APP)}")

    # 3. index.html: 备份 + 移除悬浮球 + app 引用更新
    if not os.path.exists(INDEX + ".bak-mineentry"):
        with open(INDEX, "rb") as f:
            backup = f.read()
        with open(INDEX + ".bak-mineentry", "wb") as f:
            f.write(backup)
    idx = read(INDEX)
    if WIDGET_TAG in idx:
        idx = idx.replace(WIDGET_TAG, "")
        done.append("widget-tag-removed")
    if "app.zjcity6.js" in idx:
        idx = idx.replace("app.zjcity6.js", "app.zjcity7.js")
        done.append("index-app-ref -> zjcity7")
    write(INDEX, idx)

    _cleanup_widget()
    print("patched:", "; ".join(done))


def _cleanup_widget():
    """悬浮球挂件文件下线(保留旧 chunk/app 供回滚)"""
    if os.path.exists(WIDGET):
        os.remove(WIDGET)
        print("widget-js-removed")


if __name__ == "__main__":
    main()
