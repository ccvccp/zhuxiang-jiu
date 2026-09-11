"""智法·AI智能法务大模型 P3 电商合规深化(zf_commerce_service)

深化方案 §二(四) 电商合规深化: 从"内容审查"到"交易全链路风控"
    - 价格合规动态监测: 划线价/券后价/会员价逻辑+先涨后降检测
      → 《价格合规自查报告》备查
    - 预售/众筹合规护栏: 老酒封坛/新酒预售 → 《预售协议》+
      《风险提示书》(规范促销行为暂行规定)
    - 职业打假防御体系: 高风险画像+证据固化清单 → 《应诉证据包》

铁律: 检测全确定性阈值; 报告为备查/建议, 处置永不自动。
"""

import logging

from services.zf_fabric_service import _ZfStore, _round2, _now_iso

logger = logging.getLogger(__name__)

# ============================================================
# 价格合规检测阈值(确定性——《明码标价和禁止价格欺诈规定》口径)
# ============================================================

# 大促前 N 天内涨价超过该比例、促销时再降价 → "先涨后降"欺诈嫌疑
PRICE_HIKE_WINDOW_DAYS = 30
PRICE_HIKE_RATIO = 0.15
# 划线价须有成交依据: 高于近 30 日最低成交价×该倍数即"无依据划线"
STRIKETHROUGH_BASE_RATIO = 1.2
# 会员价高于券后价 → 会员权益虚标
MEMBER_PRICE_TOLERANCE = 0.0

# 职业打假风险画像阈值(确定性)
BLACKMAIL_COMPLAINT_LIMIT = 5       # 90 日投诉次数上限
BLACKMAIL_RETURN_RATIO = 0.6         # 退货率上限
BLACKMAIL_LAWSUIT_LIMIT = 1         # 涉诉次数上限

PRESALE_RULES = {
    "maxTermDays": 90,               # 预售交付期上限(暂行规定)
    "depositRatioCap": 0.2,          # 定金比例上限
    "mustClauses": ("交付时间", "违约责任", "退定金规则", "风险提示"),
}


class ZfCommerceService:
    """P3: 价格合规监测 + 预售护栏 + 职业打假防御"""

    def __init__(self, store: _ZfStore = None):
        self.store = store or _ZfStore()

    # ============================================================
    # 价格合规动态监测
    # ============================================================

    async def price_audit(self, product_id: str,
                          price_history: list[dict],
                          current: dict) -> dict:
        """价格链路检测 → 《价格合规自查报告》

        输入:
            price_history: [{day, dealPrice}] 近 30 日成交价序列
            current: {original, strikethrough, coupon, member}
        检测(确定性):
            - 先涨后降: 窗口内涨价>15% 且促销价低于涨价前 → 欺诈嫌疑
            - 划线价无依据: 高于近 30 日最低成交价×1.2
            - 会员价虚标: 会员价高于券后价
        """
        if not price_history:
            raise ValueError("历史价格序列不可为空")
        if not product_id:
            raise ValueError("商品 ID 不可为空")
        deal_prices = [float(p.get("dealPrice", 0)) for p in price_history]
        if any(p <= 0 for p in deal_prices):
            raise ValueError("历史成交价须为正")
        low = min(deal_prices)
        pre_hike_max = max(deal_prices)

        findings = []
        coupon = float(current.get("coupon", 0) or 0)
        member = float(current.get("member", 0) or 0)
        strikethrough = float(current.get("strikethrough", 0) or 0)

        # 1) 先涨后降
        if pre_hike_max > low * (1 + PRICE_HIKE_RATIO) \
                and coupon < pre_hike_max:
            findings.append({
                "type": "hike_then_drop",
                "name": "先涨后降",
                "detail": (f"近 30 日最高成交 {pre_hike_max} 较最低 {low} "
                           f"涨幅超 {PRICE_HIKE_RATIO:.0%} 且券后价 "
                           f"{coupon} 低于涨价前水平——价格欺诈嫌疑"),
                "law": "明码标价和禁止价格欺诈规定 第19条",
            })
        # 2) 划线价无依据
        if strikethrough > low * STRIKETHROUGH_BASE_RATIO:
            findings.append({
                "type": "unfounded_strikethrough",
                "name": "划线价无成交依据",
                "detail": (f"划线价 {strikethrough} 高于近 30 日最低成交 "
                           f"{low}×{STRIKETHROUGH_BASE_RATIO}, 无依据"),
                "law": "明码标价和禁止价格欺诈规定 第16条",
            })
        # 3) 会员价虚标
        if member > coupon + MEMBER_PRICE_TOLERANCE and coupon > 0:
            findings.append({
                "type": "member_price_inflated",
                "name": "会员价虚标",
                "detail": (f"会员价 {member} 高于券后价 {coupon}, "
                           "会员权益未兑现"),
                "law": "消费者权益保护法 第55条",
            })

        audit_id = await self.store.next_id("price_audit")
        record = {
            "auditId": audit_id, "productId": product_id,
            "currentPrices": current,
            "windowLow": low, "windowHigh": pre_hike_max,
            "findings": findings,
            "compliant": not findings,
            "conclusion": ("价格链路合规, 报告备查" if not findings
                           else f"检出 {len(findings)} 项风险, "
                                "建议下架整改(须人工)"),
            "note": "确定性阈值检测; 报告为自查备查, 处置永不自动",
            "auditedAt": _now_iso(),
        }
        await self.store.save("price_audits", audit_id, record)
        return record

    # ============================================================
    # 预售/众筹合规护栏
    # ============================================================

    async def presale_guard(self, product_id: str, term_days: int,
                            deposit: float, total_price: float,
                            presale_type: str = "new_wine") -> dict:
        """预售模式 → 《预售协议》+《风险提示书》

        校验(《规范促销行为暂行规定》):
            交付期 ≤90 天 / 定金 ≤20% / 四必备条款
        """
        if not product_id:
            raise ValueError("商品 ID 不可为空")
        if not term_days >= 1:
            raise ValueError("交付期须为正整数")
        if deposit <= 0 or total_price <= 0:
            raise ValueError("定金与总价须为正")

        violations = []
        if term_days > PRESALE_RULES["maxTermDays"]:
            violations.append(
                f"交付期 {term_days} 天超上限 "
                f"{PRESALE_RULES['maxTermDays']} 天")
        deposit_ratio = _round2(deposit / total_price)
        if deposit_ratio > PRESALE_RULES["depositRatioCap"]:
            violations.append(
                f"定金比例 {deposit_ratio:.0%} 超上限 "
                f"{PRESALE_RULES['depositRatioCap']:.0%}")
        if violations:
            raise ValueError("; ".join(violations)
                             + "——预售护栏拦截(暂行规定)")

        type_name = ("老酒封坛" if presale_type == "cellar"
                     else "新酒预售")
        guard_id = await self.store.next_id("presale")
        record = {
            "guardId": guard_id, "productId": product_id,
            "presaleType": presale_type, "presaleName": type_name,
            "termDays": term_days, "deposit": deposit,
            "totalPrice": total_price, "depositRatio": deposit_ratio,
            "agreement": {
                "title": f"{type_name}预售协议",
                "clauses": [
                    f"第1条 交付时间: 买家付款后 {term_days} 日内交付"
                    "(超期按日计违约金)",
                    "第2条 违约责任: 平台无法交付→双倍返还定金"
                    "+全额退款",
                    "第3条 退定金规则: 交付期届满前 7 日可无理由退定金",
                    "第4条 风险提示: 预售商品为定制生产, 不适用"
                    "七天无理由退货(质量问题除外)",
                ],
                "mustClauses": list(PRESALE_RULES["mustClauses"]),
            },
            "riskNotice": (f"《风险提示书》: {type_name}属预售模式, "
                           f"交付周期 {term_days} 天; 酒类商品"
                           "未成年人禁购; 过量饮酒有害健康"),
            "note": "协议为确定性模板; 上线须人工审批+页面前端展示",
            "guardedAt": _now_iso(),
        }
        await self.store.save("presales", guard_id, record)
        return record

    # ============================================================
    # 职业打假防御体系
    # ============================================================

    async def anti_blackmail(self, member_id: int, order_id: str,
                             complaints_90d: int, return_ratio: float,
                             lawsuit_count: int = 0) -> dict:
        """高风险画像 + 证据固化清单 → 《应诉证据包》

        画像(确定性阈值): 90 日投诉≥5 / 退货率≥60% / 涉诉≥1
        → 命中任一即标记高风险, 生成证据固化建议。
        """
        if not member_id or not order_id:
            raise ValueError("会员 ID 与订单号不可为空")
        if complaints_90d < 0 or not 0 <= return_ratio <= 1 \
                or lawsuit_count < 0:
            raise ValueError("画像输入越界(投诉/退货率/涉诉)")

        markers = []
        if complaints_90d >= BLACKMAIL_COMPLAINT_LIMIT:
            markers.append(f"90 日投诉 {complaints_90d} 次≥"
                           f"{BLACKMAIL_COMPLAINT_LIMIT}")
        if return_ratio >= BLACKMAIL_RETURN_RATIO:
            markers.append(f"退货率 {return_ratio:.0%}≥"
                           f"{BLACKMAIL_RETURN_RATIO:.0%}")
        if lawsuit_count >= BLACKMAIL_LAWSUIT_LIMIT:
            markers.append(f"涉诉 {lawsuit_count} 次≥"
                           f"{BLACKMAIL_LAWSUIT_LIMIT}")
        high_risk = bool(markers)

        pack_id = await self.store.next_id("evidence_pack")
        record = {
            "packId": pack_id, "memberId": member_id, "orderId": order_id,
            "complaints90d": complaints_90d,
            "returnRatio": return_ratio,
            "lawsuitCount": lawsuit_count,
            "highRisk": high_risk, "riskMarkers": markers,
            "evidenceChecklist": [
                "商品详情页快照(含价格/宣传文案/规格——交易时点版)",
                "客服沟通记录存档(含平台内信/通话录音索引)",
                "发货称重视频+物流揽收回执(防调包主张)",
                "订单快照+支付流水(资金与订单一致性)",
                "退货开箱验收视频(拆封过程全程记录)",
                "产品批次质检报告+数字产品护照(品质背书)",
            ] if high_risk else ["常规留档: 订单快照+物流回执"],
            "defensePackage": (f"《应诉证据包》: {order_id} 全流程证据"
                               "固化清单已生成") if high_risk
            else "常规订单留档(未触发高风险画像)",
            "note": "画像为确定性阈值; 标记不拦截交易(避免误伤), "
                    "仅证据固化建议",
            "generatedAt": _now_iso(),
        }
        await self.store.save("evidence_packs", pack_id, record)
        return record
