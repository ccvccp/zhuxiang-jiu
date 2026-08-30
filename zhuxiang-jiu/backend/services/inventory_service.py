"""库存业务:多行扣减/回补(P4.4 对齐前端契约)+ 旧单品兼容方法

多行事务语义(对齐 js/inventory-service.js mock):
    - 锁: 所有行 stock:{productId} 升序获取、反向释放(防死锁)
    - 先校验后执行: Pass1 逐行校验(id/qty 范围/商品存在/库存足够),
      Pass2 逐行扣减, Pass3 攒批写流水+预警(成功后统一提交)
    - 执行阶段防御性补偿: 逆序恢复已扣行(校验先行, 正常不触发)
    - 低库存预警: 扣后 0 < stock <= 10 写 stock_alerts
    - 响应: {success, operation, details:{totalQty, lines, alertsTriggered,
      reason, refNo}, logs, asyncOps}
    - 失败: {success: false, operation, error, failedStage, executedStages, logs}

单品兼容方法 deduct/restock 保留(test_redis_integration 在用)。
"""

import logging

from core.locks import get_lock
from repositories.inventory_repository import InventoryRepository
from repositories.supply_chain_repository import SupplyChainRepository
from services.tx_utils import (
    StageError, TxLog, acquire_locks, gen_no, now_iso, result_abort,
    result_failure, result_success,
)

logger = logging.getLogger(__name__)

# 低库存预警阈值(对齐前端 CONFIG.LOW_STOCK_THRESHOLD)
LOW_STOCK_THRESHOLD = 10
# 单行数量上限(对齐前端 CONFIG.MAX_QTY_PER_LINE)
MAX_QTY_PER_LINE = 9999


def _tx_id() -> str:
    """生成交易 ID"""
    return f"TX{gen_no('')}"


class InventoryService:
    def __init__(self, inventory_repo: InventoryRepository = InventoryRepository(),
                 sc_repo: SupplyChainRepository = SupplyChainRepository()):
        self.inventory_repo = inventory_repo
        self.sc_repo = sc_repo

    # ============================================================
    # P4.4 多行事务: deduct_lines / restock_lines
    # ============================================================

    async def deduct_lines(self, items: list[dict], reason: str = "库存扣减",
                           ref_no: str | None = None) -> dict:
        """多行库存扣减(流水 + 预警 + 补偿回滚)

        items: [{id, name?, qty}]
        """
        log = TxLog()
        # ---- preflight(锁外只读校验) ----
        log.info("阶段1-参数校验", f"开始校验扣减请求: {len(items)} 行")
        if not items:
            return result_abort("扣减清单为空", log)
        for item in items:
            if item is None or item.get("id") is None:
                return result_abort("扣减项缺少 id", log)
            qty = item.get("qty")
            if not isinstance(qty, (int, float)) or qty <= 0:
                return result_abort(f"扣减数量必须>0: {item.get('name') or item.get('id')}", log)
            if qty > MAX_QTY_PER_LINE:
                return result_abort(f"扣减数量超限: {qty} > {MAX_QTY_PER_LINE}", log)

        lock_keys = [f"stock:{item['id']}" for item in items]
        async with acquire_locks(lock_keys):
            return await self._deduct_lines_locked(items, reason, ref_no, log)

    async def _deduct_lines_locked(self, items, reason, ref_no, log) -> dict:
        """锁内执行: 校验→扣减→攒批写流水/预警"""
        log.info("阶段2-开启事务", "库存扣减事务已开启")
        executed: list[tuple] = []   # (product_id, before) 已扣行, 补偿用
        flows: list[dict] = []       # 攒批流水
        alerts: list[dict] = []      # 攒批预警
        lines: list[dict] = []
        total_qty = 0
        alerts_triggered = 0
        try:
            # Pass1: 全量校验(无副作用)
            stage = "阶段3-库存扣减"
            for item in items:
                product = await self.inventory_repo.get(item["id"])
                if not product:
                    raise StageError(stage, f"商品不存在: id={item['id']}")
                if product["stock"] < item["qty"]:
                    raise StageError(
                        stage, f"库存不足: {item.get('name') or item['id']} "
                        f"需要{item['qty']}现有{product['stock']}")
            # Pass2: 逐行扣减(校验已过, 防御性补偿)
            for item in items:
                before = await self.inventory_repo.get_stock(item["id"])
                await self.inventory_repo.deduct(item["id"], item["qty"])
                executed.append((item["id"], before))
                after = before - item["qty"]
                total_qty += item["qty"]
                lines.append({"id": item["id"], "name": item.get("name"),
                             "qty": item["qty"], "before": before, "after": after})
                flows.append({
                    "id": gen_no("IF"), "product_id": item["id"],
                    "name": item.get("name"), "type": "出库",
                    "qty": item["qty"], "before": before, "after": after,
                    "reason": reason, "ref_no": ref_no, "time": now_iso(),
                })
                if 0 < after <= LOW_STOCK_THRESHOLD:
                    alerts.append({
                        "id": gen_no("SA"), "product_id": item["id"],
                        "name": item.get("name"), "stock": after,
                        "threshold": LOW_STOCK_THRESHOLD,
                        "level": "低库存", "time": now_iso(),
                    })
                    alerts_triggered += 1
                    log.warn("阶段3-库存扣减", f"触发库存预警: {item.get('name') or item['id']} 剩余 {after}")
                log.enter(stage)
            # Pass3: 攒批统一提交(流水+预警)
            for flow in flows:
                await self.sc_repo.append("inventory_logs", flow)
            for alert in alerts:
                await self.sc_repo.append("stock_alerts", alert)
            log.info("阶段4-提交事务", f"库存扣减完成: {total_qty} 件 / {len(lines)} 行 / 预警 {alerts_triggered}")
        except StageError as err:
            # 补偿回滚: 逆序恢复已扣行
            for pid, before in reversed(executed):
                await self.inventory_repo.set_stock(pid, before)
            log.error("回滚", f"事务已回滚(补偿恢复 {len(executed)} 行)")
            return result_failure(err, log)

        return self._with_single_compat({
            "operation": "deduct",
            "details": {"totalQty": total_qty, "lines": lines,
                        "alertsTriggered": alerts_triggered,
                        "reason": reason, "refNo": ref_no},
        }, log, ["inventory_notify", "blockchain_notarize"], lines)

    @staticmethod
    def _with_single_compat(payload: dict, log: TxLog,
                            async_ops: list[str], lines: list[dict]) -> dict:
        """成功响应包装 + 单行时附加旧单品兼容字段(productId/stockAfter/txId)"""
        out = result_success(payload, log, async_ops)
        if len(lines) == 1:
            out["productId"] = lines[0]["id"]
            out["stockAfter"] = lines[0]["after"]
            out["txId"] = _tx_id()
        return out

    async def restock_lines(self, items: list[dict], reason: str = "库存回补",
                            ref_no: str | None = None) -> dict:
        """多行库存回补(流水, 对齐前端 restock 契约)"""
        log = TxLog()
        log.info("阶段1-参数校验", f"开始校验回补请求: {len(items)} 行")
        if not items:
            return result_abort("回补清单为空", log)
        for item in items:
            if item is None or item.get("id") is None:
                return result_abort("回补项缺少 id", log)
            qty = item.get("qty")
            if not isinstance(qty, (int, float)) or qty <= 0:
                return result_abort(f"回补数量必须>0: {item.get('name') or item.get('id')}", log)
            if qty > MAX_QTY_PER_LINE:
                return result_abort(f"回补数量超限: {qty} > {MAX_QTY_PER_LINE}", log)

        lock_keys = [f"stock:{item['id']}" for item in items]
        async with acquire_locks(lock_keys):
            log.info("阶段2-开启事务", "库存回补事务已开启")
            flows: list[dict] = []
            lines: list[dict] = []
            total_qty = 0
            stage = "阶段3-库存回补"
            try:
                for item in items:
                    product = await self.inventory_repo.get(item["id"])
                    if not product:
                        raise StageError(stage, f"商品不存在: id={item['id']}")
                    before = product["stock"]
                    await self.inventory_repo.restock(item["id"], item["qty"])
                    after = before + item["qty"]
                    total_qty += item["qty"]
                    lines.append({"id": item["id"], "name": item.get("name"),
                                 "qty": item["qty"], "before": before, "after": after})
                    flows.append({
                        "id": gen_no("IF"), "product_id": item["id"],
                        "name": item.get("name"), "type": "入库",
                        "qty": item["qty"], "before": before, "after": after,
                        "reason": reason, "ref_no": ref_no, "time": now_iso(),
                    })
                    log.enter(stage)
                for flow in flows:
                    await self.sc_repo.append("inventory_logs", flow)
                log.info("阶段4-提交事务", f"库存回补完成: {total_qty} 件 / {len(lines)} 行")
            except StageError as err:
                log.error("回滚", "事务已回滚(补偿)")
                return result_failure(err, log)

            return self._with_single_compat({
                "operation": "restock",
                "details": {"totalQty": total_qty, "lines": lines,
                            "reason": reason, "refNo": ref_no},
            }, log, ["inventory_notify"], lines)

    # ============================================================
    # 旧单品方法(兼容保留: test_redis_integration 在用)
    # ============================================================

    async def deduct(self, product_id, quantity: int) -> dict:
        """库存扣减(单品, 旧契约)

        Returns:
            {success, productId, stockAfter, txId} 或 {success: False, error}

        Raises:
            KeyError: 产品不存在
        """
        async with get_lock(f"stock:{product_id}"):
            product = await self.inventory_repo.get(product_id)
            if not product:
                raise KeyError(f"产品 {product_id} 不存在")
            if product["stock"] < quantity:
                return {"success": False,
                        "error": f"库存不足: 当前 {product['stock']}, 需要 {quantity}"}
            stock_after = await self.inventory_repo.deduct(product_id, quantity)
            return {
                "success": True,
                "productId": product_id,
                "stockAfter": stock_after,
                "txId": _tx_id(),
            }

    async def restock(self, product_id, quantity: int) -> dict:
        """库存回补(单品, 旧契约)

        Raises:
            KeyError: 产品不存在
        """
        async with get_lock(f"stock:{product_id}"):
            product = await self.inventory_repo.get(product_id)
            if not product:
                raise KeyError(f"产品 {product_id} 不存在")
            stock_after = await self.inventory_repo.restock(product_id, quantity)
            return {
                "success": True,
                "productId": product_id,
                "stockAfter": stock_after,
                "txId": _tx_id(),
            }
