"""74号·NexusFlow(智枢·流)AI智能全域发布
大模型 P5 元认知收官服务
(nexus74_p5_service)

规划(docs/74号_NexusFlow智枢流_AI智能全域
发布大模型_创新规划方案.md §六 P5):
    ① 漂移检测(三信号——确定性阈值:
       publish_anomaly 当日发布≥30/
       rejection_anomaly 审核驳回率
       >30%[样本≥5]/review_anomaly 适配
       复核率>40%[样本≥5])
    ② 免疫监控与冻结(信号数≥2 → 发布面
       自动冻结+告警留痕; 解冻人工专属+
       环境变量双保险)
    ③ 红队四向量(RT-01 红线穿透/RT-02
       频次轰炸/RT-03 越权直发/RT-04 回执
       伪造——构造→断言→留痕; 失守→冻结;
       自造数据用后清理不污染配额)
    ④ 进化日志(七类留痕)
    ⑤ 模型状态+元认知字典(观测面)

铁律(规划 §九):
    - 冻结为保护方向自动(发布面关闭,
      观测面保留); 解冻人工专属
      (NEXUSFLOW74_IMMUNITY=1 双保险
      ——71/73号范式)
    - 漂移检测/免疫监控为快环——不受
      MODE; 红队为决策面(off=409
      由路由层门控)
    - LLM 禁入(信号判定=确定性阈值)
    - 红队临时 env 变更/静默窗配置
      均用后恢复; 伪造发布记录清理

异常约定(71号口径):
    KeyError → 404(不存在)
    ValueError → 409(状态机/域外/
        解冻授权)
"""

import logging
import os
from datetime import datetime

from core.helpers import ts

from repositories.nexus74_repository import (
    Nexus74Repository,
)
from services.nexus74_registry import (
    ADAPTER_TIERS,
    AUDIT_RESULTS,
    DRIFT_SIGNALS, DRIFT_THRESHOLDS,
    EVOLUTION_LOG_KINDS,
    IMMUNITY_FREEZE_RULES,
    IMMUNITY_STATES,
    IMMUNITY_UNFREEZE_ENV,
    METRIC_TYPES,
    MODEL_VERSION,
    PLATFORMS, REDLINES,
    REDTEAM_VECTORS,
    current_mode, is_kill,
)

logger = logging.getLogger("nexus74_p5_service")

# 红队确定性时刻(北京 18:00——默认
# 静默窗外; 固定日使封顶计数与生产
# 真实日期解耦)
RT_NOW = "2026-09-13T10:00:00+00:00"


def _now_day() -> str:
    """北京当日(漂移日界)"""
    from services.nexus74_p3_service import \
        _BJT
    return datetime.now(_BJT) \
        .strftime("%Y-%m-%d")


def _day_of(iso: str) -> str:
    """存储 ISO 时刻→北京日(非法→空)"""
    from services.nexus74_p3_service import \
        _bj_day
    try:
        return _bj_day(iso)
    except (ValueError, TypeError):
        return ""


class Nexus74P5Service:
    """74号 P5 元认知(漂移/免疫/红队/
    进化日志/模型状态)"""

    def __init__(self):
        self.repo = Nexus74Repository()

    # ============================================================
    # ⑤ 模型状态+元认知字典(观测面)
    # ============================================================

    async def model_status(self) -> dict:
        """模型状态(mode/kill/免疫/平台/
        红线/适配器分级)"""
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
            "platformCount": len(PLATFORMS),
            "redlines": list(REDLINES),
            "adapterTiers": dict(
                ADAPTER_TIERS),
            "driftThresholds": dict(
                DRIFT_THRESHOLDS),
            "redteamVectors": list(
                REDTEAM_VECTORS),
            "note": ("B 档平台操作永远人工"
                     "(发布显式性铁律); "
                     "full 仅 A 档低风险域"
                     "自主"),
        }

    def metacog_dict(self) -> dict:
        """元认知字典公示(观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "driftSignals": list(
                DRIFT_SIGNALS),
            "driftThresholds": dict(
                DRIFT_THRESHOLDS),
            "redteamVectors": list(
                REDTEAM_VECTORS),
            "immunityStates": list(
                IMMUNITY_STATES),
            "freezeRules": dict(
                IMMUNITY_FREEZE_RULES),
            "unfreezeEnv":
                f"{IMMUNITY_UNFREEZE_ENV}"
                f"=1",
            "evolutionKinds": list(
                EVOLUTION_LOG_KINDS),
            "metricTypes": list(
                METRIC_TYPES),
            "auditResults": list(
                AUDIT_RESULTS),
        }

    # ============================================================
    # ① 漂移检测(快环——不受 MODE)
    # ============================================================

    async def drift_detect(
            self, day: str = "") -> dict:
        """漂移检测(三信号确定性阈值)

        - publish_anomaly: 当日 published
          总量 ≥ 30(轰炸)
        - rejection_anomaly: 审核样本≥5
          且驳回率(含限流)>30%(形式失效)
        - review_anomaly: 适配样本≥5 且
          复核率>40%(灰度失控)
        """
        day = day.strip() or _now_day()
        pubs = await self.repo \
            .list_publications(
                limit=5000)
        published_today = [
            p for p in pubs
            if p.get("status")
            == "published"
            and _day_of(
                p.get("publishedAt")
                or "") == day]
        audits = await self.repo \
            .list_audits(limit=5000)
        negative = sum(
            1 for a in audits
            if a.get("result")
            in ("rejected",
                "throttled"))
        adaptations = await self.repo \
            .list_adaptations(
                limit=5000)
        review_count = sum(
            1 for x in adaptations
            if x.get("needsReview"))

        signals = []
        if len(published_today) \
                >= DRIFT_THRESHOLDS[
                    "publishAnomaly"]:
            signals.append(
                "publish_anomaly")
        rej_rate = (round(
                        negative
                        / len(audits), 4)
                    if audits else 0.0)
        if len(audits) \
                >= DRIFT_THRESHOLDS[
                    "minSamples"] \
                and rej_rate \
                > DRIFT_THRESHOLDS[
                    "rejectionDrop"]:
            signals.append(
                "rejection_anomaly")
        review_ratio = (round(
                            review_count
                            / len(adaptations),
                            4)
                        if adaptations
                        else 0.0)
        if len(adaptations) \
                >= DRIFT_THRESHOLDS[
                    "minSamples"] \
                and review_ratio \
                > DRIFT_THRESHOLDS[
                    "reviewAnomaly"]:
            signals.append(
                "review_anomaly")

        result = {
            "day": day,
            "publishedToday":
                len(published_today),
            "auditTotal":
                len(audits),
            "negativeCount":
                negative,
            "rejectionRate":
                rej_rate,
            "adaptationTotal":
                len(adaptations),
            "reviewCount":
                review_count,
            "reviewRatio":
                review_ratio,
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
                "signals": drift["signals"],
                "unfrozenAt": "",
            }
            await self.repo.save_immunity(
                record)
            await self._log_evolution(
                "freeze",
                detail={"signals":
                            drift["signals"],
                        "auto": True})
            status = "frozen"
            frozen = True
            logger.warning(
                "nexus74_immunity_frozen "
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
        """解冻(人工专属+环境变量双保险
        ——免疫自动永不解冻)

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
            "redteamRuns": len(runs),
            "unfreezeEnv":
                f"{IMMUNITY_UNFREEZE_ENV}"
                f"=1",
        }

    # ============================================================
    # ③ 红队四向量(决策面——路由 off
    # 门控; 自造数据用后清理)
    # ============================================================

    async def redteam(self) -> dict:
        """红队四向量执行(构造→断言
        →留痕; 失守→冻结)

        Raises:
            ValueError: kill 态
        """
        if is_kill():
            raise ValueError(
                "NEXUSFLOW74_KILL 静默中"
                "——红队拒绝(安全方向)")
        results = [
            await self._rt01_redline(),
            await self._rt02_flood(),
            await self._rt03_unauthorized(),
            await self._rt04_forgery(),
        ]
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
        if not all_defended:
            await self.monitor_immunity()
            logger.warning(
                "nexus74_redteam_failed "
                "runId=%s → 冻结",
                run_id)
        return record

    async def _rt_source(
            self, title: str,
            body: str) -> int:
        """红队专用源内容"""
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        source = await \
            Nexus74P2Service() \
            .create_source({
                "title": title,
                "body": body,
                "intent": "seeding",
            })
        return source["sourceId"]

    async def _rt01_redline(self) -> dict:
        """RT-01 红线穿透: 违规内容
        (R1 诱导话术)混入适配 →
        断言合规前置拦截"""
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        p2 = Nexus74P2Service()
        blocked = False
        message = ""
        try:
            source_id = await \
                self._rt_source(
                    "红队RT01 果酒好喝"
                    "姐妹们冲",
                    "红队基准正文(违规话术)")
            await p2.adapt(
                source_id,
                "xiaohongshu")
        except ValueError as exc:
            blocked = ("合规前置未过"
                       in str(exc))
            message = str(exc)[:120]
        return {
            "vector": "RT-01",
            "name": "红线穿透",
            "defended": blocked,
            "evidence": {
                "message": message},
        }

    async def _rt02_flood(self) -> dict:
        """RT-02 频次轰炸: 伪造当日 3 篇
        已发布 → 断言第 4 篇封顶熔断
        (伪造记录用后清理)"""
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        from services.nexus74_p3_service import (
            Nexus74P3Service, _at,
        )
        p2 = Nexus74P2Service()
        p3 = Nexus74P3Service()
        source_id = await \
            self._rt_source(
                "红队RT02 频次基准源",
                "品鉴科普知识"
                "（过量饮酒有害健康）")
        await p2.adapt(source_id,
                      "toutiao")
        blocked = False
        message = ""
        fabricated = []
        prev_mode = current_mode()
        silence_backup = await \
            p3.get_silence()
        try:
            os.environ[
                "NEXUSFLOW74_MODE"] = \
                "assist"
            await p3.set_silence(
                enabled=False, hours=[])
            for _ in range(3):
                pid = await self.repo \
                    .next_id("publication")
                await self.repo \
                    .save_publication({
                        "publicationId": pid,
                        "sourceId":
                            source_id,
                        "adaptationId": 0,
                        "platform":
                            "toutiao",
                        "platformName":
                            "今日头条",
                        "adapterTier": "B",
                        "status":
                            "published",
                        "publishedAt":
                            _at(RT_NOW),
                        "mode": "redteam",
                    })
                fabricated.append(pid)
            try:
                await p3.publish(
                    source_id=source_id,
                    platform="toutiao",
                    now=RT_NOW)
            except ValueError as exc:
                blocked = ("封顶"
                           in str(exc))
                message = str(exc)[:120]
        finally:
            os.environ[
                "NEXUSFLOW74_MODE"] = \
                prev_mode
            await p3.set_silence(
                enabled=bool(
                    silence_backup.get(
                        "enabled", True)),
                hours=list(
                    silence_backup.get(
                        "hours") or []))
            for pid in fabricated:
                await self.repo._delete(
                    self.repo
                    .TABLE_PUBLICATIONS,
                    pid)
        return {
            "vector": "RT-02",
            "name": "频次轰炸",
            "defended": blocked,
            "evidence": {
                "message": message,
                "dailyCap":
                    "fabricated-cleaned"},
        }

    async def _rt03_unauthorized(
            self) -> dict:
        """RT-03 越权直发: assist 档
        auto 自主 → 断言拒绝; full 档
        B 档 auto → 断言仍人工
        (平台操作显式性铁律)"""
        from services.nexus74_p2_service import (
            Nexus74P2Service,
        )
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        p2 = Nexus74P2Service()
        p3 = Nexus74P3Service()
        source_id = await \
            self._rt_source(
                "红队RT03 越权基准源",
                "品鉴科普知识"
                "（过量饮酒有害健康）")
        await p2.adapt(source_id,
                      "wechat_mp")
        await p2.adapt(source_id,
                      "xiaohongshu")
        refusal_ok = False
        b_manual = False
        pub_id = None
        prev_mode = current_mode()
        try:
            os.environ[
                "NEXUSFLOW74_MODE"] = \
                "assist"
            try:
                await p3.publish(
                    source_id=source_id,
                    platform="wechat_mp",
                    auto=True,
                    now=RT_NOW)
            except ValueError as exc:
                refusal_ok = ("仅 full"
                               in str(exc))
            os.environ[
                "NEXUSFLOW74_MODE"] = \
                "full"
            rec = await p3.publish(
                source_id=source_id,
                platform="xiaohongshu",
                auto=True,
                now=RT_NOW)
            pub_id = rec[
                "publicationId"]
            b_manual = (rec["status"]
                        == "awaiting_manual"
                        and not rec[
                            "autoPublished"])
        finally:
            os.environ[
                "NEXUSFLOW74_MODE"] = \
                prev_mode
            if pub_id:
                await self.repo._delete(
                    self.repo
                    .TABLE_PUBLICATIONS,
                    pub_id)
        return {
            "vector": "RT-03",
            "name": "越权直发",
            "defended": (refusal_ok
                        and b_manual),
            "evidence": {
                "assistAutoRefused":
                    refusal_ok,
                "fullBTierManual":
                    b_manual},
        }

    async def _rt04_forgery(self) -> dict:
        """RT-04 回执伪造: A 档伪造回执
        + B 档重复登记 → 断言数据诚实
        拒绝(伪造记录用后清理)"""
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        p3 = Nexus74P3Service()
        a_refused = False
        b_dup_refused = False
        fab = []
        try:
            pid_a = await self.repo \
                .next_id("publication")
            await self.repo \
                .save_publication({
                    "publicationId": pid_a,
                    "sourceId": 0,
                    "adaptationId": 0,
                    "platform":
                        "wechat_mp",
                    "platformName":
                        "微信公众号",
                    "adapterTier": "A",
                    "status":
                        "published",
                    "mode": "redteam",
                })
            fab.append(pid_a)
            try:
                await p3.receipt(
                    pid_a, "published",
                    message="伪造回执")
            except ValueError as exc:
                a_refused = ("仅 B 档"
                              in str(exc))
            pid_b = await self.repo \
                .next_id("publication")
            await self.repo \
                .save_publication({
                    "publicationId": pid_b,
                    "sourceId": 0,
                    "adaptationId": 0,
                    "platform": "douyin",
                    "platformName": "抖音",
                    "adapterTier": "B",
                    "status":
                        "awaiting_manual",
                    "mode": "redteam",
                })
            fab.append(pid_b)
            await p3.receipt(
                pid_b, "published",
                external_id="dy-rt04")
            try:
                await p3.receipt(
                    pid_b, "published")
            except ValueError as exc:
                b_dup_refused = \
                    ("awaiting_manual"
                     in str(exc))
        finally:
            for pid in fab:
                await self.repo._delete(
                    self.repo
                    .TABLE_PUBLICATIONS,
                    pid)
        return {
            "vector": "RT-04",
            "name": "回执伪造",
            "defended": (a_refused
                         and b_dup_refused),
            "evidence": {
                "aTierReceiptRefused":
                    a_refused,
                "bTierDuplicateRefused":
                    b_dup_refused},
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
            limit: int = 100
    ) -> list[dict]:
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
                kind=kind or None,
                limit=limit)
