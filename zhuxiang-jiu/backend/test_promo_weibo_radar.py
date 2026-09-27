"""36号·微博热搜真实源专项回归(CLI 桥, 2026-09-27 接入)

[A] 成本闸: WEIBO_RADAR_ENABLED 未开 → None(零消耗)
[B] 解析: 榜单 word/num/flag → 雷达条目(num 归一万级/标记入摘要)
[C] 失败回退: CLI 异常 → None(scan 回退 mock, 产出不中断)
[D] 扫描集成: weibo 真实条目入评分/风险/去重链(内存 store)

运行: python test_promo_weibo_radar.py
(零 C 消耗: CLI 全程 monkeypatch, 不真调)
"""
import asyncio
import json
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["WEIBO_RADAR_ENABLED"] = "1"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  OK {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  FAIL {name} -- {detail}")


class TestAGate:
    async def run(self):
        print("[A 成本闸]")
        from services import promo_radar_service as srv
        os.environ.pop("WEIBO_RADAR_ENABLED", None)
        r = await srv._fetch_weibo_real()
        record("未启用 → None(零消耗)", r is None, f"got={r}")
        os.environ["WEIBO_RADAR_ENABLED"] = "0"
        r2 = await srv._fetch_weibo_real()
        record("ENABLED=0 → None", r2 is None, f"got={r2}")
        os.environ["WEIBO_RADAR_ENABLED"] = "1"


class TestBParse:
    async def run(self):
        print("[B 榜单解析]")
        from services import promo_radar_service as srv
        board = {"data": [
            {"id": 1, "word": "中秋团圆宴白酒清单",
             "num": "5121870", "flag": "2"},
            {"id": 2, "word": "国风音乐节门票秒空",
             "num": 800000, "flag": 4},
            {"id": 3, "word": "", "num": 100},          # 无词跳过
            {"id": 4, "word": "低热词条", "num": 500},
        ]}
        srv._run_weibo_cli = \
            lambda *a, **k: asyncio.sleep(0, result=json.dumps(board))
        items = await srv._fetch_weibo_real()
        record("解析条目数(3/4)", items is not None
               and len(items) == 3, f"items={items}")
        if items:
            i0 = items[0]
            record("num 归一万级(512万)",
                   i0["heat"] == 512.2, f"heat={i0['heat']}")
            record("flag 标记入摘要(热)",
                   "（热）" in i0["summary"], i0["summary"])
            record("低热词条万级兜底",
                   items[2]["heat"] == 500.0,
                   f"heat={items[2]['heat']}")
            record("platform=weibo",
                   all(i["platform"] == "weibo" for i in items), "")

    async def _orig_run(self):
        return ""


class TestCFallback:
    async def run(self):
        print("[C 失败回退]")
        from services import promo_radar_service as srv

        async def boom(*a, **k):
            raise RuntimeError("weibo-cli exit=1: 模拟故障")

        srv._run_weibo_cli = boom
        r = await srv._fetch_weibo_real()
        record("CLI 异常 → None(回退 mock)", r is None, f"got={r}")

        async def empty(*a, **k):
            return json.dumps({"data": []})

        srv._run_weibo_cli = empty
        r2 = await srv._fetch_weibo_real()
        record("空榜单 → None", r2 is None, f"got={r2}")


class TestEBoard:
    async def run(self):
        print("[E 推送板 roundtrip(fake redis)]")
        from repositories import backend
        from services import promo_radar_service as srv

        store = {}

        class FakeClient:
            async def set(self, key, value, ex=None, nx=False):
                if nx and key in store:
                    return None
                store[key] = value
                return True

            async def get(self, key):
                return store.get(key)

        async def fake_client():
            return FakeClient()

        board = {"data": [
            {"id": 1, "word": "中秋宴用酒话题", "num": "6200000",
             "flag": "4"},
            {"id": 2, "word": "白酒文化讨论热", "num": 900000},
        ]}
        orig = (backend.is_redis_mode, backend.get_redis_client)
        backend.is_redis_mode = lambda: True
        backend.get_redis_client = fake_client
        try:
            stored, ttl = await srv.save_weibo_board(board["data"])
            record("板入库(stored=2, ttl=48h)",
                   stored == 2 and ttl == 48 * 3600,
                   f"stored={stored} ttl={ttl}")
            items = await srv._fetch_weibo_real()
            record("雷达读板免费消费(免 CLI/锁)",
                   items is not None and len(items) == 2
                   and items[0]["title"] == "中秋宴用酒话题",
                   f"items={items}")
            record("爆标记入摘要", "（爆）" in items[0]["summary"],
                   items[0]["summary"])
            try:
                await srv.save_weibo_board([{"num": "1"}])
                record("空板(无word)拒绝 ValueError", False,
                       "未抛异常")
            except ValueError:
                record("空板(无word)拒绝 ValueError", True, "")
        finally:
            backend.is_redis_mode, backend.get_redis_client = orig


class TestDScanIntegration:
    async def run(self):
        print("[D 扫描集成]")
        from repositories.promo_repository import PromoRepository
        from services import promo_radar_service as srv
        board = {"data": [
            {"id": 1, "word": "中秋宴用酒讨论热度攀升",
             "num": "6200000", "flag": "1"},
        ]}
        srv._run_weibo_cli = \
            lambda *a, **k: asyncio.sleep(0, result=json.dumps(board))
        service = srv.PromoRadarService()
        result = await service.scan(
            platforms=(srv.HOTSPOT_PLATFORM_WEIBO,))
        record("扫描成功(scanned=1)", result["scanned"] == 1,
               f"result={result}")
        rows = await PromoRepository().list_hotspots(
            platform=srv.HOTSPOT_PLATFORM_WEIBO)
        hit = [h for h in rows
               if h["title"] == "中秋宴用酒讨论热度攀升"]
        record("真实词条入库(platform=weibo)",
               bool(hit), f"rows={len(rows)}")
        if hit:
            h = hit[0]
            record("评分/品牌命中链生效",
                   0 < h["score"] <= 100
                   and isinstance(h["brandHits"], list),
                   f"score={h['score']} hits={h['brandHits']}")
            record("去重指纹在位", bool(h.get("fingerprint")), "")


async def main():
    tests = [TestAGate(), TestBParse(), TestCFallback(),
             TestEBoard(), TestDScanIntegration()]
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
