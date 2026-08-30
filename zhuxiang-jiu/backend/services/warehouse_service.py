"""仓储业务:10 端点全量实现(P4.4 对齐前端 warehouse-service.js mock 契约)

端点(前 5 个重对齐 + 后 5 个新增):
    POST /inbound        入库上架(视觉验货+自动码垛+库位分配)
    POST /outbound       出库核销(波次拣选, 库存不足抛错回滚)
    POST /stocktake      盘点(以 actualQty 覆盖系统库存, 盘盈/亏汇总)
    POST /slot-optimize  ABC 分类库位重排(hot/warm/cold 分区)
    GET  /forecast       AI 库存预测(LSTM, 补货建议)
    POST /multi-transfer 多仓调拨(源减目标增+双向流水)
    POST /loss           损耗登记(扣库存+根因分析)
    POST /cross-dock     越库作业(库存不变, 仅流水)
    GET  /safety-stock   动态安全库存+再订货点
    GET  /env-monitor    温湿度监控(异常预警+酒龄管理)

事务语义(对齐前端):
    - 锁: stock:{wid}:{pid}(库存级) + wh:{wid}(仓库级), 升序获取
    - 先校验后执行 + 攒批落库(单据/流水 commit 前统一写入)
    - AI 状态机: 入库后 >50 sufficient/>20 normal/其他 low;
      出库/盘点后 =0 critical/<=10 low/其他 normal; 损耗后同理
"""

import logging
import math

from repositories.supply_chain_repository import SupplyChainRepository
from services.tx_utils import (
    StageError, TxLog, acquire_locks, gen_no, now_iso, result_abort,
    result_failure, result_success,
)

logger = logging.getLogger(__name__)

# 单行数量上限(对齐前端 CONFIG.MAX_QTY_PER_LINE)
MAX_QTY_PER_LINE = 9999
# 损耗类型枚举(对齐前端)
LOSS_TYPES = ("evaporation", "breakage", "quality_downgrade",
              "expired", "missing")


class WarehouseService:
    def __init__(self, sc_repo: SupplyChainRepository = SupplyChainRepository()):
        self.sc_repo = sc_repo

    # ============================================================
    # 内部工具
    # ============================================================

    async def _load_stocks(self) -> list[dict]:
        return await self.sc_repo.load("warehouse_stock")

    @staticmethod
    def _find_stock(stocks: list[dict], warehouse_id, product_id) -> dict | None:
        for s in stocks:
            if s["warehouse_id"] == warehouse_id and str(s["product_id"]) == str(product_id):
                return s
        return None

    async def _load_locations(self) -> list[dict]:
        return await self.sc_repo.load("warehouse_locations")

    @staticmethod
    def _find_empty_location(locations: list[dict], warehouse_id) -> dict | None:
        for loc in locations:
            if loc["warehouse_id"] == warehouse_id and loc["status"] == "empty":
                return loc
        return None

    async def _get_warehouse(self, warehouse_id) -> dict:
        warehouses = await self.sc_repo.load("supply_warehouses")
        for wh in warehouses:
            if str(wh["id"]) == str(warehouse_id):
                return wh
        raise StageError("阶段3", f"仓库不存在: id={warehouse_id}")

    @staticmethod
    def _ai_status_after_inbound(qty: int) -> str:
        if qty > 50:
            return "sufficient"
        if qty > 20:
            return "normal"
        return "low"

    @staticmethod
    def _ai_status_after_outbound(qty: int) -> str:
        if qty == 0:
            return "critical"
        if qty <= 10:
            return "low"
        return "normal"

    def _validate_items(self, items, field="qty", allow_zero=False) -> str | None:
        """通用清单校验, 返回错误消息(None=通过)"""
        if not items:
            return "清单为空"
        for item in items:
            if item is None or item.get("id") is None:
                return "清单项缺少 id"
            qty = item.get(field)
            if not isinstance(qty, (int, float)) or (qty <= 0 and not allow_zero):
                return f"数量必须>0: {item.get('name') or item.get('id')}"
            if qty > MAX_QTY_PER_LINE:
                return f"数量超限: {qty} > {MAX_QTY_PER_LINE}"
        return None

    # ============================================================
    # 1. AI 智能入库
    # ============================================================

    async def inbound(self, items: list[dict], warehouse_id=1,
                      reason: str = "AI智能入库",
                      ref_no: str | None = None) -> dict:
        log = TxLog()
        log.info("阶段1-参数校验", f"入库请求: {len(items)} 行 → 仓 {warehouse_id}")
        err = self._validate_items(items)
        if err:
            return result_abort(f"入库失败: {err}", log)

        lock_keys = [f"stock:{warehouse_id}:{item['id']}" for item in items]
        async with acquire_locks(lock_keys):
            log.info("阶段2-开启事务", "入库事务已开启")
            try:
                await self._get_warehouse(warehouse_id)
                stocks = await self._load_stocks()
                locations = await self._load_locations()
                order_no = gen_no("IN-")
                lines, flows, total_qty = [], [], 0
                for item in items:
                    stock = self._find_stock(stocks, warehouse_id, item["id"])
                    before = stock["stock_qty"] if stock else 0
                    if not stock:
                        loc = self._find_empty_location(locations, warehouse_id)
                        stock = {
                            "id": (max((s["id"] for s in stocks), default=0) + 1),
                            "warehouse_id": warehouse_id,
                            "location_id": loc["id"] if loc else None,
                            "product_id": item["id"], "material_id": None,
                            "stock_qty": 0, "ai_recommended_safety": 20,
                            "ai_turnover_rate": 2.5, "ai_stock_status": "normal",
                            "abc_class": "B",
                            "batch_no": item.get("batchNo")
                                        or f"BLC-{item['id']}-150001",
                            "life_code_activated_at": now_iso(),
                        }
                        stocks.append(stock)
                        if loc:
                            loc["status"] = "occupied"
                    stock["stock_qty"] = before + item["qty"]
                    stock["ai_stock_status"] = self._ai_status_after_inbound(stock["stock_qty"])
                    total_qty += item["qty"]
                    lines.append({"id": item["id"], "name": item.get("name"),
                                  "qty": item["qty"], "before": before,
                                  "after": stock["stock_qty"],
                                  "location": stock["location_id"],
                                  "aiVerified": True})
                    flows.append({
                        "id": gen_no("SM-"), "warehouse_id": warehouse_id,
                        "product_id": item["id"], "movement_type": "inbound",
                        "qty": item["qty"], "before_qty": before,
                        "after_qty": stock["stock_qty"], "reason": reason,
                        "ref_no": ref_no or order_no, "created_at": now_iso(),
                    })
                    log.enter("阶段3-AI视觉验货与码垛")
                # 攒批落库(单据+流水+主数据)
                await self.sc_repo.save("warehouse_stock", stocks)
                await self.sc_repo.save("warehouse_locations", locations)
                await self.sc_repo.append("inbound_orders", {
                    "id": gen_no(""), "order_no": order_no,
                    "warehouse_id": warehouse_id, "total_qty": total_qty,
                    "ai_verification_rate": 0.96, "status": "completed",
                    "ref_no": ref_no, "created_at": now_iso(),
                })
                for flow in flows:
                    await self.sc_repo.append("stock_movements", flow)
                log.info("阶段4-提交事务", f"入库完成: {order_no} 共 {total_qty} 件")
            except StageError as err:
                log.error("回滚", f"事务已回滚: {err}")
                return result_failure(err, log)

            return result_success({
                "operation": "inbound",
                "details": {"totalQty": total_qty, "lines": lines,
                            "aiVerificationRate": 0.96,
                            "warehouseId": warehouse_id,
                            "reason": reason, "refNo": ref_no},
            }, log, ["inbound_order", "stock_movement", "blockchain_notarize"])

    # ============================================================
    # 2. AI 智能出库
    # ============================================================

    async def outbound(self, items: list[dict], warehouse_id=1,
                       reason: str = "AI智能出库",
                       ref_no: str | None = None) -> dict:
        log = TxLog()
        log.info("阶段1-参数校验", f"出库请求: {len(items)} 行 ← 仓 {warehouse_id}")
        err = self._validate_items(items)
        if err:
            return result_abort(f"出库失败: {err}", log)

        lock_keys = [f"stock:{warehouse_id}:{item['id']}" for item in items]
        async with acquire_locks(lock_keys):
            log.info("阶段2-开启事务", "出库事务已开启")
            try:
                await self._get_warehouse(warehouse_id)
                stocks = await self._load_stocks()
                order_no = gen_no("OUT-")
                # 先全量校验
                for item in items:
                    stock = self._find_stock(stocks, warehouse_id, item["id"])
                    if not stock:
                        raise StageError("阶段3-AI波次拣选",
                                         f"库存记录不存在: productId={item['id']}")
                    if stock["stock_qty"] < item["qty"]:
                        raise StageError(
                            "阶段3-AI波次拣选",
                            f"库存不足: {item.get('name') or item['id']} "
                            f"需要{item['qty']}现有{stock['stock_qty']}")
                lines, flows, total_qty = [], [], 0
                for item in items:
                    stock = self._find_stock(stocks, warehouse_id, item["id"])
                    before = stock["stock_qty"]
                    stock["stock_qty"] = before - item["qty"]
                    stock["ai_stock_status"] = self._ai_status_after_outbound(stock["stock_qty"])
                    total_qty += item["qty"]
                    lines.append({"id": item["id"], "name": item.get("name"),
                                 "qty": item["qty"], "before": before,
                                 "after": stock["stock_qty"],
                                 "location": stock["location_id"],
                                 "wavePicked": True})
                    flows.append({
                        "id": gen_no("SM-"), "warehouse_id": warehouse_id,
                        "product_id": item["id"], "movement_type": "outbound",
                        "qty": item["qty"], "before_qty": before,
                        "after_qty": stock["stock_qty"], "reason": reason,
                        "ref_no": ref_no or order_no, "created_at": now_iso(),
                    })
                    log.enter("阶段3-AI波次拣选")
                await self.sc_repo.save("warehouse_stock", stocks)
                await self.sc_repo.append("outbound_orders", {
                    "id": gen_no(""), "order_no": order_no,
                    "warehouse_id": warehouse_id, "total_qty": total_qty,
                    "ai_picking_efficiency_gain": 0.50,
                    "status": "completed", "ref_no": ref_no,
                    "created_at": now_iso(),
                })
                for flow in flows:
                    await self.sc_repo.append("stock_movements", flow)
                log.info("阶段4-提交事务", f"出库完成: {order_no} 共 {total_qty} 件")
            except StageError as err:
                log.error("回滚", f"事务已回滚: {err}")
                return result_failure(err, log)

            return result_success({
                "operation": "outbound",
                "details": {"totalQty": total_qty, "lines": lines,
                            "pickingEfficiencyGain": 0.50,
                            "warehouseId": warehouse_id},
            }, log, ["outbound_order", "stock_movement"])

    # ============================================================
    # 3. AI 智能盘点
    # ============================================================

    async def stocktake(self, items: list[dict], warehouse_id=1,
                        method: str = "drone_ai",
                        ref_no: str | None = None) -> dict:
        log = TxLog()
        log.info("阶段1-参数校验", f"盘点请求: {len(items)} 行 @ 仓 {warehouse_id}")
        if not items:
            return result_abort("盘点失败: 清单为空", log)
        for item in items:
            if item is None or item.get("id") is None:
                return result_abort("盘点失败: 清单项缺少 id", log)
            if not isinstance(item.get("actualQty"), (int, float)) or item["actualQty"] < 0:
                return result_abort(f"实盘数量必须≥0: {item.get('name') or item['id']}", log)

        async with acquire_locks([f"wh:{warehouse_id}"]):
            log.info("阶段2-开启事务", "盘点事务已开启")
            try:
                await self._get_warehouse(warehouse_id)
                stocks = await self._load_stocks()
                diff_lines, surplus, deficit = [], 0, 0
                for item in items:
                    stock = self._find_stock(stocks, warehouse_id, item["id"])
                    system_qty = stock["stock_qty"] if stock else 0
                    actual_qty = int(item["actualQty"])
                    diff = actual_qty - system_qty
                    if diff > 0:
                        surplus += diff
                        diff_type = "surplus"
                    elif diff < 0:
                        deficit += -diff
                        diff_type = "deficit"
                    else:
                        diff_type = "match"
                    diff_lines.append({"id": item["id"], "name": item.get("name"),
                                       "systemQty": system_qty,
                                       "actualQty": actual_qty,
                                       "diff": diff, "diffType": diff_type})
                    if stock:
                        stock["stock_qty"] = actual_qty
                        stock["ai_stock_status"] = self._ai_status_after_outbound(actual_qty)
                    log.enter("阶段3-AI无人机盘点")
                await self.sc_repo.save("warehouse_stock", stocks)
                await self.sc_repo.append("stocktaking_records", {
                    "id": gen_no("ST-"), "warehouse_id": warehouse_id,
                    "method": method, "ai_accuracy": 0.98,
                    "diff_lines": diff_lines, "surplus_qty": surplus,
                    "deficit_qty": deficit, "ref_no": ref_no,
                    "created_at": now_iso(),
                })
                log.info("阶段4-提交事务",
                         f"盘点完成: 盘盈 {surplus} / 盘亏 {deficit} / 差异行 {len(diff_lines)}")
            except StageError as err:
                log.error("回滚", f"事务已回滚: {err}")
                return result_failure(err, log)

            return result_success({
                "operation": "stocktake",
                "details": {"diffLines": diff_lines, "surplusQty": surplus,
                            "deficitQty": deficit, "aiAccuracy": 0.98,
                            "method": method, "warehouseId": warehouse_id},
            }, log, ["stocktake_record"])

    # ============================================================
    # 4. AI 智能库位优化
    # ============================================================

    async def slot_optimize(self, warehouse_id=1) -> dict:
        log = TxLog()
        async with acquire_locks([f"wh:{warehouse_id}:slot"]):
            log.info("阶段2-开启事务", "库位优化事务已开启")
            try:
                await self._get_warehouse(warehouse_id)
                stocks = [s for s in await self._load_stocks()
                          if s["warehouse_id"] == warehouse_id]
                stocks.sort(key=lambda s: s.get("ai_turnover_rate", 0), reverse=True)
                locations = [l for l in await self._load_locations()
                             if l["warehouse_id"] == warehouse_id]
                hot = [l for l in locations if l["zone_type"] == "hot"]
                warm = [l for l in locations if l["zone_type"] == "warm"]
                cold = [l for l in locations if l["zone_type"] == "cold"]
                relocated, hot_n, warm_n, cold_n = [], 0, 0, 0
                all_stocks = await self._load_stocks()
                for idx, stock in enumerate(stocks):
                    if idx < len(stocks) / 3:
                        target, abc, hot_n = hot[idx % len(hot)] if hot else None, "A", hot_n + 1
                    elif idx < len(stocks) * 2 / 3:
                        target, abc, warm_n = warm[idx % len(warm)] if warm else None, "B", warm_n + 1
                    else:
                        target, abc, cold_n = cold[idx % len(cold)] if cold else None, "C", cold_n + 1
                    old_loc = stock["location_id"]
                    if target:
                        stock["location_id"] = target["id"]
                        stock["abc_class"] = abc
                        target["status"] = "occupied"
                    relocated.append({"productId": stock["product_id"],
                                      "oldLocId": old_loc,
                                      "newLocId": stock["location_id"],
                                      "abcClass": abc,
                                      "turnoverRate": stock.get("ai_turnover_rate", 0)})
                    log.enter("阶段3-AI库位优化分析")
                await self.sc_repo.save("warehouse_stock", all_stocks)
                await self.sc_repo.save("warehouse_locations", locations)
                await self.sc_repo.append("stock_movements", {
                    "id": gen_no("SM-"), "warehouse_id": warehouse_id,
                    "optimization_no": gen_no("AO-"),
                    "movement_type": "slot_optimization",
                    "ai_gain": 0.30, "relocated": len(relocated),
                    "created_at": now_iso(),
                })
                log.info("阶段4-提交事务",
                         f"库位重排完成: {len(relocated)} 项(hot {hot_n}/warm {warm_n}/cold {cold_n})")
            except StageError as err:
                log.error("回滚", f"事务已回滚: {err}")
                return result_failure(err, log)

            return result_success({
                "operation": "slotOptimize",
                "details": {"relocatedItems": relocated,
                            "optimizationGain": 0.30,
                            "hotZoneCount": hot_n, "warmZoneCount": warm_n,
                            "coldZoneCount": cold_n,
                            "warehouseId": warehouse_id},
            }, log, ["ai_optimization_record"])

    # ============================================================
    # 5. AI 智能库存预测(GET)
    # ============================================================

    async def forecast(self, product_id, warehouse_id=1,
                       horizon_days: int = 30) -> dict:
        log = TxLog()
        stocks = await self._load_stocks()
        stock = self._find_stock(stocks, warehouse_id, product_id)
        if not stock:
            return {"success": False,
                    "error": f"库存记录不存在: productId={product_id}"}
        current = stock["stock_qty"]
        daily = max(1, round(current / 30))          # 日均消耗推演
        season = 1.1                                   # 旺季系数
        demand = round(daily * horizon_days * season)
        days_of_supply = round(current / daily, 1)
        replenish = days_of_supply < 7
        return {
            "success": True,
            "details": {"productId": product_id,
                        "warehouseId": warehouse_id,
                        "horizonDays": horizon_days, "currentQty": current,
                        "forecastedDemand": demand, "dailyConsumption": daily,
                        "seasonFactor": season, "daysOfSupply": days_of_supply,
                        "replenishmentSuggested": replenish,
                        "aiAccuracy": 0.89, "aiModel": "LSTM"},
            "logs": log.logs,
        }

    # ============================================================
    # 6. AI 智能多仓调拨
    # ============================================================

    async def multi_transfer(self, items: list[dict], from_warehouse_id,
                             to_warehouse_id, reason: str = "AI智能多仓调拨",
                             ref_no: str | None = None) -> dict:
        log = TxLog()
        log.info("阶段1-参数校验",
                 f"调拨请求: {len(items)} 行 仓{from_warehouse_id}→仓{to_warehouse_id}")
        err = self._validate_items(items)
        if err:
            return result_abort(f"调拨失败: {err}", log)
        if str(from_warehouse_id) == str(to_warehouse_id):
            return result_abort("调拨失败: 源仓与目标仓不能相同", log)

        lock_keys = [f"wh:{from_warehouse_id}", f"wh:{to_warehouse_id}"]
        for item in items:
            lock_keys.append(f"stock:{from_warehouse_id}:{item['id']}")
            lock_keys.append(f"stock:{to_warehouse_id}:{item['id']}")
        async with acquire_locks(lock_keys):
            log.info("阶段2-开启事务", "多仓调拨事务已开启")
            try:
                await self._get_warehouse(from_warehouse_id)
                await self._get_warehouse(to_warehouse_id)
                stocks = await self._load_stocks()
                locations = await self._load_locations()
                order_no = gen_no("TR-")
                for item in items:
                    src = self._find_stock(stocks, from_warehouse_id, item["id"])
                    if not src:
                        raise StageError("阶段3-AI智能调拨",
                                         f"源仓库存记录不存在: productId={item['id']}")
                    if src["stock_qty"] < item["qty"]:
                        raise StageError(
                            "阶段3-AI智能调拨",
                            f"源仓库存不足: {item.get('name') or item['id']} "
                            f"需要{item['qty']}现有{src['stock_qty']}")
                transfer_lines, flows, total_qty = [], [], 0
                for item in items:
                    src = self._find_stock(stocks, from_warehouse_id, item["id"])
                    dst = self._find_stock(stocks, to_warehouse_id, item["id"])
                    from_before = src["stock_qty"]
                    to_before = dst["stock_qty"] if dst else 0
                    src["stock_qty"] = from_before - item["qty"]
                    src["ai_stock_status"] = self._ai_status_after_outbound(src["stock_qty"])
                    if not dst:
                        loc = self._find_empty_location(locations, to_warehouse_id)
                        dst = {
                            "id": (max((s["id"] for s in stocks), default=0) + 1),
                            "warehouse_id": to_warehouse_id,
                            "location_id": loc["id"] if loc else None,
                            "product_id": item["id"], "material_id": None,
                            "stock_qty": 0, "ai_recommended_safety": 20,
                            "ai_turnover_rate": 2.0, "ai_stock_status": "normal",
                            "abc_class": "B", "batch_no": src.get("batch_no", ""),
                            "life_code_activated_at": now_iso(),
                        }
                        stocks.append(dst)
                        if loc:
                            loc["status"] = "occupied"
                    dst["stock_qty"] = to_before + item["qty"]
                    dst["ai_stock_status"] = self._ai_status_after_inbound(dst["stock_qty"])
                    total_qty += item["qty"]
                    transfer_lines.append({
                        "id": item["id"], "name": item.get("name"),
                        "qty": item["qty"], "fromBefore": from_before,
                        "fromAfter": src["stock_qty"], "toBefore": to_before,
                        "toAfter": dst["stock_qty"]})
                    flows.append({
                        "id": gen_no("SM-"), "warehouse_id": from_warehouse_id,
                        "product_id": item["id"], "movement_type": "transfer_out",
                        "qty": item["qty"], "before_qty": from_before,
                        "after_qty": src["stock_qty"], "reason": reason,
                        "ref_no": ref_no or order_no, "created_at": now_iso(),
                    })
                    flows.append({
                        "id": gen_no("SM-"), "warehouse_id": to_warehouse_id,
                        "product_id": item["id"], "movement_type": "transfer_in",
                        "qty": item["qty"], "before_qty": to_before,
                        "after_qty": dst["stock_qty"], "reason": reason,
                        "ref_no": ref_no or order_no, "created_at": now_iso(),
                    })
                    log.enter("阶段3-AI智能调拨")
                await self.sc_repo.save("warehouse_stock", stocks)
                await self.sc_repo.save("warehouse_locations", locations)
                await self.sc_repo.append("transfer_orders", {
                    "id": gen_no(""), "order_no": order_no,
                    "from_warehouse_id": from_warehouse_id,
                    "to_warehouse_id": to_warehouse_id,
                    "total_qty": total_qty, "ai_timeliness": 0.92,
                    "status": "completed", "ref_no": ref_no,
                    "created_at": now_iso(),
                })
                for flow in flows:
                    await self.sc_repo.append("stock_movements", flow)
                log.info("阶段4-提交事务", f"调拨完成: {order_no} 共 {total_qty} 件")
            except StageError as err:
                log.error("回滚", f"事务已回滚: {err}")
                return result_failure(err, log)

            return result_success({
                "operation": "multiTransfer",
                "details": {"transferLines": transfer_lines,
                            "totalQty": total_qty,
                            "transferTimeliness": 0.92,
                            "fromWarehouseId": from_warehouse_id,
                            "toWarehouseId": to_warehouse_id},
            }, log, ["transfer_order", "stock_movement"])

    # ============================================================
    # 7. AI 智能损耗管理
    # ============================================================

    async def loss(self, items: list[dict], warehouse_id=1,
                   loss_type: str = "evaporation",
                   ref_no: str | None = None) -> dict:
        log = TxLog()
        log.info("阶段1-参数校验", f"损耗登记: {len(items)} 行 @ 仓 {warehouse_id} 类型 {loss_type}")
        err = self._validate_items(items)
        if err:
            return result_abort(f"损耗登记失败: {err}", log)
        if loss_type not in LOSS_TYPES:
            return result_abort(f"损耗登记失败: 非法损耗类型({loss_type})", log)

        lock_keys = [f"wh:{warehouse_id}"] + [
            f"stock:{warehouse_id}:{item['id']}" for item in items]
        async with acquire_locks(lock_keys):
            log.info("阶段2-开启事务", "损耗登记事务已开启")
            try:
                await self._get_warehouse(warehouse_id)
                stocks = await self._load_stocks()
                for item in items:
                    stock = self._find_stock(stocks, warehouse_id, item["id"])
                    if not stock:
                        raise StageError("阶段3-AI损耗登记",
                                         f"库存记录不存在: productId={item['id']}")
                    if stock["stock_qty"] < item["qty"]:
                        raise StageError(
                            "阶段3-AI损耗登记",
                            f"库存不足: {item.get('name') or item['id']} "
                            f"需要{item['qty']}现有{stock['stock_qty']}")
                loss_lines, flows, total_loss = [], [], 0
                for item in items:
                    stock = self._find_stock(stocks, warehouse_id, item["id"])
                    before = stock["stock_qty"]
                    stock["stock_qty"] = before - item["qty"]
                    stock["ai_stock_status"] = self._ai_status_after_outbound(stock["stock_qty"])
                    total_loss += item["qty"]
                    loss_lines.append({
                        "id": item["id"], "name": item.get("name"),
                        "qty": item["qty"], "before": before,
                        "after": stock["stock_qty"], "lossType": loss_type,
                        "rootCause": item.get("rootCause", "AI根因分析完成")})
                    flows.append({
                        "id": gen_no("SM-"), "warehouse_id": warehouse_id,
                        "product_id": item["id"], "movement_type": "loss",
                        "qty": item["qty"], "before_qty": before,
                        "after_qty": stock["stock_qty"],
                        "reason": f"损耗({loss_type})",
                        "ref_no": ref_no, "created_at": now_iso(),
                    })
                    log.enter("阶段3-AI损耗登记")
                await self.sc_repo.save("warehouse_stock", stocks)
                await self.sc_repo.append("loss_records", {
                    "id": gen_no("LR-"), "warehouse_id": warehouse_id,
                    "loss_type": loss_type, "loss_lines": loss_lines,
                    "total_loss_qty": total_loss, "ai_reduction_rate": 0.20,
                    "ai_root_cause": "AI根因分析完成", "ref_no": ref_no,
                    "created_at": now_iso(),
                })
                for flow in flows:
                    await self.sc_repo.append("stock_movements", flow)
                log.info("阶段4-提交事务", f"损耗登记完成: 共 {total_loss} 件")
            except StageError as err:
                log.error("回滚", f"事务已回滚: {err}")
                return result_failure(err, log)

            return result_success({
                "operation": "loss",
                "details": {"lossLines": loss_lines, "totalLossQty": total_loss,
                            "lossReduction": 0.20, "lossType": loss_type,
                            "warehouseId": warehouse_id},
            }, log, ["loss_record", "ai_root_cause"])

    # ============================================================
    # 8. AI 智能仓配一体(越库)
    # ============================================================

    async def cross_dock(self, items: list[dict], warehouse_id=1,
                         carrier_id: str = "LOGISTICS-06",
                         ref_no: str | None = None) -> dict:
        log = TxLog()
        log.info("阶段1-参数校验", f"越库请求: {len(items)} 行 @ 仓 {warehouse_id}")
        err = self._validate_items(items)
        if err:
            return result_abort(f"越库失败: {err}", log)

        async with acquire_locks([f"wh:{warehouse_id}"]):
            log.info("阶段2-开启事务", "越库事务已开启")
            try:
                await self._get_warehouse(warehouse_id)
                dock_no = gen_no("CD-")
                lines, total_qty = [], 0
                for item in items:
                    total_qty += item["qty"]
                    lines.append({
                        "id": item["id"], "name": item.get("name"),
                        "qty": item["qty"],
                        "inboundVehicle": item.get("inboundVehicle", ""),
                        "outboundVehicle": carrier_id, "crossDocked": True})
                    log.enter("阶段3-AI越库作业")
                for item in items:
                    await self.sc_repo.append("stock_movements", {
                        "id": gen_no("SM-"), "warehouse_id": warehouse_id,
                        "product_id": item["id"], "movement_type": "cross_dock",
                        "qty": item["qty"], "before_qty": 0, "after_qty": 0,
                        "dock_no": dock_no, "carrier_id": carrier_id,
                        "reason": "AI智能越库(不入库直接分发)",
                        "ref_no": ref_no, "created_at": now_iso(),
                    })
                await self.sc_repo.append("cross_dock_records", {
                    "id": gen_no(""), "dock_no": dock_no,
                    "warehouse_id": warehouse_id, "total_qty": total_qty,
                    "cross_dock_rate": 0.40, "carrier_id": carrier_id,
                    "lines": lines, "ref_no": ref_no, "created_at": now_iso(),
                })
                log.info("阶段4-提交事务", f"越库完成: {dock_no} 共 {total_qty} 件")
            except StageError as err:
                log.error("回滚", f"事务已回滚: {err}")
                return result_failure(err, log)

            return result_success({
                "operation": "crossDock",
                "details": {"crossDockLines": lines, "totalQty": total_qty,
                            "crossDockRate": 0.40, "carrierId": carrier_id,
                            "warehouseId": warehouse_id},
            }, log, ["cross_dock_movement", "logistics_integration"])

    # ============================================================
    # 9. AI 智能安全库存(GET)
    # ============================================================

    async def safety_stock(self, product_id, warehouse_id=1) -> dict:
        stocks = await self._load_stocks()
        stock = self._find_stock(stocks, warehouse_id, product_id)
        if not stock:
            return {"success": False,
                    "error": f"库存记录不存在: productId={product_id}"}
        current = stock["stock_qty"]
        avg_daily = max(1, round(current / 30))
        lead_time = 7
        # 95% 服务水平(z=1.645)的简化动态安全库存
        safety = math.ceil(avg_daily * lead_time * 1.645 * 0.5)
        reorder = safety + avg_daily * lead_time
        # 副作用: 回写 AI 推荐安全库存(对齐前端 mock)
        stock["ai_recommended_safety"] = safety
        await self.sc_repo.save("warehouse_stock", stocks)
        return {
            "success": True,
            "details": {"productId": product_id,
                        "warehouseId": warehouse_id, "currentQty": current,
                        "aiRecommendedSafety": safety,
                        "reorderPoint": reorder, "avgDailyDemand": avg_daily,
                        "leadTime": lead_time, "serviceLevel": "95%",
                        "turnoverImprovement": 0.25,
                        "aiModel": "动态安全库存模型"},
        }

    # ============================================================
    # 10. AI 智能温湿度监控(GET)
    # ============================================================

    async def env_monitor(self, warehouse_id=1) -> dict:
        try:
            wh = await self._get_warehouse(warehouse_id)
        except StageError as err:
            return {"success": False, "error": str(err)}
        # 确定性伪随机温湿度(基于记录数, 避免测试不可重现)
        records = await self.sc_repo.list_all("environment_monitoring")
        seq = len(records)
        temp = 18 + (seq * 7) % 22            # 18~39 ℃
        humidity = 45 + (seq * 13) % 45       # 45~89 %
        has_anomaly = not (5 <= temp <= 35) or not (40 <= humidity <= 80)
        anomaly_type = None
        if not (5 <= temp <= 35):
            anomaly_type = "temp"
        elif not (40 <= humidity <= 80):
            anomaly_type = "humidity"
        # 酒龄管理(激活超 3 个月视为陈化候选)
        stocks = [s for s in await self._load_stocks()
                  if s["warehouse_id"] == warehouse_id]
        aged = []
        for s in stocks[:10]:
            activated = str(s.get("life_code_activated_at", ""))
            age_months = max(0, (12 if "2025" in activated else 13) - 12) if activated else 1
            aged.append({"productId": s["product_id"],
                         "ageMonths": age_months + 1,
                         "eligible": True})
        # 副作用: 追加监控记录(对齐前端 mock)
        await self.sc_repo.append("environment_monitoring", {
            "id": gen_no("EM-"), "warehouse_id": warehouse_id,
            "temp": temp, "humidity": humidity,
            "has_anomaly": has_anomaly, "created_at": now_iso(),
        })
        return {
            "success": True,
            "details": {"warehouseId": warehouse_id,
                        "warehouseName": wh["warehouse_name"],
                        "temp": temp, "humidity": humidity,
                        "tempRange": [5, 35], "humidityRange": [40, 80],
                        "hasAnomaly": has_anomaly,
                        "anomalyType": anomaly_type,
                        "aiAnomalyDetection": 0.95,
                        "agedStocks": aged,
                        "monitoringRecords": seq + 1},
        }
