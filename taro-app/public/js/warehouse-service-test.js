/* ============================================
   竹香酒官网 · AI智能仓储服务(模块28) - 回归测试脚本
   --------------------------------------------
   用途: 验证 warehouse-service.js 的 10 个 AI 能力 API 端点
         覆盖事务流程/Mutex锁/Mock数据/业务逻辑
   --------------------------------------------
   12 个测试用例 (WSTC1-WSTC12):
     WSTC1  AI智能入库      qty=5        (事务+库存增+入库单+流水+库位分配)
     WSTC2  AI智能出库      qty=3        (事务+库存减+出库单+流水+拣选效率)
     WSTC3  AI智能出库-库存不足 qty=999  (验证阶段3抛错+快照回滚+原子性)
     WSTC4  AI智能盘点      actualQty=98 (盘点记录+差异调整+准确率98%)
     WSTC5  AI智能库位优化               (ABC分类+冷热区+优化记录+提升30%)
     WSTC6  AI智能库存预测               (LSTM模型+季节性+预测准确率89%)
     WSTC7  AI智能安全库存               (动态安全库存+周转提升25%+补货点)
     WSTC8  AI智能温湿度监控             (IoT读数+异常检测95%+酒龄管理)
     WSTC9  AI智能多仓协同               (源仓减+目标仓增+调拨单+及时率92%)
     WSTC10 AI智能损耗管理               (库存减+损耗单+根因分析+降低20%)
     WSTC11 AI智能仓配一体               (越库+不入库+对接物流06+越库率40%)
     WSTC12 服务结构与规范验证           (全局暴露/Mock/Live/Mutex/CONFIG)
   --------------------------------------------
   运行方式:
     · 浏览器: 在 module-test.html 点击「仓储服务API测试」按钮
     · 控制台: runWarehouseServiceTest()
     · Headless: window.__runWarehouseServiceTestPromise
   ============================================ */

(function () {
    'use strict';

    // ---------- 断言工具 ----------
    function assert(cond, message) {
        if (!cond) throw new Error('断言失败: ' + message);
    }
    function assertEqual(actual, expected, message) {
        if (actual !== expected) {
            throw new Error((message || '断言失败') + ' (期望 ' + JSON.stringify(expected) + ', 实际 ' + JSON.stringify(actual) + ')');
        }
    }
    function assertIncludes(arr, item, message) {
        if (!arr.some(x => (typeof x === 'string' && x.includes(item)) || x === item)) {
            throw new Error((message || '断言失败') + ' (数组中未找到 ' + item + ')');
        }
    }

    // ---------- 测试执行器 ----------
    async function runOne(name, fn) {
        const start = Date.now();
        try {
            await fn();
            return { name, status: 'PASS', duration: Date.now() - start, error: null };
        } catch (e) {
            return { name, status: 'FAIL', duration: Date.now() - start, error: e.message };
        }
    }

    // ---------- 输出适配 ----------
    let _sink = null;
    function emit(line, type) {
        if (_sink && typeof _sink === 'function') { _sink(line, type); return; }
        if (typeof document !== 'undefined' && document.getElementById) {
            const logEl = document.getElementById('warehouseServiceLog');
            if (logEl) {
                const color = type === 'pass' ? '#0f0' : type === 'fail' ? '#f88' : type === 'warn' ? '#fc0' : '#0ff';
                const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
                const entry = document.createElement('div');
                entry.style.color = color;
                entry.innerHTML = '<span style="opacity:0.6;">[' + t + ']</span> ' + line;
                logEl.appendChild(entry);
                logEl.scrollTop = logEl.scrollHeight;
                return;
            }
        }
        if (typeof console !== 'undefined') console.log(line);
    }

    // ---------- setup ----------
    async function setup() {
        if (typeof WarehouseService === 'undefined') {
            throw new Error('WarehouseService 未加载,请先引入 js/warehouse-service.js');
        }
        WarehouseService.resetMock();
    }

    // ============================================================
    //  测试用例
    // ============================================================

    // WSTC1: AI智能入库
    async function WSTC1_inbound() {
        await setup();
        const db0 = WarehouseService.getMockDB();
        const initStock = db0.inventory_stock.find(s => s.warehouse_id === 1 && s.product_id === 1).stock_qty;
        assertEqual(initStock, 100, 'WSTC1 初始库存应为100');

        const r = await WarehouseService.inbound({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 5 }],
            warehouseId: 1, reason: '采购入库', refNo: 'WH-TC1-001',
        });

        assertEqual(r.success, true, 'WSTC1 入库应成功');
        assertEqual(r.operation, 'inbound', 'WSTC1 operation');
        assertEqual(r.details.totalQty, 5, 'WSTC1 totalQty=5');
        assertEqual(r.details.aiVerificationRate, 0.96, 'WSTC1 AI验货准确率=96%');
        assertIncludes(r.asyncOps, 'inbound_order', 'WSTC1 异步写入入库单');
        assertIncludes(r.asyncOps, 'blockchain_notarize', 'WSTC1 区块链存证');

        // 验证库存已增加
        const db1 = WarehouseService.getMockDB();
        const afterStock = db1.inventory_stock.find(s => s.warehouse_id === 1 && s.product_id === 1).stock_qty;
        assertEqual(afterStock, 105, 'WSTC1 入库后库存=105');

        // 验证入库单和流水已生成
        assertEqual(db1.inbound_orders.length, 1, 'WSTC1 入库单数=1');
        assertEqual(db1.stock_movements.length, 1, 'WSTC1 库存流水数=1');
        assertEqual(db1.stock_movements[0].movement_type, 'inbound', 'WSTC1 流水类型=inbound');

        // 验证事务日志(BEGIN+COMMIT)
        const begins = db1.tx_log.filter(l => l.type === 'BEGIN').length;
        const commits = db1.tx_log.filter(l => l.type === 'COMMIT').length;
        assertEqual(begins, 1, 'WSTC1 BEGIN数=1');
        assertEqual(commits, 1, 'WSTC1 COMMIT数=1');

        emit('  ✓ WSTC1 AI智能入库: 100→105, 入库单+流水+区块链存证, 验货准确率96%', 'pass');
    }

    // WSTC2: AI智能出库
    async function WSTC2_outbound() {
        await setup();
        const r = await WarehouseService.outbound({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 3 }],
            warehouseId: 1, reason: '订单出库', refNo: 'WH-TC2-001',
        });

        assertEqual(r.success, true, 'WSTC2 出库应成功');
        assertEqual(r.operation, 'outbound', 'WSTC2 operation');
        assertEqual(r.details.totalQty, 3, 'WSTC2 totalQty=3');
        assertEqual(r.details.pickingEfficiencyGain, 0.50, 'WSTC2 拣选效率提升=50%');
        assertIncludes(r.asyncOps, 'outbound_order', 'WSTC2 异步写出库单');

        const db1 = WarehouseService.getMockDB();
        const afterStock = db1.inventory_stock.find(s => s.warehouse_id === 1 && s.product_id === 1).stock_qty;
        assertEqual(afterStock, 97, 'WSTC2 出库后库存=97');
        assertEqual(db1.outbound_orders.length, 1, 'WSTC2 出库单数=1');
        assertEqual(db1.stock_movements.length, 1, 'WSTC2 流水数=1');
        assertEqual(db1.stock_movements[0].movement_type, 'outbound', 'WSTC2 流水类型=outbound');

        emit('  ✓ WSTC2 AI智能出库: 100→97, 出库单+流水, 拣选效率提升50%', 'pass');
    }

    // WSTC3: AI智能出库-库存不足(验证回滚)
    async function WSTC3_outboundStockout() {
        await setup();
        const r = await WarehouseService.outbound({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 999 }],
            warehouseId: 1, reason: '超量出库',
        });

        assertEqual(r.success, false, 'WSTC3 库存不足应失败');
        assert(!!r.error, 'WSTC3 应返回错误信息');
        assert(!!r.failedStage, 'WSTC3 应记录失败阶段');

        // 验证库存未变(事务回滚)
        const db1 = WarehouseService.getMockDB();
        const afterStock = db1.inventory_stock.find(s => s.warehouse_id === 1 && s.product_id === 1).stock_qty;
        assertEqual(afterStock, 100, 'WSTC3 库存应保持100(回滚)');

        // 验证事务原子性: BEGIN === COMMIT + ROLLBACK
        const begins = db1.tx_log.filter(l => l.type === 'BEGIN').length;
        const commits = db1.tx_log.filter(l => l.type === 'COMMIT').length;
        const rollbacks = db1.tx_log.filter(l => l.type === 'ROLLBACK').length;
        assertEqual(begins, commits + rollbacks, 'WSTC3 事务原子性: BEGIN === COMMIT + ROLLBACK');
        assertEqual(rollbacks, 1, 'WSTC3 ROLLBACK数=1');

        emit('  ✓ WSTC3 AI智能出库-库存不足: 库存保持100, 事务回滚, 原子性校验通过', 'pass');
    }

    // WSTC4: AI智能盘点
    async function WSTC4_stocktake() {
        await setup();
        const r = await WarehouseService.stocktake({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', actualQty: 98 }],
            warehouseId: 1, method: 'drone_ai', refNo: 'WH-TC4-001',
        });

        assertEqual(r.success, true, 'WSTC4 盘点应成功');
        assertEqual(r.operation, 'stocktake', 'WSTC4 operation');
        assertEqual(r.details.aiAccuracy, 0.98, 'WSTC4 AI盘点准确率=98%');
        assertEqual(r.details.method, 'drone_ai', 'WSTC4 盘点方法=无人机AI');
        assertEqual(r.details.deficitQty, 2, 'WSTC4 盘亏数量=2');
        assertEqual(r.details.surplusQty, 0, 'WSTC4 盘盈数量=0');

        const db1 = WarehouseService.getMockDB();
        const afterStock = db1.inventory_stock.find(s => s.warehouse_id === 1 && s.product_id === 1).stock_qty;
        assertEqual(afterStock, 98, 'WSTC4 盘点后库存=98(实际值)');
        assertEqual(db1.stocktaking_records.length, 1, 'WSTC4 盘点记录数=1');
        assertEqual(db1.stocktaking_records[0].method, 'drone_ai', 'WSTC4 盘点记录方法=无人机AI');

        emit('  ✓ WSTC4 AI智能盘点: 100→98(盘亏2), 无人机AI方法, 准确率98%', 'pass');
    }

    // WSTC5: AI智能库位优化
    async function WSTC5_slotOptimize() {
        await setup();
        const r = await WarehouseService.slotOptimize({ warehouseId: 1 });

        assertEqual(r.success, true, 'WSTC5 库位优化应成功');
        assertEqual(r.operation, 'slotOptimize', 'WSTC5 operation');
        assertEqual(r.details.optimizationGain, 0.30, 'WSTC5 库位利用率提升=30%');
        assert(r.details.relocatedItems.length > 0, 'WSTC5 应有库位重排记录');
        assert(r.details.hotZoneCount > 0, 'WSTC5 应有 hot 区商品');
        assert(r.details.warmZoneCount > 0, 'WSTC5 应有 warm 区商品');
        assert(r.details.coldZoneCount > 0, 'WSTC5 应有 cold 区商品');

        const db1 = WarehouseService.getMockDB();
        assertEqual(db1.ai_optimization_records.length, 1, 'WSTC5 AI优化记录数=1');
        assertEqual(db1.ai_optimization_records[0].optimization_type, 'slot_optimization', 'WSTC5 优化类型=库位优化');

        emit('  ✓ WSTC5 AI智能库位优化: hot/warm/cold分区+ABC分类, 提升30%, 优化记录已生成', 'pass');
    }

    // WSTC6: AI智能库存预测
    async function WSTC6_forecast() {
        await setup();
        const r = WarehouseService.forecast({ productId: 1, warehouseId: 1, horizonDays: 30 });

        assertEqual(r.success, true, 'WSTC6 预测应成功');
        assertEqual(r.operation, 'forecast', 'WSTC6 operation');
        assertEqual(r.details.aiAccuracy, 0.89, 'WSTC6 AI预测准确率=89%');
        assertEqual(r.details.aiModel, 'LSTM', 'WSTC6 AI模型=LSTM');
        assertEqual(r.details.horizonDays, 30, 'WSTC6 预测周期=30天');
        assert(r.details.forecastedDemand > 0, 'WSTC6 预测需求应>0');
        assert(r.details.daysOfSupply > 0, 'WSTC6 库存可供应天数应>0');
        assert(typeof r.details.seasonFactor === 'number', 'WSTC6 应有季节性因子');

        emit('  ✓ WSTC6 AI智能库存预测: LSTM模型, 准确率89%, 周期30天, 含季节性因子', 'pass');
    }

    // WSTC7: AI智能安全库存
    async function WSTC7_safetyStock() {
        await setup();
        const r = WarehouseService.safetyStock({ productId: 1, warehouseId: 1 });

        assertEqual(r.success, true, 'WSTC7 安全库存计算应成功');
        assertEqual(r.operation, 'safetyStock', 'WSTC7 operation');
        assertEqual(r.details.turnoverImprovement, 0.25, 'WSTC7 库存周转提升=25%');
        assertEqual(r.details.serviceLevel, '95%', 'WSTC7 服务水平=95%');
        assert(r.details.aiRecommendedSafety > 0, 'WSTC7 AI推荐安全库存应>0');
        assert(r.details.reorderPoint > r.details.aiRecommendedSafety, 'WSTC7 补货点应>安全库存');
        assertEqual(r.details.leadTime, 7, 'WSTC7 提前期=7天');

        // 验证库存表的 AI 推荐安全库存已更新
        const db1 = WarehouseService.getMockDB();
        const stock = db1.inventory_stock.find(s => s.warehouse_id === 1 && s.product_id === 1);
        assertEqual(stock.ai_recommended_safety, r.details.aiRecommendedSafety, 'WSTC7 库存表AI安全库存已更新');

        emit('  ✓ WSTC7 AI智能安全库存: 动态安全库存+补货点, 周转提升25%, 服务水平95%', 'pass');
    }

    // WSTC8: AI智能温湿度监控
    async function WSTC8_envMonitor() {
        await setup();
        const r = WarehouseService.envMonitor({ warehouseId: 1 });

        assertEqual(r.success, true, 'WSTC8 温湿度监控应成功');
        assertEqual(r.operation, 'envMonitor', 'WSTC8 operation');
        assertEqual(r.details.aiAnomalyDetection, 0.95, 'WSTC8 AI异常发现率=95%');
        assert(typeof r.details.temp === 'number', 'WSTC8 应返回温度值');
        assert(typeof r.details.humidity === 'number', 'WSTC8 应返回湿度值');
        assert(Array.isArray(r.details.tempRange), 'WSTC8 应返回温度合规区间');
        assert(Array.isArray(r.details.humidityRange), 'WSTC8 应返回湿度合规区间');
        assert(Array.isArray(r.details.agedStocks), 'WSTC8 应返回酒龄管理数据');
        assert(r.details.warehouseName, 'WSTC8 应返回仓库名称');

        const db1 = WarehouseService.getMockDB();
        assertEqual(db1.environment_monitoring.length, 1, 'WSTC8 监控记录数=1');

        emit('  ✓ WSTC8 AI智能温湿度监控: IoT读数+异常发现率95%, 酒龄管理+监控记录已生成', 'pass');
    }

    // WSTC9: AI智能多仓协同
    async function WSTC9_multiTransfer() {
        await setup();
        const r = await WarehouseService.multiTransfer({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 10 }],
            fromWarehouseId: 1, toWarehouseId: 2, reason: '区域仓补货', refNo: 'WH-TC9-001',
        });

        assertEqual(r.success, true, 'WSTC9 多仓调拨应成功');
        assertEqual(r.operation, 'multiTransfer', 'WSTC9 operation');
        assertEqual(r.details.totalQty, 10, 'WSTC9 totalQty=10');
        assertEqual(r.details.transferTimeliness, 0.92, 'WSTC9 AI调拨及时率=92%');
        assertIncludes(r.asyncOps, 'transfer_order', 'WSTC9 异步写调拨单');
        assertIncludes(r.asyncOps, 'stock_movement', 'WSTC9 异步写双向流水');

        const db1 = WarehouseService.getMockDB();
        const fromStock = db1.inventory_stock.find(s => s.warehouse_id === 1 && s.product_id === 1).stock_qty;
        const toStock = db1.inventory_stock.find(s => s.warehouse_id === 2 && s.product_id === 1);
        assertEqual(fromStock, 90, 'WSTC9 源仓库存=90(100-10)');
        assert(toStock, 'WSTC9 目标仓应有库存记录');
        assertEqual(toStock.stock_qty, 10, 'WSTC9 目标仓库存=10');
        assertEqual(db1.multi_warehouse_transfers.length, 1, 'WSTC9 调拨单数=1');
        // 双向流水: transfer_out + transfer_in
        const outMoves = db1.stock_movements.filter(m => m.movement_type === 'transfer_out').length;
        const inMoves = db1.stock_movements.filter(m => m.movement_type === 'transfer_in').length;
        assertEqual(outMoves, 1, 'WSTC9 transfer_out流水=1');
        assertEqual(inMoves, 1, 'WSTC9 transfer_in流水=1');

        emit('  ✓ WSTC9 AI智能多仓协同: 工厂仓90→区域仓10, 双向流水, 及时率92%', 'pass');
    }

    // WSTC10: AI智能损耗管理
    async function WSTC10_loss() {
        await setup();
        const r = await WarehouseService.loss({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 2, rootCause: '包装破损' }],
            warehouseId: 1, lossType: 'breakage', refNo: 'WH-TC10-001',
        });

        assertEqual(r.success, true, 'WSTC10 损耗登记应成功');
        assertEqual(r.operation, 'loss', 'WSTC10 operation');
        assertEqual(r.details.totalLossQty, 2, 'WSTC10 损耗数量=2');
        assertEqual(r.details.lossType, 'breakage', 'WSTC10 损耗类型=破损');
        assertEqual(r.details.lossReduction, 0.20, 'WSTC10 AI损耗降低=20%');
        assertIncludes(r.asyncOps, 'loss_record', 'WSTC10 异步写损耗单');
        assertIncludes(r.asyncOps, 'ai_root_cause', 'WSTC10 AI根因分析');

        const db1 = WarehouseService.getMockDB();
        const afterStock = db1.inventory_stock.find(s => s.warehouse_id === 1 && s.product_id === 1).stock_qty;
        assertEqual(afterStock, 98, 'WSTC10 损耗后库存=98(100-2)');
        assertEqual(db1.loss_records.length, 1, 'WSTC10 损耗记录数=1');
        assertEqual(db1.loss_records[0].loss_type, 'breakage', 'WSTC10 损耗记录类型=破损');

        emit('  ✓ WSTC10 AI智能损耗管理: 100→98(损耗2), 破损类型, 降低20%, 根因分析完成', 'pass');
    }

    // WSTC11: AI智能仓配一体
    async function WSTC11_crossDock() {
        await setup();
        const r = await WarehouseService.crossDock({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 8 }],
            warehouseId: 1, carrierId: 'LOGISTICS-06', refNo: 'WH-TC11-001',
        });

        assertEqual(r.success, true, 'WSTC11 越库应成功');
        assertEqual(r.operation, 'crossDock', 'WSTC11 operation');
        assertEqual(r.details.totalQty, 8, 'WSTC11 totalQty=8');
        assertEqual(r.details.crossDockRate, 0.40, 'WSTC11 AI越库率=40%');
        assertEqual(r.details.carrierId, 'LOGISTICS-06', 'WSTC11 承运商=物流模块06');
        assertIncludes(r.asyncOps, 'cross_dock_movement', 'WSTC11 异步越库流水');
        assertIncludes(r.asyncOps, 'logistics_integration', 'WSTC11 物流对接');

        const db1 = WarehouseService.getMockDB();
        // 越库: 库存不变(不入库直接分发)
        const afterStock = db1.inventory_stock.find(s => s.warehouse_id === 1 && s.product_id === 1).stock_qty;
        assertEqual(afterStock, 100, 'WSTC11 越库后库存不变=100(不入库)');
        const cdMoves = db1.stock_movements.filter(m => m.movement_type === 'cross_dock').length;
        assertEqual(cdMoves, 1, 'WSTC11 越库流水数=1');

        emit('  ✓ WSTC11 AI智能仓配一体: 越库8瓶, 库存不变(不入库), 对接物流06, 越库率40%', 'pass');
    }

    // WSTC12: 服务结构与规范验证
    async function WSTC12_serviceStructure() {
        await setup();
        // 全局暴露
        assert(typeof window.WarehouseService === 'object', 'WSTC12 window.WarehouseService 应已暴露');
        assert(typeof WarehouseService === 'object', 'WSTC12 WarehouseService 全局对象应存在');

        // 10个 AI 能力方法
        const methods = ['inbound', 'outbound', 'stocktake', 'slotOptimize', 'forecast',
            'safetyStock', 'envMonitor', 'multiTransfer', 'loss', 'crossDock'];
        for (const m of methods) {
            assert(typeof WarehouseService[m] === 'function', 'WSTC12 方法 ' + m + ' 应为函数');
        }

        // 基础方法
        assert(typeof WarehouseService.init === 'function', 'WSTC12 init 方法');
        assert(typeof WarehouseService.setMode === 'function', 'WSTC12 setMode 方法');
        assert(typeof WarehouseService.setApiBase === 'function', 'WSTC12 setApiBase 方法');
        assert(typeof WarehouseService.getMode === 'function', 'WSTC12 getMode 方法');
        assert(typeof WarehouseService.resetMock === 'function', 'WSTC12 resetMock 方法');
        assert(typeof WarehouseService.getMockDB === 'function', 'WSTC12 getMockDB 方法');

        // Mock/Live 模式切换
        const origMode = WarehouseService.getMode();
        WarehouseService.setMode('live');
        assertEqual(WarehouseService.getMode(), 'live', 'WSTC12 切换到 live 模式');
        WarehouseService.setMode(origMode);
        assertEqual(WarehouseService.getMode(), origMode, 'WSTC12 恢复 ' + origMode + ' 模式');

        // CONFIG 配置(10个AI能力的核心指标)
        const c = WarehouseService.CONFIG;
        assertEqual(c.AI_INBOUND_ACCURACY, 0.96, 'WSTC12 AI入库验货准确率=96%');
        assertEqual(c.AI_PICKING_EFFICIENCY_GAIN, 0.50, 'WSTC12 AI出库拣选效率提升=50%');
        assertEqual(c.AI_STOCKTAKE_ACCURACY, 0.98, 'WSTC12 AI盘点准确率=98%');
        assertEqual(c.AI_SLOT_OPTIMIZATION_GAIN, 0.30, 'WSTC12 AI库位优化提升=30%');
        assertEqual(c.AI_FORECAST_ACCURACY, 0.89, 'WSTC12 AI预测准确率=89%');
        assertEqual(c.AI_TURNOVER_IMPROVEMENT, 0.25, 'WSTC12 AI周转提升=25%');
        assertEqual(c.AI_ENV_ANOMALY_DETECTION, 0.95, 'WSTC12 AI温湿度异常发现率=95%');
        assertEqual(c.AI_TRANSFER_TIMELINESS, 0.92, 'WSTC12 AI调拨及时率=92%');
        assertEqual(c.AI_LOSS_REDUCTION, 0.20, 'WSTC12 AI损耗降低=20%');
        assertEqual(c.AI_CROSS_DOCK_RATE, 0.40, 'WSTC12 AI越库率=40%');

        // Mock DB 结构(12张表)
        const db = WarehouseService.getMockDB();
        const tables = ['warehouses', 'warehouse_locations', 'inventory_stock',
            'inbound_orders', 'outbound_orders', 'stock_movements', 'stocktaking_records',
            'environment_monitoring', 'loss_records', 'multi_warehouse_transfers',
            'ai_optimization_records', 'ai_compliance_monitoring', 'tx_log'];
        for (const t of tables) {
            assert(Array.isArray(db[t]), 'WSTC12 表 ' + t + ' 应为数组');
        }
        assert(db.warehouses.length >= 4, 'WSTC12 应有至少4个仓库(工厂/区域/零售/陈酿)');
        assert(db.warehouse_locations.length > 0, 'WSTC12 应有库位记录');
        assert(db.inventory_stock.length > 0, 'WSTC12 应有库存记录');

        emit('  ✓ WSTC12 服务结构规范: 全局暴露+10个AI方法+Mock/Live+CONFIG+12张表全验证', 'pass');
    }

    // ============================================================
    //  主执行函数
    // ============================================================
    async function executeTests() {
        const cases = [
            { name: 'WSTC1', fn: WSTC1_inbound },
            { name: 'WSTC2', fn: WSTC2_outbound },
            { name: 'WSTC3', fn: WSTC3_outboundStockout },
            { name: 'WSTC4', fn: WSTC4_stocktake },
            { name: 'WSTC5', fn: WSTC5_slotOptimize },
            { name: 'WSTC6', fn: WSTC6_forecast },
            { name: 'WSTC7', fn: WSTC7_safetyStock },
            { name: 'WSTC8', fn: WSTC8_envMonitor },
            { name: 'WSTC9', fn: WSTC9_multiTransfer },
            { name: 'WSTC10', fn: WSTC10_loss },
            { name: 'WSTC11', fn: WSTC11_crossDock },
            { name: 'WSTC12', fn: WSTC12_serviceStructure },
        ];

        const results = [];
        for (const c of cases) {
            emit('▶ ' + c.name + ' 运行中...', 'info');
            const r = await runOne(c.name, c.fn);
            results.push(r);
            if (r.status === 'PASS') {
                emit('  ' + c.name + ' PASS (' + r.duration + 'ms)', 'pass');
            } else {
                emit('  ' + c.name + ' FAIL: ' + r.error, 'fail');
            }
        }

        const passed = results.filter(r => r.status === 'PASS').length;
        const total = results.length;
        const rate = ((passed / total) * 100).toFixed(1);

        const report = {
            module: '28-service',
            moduleName: 'AI智能仓储服务API',
            results: results.map(r => ({ id: r.name, name: r.name, pass: r.status === 'PASS', msg: r.error || '通过', duration: r.duration })),
            passed, total, rate,
            timestamp: new Date().toISOString(),
        };

        window.__lastWarehouseServiceTestReport = report;
        return report;
    }

    // ---------- 全局暴露 ----------
    window.runWarehouseServiceTest = async function (opts) {
        _sink = (opts && opts.sink) || null;
        emit('==========================================', 'info');
        emit('  模块28服务API: AI智能仓储服务 回归测试', 'info');
        emit('==========================================', 'info');
        const report = await executeTests();
        emit('------------------------------------------', 'info');
        emit('  通过: ' + report.passed + '/' + report.total + ' (' + report.rate + '%)', report.passed === report.total ? 'pass' : 'warn');
        emit('==========================================', 'info');
        return report;
    };

    window.__runWarehouseServiceTestPromise = function () {
        return window.runWarehouseServiceTest();
    };

    console.log('✅ warehouse-service-test.js 已加载 (12用例, WSTC1-WSTC12)');

})();
