"""智酿运通 P6 多角色智能提醒引擎(zw_alert_service)

创新规划 §三 P6: 从"被动查询"到"主动关怀"
    - 五角色触发面: 消费者/客服/仓储/运营/管理层
    - 全部确定性模板拼接, 数据 100% 查询层插值(LLM 禁入;
      如配置 LLM 仅可润色文案——当前走规则模板轨)
    - 提醒为草稿+建议模式; ack 确认/驳回闭环, 驳回记负样本留痕

触发面(数据源确定性):
    - 消费者: P1 延误预警 → 致歉+券补偿建议; 签收单 → 品鉴内容推送建议
    - 客服:   P1 异常四检测器聚合 → 一键回复话术(轨迹摘要+方案)
    - 仓储:   日发货量预测 vs 仓容日承载 → 爆仓预警+《加急提货单》
    - 运营:   渠道均费超基准 15% → 《物流成本分析报告》
    - 管理层: 破损率超 0.5% → 《整改建议书》(主因渠道归因)
"""

import logging

from services.zw_fabric_service import (
    _ZwStore, ZwFabricService, _round2, _now_iso)
from services.zw_track_service import ZwTrackService
from repositories.logistics_repository import CARRIER_NAMES

logger = logging.getLogger(__name__)

ALERT_ROLES = ("consumer", "service", "warehouse", "operations",
               "management")
ALERT_ROLE_NAMES = {
    "consumer": "消费者", "service": "客服",
    "warehouse": "仓储", "operations": "运营", "management": "管理层",
}

# 触发阈值(确定性)
WAREHOUSE_DAILY_CAPACITY = 200    # 仓容日承载(单/日)
WAREHOUSE_WARN_RATIO = 0.85       # 超 85% 触发爆仓预警
FEE_BASELINE = 30.0               # 渠道均费基准(对齐 P0 口径)
FEE_SURGE_RATIO = 1.15            # 超基准 15% 触发成本预警
DAMAGE_RATE_LINE = 0.005          # 破损率超 0.5% 触发整改预警
COMPENSATION_COUPON = 5           # 延误致歉券面值(元)


class ZwAlertService:
    """P6: 五角色智能提醒 + 确认闭环"""

    def __init__(self, store: _ZwStore = None,
                 fabric: ZwFabricService = None,
                 track: ZwTrackService = None):
        self.store = store or _ZwStore()
        self.fabric = fabric or ZwFabricService(self.store)
        self.track = track or ZwTrackService(store=self.store)

    async def _save_alert(self, role: str, title: str, body: str,
                          action: dict) -> dict:
        record = {
            "alertId": await self.store.next_id("alerts"),
            "role": role, "roleName": ALERT_ROLE_NAMES[role],
            "title": title, "body": body,
            "action": action,          # 动作建议(永不自动执行)
            "status": "pending",       # pending/acked/dismissed
            "raisedAt": _now_iso(),
        }
        await self.store.save("alerts", record["alertId"], record)
        return record

    # ============================================================
    # 触发面扫描(逐角色确定性判定)
    # ============================================================

    async def _consumer_alerts(self) -> list[dict]:
        """消费者: 延误致歉+券补偿 / 签收后品鉴内容推送建议"""
        alerts = []
        delays = (await self.track.delay_warnings(limit=100)) \
            .get("delays", [])
        for d in delays[:5]:          # 最多 5 条防刷屏
            waybill = d.get("waybillNo", "")
            alerts.append(await self._save_alert(
                "consumer", "配送延误致歉(模板)",
                f"亲爱的竹香酒会员, 您的订单({waybill})物流进度较缓, "
                "预计延迟送达, 我们深表歉意",
                {"type": "compensation", "couponValue": COMPENSATION_COUPON,
                 "suggest": f"赠送 ¥{COMPENSATION_COUPON} 无门槛券(人工"
                            "确认后发放)",
                 "learningSignal": "券核销率/复购率(回流观测)"}))
        signed = [o for o in await self.fabric.repo.list_orders(limit=500)
                  if o.get("status") == "signed"]
        for _ in signed[:3]:          # 签收后关怀建议(最多3条)
            alerts.append(await self._save_alert(
                "consumer", "签收后关怀(模板)",
                "您购买的竹香酒已签收, 恭喜到手! 附上醒酒指南与品鉴"
                "视频, 静置 3-5 天风味更佳",
                {"type": "content_push", "contentId": "wakeup-guide-001",
                 "suggest": "推送醒酒指南+品鉴视频(人工确认后发送)",
                 "learningSignal": "内容打开率(回流观测)"}))
        return alerts

    async def _service_alerts(self) -> list[dict]:
        """客服: 异常聚合 → 一键回复话术(轨迹摘要+方案)"""
        anomalies = await self.track.detect_anomalies(limit=100)
        if not anomalies:
            return []
        by_type: dict[str, list] = {}
        for a in anomalies:
            by_type.setdefault(a["type"], []).append(a)
        type_names = {"pickup_timeout": "揽收超时",
                      "stagnation": "运输停滞",
                      "deliver_failed": "派送失败",
                      "sign_timeout": "签收超时"}
        alerts = []
        for atype, group in sorted(by_type.items(),
                                   key=lambda x: -len(x[1]))[:3]:
            waybills = [a.get("waybillNo", "") for a in group[:5]]
            detail = group[0].get("detail", "")
            carrier = group[0].get("carrier", "")
            carrier_name = CARRIER_NAMES.get(carrier, carrier)
            alerts.append(await self._save_alert(
                "service", f"一键回复话术·{type_names.get(atype, atype)}",
                f"您好, 您的订单物流出现{type_names.get(atype, atype)}"
                f"({detail}); 承运方{carrier_name}正在处理, 我们将持续"
                "跟进并在有进展时第一时间同步您",
                {"type": "quick_reply", "waybills": waybills,
                 "count": len(group),
                 "suggest": "客服一键发送话术(人工确认后发送)",
                 "learningSignal": "解决时长/客诉升级率(回流观测)"}))
        return alerts

    async def _warehouse_alert(self) -> list[dict]:
        """仓储: 发货量预测 vs 仓容 → 爆仓预警+加急提货单"""
        orders = await self.fabric.repo.list_orders(limit=500)
        by_day: dict[str, int] = {}
        for o in orders:
            day = str(o.get("createdAt", "") or "")[:10]
            if day:
                by_day[day] = by_day.get(day, 0) + 1
        if not by_day:
            return []
        peak_day = max(by_day, key=by_day.get)
        peak = by_day[peak_day]
        ratio = _round2(peak / WAREHOUSE_DAILY_CAPACITY)
        if ratio < WAREHOUSE_WARN_RATIO:
            return []
        over_pct = _round2((ratio - 1) * 100) if ratio > 1 else 0
        body = (f"单日发货量 {peak} 单已达仓容 {WAREHOUSE_DAILY_CAPACITY}"
                f" 的 {ratio:.0%}" +
                (f"(超 {over_pct:.0f}%)" if over_pct > 0 else "")
                + ", 建议启用备用仓或分批发送")
        return [await self._save_alert(
            "warehouse", "爆仓预警(模板)", body,
            {"type": "urgent_pickup",
             "suggest": "生成《加急提货单》(人工确认后执行)",
             "doc": "加急提货单: 优先出库高时效订单, 分批发送非紧急单",
             "learningSignal": "仓库利用率/发货及时率(回流观测)"})]

    async def _operations_alert(self) -> list[dict]:
        """运营: 渠道均费超基准 → 《物流成本分析报告》"""
        performance = await self.fabric.carrier_performance()
        alerts = []
        for carrier, p in performance.items():
            if p["total"] == 0:
                continue
            avg_fee = p.get("avgFee", 0)
            if avg_fee > FEE_BASELINE * FEE_SURGE_RATIO:
                name = CARRIER_NAMES.get(carrier, carrier)
                over_pct = _round2((avg_fee / FEE_BASELINE - 1) * 100)
                alerts.append(await self._save_alert(
                    "operations", "物流成本分析报告(模板)",
                    f"{name} 近期均单运费 ¥{avg_fee:.2f}, 高于基准 "
                    f"¥{FEE_BASELINE:.2f} 达 {over_pct:.0f}%; 建议: 调整"
                    "包邮门槛 / 评估切换低成本渠道 / 与渠道议价",
                    {"type": "cost_report",
                     "doc": "物流成本分析报告(渠道费率明细见分析引擎)",
                     "suggest": "输出报告并评估渠道策略(人工确认)",
                     "learningSignal": "物流费用占比/活动ROI(回流观测)"}))
        return alerts

    async def _management_alert(self) -> list[dict]:
        """管理层: 破损率超标 → 《整改建议书》(主因渠道归因)"""
        claims = await self.store.list("claims")
        damage_claims = [c for c in claims
                         if c.get("claimType") == "damage"]
        orders = await self.fabric.repo.list_orders(limit=500)
        signed = sum(1 for o in orders if o.get("status") == "signed")
        if not signed:
            return []
        damage_rate = _round2(len(damage_claims) / signed)
        if damage_rate <= DAMAGE_RATE_LINE:
            return []
        # 主因渠道归因(按破损理赔渠道计数 top1)
        by_carrier: dict[str, int] = {}
        for c in damage_claims:
            carrier = str(c.get("carrier", "") or "")
            if carrier:
                by_carrier[carrier] = by_carrier.get(carrier, 0) + 1
        top_carrier = max(by_carrier, key=by_carrier.get) \
            if by_carrier else ""
        top_name = CARRIER_NAMES.get(top_carrier, top_carrier or "未知")
        over = _round2((damage_rate - DAMAGE_RATE_LINE) * 100)
        return [await self._save_alert(
            "management", "整改建议书(模板)",
            f"本月破损率 {damage_rate:.1%} 超标(阈值 {DAMAGE_RATE_LINE:.1%},"
            f"超 {over:.1f}pct), 主因渠道: {top_name}(包装不规范/野蛮"
            "分拣风险); 建议: 渠道约谈+缓冲包装升级+破损险覆盖复核",
            {"type": "rectification",
             "doc": "整改建议书(渠道归因+措施+复盘节点)",
             "suggest": "推送管理层并启动整改(人工确认)",
             "learningSignal": "决策采纳率/损失挽回金额(回流观测)"})]

    async def scan(self) -> dict:
        """全角色触发面扫描(幂等: 每次扫描生成新一批草稿)"""
        alerts = []
        alerts += await self._consumer_alerts()
        alerts += await self._service_alerts()
        alerts += await self._warehouse_alert()
        alerts += await self._operations_alert()
        alerts += await self._management_alert()
        by_role = {}
        for a in alerts:
            by_role[a["role"]] = by_role.get(a["role"], 0) + 1
        return {
            "generated": len(alerts),
            "byRole": by_role,
            "alerts": alerts,
            "note": "提醒为草稿+建议模式; 动作须人工确认(永不自动执行)",
            "scannedAt": _now_iso(),
        }

    async def alerts(self, role: str = None,
                     status: str = None) -> list[dict]:
        if role and role not in ALERT_ROLES:
            raise ValueError(f"角色无效({role}: {'/'.join(ALERT_ROLES)})")
        rows = await self.store.list("alerts")
        if role:
            rows = [r for r in rows if r.get("role") == role]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return sorted(rows, key=lambda r: r.get("raisedAt", ""),
                      reverse=True)

    async def ack(self, alert_id: int,
                  disposition: str = "acked") -> dict:
        """提醒确认闭环(acked 确认 / dismissed 驳回=负样本留痕)"""
        record = await self.store.get("alerts", alert_id)
        if record is None:
            raise KeyError(f"提醒不存在(alertId={alert_id})")
        if disposition not in ("acked", "dismissed"):
            raise ValueError("处置无效(acked/dismissed)")
        record["status"] = disposition
        record["acknowledgedAt"] = _now_iso()
        if disposition == "dismissed":
            record["negativeSample"] = (
                "驳回记负样本: 该提醒判定不适用, 回流观测面供阈值"
                "与模板校准(永不自动调整)")
        await self.store.save("alerts", alert_id, record)
        return record
