"""仓储业务:入库/出库/盘点/库位优化/预测

无并发风险:日志追加 + 静态返回(纯 Mock 业务规则)
"""

import logging

from core.helpers import ts
from repositories.backend import is_redis_mode
from repositories.warehouse_repository import WarehouseRepository

logger = logging.getLogger(__name__)


class WarehouseService:
    def __init__(self, warehouse_repo: WarehouseRepository = WarehouseRepository()):
        self.warehouse_repo = warehouse_repo

    async def inbound(self, product_id) -> dict:
        """AI智能入库"""
        log = {"action": "inbound", "productId": product_id, "time": ts(), "slot": "A1"}
        await self.warehouse_repo.append_inbound_log(log)
        return {
            "success": True,
            "productId": product_id,
            "slot": "A1",
            "message": "视觉验货通过,自动码垛完成,库位 A1 已分配",
        }

    async def outbound(self, product_id) -> dict:
        """AI智能出库"""
        log = {"action": "outbound", "productId": product_id, "time": ts()}
        await self.warehouse_repo.append_outbound_log(log)
        return {
            "success": True,
            "productId": product_id,
            "message": "波次拣选完成,路径优化 30% 提升,自动分拣完成",
        }

    async def stocktake(self) -> dict:
        """AI智能盘点(基于已知库位集合统计,双模式结果一致)

        Redis 模式下空库位(值为 None)不会写入 Hash, 导致 len(slots) 偏小;
        使用 KNOWN_SLOTS 常量保证 totalSlots 跨模式一致(与 seed_redis.py 对齐)。
        """
        slots = await self.warehouse_repo.get_slots()
        KNOWN_SLOTS = {"A1", "A2", "B1"}
        total = len(KNOWN_SLOTS)
        occupied = sum(1 for slot in KNOWN_SLOTS if slots.get(slot) is not None)
        empty_set = sorted(s for s in KNOWN_SLOTS if slots.get(s) is None)
        mode = "redis" if is_redis_mode() else "memory"
        logger.info("stocktake mode=%s store_keys=%s known=%d occupied=%d empty=%d empty_slots=%s",
                    mode, sorted(slots.keys()), total, occupied, len(empty_set), empty_set)
        return {
            "success": True,
            "totalSlots": total,
            "occupiedSlots": occupied,
            "emptySlots": total - occupied,
            "accuracy": 0.98,
            "message": "无人机+视觉AI盘点完成,准确率 98%",
        }

    def slot_optimize(self) -> dict:
        """AI智能库位优化(Mock 推演结果)"""
        return {
            "success": True,
            "optimized": True,
            "utilizationBefore": 0.65,
            "utilizationAfter": 0.85,
            "improvement": "30%",
            "message": "ABC分类+冷热区+高频前置,库位利用率提升 30%",
        }

    def forecast(self, product_id=None) -> dict:
        """AI智能库存预测(Mock 季节性+趋势)"""
        return {
            "success": True,
            "productId": product_id or "ZX42-2026L07",
            "forecast7d": [120, 135, 128, 142, 150, 145, 138],
            "seasonality": "上升期",
            "accuracy": 0.89,
            "message": "季节性+趋势+OEM排程驱动,预测准确率 89%",
        }
