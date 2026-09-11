"""36号·热点源真实抓取适配器专项测试(P2 真实化预留的落地实现)

覆盖(promo_radar_service._fetch_real / _parse_hotspot_items):
    1.  未配置 KEY → None(mock-first 回退)
    2.  有 KEY 无 URL → None(端点未指定, 回退)
    3.  URL 拼接口径(plain query + key 参数)
    4.  聚合数据格式(result.data 嵌套列表解析)
    5.  天行数据格式(newslist + word/num 字段)
    6.  顶层列表格式(最简)
    7.  深层嵌套探测(result 为 dict 递归)
    8.  缺标题/非 dict 条目跳过
    9.  热度数值化(字符串数字/原始计数>10万归一到万)
    10. 缺省口径(velocity 0.5/persistence 12/平台标签摘要)
    11. HTTP 失败 → None(网络异常回退 mock, 产出不中断)
    12. 响应空列表 → None(无可解析条目)
    13. 真实条目风险否决(标题含"地震"→ scan 后 discarded)
    14. 品牌相关命中(真实标题含品牌词 → 评分链路正常)
    15. 解析确定性(同响应多次解析结构一致)

运行: python backend/test_promo_radar_real.py(自跑范式, 对齐 test_promo_radar_routes)
"""
import asyncio
import json
import os
import sys
from unittest import mock

# 确保使用内存模式(不触碰 Redis)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.promo_radar_service import (
    _fetch_real, _parse_hotspot_items,
    PromoRadarService, DEFAULT_RADAR_WEIGHTS,
)
from repositories.promo_repository import (
    HOTSPOT_STATUS_ACTIVE, HOTSPOT_STATUS_DISCARDED,
)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


class _FakeResponse:
    """urlopen 假响应(bytes 主体)"""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_urlopen(body):
    return mock.patch(
        "services.promo_radar_service.urllib.request.urlopen",
        return_value=_FakeResponse(json.dumps(body).encode("utf-8")))


def _patch_urlopen_raw(raw: bytes):
    return mock.patch(
        "services.promo_radar_service.urllib.request.urlopen",
        return_value=_FakeResponse(raw))


class _ScanRepo:
    """扫描链最小仓储替身(指纹全放行+id 递增+入库列表)"""

    def __init__(self):
        self.saved = []
        self._next = 0

    async def check_and_mark_fingerprint(self, fp: str) -> bool:
        return True

    async def next_id(self, name: str) -> int:
        self._next += 1
        return self._next

    async def save_hotspot(self, hotspot: dict) -> None:
        self.saved.append(hotspot)


class TestHotspotRealFetch:

    async def run(self):
        # ---------- 1-2 门控 ----------
        os.environ.pop("HOTSPOT_DOUYIN_API_KEY", None)
        os.environ.pop("HOTSPOT_DOUYIN_URL", None)
        record("门控-无KEY回退", _fetch_real("douyin") is None)

        os.environ["HOTSPOT_DOUYIN_API_KEY"] = "k"
        os.environ.pop("HOTSPOT_DOUYIN_URL", None)
        record("门控-有KEY无URL回退", _fetch_real("douyin") is None)

        # ---------- 3 URL 拼接 ----------
        os.environ["HOTSPOT_DOUYIN_API_KEY"] = "test-key-123"
        os.environ["HOTSPOT_DOUYIN_URL"] = "https://api.example.com/hot"
        with mock.patch(
            "services.promo_radar_service.urllib.request.urlopen",
            return_value=_FakeResponse(b'[{"title":"a"}]'),
        ) as m:
            items = _fetch_real("douyin")
            called = m.call_args[0][0].full_url
        record("URL拼接-key参数", called
               == "https://api.example.com/hot?key=test-key-123",
               f"实际{called}")
        record("URL拼接-条目解析", items is not None
               and items[0]["title"] == "a")

        # 平台环境(后续用例)
        os.environ["HOTSPOT_BAIDU_API_KEY"] = "bk"
        os.environ["HOTSPOT_BAIDU_URL"] = "https://api.example.com/baidu"

        # ---------- 4 聚合数据格式 ----------
        with _patch_urlopen({"reason": "success", "error_code": 0,
                             "result": {"data": [
                                 {"title": "中秋宴白酒热", "hot": "468"},
                                 {"title": "国风音乐节", "hot": "220"}]}}):
            items = _fetch_real("baidu")
        record("解析-聚合数据格式", items is not None and len(items) == 2
               and items[0]["title"] == "中秋宴白酒热"
               and items[0]["heat"] == 468.0)

        # ---------- 5 天行数据格式 ----------
        with _patch_urlopen({"code": 200, "newslist": [
            {"word": "非遗文化热", "num": 1234567},
            {"word": "露营攻略", "num": "890000"}]}):
            items = _fetch_real("baidu")
        record("解析-天行格式+计数归一", items is not None
               and len(items) == 2 and items[0]["title"] == "非遗文化热"
               and items[0]["heat"] == 123.5
               and items[1]["heat"] == 89.0,
               f"heat={items[0]['heat']}/{items[1]['heat']}")

        # ---------- 6 顶层列表 ----------
        with _patch_urlopen([{"title": "热点A"}, {"name": "热点B"}]):
            items = _fetch_real("baidu")
        record("解析-顶层列表", items is not None and len(items) == 2
               and items[1]["title"] == "热点B")

        # ---------- 7 深层嵌套 ----------
        with _patch_urlopen({"reason": "ok", "result": {
                "stat": "1", "data": [{"title": "嵌套热点"}]}}):
            items = _fetch_real("baidu")
        record("解析-嵌套递归", items is not None
               and items[0]["title"] == "嵌套热点")

        # ---------- 8 脏条目过滤 ----------
        with _patch_urlopen({"data": [
                {"hot": "100"}, {"title": "  "},
                {"title": "有效", "summary": "s"}, "not-a-dict"]}):
            items = _fetch_real("baidu")
        record("过滤-缺标题跳过", items is not None and len(items) == 1
               and items[0]["title"] == "有效")

        # ---------- 9-10 数值化与缺省 ----------
        with _patch_urlopen({"data": [{"title": "t", "hotvalue": "305.5"}]}):
            items = _fetch_real("baidu")
        record("数值化-字符串热度", items[0]["heat"] == 305.5)

        with _patch_urlopen({"data": [{"title": "无热度字段"}]}):
            items = _fetch_real("baidu")
        item = items[0]
        record("缺省-口径完整", item["platform"] == "baidu"
               and item["heat"] == 0.0 and item["velocity"] == 0.5
               and item["persistenceHours"] == 12
               and item["riskWord"] is None
               and item["summary"].startswith("[baidu热榜]"))

        # ---------- 11-12 失败回退(mock-first 铁律) ----------
        with mock.patch(
            "services.promo_radar_service.urllib.request.urlopen",
            side_effect=OSError("timeout"),
        ):
            record("回退-HTTP失败", _fetch_real("baidu") is None)

        with _patch_urlopen({"data": []}):
            record("回退-空结果", _fetch_real("baidu") is None)

        # ---------- 13 风险否决(scan 主链集成) ----------
        with _patch_urlopen({"data": [
                {"title": "某地地震最新进展", "hot": "600"},
                {"title": "中秋团圆宴白酒清单", "hot": "450"}]}), \
             mock.patch.object(PromoRadarService, "get_effective_weights",
                               return_value=dict(DEFAULT_RADAR_WEIGHTS)):
            svc = PromoRadarService.__new__(PromoRadarService)
            svc.repo = _ScanRepo()
            result = await svc.scan(platforms=("baidu",))
        statuses = {h["title"]: h["status"] for h in result["hotspots"]}
        record("主链-风险一票否决", result["discarded"] == 1
               and statuses["某地地震最新进展"] == HOTSPOT_STATUS_DISCARDED
               and statuses["中秋团圆宴白酒清单"] == HOTSPOT_STATUS_ACTIVE)

        # ---------- 14 品牌命中评分 ----------
        with _patch_urlopen({"data": [
                {"title": "白酒宴请文化讨论", "hot": "300"}]}), \
             mock.patch.object(PromoRadarService, "get_effective_weights",
                               return_value=dict(DEFAULT_RADAR_WEIGHTS)):
            svc2 = PromoRadarService.__new__(PromoRadarService)
            svc2.repo = _ScanRepo()
            result2 = await svc2.scan(platforms=("baidu",))
        hs = result2["hotspots"][0]
        record("主链-品牌命中评分", bool(hs["brandHits"])
               and hs["score"] > 0, f"hits={hs['brandHits']}")


class TestParseDeterminism:

    async def run(self):
        body = {"data": [{"title": "x", "hot": "100"}] * 3}
        a = _parse_hotspot_items("weibo", json.loads(json.dumps(body)))
        b = _parse_hotspot_items("weibo", json.loads(json.dumps(body)))
        record("确定性-重复解析一致", a == b and len(a) == 3)

        # 原始 bytes 主体(非 JSON dict)容错
        with _patch_urlopen_raw(b'plain text'):
            record("容错-非JSON响应回退", _fetch_real("baidu") is None)


async def main():
    print("=" * 60)
    print("36号·热点源真实抓取适配器 专项测试")
    print("=" * 60)
    for suite in (TestHotspotRealFetch(), TestParseDeterminism()):
        await suite.run()
    print("-" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
