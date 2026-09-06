"""62号·AI智能无形资产估值 转化层服务
(av62_liquidity_service, P2)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§3.3/§七 P2):
    ① 流动性评级三档+使用约束建议
       (high 限频+场景校验/medium
        上下文解释/low 仅自证
        不可流转/risk 不可流转)
    ② 衰减模型: decay = base ×
       exp(-λ×idleDays)(90 日半衰期
       ——经 46号审批可校准 30-365)
    ③ 激活机制: 合规使用/知识更新
       →衰减重置(状态 reactivated)
    ④ 场景信值折算(SCENARIO_FACTORS
       ×域系数×场景乘子)→输出到
       45号 L2 platform_conduct 增益域
       (deposit 范式调用——56号同款
        fail-soft)
    ⑤ 反事实压测: 要素摘除重算
       Δ% 报告(风险管理辅助)

铁律(计划 §1.3/§八):
    - 负资产不可流转(risk 域
      liquidity=none——不参与
      场景折算)
    - low 档仅自证不可流转
    - 45号零改动(deposit 纯调用)
    - LLM 不进判定链(纯确定性)
"""

import logging
import os
from datetime import datetime, UTC

from core.helpers import ts

from repositories.av62_repository import (
    Av62Repository,
)

logger = logging.getLogger("av62_liquidity")

MODEL_VERSION = "v1-av62-liquidity"

SCORER_ID = "asset_valuation"

# 激活允许源态
ACTIVATE_FROM_STATES = (
    "active", "assessed", "decaying",
    "reactivated", "pending_review")

# 激活理由域(封闭——合规使用/知识
# 更新两类, 防任意洗衰减)
ACTIVATE_REASONS = (
    "compliance_use",    # 合规使用
    "knowledge_update",  # 知识更新
)

# 45号 deposit 参数(56号补偿范式)
DEPOSIT_LAYER = "L2"
DEPOSIT_FACTOR = "platform_conduct"


def current_mode() -> str:
    """模块开关(AV62_MODE——同底座口径)"""
    return os.environ.get(
        "AV62_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"AV62_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _days_since(iso_ts: str) -> int:
    """距某时间戳的闲置天数(解析失败 0)"""
    try:
        dt = datetime.fromisoformat(
            str(iso_ts))
        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=UTC)
        return max(int(
            (datetime.now(UTC)
             - dt).total_seconds()
            // 86400), 0)
    except (TypeError, ValueError):
        return 0


class Av62LiquidityService:
    """62号转化层(P2——流动性+衰减
    +激活+场景折算+反事实压测)"""

    def __init__(self):
        self.repo = Av62Repository()

    # ============================================================
    # 流动性档案(资产级)
    # ============================================================

    async def get_profile(self,
                           asset_id: int
                           ) -> dict:
        """流动性档案(评级+衰减状态+
        使用约束建议——观测面)

        副作用: 衰减因子跌破阈值→状态
        置 decaying(闲置衰减标记)

        Raises:
            KeyError: 资产不存在
        """
        asset = await self.repo.get_asset(
            int(asset_id))
        if not asset:
            raise KeyError(
                f"资产 {asset_id} 不存在")

        from services.av62_registry import (
            DECAYING_THRESHOLD,
            LIQUIDITY_META,
            RISK_DOMAIN,
            decay_factor,
            liquidity_of,
        )
        half_life = await \
            self.get_active_half_life()
        idle_days = _days_since(
            asset.get("updatedAt")
            or asset.get("createdAt")
            or ts())
        factor = decay_factor(
            idle_days, half_life)
        tier = liquidity_of(
            asset.get("domain"))
        meta = LIQUIDITY_META.get(
            tier) or {}

        # 评估基线(最新评估)
        latest = await self.repo \
            .list_assessments(
                asset_id=int(asset_id),
                limit=1)
        base = float(
            (latest[0].get("baseValue")
             if latest else 0) or 0)
        decayed = round(
            base * factor, 2)

        # decaying 态标记(正资产且
        # 因子跌破阈值)
        status = asset.get("status")
        if tier != "none" \
                and factor \
                < DECAYING_THRESHOLD \
                and status in (
                    "active", "assessed",
                    "reactivated"):
            status = "decaying"
            asset["status"] = status
            asset["updatedAt"] = ts()
            await self.repo.save_asset(
                asset, create=False)
            await self._track(
                "decay", {
                    "assetId":
                        int(asset_id),
                    "idleDays": idle_days,
                    "decayFactor": factor,
                    "halfLifeDays":
                        half_life,
                })

        record = {
            "assetId": int(asset_id),
            "subjectId": int(
                asset.get("subjectId")
                or 0),
            "domain": asset.get("domain"),
            "negative":
                asset.get("domain")
                == RISK_DOMAIN,
            "assetStatus": status,
            "liquidityTier": tier,
            "liquidityLabel":
                meta.get("label"),
            "usageConstraint":
                meta.get("usage"),
            "frequencyCap":
                meta.get(
                    "frequencyCap"),
            "convertible":
                bool(meta.get(
                    "convertible")),
            "idleDays": idle_days,
            "halfLifeDays": half_life,
            "decayFactor": factor,
            "baseValue": base,
            "decayedValue": decayed,
            "lastActiveAt": asset.get(
                "updatedAt"),
            "updatedAt": ts(),
        }
        await self.repo.save_liquidity(
            record)
        return {
            "success": True,
            "profile": record,
            "note": "流动性档案——三档评级"
                    "+衰减(exp 半衰期)"
                    "+使用约束建议",
        }

    async def list_profiles(self,
                            subject_id: int = None,
                            limit: int = 100
                            ) -> dict:
        """主体流动性档案汇总(观测面)"""
        assets = await self.repo.list_assets(
            subject_id=subject_id,
            limit=int(limit or 100))
        profiles = []
        for a in assets:
            try:
                r = await self.get_profile(
                    int(a.get("assetId")))
                profiles.append(
                    r.get("profile"))
            except KeyError:
                continue
        by_tier = {}
        for p in profiles:
            by_tier[p.get(
                "liquidityTier")] = \
                by_tier.get(
                    p.get("liquidityTier"),
                    0) + 1
        return {
            "success": True,
            "subjectId": subject_id,
            "total": len(profiles),
            "byTier": by_tier,
            "profiles": profiles,
            "note": "主体流动性汇总——"
                    "三档分布+衰减状态",
        }

    # ============================================================
    # 激活机制(衰减重置)
    # ============================================================

    async def activate_asset(self,
                             asset_id: int,
                             reason: str,
                             activated_by: str = "admin"
                             ) -> dict:
        """衰减重激活(合规使用/知识
        更新→衰减重置; 状态 reactivated)

        Raises:
            KeyError: 资产不存在
            ValueError: off 态/理由域外/
                状态机拒绝/不可流转资产
        """
        require_active_mode()
        asset = await self.repo.get_asset(
            int(asset_id))
        if not asset:
            raise KeyError(
                f"资产 {asset_id} 不存在")
        reason = str(reason or "").strip()
        from services.av62_registry import (
            LIQUIDITY_META,
            liquidity_of,
        )
        if reason not in \
                ACTIVATE_REASONS:
            raise ValueError(
                f"激活理由 {reason} 域外"
                f"(合法: {'/'.join(
                    ACTIVATE_REASONS)}"
                f"——合规使用/知识更新)")
        tier = liquidity_of(
            asset.get("domain"))
        if not (LIQUIDITY_META.get(
                tier) or {}).get(
                    "convertible"):
            raise ValueError(
                f"资产流动性档 {tier}"
                f" 不可激活流转"
                f"(low 仅自证/risk"
                f" 负资产)")
        if asset.get("status") \
                not in ACTIVATE_FROM_STATES:
            raise ValueError(
                f"资产状态 "
                f"{asset.get('status')} "
                f"不可激活(合法源态: "
                f"{'/'.join(
                    ACTIVATE_FROM_STATES)})")

        # 衰减重置(updatedAt 刷新→
        # idleDays 归零)
        asset.update({
            "status": "reactivated",
            "updatedAt": ts()})
        await self.repo.save_asset(
            asset, create=False)
        await self._track("activate", {
            "assetId": int(asset_id),
            "reason": reason,
            "activatedBy": activated_by,
        })
        return {
            "success": True,
            "assetId": int(asset_id),
            "status": "reactivated",
            "reason": reason,
            "decayReset": True,
            "note": "衰减已重置(idleDays 归零"
                    "——合规使用/知识更新"
                    "激活)",
            "activatedAt": ts(),
        }

    # ============================================================
    # 场景信值折算(转化输出)
    # ============================================================

    async def convert_scenario(self,
                               subject_id: int,
                               scenario: str,
                               deposit: bool = True,
                               converted_by: str = "admin"
                               ) -> dict:
        """场景信值折算(资产→场景折算
        值+流动性约束建议+45号增益域
        输出)

        流程:
            ① 主体最新评估(无评估先拒)
            ② 逐资产: 场景系数×场景乘子
               ×衰减因子(仅可流转档)
            ③ low/risk 档排除(不可流转)
            ④ 45号 deposit(可选——
               fail-soft 纯调用)
        Raises:
            ValueError: off 态/场景域外/
                无评估资产
        """
        require_active_mode()
        from services.av62_registry import (
            LIQUIDITY_META,
            SCENARIO_FACTORS,
            RISK_DOMAIN,
            decay_factor,
            liquidity_of,
            scenario_factor,
        )
        scenario = str(
            scenario or "").strip()
        if scenario not in \
                SCENARIO_FACTORS:
            raise ValueError(
                f"场景 {scenario} 域外"
                f"(合法: {'/'.join(
                    SCENARIO_FACTORS)})")
        subject_id = int(subject_id or 0)
        assets = await self.repo.list_assets(
            subject_id=subject_id)
        if not assets:
            raise ValueError(
                f"主体 {subject_id} 无登记"
                f"资产(先登记评估)")

        half_life = await \
            self.get_active_half_life()
        scenario_mult = \
            await self.get_scenario_multiplier(
                scenario)

        included, excluded = [], []
        scenario_value = 0.0
        for asset in assets:
            domain = asset.get("domain")
            tier = liquidity_of(domain)
            meta = LIQUIDITY_META.get(
                tier) or {}
            latest = await self.repo \
                .list_assessments(
                    asset_id=int(
                        asset.get(
                            "assetId")),
                    limit=1)
            base = float(
                (latest[0].get("baseValue")
                 if latest else 0) or 0)
            idle_days = _days_since(
                asset.get("updatedAt")
                or asset.get("createdAt")
                or ts())
            d_factor = decay_factor(
                idle_days, half_life)
            s_factor = scenario_factor(
                scenario, domain)

            if not meta.get(
                    "convertible"):
                excluded.append({
                    "assetId": int(
                        asset.get(
                            "assetId")),
                    "domain": domain,
                    "liquidityTier":
                        tier,
                    "reason": "不可流转"
                              "(low 自证/"
                              "risk 负资产)",
                })
                continue
            value = round(
                base * s_factor
                * scenario_mult * d_factor,
                2)
            scenario_value += value
            included.append({
                "assetId": int(
                    asset.get("assetId")),
                "domain": domain,
                "liquidityTier": tier,
                "baseValue": base,
                "scenarioFactor":
                    s_factor,
                "scenarioMultiplier":
                    scenario_mult,
                "decayFactor": d_factor,
                "scenarioValue": value,
                "usageConstraint":
                    meta.get("usage"),
                "frequencyCap":
                    meta.get(
                        "frequencyCap"),
            })
        scenario_value = round(
            scenario_value, 2)

        # 45号增益域输出(deposit
        # 范式——fail-soft 纯调用)
        deposit_result = None
        if deposit and scenario_value \
                > 0:
            deposit_result = \
                await self._deposit_45(
                    subject_id,
                    scenario,
                    scenario_value,
                    converted_by)

        await self._track("convert", {
            "subjectId": subject_id,
            "scenario": scenario,
            "scenarioValue":
                scenario_value,
            "included": len(included),
            "excluded": len(excluded),
            "scenarioMultiplier":
                scenario_mult,
            "deposited": bool(
                deposit_result
                and deposit_result
                .get("verified")),
            "convertedBy": converted_by,
        })
        return {
            "success": True,
            "subjectId": subject_id,
            "scenario": scenario,
            "scenarioLabel":
                (SCENARIO_FACTORS.get(
                    scenario) or {}
                ).get("label"),
            "scenarioMultiplier":
                scenario_mult,
            "halfLifeDays": half_life,
            "scenarioValue":
                scenario_value,
            "included": included,
            "excluded": excluded,
            "trustValueDeposit":
                deposit_result,
            "note": "场景信值折算——Σ(base"
                    "×场景系数×乘子×衰减)"
                    "(仅可流转档; 45号"
                    " platform_conduct "
                    "增益域 deposit)",
            "convertedAt": ts(),
        }

    # ============================================================
    # 反事实压测(要素摘除重算)
    # ============================================================

    async def stress_subject(self,
                            subject_id: int,
                            remove_asset_ids: list = None,
                            remove_domains: list = None,
                            ) -> dict:
        """反事实压测(要素摘除重算
        Δ%——"若失去 X 资产, 信值
        下降 Y%"——风险管理辅助)

        Raises:
            ValueError: off 态/无资产/
                摘除集无效
        """
        require_active_mode()
        subject_id = int(subject_id or 0)
        assets = await self.repo.list_assets(
            subject_id=subject_id)
        if not assets:
            raise ValueError(
                f"主体 {subject_id} 无登记"
                f"资产(先登记评估)")
        remove_ids = {
            int(a) for a in
            (remove_asset_ids or [])
            if str(a).strip()
            .lstrip("-").isdigit()}
        remove_domains = {
            str(d) for d in
            (remove_domains or [])}

        # 主体既有评估(基线)
        assessments = await self.repo \
            .list_assessments(
                asset_id=None, limit=200)
        by_asset = {}
        for a in assessments:
            by_asset.setdefault(
                int(a.get("assetId")
                    or 0),
                []).append(a)

        def _subject_net(
                asset_list) -> float:
            total = 0.0
            for a in asset_list:
                latest = by_asset.get(
                    int(a.get("assetId"))
                ) or []
                if latest:
                    total += float(
                        latest[0].get(
                            "netContribution")
                        or 0)
            return round(total, 4)

        before = _subject_net(assets)
        kept = [
            a for a in assets
            if int(a.get("assetId"))
            not in remove_ids
            and a.get("domain")
            not in remove_domains]
        removed = [
            a for a in assets
            if a not in kept]
        if not removed:
            raise ValueError(
                "摘除集无效(无匹配资产——"
                "removeAssetIds/"
                "removeDomains)")
        after = _subject_net(kept)
        delta = round(
            after - before, 4)
        delta_pct = round(
            (delta / before * 100)
            if before else 0.0, 2)

        await self._track("stress", {
            "subjectId": subject_id,
            "removed": len(removed),
            "before": before,
            "after": after,
            "deltaPct": delta_pct,
        })
        return {
            "success": True,
            "subjectId": subject_id,
            "before": before,
            "after": after,
            "delta": delta,
            "deltaPct": delta_pct,
            "removedAssets": [
                {"assetId": int(
                    a.get("assetId")),
                 "domain": a.get(
                     "domain"),
                 "negative":
                     a.get("negative")}
                for a in removed],
            "note": "反事实压测——若失去"
                    f" {len(removed)} 项资产, "
                    f"净贡献度变动 "
                    f"{delta_pct:+.2f}%"
                    "(风险管理辅助)",
            "stressedAt": ts(),
        }

    # ============================================================
    # 观测面
    # ============================================================

    @staticmethod
    def scenario_view() -> dict:
        """场景折算表+流动性+衰减视图
        (观测面不受开关影响)"""
        from services.av62_registry import (
            liquidity_view,
        )
        return liquidity_view()

    # --------------------------------------------------------
    # 内部(45号 deposit 范式——fail-soft)
    # --------------------------------------------------------

    async def _deposit_45(self,
                          subject_id: int,
                          scenario: str,
                          value: float,
                          converted_by: str
                          ) -> dict | None:
        """45号 L2 platform_conduct 增益域
        deposit(56号补偿同款范式——
        纯调用 fail-soft 零改动)"""
        try:
            from services.trust_radar_service import (
                TrustRadarService,
            )
            evidence = (
                f"av62 场景信值折算增益"
                f"(主体 {subject_id}, 场景 "
                f"{scenario}, 折算值 "
                f"{value}——62号转化层)"
            )
            return await (
                TrustRadarService()
                .submit_deposit(
                    int(subject_id),
                    DEPOSIT_LAYER,
                    DEPOSIT_FACTOR,
                    observed=float(value),
                    peer_baseline=0.0,
                    evidence=evidence,
                    summary="62号场景信值"
                            "折算增益"
                            "(platform_conduct)",
                    sources=["av62_liquidity"],
                    voluntary=False,
                    verify_mode="v1"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_deposit45_failed %s: %s",
                subject_id, exc)
            return {"verified": False,
                    "error": str(exc)[:200]}

    async def get_active_half_life(self) -> int:
        """生效半衰期(fail-soft 回落
        默认 90 日)"""
        from services.av62_registry import (
            DECAY_HALF_LIFE_DAYS,
            HALF_LIFE_MAX,
            HALF_LIFE_MIN,
        )
        try:
            rec = await self.repo \
                .get_threshold("decay")
            if rec \
                    and rec.get("status") \
                    == "applied":
                days = int(
                    (rec.get("config")
                     or {}).get(
                        "halfLifeDays")
                    or 0)
                if HALF_LIFE_MIN \
                        <= days \
                        <= HALF_LIFE_MAX:
                    return days
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_halflife_failsoft: %s",
                exc)
        return DECAY_HALF_LIFE_DAYS

    async def get_scenario_multiplier(
            self, scenario: str) -> float:
        """生效场景乘子(fail-soft 回落
        默认 1.0)"""
        from services.av62_registry import (
            SCENARIO_MULT_MAX,
            SCENARIO_MULT_MIN,
        )
        try:
            rec = await self.repo \
                .get_threshold(
                    f"scenario:{scenario}")
            if rec \
                    and rec.get("status") \
                    == "applied":
                m = float(
                    (rec.get("config")
                     or {}).get(
                        "multiplier")
                    or 1.0)
                if SCENARIO_MULT_MIN \
                        <= m \
                        <= SCENARIO_MULT_MAX:
                    return m
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_scenmult_failsoft: %s",
                exc)
        return 1.0

    async def _track(self, event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "assetId": int(
                    detail.get("assetId")
                    or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_track_failed %s: %s",
                event_type, exc)
