"""36 号 P1: 真实热榜解析器专项回归(百度 board 接入)

[A] 百度 board 三层嵌套解析 + index 名次热度估算
[B] 聚合 API 通行格式兼容回归(聚合数据/天行/顶层列表)
[C] _fetch_real 前置条件(无 KEY/无 URL 回退 None)

运行: python test_promo_hotspot_real.py
"""
import asyncio
import os
import sys

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


BAIDU_BOARD = {"success": True, "data": {"cards": [
    {"component": "tabTextList", "content": [
        {"content": [
            # 置顶条: isTop=True 且无 index(真实形态实证)
            {"word": "榜首热点", "url": "https://m.baidu.com/s?w=1",
             "isTop": True},
            {"word": "第二条热点", "url": "https://m.baidu.com/s?w=2",
             "isTop": False, "index": 1},
            {"word": "榜尾热点", "url": "https://m.baidu.com/s?w=50",
             "isTop": False, "index": 50},
        ]}]},
]}}

JUHE = {"reason": "success", "result": {"data": [
    {"title": "聚合标题一", "hot": 3200000},
    {"title": "聚合标题二", "num": "88 万"},
]}}

TIANXING = {"code": 200, "newslist": [
    {"title": "天行标题", "hot": 45}]}
TOP_LIST = [{"title": "顶层标题", "word": "冗余", "heat": 99}]


class TestABaiduBoard:
    async def run(self):
        print("[A 百度 board 解析(P1 接入)]")
        from services.promo_radar_service import (
            _parse_hotspot_items,
        )
        items = _parse_hotspot_items("baidu", BAIDU_BOARD)
        record("三层嵌套展开: 3 条全解析",
               len(items) == 3,
               f"n={len(items)}")
        record("标题: word 字段映射",
               items and items[0]["title"] == "榜首热点"
               and items[2]["title"] == "榜尾热点",
               str(items[:1]))
        record("index 热度估算: 置顶480/次席471/榜尾30",
               items and abs(items[0]["heat"] - 480.0) < 0.1
               and abs(items[1]["heat"] - 471.0) < 0.1
               and abs(items[2]["heat"] - 30.0) < 0.1,
               f"heat={[i.get('heat') for i in items]}")
        record("缺省字段: velocity 0.5/persistence 12",
               items and items[0]["velocity"] == 0.5
               and items[0]["persistenceHours"] == 12,
               "")
        record("平台标记: baidu",
               items and all(
                   i["platform"] == "baidu" for i in items),
               "")


class TestBLegacyFormats:
    async def run(self):
        print("[B 聚合格式兼容回归]")
        from services.promo_radar_service import (
            _parse_hotspot_items,
        )
        j = _parse_hotspot_items("weibo", JUHE)
        record("聚合数据(result.data): 解析+原始计数归万",
               len(j) == 2
               and abs(j[0]["heat"] - 320.0) < 0.1,
               f"heat={[i.get('heat') for i in j]}")
        t = _parse_hotspot_items("zhihu", TIANXING)
        record("天行(newslist): title/hot 映射",
               len(t) == 1 and t[0]["title"] == "天行标题"
               and t[0]["heat"] == 45.0,
               "")
        tl = _parse_hotspot_items("douyin", TOP_LIST)
        record("顶层列表: 解析正常",
               len(tl) == 1 and tl[0]["title"] == "顶层标题",
               "")
        empty = _parse_hotspot_items("xhs", {"foo": 1})
        record("无法解析: 返回空列表",
               empty == [],
               f"{empty}")


class TestCFetchGuard:
    async def run(self):
        print("[C _fetch_real 前置条件]")
        from services.promo_radar_service import _fetch_real
        for k in ("HOTSPOT_BAIDU_API_KEY",
                  "HOTSPOT_BAIDU_URL"):
            os.environ.pop(k, None)
        record("无 KEY: 返回 None(回退 mock)",
               _fetch_real("baidu") is None, "")
        os.environ["HOTSPOT_BAIDU_API_KEY"] = "k"
        record("有 KEY 无 URL: 返回 None",
               _fetch_real("baidu") is None, "")
        os.environ.pop("HOTSPOT_BAIDU_API_KEY", None)


async def main():
    tests = [TestABaiduBoard(),
             TestBLegacyFormats(), TestCFetchGuard()]
    for t in tests:
        await t.run()
    print("\n" + "=" * 56)
    for line in RESULTS:
        print(line)
    print("=" * 56)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
