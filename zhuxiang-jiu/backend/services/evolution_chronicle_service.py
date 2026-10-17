"""进化编年史 · 陈酿指数 · Elo 角斗场(全站进化生态地图 v1.0 短板补齐)

三项能力共用一条脊柱——统一 evolution_record 事件流:
    - 编年史: 全域进化事件统一流水(Redis LIST, 容量 500),
      消除 agent75/kb57/voice82/ai_learning 各自留痕不可追溯的割裂
    - Elo 角斗场: 从事件流派生 challenger vs baseline 对战积分
      (K=32; approve/promote/publish=挑战者胜, reject/discard=负,
      rollback=事后基线胜), 跨域可比的"进化胜率"视角
    - 陈酿指数: 域级聚合健康分(胜率+活跃度-新鲜度惩罚, ζ 衰减),
      5 分钟缓存, 回答"哪个模块最需要进化"

设计约束:
    - 仅依赖 repositories.backend(避免服务循环导入); 埋点方一律
      try/except fail-soft(观测层永不阻断业务)
    - 非 redis 模式回退进程内 deque(仅本地干跑, 不承诺持久)
    - 事件 summary 由埋点方控制, 观测面 GET 公开(对齐 zyh/arch 口径)
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

from repositories.backend import (
    _k, get_redis_client, is_redis_mode,
)

logger = logging.getLogger(__name__)

CHRONICLE_CAP = 500          # 编年史容量(条)
AGING_CACHE_SECONDS = 300    # 陈酿指数缓存
_ELO_K = 32
_ELO_BASE = 1200

# 事件类型 → Elo 对战结果(None=非对战事件)
_ELO_OUTCOME = {
    "approve": "win", "promote": "win", "publish": "win",
    "learn_promoted": "win",
    "reject": "lose", "discard": "lose",
    "rollback": "baseline_win",
}

_mem_chronicle: list[dict] = []   # 非 redis 干跑兜底


async def record(domain: str, ev_type: str, target: str,
                 summary: str, extra: dict | None = None) -> dict | None:
    """写入一条进化事件(fail-soft, 返回事件 dict; 异常吞并只记日志)"""
    try:
        now = datetime.utcnow()
        day = now.strftime("%Y%m%d")
        seq = await _next_seq(day)
        event = {
            "id": f"EVO-{day}-{seq:04d}",
            "ts": now.isoformat() + "Z",
            "domain": domain,
            "type": ev_type,
            "target": (target or "")[:80],
            "summary": (summary or "")[:160],
            "extra": extra or {},
        }
        if is_redis_mode():
            client = await get_redis_client()
            await client.lpush(_k("evolution", "chronicle"),
                               json.dumps(event, ensure_ascii=False))
            await client.ltrim(_k("evolution", "chronicle"), 0, CHRONICLE_CAP - 1)
        else:
            _mem_chronicle.insert(0, event)
            del _mem_chronicle[CHRONICLE_CAP:]
        await _elo_apply(domain, ev_type)
        return event
    except Exception as exc:
        logger.warning("evolution_chronicle_record_failed: %s", exc)
        return None


def record_bg(domain: str, ev_type: str, target: str,
              summary: str, extra: dict | None = None) -> None:
    """同步上下文埋点入口(有事件循环时 fire-and-forget, 否则丢弃)"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(record(domain, ev_type, target, summary, extra))
    except RuntimeError:
        pass  # 无事件循环(纯同步测试)——不阻断


async def _next_seq(day: str) -> int:
    if is_redis_mode():
        client = await get_redis_client()
        return int(await client.incr(_k("evolution", "seq", day)))
    return len(_mem_chronicle) + 1


async def list_records(limit: int = 50, domain: str | None = None) -> list[dict]:
    """读编年史(新→旧, 支持域过滤)"""
    limit = max(1, min(limit, CHRONICLE_CAP))
    if is_redis_mode():
        client = await get_redis_client()
        raw = await client.lrange(_k("evolution", "chronicle"), 0, limit - 1)
        items = [json.loads(x) for x in raw]
    else:
        items = list(_mem_chronicle[:limit])
    if domain:
        items = [e for e in items if e.get("domain") == domain]
    return items


async def _elo_apply(domain: str, ev_type: str) -> None:
    outcome = _ELO_OUTCOME.get(ev_type)
    if not outcome:
        return
    if is_redis_mode():
        client = await get_redis_client()
        key = _k("evolution", "elo", domain)
        raw = await client.get(key)
    else:
        raw = None
    state = json.loads(raw) if raw else {
        "domain": domain, "challengerElo": _ELO_BASE, "baselineElo": _ELO_BASE,
        "matches": 0, "challengerWins": 0}
    state["domain"] = domain
    rc, rb = state["challengerElo"], state["baselineElo"]
    exp_c = 1.0 / (1.0 + 10 ** ((rb - rc) / 400.0))
    score_c = {"win": 1.0, "lose": 0.0, "baseline_win": 0.0}[outcome]
    state["challengerElo"] = round(rc + _ELO_K * (score_c - exp_c))
    state["baselineElo"] = round(rb + _ELO_K * ((1 - score_c) - (1 - exp_c)))
    state["matches"] += 1
    state["challengerWins"] += 1 if score_c == 1.0 else 0
    if is_redis_mode():
        await client.set(key, json.dumps(state, ensure_ascii=False))


async def elo_table() -> list[dict]:
    """Elo 角斗场积分榜(全域)"""
    if is_redis_mode():
        client = await get_redis_client()
        keys = await client.keys(_k("evolution", "elo", "*"))
        rows = []
        for k in keys or []:
            raw = await client.get(k)
            if raw:
                rows.append(json.loads(raw))
        rows.sort(key=lambda r: -r.get("challengerElo", 0))
        return rows
    return []


async def aging_index() -> dict:
    """陈酿指数(域级聚合健康分, 5 分钟缓存)

    公式(生态地图 §五口径):
        domainScore = 50
            + 30 × challenger胜率                (酒体醇厚: 进化有效性)
            + 20 × min(1, 7日事件数/10)          (窖池活跃: 持续进化)
            - min(20, 距上次事件小时数 / 6)       (ζ 新鲜度惩罚)
        总指数 = 各域 domainScore 加权均值(等权)
    """
    if is_redis_mode():
        client = await get_redis_client()
        cached = await client.get(_k("evolution", "aging"))
        if cached:
            parsed = json.loads(cached)
            if parsed.get("cachedAt"):
                return parsed
    events = await list_records(limit=CHRONICLE_CAP)
    elos = {r["domain"]: r for r in await elo_table()}
    now = datetime.utcnow()
    domains: dict[str, dict] = {}
    for e in events:
        d = domains.setdefault(e["domain"], {"count7d": 0, "lastTs": None})
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", ""))
        except ValueError:
            continue
        if now - ts <= timedelta(days=7):
            d["count7d"] += 1
        if d["lastTs"] is None or ts > d["lastTs"]:
            d["lastTs"] = ts
    rows = []
    for domain, d in domains.items():
        elo = elos.get(domain) or {"matches": 0, "challengerWins": 0}
        win_rate = (elo["challengerWins"] / elo["matches"]
                    if elo.get("matches") else 0.5)
        fresh_hours = ((now - d["lastTs"]).total_seconds() / 3600.0
                       if d["lastTs"] else 48.0)
        score = 50 + 30 * win_rate + 20 * min(1.0, d["count7d"] / 10) \
            - min(20.0, fresh_hours / 6)
        rows.append({
            "domain": domain, "score": round(max(0.0, min(100.0, score)), 1),
            "winRate": round(win_rate, 3),
            "events7d": d["count7d"],
            "hoursSinceLast": round(fresh_hours, 1),
            "matches": elo.get("matches", 0),
        })
    rows.sort(key=lambda r: -r["score"])
    overall = round(sum(r["score"] for r in rows) / len(rows), 1) if rows else 0.0
    result = {
        "overall": overall,
        "domains": rows,
        "formula": "50 + 30*winRate + 20*min(1,events7d/10) - min(20,hours/6)",
        "cachedAt": datetime.utcnow().isoformat() + "Z",
        "sampleSize": len(events),
    }
    if is_redis_mode():
        await client.set(_k("evolution", "aging"),
                         json.dumps(result, ensure_ascii=False),
                         ex=AGING_CACHE_SECONDS)
    return result


def clear_mem() -> None:
    """清空进程内兜底(测试用)"""
    _mem_chronicle.clear()
