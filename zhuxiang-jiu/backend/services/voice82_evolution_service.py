"""82号·小竹语音对话逻辑学习进化模型 P2(在线强化: 信号+评分器+challenger)

P2 边界(计划 §四):
    - 奖励信号四维(§2.2): 任务完成/对话效率/用户体验/业务价值
      ——会话级确定性采集, 综合 reward 0-1
    - 评分器注册 44号学习域: voice82_strategy(batch 54,
      全站同步), auto_apply=False——学习永不自动上线
    - challenger: 参数候选版(白名单内确定性微调), promote 人工
      写 effective, discard 一键回滚
    - 三态: off(信号仍可采=观测; challenger 拒绝) / shadow
      (challenger 生成留痕, effective 不生效) / assist
      (effective_params 覆盖生效)
    - 灰度门槛观测: 影子会话 ≥200 且正反馈占优 → ready 提示

存储:
    voice82_signals     会话信号流(sessionId 键, 只追加)
    voice82_challenger  参数候选 {key: {params, createdAt}}
    voice82_effective   已 promote 生效参数 {key: {params, ...}}
"""

import json
import logging
import os

from core.helpers import ts

from repositories.backend import (
    get_in_memory_store, get_redis_client, is_redis_mode, _k,
)
from services.voice82_dialog_service import (
    PARAM_DEFAULTS, PARAM_KEYS, Voice82DialogService,
)

logger = logging.getLogger("voice82_evolution")

# 三态默认 off(宪法); 信号采集属观测面无门槛
SHADOW_MIN_SESSIONS = 200     # 灰度门槛(计划 §四 P2)
SHADOW_POSITIVE_RATIO = 0.55  # 正反馈占优阈值

TBL_SIGNALS = "voice82_signals"
TBL_CHALLENGER = "voice82_challenger"
TBL_EFFECTIVE = "voice82_effective"


def evolution_mode() -> str:
    """三态开关: off / shadow / assist(默认 off)"""
    m = os.environ.get("VOICE82_MODE", "off").strip().lower()
    return m if m in ("off", "shadow", "assist") else "off"


# ------------------------------------------------------------------
# 信号(纯函数, 确定性——LLM 永不产数字)
# ------------------------------------------------------------------

def session_signals(summary: dict) -> dict:
    """会话四维信号 + 综合 reward(0-1)

    summary: {turns, executedCount, repeatCount, interruptCount,
              fragmentCount, bizGoalsHit, exitedEarly,
              manualEscalated}
    """
    turns = max(int(summary.get("turns") or 0), 1)
    executed = int(summary.get("executedCount") or 0)
    biz_hits = int(summary.get("bizGoalsHit") or 0)

    # 任务完成(0.4): 执行率+业务目标命中
    task = min(1.0, executed / turns * 0.7 + min(biz_hits, 1) * 0.3)
    # 对话效率(0.25): 目标在少轮次完成——轮次越多效率越低
    eff = max(0.0, 1.0 - (turns - 1) * 0.15) \
        if (executed or biz_hits) else 0.0
    # 体验(0.2): 打断/纠错/碎片反面, 转人工与提前退出扣分
    neg = (int(summary.get("interruptCount") or 0)
           + int(summary.get("repeatCount") or 0) * 0.5
           + int(summary.get("fragmentCount") or 0) * 0.5) / turns
    exp = max(0.0, 1.0 - neg)
    if summary.get("manualEscalated"):
        exp *= 0.6
    if summary.get("exitedEarly"):
        exp *= 0.7
    # 业务价值(0.15): 招商域目标命中
    biz = min(1.0, biz_hits * 0.5)

    reward = round(0.4 * task + 0.25 * eff + 0.2 * exp + 0.15 * biz, 3)
    return {"task": round(task, 3), "efficiency": round(eff, 3),
            "experience": round(exp, 3), "bizValue": round(biz, 3),
            "reward": reward,
            "positive": reward >= 0.6}


# ------------------------------------------------------------------
# 评分器(44号学习域注册: voice82_strategy, batch 54)
# ------------------------------------------------------------------

class Voice82StrategyScorer:
    """小竹对话策略评分——会话信号四维加权(确定性)"""

    WEIGHTS = {
        "task_completion": 0.40,
        "dialog_efficiency": 0.25,
        "user_experience": 0.20,
        "biz_value": 0.15,
    }

    @staticmethod
    def score(signals: dict) -> float:
        """信号 → 0-100(供 44号学习回流消费)"""
        s = signals or {}
        return round(100.0 * (
            Voice82StrategyScorer.WEIGHTS["task_completion"]
            * float(s.get("task") or 0)
            + Voice82StrategyScorer.WEIGHTS["dialog_efficiency"]
            * float(s.get("efficiency") or 0)
            + Voice82StrategyScorer.WEIGHTS["user_experience"]
            * float(s.get("experience") or 0)
            + Voice82StrategyScorer.WEIGHTS["biz_value"]
            * float(s.get("bizValue") or 0)), 1)


# ------------------------------------------------------------------
# 服务
# ------------------------------------------------------------------

class Voice82EvolutionService:
    """信号采集 + challenger 生命周期 + 生效参数"""

    def __init__(self):
        self._dialog = Voice82DialogService()

    # ---- KV 原语(同 P0/P1 范式) ----

    async def _hgetall(self, table: str, key: str) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hgetall(_k(table, key))
            return {k.decode() if isinstance(k, bytes) else k:
                    v.decode() if isinstance(v, bytes) else v
                    for k, v in (raw or {}).items()}
        rec = (get_in_memory_store().get(table) or {}).get(key)
        return dict(rec) if rec else {}

    async def _hset(self, table: str, key: str, mapping: dict) -> None:
        enc = {k: (json.dumps(v, ensure_ascii=False)
                   if isinstance(v, (dict, list)) else str(v))
               for k, v in mapping.items()}
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k(table, key), mapping=enc)
            return
        get_in_memory_store().setdefault(table, {})[key] = dict(enc)

    async def _del(self, table: str, key: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.delete(_k(table, key)))
        d = get_in_memory_store().setdefault(table, {})
        return 1 if d.pop(key, None) is not None else 0

    # ---- 信号采集(观测面——off 也可采) ----

    async def record_signals(self, session_id, member_id,
                             summary: dict) -> dict:
        """会话结束钩子: 四维信号落流(fail-soft)"""
        sig = session_signals(summary)
        rec = {"sessionId": str(session_id),
               "memberId": str(member_id or ""),
               "turns": int(summary.get("turns") or 0),
               "ts": ts(),
               "score": Voice82StrategyScorer.score(sig), **sig}
        await self._hset(TBL_SIGNALS, str(session_id), rec)
        return {"success": True, **rec}

    async def signals_view(self, member_id=None,
                           limit: int = 20) -> dict:
        """信号观测(按 member 过滤/最近 N 条)"""
        out = []
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(TBL_SIGNALS, "*"))
            for k in keys[:500]:
                raw = await client.hgetall(k)
                rec = {kk.decode() if isinstance(kk, bytes) else kk:
                       vv.decode() if isinstance(vv, bytes) else vv
                       for kk, vv in (raw or {}).items()}
                out.append(rec)
        else:
            out = list((get_in_memory_store().get(TBL_SIGNALS)
                        or {}).values())
        if member_id is not None:
            out = [r for r in out
                   if str(r.get("memberId") or "") == str(member_id)]
        out.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        return {"success": True, "count": len(out),
                "signals": out[:limit]}

    # ---- challenger(决策面: shadow/assist; off → 拒绝) ----

    @staticmethod
    def _candidate_from_profile(profile: dict) -> dict | None:
        """画像 → 参数候选(白名单内确定性微调; 无动点=None)"""
        dlg = profile.get("dialog") or {}
        role = (profile.get("role") or {}).get("role")
        cand = dict(PARAM_DEFAULTS)
        moved = False
        if float(dlg.get("interruptRate") or 0) >= 0.20:
            cand["interrupt_first"] = "on"
            moved = True
        if float(dlg.get("repeatRate") or 0) >= 0.20:
            cand["probe_depth"] = 0
            cand["confirm_threshold"] = 0.5
            moved = True
        if float(dlg.get("fragmentRate") or 0) >= 0.25:
            cand["vad_silence_ms"] = 1400
            moved = True
        if role == "dealer":
            cand["probe_depth"] = 0
            moved = True
        if not moved:
            return None
        return {k: cand[k] for k in PARAM_KEYS}

    async def build_challenger(self, member_id) -> dict:
        """生成参数候选版(auto_apply=False——只留痕不上线)"""
        mode = evolution_mode()
        if mode == "off":
            return {"success": False, "error":
                    "决策面 off(VOICE82_MODE=off → 拒绝生成)"}
        prof = await self._dialog.get_profile(member_id)
        if not prof.get("success"):
            return {"success": False,
                    "error": prof.get("error") or "画像未构建"}
        profile = prof["profile"]
        cand = self._candidate_from_profile(profile)
        if cand is None:
            return {"success": True, "candidate": None,
                    "note": "画像无显著动点, 不硬造候选"}
        await self._hset(TBL_CHALLENGER, f"m{int(member_id)}", {
            "params": cand, "mode": mode,
            "fromProfile": json.dumps(
                {"role": (profile.get("role") or {}).get("role"),
                 "sessions": (profile.get("meta")
                              or {}).get("sessions")},
                ensure_ascii=False),
            "createdAt": ts()})
        return {"success": True, "candidate": cand,
                "mode": mode, "createdAt": ts()}

    async def promote(self, member_id) -> dict:
        """人工 promote: 候选 → effective(assist 模式才被消费)"""
        key = f"m{int(member_id)}"
        cand = await self._hgetall(TBL_CHALLENGER, key)
        if not cand:
            return {"success": False, "error": "无候选(先 build)"}
        params = json.loads(cand.get("params") or "{}")
        if not params:
            return {"success": False, "error": "候选为空"}
        await self._hset(TBL_EFFECTIVE, key, {
            "params": params, "promotedAt": ts(),
            "fromChallengerAt": cand.get("createdAt") or ""})
        await self._del(TBL_CHALLENGER, key)
        return {"success": True, "key": key, "params": params,
                "note": "effective 已落——仅 assist 模式消费"}

    async def discard(self, member_id,
                      reason: str | None = None) -> dict:
        """一键回滚: 弃候选 / 撤 effective"""
        key = f"m{int(member_id)}"
        n1 = await self._del(TBL_CHALLENGER, key)
        n2 = await self._del(TBL_EFFECTIVE, key)
        return {"success": True, "key": key,
                "clearedChallenger": n1, "clearedEffective": n2,
                "reason": reason or ""}

    async def effective_params(self, member_id) -> dict:
        """生效参数: assist 且有 effective → 覆盖; 否则 params_for"""
        prof = await self._dialog.get_profile(member_id)
        base = (prof.get("params") if prof.get("success")
                else dict(PARAM_DEFAULTS))
        if evolution_mode() != "assist":
            return {"mode": evolution_mode(), "params": base,
                    "effective": False}
        eff = await self._hgetall(
            TBL_EFFECTIVE, f"m{int(member_id)}" if member_id
            is not None else "")
        if not eff:
            return {"mode": "assist", "params": base,
                    "effective": False}
        params = json.loads(eff.get("params") or "{}")
        merged = {**base, **{k: params[k] for k in PARAM_KEYS
                             if k in params}}
        return {"mode": "assist", "params": merged,
                "effective": True,
                "promotedAt": eff.get("promotedAt")}

    # ---- 灰度门槛观测 ----

    async def shadow_status(self) -> dict:
        """影子就绪观测(≥200 会话且正反馈占优 → ready)"""
        view = await self.signals_view(limit=1000)
        sigs = view.get("signals") or []
        n = len(sigs)
        pos = sum(1 for s in sigs
                  if str(s.get("positive")) in ("True", "true", "1"))
        ready = (n >= SHADOW_MIN_SESSIONS
                 and n > 0 and pos / n >= SHADOW_POSITIVE_RATIO)
        return {"success": True, "sessions": n, "positive": pos,
                "positiveRatio": round(pos / n, 3) if n else 0.0,
                "minSessions": SHADOW_MIN_SESSIONS,
                "minPositiveRatio": SHADOW_POSITIVE_RATIO,
                "ready": ready,
                "note": "ready=影子达标可评估 promote(仍走人工)"
                if ready else "未达标(继续观察)"}
