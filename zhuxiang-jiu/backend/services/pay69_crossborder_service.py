"""69号·AI智能支付大模型 跨境预研沙盘
(pay69_crossborder_service, P9——远期)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§4.7/§七 P9):
    ① 币种/管辖区识别规则库草案
       (封闭注册——确定性查表)
    ② 合规预演(管辖区→单证要求
       /外汇管制级/数字货币口径
       ——建议书范式, 全合成)
    ③ 汇率换算+对冲建议档位
       (沙盘合成汇率, 确定性; 生产
       实接走外部行情商)
    ④ 监管政策追踪观测面(规则库
       变更走慢环 46号审批)

铁律(规划 §4.7——合规红线):
    - 永不实接数字货币渠道(仅试点
      参与方; 沙盘口径)
    - 沙盘内数据全合成(汇率非实时)
    - 本模块永不产生真实资金操作
"""

import logging

from core.helpers import ts

from repositories.pay69_repository import (
    Pay69Repository,
)
from services.pay69_registry import (
    CROSSBORDER_CURRENCIES,
    HEDGE_TIERS,
    JURISDICTIONS,
    JURISDICTION_RULES,
    SANDBOX_FX_RATES,
    SANDBOX_ONLY,
    MODEL_VERSION,
)

logger = logging.getLogger("pay69_crossborder")


class Pay69CrossborderService:
    """69号跨境预研沙盘(P9)"""

    def __init__(self):
        self.repo = Pay69Repository()

    # ============================================================
    # ① 合规预演(币种×管辖区识别)
    # ============================================================

    async def compliance_preview(
            self, currency: str,
            jurisdiction: str,
            amount_cny: float) -> dict:
        """跨境合规预演(沙盘——管辖区
        规则库确定性查表+对冲建议)

        全合成: 汇率/单证/通道口径均为
        规则库草案——建议书范式, 永不
        触达真实资金

        Raises:
            KeyError: 币种/管辖区域外
            ValueError: 金额非法/本位币
            无跨境语义
        """
        if currency not in \
                CROSSBORDER_CURRENCIES:
            raise KeyError(
                f"币种域外: {currency}"
                f"(须 "
                f"{CROSSBORDER_CURRENCIES})")
        if jurisdiction not in \
                JURISDICTIONS:
            raise KeyError(
                f"管辖区域外: "
                f"{jurisdiction}"
                f"(须 {JURISDICTIONS})")
        amount = round(
            float(amount_cny or 0), 2)
        if amount <= 0:
            raise ValueError(
                f"金额非法: {amount}(须>0)")
        if currency == "CNY":
            raise ValueError(
                "CNY 为本位币——无跨境"
                "换算语义(沙盘)")
        rate = SANDBOX_FX_RATES.get(
            ("CNY", currency))
        if rate is None:
            raise KeyError(
                f"沙盘汇率缺 (CNY/"
                f"{currency})——预研"
                f"范围外")
        rule = dict(
            JURISDICTION_RULES[
                jurisdiction])
        converted = round(
            amount * rate, 2)
        # 对冲建议档位(确定性查表)
        hedge_tier, hedge_action, \
            hedge_note = self._hedge_of(
                amount)
        record = {
            "sandboxOnly": SANDBOX_ONLY,
            "currency": currency,
            "jurisdiction": jurisdiction,
            "amountCny": amount,
            "sandboxRate": rate,
            "convertedAmount": converted,
            "fxControl":
                rule["fxControl"],
            "digitalCurrency":
                rule["digitalCurrency"],
            "requiredDocs": list(
                rule["requiresDocs"]),
            "sandboxNote":
                rule["sandboxNote"],
            "hedge": {
                "tier": hedge_tier,
                "action": hedge_action,
                "note": hedge_note,
            },
            "disclaimer": (
                "沙盘预研——全合成数据, "
                "永不实接渠道; 生产实接需"
                "持牌机构合规评审"),
            "previewedAt": ts(),
        }
        await self.repo.save_event({
            "type": "crossborder_preview",
            "detail": {
                "currency": currency,
                "jurisdiction":
                    jurisdiction,
                "amountCny": amount,
                "sandboxOnly":
                    SANDBOX_ONLY,
            },
            "at": ts(),
        })
        return record

    @staticmethod
    def _hedge_of(amount: float):
        """对冲档位(封闭查表)"""
        for i, (cap, action, note) \
                in enumerate(
                    HEDGE_TIERS, 1):
            if amount <= cap:
                return (i, action, note)
        return (len(HEDGE_TIERS),
                HEDGE_TIERS[-1][1],
                HEDGE_TIERS[-1][2])

    # ============================================================
    # ② 观测面(规则库/追踪)
    # ============================================================

    def crossborder_dict(self) -> dict:
        """跨境规则库公示(币种/管辖区
        /汇率/对冲档——观测面)"""
        return {
            "modelVersion":
                MODEL_VERSION,
            "sandboxOnly": SANDBOX_ONLY,
            "currencies": list(
                CROSSBORDER_CURRENCIES),
            "jurisdictions": {
                j: {
                    "label":
                        r["label"],
                    "fxControl":
                        r["fxControl"],
                    "digitalCurrency":
                        r["digitalCurrency"],
                }
                for j, r in
                JURISDICTION_RULES
                .items()
            },
            "sandboxFxRates": {
                f"CNY/{q}": v
                for (b, q), v in
                SANDBOX_FX_RATES.items()
            },
            "hedgeTiers": [
                {"cap": (c if c
                         != float("inf")
                         else None),
                 "action": a,
                 "note": n}
                for c, a, n in
                HEDGE_TIERS
            ],
            "ironRules": (
                "永不实接数字货币渠道"
                "(沙盘口径)",
                "汇率全合成(非实时行情)",
                "规则库变更走 46号审批"
                "(慢环)"),
        }

    async def policy_track_view(self) -> dict:
        """监管政策追踪观测面(规则库
        变更留痕——慢环审批队列联动)"""
        # P7 假设中 paramId 属跨境域的
        # 建议书(若未来 JURISDICTION_
        # RULES 纳入白名单)——当前
        # 观测 P9 预演事件流
        events = await self.repo\
            .list_events(limit=100)
        previews = [
            {"type": "crossborder_preview",
             **(e.get("detail") or {})}
            for e in events
            if e.get("type")
            == "crossborder_preview"
        ]
        return {
            "modelVersion":
                MODEL_VERSION,
            "sandboxOnly":
                SANDBOX_ONLY,
            "previewCount":
                len(previews),
            "recentPreviews":
                previews[:20],
            "policyChangesQueue": (
                "跨境规则库变更走 P7 进化"
                "引擎慢环(46号审批)——当前"
                "规则库为草案基线"),
            "trackedAt": ts(),
        }
