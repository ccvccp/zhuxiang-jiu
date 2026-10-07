"""82号·小竹语音对话逻辑学习进化模型 P0(画像三表+角色推断+参数白名单)

P0 边界(计划 §四):
    - 零侵入: 从 48号既有表(voice48_sessions/turns/failures
      评分流)离线聚合画像, 不改任何语音链文件
    - 画像三表: role(角色)/dialog(对话习惯)/biz(业务偏好)
    - 参数白名单: 封闭集合(§2.3), 集合外永不可调
    - 红线: 不存对话原文(只存标签+统计); VOICE82_MODE 默认
      off 时 params 恒返回全局默认; 前 10 会话只采不调

存储(repositories.backend 双模式范式):
    voice82:role:{key}   角色画像 hash
    voice82:dialog:{key} 对话画像 hash
    voice82:biz:{key}    业务偏好 hash
    key = m{member_id}(P0 仅登录态; 游客指纹 P1 接入)
"""

import json
import logging
import os

from core.helpers import ts

from repositories.backend import (
    get_in_memory_store, get_redis_client, is_redis_mode, _k,
)
from repositories.xiaozhu_repository import Xiaozhu48Repository

logger = logging.getLogger("voice82_dialog")

# ------------------------------------------------------------------
# 铁律常量
# ------------------------------------------------------------------

SCORE_KIND = "turn_score"        # 48号 evolution 评分流 kind(对齐实测值)
COLD_START_SESSIONS = 10        # 前 N 会话只采不调(计划红线)
EMA_ALPHA = 0.3                 # 画像平滑系数

ROLES = ("visitor", "member", "dealer", "agent")   # P0 落前三, agent P1

# 参数白名单(封闭集合——集合外永不可调, 计划 §2.3)
PARAM_KEYS = ("vad_silence_ms", "tts_pace", "probe_depth",
              "confirm_threshold", "interrupt_first")
PARAM_DEFAULTS = {
    "vad_silence_ms": 800,
    "tts_pace": "normal",
    "probe_depth": 1,
    "confirm_threshold": 0.7,
    "interrupt_first": "off",
}
PARAM_BOUNDS = {                # clamp 边界(越界截断)
    "vad_silence_ms": (400, 2000),
    "probe_depth": (0, 2),
    "confirm_threshold": (0.5, 0.9),
}
PARAM_ENUMS = {
    "tts_pace": ("slow", "normal", "brisk"),
    "interrupt_first": ("on", "off"),
}

# 意图 → 业务域映射(子串匹配, 顺序敏感)
BIZ_DOMAINS = (
    ("政策", ("agent", "dealer", "policy", "join", "招商", "代理")),
    ("样品", ("sample", "样品", "试饮")),
    ("价格", ("price", "价格", "多少钱", "报价")),
    ("库存", ("stock", "inventory", "库存", "货量")),
    ("物流", ("logistics", "ship", "deliver", "物流", "发货")),
    ("结算", ("pay", "settle", "invoice", "结算", "付款", "发票")),
    ("售后", ("after", "refund", "service.complaint", "售后", "退")),
    ("产品", ("product", "商品", "酒", "wine")),
)

# 角色推断信号
DEALER_BIZ_WEIGHT = 0.30        # 政策域占比阈值
DEALER_INTENT_MIN = 3           # 代理类意图最少次数


def voice82_mode() -> str:
    """三态开关(P0: off=参数恒默认; shadow/assist 预留 P2)"""
    return os.environ.get("VOICE82_MODE", "off").strip().lower()


def profile_key(member_id) -> str | None:
    """画像键(P0 仅登录态——游客指纹 P1 接入, 此处 None 不画像)"""
    if member_id is None:
        return None
    return f"m{int(member_id)}"


# ------------------------------------------------------------------
# 纯函数: 角色推断 / 参数映射(可单测, 零 IO)
# ------------------------------------------------------------------

def infer_role(member_id, intent_counts: dict,
               biz_dist: dict) -> tuple[str, float, list[str]]:
    """角色推断(P0 纯规则: 登录态+意图分布; agent 留 P1)

    Returns: (role, confidence, sources)
    """
    if member_id is None:
        return "visitor", 1.0, ["未登录"]
    sources = ["已登录"]
    policy_w = float(biz_dist.get("政策") or 0.0)
    agent_hits = sum(n for k, n in intent_counts.items()
                     if any(w in str(k) for w in
                            ("agent", "dealer", "招商", "代理")))
    if policy_w >= DEALER_BIZ_WEIGHT or agent_hits >= DEALER_INTENT_MIN:
        conf = min(1.0, 0.6 + policy_w)
        sources.append(f"政策域权重{policy_w:.2f}≥{DEALER_BIZ_WEIGHT}"
                       if policy_w >= DEALER_BIZ_WEIGHT
                       else f"代理意图{agent_hits}次≥{DEALER_INTENT_MIN}")
        return "dealer", round(conf, 2), sources
    return "member", 0.6, sources


def _clamp(name: str, value):
    lo, hi = PARAM_BOUNDS[name]
    if isinstance(value, (int, float)):
        return type(PARAM_DEFAULTS[name])(max(lo, min(hi, value)))
    return PARAM_DEFAULTS[name]


def params_for(profile: dict | None, mode: str | None = None) -> dict:
    """节奏参数白名单映射(纯函数)

    红线: mode=off → 恒默认; 冷启动(<10 会话) → 恒默认;
          输出键集恒等于 PARAM_KEYS(集合外不可注入)
    """
    out = dict(PARAM_DEFAULTS)
    m = mode if mode is not None else voice82_mode()
    if m == "off" or not profile:
        return out
    meta = profile.get("meta") or {}
    if int(meta.get("sessions") or 0) < COLD_START_SESSIONS:
        return out
    dlg = profile.get("dialog") or {}
    role = (profile.get("role") or {}).get("role")
    # fragment 高 → 延长静默容忍; 轮间隔长 → 同向
    frag = float(dlg.get("fragmentRate") or 0.0)
    gap = float(dlg.get("avgTurnGapSec") or 0.0)
    if frag >= 0.25 or gap >= 8.0:
        out["vad_silence_ms"] = _clamp(
            "vad_silence_ms", 800 + 600 * min(1.0, frag * 2))
    # 纠错率高 → 少追问给选项 + 降低确认阈
    rep = float(dlg.get("repeatRate") or 0.0)
    if rep >= 0.20:
        out["probe_depth"] = _clamp("probe_depth", 0)
        out["confirm_threshold"] = _clamp("confirm_threshold", 0.5)
    # 打断率高 → 先结论后细节
    if float(dlg.get("interruptRate") or 0.0) >= 0.20:
        out["interrupt_first"] = "on"
    # 语速快(轮密度高) → brisk
    if float(dlg.get("turnsPerMin") or 0.0) >= 6.0:
        out["tts_pace"] = "brisk"
    # 经销商 → 直入政策零寒暄
    if role == "dealer":
        out["probe_depth"] = _clamp("probe_depth", 0)
    # 白名单收口: 键集恒定 + 枚举值校验
    for k in PARAM_ENUMS:
        if out[k] not in PARAM_ENUMS[k]:
            out[k] = PARAM_DEFAULTS[k]
    return {k: out[k] for k in PARAM_KEYS}


def biz_domain_of(intent: str) -> str | None:
    """意图 → 业务域(子串匹配首中)"""
    s = str(intent or "")
    for domain, words in BIZ_DOMAINS:
        if any(w in s for w in words):
            return domain
    return None


# ------------------------------------------------------------------
# 服务
# ------------------------------------------------------------------

class Voice82DialogService:
    """画像三表 + 离线聚合(P0 零侵入——只读 48号既有表)"""

    TBL_ROLE = "voice82_role"
    TBL_DIALOG = "voice82_dialog"
    TBL_BIZ = "voice82_biz"

    def __init__(self):
        self._repo = Xiaozhu48Repository()

    # ---- KV 原语(backend 双模式) ----

    async def _hgetall(self, table: str, key: str) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hgetall(_k(table, key))
            return {k.decode() if isinstance(k, bytes) else k:
                    v.decode() if isinstance(v, bytes) else v
                    for k, v in (raw or {}).items()}
        store = get_in_memory_store()
        d = store.setdefault(table, {})
        rec = d.get(key)
        return dict(rec) if rec else {}

    async def _hset(self, table: str, key: str, mapping: dict) -> None:
        enc = {k: (json.dumps(v, ensure_ascii=False)
                   if isinstance(v, (dict, list)) else str(v))
               for k, v in mapping.items()}
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k(table, key), mapping=enc)
            return
        store = get_in_memory_store()
        store.setdefault(table, {})[key] = dict(enc)

    async def _del(self, table: str, key: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.delete(_k(table, key)))
        store = get_in_memory_store()
        d = store.setdefault(table, {})
        return 1 if d.pop(key, None) is not None else 0

    @staticmethod
    def _num(v, default=0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    # ---- 48号源数据读取 ----

    async def _member_sessions(self, member_id,
                               limit: int = 50) -> list[dict]:
        """按 memberId 归属筛会话(扫表 P0 规模可承受;
        双模式: redis hgetall per key / 内存 dict 直读)"""
        rows: list[dict] = []
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("voice48",
                                        self._repo.TABLE_SESSIONS, "*"))
            for k in keys[:500]:
                raw = await client.hgetall(k)
                rec = {(kk.decode() if isinstance(kk, bytes) else kk):
                       (vv.decode() if isinstance(vv, bytes) else vv)
                       for kk, vv in (raw or {}).items()}
                if rec.get("memberId") == str(member_id):
                    rows.append(rec)
        else:
            store = get_in_memory_store()
            for rec in (store.get(self._repo.TABLE_SESSIONS)
                        or {}).values():
                if str(rec.get("memberId") or "") == str(member_id):
                    rows.append(rec)
        rows.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        return rows[:limit]

    async def _member_turns(self, session_ids: list) -> list[dict]:
        turns: list[dict] = []
        for sid in session_ids:
            try:
                turns.extend(await self._repo.list_turns(
                    str(sid), limit=200))
            except Exception:   # noqa: BLE001  fail-soft 残缺会话
                continue
        return turns

    async def _score_tracks(self, session_ids: list) -> dict:
        """评分流 track 分布(打断/纠错/碎片信号)"""
        counts = {"fragment": 0, "repeat": 0, "negative": 0,
                  "hook_timeout": 0, "total": 0}
        try:
            cases = await self._repo.list_records(
                self._repo.TABLE_FAILURES, limit=1000)
        except Exception:       # noqa: BLE001
            return counts
        sids = {str(s) for s in session_ids}
        for c in cases:
            if c.get("kind") != SCORE_KIND:
                continue
            if sids and str(c.get("sessionId") or "") not in sids:
                continue
            tr = str(c.get("track") or "")
            counts["total"] += 1
            if tr in counts:
                counts[tr] += 1
        return counts

    # ---- 画像构建 ----

    async def rebuild_profile(self, member_id,
                              limit: int = 50) -> dict:
        """离线聚合重建画像三表(幂等; EMA 与旧画像平滑)"""
        key = profile_key(member_id)
        if key is None:
            return {"success": False, "error": "未登录无画像(P0)"}
        sessions = await self._member_sessions(member_id, limit)
        sids = [s.get("sessionId") for s in sessions]
        turns = await self._member_turns(sids)
        tracks = await self._score_tracks(sids)

        # --- biz: 意图 → 业务域分布 ---
        intent_counts: dict = {}
        for t in turns:
            it = str(t.get("intent") or "")
            if it:
                intent_counts[it] = intent_counts.get(it, 0) + 1
        domain_counts: dict = {}
        for it, n in intent_counts.items():
            d = biz_domain_of(it)
            if d:
                domain_counts[d] = domain_counts.get(d, 0) + n
        total_dom = sum(domain_counts.values()) or 1
        biz_dist = {d: round(n / total_dom, 3)
                    for d, n in sorted(domain_counts.items(),
                                       key=lambda x: -x[1])}

        # --- role ---
        role, conf, sources = infer_role(
            member_id, intent_counts, biz_dist)

        # --- dialog: 习惯统计 ---
        n = len(turns) or 1
        lat = [self._num(t.get("latencyMs")) for t in turns]
        tss = sorted(str(t.get("ts") or "") for t in turns if t.get("ts"))
        gap = 0.0
        if len(tss) >= 2 and sessions:
            # 轮密度: 总轮数/总会话时长(分钟, 粗粒度够 P0)
            span_min = max(len(sessions), 1) * 1.0
            gap = round(span_min * 60 / n, 2)
        t_total = max(tracks["total"], 1)
        dialog_new = {
            "speechRate": round(sum(lat) / max(len(lat), 1) / 1000, 2),
            "avgTurnGapSec": gap,
            "turnsPerMin": round(n / max(len(sessions), 1), 2),
            "fragmentRate": round(tracks["fragment"] / t_total, 3),
            "repeatRate": round(tracks["repeat"] / t_total, 3),
            "interruptRate": round(tracks["negative"] / t_total, 3),
            "avgLatencyMs": round(sum(lat) / max(len(lat), 1), 0),
        }
        # EMA 平滑(与旧画像)
        old = await self._hgetall(self.TBL_DIALOG, key)
        dialog = {k: round((1 - EMA_ALPHA) * self._num(old.get(k), v)
                           + EMA_ALPHA * v, 3)
                  for k, v in dialog_new.items()} \
            if old and old.get("ema") else dialog_new

        meta = {"sessions": len(sessions), "turns": len(turns),
                "scoreTracks": tracks["total"],
                "updatedAt": ts(), "ema": "v1"}

        await self._hset(self.TBL_ROLE, key, {
            "role": role, "confidence": conf,
            "sources": json.dumps(sources, ensure_ascii=False),
            "updatedAt": ts()})
        await self._hset(self.TBL_DIALOG, key,
                         {**dialog, "ema": "v1",
                          "_sessions": len(sessions),
                          "_turns": len(turns)})
        await self._hset(self.TBL_BIZ, key, {
            "intentDist": json.dumps(intent_counts, ensure_ascii=False),
            "bizDist": json.dumps(biz_dist, ensure_ascii=False),
            "topGoal": max(biz_dist, key=biz_dist.get)
            if biz_dist else "",
            "updatedAt": ts()})

        return {"success": True, "key": key, "meta": meta,
                "role": {"role": role, "confidence": conf,
                         "sources": sources},
                "dialog": dialog, "biz": biz_dist}

    async def get_profile(self, member_id) -> dict:
        """读画像三表 + 参数映射(观测面; 不触发重建)"""
        key = profile_key(member_id)
        if key is None:
            return {"success": False, "error": "未登录无画像(P0)"}
        role = await self._hgetall(self.TBL_ROLE, key)
        dlg = await self._hgetall(self.TBL_DIALOG, key)
        biz = await self._hgetall(self.TBL_BIZ, key)
        if not role and not dlg:
            return {"success": False, "error": "画像未构建",
                    "hint": "POST /api/voice82/profile/{id}?rebuild=1"}
        profile = {
            "key": key,
            "role": {"role": role.get("role"),
                     "confidence": self._num(role.get("confidence")),
                     "sources": json.loads(role.get("sources") or "[]")},
            "dialog": {k: self._num(v) for k, v in dlg.items()
                       if k not in ("ema", "_sessions", "_turns")},
            "biz": json.loads(biz.get("bizDist") or "{}"),
            "topGoal": biz.get("topGoal") or "",
            "meta": {"sessions": int(self._num(dlg.get("_sessions"))),
                     "turns": int(self._num(dlg.get("_turns"))),
                     "updatedAt": role.get("updatedAt")},
        }
        return {"success": True, "mode": voice82_mode(),
                "profile": profile,
                "params": params_for(profile)}

    async def clear_profile(self, member_id) -> dict:
        """一键清除画像(隐私红线: 用户可清除)"""
        key = profile_key(member_id)
        if key is None:
            return {"success": False, "error": "未登录无画像"}
        n = 0
        for t in (self.TBL_ROLE, self.TBL_DIALOG, self.TBL_BIZ):
            n += await self._del(t, key)
        return {"success": True, "key": key, "cleared": n}
