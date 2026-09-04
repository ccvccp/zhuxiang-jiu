"""50号·小竹语音信值积分引擎核心服务(P0)

计划(docs/50号_小竹语音信值积分引擎实施计划.md §四/§六):
    引擎职责:
    ① 事件入账: 行为信号 → 系数链计算 → 台账+激励池
       (voiceprint × quality × group 三段乘性)
    ② 防刷封顶: 日上限=滚动 7 日日均基线×3, 超限溢出 ×0.1
       (封顶先于桥接——溢出只入池不进信值轨道)
    ③ L1 实时轨: 台账入账+风控状态暴露(供 47号消费),
       扣分 >20 → frozen(只冻结积分域, 不阻断语音入口)
    ④ ref 绑定: 每笔变动 exp-voice50-*(范式平移 49号 P3)

P0 口径:
    - VOICE50_MODE=off(默认)→ 钩子空转零影响
    - L1 四行为可触发(49号 consent/会话信号源);
      L2/L3 行为注册已就绪(计分入口统一 record_behavior,
      P2/P3 分期接信号源)
    - T+1 结算桥接(45号 deposit)P2 交付——P0 台账先行

设计红线(计划 §八):
    - 积分独立台账: 入信值必经 deposit 验真(P2)
    - 衰减不碰信值(池保鲜——P5)
    - 声纹 proxy 加成只入台账(桥接轨道硬编码拒绝——P2)
"""

import logging
import os
from datetime import UTC, datetime

from core.helpers import ts

from repositories.voice50_repository import (
    Voice50Repository,
)
from services.xiaozhu_voice50_rules import (
    VOICE_RULES, CAP_BASELINE_WINDOW, CAP_MULTIPLIER,
    CAP_OVERFLOW_RATE, L1_DEGRADE_THRESHOLD,
    VOICEPRINT_VERIFIED, VOICEPRINT_UNVERIFIED,
    QUALITY_THRESHOLD, VOICEPRINT_PROXY, VOICEPRINT_REAL,
    DEFAULT_MODE, rules_view,
    NEWCOMER_L3_BOOST, NEWCOMER_WINDOW_DAYS,
)

logger = logging.getLogger("xiaozhu_voice50")

# L1 风控域因子(voiceFactor——不入 45号, 计划 §三-2)
L1_FACTORS = {
    "voice_login", "voice_confirm",
    "voice_env_verify", "voice_antifraud_coop",
}

# 声纹系数作用域(P1 修正: 只有声纹比对类行为乘
# 声纹系数——v2.0 L1 表验证加成列; env_verify 的
# "一次通过 ×1.5"/coop 的"一致性 ×1.3"走 gains)
VOICEPRINT_FACTORS = {"voice_login", "voice_confirm"}


def voice50_mode_enabled() -> bool:
    """引擎总开关(默认 off——钩子空转, 零影响)"""
    return os.environ.get(
        "VOICE50_MODE", DEFAULT_MODE).lower() in (
        "on", "1", "true")


def _today_key() -> str:
    """日切键(UTC——与 49号 预算口径一致)"""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _r2(v: float) -> float:
    return round(float(v or 0), 2)


class Voice50Service:
    """语音信值积分引擎(台账轨 + L1 实时轨)"""

    def __init__(self,
                 repo: Voice50Repository = None):
        self.repo = repo or Voice50Repository()

    # --------------------------------------------------------
    # 核心计分入口
    # --------------------------------------------------------

    async def record_behavior(self, member_id: int,
                              behavior: str,
                              session_id: int = 0,
                              turn_seq: int = 0,
                              *,
                              voiceprint: str = "",
                              quality: float = None,
                              group_mult: float = 1.0,
                              gains: dict = None,
                              penalty: bool = False,
                              extra_mult: float = 1.0,
                              note: str = "") -> dict:
        """行为计分统一入口(系数链 → 封顶 → 台账+池)

        系数链(P0 确定性数学):
            final = base × voiceprint × quality × group
                    × extra_mult(P1——场景折扣如"多次失败
                    后成功 ×0.5")
        加成(P1 行为表 gains——显式命中才乘):
            gains 命中行为表 gain 键时乘入
        日限(P1 enforcement):
            当日该行为已计事件数 ≥ dailyCap → 不计分
            (返回 skipped——v2.0 "日限"口径: 超限不累计)
        防刷封顶(台账层):
            当日累计 cap = max(基线×3, 首日下限);
            超限部分 ×0.1 入池(溢出绝不进信值轨道)

        Returns:
            {evId, behavior, layer, baseScore, multipliers,
             finalScore, cappedScore, overflowScore,
             poolBalance, frozen, ref, skipped?}
        Raises:
            KeyError: 行为未注册
            ValueError: 参数非法/会员缺省/冻结期提交
        """
        rule = VOICE_RULES.get(behavior)
        if rule is None:
            raise KeyError(f"未注册语音行为 {behavior}")
        member_id = int(member_id or 0)
        if member_id <= 0:
            raise ValueError("语音积分需登录会员(memberId)")
        if voiceprint not in ("", VOICEPRINT_PROXY,
                              VOICEPRINT_REAL):
            raise ValueError(
                f"非法声纹模式 {voiceprint}")

        ledger = await self._ensure_ledger(member_id)
        if ledger.get("frozen"):
            raise ValueError(
                "语音积分已被冻结(累计扣分超限)——"
                "需人工复核恢复")

        # ---- P4 反作弊闸门(计分前置——fail-soft;
        #      命中即处置: 归零/扣分/冻结+台账留痕) ----
        gate_hit = await self._run_gates(
            member_id, behavior, note,
            evidence=str(note or ""))
        if gate_hit is not None:
            return gate_hit

        # ---- 日限 enforcement(P1) ----
        daily_cap = rule.get("dailyCap")
        if daily_cap is not None and not penalty:
            today_events = await self.repo.list_events(
                member_id=member_id,
                day_key=_today_key(), limit=5000)
            used_n = sum(
                1 for e in today_events
                if e.get("behavior") == behavior)
            if used_n >= int(daily_cap):
                logger.info(
                    "voice50_daily_cap_skip member=%s "
                    "behavior=%s cap=%s", member_id,
                    behavior, daily_cap)
                return {
                    "skipped": "dailyCapReached",
                    "behavior": behavior,
                    "dailyCap": daily_cap,
                    "finalScore": 0.0,
                    "cappedScore": 0.0,
                    "poolBalance": ledger.get(
                        "poolBalance"),
                    "note": f"日限 {daily_cap} 已满——"
                            "超限不累计(v2.0 日限口径)",
                }

        # ---- 系数链(P2: 乘性 gains + 加法 bonus——
        #      v2.0 "被采纳 +10"类加成为加法语义) ----
        base = float(rule["base"])
        vp_mult = self._voiceprint_mult(
            voiceprint, rule)
        q_mult = self._quality_mult(quality, penalty)
        g_mult = self._gains_mult(rule, gains or {})
        bonus_add = self._bonus_add(rule, gains or {})
        final = _r2(base * vp_mult * q_mult * g_mult
                    * float(group_mult or 1.0)
                    * float(extra_mult or 1.0)
                    + bonus_add)

        # 扣分项(负向事件——不参与封顶截断, 直接入账)
        if penalty:
            final = _r2(-abs(float(
                rule.get("penalty") or 0.0)))
            if final == 0.0:
                raise ValueError(
                    f"行为 {behavior} 无扣分项定义")

        # ---- 防刷封顶(仅正向; P3: L3 专属动态天花板——
        #      贡献层防资源集中: 基线×2(比全局 ×3 紧);
        #      新用户首月(首事件 30 天内)上浮 50% 冷启动) ----
        capped, overflow = final, 0.0
        if final > 0:
            newcomer = await self._is_newcomer(member_id)
            capped, overflow = await self._apply_cap(
                member_id, ledger, final,
                layer=rule["layer"],
                newcomer=newcomer)

        # ---- 事件落账(只追加——ref 绑定) ----
        ev_id = await self.repo.next_event_id()
        from services.xiaozhu_explainability_service \
            import build_ref
        ref = build_ref("voice50", ev_id,
                        f"{member_id}-{behavior}")
        event = {
            "evId": ev_id,
            "memberId": member_id,
            "sessionId": int(session_id or 0),
            "turnSeq": int(turn_seq or 0),
            "behavior": behavior,
            "layer": rule["layer"],
            "voiceFactor": rule.get("voiceFactor") or "",
            "targetFactor": rule.get("targetFactor") or "",
            "voiceprintMode": voiceprint or "",
            "baseScore": _r2(base),
            "multipliers": {
                "voiceprint": vp_mult,
                "quality": q_mult,
                "gains": g_mult,
                "group": float(group_mult or 1.0),
            },
            "finalScore": final,
            "cappedScore": capped,
            "overflowScore": overflow,
            "status": "settled",   # P0 实时台账;
            #  P2 起 L2/L3 为 pending → 结算转 settled
            "ref": ref,
            "note": str(note or "")[:120],
            "dayKey": _today_key(),
            "ts": ts(),
        }
        # L2/L3 事件标记 pending(T+1 结算资格——P2)
        if rule["layer"] in ("L2", "L3"):
            event["status"] = "pending"

        # ---- L1 降级判定(扣分累计 >20 → frozen;
        # L1 声纹/反欺诈扣分才计入降级——L2 礼貌扣分走
        # T+1 结算轨道处置, 不触实时冻结) ----
        degrade = False
        if final < 0 and rule.get("voiceFactor") \
                in L1_FACTORS:
            ledger["l1PenaltyTotal"] = _r2(
                float(ledger.get("l1PenaltyTotal") or 0)
                + abs(final))
            if ledger["l1PenaltyTotal"] \
                    > L1_DEGRADE_THRESHOLD:
                ledger["frozen"] = True
                degrade = True
                logger.warning(
                    "voice50_l1_degraded member=%s "
                    "penalty=%s", member_id,
                    ledger["l1PenaltyTotal"])

        # ---- 台账更新(激励池——正向入池: 封顶值+
        # 溢出×0.1(v2.0 §一-4 衰减不是丢弃); 扣分只记
        # 事件+降级计数, 池不为负红线) ----
        income = _r2(capped + overflow)
        if income > 0:
            ledger["poolBalance"] = _r2(
                float(ledger.get("poolBalance") or 0)
                + income)
            ledger["earnedTotal"] = _r2(
                float(ledger.get("earnedTotal") or 0)
                + income)
        else:
            ledger["poolBalance"] = _r2(
                max(0.0, float(
                    ledger.get("poolBalance") or 0)
                + capped))
        ledger["dayKey"] = _today_key()
        ledger["lastActiveAt"] = ts()
        await self.repo.save_event(event)
        await self.repo.save_ledger(ledger)

        logger.info(
            "voice50_recorded member=%s behavior=%s "
            "final=%s capped=%s overflow=%s pool=%s",
            member_id, behavior, final, capped,
            overflow, ledger["poolBalance"])
        return {
            "evId": ev_id, "behavior": behavior,
            "layer": rule["layer"],
            "baseScore": _r2(base),
            "multipliers": event["multipliers"],
            "finalScore": final,
            "cappedScore": capped,
            "overflowScore": overflow,
            "poolBalance": ledger["poolBalance"],
            "frozen": degrade,
            "ref": ref,
        }

    # --------------------------------------------------------
    # P4 反作弊闸门(计分前置——处置只作用积分域)
    # --------------------------------------------------------

    async def _run_gates(self, member_id: int,
                         behavior: str,
                         note: str,
                         evidence: str = "") -> dict | None:
        """五模式闸门(命中 → 处置执行+台账留痕)

        处置(v2.0 §六——只作用积分域, 语音入口不阻断):
        - tts_spoof: 当次积分归零(跳过计分)+L1 扣 10
        - scripted_repeat: 台账 frozen(当日冻结+人工复核)
        - shared_account: 台账 frozen(积分域锁定)
        - privacy_extraction: L2 扣 20(以事件负分入账)
        - budget_exhausted: 拒绝积分(引导调整)
        Returns:
            命中: 处置回包(调用方直接返回);
            未中: None(继续正常计分)。
        """
        try:
            from services.xiaozhu_voice50_gates import (
                Voice50GateService, PATTERN_TTS,
                PATTERN_SCRIPTED, PATTERN_SHARED,
                PATTERN_EXTRACTION, PATTERN_BUDGET,
            )
            gates = Voice50GateService(repo=self.repo)
            # 时序信号: 距同行为上一笔事件的间隔(秒)
            interval = None
            try:
                from datetime import datetime
                prior = await self.repo.list_events(
                    member_id=member_id, limit=500)
                same = [e for e in prior
                        if e.get("behavior") == behavior]
                if same:
                    t0 = datetime.fromisoformat(
                        str(same[-1].get("ts") or ""))
                    interval = max(
                        0.0, datetime.now(UTC).timestamp()
                        - t0.timestamp())
            except (ValueError, TypeError):
                interval = None
            hit = await gates.check(
                member_id, behavior,
                evidence=evidence or note,
                interval_sec=interval)
            if hit is None:
                return None
            pattern = hit["pattern"]
            adj = await gates.record_adjudication(
                member_id, pattern, hit.get("detail"),
                hit.get("action"), evidence=note)
            out = {
                "gated": True,
                "pattern": pattern,
                "action": hit.get("action"),
                "adjId": adj["adjId"],
                "finalScore": 0.0,
                "cappedScore": 0.0,
                "overflowScore": 0.0,
                "behavior": behavior,
                "note": "反作弊闸门处置——申诉路径见"
                        "处置台账(≤48h SLA)",
            }
            # 处置执行
            if pattern == PATTERN_TTS:
                # L1 扣 10(负向事件入账——不走闸门防递归,
                # 直接触发扣分管线)
                out["finalScore"] = -10.0
                out["cappedScore"] = -10.0
                await self._apply_gate_penalty(
                    member_id, behavior, -10.0,
                    note=f"gate:{pattern}")
            elif pattern in (PATTERN_SCRIPTED,
                             PATTERN_SHARED,
                             PATTERN_BUDGET):
                # 冻结(当日积分冻结/锁定/拒绝)
                ledger = await self._ensure_ledger(
                    member_id)
                ledger["frozen"] = True
                ledger["ts"] = ts()
                await self.repo.save_ledger(ledger)
                out["frozen"] = True
                if pattern == PATTERN_BUDGET:
                    out["note"] = ("隐私预算耗尽——拒绝积分, "
                                   "请在设置中调整隐私偏好"
                                   "或申请临时额度")
            elif pattern == PATTERN_EXTRACTION:
                # L2 扣 20(负向事件)
                out["finalScore"] = -20.0
                out["cappedScore"] = -20.0
                await self._apply_gate_penalty(
                    member_id, behavior, -20.0,
                    note=f"gate:{pattern}")
            logger.warning(
                "voice50_gate_hit member=%s behavior=%s "
                "pattern=%s", member_id, behavior, pattern)
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice50_gates_failsoft: %s",
                           exc)
            return None   # fail-soft 放行

    async def _apply_gate_penalty(self, member_id: int,
                                  behavior: str,
                                  delta: float,
                                  note: str) -> None:
        """闸门扣分直入账(负向事件——绕闸门防递归)"""
        from services.xiaozhu_voice50_rules import (
            VOICE_RULES,
        )
        rule = VOICE_RULES.get(behavior) or {}
        ev_id = await self.repo.next_event_id()
        from services.xiaozhu_explainability_service \
            import build_ref
        ref = build_ref("voice50", ev_id,
                        f"{member_id}-{behavior}-gate")
        ledger = await self._ensure_ledger(member_id)
        await self.repo.save_event({
            "evId": ev_id,
            "memberId": member_id,
            "sessionId": 0, "turnSeq": 0,
            "behavior": behavior,
            "layer": rule.get("layer") or "L1",
            "voiceFactor": rule.get("voiceFactor") or "",
            "targetFactor": rule.get("targetFactor") or "",
            "voiceprintMode": "",
            "baseScore": 0.0,
            "multipliers": {"gate": 1.0},
            "finalScore": delta,
            "cappedScore": delta,
            "overflowScore": 0.0,
            "status": "settled",
            "ref": ref,
            "note": str(note)[:120],
            "dayKey": _today_key(),
            "ts": ts(),
        })
        # L1 因子扣分计入降级累计
        if rule.get("voiceFactor") in L1_FACTORS:
            ledger["l1PenaltyTotal"] = _r2(
                float(ledger.get("l1PenaltyTotal") or 0)
                + abs(delta))
            if ledger["l1PenaltyTotal"] \
                    > L1_DEGRADE_THRESHOLD:
                ledger["frozen"] = True
        await self.repo.save_ledger(ledger)

    # --------------------------------------------------------
    # 系数链(确定性数学)
    # --------------------------------------------------------

    @staticmethod
    def _voiceprint_mult(voiceprint: str,
                         rule: dict) -> float:
        """声纹系数: proxy 加成只入台账(桥接轨道 P2 硬拒)

        P1 作用域修正: 仅声纹比对类行为(login/confirm——
        v2.0 L1 验证加成列)乘声纹系数; env_verify 的
        "一次通过 ×1.5"与 coop 的"一致性 ×1.3"走 gains
        (环境核验/风控应答的加成与生物特征解耦)。
        """
        if rule.get("voiceFactor") not in \
                VOICEPRINT_FACTORS:
            return 1.0
        if voiceprint == VOICEPRINT_REAL:
            return VOICEPRINT_VERIFIED
        if voiceprint == VOICEPRINT_PROXY:
            # proxy: 半程加成(确定性哈希代理不作凭证——
            # 计划 §三-3; 真声纹后全额)
            return 1.0 + (VOICEPRINT_VERIFIED - 1.0) * 0.5
        return VOICEPRINT_UNVERIFIED

    @staticmethod
    def _quality_mult(quality, penalty: bool) -> float:
        """质量系数(v2.0 §一-2: 置信度 ≥0.8 有效)

        置信度缺省(非意图类行为)按 1.0; <0.8 不计分
        (由调用方判断——引擎返回质量拒绝)。
        """
        if penalty or quality is None:
            return 1.0
        q = float(quality)
        if q < QUALITY_THRESHOLD:
            return 0.0
        return 1.0

    @staticmethod
    def _gains_mult(rule: dict, gains: dict) -> float:
        """行为加成链(乘性——显式命中才乘; v2.0 ×1.2/×1.5 类)"""
        mult = 1.0
        for key, hit in (gains or {}).items():
            if hit and key in (rule.get("gain") or {}):
                mult *= float(rule["gain"][key])
        return round(mult, 3)

    @staticmethod
    def _bonus_add(rule: dict, gains: dict) -> float:
        """行为加成(加法——v2.0 "被采纳 +10/+20" 列;
        命中 gains 键且注册表声明 bonus 时累加)"""
        add = 0.0
        for key, hit in (gains or {}).items():
            if hit and key in (rule.get("bonus") or {}):
                add += float(rule["bonus"][key])
        return round(add, 2)

    # --------------------------------------------------------
    # 防刷封顶(台账层——溢出 ×0.1 只入池)
    # --------------------------------------------------------

    async def _apply_cap(self, member_id: int,
                         ledger: dict, score: float,
                         layer: str = "",
                         newcomer: bool = False
                         ) -> tuple[float, float]:
        """封顶: 当日累计 ≤ max(基线×3, 首日下限)

        P3 L3 动态天花板(公平性保障):
        - L3 层单独收紧: cap = min(全局 cap, 基线×2)
          (贡献层防资源向高频用户集中——v2.0 §四);
        - 新用户首月(首事件 30 天内)L3 上浮 50%:
          基线×2×1.5=基线×3(与全局持平——冷启动激励)。
        基线=前 7 日日均(滚动); 新用户/冷启动首日下限
        30 分(保证可测)。溢出部分 ×0.1(CAP_OVERFLOW_RATE)。
        """
        today = _today_key()
        if ledger.get("dayKey") == today:
            used = float(ledger.get("usedToday") or 0)
        else:
            used = 0.0
        baseline = await self._rolling_baseline(member_id)
        cap = max(baseline * CAP_MULTIPLIER, 30.0)
        if layer == "L3":
            l3_cap = baseline * 2.0 * (
                NEWCOMER_L3_BOOST if newcomer else 1.0)
            cap = min(cap, max(l3_cap, 30.0))
        room = max(0.0, cap - used)
        if score <= room:
            ledger["usedToday"] = _r2(used + score)
            ledger["capBaseline"] = _r2(baseline)
            return score, 0.0
        capped = _r2(room)
        overflow = _r2((score - room)
                       * CAP_OVERFLOW_RATE)
        ledger["usedToday"] = _r2(used + capped)
        ledger["capBaseline"] = _r2(baseline)
        logger.info(
            "voice50_capped member=%s score=%s cap=%s "
            "capped=%s overflow=%s", member_id, score,
            cap, capped, overflow)
        return capped, overflow

    async def _is_newcomer(self, member_id: int) -> bool:
        """新用户判定(voice50 首笔事件 30 天内——冷启动)

        无事件按新用户(首笔即将发生)。
        """
        from datetime import datetime, timedelta
        evs = await self.repo.list_events(
            member_id=member_id, limit=1)
        if not evs:
            return True
        try:
            first = datetime.fromisoformat(
                str(evs[0].get("ts") or ""))
        except ValueError:
            return False
        return datetime.now(UTC) - first \
            < timedelta(days=NEWCOMER_WINDOW_DAYS)

    async def _rolling_baseline(self,
                                member_id: int) -> float:
        """滚动 7 日日均基线(前 7 日 settled 正向事件均值)"""
        events = await self.repo.list_events(
            member_id=member_id, limit=5000)
        day = _today_key()
        daily: dict = {}
        for e in events:
            if e.get("status") != "settled":
                continue
            dk = e.get("dayKey")
            if dk == day:
                continue
            score = float(e.get("cappedScore") or 0)
            if score > 0:
                daily[dk] = daily.get(dk, 0.0) + score
        if not daily:
            return 10.0   # 冷启动基线(日均 10 分)
        recent = sorted(daily.keys())[-CAP_BASELINE_WINDOW:]
        total = sum(daily[d] for d in recent)
        return _r2(total / max(1, len(recent)))

    # --------------------------------------------------------
    # 台账
    # --------------------------------------------------------

    async def _ensure_ledger(self,
                             member_id: int) -> dict:
        ledger = await self.repo.get_ledger(member_id)
        if ledger is None:
            ledger = {
                "memberId": member_id,
                "poolBalance": 0.0,
                "earnedTotal": 0.0,
                "offsetUsed": 0.0,
                "usedToday": 0.0,
                "capBaseline": 10.0,
                "l1PenaltyTotal": 0.0,
                "frozen": False,
                "dayKey": _today_key(),
                "decayHistory": [],
                "lastActiveAt": ts(),
            }
        elif ledger.get("dayKey") != _today_key():
            # 日切: usedToday 清零(封顶窗口滚动)
            ledger["dayKey"] = _today_key()
            ledger["usedToday"] = 0.0
        return ledger

    # --------------------------------------------------------
    # 视图(会员/管理端)
    # --------------------------------------------------------

    async def my_view(self, member_id: int) -> dict:
        """「我的语音积分」(第 17 指令/GET my 数据源)"""
        ledger = await self._ensure_ledger(member_id)
        await self.repo.save_ledger(ledger)
        events = await self.repo.list_events(
            member_id=member_id, limit=200)
        recent = [{
            "behavior": e.get("behavior"),
            "layer": e.get("layer"),
            "score": float(e.get("cappedScore") or 0),
            "ref": e.get("ref"),
            "ts": e.get("ts"),
        } for e in events[-8:]]
        return {
            "success": True,
            "memberId": member_id,
            "poolBalance": _r2(
                ledger.get("poolBalance")),
            "earnedTotal": _r2(
                ledger.get("earnedTotal")),
            "usedToday": _r2(
                ledger.get("usedToday")),
            "frozen": bool(ledger.get("frozen")),
            "recent": recent,
            "redlines": (
                "语音积分是激励池余额(≠信值分)——入信值"
                "必经 T+1 验真(45号 deposit)",
                "不用语音不扣分(反语音霸权)",
                "冻结只停积分获取, 语音功能照常",
            ),
            "note": "每笔变动均带 ref 可溯("
                    "explainability 绑定)",
        }

    # --------------------------------------------------------
    # L1 风控状态(47号消费口径——只读桥)
    # --------------------------------------------------------

    async def risk_state(self, member_id: int) -> dict:
        """L1 实时风控状态(风控域消费——不入 45号)"""
        ledger = await self._ensure_ledger(member_id)
        return {
            "memberId": member_id,
            "frozen": bool(ledger.get("frozen")),
            "l1PenaltyTotal": _r2(
                ledger.get("l1PenaltyTotal")),
            "poolBalance": _r2(
                ledger.get("poolBalance")),
            "note": "L1 实时轨(台账+风控域)——"
                    "不污染 45号法治因子",
        }

    # --------------------------------------------------------
    # L1 行为信号源 API(P1——供钩子/管理端/后续真实撤销流)
    # --------------------------------------------------------

    async def record_confirm_undo(self, member_id: int,
                                   session_id: int = 0,
                                   turn_seq: int = 0,
                                   note: str = "confirm-undo"
                                   ) -> dict:
        """确认后撤销(v2.0 L1 扣分项 -1)

        高敏操作语音确认后又撤销——负向事件(记事件+池
        不动; 真实撤销信号源由后续撤销流接入, P1 提供
        引擎 API+管理端补录通道)。
        """
        return await self.record_behavior(
            member_id, "voice_confirm",
            session_id, turn_seq, penalty=True,
            note=str(note)[:120])

    async def record_antifraud_coop(
            self, member_id: int,
            session_id: int = 0,
            turn_seq: int = 0,
            consistency_passed: bool = True,
            note: str = "") -> dict:
        """反欺诈配合响应(v2.0 L1——47号画像联动)

        触发条件(计划 §六 L1 表): 会员被 47号画像标记
        风险(tier ∈ watched/restricted/flagged——风控
        问询场景)时, 如实语音应答。
        consistency_passed: 内容一致性校验(绑定+查询
        完成=通过 → ×1.3; 回避/矛盾 → 扣分 -3)。

        Raises:
            ValueError: 会员未被风控标记(非问询场景
            不计配合分——防刷)
        """
        # 47号画像检查(绑定→trustId→tier)
        binding = await self._get_binding(member_id)
        tier = None
        if binding:
            tier = await self._risk47_tier(
                binding.get("trustId"))
        if tier in (None, "trusted"):
            raise ValueError(
                "该会员未被风控问询(47号画像 trusted/"
                "无画像)——非问询场景不计配合分")
        if consistency_passed:
            return await self.record_behavior(
                member_id, "voice_antifraud_coop",
                session_id, turn_seq,
                gains={"consistency": True},
                note=str(note or "risk-query")[:80]
                     + f"|risk47-tier:{tier}")
        # 回避/矛盾 → 扣分
        return await self.record_behavior(
            member_id, "voice_antifraud_coop",
            session_id, turn_seq, penalty=True,
            note=str(note or "risk-query")[:80]
                 + f"|risk47-tier:{tier}-回避")

    async def _get_binding(self, member_id: int) -> dict:
        """会员信值绑定(48号仓储——fail-soft None)"""
        try:
            from repositories.xiaozhu_repository import (
                Xiaozhu48Repository,
            )
            return await Xiaozhu48Repository().get_binding(
                member_id)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    async def _risk47_tier(trust_id) -> str | None:
        """47号画像 tier(风控域只读——异常 None)"""
        try:
            from services.trust_risk_profile_service \
                import (TrustRiskProfileService,
                        trust_level_of, tier_of)
            profile = await TrustRiskProfileService(
            ).get_profile(int(trust_id))
            return tier_of(trust_level_of(
                float(profile.get("riskEMA") or 0)))
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------
    # P3 L3 五行为 API(佐证/语料/问答/伴侣/FL 预留)
    # --------------------------------------------------------

    async def record_evidence(self, member_id: int,
                              evidence: str,
                              sources: list = None,
                              summary: str = ""
                              ) -> dict:
        """真伪鉴别辅助验证(v2.0 L3——per-claim 验真)

        佐证行为不走 T+1 聚合, 逐条走 45号验真三道关
        (证据≥8字/双源或权威源/意图中性):
        - 验真通过 → 采信 gains ×2(base 12 → 24);
        - 不通过 → 基础分(未采信——留事件待补源重试)。
        Returns:
            {**record_behavior 回包, verify: {verified,
            confidence, checks}}
        """
        from services.trust_radar_service import (
            verify_pipeline,
        )
        srcs = sources or ["voice_evidence"]
        v = verify_pipeline(
            "deposit", evidence, srcs,
            summary or "语音佐证提交")
        r = await self.record_behavior(
            member_id, "voice_evidence",
            gains={"accepted": v["verified"]},
            note=f"verify:{v['confidence']}"
                 f"|src:{','.join(srcs)[:40]}")
        r["verify"] = {
            "verified": v["verified"],
            "confidence": v["confidence"],
            "checks": v["checks"],
        }
        return r

    async def submit_corpus(self, member_id: int,
                            scenario: str) -> dict:
        """新场景语料捐赠提交(v2.0 L3——人工审核)

        捐赠即得基础 10 分(主动描述未覆盖场景);
        纳入训练集(审核采纳)再 +20(P2 加法 bonus)。
        场景描述 ≥8 字(与 45号证据口径一致)。
        """
        scenario = str(scenario or "").strip()
        if len(scenario) < 8:
            raise ValueError("场景描述需 ≥8 字符")
        corpus_id = await self.repo.next_corpus_id()
        await self.repo.save_corpus({
            "corpusId": corpus_id,
            "memberId": member_id,
            "scenario": scenario[:500],
            "status": "pending",
            "reviewedAt": "", "note": "",
            "ts": ts(),
        })
        # 捐赠基础分(不采纳也计入——v2.0 口径)
        r = await self.record_behavior(
            member_id, "voice_corpus_donate",
            note=f"corpus-{corpus_id}-submitted")
        return {"success": True,
                "corpusId": corpus_id,
                "status": "pending",
                "baseScore": r.get("finalScore"),
                "note": "审核采纳后 +20(纳入训练集)"}

    async def review_corpus(self, corpus_id: int,
                            adopted: bool,
                            note: str = "") -> dict:
        """语料审核(admin——采纳 +20 bonus)"""
        rec = await self.repo.get_corpus(corpus_id)
        if rec is None:
            raise KeyError(f"语料 {corpus_id} 不存在")
        if rec.get("status") != "pending":
            raise ValueError(
                f"已处理({rec.get('status')})")
        rec["status"] = ("adopted" if adopted
                         else "rejected")
        rec["reviewedAt"] = ts()
        rec["note"] = str(note or "")[:200]
        await self.repo.save_corpus(rec)
        if adopted:
            await self.record_behavior(
                rec.get("memberId"),
                "voice_corpus_donate",
                gains={"adopted": True},
                note=f"corpus-{corpus_id}-adopted")
        return {"success": True, **rec}

    async def record_qa(self, member_id: int,
                        content: str,
                        liked: bool = False) -> dict:
        """社区知识问答(v2.0 L3——内容安全过滤)

        语音回答其他用户关于信值的疑问:
        - 攻击性内容拒绝(内容安全红线);
        - 答案被点赞 ×1.5(gains liked);
        日限 40(dailyCap enforcement)。
        """
        content = str(content or "")
        from services.xiaozhu_service import ATTACK_WORDS
        if any(w in content for w in ATTACK_WORDS):
            raise ValueError(
                "内容含攻击性语言——社区问答拒绝计分")
        # P4: 诱导套取闸门(content 走证据检测)
        from services.xiaozhu_voice50_gates import (
            Voice50GateService,
        )
        gates = Voice50GateService(repo=self.repo)
        hit = await gates.check(
            member_id, "voice_community_qa",
            evidence=content)
        if hit is not None:
            adj = await gates.record_adjudication(
                member_id, hit["pattern"],
                hit.get("detail"), hit.get("action"),
                evidence=content)
            if hit["pattern"] == "privacy_extraction":
                await self._apply_gate_penalty(
                    member_id, "voice_community_qa",
                    -20.0,
                    note=f"gate:{hit['pattern']}")
                return {"gated": True,
                        "pattern": hit["pattern"],
                        "action": hit["action"],
                        "adjId": adj["adjId"],
                        "finalScore": -20.0,
                        "cappedScore": -20.0,
                        "note": "诱导套取处置——L2 扣 20"}
            return {"gated": True,
                    "pattern": hit["pattern"],
                    "action": hit["action"],
                    "adjId": adj["adjId"],
                    "finalScore": 0.0,
                    "note": "反作弊闸门处置"}
        return await self.record_behavior(
            member_id, "voice_community_qa",
            gains={"liked": bool(liked)},
            note="community-qa")

    async def check_companion(self,
                             member_id: int) -> dict:
        """长期语音伴侣关系核算(v2.0 L3——月度)

        条件: 连续 30 天日均有效交互(cappedScore>0
        事件)≥3 次 → 月度 100;
        多样性指数(近 30 天 distinct 行为数/14)>0.6
        → ×1.3; 月限 1(当月已发拒绝)。
        """
        from datetime import datetime, timedelta
        cutoff = (datetime.now(UTC)
                  - timedelta(days=30)).isoformat()
        evs = await self.repo.list_events(
            member_id=member_id, limit=10000)
        window = [e for e in evs
                  if str(e.get("ts") or "") >= cutoff
                  and float(e.get("cappedScore")
                           or 0) > 0]
        if not window:
            return {"success": True, "eligible": False,
                    "reason": "近 30 天无有效交互",
                    "dailyAvg": 0.0}
        daily_avg = round(len(window) / 30.0, 1)
        diversity = round(len(
            {e.get("behavior") for e in window}) / 14.0, 3)
        # 月限 1: 当月已有 companion 事件 → 拒绝
        month_prefix = _today_key()[:7]
        already = any(
            e.get("behavior") == "voice_companion"
            and str(e.get("ts") or "").startswith(
                month_prefix)
            for e in evs)
        if already:
            return {"success": True, "eligible": False,
                    "reason": "本月已发放(月限 1)",
                    "dailyAvg": daily_avg,
                    "diversity": diversity}
        if daily_avg < 3.0:
            return {"success": True, "eligible": False,
                    "reason": f"日均有效交互 {daily_avg}<3",
                    "dailyAvg": daily_avg,
                    "diversity": diversity}
        r = await self.record_behavior(
            member_id, "voice_companion",
            gains={"diversity": diversity > 0.6},
            note=f"companion|avg:{daily_avg}"
                 f"|div:{diversity}")
        return {"success": True, "eligible": True,
                "dailyAvg": daily_avg,
                "diversity": diversity,
                "award": r}

    async def record_fl_gradient(self, member_id: int,
                                 quality: float = 0.75
                                 ) -> dict:
        """联邦梯度贡献(v2.0 L3——FL 预留接口)

        预算充足前置(49号 P2——FL 上传消耗隐私预算,
        剩余不足拒绝); 梯度质量 >0.7 ×1.5;
        真实 FL 上传通道为外部待办——本 API 为预留
        接口(集成方调用时走此口径)。
        """
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        budget = await XiaozhuPrivacyService(
        ).budget_view(member_id)
        if float(budget.get("remaining") or 0) < 0.5:
            raise ValueError(
                "隐私预算不足(FL 梯度上传前置校验"
                f"剩余 {budget.get('remaining')})"
                "——请调整偏好或明日再试")
        return await self.record_behavior(
            member_id, "voice_fl_gradient",
            gains={"quality": float(quality) > 0.7},
            note=f"fl-gradient|q:{quality}"
                 f"|budget:{budget.get('remaining')}")

    # --------------------------------------------------------
    # P3 公平性桥(46号采样——L3 分布无歧视核验)
    # --------------------------------------------------------

    async def bridge_fairness(self) -> dict:
        """L3 日积分分布上报 46号公平性采样

        分组(v2.0 §四公平性保障——动态天花板/新用户
        上浮的分布核验): l3_high(≥30)/l3_mid(10-30)/
        l3_low(<10), 各组 score=当日 L3 正向积分均值;
        46号 MIN_GROUP_SAMPLES=5(不足组不上报——47号
        教训)。side-door 档案 voice50_l3_credits(不入
        SCORER_REGISTRY——46号 28 档案断言零改动红线)。
        """
        evs = await self.repo.list_events(limit=10000)
        today = _today_key()
        daily: dict = {}
        for e in evs:
            if e.get("dayKey") != today \
                    or e.get("layer") != "L3":
                continue
            score = float(e.get("cappedScore") or 0)
            if score > 0:
                daily.setdefault(
                    e.get("memberId"), []).append(score)
        if not daily:
            return {"success": True, "bridged": 0,
                    "groups": [],
                    "note": "今日无 L3 正向积分(无需上报)"}
        buckets = {"l3_high": [], "l3_mid": [],
                   "l3_low": []}
        for mid, scores in daily.items():
            total = sum(scores)
            if total >= 30:
                buckets["l3_high"].append(total)
            elif total >= 10:
                buckets["l3_mid"].append(total)
            else:
                buckets["l3_low"].append(total)
        # 46号 side-door 档案(48号 bridge 范式)
        from repositories.ai_governance_repository \
            import AiGovernance46Repository
        gov_repo = AiGovernance46Repository()
        scorer_id = "voice50_l3_credits"
        gov = await gov_repo.get_gov(scorer_id)
        if gov is None:
            gov = {"govId": await gov_repo.next_gov_id(),
                   "scorerId": scorer_id,
                   "label": "语音L3积分分布采样",
                   "module": "50语音积分",
                   "batch": 13, "status": "active",
                   "ownerNote": "50号公平性桥接专属档案"
                                "(side-door 入册)",
                   "frozenAt": "", "frozenBy": "",
                   "firstSeenAt": ts(),
                   "createdAt": ts(),
                   "lastSyncedAt": ts()}
        else:
            gov["status"] = "active"
            gov["lastSyncedAt"] = ts()
        await gov_repo.save_gov(gov)

        samples = []
        for group, totals in sorted(buckets.items()):
            if len(totals) < 5:   # MIN_GROUP_SAMPLES
                continue
            samples.append({
                "group": group,
                "score": round(
                    sum(totals) / len(totals), 1),
                "passed": None})
        if not samples:
            return {"success": True, "bridged": 0,
                    "groups": [],
                    "note": "各分组样本 <5(46号最小采样"
                            "口径)——暂不上报"}
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        result = await AiGovernanceFairnessService(
        ).submit_samples(
            scorer_id, samples, source="report")
        logger.info("voice50_fairness_bridged groups=%s",
                    len(samples))
        return {"success": True,
                "bridged": result.get("accepted"),
                "groups": [s["group"] for s in samples]}

    # --------------------------------------------------------
    # T+1 结算器(P2——L2/L3 聚合 → 45号 deposit 验真入信值)
    # --------------------------------------------------------

    async def settle_day(self, day_key: str = None,
                         member_id: int = None,
                         operator: str = "manual") -> dict:
        """结算一批 pending L2/L3 事件(45号 deposit 验真)

        口径(计划 §三-2/§六):
        - day_key=None → 结算 dayKey < 今日 的全部 pending
          (T+1 次日凌晨语义; 手动补偿可显式指定 day_key)
        - 聚合只计正向 cappedScore(溢出不进信值轨道——
          封顶先于桥接红线; 负向事件留台账不申报)
        - 验真通过 → 事件 settled + 批次 done(delta 留痕);
          验真拒收 → 事件保持 pending(可重试) + 批次
          rejected(reason——47号教训: summary 中性措辞)
        - frozen/unbound → 批次 skipped, 事件保持 pending
        幂等: settled/rejected 批次不再重拾; 事件状态
        pending→settled 迁移即防重复申报。

        Returns:
            {dayKey, batches: [...], counts: {done|
            rejected|skipped, credits, settledEvents}}
        """
        today = _today_key()
        pending = await self.repo.list_events(
            member_id=member_id, status="pending",
            limit=10000)
        if day_key is not None:
            targets = [e for e in pending
                       if e.get("dayKey") == day_key
                       and e.get("layer")
                       in ("L2", "L3")]
        else:
            targets = [e for e in pending
                       if (e.get("dayKey") or "")
                       < today
                       and e.get("layer")
                       in ("L2", "L3")]
        if not targets:
            return {"success": True, "dayKey": day_key,
                    "batches": [], "counts": {
                        "done": 0, "rejected": 0,
                        "skipped": 0, "credits": 0.0,
                        "settledEvents": 0},
                    "note": "无可结算事件(pending L2/L3)"}

        # 按 (member, layer, factor) 分组聚合
        groups: dict = {}
        for e in targets:
            key = (e.get("memberId"), e.get("layer"),
                   e.get("targetFactor")
                   or "ethics_evidence")
            groups.setdefault(key, []).append(e)

        batches = []
        counts = {"done": 0, "rejected": 0, "skipped": 0,
                  "credits": 0.0, "settledEvents": 0}
        for (mid, layer, factor), evs in \
                sorted(groups.items()):
            batch_id = await self.repo.next_batch_id()
            day_k = (day_key
                     if day_key is not None
                     else max(e.get("dayKey") or ""
                              for e in evs))
            base_rec = {
                "batchId": batch_id, "dayKey": day_k,
                "memberId": mid, "layer": layer,
                "factor": factor,
                "credits": 0.0, "eventCount": len(evs),
                "status": "skipped", "reason": "",
                "depositId": 0, "depositVerified": False,
                "depositDelta": 0.0,
                "evidence": "", "operator": operator,
                "ts": ts(),
            }
            # ① 冻结/未绑定 → 跳过(事件保持 pending)
            ledger = await self.repo.get_ledger(mid)
            if ledger is not None \
                    and ledger.get("frozen"):
                base_rec["reason"] = "frozen"
                await self.repo.save_settlement(base_rec)
                batches.append(base_rec)
                counts["skipped"] += 1
                continue
            binding = await self._get_binding(mid)
            if binding is None:
                base_rec["reason"] = "unbound"
                await self.repo.save_settlement(base_rec)
                batches.append(base_rec)
                counts["skipped"] += 1
                continue
            # ② 聚合正向 cappedScore(溢出/负向不入信值轨道)
            credits = _r2(sum(
                max(0.0, float(e.get("cappedScore") or 0))
                for e in evs))
            base_rec["credits"] = credits
            refs = [e.get("ref") or ""
                    for e in evs[:5]]
            if credits <= 0:
                # 无正向可申报(纯负向/零分事件)——
                # 闭环标记 settled(无申报必要)
                base_rec["reason"] = "no_positive_credits"
                await self.repo.save_settlement(base_rec)
                for e in evs:
                    e["status"] = "settled"
                    e["settledBatchId"] = batch_id
                    await self.repo.save_event(e)
                batches.append(base_rec)
                counts["skipped"] += 1
                counts["settledEvents"] += len(evs)
                continue
            # ③ 45号 deposit 申报(验真管线全继承:
            #    三道关/语义指纹/因果净贡献/UEBA 守门)
            evidence = (f"voice50 T+1 结算批次 {batch_id}"
                        f"(member {mid}, {len(evs)} 笔"
                        f"语音事件, 明细 ref: "
                        f"{';'.join(refs)})")
            summary = (f"语音交互行为积分聚合"
                       f"(批次 {batch_id}, "
                       f"{len(evs)} 笔事件)")
            deposit = None
            try:
                from services.trust_radar_service import (
                    TrustRadarService,
                )
                deposit = await TrustRadarService(
                ).submit_deposit(
                    binding["trustId"], layer, factor,
                    observed=credits, peer_baseline=0.0,
                    evidence=evidence, summary=summary,
                    sources=["voice50_engine",
                             "session_audit"],
                    voluntary=True,
                    verify_mode="v1")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "voice50_settle_deposit_fail "
                    "member=%s batch=%s: %s", mid,
                    batch_id, exc)
            if deposit and deposit.get("verified"):
                # 入账: 事件 settled + 批次 done
                base_rec.update({
                    "status": "done", "reason": "",
                    "depositId": deposit.get("depositId")
                    or 0,
                    "depositVerified": True,
                    "depositDelta": float(
                        deposit.get("delta") or 0),
                    "evidence": evidence,
                })
                await self.repo.save_settlement(base_rec)
                for e in evs:
                    e["status"] = "settled"
                    e["settledBatchId"] = batch_id
                    await self.repo.save_event(e)
                batches.append(base_rec)
                counts["done"] += 1
                counts["credits"] = _r2(
                    counts["credits"] + credits)
                counts["settledEvents"] += len(evs)
            else:
                # 拒收: 事件保持 pending(可重试——
                # 拒绝原因留批次; P4 处置台账接管)
                reason = ((deposit or {}).get("note")
                          or "验真未通过")
                checks = (deposit or {}).get("checks")
                if checks:
                    reason += "; " + "; ".join(
                        f"{c.get('stage')}:"
                        f"{c.get('note')}"
                        for c in checks)
                base_rec.update({
                    "status": "rejected",
                    "reason": str(reason)[:300],
                    "depositId":
                        (deposit or {}).get("depositId")
                        or 0,
                    "depositVerified": False,
                    "evidence": evidence,
                })
                await self.repo.save_settlement(base_rec)
                batches.append(base_rec)
                counts["rejected"] += 1
        return {"success": True, "dayKey": day_key,
                "batches": batches, "counts": counts}

    async def settlement_view(self, day_key: str = None,
                              member_id: int = None,
                              limit: int = 100) -> dict:
        """结算批次视图(管理端——批次/状态/拒收原因)"""
        rows = await self.repo.list_settlements(
            day_key=day_key, member_id=member_id,
            limit=limit)
        by_status: dict = {}
        for r in rows:
            s = r.get("status") or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
        return {"success": True, "total": len(rows),
                "byStatus": by_status,
                "batches": rows[-limit:],
                "note": "done=入账(事件 settled)/rejected="
                        "拒收(事件 pending 可重试)/skipped="
                        "冻结或未绑定或无正向——幂等由事件"
                        "状态迁移保证"}

    # --------------------------------------------------------
    # 冻结恢复(人工复核——admin)
    # --------------------------------------------------------

    async def unfreeze(self, member_id: int,
                       note: str = "") -> dict:
        """人工复核恢复(L1 降级只冻结积分域——恢复同域)"""
        ledger = await self._ensure_ledger(member_id)
        if not ledger.get("frozen"):
            raise ValueError("该会员语音积分未被冻结")
        ledger["frozen"] = False
        ledger["l1PenaltyTotal"] = 0.0
        ledger["ts"] = ts()
        await self.repo.save_ledger(ledger)
        logger.info("voice50_unfroze member=%s note=%s",
                    member_id, note[:60])
        return {"success": True, "memberId": member_id,
                "frozen": False,
                "note": str(note or "")[:120]}

    # --------------------------------------------------------
    # 规则(管理端——热更新留痕)
    # --------------------------------------------------------

    async def rules_admin_view(self) -> dict:
        view = rules_view()
        logs = await self.repo.list_rules_log(limit=20)
        view["recentUpdates"] = logs
        return view

    async def update_rule(self, behavior: str,
                          updates: dict,
                          operator: str = "admin") -> dict:
        """规则热更新(留痕——46号审批流范式简化版)

        可更新字段: base/dailyCap(其余结构层字段只读——
        系数链/因子映射改动须走发布流程)。
        """
        rule = VOICE_RULES.get(behavior)
        if rule is None:
            raise KeyError(f"未注册语音行为 {behavior}")
        allowed = {"base", "dailyCap"}
        unknown = set(updates or {}) - allowed
        if unknown:
            raise ValueError(
                f"不可更新字段: {sorted(unknown)}"
                f"(允许: {sorted(allowed)})")
        changes = {}
        if "base" in updates:
            base = float(updates["base"])
            if not 0 < base <= 200:
                raise ValueError("base 需在 (0, 200]")
            changes["base"] = {"from": rule["base"],
                               "to": _r2(base)}
            rule["base"] = _r2(base)
        if "dailyCap" in updates:
            cap = updates["dailyCap"]
            if cap is not None:
                cap = int(cap)
                if not 1 <= cap <= 1000:
                    raise ValueError(
                        "dailyCap 需在 [1, 1000] 或 null")
            changes["dailyCap"] = {
                "from": rule["dailyCap"], "to": cap}
            rule["dailyCap"] = cap
        log_id = await self.repo.next_log_id()
        await self.repo.save_rules_log({
            "logId": log_id,
            "behavior": behavior,
            "changes": changes,
            "operator": operator,
            "ts": ts(),
        })
        logger.info("voice50_rule_updated %s %s",
                    behavior, changes)
        return {"success": True, "behavior": behavior,
                "changes": changes,
                "logId": log_id}
