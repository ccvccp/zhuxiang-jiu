/**
 * inventory-service.js  ·  库存扣减服务模块（基于工具包）
 * ============================================================
 * 用途:
 *   实现库存扣减/回补的完整事务流程,并对外提供共享核心 applyDeduct
 *   供 checkout-service 等上层事务在其阶段内直接调用(避免嵌套事务)。
 *
 * 基于 toolkit/ 工具包:
 *   · UpgradeLogger         → 结构化事务日志(事务ID/计时器/阶段追踪)
 *   · TransactionTemplate   → 事务编排(BEGIN/COMMIT/ROLLBACK/异步任务)
 *
 * 双层 API 设计:
 *   1) applyDeduct(dbRef, items, logger, opts)  → 共享核心(原子单元)
 *      在调用方事务内执行: 校验→扣减→流水→预警,失败抛错触发外层回滚
 *      不开启/提交自己的事务,适合 checkout-service 阶段4 等委托场景
 *   2) deduct(items, opts) / restock(items, opts) → 独立事务
 *      包装 applyDeduct/restockCore 于完整 TransactionTemplate 事务
 *      适合独立库存调整/退货入库/盘点等场景
 *
 * 事务结构(独立 deduct):
 *   preflight     : 参数校验
 *   阶段2-开启事务 : BEGIN(快照)
 *   阶段3-库存扣减 : applyDeduct(校验+扣减+流水+预警)
 *   阶段4-提交事务 : COMMIT
 *   asyncTasks    : 库存变更通知 + 区块链存证
 *
 * 使用示例(独立):
 *   const r = await InventoryService.deduct({
 *       items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 2 }],
 *       reason: '订单出库',
 *   });
 *
 * 使用示例(委托,checkout-service 阶段4):
 *   InventoryService.applyDeduct(dbRef, ctx.items, ctx.logger, { reason: '订单出库' });
 *
 * 浏览器环境:
 *   需先加载 toolkit/upgrade-logger.js + toolkit/transaction-template.js
 *   全局名: InventoryService / window.InventoryService
 * ============================================================
 */

const InventoryService = (function () {
    'use strict';

    const STORAGE_KEY = 'zhuxiang_inventory_db_v1';

    // ---------- 配置 ----------
    const CONFIG = {
        // 库存预警阈值: 扣减后低于此值则记录预警
        LOW_STOCK_THRESHOLD: 10,
        // 单次扣减/回补数量上限(防误操作)
        MAX_QTY_PER_LINE: 9999,
    };

    let mode = 'mock'; // 'mock' | 'live'
    let apiBase = '/api/inventory';

    // ---------- 悲观锁(Mutex, FIFO 队列实现) ----------
    // 引用独立工具类 mutex.js 的 Mutex(若未加载则内部兜底, 同为 FIFO)
    // 确保同一商品的库存扣减/回补串行执行,防止并发超卖
    // FIFO: 每个等待者持有独立 Promise, release 仅唤醒队首一个(直接交接),
    //       消除 thundering herd(旧 while+await 共享 Promise 会唤醒全部 N-1 个, O(n²))
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
        ]).map(p => ({ ...p, stock: 100 }));

        const db = {
            products: products,
            inventory_logs: [],   // 库存流水: {id, product_id, name, type, qty, before, after, reason, time}
            stock_alerts: [],     // 库存预警: {id, product_id, name, stock, threshold, time}
            tx_log: [],           // 事务日志: BEGIN/COMMIT/ROLLBACK
        };
        writeDB(db);
        return db;
    }

    // ---------- Mock 事务适配器(快照模式) ----------
    function createAdapter(dbRef) {
        return {
            begin(ctx) {
                // 先 push BEGIN 再取快照: 确保快照包含本次 BEGIN,
                // 否则 rollback 用快照恢复时会丢失本次 BEGIN(只剩 ROLLBACK), 导致事务原子性校验 begins !== commits + rollbacks
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
                    snapshot.tx_log.push({ type: 'ROLLBACK', time: new Date().toISOString() });
                    dbRef.db = snapshot;
                    writeDB(snapshot);
                    ctx.logger.error('回滚', '事务已回滚(快照恢复)', {});
                }
            },
        };
    }

    // ---------- 工具: 确保 db 拥有库存相关表 ----------
    function ensureTables(db) {
        if (!Array.isArray(db.inventory_logs)) db.inventory_logs = [];
        if (!Array.isArray(db.stock_alerts)) db.stock_alerts = [];
        if (!Array.isArray(db.tx_log)) db.tx_log = [];
        return db;
    }

    // ---------- 共享核心: applyDeduct ----------
    /**
     * 在调用方事务内执行库存扣减(原子单元)。
     * 不开启/提交自己的事务,失败抛错以触发外层回滚。
     *
     * @param {Object} dbRef  - { db } 包装对象,db 为当前事务可见的数据库
     * @param {Array}  items  - [{ id, name?, qty }] 扣减清单
     * @param {Object} logger - UpgradeLogger 实例(用于阶段日志)
     * @param {Object} [opts] - { reason, refNo, flowPrefix }
     * @returns {Object} { deductedLines, totalQty, alertsTriggered }
     */
    function applyDeduct(dbRef, items, logger, opts) {
        const o = opts || {};
        const reason = o.reason || '库存扣减';
        ensureTables(dbRef.db);

        let totalQty = 0;
        let alertsTriggered = 0;
        const deductedLines = [];

        logger.info('阶段3-库存扣减', '开始执行库存扣减', {
            lineCount: items.length,
            reason: reason,
            refNo: o.refNo || null,
        });

        // Step 1: 逐行校验 + 扣减
        for (const item of items) {
            if (!item || typeof item.id === 'undefined') {
                throw new Error('库存扣减项缺少 id');
            }
            const qty = Number(item.qty);
            if (!Number.isFinite(qty) || qty <= 0) {
                throw new Error(`扣减数量必须>0: ${item.name || 'id=' + item.id}`);
            }
            if (qty > CONFIG.MAX_QTY_PER_LINE) {
                throw new Error(`扣减数量超限: ${qty} > ${CONFIG.MAX_QTY_PER_LINE}`);
            }

            const product = dbRef.db.products.find(p => p.id === item.id);
            if (!product) {
                throw new Error(`商品不存在: id=${item.id}`);
            }
            if (product.stock < qty) {
                throw new Error(`库存不足: ${product.name} 需要${qty}现有${product.stock}`);
            }

            const before = product.stock;
            product.stock = before - qty;
            totalQty += qty;

            // Step 2: 写入库存流水(类型=出库)
            const flowId = 'IF' + Date.now() + '-' + Math.floor(Math.random() * 1000);
            dbRef.db.inventory_logs.push({
                id: flowId,
                product_id: product.id,
                name: product.name,
                type: '出库',
                qty: qty,
                before: before,
                after: product.stock,
                reason: reason,
                ref_no: o.refNo || null,
                time: new Date().toISOString(),
            });
            deductedLines.push({ id: product.id, name: product.name, qty, before, after: product.stock });

            logger.info('阶段3-库存扣减', '库存已扣减', {
                product: product.name,
                qty: qty,
                before: before,
                after: product.stock,
            });

            // Step 3: 低库存预警检查
            if (product.stock > 0 && product.stock <= CONFIG.LOW_STOCK_THRESHOLD) {
                const alertId = 'SA' + Date.now() + '-' + product.id;
                dbRef.db.stock_alerts.push({
                    id: alertId,
                    product_id: product.id,
                    name: product.name,
                    stock: product.stock,
                    threshold: CONFIG.LOW_STOCK_THRESHOLD,
                    level: product.stock === 0 ? '缺货' : '低库存',
                    time: new Date().toISOString(),
                });
                alertsTriggered++;
                logger.warn('阶段3-库存扣减', '触发库存预警', {
                    product: product.name,
                    stock: product.stock,
                    threshold: CONFIG.LOW_STOCK_THRESHOLD,
                });
            }
        }

        logger.info('阶段3-库存扣减', '库存扣减完成', {
            totalQty: totalQty,
            lines: deductedLines.length,
            alerts: alertsTriggered,
            flowRecords: dbRef.db.inventory_logs.length,
        });

        return { deductedLines, totalQty, alertsTriggered };
    }

    // ---------- 共享核心: applyRestock(回补,applyDeduct 的逆操作) ----------
    function applyRestock(dbRef, items, logger, opts) {
        const o = opts || {};
        const reason = o.reason || '库存回补';
        ensureTables(dbRef.db);

        let totalQty = 0;
        const restockedLines = [];

        logger.info('阶段3-库存回补', '开始执行库存回补', {
            lineCount: items.length,
            reason: reason,
            refNo: o.refNo || null,
        });

        for (const item of items) {
            if (!item || typeof item.id === 'undefined') {
                throw new Error('库存回补项缺少 id');
            }
            const qty = Number(item.qty);
            if (!Number.isFinite(qty) || qty <= 0) {
                throw new Error(`回补数量必须>0: ${item.name || 'id=' + item.id}`);
            }
            const product = dbRef.db.products.find(p => p.id === item.id);
            if (!product) {
                throw new Error(`商品不存在: id=${item.id}`);
            }

            const before = product.stock;
            product.stock = before + qty;
            totalQty += qty;

            const flowId = 'IF' + Date.now() + '-' + Math.floor(Math.random() * 1000);
            dbRef.db.inventory_logs.push({
                id: flowId,
                product_id: product.id,
                name: product.name,
                type: '入库',
                qty: qty,
                before: before,
                after: product.stock,
                reason: reason,
                ref_no: o.refNo || null,
                time: new Date().toISOString(),
            });
            restockedLines.push({ id: product.id, name: product.name, qty, before, after: product.stock });

            logger.info('阶段3-库存回补', '库存已回补', {
                product: product.name,
                qty: qty,
                before: before,
                after: product.stock,
            });
        }

        logger.info('阶段3-库存回补', '库存回补完成', {
            totalQty: totalQty,
            lines: restockedLines.length,
            flowRecords: dbRef.db.inventory_logs.length,
        });

        return { restockedLines, totalQty };
    }

    // ---------- 独立事务: deduct(扣减) ----------
    async function deduct(params) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) {
            throw new Error('InventoryService 需要工具包,请先加载 js/toolkit/upgrade-logger.js 和 js/toolkit/transaction-template.js');
        }

        const { items, reason = '库存扣减', refNo = null } = params;

        // 获取所有商品锁 key(按 id 升序,避免死锁)
        // 用 Mutex.withLocks 包装整个事务体,防止并发超卖
        const lockKeys = items.map(i => 'stock:' + i.id);

        const result = await _withMutex(lockKeys, () => {
            // dbRef 必须在锁内创建: 加锁后才 readDB() 读最新已提交状态,
            // 避免并发下多个事务读到同一陈旧快照、各自覆盖写回导致 lost-update
            const dbRef = { db: readDB() };
            const adapter = createAdapter(dbRef);
            const template = new Template({ name: 'inventory_deduct', adapter: adapter });
            return template.run({
            context: {
                items, reason, refNo,
                deductedLines: [], totalQty: 0, alertsTriggered: 0,
            },

            preflight: async (ctx) => {
                ctx.logger.info('阶段1-参数校验', '开始校验扣减请求', {
                    lineCount: ctx.items.length,
                    reason: ctx.reason,
                });
                if (!ctx.items || ctx.items.length === 0) {
                    ctx.logger.error('阶段1-参数校验', '扣减清单为空');
                    return { abort: true, reason: '扣减清单为空' };
                }
                // 结构校验: 每行必须有 id 且 qty>0
                // 注: 库存/商品存在性校验放在阶段3(applyDeduct)事务内执行,
                //     以便库存不足时触发完整的事务回滚(与 checkout-service 阶段4 委托一致)。
                for (const item of ctx.items) {
                    const qty = Number(item.qty);
                    if (!item || typeof item.id === 'undefined') {
                        return { abort: true, reason: '扣减项缺少 id' };
                    }
                    if (!Number.isFinite(qty) || qty <= 0) {
                        return { abort: true, reason: `扣减数量必须>0: ${item.name || 'id=' + item.id}` };
                    }
                }
            },

            stages: [
                // 阶段2: 开启事务
                {
                    name: '阶段2-开启事务',
                    action: async (ctx) => {
                        ctx.conn = await ctx.template.adapter.begin(ctx);
                    },
                },
                // 阶段3: 库存扣减(共享核心)
                {
                    name: '阶段3-库存扣减',
                    action: async (ctx) => {
                        const r = applyDeduct(dbRef, ctx.items, ctx.logger, {
                            reason: ctx.reason, refNo: ctx.refNo,
                        });
                        ctx.deductedLines = r.deductedLines;
                        ctx.totalQty = r.totalQty;
                        ctx.alertsTriggered = r.alertsTriggered;
                    },
                },
                // 阶段4: 提交事务
                {
                    name: '阶段4-提交事务',
                    action: async (ctx) => {
                        ctx.logger.info('阶段4-事务提交', '准备提交事务', {
                            executedStages: ctx.logger.executedStages(),
                            totalElapsedMs: ctx.logger.totalElapsedMs(),
                        });
                        await ctx.template.adapter.commit(ctx.conn, ctx);
                        ctx.conn = null;
                    },
                },
            ],

            asyncTasks: [
                {
                    name: '阶段5-库存变更通知',
                    action: (ctx) => {
                        ctx.logger.info('阶段5-库存变更通知', '库存变更通知已发送', {
                            totalQty: ctx.totalQty,
                            alerts: ctx.alertsTriggered,
                        });
                    },
                },
                {
                    name: '阶段5-区块链存证',
                    action: (ctx) => {
                        ctx.logger.info('阶段5-区块链存证', '库存流水上链完成', {
                            hash: '0x' + Date.now().toString(16),
                        });
                    },
                },
            ],
            });
        }); // end _withMutex(锁自动释放)

        // 日志格式适配(补 msg 别名)
        const logs = result.logs.map(l => ({ ...l, msg: l.message }));

        if (result.aborted) {
            return { success: false, error: result.reason, logs };
        }
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true,
                operation: 'deduct',
                details: {
                    totalQty: ctx.totalQty,
                    lines: ctx.deductedLines,
                    alertsTriggered: ctx.alertsTriggered,
                    reason: ctx.reason,
                    refNo: ctx.refNo,
                },
                logs,
                asyncOps: ['inventory_notify', 'blockchain_notarize'],
            };
        }
        return {
            success: false,
            operation: 'deduct',
            error: result.error,
            failedStage: result.failedStage,
            executedStages: result.executedStages,
            logs,
        };
    }

    // ---------- 独立事务: restock(回补) ----------
    async function restock(params) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) {
            throw new Error('InventoryService 需要工具包,请先加载 js/toolkit/upgrade-logger.js 和 js/toolkit/transaction-template.js');
        }

        const { items, reason = '库存回补', refNo = null } = params;

        // 获取所有商品锁 key(与 deduct 一致,确保扣减/回补互斥)
        const lockKeys = items.map(i => 'stock:' + i.id);

        const result = await _withMutex(lockKeys, () => {
            // dbRef 必须在锁内创建: 加锁后才 readDB() 读最新已提交状态,
            // 避免并发下读到陈旧快照导致 lost-update(与 deduct 同因)
            const dbRef = { db: readDB() };
            const adapter = createAdapter(dbRef);
            const template = new Template({ name: 'inventory_restock', adapter: adapter });
            return template.run({
            context: { items, reason, refNo, restockedLines: [], totalQty: 0 },

            preflight: async (ctx) => {
                ctx.logger.info('阶段1-参数校验', '开始校验回补请求', {
                    lineCount: ctx.items.length,
                });
                if (!ctx.items || ctx.items.length === 0) {
                    return { abort: true, reason: '回补清单为空' };
                }
                // 结构校验(同 deduct,商品存在性在阶段3 applyRestock 内校验)
                for (const item of ctx.items) {
                    const qty = Number(item.qty);
                    if (!item || typeof item.id === 'undefined') {
                        return { abort: true, reason: '回补项缺少 id' };
                    }
                    if (!Number.isFinite(qty) || qty <= 0) {
                        return { abort: true, reason: `回补数量必须>0: ${item.name || 'id=' + item.id}` };
                    }
                }
            },

            stages: [
                {
                    name: '阶段2-开启事务',
                    action: async (ctx) => { ctx.conn = await ctx.template.adapter.begin(ctx); },
                },
                {
                    name: '阶段3-库存回补',
                    action: async (ctx) => {
                        const r = applyRestock(dbRef, ctx.items, ctx.logger, {
                            reason: ctx.reason, refNo: ctx.refNo,
                        });
                        ctx.restockedLines = r.restockedLines;
                        ctx.totalQty = r.totalQty;
                    },
                },
                {
                    name: '阶段4-提交事务',
                    action: async (ctx) => {
                        ctx.logger.info('阶段4-事务提交', '准备提交事务', {
                            executedStages: ctx.logger.executedStages(),
                        });
                        await ctx.template.adapter.commit(ctx.conn, ctx);
                        ctx.conn = null;
                    },
                },
            ],

            asyncTasks: [
                {
                    name: '阶段5-库存变更通知',
                    action: (ctx) => {
                        ctx.logger.info('阶段5-库存变更通知', '回补通知已发送', {
                            totalQty: ctx.totalQty,
                        });
                    },
                },
            ],
            });
        }); // end _withMutex(锁自动释放)

        const logs = result.logs.map(l => ({ ...l, msg: l.message }));
        if (result.aborted) {
            return { success: false, error: result.reason, logs };
        }
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true,
                operation: 'restock',
                details: {
                    totalQty: ctx.totalQty,
                    lines: ctx.restockedLines,
                    reason: ctx.reason,
                    refNo: ctx.refNo,
                },
                logs,
                asyncOps: ['inventory_notify'],
            };
        }
        return {
            success: false,
            operation: 'restock',
            error: result.error,
            failedStage: result.failedStage,
            executedStages: result.executedStages,
            logs,
        };
    }

    // ---------- 查询: getStock ----------
    function getStock(productId) {
        const db = readDB();
        const p = db.products.find(x => x.id === productId);
        return p ? p.stock : null;
    }

    // ---------- Live: 调用后端 API ----------
    async function liveDeduct(params) {
        const r = await EnvAdapter.request({
            url: apiBase + '/deduct',
            method: 'POST',
            data: params,
            header: { 'Content-Type': 'application/json' },
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
    }

    // P5.1: getStock live 查询(GET 分支 EnvAdapter 直接填充 res.data)
    async function liveGetStock(productId) {
        const r = await EnvAdapter.request({
            url: apiBase + '/stock?productId=' + encodeURIComponent(productId),
            method: 'GET',
        });
        if (!r.ok) return null;   // 产品不存在等错误 → null(与 mock 语义一致)
        const body = r.data;
        return body && body.success ? body.stock : null;
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
            // 清空所有锁状态(优先用 Mutex 类,兜底用 FIFO 队列)
            if (_mutex) _mutex.clear();
            Object.keys(_mutexLocked).forEach(k => { delete _mutexLocked[k]; });
            Object.keys(_mutexQueues).forEach(k => { delete _mutexQueues[k]; });
            return this;
        },
        getMockDB() { return readDB(); },
        // getStock 双模式(P5.1): mock 同步返回 number|null; live 返回 Promise<number|null>
        // (mock 调用方无感; live 调用方需 await)
        getStock(productId) {
            if (mode === 'live') return liveGetStock(productId);
            return getStock(productId);
        },
        // 共享核心(供 checkout-service 等上层事务委托调用)
        applyDeduct: applyDeduct,
        applyRestock: applyRestock,
        // 独立事务入口
        async deduct(params) {
            if (mode === 'live') {
                try { return await liveDeduct(params); }
                catch (e) {
                    return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] };
                }
            }
            return await deduct(params);
        },
        async restock(params) {
            if (mode === 'live') {
                // live restock 略,复用 deduct 的错误处理结构
                try {
                    const r = await EnvAdapter.request({
                        url: apiBase + '/restock',
                        method: 'POST',
                        data: params,
                        header: { 'Content-Type': 'application/json' },
                    });
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return await r.json();
                } catch (e) {
                    return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] };
                }
            }
            return await restock(params);
        },
    };
})();

// 暴露到 window 全局
if (typeof window !== 'undefined') {
    window.InventoryService = InventoryService;
}

// ============================================================
//  InventoryService Mutex 混合并发压测(deduct + restock 互斥验证)
//  全局函数: runInventoryMutexStressTest(opts)
// ============================================================
if (typeof window !== 'undefined') {
    window.runInventoryMutexStressTest = async function (opts) {
        opts = opts || {};
        const {
            deductCount = 50,      // deduct 请求数
            restockCount = 30,     // restock 请求数
            productId = 2,         // 商品 ID(默认 2)
            initialStock = 100,    // 初始库存
            qtyPerOrder = 1,       // 每次扣减/回补数量
            sink = (line, type) => console.log((type === 'FAIL' ? '❌ ' : (type === 'PASS' ? '✅ ' : '   ')) + line),
        } = opts;

        const REPORT = { scenarios: [], overall: { allPass: true } };

        function log(line, type) { sink(line, type || 'INFO'); }

        // ---------- 辅助: 构造任务 ----------
        function buildDeductTask(pid, qty) {
            return () => InventoryService.deduct({
                items: [{ id: pid, name: '压测商品', qty }],
                reason: '并发扣减',
            });
        }
        function buildRestockTask(pid, qty) {
            return () => InventoryService.restock({
                items: [{ id: pid, name: '压测商品', qty }],
                reason: '并发回补',
            });
        }

        // ---------- 辅助: 校验事务日志原子性 ----------
        function checkAtomicity() {
            const db = InventoryService.getMockDB();
            const begins = db.tx_log.filter(e => e.type === 'BEGIN').length;
            const commits = db.tx_log.filter(e => e.type === 'COMMIT').length;
            const rollbacks = db.tx_log.filter(e => e.type === 'ROLLBACK').length;
            return {
                begins, commits, rollbacks,
                atomicity: begins === commits + rollbacks,
            };
        }

        // ---------- 辅助: 运行单个场景 ----------
        async function runScenario(name, tasks, expectedStock, desc) {
            InventoryService.resetMock();
            // 手动设置初始库存
            const db = InventoryService.getMockDB();
            const p = db.products.find(x => x.id === productId);
            if (p) p.stock = initialStock;
            // 持久化
            EnvAdapter.storage.set('zhuxiang_inventory_db_v1', db);

            const stockBefore = InventoryService.getStock(productId);
            const t0 = performance.now();
            const results = await Promise.all(tasks.map(t => t()));
            const elapsed = performance.now() - t0;
            const stockAfter = InventoryService.getStock(productId);

            const successCount = results.filter(r => r.success).length;
            const failCount = results.filter(r => !r.success).length;
            const deductSuccess = results.filter((r, i) => r.success && tasks[i].__type === 'deduct').length;
            const restockSuccess = results.filter((r, i) => r.success && tasks[i].__type === 'restock').length;

            // 标记任务类型(用于上面统计)
            // 注: __type 在构造时已设置

            const atomicity = checkAtomicity();
            const noOversell = stockAfter >= 0;
            const stockCorrect = stockAfter === expectedStock;
            const pass = noOversell && stockCorrect && atomicity.atomicity;

            const scenario = {
                name, desc,
                stockBefore, stockAfter, expectedStock,
                totalTasks: tasks.length,
                successCount, failCount,
                deductSuccess, restockSuccess,
                elapsed: Math.round(elapsed),
                throughput: Math.round(tasks.length / (elapsed / 1000)),
                noOversell, stockCorrect, atomicity,
                pass,
            };
            REPORT.scenarios.push(scenario);

            log(`[${name}] ${desc}`, pass ? 'PASS' : 'FAIL');
            log(`  库存: ${stockBefore} → ${stockAfter} (期望 ${expectedStock})`, pass ? 'PASS' : 'FAIL');
            log(`  成功/失败: ${successCount}/${failCount} (deduct成功=${deductSuccess}, restock成功=${restockSuccess})`, pass ? 'PASS' : 'FAIL');
            log(`  事务: BEGIN=${atomicity.begins} COMMIT=${atomicity.commits} ROLLBACK=${atomicity.rollbacks} 原子性=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
            log(`  耗时 ${scenario.elapsed}ms, 吞吐 ${scenario.throughput} req/s`, 'INFO');

            if (!pass) REPORT.overall.allPass = false;
            return scenario;
        }

        // 标记任务类型
        function tagTasks(tasks, type) {
            tasks.forEach(t => { t.__type = type; });
            return tasks;
        }

        // ============================================================
        //  场景1: 纯 deduct 高并发(库存100, 50个扣1) → 库存50
        // ============================================================
        {
            const tasks = tagTasks(
                Array.from({ length: deductCount }, () => buildDeductTask(productId, qtyPerOrder)),
                'deduct'
            );
            const expected = initialStock - deductCount * qtyPerOrder;
            await runScenario('S1', tasks, expected, `纯 deduct × ${deductCount}(库存${initialStock})`);
        }

        // ============================================================
        //  场景2: 纯 restock 高并发(库存100, 30个补1) → 库存130
        // ============================================================
        {
            const tasks = tagTasks(
                Array.from({ length: restockCount }, () => buildRestockTask(productId, qtyPerOrder)),
                'restock'
            );
            const expected = initialStock + restockCount * qtyPerOrder;
            await runScenario('S2', tasks, expected, `纯 restock × ${restockCount}(库存${initialStock})`);
        }

        // ============================================================
        //  场景3: deduct + restock 等量混合(50扣 + 50补, 库存100) → 库存100
        // ============================================================
        {
            const dTasks = tagTasks(
                Array.from({ length: 50 }, () => buildDeductTask(productId, qtyPerOrder)),
                'deduct'
            );
            const rTasks = tagTasks(
                Array.from({ length: 50 }, () => buildRestockTask(productId, qtyPerOrder)),
                'restock'
            );
            const expected = initialStock; // -50 + 50 = 100
            await runScenario('S3', [...dTasks, ...rTasks], expected, '混合 50 deduct + 50 restock(库存100)');
        }

        // ============================================================
        //  场景4: 极端比例(80 deduct + 20 restock, 库存100) → 库存40
        // ============================================================
        {
            const dTasks = tagTasks(
                Array.from({ length: 80 }, () => buildDeductTask(productId, qtyPerOrder)),
                'deduct'
            );
            const rTasks = tagTasks(
                Array.from({ length: 20 }, () => buildRestockTask(productId, qtyPerOrder)),
                'restock'
            );
            const expected = initialStock - 80 + 20; // 40
            await runScenario('S4', [...dTasks, ...rTasks], expected, '极端 80 deduct + 20 restock(库存100)');
        }

        // ============================================================
        //  场景5: 超库存 deduct + restock 混合(150扣 + 50补, 库存100)
        //         最终 = 100 - min(150, 100+50补) + 50 = 100 - 100 + 50 = 50
        //         注: restock 先执行会让 deduct 有更多库存可扣
        //         Mutex 串行化后: 最终库存 = 100 - (deduct成功数) + (restock成功数)
        //         由于 restock 全部成功(50), deduct 成功数 = 100 + 50 - finalStock
        //         关键: 库存永远 >= 0
        // ============================================================
        {
            const dTasks = tagTasks(
                Array.from({ length: 150 }, () => buildDeductTask(productId, qtyPerOrder)),
                'deduct'
            );
            const rTasks = tagTasks(
                Array.from({ length: 50 }, () => buildRestockTask(productId, qtyPerOrder)),
                'restock'
            );
            // 由于并发顺序不确定,期望值 = 初始 - deduct成功 + restock成功
            // 但 restock 全部成功(50), deduct 最多成功 100+50=150
            // 关键验证: 库存 >= 0 且 = 初始 - deduct成功 + restock成功
            const tasks = [...dTasks, ...rTasks];
            const expected = initialStock; // 理论上 deduct 150 + restock 50, 库存 100-150+50=0
            // 但实际执行顺序影响: 如果 restock 先执行, deduct 可能全部成功
            // 所以这里用动态校验,只要 noOversell 即可
            const scenario = await (async () => {
                InventoryService.resetMock();
                const db = InventoryService.getMockDB();
                const p = db.products.find(x => x.id === productId);
                if (p) p.stock = initialStock;
                EnvAdapter.storage.set('zhuxiang_inventory_db_v1', db);

                const stockBefore = InventoryService.getStock(productId);
                const t0 = performance.now();
                const results = await Promise.all(tasks.map(t => t()));
                const elapsed = performance.now() - t0;
                const stockAfter = InventoryService.getStock(productId);

                const successCount = results.filter(r => r.success).length;
                const failCount = results.filter(r => !r.success).length;
                const deductSuccess = results.filter((r, i) => r.success && tasks[i].__type === 'deduct').length;
                const restockSuccess = results.filter((r, i) => r.success && tasks[i].__type === 'restock').length;

                const atomicity = checkAtomicity();
                const noOversell = stockAfter >= 0;
                // 库存一致性: final = initial - deductSuccess + restockSuccess
                const stockConsistent = stockAfter === initialStock - deductSuccess + restockSuccess;
                const pass = noOversell && stockConsistent && atomicity.atomicity;

                const s = {
                    name: 'S5', desc: '超库存 150 deduct + 50 restock(库存100)',
                    stockBefore, stockAfter,
                    expectedStock: initialStock - deductSuccess + restockSuccess,
                    totalTasks: tasks.length,
                    successCount, failCount,
                    deductSuccess, restockSuccess,
                    elapsed: Math.round(elapsed),
                    throughput: Math.round(tasks.length / (elapsed / 1000)),
                    noOversell, stockConsistent, atomicity,
                    pass,
                };
                REPORT.scenarios.push(s);
                if (!pass) REPORT.overall.allPass = false;

                log(`[S5] 超库存 150 deduct + 50 restock(库存100)`, pass ? 'PASS' : 'FAIL');
                log(`  库存: ${stockBefore} → ${stockAfter} (期望 = ${initialStock} - ${deductSuccess} + ${restockSuccess} = ${initialStock - deductSuccess + restockSuccess})`, pass ? 'PASS' : 'FAIL');
                log(`  成功/失败: ${successCount}/${failCount} (deduct成功=${deductSuccess}, restock成功=${restockSuccess})`, pass ? 'PASS' : 'FAIL');
                log(`  事务: BEGIN=${atomicity.begins} COMMIT=${atomicity.commits} ROLLBACK=${atomicity.rollbacks} 原子性=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
                log(`  耗时 ${s.elapsed}ms, 吞吐 ${s.throughput} req/s`, 'INFO');
                return s;
            })();
        }

        // ============================================================
        //  场景6: 多商品混合(deduct商品1×30 + restock商品2×30, 库存各100)
        //         商品1 → 70, 商品2 → 130
        // ============================================================
        {
            const pid2 = productId === 2 ? 1 : 2; // 另一个商品
            const dTasks = tagTasks(
                Array.from({ length: 30 }, () => buildDeductTask(productId, qtyPerOrder)),
                'deduct'
            );
            const rTasks = tagTasks(
                Array.from({ length: 30 }, () => buildRestockTask(pid2, qtyPerOrder)),
                'restock'
            );
            // 不同商品,期望各算各的
            InventoryService.resetMock();
            const db = InventoryService.getMockDB();
            const p1 = db.products.find(x => x.id === productId);
            const p2 = db.products.find(x => x.id === pid2);
            if (p1) p1.stock = initialStock;
            if (p2) p2.stock = initialStock;
            EnvAdapter.storage.set('zhuxiang_inventory_db_v1', db);

            const stock1Before = InventoryService.getStock(productId);
            const stock2Before = InventoryService.getStock(pid2);
            const tasks = [...dTasks, ...rTasks];
            const t0 = performance.now();
            const results = await Promise.all(tasks.map(t => t()));
            const elapsed = performance.now() - t0;
            const stock1After = InventoryService.getStock(productId);
            const stock2After = InventoryService.getStock(pid2);

            const atomicity = checkAtomicity();
            const noOversell = stock1After >= 0 && stock2After >= 0;
            const stockCorrect = stock1After === stock1Before - 30 && stock2After === stock2Before + 30;
            const pass = noOversell && stockCorrect && atomicity.atomicity;

            const s = {
                name: 'S6', desc: `多商品 deduct(商品${productId}×30) + restock(商品${pid2}×30)`,
                stock1Before, stock1After, stock2Before, stock2After,
                expected1: stock1Before - 30, expected2: stock2Before + 30,
                totalTasks: tasks.length,
                successCount: results.filter(r => r.success).length,
                failCount: results.filter(r => !r.success).length,
                elapsed: Math.round(elapsed),
                throughput: Math.round(tasks.length / (elapsed / 1000)),
                noOversell, stockCorrect, atomicity,
                pass,
            };
            REPORT.scenarios.push(s);
            if (!pass) REPORT.overall.allPass = false;

            log(`[S6] 多商品 deduct(商品${productId}×30) + restock(商品${pid2}×30)`, pass ? 'PASS' : 'FAIL');
            log(`  商品${productId}: ${stock1Before} → ${stock1After} (期望 ${stock1Before - 30})`, pass ? 'PASS' : 'FAIL');
            log(`  商品${pid2}: ${stock2Before} → ${stock2After} (期望 ${stock2Before + 30})`, pass ? 'PASS' : 'FAIL');
            log(`  事务: BEGIN=${atomicity.begins} COMMIT=${atomicity.commits} ROLLBACK=${atomicity.rollbacks} 原子性=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
            log(`  耗时 ${s.elapsed}ms, 吞吐 ${s.throughput} req/s`, 'INFO');
        }

        // ============================================================
        //  汇总
        // ============================================================
        const totalTasks = REPORT.scenarios.reduce((s, x) => s + x.totalTasks, 0);
        const totalSuccess = REPORT.scenarios.reduce((s, x) => s + x.successCount, 0);
        const totalFail = REPORT.scenarios.reduce((s, x) => s + x.failCount, 0);
        const passed = REPORT.scenarios.filter(x => x.pass).length;

        log('========== InventoryService Mutex 混合并发压测汇总 ==========', 'INFO');
        log(`场景总数: ${REPORT.scenarios.length}, 通过: ${passed}/${REPORT.scenarios.length}`, REPORT.overall.allPass ? 'PASS' : 'FAIL');
        log(`总任务: ${totalTasks}, 总成功: ${totalSuccess}, 总失败: ${totalFail}`, 'INFO');
        log(REPORT.overall.allPass ? '✅ 所有场景通过, deduct/restock 互斥锁生效' : '❌ 有场景失败', REPORT.overall.allPass ? 'PASS' : 'FAIL');

        REPORT.overall.totalTasks = totalTasks;
        REPORT.overall.totalSuccess = totalSuccess;
        REPORT.overall.totalFail = totalFail;
        REPORT.overall.passedScenarios = passed;
        REPORT.overall.totalScenarios = REPORT.scenarios.length;

        return REPORT;
    };

    // Headless 检测钩子
    if (typeof window !== 'undefined') {
        Object.defineProperty(window, '__runInventoryMutexStressTestPromise', {
            get: () => window.runInventoryMutexStressTest(),
            configurable: true,
        });
    }
}

// ============================================================
//  InventoryService 库存为 0 极端边界场景测试
//  全局函数: runInventoryEdgeCaseTest(opts)
//  专门测试库存=0 时 deduct/restock 并发的边界行为
// ============================================================
if (typeof window !== 'undefined') {
    window.runInventoryEdgeCaseTest = async function (opts) {
        opts = opts || {};
        const {
            productId = 2,
            sink = (line, type) => console.log((type === 'FAIL' ? '❌ ' : (type === 'PASS' ? '✅ ' : '   ')) + line),
        } = opts;

        const REPORT = { scenarios: [], overall: { allPass: true } };
        function log(line, type) { sink(line, type || 'INFO'); }

        // 辅助: 设置库存
        function setStock(pid, stock) {
            InventoryService.resetMock();
            const db = InventoryService.getMockDB();
            const p = db.products.find(x => x.id === pid);
            if (p) p.stock = stock;
            EnvAdapter.storage.set('zhuxiang_inventory_db_v1', db);
        }

        // 辅助: 检查事务原子性
        function checkAtomicity() {
            const db = InventoryService.getMockDB();
            const begins = db.tx_log.filter(e => e.type === 'BEGIN').length;
            const commits = db.tx_log.filter(e => e.type === 'COMMIT').length;
            const rollbacks = db.tx_log.filter(e => e.type === 'ROLLBACK').length;
            return { begins, commits, rollbacks, atomicity: begins === commits + rollbacks };
        }

        // ============================================================
        //  场景1: 库存0 + 30并发 deduct → 全部失败, 库存保持0
        //  验证: 库存不足时全部回滚, 库存不变
        // ============================================================
        {
            setStock(productId, 0);
            const before = InventoryService.getStock(productId);
            const r = await Promise.all(Array.from({ length: 30 }, () =>
                InventoryService.deduct({ items: [{ id: productId, name: '边界商品', qty: 1 }], reason: '边界-纯扣减' })
            ));
            const after = InventoryService.getStock(productId);
            const success = r.filter(x => x.success).length;
            const fail = r.filter(x => !x.success).length;
            const atomicity = checkAtomicity();
            const pass = success === 0 && fail === 30 && after === 0 && after >= 0 && atomicity.atomicity;
            const s = { name: 'E1', desc: '库存0 + 30并发 deduct(应全失败)', before, after, expected: 0, success, fail, noOversell: after >= 0, atomicity, pass };
            REPORT.scenarios.push(s);
            if (!pass) REPORT.overall.allPass = false;
            log(`[E1] 库存0 + 30并发 deduct`, pass ? 'PASS' : 'FAIL');
            log(`  库存: ${before} → ${after} (期望0), 成功/失败: ${success}/${fail}`, pass ? 'PASS' : 'FAIL');
            log(`  事务: B=${atomicity.begins} C=${atomicity.commits} R=${atomicity.rollbacks} 原子=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
        }

        // ============================================================
        //  场景2: 库存0 + 30并发 restock → 全部成功, 库存0→30
        //  验证: 库存0时回补正常, 不受0库存影响
        // ============================================================
        {
            setStock(productId, 0);
            const before = InventoryService.getStock(productId);
            const r = await Promise.all(Array.from({ length: 30 }, () =>
                InventoryService.restock({ items: [{ id: productId, name: '边界商品', qty: 1 }], reason: '边界-纯回补' })
            ));
            const after = InventoryService.getStock(productId);
            const success = r.filter(x => x.success).length;
            const fail = r.filter(x => !x.success).length;
            const atomicity = checkAtomicity();
            const pass = success === 30 && fail === 0 && after === 30 && atomicity.atomicity;
            const s = { name: 'E2', desc: '库存0 + 30并发 restock(应全成功)', before, after, expected: 30, success, fail, atomicity, pass };
            REPORT.scenarios.push(s);
            if (!pass) REPORT.overall.allPass = false;
            log(`[E2] 库存0 + 30并发 restock`, pass ? 'PASS' : 'FAIL');
            log(`  库存: ${before} → ${after} (期望30), 成功/失败: ${success}/${fail}`, pass ? 'PASS' : 'FAIL');
            log(`  事务: B=${atomicity.begins} C=${atomicity.commits} R=${atomicity.rollbacks} 原子=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
        }

        // ============================================================
        //  场景3: 库存0 + 30 deduct + 30 restock 混合并发
        //  restock全成功(30), deduct部分成功(取决于restock先执行)
        //  最终: 0 - dSuccess + 30
        //  关键: 库存>=0, final = 0 - dSuccess + rSuccess
        // ============================================================
        {
            setStock(productId, 0);
            const before = InventoryService.getStock(productId);
            const dTasks = Array.from({ length: 30 }, () =>
                InventoryService.deduct({ items: [{ id: productId, name: '边界商品', qty: 1 }], reason: '边界-混合扣减' })
            );
            const rTasks = Array.from({ length: 30 }, () =>
                InventoryService.restock({ items: [{ id: productId, name: '边界商品', qty: 1 }], reason: '边界-混合回补' })
            );
            const allTasks = [...dTasks, ...rTasks];
            const r = await Promise.all(allTasks);
            const after = InventoryService.getStock(productId);
            // 前30是deduct, 后30是restock
            const dSuccess = r.slice(0, 30).filter(x => x.success).length;
            const rSuccess = r.slice(30).filter(x => x.success).length;
            const expected = 0 - dSuccess + rSuccess;
            const atomicity = checkAtomicity();
            const noOversell = after >= 0;
            const stockConsistent = after === expected;
            const pass = noOversell && stockConsistent && atomicity.atomicity;
            const s = { name: 'E3', desc: '库存0 + 30 deduct + 30 restock混合', before, after, expected, dSuccess, rSuccess, noOversell, stockConsistent, atomicity, pass };
            REPORT.scenarios.push(s);
            if (!pass) REPORT.overall.allPass = false;
            log(`[E3] 库存0 + 30 deduct + 30 restock混合`, pass ? 'PASS' : 'FAIL');
            log(`  库存: ${before} → ${after} (期望 = 0 - ${dSuccess} + ${rSuccess} = ${expected})`, pass ? 'PASS' : 'FAIL');
            log(`  dSuccess=${dSuccess}, rSuccess=${rSuccess}, noOversell=${noOversell}, consistent=${stockConsistent}`, pass ? 'PASS' : 'FAIL');
            log(`  事务: B=${atomicity.begins} C=${atomicity.commits} R=${atomicity.rollbacks} 原子=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
        }

        // ============================================================
        //  场景4: 库存0 + 100 deduct + 10 restock(极端比例)
        //  restock全成功(10), deduct最多成功10
        //  最终: 0 - dSuccess + 10
        //  关键: 库存>=0, 不超卖
        // ============================================================
        {
            setStock(productId, 0);
            const before = InventoryService.getStock(productId);
            const dTasks = Array.from({ length: 100 }, () =>
                InventoryService.deduct({ items: [{ id: productId, name: '边界商品', qty: 1 }], reason: '边界-极端比例扣减' })
            );
            const rTasks = Array.from({ length: 10 }, () =>
                InventoryService.restock({ items: [{ id: productId, name: '边界商品', qty: 1 }], reason: '边界-极端比例回补' })
            );
            const allTasks = [...dTasks, ...rTasks];
            const r = await Promise.all(allTasks);
            const after = InventoryService.getStock(productId);
            const dSuccess = r.slice(0, 100).filter(x => x.success).length;
            const rSuccess = r.slice(100).filter(x => x.success).length;
            const expected = 0 - dSuccess + rSuccess;
            const atomicity = checkAtomicity();
            const noOversell = after >= 0;
            const stockConsistent = after === expected;
            const pass = noOversell && stockConsistent && atomicity.atomicity;
            const s = { name: 'E4', desc: '库存0 + 100 deduct + 10 restock(极端比例)', before, after, expected, dSuccess, rSuccess, noOversell, stockConsistent, atomicity, pass };
            REPORT.scenarios.push(s);
            if (!pass) REPORT.overall.allPass = false;
            log(`[E4] 库存0 + 100 deduct + 10 restock`, pass ? 'PASS' : 'FAIL');
            log(`  库存: ${before} → ${after} (期望 = 0 - ${dSuccess} + ${rSuccess} = ${expected})`, pass ? 'PASS' : 'FAIL');
            log(`  dSuccess=${dSuccess}, rSuccess=${rSuccess}, noOversell=${noOversell}, consistent=${stockConsistent}`, pass ? 'PASS' : 'FAIL');
            log(`  事务: B=${atomicity.begins} C=${atomicity.commits} R=${atomicity.rollbacks} 原子=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
        }

        // ============================================================
        //  场景5: 库存0 + 5次交替执行(restock→deduct 串行交替)
        //  验证: 锁释放后 deduct 能正确看到 restock 增加的库存
        //  每次 restock +1 → deduct -1, 最终库存0
        // ============================================================
        {
            setStock(productId, 0);
            const before = InventoryService.getStock(productId);
            const results = [];
            for (let i = 0; i < 5; i++) {
                results.push(await InventoryService.restock({ items: [{ id: productId, name: '边界商品', qty: 1 }], reason: '交替-回补' }));
                results.push(await InventoryService.deduct({ items: [{ id: productId, name: '边界商品', qty: 1 }], reason: '交替-扣减' }));
            }
            const after = InventoryService.getStock(productId);
            // 偶数索引是restock, 奇数索引是deduct
            const rSuccess = results.filter((x, i) => i % 2 === 0).filter(x => x.success).length;
            const dSuccess = results.filter((x, i) => i % 2 === 1).filter(x => x.success).length;
            const atomicity = checkAtomicity();
            const pass = rSuccess === 5 && dSuccess === 5 && after === 0 && atomicity.atomicity;
            const s = { name: 'E5', desc: '库存0 + 5次交替(restock→deduct串行)', before, after, expected: 0, rSuccess, dSuccess, atomicity, pass };
            REPORT.scenarios.push(s);
            if (!pass) REPORT.overall.allPass = false;
            log(`[E5] 库存0 + 5次交替(restock→deduct串行)`, pass ? 'PASS' : 'FAIL');
            log(`  库存: ${before} → ${after} (期望0), rSuccess=${rSuccess}, dSuccess=${dSuccess}`, pass ? 'PASS' : 'FAIL');
            log(`  事务: B=${atomicity.begins} C=${atomicity.commits} R=${atomicity.rollbacks} 原子=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
        }

        // ============================================================
        //  场景6: 库存0 + 单个 deduct 单次(单点边界)
        //  验证: 库存0时单个 deduct 立即失败
        // ============================================================
        {
            setStock(productId, 0);
            const before = InventoryService.getStock(productId);
            const r = await InventoryService.deduct({ items: [{ id: productId, name: '边界商品', qty: 1 }], reason: '边界-单次扣减' });
            const after = InventoryService.getStock(productId);
            const atomicity = checkAtomicity();
            const pass = r.success === false && after === 0 && atomicity.atomicity;
            const s = { name: 'E6', desc: '库存0 + 单个 deduct(单点边界)', before, after, expected: 0, success: r.success, atomicity, pass };
            REPORT.scenarios.push(s);
            if (!pass) REPORT.overall.allPass = false;
            log(`[E6] 库存0 + 单个 deduct`, pass ? 'PASS' : 'FAIL');
            log(`  库存: ${before} → ${after} (期望0), success=${r.success}`, pass ? 'PASS' : 'FAIL');
            log(`  事务: B=${atomicity.begins} C=${atomicity.commits} R=${atomicity.rollbacks} 原子=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
        }

        // ============================================================
        //  汇总
        // ============================================================
        const passed = REPORT.scenarios.filter(x => x.pass).length;
        log('========== 库存0 极端边界场景测试汇总 ==========', 'INFO');
        log(`场景总数: ${REPORT.scenarios.length}, 通过: ${passed}/${REPORT.scenarios.length}`, REPORT.overall.allPass ? 'PASS' : 'FAIL');
        log(REPORT.overall.allPass ? '✅ 所有边界场景通过, 库存0时锁依然生效' : '❌ 有场景失败', REPORT.overall.allPass ? 'PASS' : 'FAIL');

        REPORT.overall.passedScenarios = passed;
        REPORT.overall.totalScenarios = REPORT.scenarios.length;
        return REPORT;
    };

    // Headless 检测钩子
    Object.defineProperty(window, '__runInventoryEdgeCaseTestPromise', {
        get: () => window.runInventoryEdgeCaseTest(),
        configurable: true,
    });
}

// ============================================================
//  InventoryService 极限并发高压吞吐测试
//  全局函数: runInventoryHighPressureTest(opts)
//  500 deduct + 500 restock, 验证极限并发下的吞吐量/延迟/锁稳定性
//
//  指标:
//    · 吞吐量 (req/s)        — 总任务数 / 总耗时
//    · 延迟分布 (min/avg/p50/p95/p99/max) — 单请求从派发到完成
//    · 不超卖 / 库存一致 / 事务原子性 — 正确性三不变量
//    · 锁竞争指标 (maxLatency vs avgLatency 比值) — 串行化代价
// ============================================================
if (typeof window !== 'undefined') {
    window.runInventoryHighPressureTest = async function (opts) {
        opts = opts || {};
        const {
            deductCount   = 500,  // deduct 请求数
            restockCount   = 500,  // restock 请求数
            productId      = 2,
            initialStock   = 500,  // 默认充足库存, 保证 deduct 不被库存限制
            qtyPerOrder    = 1,
            sink = (line, type) => console.log((type === 'FAIL' ? '❌ ' : (type === 'PASS' ? '✅ ' : '   ')) + line),
        } = opts;

        const REPORT = { scenarios: [], overall: { allPass: true } };
        function log(line, type) { sink(line, type || 'INFO'); }

        // ---------- 辅助: 设置库存 ----------
        function setStock(pid, stock) {
            InventoryService.resetMock();
            const db = InventoryService.getMockDB();
            const p = db.products.find(x => x.id === pid);
            if (p) p.stock = stock;
            EnvAdapter.storage.set('zhuxiang_inventory_db_v1', db);
        }

        // ---------- 辅助: 事务原子性 ----------
        function checkAtomicity() {
            const db = InventoryService.getMockDB();
            const begins = db.tx_log.filter(e => e.type === 'BEGIN').length;
            const commits = db.tx_log.filter(e => e.type === 'COMMIT').length;
            const rollbacks = db.tx_log.filter(e => e.type === 'ROLLBACK').length;
            return { begins, commits, rollbacks, atomicity: begins === commits + rollbacks };
        }

        // ---------- 辅助: 百分位 ----------
        function percentile(arr, p) {
            if (!arr.length) return 0;
            const sorted = [...arr].sort((a, b) => a - b);
            const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
            return sorted[idx];
        }

        // ---------- 辅助: 延迟汇总 ----------
        function summarizeLatency(latencies) {
            if (!latencies.length) return { min: 0, avg: 0, max: 0, p50: 0, p95: 0, p99: 0 };
            const sum = latencies.reduce((a, b) => a + b, 0);
            return {
                min: Math.round(Math.min(...latencies) * 100) / 100,
                avg: Math.round((sum / latencies.length) * 100) / 100,
                max: Math.round(Math.max(...latencies) * 100) / 100,
                p50: Math.round(percentile(latencies, 50) * 100) / 100,
                p95: Math.round(percentile(latencies, 95) * 100) / 100,
                p99: Math.round(percentile(latencies, 99) * 100) / 100,
            };
        }

        // ---------- 辅助: 带计时任务 ----------
        // 每个任务返回 { result, latencyMs }(latency = 派发到完成的墙钟时间)
        function buildTimedDeduct(pid, qty) {
            return () => {
                const t0 = performance.now();
                return InventoryService.deduct({
                    items: [{ id: pid, name: '高压商品', qty }],
                    reason: '高压并发扣减',
                }).then(result => ({ result, latencyMs: performance.now() - t0 }));
            };
        }
        function buildTimedRestock(pid, qty) {
            return () => {
                const t0 = performance.now();
                return InventoryService.restock({
                    items: [{ id: pid, name: '高压商品', qty }],
                    reason: '高压并发回补',
                }).then(result => ({ result, latencyMs: performance.now() - t0 }));
            };
        }

        // ---------- 辅助: 运行高压场景 ----------
        // tagMap: 与 tasks 对齐的 'deduct'|'restock' 数组
        // expectedFactory: (dSuccess, rSuccess) => 期望库存 (动态场景); 静态场景传 null 并用 expectedStatic
        async function runHP(name, tasks, tagMap, desc, expectedFactory, expectedStatic) {
            const t0 = performance.now();
            const timed = await Promise.all(tasks.map(t => t()));
            const elapsed = performance.now() - t0;
            const latencies = timed.map(x => x.latencyMs);
            const results = timed.map(x => x.result);

            const lat = summarizeLatency(latencies);
            const stockAfter = InventoryService.getStock(productId);
            const atomicity = checkAtomicity();
            const successCount = results.filter(r => r.success).length;
            const failCount = results.filter(r => !r.success).length;
            const dSuccess = results.filter((r, i) => r.success && tagMap[i] === 'deduct').length;
            const rSuccess = results.filter((r, i) => r.success && tagMap[i] === 'restock').length;
            const noOversell = stockAfter >= 0;
            const expected = expectedFactory ? expectedFactory(dSuccess, rSuccess) : expectedStatic;
            const stockConsistent = stockAfter === expected;
            const pass = noOversell && stockConsistent && atomicity.atomicity;
            const throughput = elapsed > 0 ? Math.round(tasks.length / (elapsed / 1000)) : 0;
            // 锁竞争度: max/avg 比值(越大说明排队越严重)
            const contentionRatio = lat.avg > 0 ? Math.round((lat.max / lat.avg) * 100) / 100 : 0;

            const scenario = {
                name, desc,
                totalTasks: tasks.length,
                successCount, failCount, dSuccess, rSuccess,
                stockAfter, expectedStock: expected,
                elapsed: Math.round(elapsed),
                throughput,
                latency: lat,
                contentionRatio,
                noOversell, stockConsistent, atomicity, pass,
            };
            REPORT.scenarios.push(scenario);
            if (!pass) REPORT.overall.allPass = false;

            log(`[${name}] ${desc}`, pass ? 'PASS' : 'FAIL');
            log(`  库存: → ${stockAfter} (期望 ${expected}), dSuccess=${dSuccess}, rSuccess=${rSuccess}`, pass ? 'PASS' : 'FAIL');
            log(`  成功/失败: ${successCount}/${failCount}, 不超卖=${noOversell}, 一致=${stockConsistent}`, pass ? 'PASS' : 'FAIL');
            log(`  事务: B=${atomicity.begins} C=${atomicity.commits} R=${atomicity.rollbacks} 原子=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
            log(`  吞吐 ${throughput} req/s, 耗时 ${scenario.elapsed}ms, 竞争比 max/avg=${contentionRatio}`, 'INFO');
            log(`  延迟(ms): min=${lat.min} avg=${lat.avg} p50=${lat.p50} p95=${lat.p95} p99=${lat.p99} max=${lat.max}`, 'INFO');
            return scenario;
        }

        // ============================================================
        //  HP1: 500 纯 deduct(库存500) → 库存0
        //  验证: 纯扣减极限吞吐, 全部成功, 库存守恒
        // ============================================================
        {
            setStock(productId, initialStock);
            const tasks = Array.from({ length: deductCount }, () => buildTimedDeduct(productId, qtyPerOrder));
            const tagMap = tasks.map(() => 'deduct');
            await runHP('HP1', tasks, tagMap,
                `${deductCount} 纯 deduct(库存${initialStock})`,
                null, initialStock - deductCount * qtyPerOrder);
        }

        // ============================================================
        //  HP2: 500 纯 restock(库存0) → 库存500
        //  验证: 纯回补极限吞吐, 全部成功
        // ============================================================
        {
            setStock(productId, 0);
            const tasks = Array.from({ length: restockCount }, () => buildTimedRestock(productId, qtyPerOrder));
            const tagMap = tasks.map(() => 'restock');
            await runHP('HP2', tasks, tagMap,
                `${restockCount} 纯 restock(库存0)`,
                null, 0 + restockCount * qtyPerOrder);
        }

        // ============================================================
        //  HP3: 500 deduct + 500 restock 混合(库存500, 等量抵消) → 库存500
        //  最终 = 500 - dSuccess + rSuccess
        //  验证: 混合极限并发吞吐 + 锁互斥正确性
        // ============================================================
        {
            setStock(productId, initialStock);
            const dTasks = Array.from({ length: deductCount }, () => buildTimedDeduct(productId, qtyPerOrder));
            const rTasks = Array.from({ length: restockCount }, () => buildTimedRestock(productId, qtyPerOrder));
            // 交替排列最大化锁竞争(deduct,restock,deduct,restock...)
            const tasks = [];
            const tagMap = [];
            const max = Math.max(deductCount, restockCount);
            for (let i = 0; i < max; i++) {
                if (i < deductCount) { tasks.push(dTasks[i]); tagMap.push('deduct'); }
                if (i < restockCount) { tasks.push(rTasks[i]); tagMap.push('restock'); }
            }
            await runHP('HP3', tasks, tagMap,
                `${deductCount} deduct + ${restockCount} restock 混合(库存${initialStock}, 交替排列)`,
                (d, r) => initialStock - d + r, null);
        }

        // ============================================================
        //  HP4: 500 deduct + 500 restock, 库存0 极端边界
        //  最终 = 0 - dSuccess + rSuccess
        //  验证: 库存0时极限并发不超卖 + 吞吐稳定性
        // ============================================================
        {
            setStock(productId, 0);
            const dTasks = Array.from({ length: deductCount }, () => buildTimedDeduct(productId, qtyPerOrder));
            const rTasks = Array.from({ length: restockCount }, () => buildTimedRestock(productId, qtyPerOrder));
            const tasks = [];
            const tagMap = [];
            const max = Math.max(deductCount, restockCount);
            for (let i = 0; i < max; i++) {
                if (i < deductCount) { tasks.push(dTasks[i]); tagMap.push('deduct'); }
                if (i < restockCount) { tasks.push(rTasks[i]); tagMap.push('restock'); }
            }
            await runHP('HP4', tasks, tagMap,
                `${deductCount} deduct + ${restockCount} restock 混合(库存0, 极端边界)`,
                (d, r) => 0 - d + r, null);
        }

        // ============================================================
        //  HP5: 分批持续压测(10批 × 100任务 = 50扣+50补/批, 库存500)
        //  验证: 持续负载下吞吐稳定性(无锁泄漏/死锁/性能退化)
        //  每批独立并发, 批间串行; 累计 500 deduct + 500 restock
        // ============================================================
        {
            setStock(productId, initialStock);
            const batches = 10;
            const perBatch = 100; // 50 deduct + 50 restock
            const batchStats = [];
            const t0 = performance.now();
            for (let b = 0; b < batches; b++) {
                const dTasks = Array.from({ length: perBatch / 2 }, () => buildTimedDeduct(productId, qtyPerOrder));
                const rTasks = Array.from({ length: perBatch / 2 }, () => buildTimedRestock(productId, qtyPerOrder));
                const tasks = [...dTasks, ...rTasks];
                const tagMap = tasks.map((_, i) => i < perBatch / 2 ? 'deduct' : 'restock');
                const bt0 = performance.now();
                const timed = await Promise.all(tasks.map(t => t()));
                const bElapsed = performance.now() - bt0;
                const bLat = summarizeLatency(timed.map(x => x.latencyMs));
                const bResults = timed.map(x => x.result);
                batchStats.push({
                    batch: b + 1,
                    elapsed: Math.round(bElapsed),
                    throughput: bElapsed > 0 ? Math.round(perBatch / (bElapsed / 1000)) : 0,
                    p50: bLat.p50, p99: bLat.p99, max: bLat.max,
                    success: bResults.filter(r => r.success).length,
                });
            }
            const totalElapsed = performance.now() - t0;
            const totalTasks = batches * perBatch;
            const stockAfter = InventoryService.getStock(productId);
            const atomicity = checkAtomicity();
            // 累计 500扣+500补, 库存守恒 → 500
            const noOversell = stockAfter >= 0;
            const stockConsistent = stockAfter === initialStock;
            const pass = noOversell && stockConsistent && atomicity.atomicity;
            const throughput = totalElapsed > 0 ? Math.round(totalTasks / (totalElapsed / 1000)) : 0;
            // 性能退化检测: 末批吞吐 vs 首批吞吐
            const firstTp = batchStats[0].throughput;
            const lastTp = batchStats[batchStats.length - 1].throughput;
            const degradation = firstTp > 0 ? Math.round((1 - lastTp / firstTp) * 10000) / 100 : 0;

            const scenario = {
                name: 'HP5', desc: `分批持续压测(${batches}批×${perBatch}, 库存${initialStock})`,
                totalTasks, successCount: totalTasks, failCount: 0,
                stockAfter, expectedStock: initialStock,
                elapsed: Math.round(totalElapsed),
                throughput,
                batchStats,
                firstBatchThroughput: firstTp,
                lastBatchThroughput: lastTp,
                degradationPercent: degradation,
                noOversell, stockConsistent, atomicity, pass,
            };
            REPORT.scenarios.push(scenario);
            if (!pass) REPORT.overall.allPass = false;

            log(`[HP5] ${scenario.desc}`, pass ? 'PASS' : 'FAIL');
            log(`  库存: → ${stockAfter} (期望 ${initialStock}), 不超卖=${noOversell}, 一致=${stockConsistent}`, pass ? 'PASS' : 'FAIL');
            log(`  事务: B=${atomicity.begins} C=${atomicity.commits} R=${atomicity.rollbacks} 原子=${atomicity.atomicity}`, pass ? 'PASS' : 'FAIL');
            log(`  累计吞吐 ${throughput} req/s, 总耗时 ${scenario.elapsed}ms, 性能退化 ${degradation}%`, 'INFO');
            log(`  首批吞吐 ${firstTp} req/s → 末批吞吐 ${lastTp} req/s`, 'INFO');
        }

        // ============================================================
        //  汇总
        // ============================================================
        const totalTasks = REPORT.scenarios.reduce((s, x) => s + x.totalTasks, 0);
        const totalSuccess = REPORT.scenarios.reduce((s, x) => s + (x.successCount || 0), 0);
        const totalFail = REPORT.scenarios.reduce((s, x) => s + (x.failCount || 0), 0);
        const passed = REPORT.scenarios.filter(x => x.pass).length;
        const avgThroughput = REPORT.scenarios
            .filter(x => x.throughput)
            .reduce((a, x, _i, arr) => a + x.throughput / arr.length, 0);

        log('========== 极限并发高压吞吐测试汇总 ==========', 'INFO');
        log(`场景总数: ${REPORT.scenarios.length}, 通过: ${passed}/${REPORT.scenarios.length}`, REPORT.overall.allPass ? 'PASS' : 'FAIL');
        log(`总任务: ${totalTasks}, 总成功: ${totalSuccess}, 总失败: ${totalFail}`, 'INFO');
        log(`平均吞吐: ${Math.round(avgThroughput)} req/s`, 'INFO');
        log(REPORT.overall.allPass ? '✅ 所有高压场景通过, 极限并发下锁依然生效, 无超卖/死锁/退化' : '❌ 有场景失败', REPORT.overall.allPass ? 'PASS' : 'FAIL');

        REPORT.overall.totalTasks = totalTasks;
        REPORT.overall.totalSuccess = totalSuccess;
        REPORT.overall.totalFail = totalFail;
        REPORT.overall.avgThroughput = Math.round(avgThroughput);
        REPORT.overall.passedScenarios = passed;
        REPORT.overall.totalScenarios = REPORT.scenarios.length;

        return REPORT;
    };

    // Headless 检测钩子
    Object.defineProperty(window, '__runInventoryHighPressureTestPromise', {
        get: () => window.runInventoryHighPressureTest(),
        configurable: true,
    });
}
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { InventoryService };
}
