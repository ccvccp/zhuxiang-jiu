"""智酿运通 P5 订单-物流深度绑定(zw_binding_service)

创新规划 §三 P5: 从"发货"到"全链数字孪生"
    - 生产-物流联动: 日粒度发货量预测(0.6 近7日 + 0.4 近14日, 对齐
      P3 加权移动平均范式) → 未来 3 天分渠道运力舱位预约建议书
      (安全系数 1.2, 按历史渠道占比分摊, 人工确认后预约)
    - 三码合一溯源绑定: 运单号+酒水批次码+防伪码绑定; 消费者扫码
      验真(公开端点) → "从窖池到餐桌"全链档案(脱敏输出);
      IoT 温控/震动未接入时诚实降级为轨迹时间线(不伪造数据)
    - 逆向物流智能绑定: 商品状态(未开封/已开封/破损)确定性匹配最优
      退回路径(返仓入库/转赠品鉴/就地销毁)+《退货处置协议》

铁律: 预约/处置全为建议书(人工确认执行); 验真端点脱敏; 确定性规则。
"""

import logging
from datetime import datetime, UTC

from services.zw_fabric_service import _ZwStore, _round2, _now_iso

logger = logging.getLogger(__name__)

# 容量预约参数(确定性)
CAPACITY_DAYS = 3            # 预约窗口(天)
SAFETY_FACTOR = 1.2         # 运力安全系数(宁多勿少)
RECENT_WEIGHT = 0.6          # 近 7 日权重(对齐 P3 范式)
FULL_WEIGHT = 0.4            # 近 14 日权重

# 逆向物流路径匹配表(商品状态 → 退回路径, 确定性)
REVERSE_PATHS = {
    "unopened": {
        "path": "return_warehouse", "pathName": "返仓入库",
        "condition": "未开封(密封完好)",
        "protocol": ("商品密封完好, 逆向物流原路返回成都总仓, 质检"
                     "合格后重新入库(批次码保留, 转临期专区或正价"
                     "二次销售)"),
        "costNote": "承担单程退回运费(到付)",
    },
    "opened": {
        "path": "gift_transfer", "pathName": "转赠品鉴",
        "condition": "已开封(不影响食品安全)",
        "protocol": ("已开封酒水不再二次销售, 转为品鉴装/员工福利/"
                     "门店品鉴会物料, 财务按损耗科目入账"),
        "costNote": "平台承担损耗(计入品鉴营销预算)",
    },
    "damaged": {
        "path": "local_destroy", "pathName": "就地销毁",
        "condition": "破损(渗漏/碎裂)",
        "condition2": "液态破损品回运风险高(污染/安全)",
        "protocol": ("破损品不回运, 消费者拍照+视频留证(须含运单号"
                     "与批次码同框), 平台核实后补发或退款, 商品就地"
                     "销毁并出具《销毁确认回执》"),
        "costNote": "平台承担全额损失(走破损险/渠道索赔)",
    },
}


def _mask_name(name: str) -> str:
    """姓名脱敏(保留姓)"""
    name = str(name or "")
    return name[0] + "**" if len(name) >= 2 else (name or "**")


def _mask_phone(phone: str) -> str:
    """电话脱敏(保留前3后4)"""
    phone = str(phone or "")
    if len(phone) >= 7:
        return f"{phone[:3]}****{phone[-4:]}"
    return "****" if phone else ""


class ZwBindingService:
    """P5: 生产-物流联动 + 三码合一 + 逆向物流"""

    def __init__(self, store: _ZwStore = None):
        self.store = store or _ZwStore()
        from repositories.logistics_repository import LogisticsRepository
        self.repo = LogisticsRepository()

    # ============================================================
    # 生产-物流联动: 日粒度预测 → 运力舱位预约建议书
    # ============================================================

    async def capacity_plan(self) -> dict:
        """未来 3 天分渠道运力舱位预约建议书(人工确认后预约)

        预测口径(日粒度, 对齐 P3 加权移动平均范式):
            日均预测 = 近7日均值×0.6 + 近14日均值×0.4
            (订单 createdAt 日期分布, 确定性)
        分摊口径: 按近 14 日渠道订单占比分摊; 预约量 = 预测×安全系数1.2。
        """
        orders = await self.repo.list_orders(limit=500)
        today = datetime.now(UTC).date()
        by_day: dict[str, int] = {}
        by_carrier: dict[str, int] = {}
        for o in orders:
            day = str(o.get("createdAt", "") or "")[:10]
            if day:
                by_day[day] = by_day.get(day, 0) + 1
            carrier = str(o.get("carrier", "") or "")
            if carrier:
                by_carrier[carrier] = by_carrier.get(carrier, 0) + 1

        def _avg(days: int) -> float:
            """近 N 日日均(缺日照常计入分母, 诚实口径)"""
            if not by_day:
                return 0.0
            from datetime import timedelta
            total = 0
            for i in range(days):
                key = str(today - timedelta(days=i + 1))
                total += by_day.get(key, 0)
            return total / days

        recent_avg = _avg(7)
        full_avg = _avg(14)
        daily_forecast = _round2(RECENT_WEIGHT * recent_avg
                                + FULL_WEIGHT * full_avg)

        total_carrier = sum(by_carrier.values()) or 1
        allocations = []
        for carrier, count in sorted(by_carrier.items(),
                                     key=lambda x: -x[1]):
            share = _round2(count / total_carrier)
            daily_alloc = _round2(daily_forecast * share)
            allocations.append({
                "carrier": carrier,
                "historyShare": share,
                "dailyForecast": daily_alloc,
                "suggestBooking": max(1, round(daily_alloc
                                                 * SAFETY_FACTOR)),
            })

        plan = {
            "planId": await self.store.next_id("capacity_plans"),
            "windowDays": CAPACITY_DAYS,
            "dailyForecastOrders": daily_forecast,
            "recentAvg7d": _round2(recent_avg),
            "fullAvg14d": _round2(full_avg),
            "formula": (f"日均 = 近7日均值×{RECENT_WEIGHT} + "
                        f"近14日均值×{FULL_WEIGHT}; 预约 = 日均分摊×"
                        f"{SAFETY_FACTOR}(安全系数)"),
            "allocations": allocations,
            "disposition": "《运力舱位预约建议书》: 人工确认后向渠道预约, "
                           "永不自动执行",
            "generatedAt": _now_iso(),
        }
        await self.store.save("capacity_plans",
                              plan["planId"], plan)
        return plan

    async def capacity_plans(self, limit: int = 20) -> list[dict]:
        rows = await self.store.list("capacity_plans")
        return sorted(rows, key=lambda r: r.get("generatedAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 三码合一溯源绑定 + 消费者扫码验真(公开, 脱敏)
    # ============================================================

    async def tri_code_bind(self, waybill_no: str, order_id: str,
                            batch_code: str, anti_fake_code: str) -> dict:
        """三码合一绑定(运单号+批次码+防伪码)"""
        for label, v in (("运单号", waybill_no), ("订单号", order_id),
                         ("批次码", batch_code), ("防伪码", anti_fake_code)):
            if not v or len(str(v)) > 60:
                raise ValueError(f"{label}无效(1-60字符)")
        order = None
        for o in await self.repo.list_orders(limit=500):
            if o.get("waybillNo") == waybill_no \
                    or o.get("orderId") == order_id:
                order = o
                break
        if order is None:
            raise KeyError(f"运单/订单不存在(waybill={waybill_no})")

        bindings = await self.store.list("tri_codes")
        for b in bindings:
            if b.get("antiFakeCode") == anti_fake_code:
                raise ValueError("防伪码已绑定(不可重复)")

        record = {
            "bindingId": await self.store.next_id("tri_codes"),
            "waybillNo": waybill_no, "orderId": order_id,
            "batchCode": batch_code, "antiFakeCode": anti_fake_code,
            "productId": order.get("productId", ""),
            "boundAt": _now_iso(),
        }
        await self.store.save("tri_codes", record["bindingId"], record)
        return record

    async def tri_codes(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("tri_codes")
        return sorted(rows, key=lambda r: r.get("boundAt", ""),
                      reverse=True)[:limit]

    async def verify_by_code(self, code: str) -> dict:
        """消费者扫码验真(公开端点, 任一码 → 全链档案, 脱敏输出)

        IoT 温控/震动记录未接入 → 诚实降级为轨迹时间线(不伪造数据)。
        """
        if not code or len(code) > 60:
            raise ValueError("验真码无效")
        binding = None
        for b in await self.store.list("tri_codes"):
            if code in (b.get("waybillNo"), b.get("batchCode"),
                        b.get("antiFakeCode"), b.get("orderId")):
                binding = b
                break
        if binding is None:
            raise KeyError("验真失败: 码未绑定(谨防假冒, 可联系客服核验)")

        order = None
        for o in await self.repo.list_orders(limit=500):
            if o.get("waybillNo") == binding["waybillNo"]:
                order = o
                break
        tracks = await self.repo.list_tracks(binding["waybillNo"],
                                             limit=20) if order else []
        track_lines = [f"{t.get('trackTime', '')} {t.get('detail', '')}"
                       for t in tracks]

        return {
            "verified": True,
            "product": {
                "productId": binding.get("productId", ""),
                "batchCode": binding["batchCode"],
                "antiFakeCode": binding["antiFakeCode"],
            },
            "logistics": ({
                "waybillNo": order.get("waybillNo", ""),
                "carrierName": order.get("carrierName", ""),
                "statusName": order.get("statusName", ""),
                "receiverMasked": _mask_name(
                    order.get("receiverName", "")),
                "receiverPhoneMasked": _mask_phone(
                    order.get("receiverPhone", "")),
            } if order else None),
            "journey": {
                "iotNote": "IoT 温控/震动记录未接入, 降级为轨迹时间线"
                           "(诚实降级, 不伪造数据)",
                "trackTimeline": track_lines,
            },
            "note": "三码合一验真: 从窖池到餐桌全链档案(脱敏输出)",
            "verifiedAt": _now_iso(),
        }

    # ============================================================
    # 逆向物流智能绑定(商品状态 → 最优退回路径 + 处置协议)
    # ============================================================

    async def reverse_bind(self, order_id: str, condition: str,
                           reason: str = "") -> dict:
        """退货路径匹配 + 《退货处置协议》(人工确认执行)"""
        if condition not in REVERSE_PATHS:
            raise ValueError(f"商品状态无效({condition}: "
                             "unopened/opened/damaged)")
        path = REVERSE_PATHS[condition]
        order = None
        for o in await self.repo.list_orders(limit=500):
            if o.get("orderId") == order_id:
                order = o
                break
        if order is None:
            raise KeyError(f"订单不存在(orderId={order_id})")

        record = {
            "reverseId": await self.store.next_id("reverse_plans"),
            "orderId": order_id,
            "waybillNo": order.get("waybillNo", ""),
            "condition": condition,
            "conditionName": path["condition"],
            "reason": reason or "",
            "matchedPath": path["path"],
            "matchedPathName": path["pathName"],
            "protocol": {
                "title": "退货处置协议",
                "body": path["protocol"],
                "costNote": path["costNote"],
            },
            "disposition": "处置协议为建议书: 人工确认后执行, 永不自动",
            "decidedAt": _now_iso(),
        }
        await self.store.save("reverse_plans",
                              record["reverseId"], record)
        return record

    async def reverses(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("reverse_plans")
        return sorted(rows, key=lambda r: r.get("decidedAt", ""),
                      reverse=True)[:limit]
