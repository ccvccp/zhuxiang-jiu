"""物流接口管理业务: 物流下单 + 轨迹追踪 + 月结对账

并发安全(遵循项目约定 lock:{key} 格式):
    - 物流下单: logistics:order:{orderId} 锁(防同订单重复下单)
    - 状态流转: logistics:order:{waybillNo} 锁(状态机保护)
    - 月结对账: logistics:settle:lock:{period}:{carrier} 锁(防并发对账)

状态机(对齐设计文档):
    物流订单: pending → booked → picked → transporting → delivering → signed
                                                   ↘ failed(可重投) → returned(终态)
    结算单:    pending → reconciling → confirmed → paid
                              ↘ diff → investigating → resolved → confirmed

异常约定(遵循项目约定):
    - KeyError(message)  → 路由层映射为 404
    - ValueError(message) → 路由层映射为 409

注: 跨模块联动(订单状态变更/钱包/财务)由路由层或事件回调处理, 本服务保持单一职责
"""

import logging
from typing import Optional

from core.helpers import ts
from core.locks import get_lock
from repositories.logistics_repository import (
    LogisticsRepository,
    # 物流订单状态
    ORDER_STATUS_PENDING, ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED,
    ORDER_STATUS_TRANSPORTING, ORDER_STATUS_DELIVERING, ORDER_STATUS_SIGNED,
    ORDER_STATUS_FAILED, ORDER_STATUS_RETURNED,
    ORDER_STATUS_FLOW, ORDER_STATUS_NAMES,
    # 结算状态
    SETTLE_STATUS_PENDING, SETTLE_STATUS_RECONCILING, SETTLE_STATUS_CONFIRMED,
    SETTLE_STATUS_PAID, SETTLE_STATUS_DIFF, SETTLE_STATUS_INVESTIGATING,
    SETTLE_STATUS_RESOLVED, SETTLE_STATUS_FLOW, SETTLE_PENDING_STATUSES,
    SETTLE_STATUS_NAMES,
    # 物流商
    CARRIER_SF, CARRIER_JD, CARRIER_LLL, CARRIER_DB, CARRIER_YT,
    CARRIER_NAMES,
)

logger = logging.getLogger(__name__)


# ============================================================
# 业务常量
# ============================================================

# 支持的物流商
SUPPORTED_CARRIERS = {CARRIER_SF, CARRIER_JD, CARRIER_LLL, CARRIER_DB, CARRIER_YT}

# 支持的订单类型
SUPPORTED_ORDER_TYPES = {"retail", "groupbuy", "return"}

# 支持的结算模式
SUPPORTED_SETTLE_MODES = {"monthly", "cash", "prepaid"}

# 支持的签收方式
SUPPORTED_SIGN_TYPES = {"self", "agent", "station"}

# 保价费率(0.5%)
INSURED_FEE_RATE = 0.005

# 包装费(元/件, 默认 2 元)
PACKAGE_FEE_PER_PIECE = 2.0

# 顺丰基础运费表(简化版, 实际由物流商 API 返回)
# key: (重量档位, 服务类型), value: 基础运费
SF_BASE_FEE_TABLE = {
    ("standard", 1.0): 18.0,    # 1kg 内标准件
    ("standard", 3.0): 22.0,    # 3kg 内
    ("standard", 5.0): 28.0,    # 5kg 内
    ("standard", 10.0): 38.0,   # 10kg 内
    ("standard", 20.0): 58.0,   # 20kg 内
    ("express", 1.0): 25.0,     # 顺丰特快
    ("express", 3.0): 30.0,
    ("express", 5.0): 38.0,
    ("express", 10.0): 50.0,
    ("express", 20.0): 75.0,
}

# 货拉拉同城运费(简化版)
LLL_BASE_FEE_TABLE = {
    1.0: 35.0,
    3.0: 55.0,
    5.0: 75.0,
    10.0: 120.0,
}

# 月结对账差异类型
DIFF_TYPE_AMOUNT_MISMATCH = "amount_mismatch"  # 金额不符
DIFF_TYPE_ORDER_MISSING = "order_missing"      # 单据缺失(平台有, 物流商无)
DIFF_TYPE_EXTRA_ORDER = "extra_order"          # 多余单据(物流商有, 平台无)

DIFF_TYPE_NAMES = {
    DIFF_TYPE_AMOUNT_MISMATCH: "金额不符",
    DIFF_TYPE_ORDER_MISSING: "单据缺失",
    DIFF_TYPE_EXTRA_ORDER: "多余单据",
}

# 差异处理建议
HANDLE_SUGGEST_SUPPLEMENT = "supplement"  # 补单
HANDLE_SUGGEST_REFUND = "refund"          # 退款
HANDLE_SUGGEST_IGNORE = "ignore"          # 忽略


def _calc_insured_fee(insured_value: float) -> float:
    """计算保价费(0.5%)"""
    return round(insured_value * INSURED_FEE_RATE, 2)


def _calc_package_fee(piece_count: int) -> float:
    """计算包装费(2 元/件)"""
    return round(PACKAGE_FEE_PER_PIECE * max(piece_count, 1), 2)


def _calc_sf_base_fee(service_type: str, weight: float) -> float:
    """计算顺丰基础运费(按重量档位查表)

    Args:
        service_type: standard/express
        weight: 重量 kg

    Returns:
        基础运费
    """
    # 按重量档位查找(从低到高)
    for (svc, max_weight), fee in sorted(
        [(k, v) for k, v in SF_BASE_FEE_TABLE.items() if k[0] == service_type],
        key=lambda x: x[0][1]
    ):
        if weight <= max_weight:
            return fee
    # 超过最高档位, 按最高档位 + 续重计算
    max_tier = max(v for k, v in SF_BASE_FEE_TABLE.items() if k[0] == service_type)
    max_weight_tier = max(k[1] for k in SF_BASE_FEE_TABLE if k[0] == service_type)
    extra_weight = weight - max_weight_tier
    return round(max_tier + extra_weight * 2.0, 2)  # 续重 2 元/kg


def _calc_lll_base_fee(weight: float) -> float:
    """计算货拉拉同城运费"""
    for max_weight, fee in sorted(LLL_BASE_FEE_TABLE.items()):
        if weight <= max_weight:
            return fee
    # 超重按最高档位 + 续重 5 元/kg
    max_fee = max(LLL_BASE_FEE_TABLE.values())
    max_weight = max(LLL_BASE_FEE_TABLE.keys())
    extra = weight - max_weight
    return round(max_fee + extra * 5.0, 2)


def _calc_total_fee(base_fee: float, insured_fee: float, package_fee: float,
                    extra_fee: float = 0.0, discount: float = 1.0) -> float:
    """计算总运费

    Args:
        base_fee: 基础运费
        insured_fee: 保价费
        package_fee: 包装费
        extra_fee: 附加费
        discount: 折扣率(1.0 = 无折扣)

    Returns:
        总运费
    """
    subtotal = base_fee + insured_fee + package_fee + extra_fee
    return round(subtotal * discount, 2)


def _gen_settle_no(period: str, carrier: str) -> str:
    """生成结算单号 SETTLE{YYYYMM}{carrier}"""
    return f"SETTLE{period.replace('-', '')}{carrier}"


def _mask_phone(phone: str) -> str:
    """手机号脱敏(保留前 3 后 4)"""
    if not phone or len(phone) <= 7:
        return "****"
    return phone[:3] + "****" + phone[-4:]


class LogisticsService:
    """物流接口管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, logistics_repo: LogisticsRepository = None):
        self.repo = logistics_repo if logistics_repo is not None else LogisticsRepository()

    # ============================================================
    # P0: 物流订单(下单 + 状态流转)
    # ============================================================

    async def create_order(self, order_id: str, order_type: str,
                            carrier: str, service_type: str,
                            sender: dict, receiver: dict,
                            weight: float, piece_count: int = 1,
                            volume: float = 0.0,
                            insured_value: float = 0.0,
                            settle_mode: str = "monthly",
                            extra_fee: float = 0.0,
                            discount: float = 1.0) -> dict:
        """物流下单(幂等: 同一 orderId 只能有一个未关闭运单)

        Args:
            order_id: 关联竹香酒订单号
            order_type: 订单类型 retail/groupbuy/return
            carrier: 物流商编码 SF/JD/LLL/DB/YT
            service_type: 服务类型 standard/express
            sender: 寄件人 {name, phone, address}
            receiver: 收件人 {name, phone, address, province, city}
            weight: 重量 kg
            piece_count: 件数
            volume: 体积 m³
            insured_value: 保价金额
            settle_mode: 结算模式 monthly/cash/prepaid
            extra_fee: 附加费
            discount: 折扣率(0.6-1.0)

        Raises:
            ValueError: 参数非法 / 已有未关闭运单
        """
        # 参数校验
        if order_type not in SUPPORTED_ORDER_TYPES:
            raise ValueError(f"订单类型非法: {order_type}")
        if carrier not in SUPPORTED_CARRIERS:
            raise ValueError(f"物流商非法: {carrier}")
        if settle_mode not in SUPPORTED_SETTLE_MODES:
            raise ValueError(f"结算模式非法: {settle_mode}")
        if weight <= 0:
            raise ValueError("重量须 > 0")
        if piece_count <= 0:
            raise ValueError("件数须 > 0")
        if not (0 < discount <= 1.0):
            raise ValueError("折扣率须在 (0, 1.0] 区间")
        if not sender.get("name") or not sender.get("phone") or not sender.get("address"):
            raise ValueError("寄件人信息不完整")
        if not receiver.get("name") or not receiver.get("phone") or not receiver.get("address"):
            raise ValueError("收件人信息不完整")

        # 加锁防止同订单并发下单
        async with get_lock(f"logistics:order:{order_id}"):
            # 幂等校验: 同订单已有未关闭运单 → 拒绝
            existing = await self.repo.find_by_order(order_id)
            if existing and existing.get("status") not in (
                ORDER_STATUS_SIGNED, ORDER_STATUS_RETURNED
            ):
                raise ValueError(
                    f"订单 {order_id} 已有未关闭运单 {existing['waybillNo']}"
                )

            # 计算运费
            if carrier == CARRIER_SF:
                base_fee = _calc_sf_base_fee(service_type, weight)
            elif carrier == CARRIER_LLL:
                base_fee = _calc_lll_base_fee(weight)
            else:
                # 其他物流商暂用顺丰标准费率
                base_fee = _calc_sf_base_fee("standard", weight)

            insured_fee = _calc_insured_fee(insured_value)
            package_fee = _calc_package_fee(piece_count)
            total_fee = _calc_total_fee(
                base_fee, insured_fee, package_fee, extra_fee, discount
            )

            # 生成运单号(实际由物流商返回, 此处模拟)
            # 加入随机后缀确保唯一性(避免同毫秒同订单生成相同运单号)
            import secrets
            waybill_no = f"{carrier}{ts().replace('-', '').replace(':', '').replace(' ', '')}{order_id[-4:]}{secrets.token_hex(3)}"

            order_data = {
                "waybillNo": waybill_no,
                "orderId": order_id,
                "orderType": order_type,
                "carrier": carrier,
                "carrierName": CARRIER_NAMES.get(carrier, carrier),
                "serviceType": service_type,
                "senderName": sender["name"],
                "senderPhone": _mask_phone(sender["phone"]),
                "senderAddress": sender["address"],
                "receiverName": receiver["name"],
                "receiverPhone": _mask_phone(receiver["phone"]),
                "receiverAddress": receiver["address"],
                "province": receiver.get("province", ""),
                "city": receiver.get("city", ""),
                "weight": weight,
                "volume": volume,
                "pieceCount": piece_count,
                "insuredValue": insured_value,
                "baseFee": base_fee,
                "insuredFee": insured_fee,
                "packageFee": package_fee,
                "extraFee": extra_fee,
                "discount": discount,
                "totalFee": total_fee,
                "settleMode": settle_mode,
                "status": ORDER_STATUS_PENDING,
            }

            saved = await self.repo.save_order(order_data)
            logger.info(f"物流下单成功 waybillNo={waybill_no} orderId={order_id} totalFee={total_fee}")
            return saved

    async def get_order(self, waybill_no: str) -> dict:
        """查询物流订单详情

        Raises:
            KeyError: 运单不存在
        """
        order = await self.repo.get_order(waybill_no)
        if not order:
            raise KeyError(f"运单 {waybill_no} 不存在")
        return order

    async def get_order_by_order_id(self, order_id: str) -> Optional[dict]:
        """按订单号查询物流单(可能不存在)"""
        return await self.repo.find_by_order(order_id)

    async def list_orders(self, carrier: str = None, status: str = None,
                          order_type: str = None, limit: int = 50) -> list:
        """物流订单列表"""
        return await self.repo.list_orders(carrier, status, order_type, limit)

    async def update_status(self, waybill_no: str, new_status: str,
                             operator: str = "system",
                             track_desc: str = None,
                             track_location: str = None,
                             sign_info: dict = None) -> dict:
        """更新物流订单状态(状态机校验 + 自动添加轨迹)

        Args:
            waybill_no: 运单号
            new_status: 新状态
            operator: 操作人
            track_desc: 轨迹描述(为 None 时使用状态中文名)
            track_location: 轨迹所在城市
            sign_info: 签收信息 {signerName, signType, signPhoto, signLocation, signedTime}
                       仅 signed 状态需要

        Raises:
            KeyError: 运单不存在
            ValueError: 状态机非法流转
        """
        async with get_lock(f"logistics:order:{waybill_no}"):
            order = await self.repo.get_order(waybill_no)
            if not order:
                raise KeyError(f"运单 {waybill_no} 不存在")

            old_status = order.get("status")
            # 状态机校验
            allowed = ORDER_STATUS_FLOW.get(old_status, set())
            if new_status not in allowed:
                raise ValueError(
                    f"状态机非法: {old_status}({ORDER_STATUS_NAMES.get(old_status, old_status)})"
                    f" → {new_status}({ORDER_STATUS_NAMES.get(new_status, new_status)})"
                )

            # 准备更新字段
            fields = {"status": new_status}

            # 签收状态补充签收信息
            if new_status == ORDER_STATUS_SIGNED:
                if sign_info:
                    fields["signerName"] = sign_info.get("signerName", "")
                    fields["signType"] = sign_info.get("signType", "self")
                    fields["signPhoto"] = sign_info.get("signPhoto", "")
                    fields["signLocation"] = sign_info.get("signLocation", "")
                fields["signedTime"] = ts()

            # 揽收状态补充揽收时间
            if new_status == ORDER_STATUS_PICKED:
                fields["pickupTime"] = ts()

            updated = await self.repo.update_order_fields(waybill_no, fields)

            # 自动添加轨迹
            track_desc = track_desc or ORDER_STATUS_NAMES.get(new_status, new_status)
            await self.repo.add_track({
                "waybillNo": waybill_no,
                "carrier": order.get("carrier", ""),
                "trackStatus": new_status.upper(),
                "unifiedStatus": new_status,
                "description": track_desc,
                "location": track_location or order.get("city", ""),
                "operator": operator,
                "trackTime": ts(),
            })

            logger.info(f"物流状态流转 {waybill_no}: {old_status} → {new_status}")
            return updated

    async def close_failed_order(self, waybill_no: str, reason: str = "") -> dict:
        """关闭失败的运单(允许从 failed 退回 pending 重新下单)

        Raises:
            KeyError: 运单不存在
            ValueError: 状态非 failed
        """
        async with get_lock(f"logistics:order:{waybill_no}"):
            order = await self.repo.get_order(waybill_no)
            if not order:
                raise KeyError(f"运单 {waybill_no} 不存在")
            if order.get("status") != ORDER_STATUS_FAILED:
                raise ValueError("仅 failed 状态可关闭重试")

            updated = await self.repo.update_order_fields(waybill_no, {
                "status": ORDER_STATUS_RETURNED,
                "labelUrl": "",  # 清空面单
            })
            logger.info(f"关闭失败运单 {waybill_no} 原因: {reason}")
            return updated

    # ============================================================
    # P0: 物流轨迹
    # ============================================================

    async def list_tracks(self, waybill_no: str, limit: int = 50) -> list:
        """查询运单轨迹列表

        Raises:
            KeyError: 运单不存在
        """
        order = await self.repo.get_order(waybill_no)
        if not order:
            raise KeyError(f"运单 {waybill_no} 不存在")
        return await self.repo.list_tracks(waybill_no, limit)

    async def add_track_callback(self, waybill_no: str, track_status: str,
                                  unified_status: str, description: str,
                                  location: str = "", operator: str = "carrier",
                                  track_time: str = None) -> dict:
        """物流商轨迹回调(自动更新订单状态, 由 update_status 添加轨迹)

        Args:
            waybill_no: 运单号
            track_status: 物流商原始状态
            unified_status: 统一状态(映射到状态机)
            description: 轨迹描述
            location: 所在城市
            operator: 操作人(默认 carrier)
            track_time: 轨迹时间(为 None 时用当前时间)

        Raises:
            KeyError: 运单不存在

        注: 轨迹由 update_status 自动添加, 避免重复记录
        """
        if not track_time:
            track_time = ts()

        # 自动更新订单状态(由 update_status 添加轨迹)
        valid_statuses = {
            ORDER_STATUS_BOOKED, ORDER_STATUS_PICKED, ORDER_STATUS_TRANSPORTING,
            ORDER_STATUS_DELIVERING, ORDER_STATUS_SIGNED,
            ORDER_STATUS_FAILED, ORDER_STATUS_RETURNED,
        }
        if unified_status in valid_statuses:
            try:
                await self.update_status(
                    waybill_no, unified_status, operator,
                    description, location
                )
                # 查询最新添加的轨迹返回
                tracks = await self.repo.list_tracks(waybill_no, 1)
                return tracks[0] if tracks else {
                    "waybillNo": waybill_no,
                    "trackStatus": track_status,
                    "unifiedStatus": unified_status,
                    "description": description,
                    "location": location,
                    "operator": operator,
                    "trackTime": track_time,
                }
            except ValueError as e:
                # 状态机非法流转(如重复签收), 仍需记录轨迹
                logger.warning(f"轨迹回调状态流转失败(仅记录轨迹): {waybill_no} {e}")
        # 状态非法或流转失败, 仅添加轨迹
        track = await self.repo.add_track({
            "waybillNo": waybill_no,
            "carrier": "",
            "trackStatus": track_status,
            "unifiedStatus": unified_status,
            "description": description,
            "location": location,
            "operator": operator,
            "trackTime": track_time,
        })
        return track

    # ============================================================
    # P0: 月结对账
    # ============================================================

    async def start_settlement(self, period: str, carrier: str,
                                channel_orders: list = None) -> dict:
        """启动月结对账(按账期 + 物流商维度)

        Args:
            period: 账期 YYYY-MM
            carrier: 物流商编码
            channel_orders: 物流商返回的对账明细列表 [{waybillNo, totalFee, ...}]
                            为 None 时仅按平台数据生成结算单

        Raises:
            ValueError: 对账锁已被占用 / 结算单已存在
        """
        if carrier not in SUPPORTED_CARRIERS:
            raise ValueError(f"物流商非法: {carrier}")

        # 获取对账锁
        locked = await self.repo.acquire_settle_lock(period, carrier)
        if not locked:
            raise ValueError(f"账期 {period} 物流商 {carrier} 已在对账中")

        try:
            settle_no = _gen_settle_no(period, carrier)

            # 检查是否已存在
            existing = await self.repo.get_settlement(settle_no)
            if existing:
                raise ValueError(f"结算单 {settle_no} 已存在")

            # 查询平台该账期该物流商的所有订单(简化: 按 carrier 筛选)
            platform_orders = await self.repo.list_orders(carrier=carrier, limit=1000)

            # 统计平台数据
            total_orders = len(platform_orders)
            total_weight = sum(float(o.get("weight", 0)) for o in platform_orders)
            base_fee_total = sum(float(o.get("baseFee", 0)) for o in platform_orders)
            insured_total = sum(float(o.get("insuredFee", 0)) for o in platform_orders)
            package_total = sum(float(o.get("packageFee", 0)) for o in platform_orders)
            extra_total = sum(float(o.get("extraFee", 0)) for o in platform_orders)
            subtotal = base_fee_total + insured_total + package_total + extra_total
            discount_amount = subtotal - sum(
                float(o.get("totalFee", 0)) for o in platform_orders
            )
            payable_amount = subtotal - discount_amount

            # 与物流商对账(若提供 channel_orders)
            diff_details = []
            diff_count = 0
            if channel_orders:
                channel_map = {c.get("waybillNo"): c for c in channel_orders}
                platform_map = {o.get("waybillNo"): o for o in platform_orders}

                # 平台有, 物流商无 → 单据缺失
                for wn, po in platform_map.items():
                    if wn not in channel_map:
                        diff_count += 1
                        diff_details.append({
                            "type": DIFF_TYPE_ORDER_MISSING,
                            "waybillNo": wn,
                            "platformFee": po.get("totalFee", 0),
                            "channelFee": 0,
                            "suggestion": HANDLE_SUGGEST_SUPPLEMENT,
                        })

                # 物流商有, 平台无 → 多余单据
                for wn, co in channel_map.items():
                    if wn not in platform_map:
                        diff_count += 1
                        diff_details.append({
                            "type": DIFF_TYPE_EXTRA_ORDER,
                            "waybillNo": wn,
                            "platformFee": 0,
                            "channelFee": co.get("totalFee", 0),
                            "suggestion": HANDLE_SUGGEST_IGNORE,
                        })

                # 双方都有但金额不符
                for wn, po in platform_map.items():
                    if wn in channel_map:
                        pf = float(po.get("totalFee", 0))
                        cf = float(channel_map[wn].get("totalFee", 0))
                        if abs(pf - cf) > 0.01:
                            diff_count += 1
                            diff_details.append({
                                "type": DIFF_TYPE_AMOUNT_MISMATCH,
                                "waybillNo": wn,
                                "platformFee": pf,
                                "channelFee": cf,
                                "suggestion": HANDLE_SUGGEST_REFUND if cf < pf else HANDLE_SUGGEST_SUPPLEMENT,
                            })

            # 决定状态: 有差异 → diff, 无差异 → reconciling(待确认)
            status = SETTLE_STATUS_DIFF if diff_count > 0 else SETTLE_STATUS_RECONCILING

            settle_data = {
                "settleNo": settle_no,
                "carrier": carrier,
                "period": period,
                "totalOrders": total_orders,
                "totalWeight": round(total_weight, 2),
                "baseFeeTotal": round(base_fee_total, 2),
                "insuredTotal": round(insured_total, 2),
                "packageTotal": round(package_total, 2),
                "extraTotal": round(extra_total, 2),
                "subtotal": round(subtotal, 2),
                "discountAmount": round(discount_amount, 2),
                "payableAmount": round(payable_amount, 2),
                "status": status,
                "diffDetails": diff_details,
                "diffCount": diff_count,
            }

            saved = await self.repo.create_settlement(settle_data)
            logger.info(f"月结对账启动 settleNo={settle_no} diffCount={diff_count}")
            return saved

        finally:
            # 无论成功失败都释放锁
            await self.repo.release_settle_lock(period, carrier)

    async def get_settlement(self, settle_no: str) -> dict:
        """查询结算单详情

        Raises:
            KeyError: 结算单不存在
        """
        settle = await self.repo.get_settlement(settle_no)
        if not settle:
            raise KeyError(f"结算单 {settle_no} 不存在")
        return settle

    async def list_settlements(self, carrier: str = None, period: str = None,
                                status: str = None, limit: int = 50) -> list:
        """结算单列表"""
        return await self.repo.list_settlements(carrier, period, status, limit)

    async def list_pending_settlements(self, limit: int = 50) -> list:
        """待处理结算单列表(pending/diff/investigating)"""
        return await self.repo.list_pending_settlements(limit)

    async def investigate_diff(self, settle_no: str, auditor: str = "admin") -> dict:
        """介入调查差异(diff → investigating)

        Raises:
            KeyError: 结算单不存在
            ValueError: 状态非 diff
        """
        async with get_lock(f"logistics:settle:{settle_no}"):
            settle = await self.repo.get_settlement(settle_no)
            if not settle:
                raise KeyError(f"结算单 {settle_no} 不存在")
            if settle.get("status") != SETTLE_STATUS_DIFF:
                raise ValueError("仅 diff 状态可介入调查")

            updated = await self.repo.update_settlement_fields(settle_no, {
                "status": SETTLE_STATUS_INVESTIGATING,
            })
            logger.info(f"结算单 {settle_no} 介入调查 auditor={auditor}")
            return updated

    async def resolve_settlement(self, settle_no: str, resolution: str = "",
                                  auditor: str = "admin") -> dict:
        """处理完毕(investigating → resolved)

        Raises:
            KeyError: 结算单不存在
            ValueError: 状态非 investigating
        """
        async with get_lock(f"logistics:settle:{settle_no}"):
            settle = await self.repo.get_settlement(settle_no)
            if not settle:
                raise KeyError(f"结算单 {settle_no} 不存在")
            if settle.get("status") != SETTLE_STATUS_INVESTIGATING:
                raise ValueError("仅 investigating 状态可处理完毕")

            fields = {"status": SETTLE_STATUS_RESOLVED}
            if resolution:
                fields["resolution"] = resolution

            updated = await self.repo.update_settlement_fields(settle_no, fields)
            logger.info(f"结算单 {settle_no} 处理完毕 auditor={auditor}")
            return updated

    async def confirm_settlement(self, settle_no: str,
                                  auditor: str = "admin") -> dict:
        """确认结算单(reconciling/resolved → confirmed)

        Raises:
            KeyError: 结算单不存在
            ValueError: 状态非 reconciling/resolved
        """
        async with get_lock(f"logistics:settle:{settle_no}"):
            settle = await self.repo.get_settlement(settle_no)
            if not settle:
                raise KeyError(f"结算单 {settle_no} 不存在")
            current = settle.get("status")
            if current not in (SETTLE_STATUS_RECONCILING, SETTLE_STATUS_RESOLVED):
                raise ValueError("仅 reconciling/resolved 状态可确认")

            updated = await self.repo.update_settlement_fields(settle_no, {
                "status": SETTLE_STATUS_CONFIRMED,
                "confirmTime": ts(),
            })
            logger.info(f"结算单 {settle_no} 已确认 auditor={auditor}")
            return updated

    async def pay_settlement(self, settle_no: str) -> dict:
        """付款(确认已付款, 实际打款由 payment_service 处理)

        Raises:
            KeyError: 结算单不存在
            ValueError: 状态非 confirmed
        """
        async with get_lock(f"logistics:settle:{settle_no}"):
            settle = await self.repo.get_settlement(settle_no)
            if not settle:
                raise KeyError(f"结算单 {settle_no} 不存在")
            if settle.get("status") != SETTLE_STATUS_CONFIRMED:
                raise ValueError("仅 confirmed 状态可付款")

            updated = await self.repo.update_settlement_fields(settle_no, {
                "status": SETTLE_STATUS_PAID,
                "payTime": ts(),
            })
            logger.info(f"结算单 {settle_no} 已付款")
            return updated
