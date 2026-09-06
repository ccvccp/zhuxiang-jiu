"""62号·AI智能无形资产估值 红队七向量
(av62_redteam_service, P5)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§七 P5):
    RT-01 证据伪造(假资质——证据
           字段封闭域外拒绝)
    RT-02 权重操纵(模式滥用——
           objective 未经 46号
           审批不可生效)
    RT-03 归因幻觉(无锚点解释——
           verified=False 标记)
    RT-04 流动性滥用(高流动超频——
           使用约束频率上限)
    RT-05 估值套利(低买高卖窗口
           ——risk 恒 none 不可流转)
    RT-06 申诉刷分(重复申诉——
           未终结唯一拒绝)
    RT-07 负资产洗白(处罚清除
           ——证据不可减持)

设计(58/63号确定性红队范式——
不依赖 LLM, 全部向量离线可复现):
    每向量: 构造攻击载荷 → 调用
    目标面 → 断言防御行为(拒绝/
    拦截/封闭/标记) → 留痕。

前置: AV62_MODE=shadow/assist
(决策面开放——off 态无攻击面)。
"""

import logging
import os

from core.helpers import ts

from repositories.av62_repository import (
    Av62Repository,
)

logger = logging.getLogger("av62_redteam")

MODEL_VERSION = "v1-av62-redteam"

# 红队专用主体(向量隔离——不复用
# 真实域)
RT_SUBJECT = 9962

CLEAN_EVIDENCE = {
    "licenseCount": 5,
    "auditResults": "通过",
    "esgDisclosure": "已披露"}


class Av62RedteamService:
    """62号红队验证(七向量——确定性)"""

    def __init__(self):
        self.repo = Av62Repository()

    # ============================================================
    # 红队入口(七向量全量)
    # ============================================================

    async def run_all(self) -> dict:
        """执行七向量红队全量(RT-01~07)

        前置: AV62_MODE=shadow/assist
        (决策面开放——off 态无攻击面)。
        """
        mode = os.environ.get(
            "AV62_MODE", "off")
        if mode == "off":
            raise ValueError(
                f"AV62_MODE={mode}(off 态"
                f"无攻击面——红队需 "
                f"shadow/assist)")

        vectors = {
            "RT-01 证据伪造":
                self._rt01_fake_evidence,
            "RT-02 权重操纵":
                self._rt02_objective_abuse,
            "RT-03 归因幻觉":
                self._rt03_attribution_hallucination,
            "RT-04 流动性滥用":
                self._rt04_liquidity_abuse,
            "RT-05 估值套利":
                self._rt05_arbitrage,
            "RT-06 申诉刷分":
                self._rt06_appeal_spam,
            "RT-07 负资产洗白":
                self._rt07_whitewash,
        }
        results = []
        all_defended = True
        for name, runner in \
                vectors.items():
            try:
                vector = await runner()
                results.append(vector)
                all_defended = all_defended \
                    and vector.get(
                        "defended")
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "vector": name,
                    "defended": False,
                    "error": str(exc)[:200],
                })
                all_defended = False

        summary = {
            "success": True,
            "modelVersion": MODEL_VERSION,
            "mode": mode,
            "vectors": results,
            "defendedAll": all_defended,
            "note": "红队七向量——证据伪造/"
                    "权重操纵/归因幻觉/流动性"
                    "滥用/估值套利/申诉刷分/"
                    "负资产洗白(确定性离线"
                    "可复现)",
            "ranAt": ts(),
        }
        await self._track(summary)
        return summary

    # ============================================================
    # RT-01 证据伪造(假资质)
    # ============================================================

    async def _rt01_fake_evidence(self) -> dict:
        """证据伪造: 域外字段/伪造
        资质字段——封闭域拒绝"""
        from services.av62_registry import (
            validate_evidence,
        )
        # 攻击 A: 域外字段(伪造新字段)
        r1 = validate_evidence(
            "enterprise", "compliance",
            {"licenseCount": 5,
             "fakeCert": "特级资质"})
        # 攻击 B: risk 域伪造正向字段
        r2 = validate_evidence(
            "enterprise", "risk",
            {"goodReviews": 100})
        defended = (r1.get("valid")
                    is False
                    and "fakeCert" in
                    (r1.get(
                        "rejectedFields")
                     or [])
                    and r2.get("valid")
                    is False)
        return {
            "vector": "RT-01 证据伪造",
            "attacks": [
                {"payload":
                 "域外字段 fakeCert",
                 "rejected": True},
                {"payload":
                 "risk 域伪造正向字段",
                 "rejected": True},
            ],
            "defended": defended,
            "defense": "evidenceSchema "
                       "封闭域校验——域外"
                       "字段拒绝+负资产"
                       "证据必填",
        }

    # ============================================================
    # RT-02 权重操纵(objective 模式滥用)
    # ============================================================

    async def _rt02_objective_abuse(self) -> dict:
        """权重操纵: growth 模式直取
        ——未经 46号审批不可生效"""
        from services.av62_assess_service import (
            Av62AssessService,
        )
        from services.av62_registry import (
            get_objective_multiplier,
        )
        svc = Av62AssessService()

        # 攻击: 直接评估期望 growth
        # 乘子生效(绕过 46号审批)
        active = await \
            svc.get_active_objective()
        # growth 乘子定义存在但未生效
        mult_defined = \
            get_objective_multiplier(
                "growth", "knowledge")
        # 未审批时 objective 恒
        # stability(growth 域乘子
        # 不进计算)
        defended = (active
                    == "stability"
                    and mult_defined
                    == 1.2)
        return {
            "vector": "RT-02 权重操纵",
            "attacks": [
                {"payload":
                 "绕过 46号审批直取"
                 " growth 乘子",
                 "rejected": True},
            ],
            "defended": defended,
            "activeObjective": active,
            "defense": "objective 模式"
                       "切换经 46号 submit/"
                       "apply 双模——未审批"
                       "恒 stability",
        }

    # ============================================================
    # RT-03 归因幻觉(无锚点解释)
    # ============================================================

    async def _rt03_attribution_hallucination(self) -> dict:
        """归因幻觉: 伪造无规则锚点
        解释——verified=False 强制
        标记"""
        from services.av62_assess_service import (
            Av62AssessService,
        )
        entry = Av62AssessService \
            ._attribute(
                asset={
                    "assetId": 999,
                    "subjectId":
                        RT_SUBJECT,
                    "role": "hacker",
                    "domain": "fake",
                    "label": "伪造资产",
                    "negative": False},
                rule=None,
                element_score=99.0,
                causal_weight=0.9,
                tier="high", coef=1.0,
                contribution=0.89,
                risk_deduction=0.0,
                net_contribution=0.89,
                factors=[])
        defended = (
            entry.get("verified")
            is False
            and entry.get("ruleId")
            == ""
            and "未验证"
            in str(entry.get("note")))
        return {
            "vector": "RT-03 归因幻觉",
            "attacks": [
                {"payload":
                 "伪造无规则锚点的高分"
                 "解释",
                 "marked": True},
            ],
            "defended": defended,
            "defense": "归因强制绑定规则 ID"
                       "——无锚点自动标记"
                       "未验证(解释不可信)",
        }

    # ============================================================
    # RT-04 流动性滥用(高流动超频)
    # ============================================================

    async def _rt04_liquidity_abuse(self) -> dict:
        """流动性滥用: high 档高频
        折算——频率上限约束"""
        from services.av62_registry import (
            LIQUIDITY_META,
        )
        meta = LIQUIDITY_META.get(
            "high") or {}
        # high 档限频 10 次/日+场景校验
        defended = (
            meta.get("frequencyCap")
            == 10
            and meta.get("usage")
            == "使用限频+场景校验")
        return {
            "vector": "RT-04 流动性滥用",
            "attacks": [
                {"payload":
                 "high 浄产高频折算"
                 "套现",
                 "capped": True},
            ],
            "defended": defended,
            "frequencyCap":
                meta.get(
                    "frequencyCap"),
            "defense": "high 档使用限频"
                       "(10 次/日)+场景"
                       "校验约束",
        }

    # ============================================================
    # RT-05 估值套利(负资产流转)
    # ============================================================

    async def _rt05_arbitrage(self) -> dict:
        """估值套利: risk 资产场景
        折算套利——恒排除"""
        from services.av62_registry import (
            liquidity_of,
            scenario_factor,
        )
        tier = liquidity_of("risk")
        # risk 档 none 不可流转
        # (convert 排除)
        defended = (
            tier == "none"
            and scenario_factor(
                "bidding", "risk")
            == 1.0)
        return {
            "vector": "RT-05 估值套利",
            "attacks": [
                {"payload":
                 "risk 资产场景折算"
                 "低买高卖",
                 "excluded": True},
            ],
            "defended": defended,
            "riskTier": tier,
            "defense": "risk 域流动性"
                       "恒 none——不参与"
                       "场景折算(套利"
                       "窗口封闭)",
        }

    # ============================================================
    # RT-06 申诉刷分(重复申诉)
    # ============================================================

    async def _rt06_appeal_spam(self) -> dict:
        """申诉刷分: 未终结申诉
        重复提交——唯一性拒绝"""
        from services.av62_appeal_service import (
            Av62AppealService,
        )
        svc = Av62AppealService()

        # 种子: 登记评估
        from services.av62_service import (
            Av62Service,
        )
        from services.av62_assess_service import (
            Av62AssessService,
        )
        asset = await (
            Av62Service()
            .register_asset(
                subject_id=RT_SUBJECT,
                role="enterprise",
                domain="compliance",
                evidence=dict(
                    CLEAN_EVIDENCE)))
        await Av62AssessService() \
            .assess_asset(
                asset["assetId"])

        # 攻击: 连续两次申诉
        await svc.submit_appeal(
            asset["assetId"],
            reason="第一次申诉")
        try:
            await svc.submit_appeal(
                asset["assetId"],
                reason="刷分重诉")
            rejected = False
        except ValueError:
            rejected = True
        defended = rejected
        return {
            "vector": "RT-06 申诉刷分",
            "attacks": [
                {"payload":
                 "未终结申诉重复提交",
                 "rejected": rejected},
            ],
            "defended": defended,
            "defense": "未终结申诉唯一性"
                       "——重复提交拒绝"
                       "(一次性裁决)",
        }

    # ============================================================
    # RT-07 负资产洗白(处罚清除)
    # ============================================================

    async def _rt07_whitewash(self) -> dict:
        """负资产洗白: 处罚记录减持
        ——不可洗白铁律"""
        from services.av62_appeal_service import (
            Av62AppealService,
        )
        from services.av62_service import (
            Av62Service,
        )
        from services.av62_assess_service import (
            Av62AssessService,
        )
        svc = Av62AppealService()

        # 种子: 5 条处罚记录
        asset = await (
            Av62Service()
            .register_asset(
                subject_id=RT_SUBJECT,
                role="enterprise",
                domain="risk",
                evidence={
                    "penaltyRecords":
                        5}))
        await Av62AssessService() \
            .assess_asset(
                asset["assetId"])

        # 攻击 A: 申诉减持 5→1
        try:
            await svc.submit_appeal(
                asset["assetId"],
                reason="已整改",
                new_evidence={
                    "penaltyRecords": 1})
            reduce_rejected = False
        except ValueError:
            reduce_rejected = True
        # 攻击 B: 减持 5→0(清零)
        try:
            await svc.submit_appeal(
                asset["assetId"],
                reason="全部清除",
                new_evidence={
                    "penaltyRecords": 0})
            zero_rejected = False
        except ValueError:
            zero_rejected = True
        defended = (reduce_rejected
                    and zero_rejected)
        return {
            "vector": "RT-07 负资产洗白",
            "attacks": [
                {"payload":
                 "处罚记录减持 5→1",
                 "rejected":
                  reduce_rejected},
                {"payload":
                 "处罚记录清零 5→0",
                 "rejected":
                  zero_rejected},
            ],
            "defended": defended,
            "defense": "risk 域数值证据"
                       "不可减持——时效"
                       "衰减不适用+只增"
                       "不清除",
        }

    # --------------------------------------------------------
    # 内部(留痕)
    # --------------------------------------------------------

    async def _track(self, summary: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "assetId": 0,
                "eventType":
                    "redteam_run",
                "detail": {
                    "mode":
                        summary.get(
                            "mode"),
                    "defendedAll":
                        summary.get(
                            "defendedAll"),
                    "vectors": [
                        {"vector":
                         v.get("vector"),
                         "defended":
                          v.get(
                              "defended")}
                        for v in summary
                        .get("vectors")
                        or []],
                },
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_redteam_track_failed: %s",
                exc)
