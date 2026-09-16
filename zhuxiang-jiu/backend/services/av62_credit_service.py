"""62号·AI智能无形资产估值——信值调整分支(P3 创新升级)

设计依据: 信值导向估值重构(2026-09-16)——估值逻辑从
"交易定价"转向"风险与偿债能力评估": 不仅回答资产
"值多少钱", 更回答"违约或清算时能变现多少"及
"价值波动对主体信用的影响程度"。

核心公式(信值与公允解耦——本分支只算调整因子,
不参与公允价值计算):
    V_credit = V_fair × alpha_liquidity
             × beta_legal × gamma_stability

    V_fair: 公允信值贡献(估值引擎 assess 最新记录的
            netContribution 绝对值)
    alpha_liquidity(流动性折扣): 信值场景下无法快速
        变现的资产大幅折减——基于 DOMAIN_LIQUIDITY
        域档映射(high 0.95/medium 0.85/low 0.60/
        none 0.00)+ 三主体差异化 alphaCap 强制上限
    beta_legal(权属确定性系数): 存在质押/诉讼/共有人
        争议的资产信值权重降低(clean 1.00/pledged
        0.70/litigated 0.40/disputed 0.30/
        unverified 0.80——登记缺省保守)
    gamma_stability(价值稳定性系数): 高波动资产作为
        信值锚点保守处理——估值置信档映射
        (high 0.95/medium 0.85/low 0.60)

三主体差异化(封闭注册 CREDIT_ROLE_RULES):
    - organization 事业单位: 合规优先+公共效用货币化
      (科研成果未转化流动性折扣≥60%; 特许经营公共
      属性剥离仅计市场化增值)
    - enterprise 企业: 偿债覆盖+动态压力测试
      (商标品牌信值上限锁公允 40%; 客户关系高集中
      权重减半; 技术类人员流失风险)
    - personal 个人: 可执行性+隐私合规边界
      (不可转让资质原则上不计入; 自媒体/IP 流动性
      折扣≥80%)

信值有效性判定(valid):
    - 负资产(risk 域)不参与信值(仅扣减——不可洗白);
    - 权属争议(disputed)信值无效;
    - 三系数乘积 < 0.10(CREDIT_VALID_THRESHOLD)无效;
    - 须先完成公允估值(assess)——V_fair 锚定。

报告模板(信值评估报告——区别于公允价值报告):
    强制三章节: 流动性分析 / 法律瑕疵披露 /
    压力测试引用 + 醒目免责标注。

守护(透明审计——计算依据可追溯):
    每次信值评估留痕三系数计算依据(档位来源/
    权属态/置信档)与数据来源(评估记录 ID 锚定)。

铁律: LLM 禁入——全部确定性计算(阈值+映射+乘法)。
"""

import logging

from core.helpers import ts

from repositories.av62_repository import (
    Av62Repository,
)

logger = logging.getLogger("av62_credit_service")

MODEL_VERSION = "v1-av62-credit"

CREDIT_DISCLAIMER = (
    "本估值仅供信用参考, 不构成交易定价依据")


def _clamp(value: float, low: float,
           high: float) -> float:
    return round(
        max(low, min(high, float(value))), 4)


class Av62CreditService:
    """信值调整分支(α/β/γ 三系数——与公允估值解耦)"""

    def __init__(self):
        self.repo = Av62Repository()

    # ============================================================
    # 信值评估(资产级——决策面)
    # ============================================================

    async def credit_assess(self,
                            asset_id: int,
                            assessed_by: str = "admin"
                            ) -> dict:
        """信值评估(V_credit 三系数 + 信值报告)

        前置: 资产已存在且已完成至少一次公允估值
        (assess——V_fair 锚定最新评估记录)。

        Raises:
            KeyError: 资产不存在
            ValueError: off 态/未完成公允估值/
                负资产不参与信值
        """
        from services.av62_service import (
            require_active_mode,
        )
        require_active_mode()
        asset = await self.repo.get_asset(
            int(asset_id))
        if not asset:
            raise KeyError(
                f"资产 {asset_id} 不存在")
        role = asset.get("role")
        domain = asset.get("domain")

        # 负资产不参与信值(仅扣减——不可洗白铁律)
        if asset.get("negative") \
                or domain == "risk":
            raise ValueError(
                "负资产(risk 域)不参与信值评估"
                "(仅扣减, 不可洗白)")

        # V_fair 锚定: 最新公允评估记录
        assessments = await self.repo._list(
            "assessment", 10,
            assetId=int(asset_id))
        if not assessments:
            raise ValueError(
                "须先完成公允估值(assess)再信值评估"
                "——V_fair 锚定最新评估记录")
        latest = assessments[0]
        v_fair = abs(float(
            latest.get("netContribution") or 0))

        # ---- α 流动性折扣 ----
        from services.av62_registry import (
            CREDIT_ALPHA_BY_TIER,
            CREDIT_BETA_BY_LEGAL,
            CREDIT_GAMMA_BY_TIER,
            CREDIT_ROLE_RULES,
            CREDIT_VALID_THRESHOLD,
            liquidity_of,
        )
        tier = liquidity_of(domain)
        alpha = CREDIT_ALPHA_BY_TIER.get(
            tier, 0.0)
        alpha_basis = f"域档 {domain}→{tier}"

        # ---- β 权属确定性 ----
        legal_status = str(
            asset.get("legalStatus")
            or "unverified")
        beta = CREDIT_BETA_BY_LEGAL.get(
            legal_status, 0.80)

        # ---- γ 价值稳定性(置信档) ----
        confidence_tier = str(
            latest.get("confidenceTier")
            or "low")
        gamma = CREDIT_GAMMA_BY_TIER.get(
            confidence_tier, 0.60)

        # ---- 三主体差异化规则(封闭) ----
        rule = CREDIT_ROLE_RULES.get(
            (role, domain)) or {}
        rule_note = rule.get("note", "")
        if "alphaCap" in rule:
            cap = float(rule["alphaCap"])
            if alpha > cap:
                alpha = _clamp(cap, 0.0, 1.0)
                alpha_basis += (
                    f"; 主体规则上限 {cap}"
                    f"({role})")
        v_credit_cap = None
        if "vCreditCap" in rule:
            v_credit_cap = float(
                rule["vCreditCap"])

        # ---- V_credit 合成 ----
        product = round(
            alpha * beta * gamma, 6)
        v_credit = round(
            v_fair * product, 4)
        capped = False
        if v_credit_cap is not None:
            ceiling = round(
                v_fair * v_credit_cap, 4)
            if v_credit > ceiling:
                v_credit = ceiling
                capped = True

        # ---- 信值有效性判定 ----
        invalid_reasons = []
        if legal_status == "disputed":
            invalid_reasons.append(
                "权属共有人争议(disputed)——信值无效")
        if product < CREDIT_VALID_THRESHOLD:
            invalid_reasons.append(
                f"三系数乘积 {product:.4f} 低于"
                f"有效门槛 {CREDIT_VALID_THRESHOLD}")
        valid = not invalid_reasons

        # ---- 审计因子链(守护: 计算依据可追溯) ----
        factors = {
            "vFair": {
                "value": v_fair,
                "source": f"评估#{latest.get('assessId')}"
                          f" v{latest.get('version')}"
                          f" netContribution",
            },
            "alpha": {
                "value": alpha,
                "basis": alpha_basis,
                "tier": tier,
            },
            "beta": {
                "value": beta,
                "legalStatus": legal_status,
            },
            "gamma": {
                "value": gamma,
                "confidenceTier": confidence_tier,
            },
            "roleRule": {
                "key": f"{role}.{domain}",
                "note": rule_note,
                "vCreditCap": v_credit_cap,
                "capped": capped,
            },
        }

        # ---- 信值报告(三章节+免责) ----
        report = {
            "templateType": "credit",
            "disclaimer": CREDIT_DISCLAIMER,
            "sections": {
                "liquidityAnalysis": {
                    "title": "流动性分析",
                    "tier": tier,
                    "alpha": alpha,
                    "basis": alpha_basis,
                },
                "legalDisclosure": {
                    "title": "法律瑕疵披露",
                    "legalStatus": legal_status,
                    "beta": beta,
                    "clean": legal_status == "clean",
                },
                "stressReference": {
                    "title": "压力测试引用",
                    "confidenceTier":
                        confidence_tier,
                    "gamma": gamma,
                    "assessId": latest.get(
                        "assessId"),
                    "note": "反事实压测见"
                            " POST /api/av62/stress",
                },
            },
        }

        # ---- 落库 + 审计留痕 ----
        credit_id = await \
            self.repo.next_credit_id()
        record = {
            "creditId": credit_id,
            "assetId": int(asset_id),
            "subjectId": int(
                asset.get("subjectId") or 0),
            "role": role, "domain": domain,
            "assessId": int(
                latest.get("assessId") or 0),
            "vFair": v_fair,
            "alpha": alpha, "beta": beta,
            "gamma": gamma,
            "product": product,
            "vCredit": v_credit,
            "valid": valid,
            "invalidReasons": invalid_reasons,
            "factors": factors,
            "report": report,
            "assessedBy": str(
                assessed_by or "admin"),
            "createdAt": ts(),
        }
        await self.repo.save_credit(record)
        await self._track(
            int(asset_id), "credit_assess", {
                "creditId": credit_id,
                "vFair": v_fair,
                "vCredit": v_credit,
                "alpha": alpha, "beta": beta,
                "gamma": gamma, "valid": valid,
                "assessedBy": assessed_by,
            })
        logger.info(
            "av62_credit_assessed asset=%s "
            "vCredit=%s valid=%s",
            asset_id, v_credit, valid)
        return record

    # ============================================================
    # 观测面(信值报告)
    # ============================================================

    async def get_credit(self,
                         asset_id: int) -> dict:
        """最新信值评估报告(观测面)

        Raises:
            KeyError: 资产不存在/无信值评估记录
        """
        asset = await self.repo.get_asset(
            int(asset_id))
        if not asset:
            raise KeyError(
                f"资产 {asset_id} 不存在")
        credits = await self.repo.list_credits(
            asset_id=int(asset_id), limit=1)
        if not credits:
            raise KeyError(
                f"资产 {asset_id} 暂无信值评估记录"
                f"(POST /api/av62/assets/"
                f"{asset_id}/credit/assess)")
        return credits[0]

    async def list_credit_history(
            self, asset_id: int,
            limit: int = 20) -> list[dict]:
        """信值评估历史(观测面)"""
        return await self.repo.list_credits(
            asset_id=int(asset_id),
            limit=limit)

    # ============================================================
    # 内部(审计留痕——复用 62号事件追踪)
    # ============================================================

    async def _track(self, ref_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "assetId": int(ref_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:
            logger.warning(
                "av62_credit_track_failed %s: %s",
                event_type, exc)
