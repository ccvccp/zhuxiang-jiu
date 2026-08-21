/**
 * warehouse-service.js  ·  AI智能仓储服务模块（模块28核心API）
 * ============================================================
 * 用途:
 *   封装模块28(AI智能仓储与库存优化模块)的10个AI能力为独立API服务接口。
 *   遵循 inventory-service.js 既有服务规范:
 *     · FIFO Mutex 悲观锁(引用 mutex.js 的 Mutex,兜底同实现)
 *     · TransactionTemplate 事务编排(BEGIN/COMMIT/ROLLBACK)
 *     · Mock/Live 双模式(EnvAdapter 适配 H5/小程序/APP)
 *     · dbRef 在锁内创建(避免并发 lost-update)
 *     · begin 先 push BEGIN 再取快照(rollback 不丢 BEGIN)
 *
 * 10 个 API 端点(对应 10 个 AI 能力):
 *   写事务(状态变更):
 *     · inbound(items)            AI智能入库  → 入库单+库存增+流水+库位分配
 *     · outbound(items)          AI智能出库  → 出库单+库存减+流水+波次拣选
 *     · stocktake(items)          AI智能盘点  → 盘点单+差异调整+盘盈盘亏
 *     · multiTransfer(transfer)  AI智能多仓协同 → 调拨单+源仓减+目标仓增
 *     · loss(record)             AI智能损耗管理 → 损耗单+库存减+根因分析
 *     · crossDock(items)         AI智能仓配一体 → 越库单+不入库直接分发
 *   AI分析(读为主,写 AI 优化记录):
 *     · slotOptimize(whId)        AI智能库位优化 → ABC分类+冷热区+高频前置
 *     · forecast(productId)       AI智能库存预测 → 季节性+趋势+OEM排程
 *     · safetyStock(productId)    AI智能安全库存 → 动态安全库存(需求波动+提前期)
 *     · envMonitor(whId)         AI智能温湿度监控 → IoT传感+异常预警+酒龄管理
 *
 * 数据库表(12张, 对应 module28-db-schema.sql):
 *   warehouses/warehouse_locations/inventory_stock/inbound_orders/
 *   outbound_orders/stock_movements/stocktaking_records/environment_monitoring/
 *   loss_records/multi_warehouse_transfers/ai_optimization_records/ai_compliance_monitoring
 *
 * 浏览器环境:
 *   需先加载 toolkit/upgrade-logger.js + toolkit/transaction-template.js + mutex.js
 *   全局名: WarehouseService / window.WarehouseService
 * ============================================================
 */

const WarehouseService = (function () {
    'use strict';

    const STORAGE_KEY = 'zhuxiang_warehouse_db_v1';

    // ---------- 配置 ----------
    const CONFIG = {
        // AI 入库验货准确率(对应 mock inboundAccuracy=96%)
        AI_INBOUND_ACCURACY: 0.96,
        // AI 出库拣选效率提升(对应 mock pickingEfficiency=50%)
        AI_PICKING_EFFICIENCY_GAIN: 0.50,
        // AI 盘点准确率(对应 mock stocktakeAccuracy=98%)
        AI_STOCKTAKE_ACCURACY: 0.98,
        // AI 库位优化提升(对应 mock slotOptimization=30%)
        AI_SLOT_OPTIMIZATION_GAIN: 0.30,
        // AI 库存预测准确率(对应 mock forecastAccuracy=89%)
        AI_FORECAST_ACCURACY: 0.89,
        // AI 安全库存周转提升(对应 mock turnoverImprovement=25%)
        AI_TURNOVER_IMPROVEMENT: 0.25,
        // AI 温湿度异常发现率(对应 mock envAnomalyDetection=95%)
        AI_ENV_ANOMALY_DETECTION: 0.95,
        // AI 多仓调拨及时率(对应 mock transferTimeliness=92%)
        AI_TRANSFER_TIMELINESS: 0.92,
        // AI 损耗降低(对应 mock lossReduction=20%)
        AI_LOSS_REDUCTION: 0.20,
        // AI 越库率(对应 mock crossDockRate=40%)
        AI_CROSS_DOCK_RATE: 0.40,
        // 低库存预警阈值
        LOW_STOCK_THRESHOLD: 10,
        // 单次操作数量上限(防误操作)
        MAX_QTY_PER_LINE: 9999,
        // 温湿度合规区间(白酒储存)
        TEMP_RANGE: [5, 35],     // 温度(℃)
        HUMIDITY_RANGE: [40, 80], // 湿度(%)
    };

    let mode = 'mock'; // 'mock' | 'live'
    let apiBase = '/api/warehouse';

    // ---------- 悲观锁(Mutex, FIFO 队列实现,与 inventory-service.js 一致) ----------
    // 引用独立工具类 mutex.js 的 Mutex(若未加载则内部兜底, 同为 FIFO)
    // 锁 key 命名:
    //   · stock:{warehouseId}:{productId}  库存级(防超卖,与 inventory-service 的 stock:{pid} 区分)
    //   · wh:{warehouseId}                仓库级(盘点/库位优化/温湿度)
    //   · wh:{warehouseId}:slot           库位级(库位优化串行)
    const _mutex = (typeof Mutex !== 'undefined') ? new Mutex() : null;
    const _mutexLocked = {};   // 兜底: key → true(是否持有)
    const _mutexQueues = {};   // 兜底: key → resolve 函数数组(FIFO 等待队列)

    async function _acquireMutex(key) {
        if (_mutex) return await _mutex.acquire(key);
        // 空闲: 直接获取(同步检查+设置, JS 单线程内原子)
        if (!_mutexLocked[key]) {
            _mutexLocked[key] = true;
            return () => _releaseMutex(key);
        }
        // 竞争: 入队等待, 每个 waiter 独立 Promise, release 只唤醒队首一个
        return new Promise(resolve => {
            (_mutexQueues[key] || (_mutexQueues[key] = [])).push(resolve);
        });
    }

    function _releaseMutex(key) {
        const q = _mutexQueues[key];
        const next = q && q.length ? q.shift() : null;
        if (next) {
            // 交接: 仅唤醒队首一个 waiter, 把它的 release 传给它; _locked 保持 true 不留空窗
            next(() => _releaseMutex(key));
        } else {
            delete _mutexLocked[key];
            delete _mutexQueues[key];
        }
    }

    async function _withMutex(keys, fn) {
        if (_mutex) return await _mutex.withLocks(keys, fn);
        const sorted = [...new Set(keys)].sort();
        const releases = [];
        for (const k of sorted) releases.push(await _acquireMutex(k));
        try { return await fn(); }
        finally {
            for (let i = releases.length - 1; i >= 0; i--) {
                try { releases[i](); } catch (e) { /* ignore */ }
            }
        }
    }

    // ---------- Mock DB ----------
    function readDB() {
        try {
            return EnvAdapter.storage.get(STORAGE_KEY) || initMockDB(true);
        } catch (e) {
            return initMockDB(true);
        }
    }

    function writeDB(db) {
        EnvAdapter.storage.set(STORAGE_KEY, db);
    }

    function initMockDB(forceWrite) {
        const existing = forceWrite ? null : EnvAdapter.storage.get(STORAGE_KEY);
        if (existing && !forceWrite) return existing;

        // 从全局 PRODUCTS 初始化库存(若 data.js 未加载,用默认值)
        const products = (typeof PRODUCTS !== 'undefined' ? PRODUCTS : [
            { id: 1, name: '竹奕·竹香经典 500ml', price: 268 },
            { id: 2, name: '竹奕·竹韵佳酿 500ml', price: 368 },
            { id: 3, name: '竹奕·竹香珍藏 500ml', price: 698 },
        ]);

        // 仓库主表(对应 warehouses 表)
        const warehouses = [
            { id: 1, warehouse_code: 'WH-FACTORY-01', warehouse_name: '山东泰安工厂仓', warehouse_type: 'factory', ai_warehouse_score: 92.5, ai_utilization_rate: 78.0, ai_efficiency_score: 90.0, status: 'active' },
            { id: 2, warehouse_code: 'WH-REGION-01', warehouse_name: '华东区域仓', warehouse_type: 'regional', ai_warehouse_score: 88.0, ai_utilization_rate: 65.0, ai_efficiency_score: 85.0, status: 'active' },
            { id: 3, warehouse_code: 'WH-RETAIL-01', warehouse_name: '上海零售仓', warehouse_type: 'retail', ai_warehouse_score: 85.0, ai_utilization_rate: 70.0, ai_efficiency_score: 82.0, status: 'active' },
            { id: 4, warehouse_code: 'WH-AGING-01', warehouse_name: '陈酿仓', warehouse_type: 'aging', ai_warehouse_score: 95.0, ai_utilization_rate: 60.0, ai_efficiency_score: 88.0, status: 'active' },
        ];

        // 库位表(对应 warehouse_locations 表, 区-排-列-层 4级)
        const locations = [];
        let locId = 1;
        for (let z = 1; z <= 3; z++) {       // 区
            for (let r = 1; r <= 5; r++) {   // 排
                for (let l = 1; l <= 4; l++) { // 列
                    for (let f = 1; f <= 3; f++) { // 层
                        locations.push({
                            id: locId++,
                            warehouse_id: 1,
                            location_code: 'A' + z + '-' + r + '-' + l + '-' + f,
                            zone_type: z === 1 ? 'hot' : (z === 2 ? 'warm' : 'cold'),
                            abc_class: z === 1 ? 'A' : (z === 2 ? 'B' : 'C'),
                            status: 'empty',
                        });
                    }
                }
            }
        }

        // 库存表(对应 inventory_stock 表)
        const inventoryStock = products.map((p, idx) => ({
            id: idx + 1,
            warehouse_id: 1,
            location_id: idx + 1,
            product_id: p.id,
            material_id: null,
            stock_qty: 100,
            ai_recommended_safety: 20,
            ai_turnover_rate: 2.5,
            ai_stock_status: 'normal',
            abc_class: idx === 0 ? 'A' : (idx === 1 ? 'B' : 'C'),
            batch_no: 'BLC-ZX42-2026L07-150001',
            life_code_activated_at: '2026-07-01T00:00:00Z',
        }));

        const db = {
            warehouses: warehouses,
            warehouse_locations: locations,
            inventory_stock: inventoryStock,
            inbound_orders: [],          // 入库单
            outbound_orders: [],          // 出库单
            stock_movements: [],          // 库存流水
            stocktaking_records: [],      // 盘点记录
            environment_monitoring: [],   // 温湿度监控
            loss_records: [],             // 损耗记录
            multi_warehouse_transfers: [],// 多仓调拨
            ai_optimization_records: [],  // AI优化记录
            ai_compliance_monitoring: [], // AI合规监控
            tx_log: [],                   // 事务日志: BEGIN/COMMIT/ROLLBACK
        };
        writeDB(db);
        return db;
    }

    // ---------- Mock 事务适配器(快照模式,与 inventory-service.js 一致) ----------
    function createAdapter(dbRef) {
        return {
            begin(ctx) {
                // 先 push BEGIN 再取快照: 确保快照包含本次 BEGIN,
                // 否则 rollback 用快照恢复时会丢失本次 BEGIN(只剩 ROLLBACK), 导致事务原子性校验失败
                dbRef.db.tx_log.push({ type: 'BEGIN', time: new Date().toISOString() });
                const snapshot = JSON.parse(JSON.stringify(dbRef.db));
                ctx.logger.info('阶段2-开启事务', '事务已开启(快照已建立)', {
                    txLogLen: dbRef.db.tx_log.length,
                });
                return snapshot;
            },
            commit(_snapshot, ctx) {
                dbRef.db.tx_log.push({ type: 'COMMIT', time: new Date().toISOString() });
                writeDB(dbRef.db);
                ctx.logger.info('阶段4-事务提交', '事务提交成功(已写入)', {});
            },
            rollback(snapshot, ctx) {
                if (snapshot) {
                    // 统一持久化顺序: 1.记录ROLLBACK → 2.恢复内存引用 → 3.持久化 → 4.日志
                    snapshot.tx_log.push({ type: 'ROLLBACK', time: new Date().toISOString() });
                    dbRef.db = snapshot;
                    writeDB(snapshot);
                    ctx.logger.error('回滚', '事务已回滚(快照恢复)', {});
                }
            },
        };
    }

    // ---------- 工具: 确保 db 拥有仓储相关表 ----------
    function ensureTables(db) {
        if (!Array.isArray(db.warehouses)) db.warehouses = [];
        if (!Array.isArray(db.warehouse_locations)) db.warehouse_locations = [];
        if (!Array.isArray(db.inventory_stock)) db.inventory_stock = [];
        if (!Array.isArray(db.inbound_orders)) db.inbound_orders = [];
        if (!Array.isArray(db.outbound_orders)) db.outbound_orders = [];
        if (!Array.isArray(db.stock_movements)) db.stock_movements = [];
        if (!Array.isArray(db.stocktaking_records)) db.stocktaking_records = [];
        if (!Array.isArray(db.environment_monitoring)) db.environment_monitoring = [];
        if (!Array.isArray(db.loss_records)) db.loss_records = [];
        if (!Array.isArray(db.multi_warehouse_transfers)) db.multi_warehouse_transfers = [];
        if (!Array.isArray(db.ai_optimization_records)) db.ai_optimization_records = [];
        if (!Array.isArray(db.ai_compliance_monitoring)) db.ai_compliance_monitoring = [];
        if (!Array.isArray(db.tx_log)) db.tx_log = [];
        return db;
    }

    // ---------- 工具: 查找库存记录 ----------
    function findStock(db, warehouseId, productId) {
        return db.inventory_stock.find(s =>
            s.warehouse_id === warehouseId && s.product_id === productId);
    }

    // ---------- 工具: 查找空闲库位 ----------
    function findEmptyLocation(db, warehouseId) {
        return db.warehouse_locations.find(l =>
            l.warehouse_id === warehouseId && l.status === 'empty');
    }

    // ---------- 工具: 生成单号 ----------
    function genOrderNo(prefix) {
        return prefix + Date.now() + '-' + Math.floor(Math.random() * 1000);
    }

    // ============================================================
    //  AI 能力 1: AI智能入库 (POST /api/warehouse/inbound)
    //  视觉验货+自动码垛+库位分配,验货准确率96%
    // ============================================================
    async function inbound(params) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) {
            throw new Error('WarehouseService 需要工具包,请先加载 js/toolkit/upgrade-logger.js 和 js/toolkit/transaction-template.js');
        }

        const { items, warehouseId = 1, reason = 'AI智能入库', refNo = null } = params;
        const lockKeys = items.map(i => 'stock:' + warehouseId + ':' + i.id);

        const result = await _withMutex(lockKeys, () => {
            // dbRef 在锁内创建: 加锁后才 readDB() 读最新已提交状态(避免 lost-update)
            const dbRef = { db: readDB() };
            const adapter = createAdapter(dbRef);
            const template = new Template({ name: 'warehouse_inbound', adapter: adapter });
            return template.run({
                context: {
                    items, warehouseId, reason, refNo,
                    inboundLines: [], totalQty: 0, aiVerificationRate: CONFIG.AI_INBOUND_ACCURACY,
                },
                preflight: async (ctx) => {
                    ctx.logger.info('阶段1-参数校验', '开始校验入库请求', {
                        lineCount: ctx.items.length, warehouseId: ctx.warehouseId,
                    });
                    if (!ctx.items || ctx.items.length === 0) {
                        return { abort: true, reason: '入库清单为空' };
                    }
                    for (const item of ctx.items) {
                        const qty = Number(item.qty);
                        if (!item || typeof item.id === 'undefined') {
                            return { abort: true, reason: '入库项缺少 id' };
                        }
                        if (!Number.isFinite(qty) || qty <= 0) {
                            return { abort: true, reason: '入库数量必须>0: ' + (item.name || 'id=' + item.id) };
                        }
                        if (qty > CONFIG.MAX_QTY_PER_LINE) {
                            return { abort: true, reason: '入库数量超限: ' + qty };
                        }
                    }
                },
                stages: [
                    {
                        name: '阶段2-开启事务',
                        action: async (ctx) => { ctx.conn = await ctx.template.adapter.begin(ctx); },
                    },
                    {
                        name: '阶段3-AI视觉验货与码垛',
                        action: async (ctx) => {
                            ensureTables(dbRef.db);
                            const wh = dbRef.db.warehouses.find(w => w.id === ctx.warehouseId);
                            if (!wh) throw new Error('仓库不存在: id=' + ctx.warehouseId);

                            // 先生成入库单号(供流水引用)
                            ctx.orderNo = genOrderNo('IN-');
                            for (const item of ctx.items) {
                                const qty = Number(item.qty);
                                // AI 视觉验货(96%准确率模拟:全通过,记录验货分)
                                let stock = findStock(dbRef.db, ctx.warehouseId, item.id);
                                const before = stock ? stock.stock_qty : 0;

                                if (!stock) {
                                    // 新建库存记录
                                    const loc = findEmptyLocation(dbRef.db, ctx.warehouseId);
                                    stock = {
                                        id: dbRef.db.inventory_stock.length + 1,
                                        warehouse_id: ctx.warehouseId,
                                        location_id: loc ? loc.id : null,
                                        product_id: item.id,
                                        material_id: null,
                                        stock_qty: 0,
                                        ai_recommended_safety: 20,
                                        ai_turnover_rate: 2.5,
                                        ai_stock_status: 'normal',
                                        abc_class: 'B',
                                        batch_no: item.batchNo || 'BLC-ZX42-2026L07-' + (100000 + item.id),
                                        life_code_activated_at: new Date().toISOString(),
                                    };
                                    dbRef.db.inventory_stock.push(stock);
                                    if (loc) loc.status = 'occupied';
                                }
                                stock.stock_qty = before + qty;
                                stock.ai_stock_status = stock.stock_qty > 50 ? 'sufficient'
                                    : (stock.stock_qty > 20 ? 'normal' : 'low');
                                ctx.totalQty += qty;
                                ctx.inboundLines.push({
                                    id: item.id, name: item.name, qty, before, after: stock.stock_qty,
                                    location: stock.location_id, aiVerified: true,
                                });
                                ctx.logger.info('阶段3-AI入库', '商品已入库', {
                                    product: item.name, qty, before, after: stock.stock_qty,
                                });
                            }
                            // 写入入库单与库存流水(必须在 commit 前, 确保与库存变更同一事务提交)
                            dbRef.db.inbound_orders.push({
                                id: dbRef.db.inbound_orders.length + 1,
                                order_no: ctx.orderNo,
                                warehouse_id: ctx.warehouseId,
                                total_qty: ctx.totalQty,
                                ai_verification_rate: ctx.aiVerificationRate,
                                status: 'completed',
                                ref_no: ctx.refNo,
                                created_at: new Date().toISOString(),
                            });
                            ctx.inboundLines.forEach(line => {
                                dbRef.db.stock_movements.push({
                                    id: dbRef.db.stock_movements.length + 1,
                                    movement_no: genOrderNo('SM-'),
                                    warehouse_id: ctx.warehouseId,
                                    product_id: line.id,
                                    movement_type: 'inbound',
                                    qty: line.qty,
                                    before_qty: line.before,
                                    after_qty: line.after,
                                    reason: ctx.reason,
                                    ref_no: ctx.refNo || ctx.orderNo,
                                    created_at: new Date().toISOString(),
                                });
                            });
                            ctx.logger.info('阶段3-AI入库', '入库单+库存流水已生成', {
                                orderNo: ctx.orderNo, totalQty: ctx.totalQty, movements: ctx.inboundLines.length,
                            });
                        },
                    },
                    {
                        name: '阶段4-提交事务',
                        action: async (ctx) => {
                            ctx.logger.info('阶段4-事务提交', '准备提交入库事务', {
                                executedStages: ctx.logger.executedStages(),
                            });
                            await ctx.template.adapter.commit(ctx.conn, ctx);
                            ctx.conn = null;
                        },
                    },
                ],
                asyncTasks: [
                    {
                        name: '阶段5-入库通知',
                        action: (ctx) => {
                            ctx.logger.info('阶段5-区块链存证', '入库流水上链完成', {
                                hash: '0x' + Date.now().toString(16),
                            });
                        },
                    },
                ],
            });
        }); // end _withMutex

        const logs = result.logs.map(l => ({ ...l, msg: l.message }));
        if (result.aborted) return { success: false, error: result.reason, logs };
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true, operation: 'inbound',
                details: {
                    totalQty: ctx.totalQty, lines: ctx.inboundLines,
                    aiVerificationRate: ctx.aiVerificationRate, warehouseId: ctx.warehouseId,
                    reason: ctx.reason, refNo: ctx.refNo,
                },
                logs, asyncOps: ['inbound_order', 'stock_movement', 'blockchain_notarize'],
            };
        }
        return { success: false, operation: 'inbound', error: result.error, failedStage: result.failedStage, logs };
    }

    // ============================================================
    //  AI 能力 2: AI智能出库 (POST /api/warehouse/outbound)
    //  波次拣选+路径优化+自动分拣,拣选效率提升50%
    // ============================================================
    async function outbound(params) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) throw new Error('WarehouseService 需要工具包');

        const { items, warehouseId = 1, reason = 'AI智能出库', refNo = null } = params;
        const lockKeys = items.map(i => 'stock:' + warehouseId + ':' + i.id);

        const result = await _withMutex(lockKeys, () => {
            const dbRef = { db: readDB() };
            const adapter = createAdapter(dbRef);
            const template = new Template({ name: 'warehouse_outbound', adapter: adapter });
            return template.run({
                context: {
                    items, warehouseId, reason, refNo,
                    outboundLines: [], totalQty: 0,
                    pickingEfficiencyGain: CONFIG.AI_PICKING_EFFICIENCY_GAIN,
                },
                preflight: async (ctx) => {
                    ctx.logger.info('阶段1-参数校验', '开始校验出库请求', { lineCount: ctx.items.length });
                    if (!ctx.items || ctx.items.length === 0) return { abort: true, reason: '出库清单为空' };
                    for (const item of ctx.items) {
                        const qty = Number(item.qty);
                        if (!item || typeof item.id === 'undefined') return { abort: true, reason: '出库项缺少 id' };
                        if (!Number.isFinite(qty) || qty <= 0) return { abort: true, reason: '出库数量必须>0' };
                    }
                },
                stages: [
                    { name: '阶段2-开启事务', action: async (ctx) => { ctx.conn = await ctx.template.adapter.begin(ctx); } },
                    {
                        name: '阶段3-AI波次拣选与库存扣减',
                        action: async (ctx) => {
                            ensureTables(dbRef.db);
                            ctx.orderNo = genOrderNo('OUT-');
                            for (const item of ctx.items) {
                                const qty = Number(item.qty);
                                const stock = findStock(dbRef.db, ctx.warehouseId, item.id);
                                if (!stock) throw new Error('库存记录不存在: 商品id=' + item.id);
                                if (stock.stock_qty < qty) throw new Error('库存不足: ' + (item.name || item.id) + ' 需要' + qty + '现有' + stock.stock_qty);

                                const before = stock.stock_qty;
                                stock.stock_qty = before - qty;
                                stock.ai_stock_status = stock.stock_qty === 0 ? 'critical'
                                    : (stock.stock_qty <= CONFIG.LOW_STOCK_THRESHOLD ? 'low' : 'normal');
                                ctx.totalQty += qty;
                                ctx.outboundLines.push({
                                    id: item.id, name: item.name, qty, before, after: stock.stock_qty,
                                    location: stock.location_id, wavePicked: true,
                                });
                                ctx.logger.info('阶段3-AI出库', '商品已出库', {
                                    product: item.name, qty, before, after: stock.stock_qty,
                                });
                            }
                            // 写入出库单与库存流水(commit 前, 同一事务)
                            dbRef.db.outbound_orders.push({
                                id: dbRef.db.outbound_orders.length + 1,
                                order_no: ctx.orderNo, warehouse_id: ctx.warehouseId,
                                total_qty: ctx.totalQty, ai_picking_efficiency_gain: ctx.pickingEfficiencyGain,
                                status: 'completed', ref_no: ctx.refNo, created_at: new Date().toISOString(),
                            });
                            ctx.outboundLines.forEach(line => {
                                dbRef.db.stock_movements.push({
                                    id: dbRef.db.stock_movements.length + 1,
                                    movement_no: genOrderNo('SM-'),
                                    warehouse_id: ctx.warehouseId, product_id: line.id,
                                    movement_type: 'outbound', qty: line.qty,
                                    before_qty: line.before, after_qty: line.after,
                                    reason: ctx.reason, ref_no: ctx.refNo || ctx.orderNo,
                                    created_at: new Date().toISOString(),
                                });
                            });
                            ctx.logger.info('阶段3-AI出库', '出库单+流水已生成', { orderNo: ctx.orderNo });
                        },
                    },
                    { name: '阶段4-提交事务', action: async (ctx) => {
                        ctx.logger.info('阶段4-事务提交', '准备提交出库事务', {});
                        await ctx.template.adapter.commit(ctx.conn, ctx); ctx.conn = null;
                    } },
                ],
                asyncTasks: [
                    { name: '阶段5-出库通知', action: (ctx) => {
                        ctx.logger.info('阶段5-出库通知', '出库变更通知已发送', {
                            totalQty: ctx.totalQty, pickingEfficiencyGain: ctx.pickingEfficiencyGain,
                        });
                    } },
                ],
            });
        });

        const logs = result.logs.map(l => ({ ...l, msg: l.message }));
        if (result.aborted) return { success: false, error: result.reason, logs };
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true, operation: 'outbound',
                details: {
                    totalQty: ctx.totalQty, lines: ctx.outboundLines,
                    pickingEfficiencyGain: ctx.pickingEfficiencyGain, warehouseId: ctx.warehouseId,
                },
                logs, asyncOps: ['outbound_order', 'stock_movement'],
            };
        }
        return { success: false, operation: 'outbound', error: result.error, failedStage: result.failedStage, logs };
    }

    // ============================================================
    //  AI 能力 3: AI智能盘点 (POST /api/warehouse/stocktake)
    //  无人机+视觉AI自动盘点,盘点准确率98%
    // ============================================================
    async function stocktake(params) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) throw new Error('WarehouseService 需要工具包');

        const { items, warehouseId = 1, method = 'drone_ai', refNo = null } = params;
        const lockKey = 'wh:' + warehouseId;

        const result = await _withMutex([lockKey], () => {
            const dbRef = { db: readDB() };
            const adapter = createAdapter(dbRef);
            const template = new Template({ name: 'warehouse_stocktake', adapter: adapter });
            return template.run({
                context: {
                    items, warehouseId, method, refNo,
                    diffLines: [], surplusQty: 0, deficitQty: 0,
                    aiAccuracy: CONFIG.AI_STOCKTAKE_ACCURACY,
                },
                preflight: async (ctx) => {
                    ctx.logger.info('阶段1-参数校验', '开始校验盘点请求', { lineCount: ctx.items.length, method: ctx.method });
                    if (!ctx.items || ctx.items.length === 0) return { abort: true, reason: '盘点清单为空' };
                },
                stages: [
                    { name: '阶段2-开启事务', action: async (ctx) => { ctx.conn = await ctx.template.adapter.begin(ctx); } },
                    {
                        name: '阶段3-AI视觉盘点与差异调整',
                        action: async (ctx) => {
                            ensureTables(dbRef.db);
                            ctx.recordNo = genOrderNo('ST-');
                            for (const item of ctx.items) {
                                const actualQty = Number(item.actualQty);
                                const stock = findStock(dbRef.db, ctx.warehouseId, item.id);
                                if (!stock) throw new Error('盘点: 库存记录不存在 商品id=' + item.id);
                                const systemQty = stock.stock_qty;
                                const diff = actualQty - systemQty;
                                if (diff > 0) ctx.surplusQty += diff;
                                else if (diff < 0) ctx.deficitQty += Math.abs(diff);
                                stock.stock_qty = actualQty;
                                stock.ai_stock_status = actualQty === 0 ? 'critical'
                                    : (actualQty <= CONFIG.LOW_STOCK_THRESHOLD ? 'low' : 'normal');
                                ctx.diffLines.push({
                                    id: item.id, name: item.name, systemQty, actualQty, diff,
                                    diffType: diff > 0 ? 'surplus' : (diff < 0 ? 'deficit' : 'match'),
                                });
                                ctx.logger.info('阶段3-AI盘点', '盘点差异已记录', {
                                    product: item.name, systemQty, actualQty, diff,
                                });
                            }
                            // 写入盘点记录(commit 前, 同一事务)
                            dbRef.db.stocktaking_records.push({
                                id: dbRef.db.stocktaking_records.length + 1,
                                record_no: ctx.recordNo, warehouse_id: ctx.warehouseId,
                                method: ctx.method, ai_accuracy: ctx.aiAccuracy,
                                surplus_qty: ctx.surplusQty, deficit_qty: ctx.deficitQty,
                                line_count: ctx.diffLines.length, status: 'completed',
                                ref_no: ctx.refNo, created_at: new Date().toISOString(),
                            });
                            ctx.logger.info('阶段3-AI盘点', '盘点单已生成', { recordNo: ctx.recordNo });
                        },
                    },
                    { name: '阶段4-提交事务', action: async (ctx) => {
                        await ctx.template.adapter.commit(ctx.conn, ctx); ctx.conn = null;
                    } },
                ],
                asyncTasks: [
                    { name: '阶段5-盘点通知', action: (ctx) => {
                        ctx.logger.info('阶段5-盘点通知', '盘点结果通知已发送', {
                            surplusQty: ctx.surplusQty, deficitQty: ctx.deficitQty,
                        });
                    } },
                ],
            });
        });

        const logs = result.logs.map(l => ({ ...l, msg: l.message }));
        if (result.aborted) return { success: false, error: result.reason, logs };
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true, operation: 'stocktake',
                details: {
                    diffLines: ctx.diffLines, surplusQty: ctx.surplusQty, deficitQty: ctx.deficitQty,
                    aiAccuracy: ctx.aiAccuracy, method: ctx.method, warehouseId: ctx.warehouseId,
                },
                logs, asyncOps: ['stocktake_record'],
            };
        }
        return { success: false, operation: 'stocktake', error: result.error, failedStage: result.failedStage, logs };
    }

    // ============================================================
    //  AI 能力 4: AI智能库位优化 (POST /api/warehouse/slot-optimize)
    //  ABC分类+冷热区+高频前置,库位利用率提升30%
    // ============================================================
    async function slotOptimize(params) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) throw new Error('WarehouseService 需要工具包');

        const { warehouseId = 1 } = params;
        const lockKey = 'wh:' + warehouseId + ':slot';

        const result = await _withMutex([lockKey], () => {
            const dbRef = { db: readDB() };
            const adapter = createAdapter(dbRef);
            const template = new Template({ name: 'warehouse_slot_optimize', adapter: adapter });
            return template.run({
                context: {
                    warehouseId,
                    optimizationGain: CONFIG.AI_SLOT_OPTIMIZATION_GAIN,
                    relocatedItems: [], hotZoneCount: 0, warmZoneCount: 0, coldZoneCount: 0,
                },
                preflight: async (ctx) => {
                    ctx.logger.info('阶段1-参数校验', '开始校验库位优化请求', { warehouseId: ctx.warehouseId });
                },
                stages: [
                    { name: '阶段2-开启事务', action: async (ctx) => { ctx.conn = await ctx.template.adapter.begin(ctx); } },
                    {
                        name: '阶段3-AI库位优化分析',
                        action: async (ctx) => {
                            ensureTables(dbRef.db);
                            // 按周转率排序(ABC分类)
                            const stocks = dbRef.db.inventory_stock.filter(s => s.warehouse_id === ctx.warehouseId);
                            stocks.sort((a, b) => b.ai_turnover_rate - a.ai_turnover_rate);
                            const locations = dbRef.db.warehouse_locations.filter(l => l.warehouse_id === ctx.warehouseId);
                            // hot区(A高频)→warm区(B中频)→cold区(C低频)
                            const hotLocs = locations.filter(l => l.zone_type === 'hot');
                            const warmLocs = locations.filter(l => l.zone_type === 'warm');
                            const coldLocs = locations.filter(l => l.zone_type === 'cold');

                            stocks.forEach((stock, idx) => {
                                let targetLoc;
                                let abcClass;
                                if (idx < stocks.length / 3) {
                                    targetLoc = hotLocs[idx % hotLocs.length];
                                    abcClass = 'A'; ctx.hotZoneCount++;
                                } else if (idx < (stocks.length * 2) / 3) {
                                    targetLoc = warmLocs[idx % warmLocs.length];
                                    abcClass = 'B'; ctx.warmZoneCount++;
                                } else {
                                    targetLoc = coldLocs[idx % coldLocs.length];
                                    abcClass = 'C'; ctx.coldZoneCount++;
                                }
                                const oldLocId = stock.location_id;
                                if (targetLoc) {
                                    stock.location_id = targetLoc.id;
                                    stock.abc_class = abcClass;
                                    targetLoc.status = 'occupied';
                                }
                                ctx.relocatedItems.push({
                                    productId: stock.product_id, oldLocId, newLocId: stock.location_id,
                                    abcClass, turnoverRate: stock.ai_turnover_rate,
                                });
                            });
                            ctx.logger.info('阶段3-AI库位优化', '库位重排完成', {
                                totalRelocated: ctx.relocatedItems.length,
                                hot: ctx.hotZoneCount, warm: ctx.warmZoneCount, cold: ctx.coldZoneCount,
                            });
                            // 写入AI优化记录(commit 前, 同一事务)
                            dbRef.db.ai_optimization_records.push({
                                id: dbRef.db.ai_optimization_records.length + 1,
                                optimization_no: genOrderNo('AO-'),
                                warehouse_id: ctx.warehouseId,
                                optimization_type: 'slot_optimization',
                                ai_gain: ctx.optimizationGain,
                                details: { relocated: ctx.relocatedItems.length, hot: ctx.hotZoneCount, warm: ctx.warmZoneCount, cold: ctx.coldZoneCount },
                                created_at: new Date().toISOString(),
                            });
                            ctx.logger.info('阶段3-AI库位优化', 'AI优化记录已生成', { gain: ctx.optimizationGain });
                        },
                    },
                    { name: '阶段4-提交事务', action: async (ctx) => {
                        await ctx.template.adapter.commit(ctx.conn, ctx); ctx.conn = null;
                    } },
                ],
                asyncTasks: [
                    { name: '阶段5-库位优化通知', action: (ctx) => {
                        ctx.logger.info('阶段5-库位优化通知', '库位变更通知已发送', {
                            relocated: ctx.relocatedItems.length,
                        });
                    } },
                ],
            });
        });

        const logs = result.logs.map(l => ({ ...l, msg: l.message }));
        if (result.aborted) return { success: false, error: result.reason, logs };
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true, operation: 'slotOptimize',
                details: {
                    relocatedItems: ctx.relocatedItems, optimizationGain: ctx.optimizationGain,
                    hotZoneCount: ctx.hotZoneCount, warmZoneCount: ctx.warmZoneCount, coldZoneCount: ctx.coldZoneCount,
                    warehouseId: ctx.warehouseId,
                },
                logs, asyncOps: ['ai_optimization_record'],
            };
        }
        return { success: false, operation: 'slotOptimize', error: result.error, failedStage: result.failedStage, logs };
    }

    // ============================================================
    //  AI 能力 5: AI智能库存预测 (GET /api/warehouse/forecast)
    //  季节性+趋势+OEM排程驱动,预测准确率89%
    // ============================================================
    function forecast(params) {
        const { productId, warehouseId = 1, horizonDays = 30 } = params || {};
        const db = readDB();
        ensureTables(db);
        const stock = findStock(db, warehouseId, productId);
        if (!stock) return { success: false, error: '库存记录不存在: productId=' + productId };

        // AI 预测算法(LSTM模拟): 基于周转率+季节性+趋势
        const turnoverRate = stock.ai_turnover_rate || 2.5;
        const currentQty = stock.stock_qty;
        const dailyConsumption = (currentQty * turnoverRate) / 30;
        // 季节性因子(春节前旺季)
        const month = new Date().getMonth() + 1;
        const seasonFactor = (month === 1 || month === 12) ? 1.5 : (month >= 6 && month <= 9 ? 1.2 : 1.0);
        const forecastedDemand = Math.round(dailyConsumption * horizonDays * seasonFactor);
        const aiAccuracy = CONFIG.AI_FORECAST_ACCURACY;
        const daysOfSupply = dailyConsumption > 0 ? Math.round(currentQty / dailyConsumption) : 999;
        const replenishmentSuggested = forecastedDemand > currentQty;

        return {
            success: true, operation: 'forecast',
            details: {
                productId, warehouseId, horizonDays,
                currentQty, forecastedDemand, dailyConsumption,
                seasonFactor, daysOfSupply, replenishmentSuggested,
                aiAccuracy, aiModel: 'LSTM',
            },
        };
    }

    // ============================================================
    //  AI 能力 6: AI智能安全库存 (GET /api/warehouse/safety-stock)
    //  动态安全库存(需求波动+提前期),库存周转提升25%
    // ============================================================
    function safetyStock(params) {
        const { productId, warehouseId = 1 } = params || {};
        const db = readDB();
        ensureTables(db);
        const stock = findStock(db, warehouseId, productId);
        if (!stock) return { success: false, error: '库存记录不存在: productId=' + productId };

        // 动态安全库存算法: SS = Z * σD * √L + Z * D_avg * σL
        // Z=1.65(95%服务水平), σD=需求标准差, L=提前期, σL=提前期标准差
        const Z = 1.65;
        const turnoverRate = stock.ai_turnover_rate || 2.5;
        const currentQty = stock.stock_qty;
        const avgDailyDemand = (currentQty * turnoverRate) / 30;
        const sigmaD = avgDailyDemand * 0.2; // 需求波动20%
        const leadTime = 7; // 提前期7天
        const sigmaL = 1.5; // 提前期波动1.5天
        const safetyStock = Math.round(Z * sigmaD * Math.sqrt(leadTime) + Z * avgDailyDemand * sigmaL);
        const reorderPoint = Math.round(safetyStock + avgDailyDemand * leadTime);
        const turnoverImprovement = CONFIG.AI_TURNOVER_IMPROVEMENT;

        // 更新库存表的 AI 推荐安全库存
        stock.ai_recommended_safety = safetyStock;
        writeDB(db);

        return {
            success: true, operation: 'safetyStock',
            details: {
                productId, warehouseId, currentQty,
                aiRecommendedSafety: safetyStock, reorderPoint,
                avgDailyDemand, leadTime, serviceLevel: '95%',
                turnoverImprovement, aiModel: '动态安全库存模型',
            },
        };
    }

    // ============================================================
    //  AI 能力 7: AI智能温湿度监控 (GET /api/warehouse/env-monitor)
    //  IoT传感+异常预警+酒龄管理,异常发现率95%
    // ============================================================
    function envMonitor(params) {
        const { warehouseId = 1 } = params || {};
        const db = readDB();
        ensureTables(db);
        const wh = db.warehouses.find(w => w.id === warehouseId);
        if (!wh) return { success: false, error: '仓库不存在: id=' + warehouseId };

        // 模拟 IoT 传感器读数
        const temp = 18 + Math.random() * 8; // 18-26℃
        const humidity = 55 + Math.random() * 15; // 55-70%
        const tempAnomaly = temp < CONFIG.TEMP_RANGE[0] || temp > CONFIG.TEMP_RANGE[1];
        const humidityAnomaly = humidity < CONFIG.HUMIDITY_RANGE[0] || humidity > CONFIG.HUMIDITY_RANGE[1];
        const hasAnomaly = tempAnomaly || humidityAnomaly;
        const aiAnomalyDetection = CONFIG.AI_ENV_ANOMALY_DETECTION;

        // 酒龄计算(从 life_code 首次激活日期)
        const stocks = db.inventory_stock.filter(s => s.warehouse_id === warehouseId);
        const agedStocks = stocks.map(s => {
            const activatedAt = new Date(s.life_code_activated_at);
            const ageMonths = (Date.now() - activatedAt.getTime()) / (1000 * 60 * 60 * 24 * 30);
            return { productId: s.product_id, ageMonths: Math.round(ageMonths), eligible: ageMonths >= 36 };
        });

        // 写入监控记录
        db.environment_monitoring.push({
            id: db.environment_monitoring.length + 1,
            warehouse_id: warehouseId,
            temp: Math.round(temp * 10) / 10,
            humidity: Math.round(humidity * 10) / 10,
            has_anomaly: hasAnomaly,
            anomaly_type: tempAnomaly ? 'temp_out_of_range' : (humidityAnomaly ? 'humidity_out_of_range' : null),
            ai_detection_rate: aiAnomalyDetection,
            monitored_at: new Date().toISOString(),
        });
        writeDB(db);

        return {
            success: true, operation: 'envMonitor',
            details: {
                warehouseId, warehouseName: wh.warehouse_name,
                temp: Math.round(temp * 10) / 10, humidity: Math.round(humidity * 10) / 10,
                tempRange: CONFIG.TEMP_RANGE, humidityRange: CONFIG.HUMIDITY_RANGE,
                hasAnomaly, anomalyType: hasAnomaly ? (tempAnomaly ? 'temp' : 'humidity') : null,
                aiAnomalyDetection, agedStocks, monitoringRecords: db.environment_monitoring.length,
            },
        };
    }

    // ============================================================
    //  AI 能力 8: AI智能多仓协同 (POST /api/warehouse/multi-transfer)
    //  工厂仓+区域仓+零售仓调拨,调拨及时率92%
    // ============================================================
    async function multiTransfer(params) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) throw new Error('WarehouseService 需要工具包');

        const { items, fromWarehouseId, toWarehouseId, reason = 'AI智能多仓调拨', refNo = null } = params;
        const lockKeys = [
            'wh:' + fromWarehouseId, 'wh:' + toWarehouseId,
            ...items.map(i => 'stock:' + fromWarehouseId + ':' + i.id),
            ...items.map(i => 'stock:' + toWarehouseId + ':' + i.id),
        ];

        const result = await _withMutex(lockKeys, () => {
            const dbRef = { db: readDB() };
            const adapter = createAdapter(dbRef);
            const template = new Template({ name: 'warehouse_multi_transfer', adapter: adapter });
            return template.run({
                context: {
                    items, fromWarehouseId, toWarehouseId, reason, refNo,
                    transferLines: [], totalQty: 0,
                    transferTimeliness: CONFIG.AI_TRANSFER_TIMELINESS,
                },
                preflight: async (ctx) => {
                    ctx.logger.info('阶段1-参数校验', '开始校验调拨请求', {
                        from: ctx.fromWarehouseId, to: ctx.toWarehouseId, lineCount: ctx.items.length,
                    });
                    if (ctx.fromWarehouseId === ctx.toWarehouseId) return { abort: true, reason: '源仓与目标仓不能相同' };
                    if (!ctx.items || ctx.items.length === 0) return { abort: true, reason: '调拨清单为空' };
                },
                stages: [
                    { name: '阶段2-开启事务', action: async (ctx) => { ctx.conn = await ctx.template.adapter.begin(ctx); } },
                    {
                        name: '阶段3-AI调拨执行(源仓减+目标仓增)',
                        action: async (ctx) => {
                            ensureTables(dbRef.db);
                            const fromWh = dbRef.db.warehouses.find(w => w.id === ctx.fromWarehouseId);
                            const toWh = dbRef.db.warehouses.find(w => w.id === ctx.toWarehouseId);
                            if (!fromWh) throw new Error('源仓库不存在: id=' + ctx.fromWarehouseId);
                            if (!toWh) throw new Error('目标仓库不存在: id=' + ctx.toWarehouseId);

                            for (const item of ctx.items) {
                                const qty = Number(item.qty);
                                const fromStock = findStock(dbRef.db, ctx.fromWarehouseId, item.id);
                                if (!fromStock) throw new Error('源仓库存记录不存在: 商品id=' + item.id);
                                if (fromStock.stock_qty < qty) throw new Error('源仓库存不足: ' + item.name + ' 需要' + qty + '现有' + fromStock.stock_qty);

                                // 源仓扣减
                                const fromBefore = fromStock.stock_qty;
                                fromStock.stock_qty = fromBefore - qty;
                                fromStock.ai_stock_status = fromStock.stock_qty <= CONFIG.LOW_STOCK_THRESHOLD ? 'low' : 'normal';

                                // 目标仓增加
                                let toStock = findStock(dbRef.db, ctx.toWarehouseId, item.id);
                                const toBefore = toStock ? toStock.stock_qty : 0;
                                if (!toStock) {
                                    const loc = findEmptyLocation(dbRef.db, ctx.toWarehouseId);
                                    toStock = {
                                        id: dbRef.db.inventory_stock.length + 1,
                                        warehouse_id: ctx.toWarehouseId,
                                        location_id: loc ? loc.id : null,
                                        product_id: item.id, material_id: null, stock_qty: 0,
                                        ai_recommended_safety: 20, ai_turnover_rate: 2.5,
                                        ai_stock_status: 'normal', abc_class: 'B',
                                        batch_no: fromStock.batch_no,
                                        life_code_activated_at: fromStock.life_code_activated_at,
                                    };
                                    dbRef.db.inventory_stock.push(toStock);
                                    if (loc) loc.status = 'occupied';
                                }
                                toStock.stock_qty = toBefore + qty;
                                ctx.totalQty += qty;
                                ctx.transferLines.push({
                                    id: item.id, name: item.name, qty,
                                    fromBefore, fromAfter: fromStock.stock_qty,
                                    toBefore, toAfter: toStock.stock_qty,
                                });
                                ctx.logger.info('阶段3-AI调拨', '商品已调拨', {
                                    product: item.name, qty, from: fromWh.warehouse_name, to: toWh.warehouse_name,
                                });
                            }
                            // 写入调拨单与双向流水(commit 前, 同一事务)
                            ctx.transferNo = genOrderNo('TR-');
                            dbRef.db.multi_warehouse_transfers.push({
                                id: dbRef.db.multi_warehouse_transfers.length + 1,
                                transfer_no: ctx.transferNo, from_warehouse_id: ctx.fromWarehouseId,
                                to_warehouse_id: ctx.toWarehouseId, total_qty: ctx.totalQty,
                                ai_timeliness: ctx.transferTimeliness, status: 'completed',
                                ref_no: ctx.refNo, created_at: new Date().toISOString(),
                            });
                            ctx.transferLines.forEach(line => {
                                dbRef.db.stock_movements.push({
                                    id: dbRef.db.stock_movements.length + 1,
                                    movement_no: genOrderNo('SM-'),
                                    warehouse_id: ctx.fromWarehouseId, product_id: line.id,
                                    movement_type: 'transfer_out', qty: line.qty,
                                    before_qty: line.fromBefore, after_qty: line.fromAfter,
                                    reason: ctx.reason, ref_no: ctx.refNo || ctx.transferNo,
                                    created_at: new Date().toISOString(),
                                });
                                dbRef.db.stock_movements.push({
                                    id: dbRef.db.stock_movements.length + 1,
                                    movement_no: genOrderNo('SM-'),
                                    warehouse_id: ctx.toWarehouseId, product_id: line.id,
                                    movement_type: 'transfer_in', qty: line.qty,
                                    before_qty: line.toBefore, after_qty: line.toAfter,
                                    reason: ctx.reason, ref_no: ctx.refNo || ctx.transferNo,
                                    created_at: new Date().toISOString(),
                                });
                            });
                            ctx.logger.info('阶段3-AI调拨', '调拨单+双向流水已生成', { transferNo: ctx.transferNo });
                        },
                    },
                    { name: '阶段4-提交事务', action: async (ctx) => {
                        await ctx.template.adapter.commit(ctx.conn, ctx); ctx.conn = null;
                    } },
                ],
                asyncTasks: [
                    { name: '阶段5-调拨通知', action: (ctx) => {
                        ctx.logger.info('阶段5-调拨通知', '调拨完成通知已发送', {
                            totalQty: ctx.totalQty, transferTimeliness: ctx.transferTimeliness,
                        });
                    } },
                ],
            });
        });

        const logs = result.logs.map(l => ({ ...l, msg: l.message }));
        if (result.aborted) return { success: false, error: result.reason, logs };
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true, operation: 'multiTransfer',
                details: {
                    transferLines: ctx.transferLines, totalQty: ctx.totalQty,
                    transferTimeliness: ctx.transferTimeliness,
                    fromWarehouseId: ctx.fromWarehouseId, toWarehouseId: ctx.toWarehouseId,
                },
                logs, asyncOps: ['transfer_order', 'stock_movement'],
            };
        }
        return { success: false, operation: 'multiTransfer', error: result.error, failedStage: result.failedStage, logs };
    }

    // ============================================================
    //  AI 能力 9: AI智能损耗管理 (POST /api/warehouse/loss)
    //  蒸发/破损/品质降级追踪+根因分析,损耗降低20%
    // ============================================================
    async function loss(params) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) throw new Error('WarehouseService 需要工具包');

        const { items, warehouseId = 1, lossType = 'evaporation', refNo = null } = params;
        const lockKeys = items.map(i => 'stock:' + warehouseId + ':' + i.id);

        const result = await _withMutex(lockKeys, () => {
            const dbRef = { db: readDB() };
            const adapter = createAdapter(dbRef);
            const template = new Template({ name: 'warehouse_loss', adapter: adapter });
            return template.run({
                context: {
                    items, warehouseId, lossType, refNo,
                    lossLines: [], totalLossQty: 0,
                    lossReduction: CONFIG.AI_LOSS_REDUCTION,
                },
                preflight: async (ctx) => {
                    ctx.logger.info('阶段1-参数校验', '开始校验损耗请求', { lineCount: ctx.items.length, lossType: ctx.lossType });
                    if (!ctx.items || ctx.items.length === 0) return { abort: true, reason: '损耗清单为空' };
                    const validTypes = ['evaporation', 'breakage', 'quality_downgrade', 'expired', 'missing'];
                    if (!validTypes.includes(ctx.lossType)) return { abort: true, reason: '损耗类型无效: ' + ctx.lossType };
                },
                stages: [
                    { name: '阶段2-开启事务', action: async (ctx) => { ctx.conn = await ctx.template.adapter.begin(ctx); } },
                    {
                        name: '阶段3-AI损耗登记与库存调整',
                        action: async (ctx) => {
                            ensureTables(dbRef.db);
                            ctx.lossNo = genOrderNo('LS-');
                            for (const item of ctx.items) {
                                const qty = Number(item.qty);
                                const stock = findStock(dbRef.db, ctx.warehouseId, item.id);
                                if (!stock) throw new Error('损耗: 库存记录不存在 商品id=' + item.id);
                                if (stock.stock_qty < qty) throw new Error('库存不足无法登记损耗: ' + item.name);

                                const before = stock.stock_qty;
                                stock.stock_qty = before - qty;
                                stock.ai_stock_status = stock.stock_qty === 0 ? 'critical' : 'normal';
                                ctx.totalLossQty += qty;
                                ctx.lossLines.push({
                                    id: item.id, name: item.name, qty, before, after: stock.stock_qty,
                                    lossType: ctx.lossType, rootCause: item.rootCause || 'AI根因分析中',
                                });
                                ctx.logger.info('阶段3-AI损耗', '损耗已登记', {
                                    product: item.name, qty, lossType: ctx.lossType,
                                });
                            }
                            // 写入损耗记录与根因分析(commit 前, 同一事务)
                            dbRef.db.loss_records.push({
                                id: dbRef.db.loss_records.length + 1,
                                loss_no: ctx.lossNo, warehouse_id: ctx.warehouseId,
                                loss_type: ctx.lossType, total_qty: ctx.totalLossQty,
                                ai_reduction_rate: ctx.lossReduction, ai_root_cause: 'AI根因分析完成',
                                line_count: ctx.lossLines.length, status: 'completed',
                                ref_no: ctx.refNo, created_at: new Date().toISOString(),
                            });
                            ctx.logger.info('阶段3-AI损耗', '损耗单+根因分析已生成', { lossNo: ctx.lossNo });
                        },
                    },
                    { name: '阶段4-提交事务', action: async (ctx) => {
                        await ctx.template.adapter.commit(ctx.conn, ctx); ctx.conn = null;
                    } },
                ],
                asyncTasks: [
                    { name: '阶段5-损耗通知', action: (ctx) => {
                        ctx.logger.info('阶段5-损耗通知', '损耗登记通知已发送', {
                            totalLossQty: ctx.totalLossQty, lossReduction: ctx.lossReduction,
                        });
                    } },
                ],
            });
        });

        const logs = result.logs.map(l => ({ ...l, msg: l.message }));
        if (result.aborted) return { success: false, error: result.reason, logs };
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true, operation: 'loss',
                details: {
                    lossLines: ctx.lossLines, totalLossQty: ctx.totalLossQty,
                    lossReduction: ctx.lossReduction, lossType: ctx.lossType, warehouseId: ctx.warehouseId,
                },
                logs, asyncOps: ['loss_record', 'ai_root_cause'],
            };
        }
        return { success: false, operation: 'loss', error: result.error, failedStage: result.failedStage, logs };
    }

    // ============================================================
    //  AI 能力 10: AI智能仓配一体 (POST /api/warehouse/cross-dock)
    //  仓→配无缝衔接+越库作业,越库率40%
    // ============================================================
    async function crossDock(params) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) throw new Error('WarehouseService 需要工具包');

        const { items, warehouseId = 1, carrierId = 'LOGISTICS-06', refNo = null } = params;
        const lockKey = 'wh:' + warehouseId;

        const result = await _withMutex([lockKey], () => {
            const dbRef = { db: readDB() };
            const adapter = createAdapter(dbRef);
            const template = new Template({ name: 'warehouse_cross_dock', adapter: adapter });
            return template.run({
                context: {
                    items, warehouseId, carrierId, refNo,
                    crossDockLines: [], totalQty: 0,
                    crossDockRate: CONFIG.AI_CROSS_DOCK_RATE,
                },
                preflight: async (ctx) => {
                    ctx.logger.info('阶段1-参数校验', '开始校验越库请求', { lineCount: ctx.items.length, carrierId: ctx.carrierId });
                    if (!ctx.items || ctx.items.length === 0) return { abort: true, reason: '越库清单为空' };
                },
                stages: [
                    { name: '阶段2-开启事务', action: async (ctx) => { ctx.conn = await ctx.template.adapter.begin(ctx); } },
                    {
                        name: '阶段3-AI越库作业(不入库直接分发)',
                        action: async (ctx) => {
                            ensureTables(dbRef.db);
                            const wh = dbRef.db.warehouses.find(w => w.id === ctx.warehouseId);
                            if (!wh) throw new Error('仓库不存在: id=' + ctx.warehouseId);
                            ctx.dockNo = genOrderNo('CD-');
                            for (const item of ctx.items) {
                                const qty = Number(item.qty);
                                // 越库: 入库即出库,不增加库存(写入流水但库存不变)
                                ctx.totalQty += qty;
                                ctx.crossDockLines.push({
                                    id: item.id, name: item.name, qty,
                                    inboundVehicle: item.inboundVehicle || 'IV-' + Date.now(),
                                    outboundVehicle: 'OV-' + Date.now() + '-' + Math.floor(Math.random() * 100),
                                    crossDocked: true,
                                });
                                ctx.logger.info('阶段3-AI越库', '商品越库完成', {
                                    product: item.name, qty, warehouse: wh.warehouse_name,
                                });
                            }
                            // 写入越库流水(commit 前, 同一事务)
                            ctx.crossDockLines.forEach(line => {
                                dbRef.db.stock_movements.push({
                                    id: dbRef.db.stock_movements.length + 1,
                                    movement_no: genOrderNo('SM-'),
                                    warehouse_id: ctx.warehouseId, product_id: line.id,
                                    movement_type: 'cross_dock', qty: line.qty,
                                    before_qty: 0, after_qty: 0,
                                    reason: 'AI智能仓配一体越库', ref_no: ctx.refNo || ctx.dockNo,
                                    carrier_id: ctx.carrierId, created_at: new Date().toISOString(),
                                });
                            });
                            ctx.logger.info('阶段3-AI越库', '越库流水已生成', {
                                dockNo: ctx.dockNo, crossDockRate: ctx.crossDockRate, carrierId: ctx.carrierId,
                            });
                        },
                    },
                    { name: '阶段4-提交事务', action: async (ctx) => {
                        await ctx.template.adapter.commit(ctx.conn, ctx); ctx.conn = null;
                    } },
                ],
                asyncTasks: [
                    { name: '阶段5-越库通知与物流对接', action: (ctx) => {
                        ctx.logger.info('阶段5-越库通知', '越库完成通知+物流对接已发送', {
                            dockNo: ctx.dockNo, carrierId: ctx.carrierId, crossDockRate: ctx.crossDockRate,
                        });
                    } },
                ],
            });
        });

        const logs = result.logs.map(l => ({ ...l, msg: l.message }));
        if (result.aborted) return { success: false, error: result.reason, logs };
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true, operation: 'crossDock',
                details: {
                    crossDockLines: ctx.crossDockLines, totalQty: ctx.totalQty,
                    crossDockRate: ctx.crossDockRate, carrierId: ctx.carrierId, warehouseId: ctx.warehouseId,
                },
                logs, asyncOps: ['cross_dock_movement', 'logistics_integration'],
            };
        }
        return { success: false, operation: 'crossDock', error: result.error, failedStage: result.failedStage, logs };
    }

    // ---------- Live: 调用后端 API ----------
    async function liveCall(endpoint, params, method) {
        const r = await EnvAdapter.request({
            url: apiBase + endpoint,
            method: method || 'POST',
            data: params,
            header: { 'Content-Type': 'application/json' },
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
    }

    // ---------- 公共 API ----------
    return {
        CONFIG: CONFIG,
        init() {
            if (!EnvAdapter.storage.get(STORAGE_KEY)) initMockDB(true);
        },
        setMode(m) { mode = m; return this; },
        setApiBase(base) { apiBase = base; return this; },
        getMode() { return mode; },
        resetMock() {
            EnvAdapter.storage.remove(STORAGE_KEY);
            initMockDB(true);
            // 清空所有锁状态(优先用 Mutex 类,兜底用 FIFO 队列,与 inventory-service.js 一致)
            if (_mutex) _mutex.clear();
            Object.keys(_mutexLocked).forEach(k => { delete _mutexLocked[k]; });
            Object.keys(_mutexQueues).forEach(k => { delete _mutexQueues[k]; });
            return this;
        },
        getMockDB() { return readDB(); },

        // ===== 10 个 AI 能力 API =====
        // 1. AI智能入库
        async inbound(params) {
            if (mode === 'live') {
                try { return await liveCall('/inbound', params, 'POST'); }
                catch (e) { return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] }; }
            }
            return await inbound(params);
        },
        // 2. AI智能出库
        async outbound(params) {
            if (mode === 'live') {
                try { return await liveCall('/outbound', params, 'POST'); }
                catch (e) { return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] }; }
            }
            return await outbound(params);
        },
        // 3. AI智能盘点
        async stocktake(params) {
            if (mode === 'live') {
                try { return await liveCall('/stocktake', params, 'POST'); }
                catch (e) { return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] }; }
            }
            return await stocktake(params);
        },
        // 4. AI智能库位优化
        async slotOptimize(params) {
            if (mode === 'live') {
                try { return await liveCall('/slot-optimize', params, 'POST'); }
                catch (e) { return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] }; }
            }
            return await slotOptimize(params);
        },
        // 5. AI智能库存预测(同步,读操作)
        forecast(params) {
            if (mode === 'live') {
                // live 模式下 forecast 也走异步 API,这里返回 Promise
                return liveCall('/forecast', params, 'GET').catch(e => ({ success: false, error: e.message }));
            }
            return forecast(params);
        },
        // 6. AI智能安全库存(同步,读操作)
        safetyStock(params) {
            if (mode === 'live') {
                return liveCall('/safety-stock', params, 'GET').catch(e => ({ success: false, error: e.message }));
            }
            return safetyStock(params);
        },
        // 7. AI智能温湿度监控(同步,读操作)
        envMonitor(params) {
            if (mode === 'live') {
                return liveCall('/env-monitor', params, 'GET').catch(e => ({ success: false, error: e.message }));
            }
            return envMonitor(params);
        },
        // 8. AI智能多仓协同
        async multiTransfer(params) {
            if (mode === 'live') {
                try { return await liveCall('/multi-transfer', params, 'POST'); }
                catch (e) { return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] }; }
            }
            return await multiTransfer(params);
        },
        // 9. AI智能损耗管理
        async loss(params) {
            if (mode === 'live') {
                try { return await liveCall('/loss', params, 'POST'); }
                catch (e) { return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] }; }
            }
            return await loss(params);
        },
        // 10. AI智能仓配一体
        async crossDock(params) {
            if (mode === 'live') {
                try { return await liveCall('/cross-dock', params, 'POST'); }
                catch (e) { return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] }; }
            }
            return await crossDock(params);
        },
    };
})();

// 暴露到 window 全局
if (typeof window !== 'undefined') {
    window.WarehouseService = WarehouseService;
}
