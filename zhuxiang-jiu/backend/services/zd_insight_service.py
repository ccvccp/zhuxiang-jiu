"""智单·AI智能订单大模型 P0 洞察中枢(zd_insight_service)

「意图驱动」入口(对齐 zy_qa_service 范式): 自然语言问订单 →
关键词意图路由(五域确定性词表) → 织物查询层数字 → 中文句子
模板拼接(数字 100% 插值, LLM 禁入判定链)。

三大分析能力:
    - 五域问答: 单量/GMV/退款/履约/异常(未知域返回引导语)
    - 订单健康体检: 四维确定性打分(状态分布/资金安全/履约时效/
      退款健康), 四维等权 0.25, 报告含 formula 与建议书 disposition
    - 三维画像: 会员×商品×时段聚合(top 榜 + 分布)

铁律: 同输入同输出; 裁决/处置类输出一律建议书, 永不自动执行。
"""

import logging

from services.zd_fabric_service import (
    _ZdStore, ZdFabricService, TERMINAL_STATUSES,
    _round2, _now_iso, _clamp, _parse_iso)

logger = logging.getLogger(__name__)

# 意图域关键词(命中优先级从上到下: 异常 > 退款 > 履约 > GMV > 单量)
QA_DOMAINS = (
    ("anomaly", ("异常", "风险", "秒退", "囤货", "高频", "预警", "可疑")),
    ("refund", ("退款", "退货", "退单")),
    ("fulfillment", ("履约", "发货", "签收", "时效", "物流", "送达")),
    ("gmv", ("GMV", "gmv", "Gmv", "交易额", "销售额", "成交额", "客单价")),
    ("volume", ("单量", "订单量", "订单数", "多少单", "订单", "单数")),
)

DOMAIN_NAME = {
    "volume": "单量", "gmv": "GMV", "refund": "退款",
    "fulfillment": "履约", "anomaly": "异常",
}

# 体检基线(确定性阈值)
TERMINAL_TARGET = 0.60          # 终态占比目标 60%
STATUS_PENALTY = 200.0          # 终态占比每偏差 1.0 扣 200 分
FUNDS_PENALTY = 250.0           # 资金风险占比每 1.0 扣 250 分
FULFILLMENT_BASELINE_H = 72.0   # 履约 72 小时基线
REFUND_BASELINE = 0.08          # 退款率 8% 基线
CHECKUP_WEIGHT = 0.25           # 四维等权

# 画像时段桶(小时区间, 左闭右开)
TIME_BUCKETS = (
    ("凌晨(0-6时)", 0, 6), ("上午(6-12时)", 6, 12),
    ("下午(12-18时)", 12, 18), ("晚间(18-24时)", 18, 24),
)


def _hour_of(iso_value) -> int:
    """createdAt 小时(解析失败返回 -1, 聚合时跳过)"""
    dt = _parse_iso(iso_value)
    if dt is not None:
        return dt.hour
    text = str(iso_value or "")
    try:
        return int(text[11:13])
    except (ValueError, IndexError):
        return -1


class ZdInsightService:
    """P0: 五域问答 + 四维体检 + 三维画像"""

    def __init__(self, fabric: ZdFabricService = None,
                 store: _ZdStore = None):
        self.store = store or _ZdStore()
        self.fabric = fabric or ZdFabricService(self.store)

    # ============================================================
    # NL 问答(关键词路由 → 确定性查询 → 模板拼接)
    # ============================================================

    async def qa(self, text: str) -> dict:
        """自然语言订单问答(数字全部来自织物查询层)

        Returns:
            {domain, domainName, answer, dataSnapshot, reasoning}
        """
        question = (text or "").strip()
        domain = self._route(question)
        ov = await self.fabric.overview()
        if not ov.get("hasData"):
            return {
                "domain": domain,
                "domainName": DOMAIN_NAME.get(domain, "未知"),
                "answer": "暂无订单数据(织物为空), 产生交易后即可问答。",
                "dataSnapshot": {},
                "reasoning": "数据织物为空(诚实零值, 不编造数字)",
            }
        handlers = {
            "volume": self._answer_volume,
            "gmv": self._answer_gmv,
            "refund": self._answer_refund,
            "fulfillment": self._answer_fulfillment,
            "anomaly": self._answer_anomaly,
        }
        if domain not in handlers:
            return self._guidance()
        result = handlers[domain](ov)
        result["reasoning"] = (
            f"关键词路由→{DOMAIN_NAME[domain]}域; 取数织物总览"
            f"(订单 {ov['totalOrders']} 单); 数字 100% 来自查询层")
        return result

    @staticmethod
    def _route(question: str) -> str:
        """关键词意图路由(确定性词表, 无命中返回 unknown)"""
        for domain, keywords in QA_DOMAINS:
            if any(kw in question for kw in keywords):
                return domain
        return "unknown"

    @staticmethod
    def _guidance() -> dict:
        """未知域引导语(列出五域可问问题)"""
        return {
            "domain": "unknown", "domainName": "未知域",
            "answer": ("未识别到订单问题域, 可试试: 问单量"
                       "(\"现在有多少订单\")/问 GMV(\"GMV 是多少\")/"
                       "问退款(\"退款率多少\")/问履约(\"平均履约时长\")/"
                       "问异常(\"有什么异常订单\")。"),
            "dataSnapshot": {},
            "reasoning": "关键词路由未命中五域(确定性词表)",
        }

    @staticmethod
    def _answer_volume(ov: dict) -> dict:
        dist = ov["statusDistribution"]
        return {
            "domain": "volume", "domainName": "单量",
            "answer": (f"当前累计订单 {ov['totalOrders']} 单"
                       f"(已支付 {ov['paidOrders']} 单), "
                       f"主链已完成 {dist.get('COMPLETED', 0)} 单, "
                       f"待付款 {dist.get('PENDING', 0)} 单; "
                       f"覆盖会员 {len(ov['memberAggregates'])} 位。"),
            "dataSnapshot": {
                "totalOrders": ov["totalOrders"],
                "paidOrders": ov["paidOrders"],
                "completed": dist.get("COMPLETED", 0)},
        }

    @staticmethod
    def _answer_gmv(ov: dict) -> dict:
        return {
            "domain": "gmv", "domainName": "GMV",
            "answer": (f"累计 GMV ¥{ov['gmv']}"
                       f"(实际支付口径, 已支付 {ov['paidOrders']} 单), "
                       f"平均客单价 ¥{ov['avgOrderValue']}; "
                       f"退款率 {_round2(ov['refundRate'] * 100)}%。"),
            "dataSnapshot": {
                "gmv": ov["gmv"], "avgOrderValue": ov["avgOrderValue"],
                "refundRate": ov["refundRate"]},
        }

    @staticmethod
    def _answer_refund(ov: dict) -> dict:
        dist = ov["statusDistribution"]
        return {
            "domain": "refund", "domainName": "退款",
            "answer": (f"退款率 {_round2(ov['refundRate'] * 100)}%"
                       f"({ov['refundedOrders']}/{ov['totalOrders']} 单), "
                       f"另有退款中 {dist.get('RETURNING', 0)} 单; "
                       f"单笔裁决可用 /api/order-ai/refund-score/{{orderId}}。"),
            "dataSnapshot": {
                "refundRate": ov["refundRate"],
                "refundedOrders": ov["refundedOrders"],
                "returning": dist.get("RETURNING", 0)},
        }

    @staticmethod
    def _answer_fulfillment(ov: dict) -> dict:
        avg = ov["avgFulfillmentHours"]
        samples = ov["fulfillmentSamples"]
        verdict = "达标" if (samples and avg <= FULFILLMENT_BASELINE_H) \
            else "超时" if samples else "无样本"
        return {
            "domain": "fulfillment", "domainName": "履约",
            "answer": (f"平均履约 {avg} 小时(支付→签收, 样本 {samples} 单), "
                       f"72 小时基线{verdict}; 履约 ETA 预测可用 "
                       f"/api/order-ai/eta 查看。"),
            "dataSnapshot": {
                "avgFulfillmentHours": avg,
                "fulfillmentSamples": samples,
                "baselineHours": FULFILLMENT_BASELINE_H},
        }

    @staticmethod
    def _answer_anomaly(ov: dict) -> dict:
        dist = ov["statusDistribution"]
        pending = dist.get("PENDING", 0)
        returning = dist.get("RETURNING", 0)
        risk_ratio = (pending + returning) / ov["totalOrders"] \
            if ov["totalOrders"] else 0.0
        return {
            "domain": "anomaly", "domainName": "异常",
            "answer": (f"当前风险观测: 待支付 {pending} 单"
                       f"(占比 {_round2(risk_ratio * 100)}%), "
                       f"退款中 {returning} 单, "
                       f"退款率 {_round2(ov['refundRate'] * 100)}%; "
                       f"高频下单/大额囤货/秒退款三类深扫可用 "
                       f"POST /api/order-ai/anomaly-scan。"),
            "dataSnapshot": {
                "pending": pending, "returning": returning,
                "fundsRiskRatio": _round2(risk_ratio),
                "refundRate": ov["refundRate"]},
        }

    # ============================================================
    # 订单健康体检(四维确定性打分, 建议书)
    # ============================================================

    async def checkup(self) -> dict:
        """订单健康体检四维打分(各 0-100, 等权 0.25)

        口径(全部确定性):
            - 状态分布健康度 = 100 − |终态占比 − 60%| × 200
            - 资金安全 = 100 − (待支付+退款中)占比 × 250
            - 履约时效 = 100 × 72h / 平均履约小时(无样本记 0)
            - 退款健康 = 100 × 8% / 退款率(零退款记 100)
        空数据诚实零值(四维 0 分); 报告为建议书。
        """
        ov = await self.fabric.overview()
        total = ov["totalOrders"]
        dist = ov["statusDistribution"]
        has_data = bool(ov.get("hasData"))
        if not has_data:
            dims = [
                {"dim": "状态分布健康度", "raw": 0.0, "score": 0.0,
                 "explain": "暂无订单数据"},
                {"dim": "资金安全", "raw": 0.0, "score": 0.0,
                 "explain": "暂无订单数据"},
                {"dim": "履约时效", "raw": 0.0, "score": 0.0,
                 "explain": "暂无订单数据"},
                {"dim": "退款健康", "raw": 0.0, "score": 0.0,
                 "explain": "暂无订单数据"},
            ]
        else:
            terminal = sum(dist.get(s, 0) for s in TERMINAL_STATUSES)
            tr = terminal / total
            dims = [{
                "dim": "状态分布健康度", "raw": _round2(tr * 100),
                "score": _round2(_clamp(
                    100 - abs(tr - TERMINAL_TARGET) * STATUS_PENALTY)),
                "explain": (f"终态占比 {_round2(tr * 100)}%"
                            f"(终态 {terminal}/{total}, 目标 60%)"),
            }]
            pending = dist.get("PENDING", 0)
            returning = dist.get("RETURNING", 0)
            risk_ratio = (pending + returning) / total
            dims.append({
                "dim": "资金安全", "raw": _round2(risk_ratio * 100),
                "score": _round2(_clamp(100 - risk_ratio * FUNDS_PENALTY)),
                "explain": (f"待支付+退款中占比 "
                            f"{_round2(risk_ratio * 100)}%"
                            f"({pending}+{returning}/{total})"),
            })
            samples = ov["fulfillmentSamples"]
            avg = ov["avgFulfillmentHours"]
            if samples:
                score = _round2(_clamp(
                    100 * FULFILLMENT_BASELINE_H / max(avg, 0.01)))
                explain = (f"平均履约 {avg} 小时"
                           f"(样本 {samples}, 基线 72h)")
            else:
                score, explain = 0.0, "暂无履约样本(无已签收订单)"
            dims.append({
                "dim": "履约时效", "raw": avg, "score": score,
                "explain": explain,
            })
            rate = ov["refundRate"]
            score = (100.0 if rate <= 0.0001
                     else _round2(_clamp(100 * REFUND_BASELINE / rate)))
            dims.append({
                "dim": "退款健康", "raw": _round2(rate * 100),
                "score": score,
                "explain": (f"退款率 {_round2(rate * 100)}%"
                            f"({ov['refundedOrders']}/{total}, 基线 8%)"),
            })
        total_score = _round2(
            sum(d["score"] for d in dims) * CHECKUP_WEIGHT)
        if not has_data:
            grade = "无数据"
        elif total_score >= 80:
            grade = "A 优秀"
        elif total_score >= 65:
            grade = "B 良好"
        elif total_score >= 50:
            grade = "C 关注"
        else:
            grade = "D 预警"
        suggestions = self._suggestions(dims)
        report = {
            "reportId": await self.store.next_id("checkups"),
            "hasData": has_data,
            "totalOrders": total,
            "dimensions": dims,
            "totalScore": total_score,
            "grade": grade,
            "formula": ("总分 = 0.25×(状态分布+资金安全+履约时效+退款健康); "
                        "状态分布 = 100−|终态占比−60%|×200; "
                        "资金安全 = 100−(待支付+退款中)占比×250; "
                        "履约时效 = 100×72h/平均履约; "
                        "退款健康 = 100×8%/退款率(各维 clamp [0,100], 确定性)"),
            "suggestions": suggestions,
            "disposition": "体检报告为建议书; 决定权在管理员(不自动执行处置)",
            "checkedAt": _now_iso(),
        }
        await self.store.save("checkups", report["reportId"], report)
        return report

    @staticmethod
    def _suggestions(dims: list[dict]) -> list[str]:
        """低分维度建议(确定性模板, 阈值 60)"""
        actions = {
            "状态分布健康度": "终态占比偏离 60% 目标: 建议核查在途积压"
                             "与超时关闭策略",
            "资金安全": "待支付/退款中占比偏高: 建议核查支付挽回"
                        "与退款审核节奏",
            "履约时效": "平均履约超 72h 基线: 建议加急排产与运力补位"
                        "(建议书)",
            "退款健康": "退款率超 8% 基线: 建议核查商品质量"
                        "与秒退款风险单",
        }
        rows = [actions[d["dim"]] for d in dims if d["score"] < 60]
        if not rows:
            rows = ["四维均在健康线(60)之上, 维持既有运营节奏"]
        return rows

    async def checkups(self, limit: int = 50) -> list[dict]:
        """体检报告历史留痕"""
        rows = await self.store.list("checkups")
        return sorted(rows, key=lambda r: str(r.get("checkedAt", "")),
                      reverse=True)[:max(1, int(limit))]

    # ============================================================
    # 三维画像(会员×商品×时段, 观测建议)
    # ============================================================

    async def portrait(self) -> dict:
        """会员×商品×时段三维聚合画像(top 榜 + 时段分布)"""
        orders = await self.fabric.orders()
        ov = await self.fabric.overview()
        total = len(orders)
        bucket_counts = [0] * len(TIME_BUCKETS)
        for order in orders:
            hour = _hour_of(order.get("createdAt"))
            if hour < 0:
                continue
            for idx, (_, lo, hi) in enumerate(TIME_BUCKETS):
                if lo <= hour < hi:
                    bucket_counts[idx] += 1
                    break
        buckets = []
        for idx, (name, _, _) in enumerate(TIME_BUCKETS):
            count = bucket_counts[idx]
            buckets.append({
                "bucket": name, "count": count,
                "share": _round2(count / total) if total else 0.0,
            })
        peak = max(buckets, key=lambda b: b["count"]) if buckets else None
        record = {
            "portraitId": await self.store.next_id("portraits"),
            "hasData": bool(ov.get("hasData")),
            "totalOrders": total,
            "topMembers": ov["memberAggregates"][:10],
            "topProducts": ov["productAggregates"][:10],
            "timeBuckets": buckets,
            "peakBucket": peak["bucket"] if peak and total else None,
            "note": ("口径: 会员/商品按已支付金额聚合; 时段按 createdAt "
                     "小时分桶(确定性聚合)"),
            "disposition": "画像为观测建议; 精准营销等动作须管理员确认",
            "generatedAt": _now_iso(),
        }
        await self.store.save("portraits", record["portraitId"], record)
        return record

    async def portraits(self, limit: int = 50) -> list[dict]:
        """画像快照历史留痕"""
        rows = await self.store.list("portraits")
        return sorted(rows, key=lambda r: str(r.get("generatedAt", "")),
                      reverse=True)[:max(1, int(limit))]
