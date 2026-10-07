"""83号·小竹语音准确率与延迟优化(P0+P1)

P0: SLA 达标率层——复用 78号 [LAT] 四段日键(voice78:lat:*)
    零侵入, 加"对照验收线的达标率"口径(78号 stats 已有
    P50/P90 裸值, 本层补达标判定+超标计数)。
P1: 事件通道——唤醒命中/打断切断(面板 v82-B LAT-BAR 埋点
    现只进 console)并入服务端收集, 日键 voice83:events:*。

口径诚实说明(计划 §三):
    用户验收四线中端到端首响 tt 可直测(700/500);
    ASR 首字/LLM 首 Token/TTS 首包三条细粒度线受现有四段
    埋点粒度所限(s2 内含 ASR+LLM+组装, s3 内含 TTS+播放),
    本层给 s2/s3 观察期参考线, 细化埋点待 P2 按基线决策。
"""

import json
import logging
import os
from datetime import date, datetime

from repositories.backend import (
    get_in_memory_store, get_redis_client, is_redis_mode, _k,
)

logger = logging.getLogger("voice83_latency")

# 观察期参考线(首个为验收线, 次个为理想线)
SLA_LINES = {
    "tt": (700, 500),    # 端到端首响(用户验收线, 直测)
    "s2": (2000, 1000),  # 后端处理段(ASR+LLM+组装; 细化待 P2)
    "s3": (1000, 500),   # TTS+播放段(细化待 P2)
}
SLA_LABELS = {"tt": "端到端首响", "s2": "后端处理",
              "s3": "TTS+播放"}

# 事件类型白名单(封闭集合)
EVENT_TYPES = ("wake_hit", "barge_cut")
TTL_DAYS = 9

TBL_EVENTS = "voice83_events"


def voice83_mode() -> str:
    """三态开关(默认 off——观测面恒可用, 决策语义预留)"""
    return os.environ.get("VOICE83_MODE", "off").strip().lower()


# ------------------------------------------------------------------
# 纯函数(可单测)
# ------------------------------------------------------------------

def _percentile(sorted_vals: list[int], q: float) -> int:
    """P 分位(升序表; 空表 0)"""
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1,
              int(round(q * (len(sorted_vals) - 1))))
    return int(sorted_vals[idx])


def sla_of_samples(samples: list[dict]) -> dict:
    """四段样本 → SLA 聚合(确定性——LLM 永不产数字)"""
    out: dict = {}
    for seg in ("s1", "s2", "s3", "tt"):
        vals = sorted(int(s.get(seg) or 0) for s in samples
                      if int(s.get(seg) or 0) > 0)
        n = len(vals)
        entry = {"n": n,
                "p50": _percentile(vals, 0.50),
                "p90": _percentile(vals, 0.90),
                "max": vals[-1] if vals else 0}
        if seg in SLA_LINES:
            accept, ideal = SLA_LINES[seg]
            entry["acceptMs"] = accept
            entry["idealMs"] = ideal
            entry["acceptRate"] = round(
                sum(1 for v in vals if v <= accept) / n, 3) \
                if n else None
            entry["idealRate"] = round(
                sum(1 for v in vals if v <= ideal) / n, 3) \
                if n else None
        out[seg] = entry
    return out


# ------------------------------------------------------------------
# 服务
# ------------------------------------------------------------------

class Voice83LatencyService:
    """SLA 达标率聚合 + 事件收集(78号 数据源零侵入)"""

    async def _day_samples(self, day: str) -> list[dict]:
        """读 78号 日键四段样本(hash field=uid, value=JSON)"""
        key = _k("voice78", "lat", day)
        raw: dict = {}
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hgetall(key) or {}
        else:
            raw = (get_in_memory_store().get(key) or {})
        out = []
        for v in raw.values():
            v = v.decode() if isinstance(v, bytes) else v
            try:
                out.append(json.loads(v))
            except (TypeError, ValueError):
                continue
        return out

    async def latency_sla_report(self, days: int = 7) -> dict:
        """逐日 + 总并 SLA 报表(观测面)"""
        n = max(1, min(9, days))
        today = date.today()
        daily = []
        all_samples: list[dict] = []
        for i in range(n):
            d = today.fromordinal(today.toordinal() - i)
            day = d.strftime("%Y%m%d")
            samples = await self._day_samples(day)
            all_samples.extend(samples)
            if samples:
                daily.append({"day": day,
                              "segs": sla_of_samples(samples)})
        return {"success": True, "days": n,
                "lines": {k: {"label": SLA_LABELS[k],
                              **dict(zip(
                                  ("acceptMs", "idealMs"), v))}
                          for k, v in SLA_LINES.items()},
                "daily": daily,
                "overall": sla_of_samples(all_samples),
                "note": "s2/s3 为观察期参考线(细化埋点待 P2 基线"
                        "决策); tt 为用户验收线直测",
                "generatedAt": datetime.now().isoformat(
                    timespec="seconds")}

    # ---- 事件收集(P1: 面板 wake_hit/barge_cut fire-and-forget) ----

    async def record_event(self, event_type: str,
                           meta: dict | None = None) -> dict:
        """事件落地(类型白名单; 日键 hash + TTL)"""
        et = str(event_type or "").strip()
        if et not in EVENT_TYPES:
            return {"success": False,
                    "error": f"事件类型未注册({et})"}
        day = date.today().strftime("%Y%m%d")
        rec = {"type": et,
               "meta": json.dumps(meta or {}, ensure_ascii=False),
               "ts": datetime.now().isoformat(timespec="seconds")}
        import uuid as _uuid
        fid = _uuid.uuid4().hex[:10]
        if is_redis_mode():
            client = await get_redis_client()
            key = _k(TBL_EVENTS, day)
            await client.hset(key, fid,
                              json.dumps(rec, ensure_ascii=False))
            await client.expire(key, TTL_DAYS * 86400)
        else:
            store = get_in_memory_store()
            store.setdefault(_k(TBL_EVENTS, day), {})[fid] = \
                json.dumps(rec, ensure_ascii=False)
        return {"success": True, "type": et}

    async def events_view(self, days: int = 1,
                          limit: int = 100) -> dict:
        """事件观测(按类型计数 + 最近样本)"""
        n = max(1, min(9, days))
        today = date.today()
        counts = {t: 0 for t in EVENT_TYPES}
        recent: list[dict] = []
        for i in range(n):
            d = today.fromordinal(today.toordinal() - i)
            day = d.strftime("%Y%m%d")
            key = _k(TBL_EVENTS, day)
            raw: dict = {}
            if is_redis_mode():
                client = await get_redis_client()
                raw = await client.hgetall(key) or {}
            else:
                raw = (get_in_memory_store().get(key) or {})
            for v in raw.values():
                v = v.decode() if isinstance(v, bytes) else v
                try:
                    rec = json.loads(v)
                except (TypeError, ValueError):
                    continue
                if rec.get("type") in counts:
                    counts[rec["type"]] += 1
                recent.append(rec)
        recent = recent[:max(1, min(limit, 500))]
        return {"success": True, "days": n, "counts": counts,
                "recent": recent}
