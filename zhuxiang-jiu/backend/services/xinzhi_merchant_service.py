"""68号 P4·信值·臻选——商家信值体系服务

依据:《信值·臻选大模型》文档"商家信值认证/动态评级"+
《68号 创新规划方案》§三 P4/§八决策备忘5

三大能力(全确定性, LLM 禁入):
    ① 4+2 认证(38号审核工作台范式):
       四必查(主体资质/履约能力/服务保障/后台系统)——缺一不可;
       两加分(生态贡献/外部背书)——确定性加分。
    ② 预演沙盘(P7c 范式移植——"模拟→预警→建议书"三段式):
       申报配置 → 模拟 1000 单履约 → 信值轨迹预测 →
       《启航报告》(TOP3 风险 + 成长路线图)。
    ③ 动态评级 S-A-B-C-D:
       认证分 + 履约分(37号订单结算/评价只读聚合);
       升级自动生效留痕; 降级仅生成 46号建议书(处罚
       永不自动——宪法域)。

红线(宪法域):
    - 四必查缺一 → 认证拒绝(确定性, 不入人工队列)
    - 降级永不自动: regrade 降档 → 46号 submit_change
      pending 建议书, 等待人工裁决
    - LLM 禁入: 认证=材料齐备性/评级=公式/沙盘=推演
    - 37号只读: 履约数据从同盟订单/评价读取, 不回写
"""

import logging
from datetime import datetime, UTC

from repositories.xinzhi_repository import (
    XinzhiRepository,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-xinzhi-merchant"

# 46号审批总线 scorer(需先入册 SCORER_REGISTRY)
SCORER_ID = "xinzhi_merchant"

# ============================================================
# P4 常量(文档口径)
# ============================================================

# 四必查(主体资质/履约能力/服务保障/后台系统——缺一不可)
REQUIRED_CHECKS = (
    "entity",        # 主体资质(营业执照/许可证)
    "fulfillment",   # 履约能力(仓储/物流/供货证明)
    "service",       # 服务保障(售后/客服体系)
    "backend",       # 后台系统(库存/订单系统对接)
)
CHECK_LABELS = {
    "entity": "主体资质", "fulfillment": "履约能力",
    "service": "服务保障", "backend": "后台系统",
}

# 两加分(生态贡献/外部背书——确定性加分)
BONUS_ITEMS = ("eco_contribution", "external_endorsement")
BONUS_LABELS = {
    "eco_contribution": "生态贡献",
    "external_endorsement": "外部背书",
}
CERT_BASE_PER_CHECK = 10   # 每必查 10 分(共 40)
CERT_ECO_BONUS = 8          # 生态贡献 +8
CERT_ENDORSE_BONUS = 6      # 外部背书 +6
CERT_MAX = (CERT_BASE_PER_CHECK * len(REQUIRED_CHECKS)
            + CERT_ECO_BONUS + CERT_ENDORSE_BONUS)  # 54

# 履约分(37号只读聚合, 满分 46)
FULFILLMENT_COMPLETION_WEIGHT = 28   # 完成率权重
FULFILLMENT_PRAISE_WEIGHT = 18       # 好评率权重
FULFILLMENT_MAX = (FULFILLMENT_COMPLETION_WEIGHT
                   + FULFILLMENT_PRAISE_WEIGHT)

# 冷启动履约基准(无订单数据时的观察口径——防新商家即 D)
COLD_COMPLETION = 0.70
COLD_PRAISE = 0.85

# 商家评级阶梯(S≥90/A≥80/B≥70/C≥60/D<60——与 P0 用户雷达同口径)
MERCHANT_GRADE_LADDER = ((90, "S"), (80, "A"),
                         (70, "B"), (60, "C"), (0, "D"))

# 认证状态(38号范式: certified/rejected)
M_STATUS_CERTIFIED = "certified"
M_STATUS_REJECTED = "rejected"

# 沙盘模拟(文档"模拟1000单履约")
SIMULATED_ORDERS = 1000
SIM_LEGS = 10   # 轨迹分段(每段 100 单)

# 风险标签(确定性映射——TOP3 排序键)
RISK_LABELS = {
    "violations": "履约违约风险",
    "complaints": "客诉集中风险",
    "lateOrders": "时效不达风险",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def grade_of_merchant(score: float) -> str:
    """商家评级(确定性阶梯——与 P0 用户雷达同口径)"""
    for threshold, name in MERCHANT_GRADE_LADDER:
        if float(score) >= threshold:
            return name
    return "D"


def cert_score_of(checks: dict, bonuses: dict) -> tuple:
    """认证分(确定性: 四必查×10 + 加分项)

    Returns:
        (score, missing)——missing 为未通过的必查项列表
    """
    missing = [c for c in REQUIRED_CHECKS
               if not (checks or {}).get(c)]
    score = CERT_BASE_PER_CHECK * (
        len(REQUIRED_CHECKS) - len(missing))
    if (bonuses or {}).get("eco_contribution"):
        score += CERT_ECO_BONUS
    if (bonuses or {}).get("external_endorsement"):
        score += CERT_ENDORSE_BONUS
    return score, missing


class XinzhiMerchantService:
    """68号 P4·商家信值体系(4+2 认证+沙盘+动态评级)"""

    def __init__(self, repo: XinzhiRepository = None):
        self.repo = repo if repo is not None else XinzhiRepository()

    # ============================================================
    # ① 4+2 认证(38号范式)
    # ============================================================

    async def apply_certification(self, member_id: int,
                                 shop_name: str,
                                 checks: dict,
                                 bonuses: dict = None,
                                 alliance_merchant_id: int = None
                                 ) -> dict:
        """商家信值认证申请(4+2——确定性评估+留痕)

        四必查缺一 → 拒绝(确定性, 留痕不入人工队列);
        全过 → certified + 初始评级(冷启动履约基准)。

        Raises:
            KeyError: 会员不存在
            ValueError: 店名非法/重复申请
        """
        from repositories.member_repository import (
            MemberRepository)
        member = await MemberRepository().get_by_id(member_id)
        if member is None:
            raise KeyError(f"会员不存在(memberId={member_id})")
        if not (shop_name or "").strip():
            raise ValueError("店铺名称不能为空")
        existing = await self.repo.find_merchant_by_member(
            member_id)
        if existing is not None:
            raise ValueError(
                f"会员已有商家档案(merchantId="
                f"{existing.get('merchantId')}, "
                f"状态{existing.get('status')})")
        score, missing = cert_score_of(checks, bonuses)
        passed = not missing
        # 冷启动履约基准(无订单数据——观察口径)
        fulfill = self._cold_fulfillment()
        merchant_score = round(score + fulfill, 1)
        grade = grade_of_merchant(merchant_score) \
            if passed else "D"
        merchant_id = await self.repo.next_id("merchant")
        record = {
            "merchantId": merchant_id,
            "memberId": member_id,
            "shopName": shop_name.strip()[:60],
            "allianceMerchantId": alliance_merchant_id,
            "checks": {c: bool((checks or {}).get(c))
                       for c in REQUIRED_CHECKS},
            "bonuses": {
                "eco_contribution": bool(
                    (bonuses or {}).get(
                        "eco_contribution")),
                "externalEndorsement": bool(
                    (bonuses or {}).get(
                        "external_endorsement"))},
            "certScore": score,
            "certified": passed,
            "missingChecks": [CHECK_LABELS[m]
                              for m in missing],
            "status": (M_STATUS_CERTIFIED if passed
                       else M_STATUS_REJECTED),
            "grade": grade,
            "merchantScore": merchant_score
            if passed else 0.0,
            "fulfillmentScore": fulfill,
            "completionRate": COLD_COMPLETION,
            "praiseRate": COLD_PRAISE,
            "gradeHistory": [{
                "from": "",
                "to": grade,
                "reason": ("认证初始评级" if passed
                           else "认证未通过"),
                "changeId": 0,
                "auto": True,
                "at": _now_iso(),
            }],
            "simulation": None,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        await self.repo.save_merchant(record)
        logger.info("xinzhi_p4_cert m=%s mid=%s "
                    "passed=%s score=%s grade=%s",
                    member_id, merchant_id, passed,
                    score, grade)
        return record

    @staticmethod
    def _cold_fulfillment() -> float:
        """冷启动履约分(无订单数据观察口径)"""
        return round(COLD_COMPLETION
                     * FULFILLMENT_COMPLETION_WEIGHT
                     + COLD_PRAISE
                     * FULFILLMENT_PRAISE_WEIGHT, 1)

    # ============================================================
    # ③ 动态评级(37号只读聚合——升降级留痕, 降级走 46号)
    # ============================================================

    async def _read_fulfillment(
            self, alliance_merchant_id: int) -> tuple:
        """37号履约数据只读聚合(settled 完成率+好评率)

        Returns:
            (completionRate, praiseRate, factors 可解释)
        """
        if not alliance_merchant_id:
            return (COLD_COMPLETION, COLD_PRAISE,
                    ["无同盟关联(冷启动基准)"])
        from repositories.alliance_repository import (
            AllianceRepository)
        repo = AllianceRepository()
        orders = await repo.list_orders(
            merchant_id=alliance_merchant_id, limit=10000)
        factors = []
        if not orders:
            return (COLD_COMPLETION, COLD_PRAISE,
                    ["无同盟订单(冷启动基准)"])
        settled = sum(1 for o in orders
                      if o.get("settled"))
        completion = round(
            settled / len(orders), 4)
        factors.append(f"结算率 {settled}/{len(orders)}"
                       f"({completion:.0%})")
        reviews = await repo.list_reviews(
            merchant_id=alliance_merchant_id,
            folded=False, limit=10000)
        if reviews:
            praise = round(
                sum(1 for r in reviews
                    if int(r.get("score") or 0) >= 4)
                / len(reviews), 4)
            factors.append(f"好评率 {praise:.0%}"
                           f"({len(reviews)}条)")
        else:
            praise = COLD_PRAISE
            factors.append("无评价(冷启动好评基准)")
        return (completion, praise, factors)

    async def regrade(self, merchant_id: int,
                      operator: str = "admin") -> dict:
        """评级重算(动态——升级生效留痕, 降级走 46号建议)

        升级: 正向激励, 自动生效+留痕;
        降级: 处罚永不自动——仅生成 46号 submit_change
        pending 建议书(等待人工裁决), 本档位不变。

        Raises:
            KeyError: 档案不存在
            ValueError: 未认证档案
        """
        m = await self.repo.get_merchant(merchant_id)
        if m is None:
            raise KeyError(
                f"商家不存在(merchantId={merchant_id})")
        if not m.get("certified"):
            raise ValueError("未认证档案不可评级")
        completion, praise, factors = \
            await self._read_fulfillment(
                m.get("allianceMerchantId"))
        fulfill = round(
            completion * FULFILLMENT_COMPLETION_WEIGHT
            + praise * FULFILLMENT_PRAISE_WEIGHT, 1)
        new_score = round(
            float(m.get("certScore") or 0) + fulfill, 1)
        new_grade = grade_of_merchant(new_score)
        old_grade = str(m.get("grade") or "D")
        history = list(m.get("gradeHistory") or [])
        base = {
            "merchantId": merchant_id,
            "oldGrade": old_grade,
            "newGrade": new_grade,
            "oldScore": m.get("merchantScore"),
            "newScore": new_score,
            "fulfillmentScore": fulfill,
            "completionRate": completion,
            "praiseRate": praise,
            "factors": factors,
        }
        if new_grade == old_grade:
            await self.repo.update_merchant(merchant_id, {
                "merchantScore": new_score,
                "fulfillmentScore": fulfill,
                "completionRate": completion,
                "praiseRate": praise,
                "updatedAt": _now_iso()})
            return {**base, "changed": False,
                    "action": "hold"}
        # 升降序(阶梯序数比较)
        order = {g[1]: i for i, g in
                 enumerate(reversed(
                     MERCHANT_GRADE_LADDER))}
        upgrade = order[new_grade] > order[old_grade]
        if upgrade:
            history.append({
                "from": old_grade, "to": new_grade,
                "reason": "履约表现提升(自动升级)",
                "changeId": 0, "auto": True,
                "at": _now_iso()})
            await self.repo.update_merchant(merchant_id, {
                "grade": new_grade,
                "merchantScore": new_score,
                "fulfillmentScore": fulfill,
                "completionRate": completion,
                "praiseRate": praise,
                "gradeHistory": history,
                "updatedAt": _now_iso()})
            logger.info("xinzhi_p4_upgrade mid=%s %s→%s",
                        merchant_id, old_grade, new_grade)
            return {**base, "changed": True,
                    "action": "upgrade_applied"}
        # 降级: 46号建议书(处罚永不自动——宪法域)
        change = await self._submit_demotion(
            merchant_id, m, old_grade, new_grade,
            new_score, operator)
        return {**base, "changed": False,
                "action": "demotion_proposed",
                "changeProposal": change}

    async def _submit_demotion(self, merchant_id: int,
                               m: dict, old_grade: str,
                               new_grade: str,
                               new_score: float,
                               operator: str) -> dict:
        """降级建议书(46号 submit_change——pending 不生效)"""
        reason = (f"履约表现下滑: 商家{merchant_id}"
                  f"({m.get('shopName')}) 评级测算 "
                  f"{old_grade}→{new_grade}"
                  f"(测算分 {new_score}), 建议降档——"
                  f"处罚须人工裁决(宪法域)")
        try:
            from services.ai_governance_service import (
                AiGovernanceService)
            change = await AiGovernanceService() \
                .submit_change(
                    scorer_id=SCORER_ID,
                    kind="patch",
                    payload={
                        "merchantId": merchant_id,
                        "before": old_grade,
                        "after": new_grade,
                        "computedScore": new_score},
                    reason=reason[:500],
                    requested_by=operator)
            logger.warning(
                "xinzhi_p4_demotion_proposed mid=%s "
                "%s→%s changeId=%s",
                merchant_id, old_grade, new_grade,
                change.get("changeId"))
            return change
        except Exception as exc:
            # fail-soft: 46号不可达 → 建议书留痕本地
            logger.warning("xinzhi_p4_demotion_deferred "
                            "mid=%s: %s", merchant_id, exc)
            return {"changeId": 0, "status": "deferred",
                    "error": str(exc)[:200],
                    "note": "46号总线不可达, 建议书暂缓"}

    async def level_view(self, merchant_id: int) -> dict:
        """评级查询(透明——分数+归因+升降级历史)"""
        m = await self.repo.get_merchant(merchant_id)
        if m is None:
            raise KeyError(
                f"商家不存在(merchantId={merchant_id})")
        return {
            "merchantId": merchant_id,
            "shopName": m.get("shopName"),
            "certified": bool(m.get("certified")),
            "status": m.get("status"),
            "grade": m.get("grade"),
            "merchantScore": m.get("merchantScore"),
            "certScore": m.get("certScore"),
            "fulfillmentScore": m.get(
                "fulfillmentScore"),
            "completionRate": m.get("completionRate"),
            "praiseRate": m.get("praiseRate"),
            "gradeLadder": [
                {"min": t, "grade": g}
                for t, g in MERCHANT_GRADE_LADDER],
            "gradeHistory": m.get("gradeHistory") or [],
            "punishmentPolicy": ("降级仅生成 46号建议书"
                                 "——处罚永不自动(宪法域)"),
            "updatedAt": m.get("updatedAt"),
        }

    # ============================================================
    # ② 预演沙盘(P7c 范式——模拟→预警→建议书)
    # ============================================================

    async def simulate_voyage(
            self, merchant_id: int = None,
            daily_orders: int = 50,
            fulfillment_rate: float = 0.95,
            complaint_rate: float = 0.03,
            on_time_rate: float = 0.92,
            cert_score: float = None) -> dict:
        """商家预演沙盘(申报配置→模拟 1000 单→
        信值轨迹预测→《启航报告》)

        Args:
            merchant_id: 已认证商家(取其实际认证分;
                空=纯申报模式)
            cert_score: 纯申报模式下的认证分假设

        Raises:
            KeyError: 档案不存在
            ValueError: 参数非法
        """
        for name, v in (("日均单量", daily_orders),
                        ("履约率", fulfillment_rate),
                        ("客诉率", complaint_rate),
                        ("时效达标率", on_time_rate)):
            if v is None or float(v) < 0:
                raise ValueError(f"{name}非法({v})")
        if not 0 < float(fulfillment_rate) <= 1 \
                or not 0 <= float(complaint_rate) <= 1 \
                or not 0 < float(on_time_rate) <= 1:
            raise ValueError("比率须在 (0,1] / [0,1] 区间")
        base_cert = float(cert_score or 0)
        if merchant_id:
            m = await self.repo.get_merchant(merchant_id)
            if m is None:
                raise KeyError(
                    f"商家不存在(merchantId={merchant_id})")
            base_cert = float(m.get("certScore") or 0)
        # 确定性推演(1000 单)
        violations = round(
            SIMULATED_ORDERS * (1 - fulfillment_rate))
        complaints = round(
            SIMULATED_ORDERS * complaint_rate)
        late_orders = round(
            SIMULATED_ORDERS * (1 - on_time_rate))
        # 信值轨迹预测(10 段×100 单——确定性爬坡:
        # 认证分起步 → 稳态分收敛)
        steady_fulfill = round(
            fulfillment_rate
            * FULFILLMENT_COMPLETION_WEIGHT
            + (1 - complaint_rate)
            * FULFILLMENT_PRAISE_WEIGHT, 1)
        steady = round(
            base_cert + steady_fulfill, 1)
        cold = self._cold_fulfillment()
        trajectory = []
        for leg in range(1, SIM_LEGS + 1):
            score = round(
                base_cert + cold
                + (steady - base_cert - cold)
                * leg / SIM_LEGS, 1)
            trajectory.append({
                "leg": leg,
                "orders": leg * (SIMULATED_ORDERS
                                 // SIM_LEGS),
                "cumulativeScore": score})
        # TOP3 风险(确定性排序: 绝对量降序取前三)
        risks = sorted(
            (("violations", violations),
             ("complaints", complaints),
             ("lateOrders", late_orders)),
            key=lambda x: -x[1])[:3]
        top_risks = [{
            "key": k,
            "label": RISK_LABELS[k],
            "simulatedCount": v,
        } for k, v in risks if v > 0]
        # 成长路线图(确定性建议——每项提升的分数弹性)
        roadmap = [
            {"lever": "履约率",
             "from": f"{fulfillment_rate:.0%}",
             "to": "98%",
             "scoreGain": round(
                 (0.98 - fulfillment_rate)
                 * FULFILLMENT_COMPLETION_WEIGHT, 1)},
            {"lever": "客诉率",
             "from": f"{complaint_rate:.0%}",
             "to": "≤1%",
             "scoreGain": round(
                 (complaint_rate - 0.01)
                 * FULFILLMENT_PRAISE_WEIGHT, 1)},
            {"lever": "时效达标率",
             "from": f"{on_time_rate:.0%}",
             "to": "95%",
             "scoreGain": round(
                 (0.95 - on_time_rate)
                 * FULFILLMENT_COMPLETION_WEIGHT * 0.5,
                 1)},
        ]
        sim_id = await self.repo.next_id("sim")
        report = {
            "simId": sim_id,
            "merchantId": merchant_id or 0,
            "reportName": "启航报告",
            "config": {
                "dailyOrders": int(daily_orders),
                "fulfillmentRate": float(
                    fulfillment_rate),
                "complaintRate": float(complaint_rate),
                "onTimeRate": float(on_time_rate),
                "certScore": base_cert,
            },
            "simulatedOrders": SIMULATED_ORDERS,
            "violations": violations,
            "complaints": complaints,
            "lateOrders": late_orders,
            "trajectory": trajectory,
            "steadyScore": steady,
            "steadyGrade": grade_of_merchant(steady),
            "topRisks": top_risks,
            "roadmap": roadmap,
            "disclaimer": ("沙盘为确定性推演(非承诺); "
                           "风险预警供决策参考"),
            "generatedAt": _now_iso(),
        }
        if merchant_id:
            await self.repo.update_merchant(merchant_id, {
                "simulation": report,
                "updatedAt": _now_iso()})
        return report
