"""智运·AI智能物流大模型 P2 风控与回执(zw_risk_service)

创新规划 §二 P2: 设计文档§八补(四防风控)+§五(验货回执)+§6.6(理赔)落地
    - 四防风控评分: 防破损/防丢失/防延误/风险前置(确定性多因子
      0-100 → 四级)
    - 验货回执(团购专属): 结构化开箱验货单(应收/实收/差异/照片清单)
    - 理赔工单: 破损/丢失/延误/污损四类(建议书, 赔付审批须人工)

铁律: 评分全确定性公式; 理赔赔付永不自动; 既有订单零改动。
"""

import logging

from services.zw_fabric_service import _ZwStore, _round2, _now_iso

logger = logging.getLogger(__name__)

# 风控因子权重(确定性——四防)
W_DAMAGE = 0.35     # 防破损
W_LOSS = 0.25        # 防丢失
W_DELAY = 0.40       # 防延误

# 因子系数
FRAGILE_BASE = 40.0          # 酒类基础破损风险分
FRAGILE_WEIGHT_FACTOR = 2.0  # 每 kg 追加分
REMOTE_LOSS_BONUS = 30.0     # 偏远地区丢失风险追加
HIGH_VALUE_LINE = 5000.0     # 高货值线

RISK_LEVELS = ("low", "medium", "high", "extreme")

CLAIM_TYPES = ("damage", "loss", "delay", "stain")
CLAIM_TYPE_NAMES = {
    "damage": "破损理赔", "loss": "丢失理赔",
    "delay": "延误理赔", "stain": "污损理赔",
}
# 理赔赔付标准(设计文档 6.6)
CLAIM_STANDARDS = {
    "damage": ("保价金额全额赔付", 7),
    "loss": ("保价金额全额赔付", 15),
    "delay": ("退还运费", 5),
    "stain": ("退还包装费", 3),
}

REMOTE_PROVINCES = {"新疆", "西藏", "青海", "内蒙古", "甘肃", "宁夏"}


class ZwRiskService:
    """P2: 四防风控评分 + 验货回执 + 理赔工单"""

    def __init__(self, store: _ZwStore = None):
        self.store = store or _ZwStore()

    # ============================================================
    # 四防风控评分(确定性多因子)
    # ============================================================

    async def risk_assess(self, order_type: str, weight: float,
                          piece_count: int, insured_value: float,
                          receiver_province: str = "",
                          urgent: bool = False) -> dict:
        """下单前风控前置评分(0-100 → 四级)

        防破损分 = 40 + 2/kg(酒类易碎基座+重量)
        防丢失分 = 偏远+30 / 高货值+20 / 基础 20
        防延误分 = 时效要求+30 / 基础 25
        综合 = 0.35×破损 + 0.25×丢失 + 0.40×延误
        """
        if weight < 0 or piece_count <= 0 or insured_value < 0:
            raise ValueError("重量/件数/货值不可为负(件数须为正)")

        # 防破损(酒类易碎 + 重量)
        damage_score = min(
            100.0, FRAGILE_BASE + weight * FRAGILE_WEIGHT_FACTOR)

        # 防丢失(偏远 + 高货值)
        loss_score = 20.0
        remote = any(p in str(receiver_province or "")
                    for p in REMOTE_PROVINCES)
        factors = []
        if remote:
            loss_score += REMOTE_LOSS_BONUS
            factors.append(f"偏远地区({receiver_province})+30")
        if insured_value >= HIGH_VALUE_LINE:
            loss_score += 20.0
            factors.append(f"高货值 ¥{insured_value}+20")
        loss_score = min(100.0, loss_score)

        # 防延误(时效要求)
        delay_score = 25.0
        if urgent:
            delay_score += 30.0
            factors.append("时效要求(urgent)+30")
        delay_score = min(100.0, delay_score)

        total = _round2(W_DAMAGE * damage_score
                        + W_LOSS * loss_score
                        + W_DELAY * delay_score)
        level = ("extreme" if total >= 80 else
                 "high" if total >= 60 else
                 "medium" if total >= 40 else "low")

        assess_id = await self.store.next_id("risk")
        record = {
            "riskId": assess_id, "orderType": order_type,
            "weight": weight, "pieceCount": piece_count,
            "insuredValue": insured_value,
            "damageScore": _round2(damage_score),
            "lossScore": _round2(loss_score),
            "delayScore": _round2(delay_score),
            "riskScore": total, "riskLevel": level,
            "factors": factors,
            "formula": (f"{W_DAMAGE}×防破损{damage_score:.0f} + "
                        f"{W_LOSS}×防丢失{loss_score:.0f} + "
                        f"{W_DELAY}×防延误{delay_score:.0f}"),
            "suggestions": self._mitigations(total, remote,
                                              insured_value),
            "note": "确定性多因子评分; 处置建议书, 拦截永不自动",
            "assessedAt": _now_iso(),
        }
        await self.store.save("risk_assess", assess_id, record)
        return record

    @staticmethod
    def _mitigations(total: float, remote: bool,
                     insured_value: float) -> list[str]:
        """分级缓解建议(建议书口径)"""
        items = []
        if total >= 60:
            items.append("高风险单: 建议加固包装(木架/双层气泡膜)"
                         "并足额保价")
        if remote:
            items.append("偏远地区: 建议预留 +2 天时效并提前告知用户")
        if insured_value >= HIGH_VALUE_LINE:
            items.append(f"高货值 ¥{insured_value}: 建议保价运输"
                         "(费率 0.5%)")
        if not items:
            items.append("常规风险: 标准防震包装即可")
        return items

    # ============================================================
    # 验货回执(团购专属, 设计文档 5.5)
    # ============================================================

    async def inspect_receipt(self, order_id: str, expected_count: int,
                              actual_count: int, inspector: str,
                              photos: list[str] = None,
                              remark: str = "") -> dict:
        """团购开箱验货回执(结构化)

        校验: 实收 < 应收 → 差异标记(补寄工单建议);
        实收 > 应收 → 多收标记(留痕); 相等 → 通过。
        """
        if not order_id or not inspector:
            raise ValueError("订单号与验货人不可为空")
        if expected_count <= 0 or actual_count < 0:
            raise ValueError("应收为正/实收不可为负")

        diff = actual_count - expected_count
        if diff == 0:
            result = "pass"
            note = "数量一致, 验货通过"
        elif diff < 0:
            result = "shortage"
            note = (f"少收 {abs(diff)} 件——建议补寄工单"
                    "(须人工确认)")
        else:
            result = "surplus"
            note = f"多收 {diff} 件(留痕核实)"

        receipt_id = await self.store.next_id("inspect")
        record = {
            "receiptId": receipt_id, "orderId": order_id,
            "expectedCount": expected_count,
            "actualCount": actual_count, "diff": diff,
            "result": result, "note": note,
            "photos": photos or [],
            "checklist": [
                "清点数量(核对发货清单)",
                "检查外包装(破损/变形)",
                "抽检瓶身(碎裂/漏液)",
                "拍照留证",
            ],
            "inspector": inspector, "remark": remark,
            "disposition": "差异处置须人工(补寄/核实永不自动)",
            "inspectedAt": _now_iso(),
        }
        await self.store.save("inspect_receipts", receipt_id, record)
        return record

    # ============================================================
    # 理赔工单(建议书, 设计文档 6.6)
    # ============================================================

    async def create_claim(self, waybill_no: str, order_id: str,
                           carrier: str, claim_type: str,
                           claim_amount: float,
                           description: str = "",
                           evidence_urls: list[str] = None) -> dict:
        """创建理赔工单(四类; 赔付审批须人工)

        立案校验: 类型四选一/金额为正。
        标准赔付口径按设计文档 6.6(展示用, 实际赔付人工审批)。
        """
        if not waybill_no or not order_id:
            raise ValueError("运单号与订单号不可为空")
        if claim_type not in CLAIM_TYPES:
            raise ValueError(f"理赔类型无效({claim_type}); "
                             f"支持: {'/'.join(CLAIM_TYPES)}")
        if claim_amount <= 0:
            raise ValueError("理赔金额须为正")

        standard, sla_days = CLAIM_STANDARDS[claim_type]
        claim_id = await self.store.next_id("claim")
        record = {
            "claimId": claim_id,
            "claimNo": f"CLM-{claim_id:06d}",
            "waybillNo": waybill_no, "orderId": order_id,
            "carrier": carrier,
            "claimType": claim_type,
            "claimTypeName": CLAIM_TYPE_NAMES[claim_type],
            "claimAmount": _round2(claim_amount),
            "description": description,
            "evidenceUrls": evidence_urls or [],
            "standard": standard, "slaDays": sla_days,
            "status": "pending_review",
            "disposition": "理赔建议书: 赔付审批须人工, "
                           "永不自动赔付",
            "createdAt": _now_iso(),
        }
        await self.store.save("claims", claim_id, record)
        return record

    async def claims(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("claims")
        return sorted(rows, key=lambda r: r.get("createdAt", ""),
                      reverse=True)[:limit]
