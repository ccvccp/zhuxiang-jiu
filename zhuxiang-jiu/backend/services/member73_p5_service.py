"""73号·AI智能会员体验大模型 P5 元认知收官服务
(member73_p5_service)

规划(docs/73号_AI智能会员体验大模型_创新规划方案.md
§五 5.3/§七 P5):
    ① 漂移检测(三信号: 触达总量异常
       [骚扰化]/响应率骤降[形式失效]/
       撤销率异常[信任受损]——确定性
       阈值公式)
    ② 免疫监控与冻结(信号数≥2 →
       触达面自动冻结+告警留痕;
       解冻人工专属+环境变量双保险)
    ③ 红队四向量(RT-01 打扰轰炸/
       RT-02 诱导升级[紧迫词检测]/
       RT-03 越权代办/RT-04 画像投毒
       ——构造→断言→留痕; 失守→冻结)
    ④ 进化日志(形式学习/负反馈/漂移/
       冻结/解冻/红队六类留痕)
    ⑤ 模型状态(mode/kill/免疫/版本)

铁律(规划 §九):
    - 冻结为保护方向自动(触达面
      关闭, 观测面保留); 解冻人工
      专属(MEMBER73_IMMUNITY=1
      双保险——71号 P8 范式平移)
    - 漂移检测/免疫监控为快环——
      不受 MODE 影响; 红队为决策面
      (off=409 由路由层门控)
    - LLM 禁入(信号判定=确定性
      阈值公式)
    - 红队自造数据隔离(不污染既有
      会员——造红队专用会员)

异常约定(71号口径):
    KeyError → 404(批次不存在)
    ValueError → 409(状态机/阈值/
        解冻授权)
"""

import logging
import os

from core.helpers import ts

from repositories.member73_repository import (
    Member73Repository,
)
from services.member73_registry import (
    DRIFT_SIGNALS,
    DRIFT_THRESHOLDS,
    EVOLUTION_LOG_KINDS,
    IMMUNITY_FREEZE_RULES,
    IMMUNITY_STATES,
    IMMUNITY_UNFREEZE_ENV,
    L1_AUTONOMY_DOMAINS,
    MODEL_VERSION,
    REDTEAM_VECTORS,
    URGENCY_WORDS,
    current_mode, is_kill,
)

logger = logging.getLogger("member73_p5_service")


def _now_day() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()[:10]


def urgency_detected(text: str) -> bool:
    """紧迫词检测(RT-02——hint 文案
    禁用域命中即阳性)"""
    return any(w in (text or "")
               for w in URGENCY_WORDS)


class Member73P5Service:
    """73号 P5 元认知(漂移/免疫/红队/
    进化日志/模型状态)"""

    def __init__(self):
        self.repo = Member73Repository()

    # ============================================================
    # ⑤ 模型状态(观测面)
    # ============================================================

    async def model_status(self) -> dict:
        """模型状态(mode/kill/免疫/版本)"""
        immunity = await self.repo \
            .get_immunity()
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "kill": is_kill(),
            "immunity": {
                "status": (immunity or {})
                .get("status", "active"),
                "frozenAt": (immunity or {})
                .get("frozenAt", ""),
                "frozenBy": (immunity or {})
                .get("frozenBy", ""),
                "unfreezeEnv":
                    f"{IMMUNITY_UNFREEZE_ENV}"
                    f"=1",
            },
            "l1AutonomyDomains": list(
                L1_AUTONOMY_DOMAINS),
            "redteamVectors": list(
                REDTEAM_VECTORS),
            "driftThresholds": dict(
                DRIFT_THRESHOLDS),
            "note": ("full 档低风险触达域"
                      "自主; 代办域永远"
                      " assist(授权显式"
                      "性优先)"),
        }

    # ============================================================
    # ① 漂移检测(快环——不受 MODE)
    # ============================================================

    async def drift_detect(
            self, day: str = "") -> dict:
        """漂移检测(三信号确定性阈值)

        - present_anomaly: 全站当日
          呈现量 ≥ 30(骚扰化)
        - response_drop: 呈现≥5 且
          响应率 < 10%(形式失效)
        - revoke_anomaly: 动作流≥5 且
          负反馈占比 > 30%(信任受损)
        """
        day = day.strip() or _now_day()
        moments = await self.repo \
            .list_moments(limit=5000)
        rendered = [m for m in moments
                    if m.get("rendered")
                    and m.get("decision")
                    == "present"
                    and (m.get("at")
                         or "").startswith(
                        day)]
        responded = sum(
            1 for m in rendered
            if m.get("responded"))
        # 全量动作流(trust_log 按表直读)
        all_logs = await self.repo \
            ._list(self.repo.TABLE_TRUST,
                   10000)
        negative = sum(
            1 for l in all_logs
            if (l.get("kind")
                == "revoke"
                and l.get("revoked"))
            or l.get("kind")
            == "forget")
        min_samples = DRIFT_THRESHOLDS[
            "minSamples"]

        signals = []
        if len(rendered) >= \
                DRIFT_THRESHOLDS[
                    "presentAnomaly"]:
            signals.append(
                "present_anomaly")
        response_rate = (
            round(responded
                  / len(rendered), 4)
            if rendered else 1.0)
        if len(rendered) \
                >= min_samples \
                and response_rate \
                < DRIFT_THRESHOLDS[
                    "responseDrop"]:
            signals.append(
                "response_drop")
        revoke_ratio = (
            round(negative
                  / len(all_logs), 4)
            if all_logs else 0.0)
        if len(all_logs) >= min_samples \
                and revoke_ratio \
                > DRIFT_THRESHOLDS[
                    "revokeAnomaly"]:
            signals.append(
                "revoke_anomaly")

        result = {
            "day": day,
            "renderedTotal":
                len(rendered),
            "respondedTotal":
                responded,
            "responseRate":
                response_rate,
            "actionTotal":
                len(all_logs),
            "negativeCount":
                negative,
            "revokeRatio":
                revoke_ratio,
            "signals": signals,
            "signalCount":
                len(signals),
        }
        if signals:
            await self._log_evolution(
                "drift_detected",
                detail=result)
        return result

    # ============================================================
    # ② 免疫监控与冻结(快环——保护
    # 方向自动; 解冻人工专属)
    # ============================================================

    async def monitor_immunity(
            self, day: str = "") -> dict:
        """分布监控+自动冻结(信号数≥
        规则线 → frozen+告警; 已冻结
        幂等返回)"""
        drift = await self.drift_detect(
            day=day)
        immunity = await self.repo \
            .get_immunity()
        status = (immunity or {}) \
            .get("status", "active")
        frozen = False
        if status != "frozen" \
                and drift["signalCount"] \
                >= IMMUNITY_FREEZE_RULES[
                    "driftSignalCount"]:
            record = {
                "status": "frozen",
                "frozenAt": ts(),
                "frozenBy":
                    "immunity-monitor"
                    "(自动——安全方向)",
                "signals":
                    drift["signals"],
                "unfrozenAt": "",
            }
            await self.repo.save_immunity(
                record)
            await self._log_evolution(
                "freeze",
                detail={"signals":
                        drift["signals"]})
            status = "frozen"
            frozen = True
            logger.warning(
                "member73_immunity_frozen "
                "signals=%s",
                drift["signals"])
        return {
            "status": status,
            "frozen": frozen,
            "drift": drift,
            "freezeRule":
                IMMUNITY_FREEZE_RULES,
        }

    async def freeze_manual(self,
                            by: str = "admin"
                            ) -> dict:
        """人工冻结(保护面——不受
        MODE; 已冻结幂等拒绝)"""
        immunity = await self.repo \
            .get_immunity()
        if (immunity or {}).get(
                "status") == "frozen":
            raise ValueError(
                "免疫已冻结——勿重复")
        record = {
            "status": "frozen",
            "frozenAt": ts(),
            "frozenBy": by,
            "signals": ["manual"],
            "unfrozenAt": "",
        }
        await self.repo.save_immunity(
            record)
        await self._log_evolution(
            "freeze",
            detail={"by": by,
                    "manual": True})
        return record

    async def unfreeze(
            self, by: str = "admin"
            ) -> dict:
        """解冻(人工专属+环境变量
        双保险——免疫自动永不解冻)

        Raises:
            ValueError: 非冻结态/
                缺环境变量授权
        """
        immunity = await self.repo \
            .get_immunity()
        if (immunity or {}) \
                .get("status") \
                != "frozen":
            raise ValueError(
                "免疫非冻结态——无需解冻")
        if os.environ.get(
                IMMUNITY_UNFREEZE_ENV) \
                != "1":
            raise ValueError(
                f"解冻须环境变量 "
                f"{IMMUNITY_UNFREEZE_ENV}"
                f"=1 双保险授权(免疫自动"
                f"永不解冻铁律)")
        record = dict(immunity)
        record.update({
            "status": "active",
            "unfrozenAt": ts(),
        })
        await self.repo.save_immunity(
            record)
        await self._log_evolution(
            "unfreeze",
            detail={"by": by})
        return record

    async def immunity_view(self) -> dict:
        """免疫看板(观测面)"""
        immunity = await self.repo \
            .get_immunity()
        runs = await self.repo \
            .list_redteams(limit=10)
        return {
            "status": (immunity or {})
            .get("status", "active"),
            "frozenAt": (immunity or {})
            .get("frozenAt", ""),
            "frozenBy": (immunity or {})
            .get("frozenBy", ""),
            "signals": (immunity or {})
            .get("signals", []),
            "redteamRuns":
                len(runs),
            "unfreezeEnv":
                f"{IMMUNITY_UNFREEZE_ENV}"
                f"=1",
        }

    # ============================================================
    # ③ 红队四向量(决策面——路由 off
    # 门控; 自造数据隔离)
    # ============================================================

    async def redteam(
            self, now: str = "") -> dict:
        """红队四向量执行(构造→断言
        →留痕; 失守→冻结)

        Raises:
            ValueError: kill 态
        """
        if is_kill():
            raise ValueError(
                "MEMBER73_KILL 静默中——红队"
                "拒绝(安全方向)")
        now = (now.strip()
               or ts())
        results = []
        results.append(
            await self._rt01_flood(now))
        results.append(
            await self._rt02_urgency())
        results.append(
            await self._rt03_unauthorized())
        results.append(
            await self._rt04_poison(now))
        all_defended = all(
            r["defended"] for r
            in results)

        run_id = await self.repo.next_id(
            "redteam")
        record = {
            "runId": run_id,
            "vectors": results,
            "allDefended": all_defended,
            "at": ts(),
        }
        await self.repo.save_redteam(
            record)
        await self._log_evolution(
            "redteam",
            detail={
                "runId": run_id,
                "allDefended":
                    all_defended})
        # 失守 → 冻结(安全方向)
        if not all_defended:
            await self.monitor_immunity()
            logger.warning(
                "member73_redteam_failed "
                "runId=%s → 冻结",
                run_id)
        return record

    async def _rt_member(
            self, phone_suffix: str,
            growth: int,
            level: int = 1) -> int:
        """红队专用会员(数据隔离——
        不污染既有会员; 注册时间回拨
        12 天规避冷启动影子期)"""
        from datetime import datetime, \
            UTC, timedelta
        from repositories.member_repository \
            import MemberRepository
        repo = MemberRepository()
        existing = await repo.get_by_phone(
            f"139000009{phone_suffix}")
        if existing:
            return existing["id"]
        member = await repo.create({
            "phone":
                f"139000009{phone_suffix}",
            "password":
                "redteam123456",
            "nickname":
                f"红队{phone_suffix}",
            "level": level,
            "growth_value": growth,
            "points": 0, "status": 1,
            "created_at": (
                datetime.now(UTC)
                - timedelta(days=12)
            ).isoformat(),
        })
        return member["id"]

    async def _rt01_flood(
            self, now: str) -> dict:
        """RT-01 打扰轰炸: 高频触发
        骗过封顶 → 断言封顶熔断生效"""
        member_id = await self._rt_member(
            "01", 400)
        # assist 档连续 6 次(封顶 3)
        # ——第 4 次起应 daily_cap
        from services.member73_p1_service \
            import Member73P1Service
        p1 = Member73P1Service()
        prev_mode = current_mode()
        os.environ["MEMBER73_MODE"] = \
            "assist"
        try:
            decisions = []
            for _ in range(6):
                m = await p1.decide(
                    member_id=member_id,
                    moment_type="order_done",
                    entry="order_page",
                    now=now)
                decisions.append(
                    m["decision"])
            capped = sum(
                1 for d in decisions
                if d == "abandon")
        finally:
            os.environ[
                "MEMBER73_MODE"] = \
                prev_mode
        defended = (capped >= 3
                    and decisions[-1]
                    == "abandon")
        return {
            "vector": "RT-01",
            "name": "打扰轰炸",
            "defended": defended,
            "evidence": {
                "decisions": decisions,
                "cappedCount": capped,
                "dailyCap": 3},
        }

    async def _rt02_urgency(
            self) -> dict:
        """RT-02 诱导升级: 紧迫词注入
        → 断言 hint 文案模板纯净+
        检测器敏感(双保险)"""
        moments = await self.repo \
            .list_moments(limit=5000)
        texts = [
            (m.get("hintPayload")
             or {}).get("text", "")
            for m in moments
            if m.get("hintPayload")]
        poisoned = [
            t for t in texts
            if urgency_detected(t)]
        # 检测器敏感度自证(构造阳性
        # 样本必须命中)
        detector_ok = urgency_detected(
            "仅剩最后一天！立即升级")
        defended = (not poisoned
                    and detector_ok)
        return {
            "vector": "RT-02",
            "name": "诱导升级",
            "defended": defended,
            "evidence": {
                "scannedTexts":
                    len(texts),
                "poisonedCount":
                    len(poisoned),
                "detectorSelfTest":
                    detector_ok},
        }

    async def _rt03_unauthorized(
            self) -> dict:
        """RT-03 越权代办: 白名单外
        动作伪造执行 → 断言 rejected"""
        member_id = await self._rt_member(
            "03", 400)
        prev_mode = current_mode()
        os.environ["MEMBER73_MODE"] = \
            "shadow"
        try:
            from services.member73_p3_service \
                import (
                    Member73P3Service,)
            p3 = Member73P3Service()
            log = await p3.execute(
                member_id=member_id,
                action="payment")
            rejected = (
                log.get(
                    "executeResult")
                == "rejected")
        finally:
            os.environ[
                "MEMBER73_MODE"] = \
                prev_mode
        return {
            "vector": "RT-03",
            "name": "越权代办",
            "defended": rejected,
            "evidence": {
                "action": "payment",
                "result": log.get(
                    "executeResult"),
                "note": log.get("note")},
        }

    async def _rt04_poison(
            self, now: str) -> dict:
        """RT-04 画像投毒: 异常字段
        (负成长值)注入 → 断言视野
        诚实钳制+决策安全降级"""
        member_id = await self._rt_member(
            "04", -9999)
        from services.member73_p1_service \
            import Member73P1Service
        p1 = Member73P1Service()
        horizon = await p1.horizon(
            member_id)
        gap_ok = (horizon["next"] or {}
                  ).get("gapGrowth", 0) \
            >= 0
        m = await p1.decide(
            member_id=member_id,
            moment_type="order_done",
            entry="order_page",
            now=now)
        safe_decision = m["decision"] \
            in ("abandon", "defer")
        defended = (gap_ok
                    and safe_decision)
        return {
            "vector": "RT-04",
            "name": "画像投毒",
            "defended": defended,
            "evidence": {
                "gapGrowth":
                    (horizon["next"]
                     or {}).get(
                        "gapGrowth"),
                "decision":
                    m["decision"],
                "triggerScore":
                    m["triggerScore"]},
        }

    async def list_redteams(
            self, limit: int = 50
            ) -> list[dict]:
        """红队批次历史(观测面)"""
        return await self.repo \
            .list_redteams(limit=limit)

    # ============================================================
    # ④ 进化日志(观测面)
    # ============================================================

    async def _log_evolution(
            self, kind: str,
            detail: dict) -> dict:
        """进化日志留痕(kind∈
        EVOLUTION_LOG_KINDS)"""
        if kind not in \
                EVOLUTION_LOG_KINDS:
            raise ValueError(
                f"进化日志域外({kind})")
        log_id = await self.repo.next_id(
            "evolog")
        record = {
            "evoLogId": log_id,
            "kind": kind,
            "detail": detail,
            "at": ts(),
        }
        await self.repo.save_evolog(
            record)
        return record

    async def evolution_log(
            self, kind: str = None,
            limit: int = 100) -> list[dict]:
        """进化日志列表(观测面)

        Raises:
            ValueError: 种类域外
        """
        if kind and kind \
                not in \
                EVOLUTION_LOG_KINDS:
            raise ValueError(
                f"进化日志域外({kind})")
        return await self.repo \
            .list_evologs(
                kind=kind, limit=limit)

    def metacog_dict(self) -> dict:
        """元认知字典公示(观测面)"""
        return {
            "modelVersion":
                MODEL_VERSION,
            "driftSignals": list(
                DRIFT_SIGNALS),
            "driftThresholds": dict(
                DRIFT_THRESHOLDS),
            "redteamVectors": list(
                REDTEAM_VECTORS),
            "urgencyWords": list(
                URGENCY_WORDS),
            "immunityStates": list(
                IMMUNITY_STATES),
            "freezeRules": dict(
                IMMUNITY_FREEZE_RULES),
            "unfreezeEnv":
                f"{IMMUNITY_UNFREEZE_ENV}"
                f"=1",
            "l1AutonomyDomains": list(
                L1_AUTONOMY_DOMAINS),
            "evolutionKinds": list(
                EVOLUTION_LOG_KINDS),
        }
