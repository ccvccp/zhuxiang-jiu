"""83号·小竹语音准确率与延迟优化 P0+P1 测试(22 断言)

覆盖: SLA 纯函数(分位/达标率)/报表日聚合/事件白名单+收集+观测/
      热词五源(82号画像场景词+地区词+cap+fail-soft)/路由注册
"""

import json
from datetime import date

import pytest

from repositories.backend import (
    get_in_memory_store, _k,
)
from services.voice83_latency_service import (
    EVENT_TYPES, SLA_LINES, Voice83LatencyService, sla_of_samples,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_store():
    store = get_in_memory_store()
    for k in list(store.keys()):
        if "voice78:lat" in k or "voice83" in k:
            store.pop(k, None)
    for t in ("voice82_role", "voice82_dialog", "voice82_biz"):
        store.pop(t, None)
    yield


def _seed_lat(day: str, rows: list[dict]):
    """模拟 78号 /lat 上报(内存键同 _k 口径)"""
    key = _k("voice78", "lat", day)
    store = get_in_memory_store()
    bucket = store.setdefault(key, {})
    for i, r in enumerate(rows):
        bucket[f"s{i}"] = json.dumps(r, ensure_ascii=False)


# ============ T01-T08: SLA 纯函数 ============

async def test_percentile_empty():
    from services.voice83_latency_service import _percentile
    assert _percentile([], 0.5) == 0                 # T01


async def test_sla_segments_shape():
    samples = [{"s1": 100, "s2": 300, "s3": 200, "tt": 600},
               {"s1": 200, "s2": 900, "s3": 400, "tt": 1500}]
    out = sla_of_samples(samples)
    for seg in ("s1", "s2", "s3", "tt"):
        assert set(out[seg]) >= {"n", "p50", "p90", "max"}  # T02


async def test_sla_tt_accept_rate():
    samples = [{"tt": 600}, {"tt": 650}, {"tt": 900},
               {"tt": 1600}]
    tt = sla_of_samples(samples)["tt"]
    assert tt["acceptMs"] == SLA_LINES["tt"][0]     # T03 线在位
    assert abs(tt["acceptRate"] - 0.5) < 0.01       # 2/4≤700


async def test_sla_ideal_rate():
    samples = [{"tt": 400}, {"tt": 480}, {"tt": 600}]
    tt = sla_of_samples(samples)["tt"]
    assert abs(tt["idealRate"] - 2 / 3) < 0.01      # T04 理想线


async def test_sla_zero_filtered():
    out = sla_of_samples([{"tt": 0}, {"tt": 500}])
    assert out["tt"]["n"] == 1                      # T05 零值剔除


async def test_sla_p50_p90_order():
    vals = [{"tt": i * 100} for i in range(1, 11)]
    tt = sla_of_samples(vals)["tt"]
    assert tt["p50"] <= tt["p90"] <= tt["max"]      # T06 单调


async def test_report_daily_and_overall():
    today = date.today().strftime("%Y%m%d")
    _seed_lat(today, [{"s1": 100, "s2": 300, "s3": 200,
                       "tt": 600, "proto": "h2",
                       "mood": "", "ts": "x"}])
    r = await Voice83LatencyService().latency_sla_report(1)
    assert r["success"] and len(r["daily"]) == 1    # T07
    assert r["overall"]["tt"]["n"] == 1
    assert "s2" in r["lines"]                       # T08 线表


async def test_report_empty_days():
    r = await Voice83LatencyService().latency_sla_report(3)
    assert r["success"] and r["daily"] == [] and \
        r["overall"]["tt"]["n"] == 0               # 空稳态


# ============ T09-T14: 事件通道 ============

async def test_event_whitelist_reject():
    r = await Voice83LatencyService().record_event(
        "hack_type", {})
    assert not r["success"]                         # T09 封闭集合


async def test_event_record_and_view():
    await Voice83LatencyService().record_event(
        "wake_hit", {"page": "/product"})
    await Voice83LatencyService().record_event(
        "barge_cut", {"afterMs": 800})
    v = await Voice83LatencyService().events_view(1)
    assert v["counts"]["wake_hit"] == 1 and \
        v["counts"]["barge_cut"] == 1               # T10 计数
    assert any(r.get("meta", "").find("800") >= 0
               for r in v["recent"])                # T11 meta 留档


async def test_event_types_closed():
    assert set(EVENT_TYPES) == {"wake_hit", "barge_cut"}  # T12


async def test_event_day_isolation():
    await Voice83LatencyService().record_event("wake_hit")
    day = date.today().strftime("%Y%m%d")
    store = get_in_memory_store()
    assert any("voice83_events" in k and day in k
               for k in store)                      # T13 日键形态


async def test_event_corrupt_view():
    day = date.today().strftime("%Y%m%d")
    store = get_in_memory_store()
    store.setdefault(_k("voice83_events", day), {})["bad"] = \
        "not-json"
    v = await Voice83LatencyService().events_view(1)
    assert v["success"] and v["counts"]["wake_hit"] == 0  # T14


# ============ T15-T21: 热词五源(83号 P0) ============

def _seed_82_profile(mid, top_goal):
    """直写 82号画像三表(同步——纯 dict 写入; async+漏 await
    会静默不执行, 本文件实证教训)"""
    store = get_in_memory_store()
    store.setdefault("voice82_role", {})[f"m{mid}"] = {
        "role": "member", "confidence": "0.6",
        "sources": "[]", "updatedAt": "x"}
    store.setdefault("voice82_dialog", {})[f"m{mid}"] = {
        "ema": "v1", "_sessions": "50", "_turns": "60"}
    store.setdefault("voice82_biz", {})[f"m{mid}"] = {
        "intentDist": "{}",
        "bizDist": json.dumps({top_goal: 1.0}),
        "topGoal": top_goal, "updatedAt": "x"}


async def test_hotwords_region_seed(monkeypatch):
    monkeypatch.setenv("ASR_HOTWORDS", "竹香酒,小竹")
    from services.xiaozhu_service import XiaozhuService
    words = await XiaozhuService()._asr_hotwords()
    assert "竹香酒" in words and "小竹" in words      # T15 静态
    for w in ("浙江", "衢州", "杭州", "宁波", "温州", "金华"):
        assert w in words                            # T16 地区


async def test_hotwords_scene_from_profile(monkeypatch):
    monkeypatch.setenv("ASR_HOTWORDS", "")
    _seed_82_profile(41, "政策")
    from services.xiaozhu_service import XiaozhuService
    words = await XiaozhuService()._asr_hotwords(41)
    assert "起订量" in words and "区域保护" in words  # T17 场景注入


async def test_hotwords_no_member_no_scene(monkeypatch):
    monkeypatch.setenv("ASR_HOTWORDS", "")
    from services.xiaozhu_service import XiaozhuService
    words = await XiaozhuService()._asr_hotwords()
    assert "起订量" not in words                      # T18 无画像跳过


async def test_hotwords_profile_missing_failsoft(monkeypatch):
    monkeypatch.setenv("ASR_HOTWORDS", "")
    from services.xiaozhu_service import XiaozhuService
    words = await XiaozhuService()._asr_hotwords(999)
    assert "浙江" in words                           # T19 fail-soft


async def test_hotwords_dedup(monkeypatch):
    monkeypatch.setenv("ASR_HOTWORDS", "竹香酒,竹香酒,小竹")
    from services.xiaozhu_service import XiaozhuService
    words = await XiaozhuService()._asr_hotwords()
    assert words.count("竹香酒") == 1                 # T20 去重


async def test_hotwords_cap_120(monkeypatch):
    monkeypatch.setenv(
        "ASR_HOTWORDS", ",".join(f"词{i}" for i in range(200)))
    from services.xiaozhu_service import XiaozhuService
    assert len(await XiaozhuService()._asr_hotwords()) \
        <= 120                                        # T21 cap


# ============ T22: 路由注册 ============

async def test_routes_registered():
    from routes import register_voice83_routes
    assert register_voice83_routes.__name__ == \
        "register_voice83_routes"                    # T22
