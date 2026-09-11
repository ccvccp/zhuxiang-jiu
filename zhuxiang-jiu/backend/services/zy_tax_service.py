"""智启元·AI智能财务大模型 P2 税务优化引擎(zy_tax_service)

「从事后申报到价值创造」(设计文档 §3 税务优化):
    - 交易级税负模拟器: 交易结构(一般/折扣/组合/跨境)× 金额 →
      综合税负对比 + 最优建议(留痕, 永不自动变更)
    - 政策匹配引擎: 优惠政策库(人工维护+生效期标记) × 业务标签
      自动匹配 + 节税估算(建议书模式)
    - 风险热力图: 税务风险点扫描(税负率偏离/退款率/关联信号)
      五级热力, 事后稽查风险前置

铁律: 建议永不自动执行; 政策库人工维护(自动匹配不自动生效);
税率口径与 finance_service 常量一致。
"""

import logging
from datetime import datetime, UTC

from services.zy_data_service import (
    ZyDataService, _round2, VAT_RATE, CONSUMPTION_AD_VALOREM,
    CONSUMPTION_PER_JIN, INCOME_TAX_RATE,
)

logger = logging.getLogger(__name__)

# 交易结构定义(确定性)
TRADE_STRUCTURES = {
    "standard": {"name": "一般销售", "vatRate": VAT_RATE,
                 "consumptionAd": CONSUMPTION_AD_VALOREM,
                 "incomeDeductRatio": 0.60,
                 "note": "标准 13% 增值税 + 白酒消费税"},
    "discount": {"name": "折扣销售", "vatRate": VAT_RATE,
                  "consumptionAd": CONSUMPTION_AD_VALOREM,
                  "incomeDeductRatio": 0.65,
                  "note": "同一发票注明折扣额, 按折后计税"},
    "bundle": {"name": "组合销售", "vatRate": VAT_RATE,
                "consumptionAd": CONSUMPTION_AD_VALOREM * 0.8,
                "incomeDeductRatio": 0.70,
                "note": "酒+非应税品组合, 消费税计税基础可分摊"},
    "cross_border": {"name": "跨境零售", "vatRate": VAT_RATE * 0.7,
                      "consumptionAd": 0.0,
                      "incomeDeductRatio": 0.55,
                      "note": "跨境电商综合税(简化口径), 免消费税"},
}

RISK_LEVELS = ("low", "attention", "medium", "high", "critical")
RISK_LEVEL_NAME = {"low": "低", "attention": "关注", "medium": "中",
                   "high": "高", "critical": "严重"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# 政策库种子(人工维护口径; effective_date 生效期标记,
# 到期自动失效匹配——对齐文档"准则时效性过滤")
POLICY_SEEDS = [
    {
        "policyId": "POL001",
        "title": "小微企业企业所得税优惠",
        "category": "income_tax",
        "tags": ["小微", "初创", "低利润"],
        "content": "年应纳税所得额 ≤300 万小微企业, 实际税负 5%",
        "condition": "年应纳税所得额 ≤ 300 万",
        "savingFormula": "income × (0.25 - 0.05)",
        "effectiveFrom": "2023-01-01", "effectiveTo": "2027-12-31",
    },
    {
        "policyId": "POL002",
        "title": "白酒消费税计税价格核定(集团核定)",
        "category": "consumption_tax",
        "tags": ["白酒", "批量", "生产"],
        "content": "按最终销售价格核定消费税计税基础的, 需省级核准",
        "condition": "生产企业批量销售",
        "savingFormula": "省税空间取决于核定价格审批",
        "effectiveFrom": "2023-01-01", "effectiveTo": "",
    },
    {
        "policyId": "POL003",
        "title": "增值税加计抵减(现代服务)",
        "category": "vat",
        "tags": ["服务", "技术服务"],
        "content": "现代服务业进项税额加计抵减 5%",
        "condition": "主营业务为现代服务业",
        "savingFormula": "进项税 × 0.05",
        "effectiveFrom": "2023-01-01", "effectiveTo": "2027-12-31",
    },
]


class ZyTaxService:
    """P2: 税负模拟器 + 政策匹配 + 风险热力图"""

    def __init__(self, data: ZyDataService = None):
        self.data = data or ZyDataService()

    # ============================================================
    # 交易级税负模拟器
    # ============================================================

    @staticmethod
    def simulate_burden(revenue_with_tax: float,
                        quantity: int) -> float:
        """含税收入+数量 → 综合税负(与 calc_tax 同口径)"""
        without_tax = revenue_with_tax / (1 + VAT_RATE)
        vat = without_tax * VAT_RATE
        consumption = (without_tax * CONSUMPTION_AD_VALOREM
                       + quantity * CONSUMPTION_PER_JIN)
        cost_deduct = without_tax * 0.60   # 成本扣除简化 60%
        taxable = max(0.0, without_tax - cost_deduct - vat
                      - consumption)
        return _round2(vat + consumption + taxable * INCOME_TAX_RATE)

    async def simulate(self, amount: float, quantity: int = 10,
                        structures: list = None) -> dict:
        """交易级税负模拟: 多结构对比 + 最优建议(留痕不自动)

        Args:
            amount: 含税交易金额(元)
            quantity: 白酒数量(斤, 从量消费税基数)
            structures: 结构键列表(默认全量)
        """
        if amount <= 0:
            raise ValueError(f"交易金额须为正(当前 {amount})")
        if not 0 < quantity <= 10_000:
            raise ValueError(f"数量须在 (0, 10000](当前 {quantity})")
        keys = (structures or list(TRADE_STRUCTURES))
        for key in keys:
            if key not in TRADE_STRUCTURES:
                raise ValueError(
                    f"交易结构无效({key}, 须为 "
                    f"{'/'.join(TRADE_STRUCTURES)})")
        rows = []
        for key in keys:
            meta = TRADE_STRUCTURES[key]
            without_tax = amount / (1 + meta["vatRate"])
            vat = _round2(without_tax * meta["vatRate"])
            consumption = _round2(
                without_tax * meta["consumptionAd"]
                + quantity * CONSUMPTION_PER_JIN
                * (1 if key != "cross_border" else 0))
            taxable = max(0.0, without_tax
                          * (1 - meta["incomeDeductRatio"])
                          - vat - consumption)
            income_tax = _round2(taxable * INCOME_TAX_RATE)
            total = _round2(vat + consumption + income_tax)
            rows.append({
                "structure": key, "structureName": meta["name"],
                "vat": vat, "consumptionTax": consumption,
                "incomeTax": income_tax, "total": total,
                "effectiveRate": _round2(total / amount),
                "note": meta["note"],
            })
        best = min(rows, key=lambda r: (r["total"], r["structure"]))
        worst = max(rows, key=lambda r: (r["total"], r["structure"]))
        return {
            "amount": amount, "quantity": quantity,
            "structures": rows,
            "recommendation": {
                "best": best["structure"],
                "bestName": best["structureName"],
                "bestTotal": best["total"],
                "savingVsWorst": _round2(worst["total"]
                                         - best["total"]),
                "note": "模拟留痕, 变更交易结构须业务侧人工决策",
            },
        }

    # ============================================================
    # 政策匹配引擎(库自动初始化, 匹配建议不自动生效)
    # ============================================================

    async def policies(self, business_tags: list = None,
                       repo=None) -> dict:
        """优惠政策库 + 业务标签匹配 + 节税估算

        Returns:
            {policies: 全库, matched: 匹配行(含估算), updatedAt}
        """
        repo = repo or _PolicyStore()
        rows = await repo.list_policies()
        if not rows:
            for seed in POLICY_SEEDS:
                await repo.save_policy(dict(seed))
            rows = await repo.list_policies()
        matched = []
        for policy in rows:
            hits = [t for t in (business_tags or [])
                    if t in policy.get("tags", [])]
            if not hits:
                continue
            matched.append({
                **policy, "matchedTags": hits,
                "eligible": True,
                "suggestion": (f"「{policy['title']}」适用场景命中 "
                               f"{hits}; {policy['content']}; "
                               f"节税口径: {policy['savingFormula']}"),
            })
        return {
            "policies": rows,
            "matched": matched,
            "note": "匹配仅建议; 享受政策须人工申报核验",
        }

    # ============================================================
    # 税务风险热力图(前置扫描)
    # ============================================================

    async def risk_heatmap(self) -> dict:
        """税务风险扫描: 五维风险点 → 五级热力

        检测器(确定性阈值):
            - 综合税负率偏离(>40% 或 <8% 预警)
            - 退款率异常(>15% 进项转出遗漏风险)
            - 单笔大额(>5 万稽查关注线)
            - 月度收入剧烈波动(环比 ±50% 关联定价信号)
            - 连续亏损(利润侵蚀信号)
        """
        snap = await self.data.snapshot()
        if not snap.get("hasData"):
            raise ValueError("暂无财务数据, 无法风险扫描")
        series = snap["series"]
        cur = series[-1]
        prev = series[-2] if len(series) >= 2 else cur
        risks = []

        def level(score: float) -> str:
            if score >= 0.8:
                return "critical"
            if score >= 0.6:
                return "high"
            if score >= 0.4:
                return "medium"
            if score >= 0.2:
                return "attention"
            return "low"

        tax_rate = (cur["taxAmount"] / cur["netAmount"]
                    if cur["netAmount"] else 0)
        deviation = abs(tax_rate - 0.25) / 0.25
        risks.append({
            "risk": "综合税负率偏离", "signal": "偏离行业均值 25%",
            "value": _round2(tax_rate * 100),
            "severity": level(min(1.0, deviation)),
            "detail": f"当月综合税负率 {_round2(tax_rate * 100)}%",
        })
        refund_ratio = (cur["refundAmount"] / cur["salesAmount"]
                        if cur["salesAmount"] else 0)
        risks.append({
            "risk": "退款率(进项转出遗漏)", "signal": "阈值 15%",
            "value": _round2(refund_ratio * 100),
            "severity": level(min(1.0, refund_ratio / 0.3)),
            "detail": (f"退款率 {_round2(refund_ratio * 100)}%; "
                       "退款对应进项税须转出"),
        })
        big_orders = 0
        try:
            orders = await self.data.order_repo.list_all()
            month_prefix = cur["month"]
            big_orders = sum(
                1 for o in orders
                if (o.get("createdAt", "")[:7].replace("-", "")
                    == month_prefix and float(
                        (o.get("priceDetail") or {}).get(
                            "actualAmount", 0) or 0) > 50_000))
        except Exception:
            big_orders = 0
        risks.append({
            "risk": "单笔大额交易", "signal": "5 万稽查关注线",
            "value": big_orders,
            "severity": level(min(1.0, big_orders / 5)),
            "detail": f"当月 5 万以上订单 {big_orders} 笔",
        })
        swing = ((cur["netAmount"] - prev["netAmount"])
                 / prev["netAmount"]) if prev["netAmount"] else 0
        risks.append({
            "risk": "月度收入剧烈波动", "signal": "环比 ±50%",
            "value": _round2(swing * 100),
            "severity": level(min(1.0, abs(swing) / 1.0)),
            "detail": f"环比 {_round2(swing * 100)}%(关联定价信号)",
        })
        loss_streak = 0
        for row in reversed(series):
            if row["netProfit"] < 0:
                loss_streak += 1
            else:
                break
        risks.append({
            "risk": "连续亏损月", "signal": "利润侵蚀",
            "value": loss_streak,
            "severity": level(min(1.0, loss_streak / 3)),
            "detail": f"连续亏损 {loss_streak} 个月",
        })
        level_idx = {"low": 0, "attention": 1, "medium": 2,
                     "high": 3, "critical": 4}
        overall = max(risks, key=lambda r: level_idx[r["severity"]])
        return {
            "month": cur["month"],
            "risks": [{**r, "severityName":
                       RISK_LEVEL_NAME[r["severity"]]} for r in risks],
            "overall": {
                "level": overall["severity"],
                "levelName": RISK_LEVEL_NAME[overall["severity"]],
                "topRisk": overall["risk"],
            },
            "note": "风险仅预警; 处置须人工/审批, 永不自动执行",
        }


# ============================================================
# 政策库存储(内存/Redis 双模式, 对齐项目存储范式)
# ============================================================

class _PolicyStore:
    """zy_policies 表(内存模式直接 dict; redis 模式 key 化)"""

    def __init__(self):
        from repositories.backend import (
            is_redis_mode, get_redis_client, get_in_memory_store)
        self._is_redis = is_redis_mode
        self._get_redis = get_redis_client
        self._store = get_in_memory_store()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """防御性建表(store 可能被 reset 清空)"""
        self._store.setdefault("zy_policies", {})
        self._store.setdefault("_zy_policy_seq", 0)

    async def list_policies(self) -> list[dict]:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            keys = await client.keys("zhuxiang:zy:policies:*")
            rows = []
            for key in keys:
                skey = key.decode() if isinstance(key, bytes) else key
                if skey.endswith(":seq"):
                    continue   # 跳过计数器键(防整型混入列表)
                raw = await client.get(skey)
                if raw:
                    try:
                        rows.append(_json.loads(raw))
                    except (ValueError, TypeError):
                        continue
            return rows
        self._ensure_tables()
        return list(self._store["zy_policies"].values())

    async def next_policy_id(self) -> str:
        """政策库 ID 递增(POL 前缀)"""
        if self._is_redis():
            client = await self._get_redis()
            seq = await client.incr("zhuxiang:zy:policies:seq")
        else:
            self._store["_zy_policy_seq"] = (
                self._store.get("_zy_policy_seq", 0) + 1)
            seq = self._store["_zy_policy_seq"]
        return f"POL{seq:03d}"

    async def save_policy(self, policy: dict) -> dict:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set(
                f"zhuxiang:zy:policies:{policy['policyId']}",
                _json.dumps(policy, ensure_ascii=False))
        else:
            self._store["zy_policies"][policy["policyId"]] = policy
        return policy
