/* ============================================
   竹香酒官网 · 库存扣减服务 - 回归测试脚本
   --------------------------------------------
   用途: 每次修改 inventory-service.js 或其依赖
         (toolkit/upgrade-logger.js、toolkit/transaction-template.js)
         后, 运行本脚本确保库存扣减/回补事务流程未被破坏
   --------------------------------------------
   4 个测试用例:
     TC1  正常扣减   qty=2         (验证事务+库存-2+流水(出库)+无预警+异步)
     TC2  库存不足   qty=999       (验证阶段3抛错+快照回滚+事务原子性)
     TC3  库存回补   restock qty=5 (验证逆操作+库存+5+流水(入库))
     TC4  低库存预警 qty=95→余5   (验证 0<余量≤10 触发预警记录)
   --------------------------------------------
   运行方式:
     · 浏览器: 在 module-test.html 点击「库存管理一键回归」按钮
     · 控制台: runInventoryRegression()
     · Headless: window.__runInventoryRegressionPromise
   ============================================ */

(function () {
    'use strict';

    // ---------- 断言工具 ----------
    function assert(cond, message) {
        if (!cond) throw new Error('断言失败: ' + message);
    }
    function assertEqual(actual, expected, message) {
        if (actual !== expected) {
            throw new Error((message || '断言失败') + ` (期望 ${JSON.stringify(expected)}, 实际 ${JSON.stringify(actual)})`);
        }
    }
    function assertIncludes(arr, item, message) {
        if (!arr.some(x => (typeof x === 'string' && x.includes(item)) || x === item)) {
            throw new Error((message || '断言失败') + ` (数组中未找到 ${item})`);
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

    // ---------- 输出适配(浏览器 / 控制台) ----------
    let _sink = null;
    function emit(line, type) {
        if (_sink && typeof _sink === 'function') {
            _sink(line, type);
            return;
        }
        if (typeof document !== 'undefined' && document.getElementById) {
            const logEl = document.getElementById('inventoryLog');
            if (logEl) {
                const color = type === 'pass' ? '#0f0' : type === 'fail' ? '#f88' : type === 'warn' ? '#fc0' : '#0ff';
                const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
                const entry = document.createElement('div');
                entry.style.color = color;
                entry.innerHTML = `<span style="opacity:0.6;">[${t}]</span> ${line}`;
                logEl.appendChild(entry);
                logEl.scrollTop = logEl.scrollHeight;
                return;
            }
        }
        if (typeof console !== 'undefined') console.log(line);
    }

    // ============================================================
    //  测试用例定义
    // ============================================================

    async function setup() {
        if (typeof InventoryService === 'undefined') {
            throw new Error('InventoryService 未加载,请先引入 js/inventory-service.js');
        }
        InventoryService.resetMock();
    }

    // TC1: 正常扣减
    async function TC1_normalDeduct() {
        await setup();
        const db0 = InventoryService.getMockDB();
        assertEqual(db0.products[0].stock, 100, 'TC1 初始库存应为100');
        assertEqual(db0.inventory_logs.length, 0, 'TC1 初始无流水');

        const r = await InventoryService.deduct({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 2 }],
            reason: '订单出库',
            refNo: 'ZX-TC1-001',
        });

        assertEqual(r.success, true, 'TC1 扣减应成功');
        assertEqual(r.operation, 'deduct', 'TC1 operation');
        assertEqual(r.details.totalQty, 2, 'TC1 totalQty应为2');
        assertEqual(r.details.alertsTriggered, 0, 'TC1 不应触发预警(余量98>10)');
        assertEqual(r.details.reason, '订单出库', 'TC1 reason');

        // 验证异步任务
        assert(r.asyncOps && r.asyncOps.length === 2, `TC1 异步任务应为2个, 实际 ${r.asyncOps ? r.asyncOps.length : 0}`);
        assertIncludes(r.asyncOps, 'inventory_notify', 'TC1 库存通知任务');
        assertIncludes(r.asyncOps, 'blockchain_notarize', 'TC1 区块链存证任务');

        // 验证关键阶段
        const steps = r.logs.map(l => l.step);
        const requiredStages = ['阶段1-参数校验', '阶段2-开启事务', '阶段3-库存扣减', '阶段4-事务提交'];
        const missing = requiredStages.filter(s => !steps.some(g => g.includes(s)));
        assert(missing.length === 0, `TC1 缺失事务阶段: ${missing.join(',')}`);

        // 验证数据库
        const db1 = InventoryService.getMockDB();
        const product1 = db1.products.find(p => p.id === 1);
        assertEqual(product1.stock, 98, 'TC1 数据库: 库存应为100-2=98');

        // 验证流水(出库)
        assertEqual(db1.inventory_logs.length, 1, 'TC1 数据库: 应有1条流水');
        const flow = db1.inventory_logs[0];
        assertEqual(flow.type, '出库', 'TC1 流水类型应为出库');
        assertEqual(flow.qty, 2, 'TC1 流水数量');
        assertEqual(flow.before, 100, 'TC1 流水 before');
        assertEqual(flow.after, 98, 'TC1 流水 after');
        assertEqual(flow.ref_no, 'ZX-TC1-001', 'TC1 流水 ref_no');
        assertEqual(flow.reason, '订单出库', 'TC1 流水 reason');

        // 验证无预警
        assertEqual(db1.stock_alerts.length, 0, 'TC1 数据库: 不应有预警');

        // 验证事务日志: BEGIN + COMMIT, 无 ROLLBACK
        const txTypes = db1.tx_log.map(t => t.type);
        assertIncludes(txTypes, 'BEGIN', 'TC1 数据库: tx_log 应有 BEGIN');
        assertIncludes(txTypes, 'COMMIT', 'TC1 数据库: tx_log 应有 COMMIT');
        assert(!txTypes.includes('ROLLBACK'), 'TC1 数据库: tx_log 不应有 ROLLBACK');
    }

    // TC2: 库存不足触发回滚
    async function TC2_rollbackOnInsufficientStock() {
        await setup();
        const db0 = InventoryService.getMockDB();
        const beforeStock = db0.products[0].stock;

        const r = await InventoryService.deduct({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 999 }],
            reason: '库存不足测试',
        });

        // 验证扣减失败
        assertEqual(r.success, false, 'TC2 扣减应失败');
        assert(r.error && r.error.includes('库存不足'), `TC2 错误信息应包含"库存不足", 实际: ${r.error}`);
        assertEqual(r.failedStage, '阶段3-库存扣减', 'TC2 失败阶段应为阶段3');

        // 验证回滚日志
        const rollbackLogs = r.logs.filter(l => l.level === 'ERROR' && l.step === '回滚');
        assert(rollbackLogs.length > 0, 'TC2 应有 ERROR 级别回滚日志');

        // 验证事务前阶段已执行(1,2)
        const steps = r.logs.map(l => l.step);
        assertIncludes(steps, '阶段2-开启事务', 'TC2 应执行阶段2(开启事务)');

        // ★ 关键: 验证事务原子性(快照恢复)
        const db1 = InventoryService.getMockDB();
        const afterProduct = db1.products.find(p => p.id === 1);
        assertEqual(afterProduct.stock, beforeStock, 'TC2 数据库: 库存应恢复(原子性)');

        // 验证流水未持久化
        assertEqual(db1.inventory_logs.length, 0, 'TC2 数据库: 流水不应持久化(回滚)');
        assertEqual(db1.stock_alerts.length, 0, 'TC2 数据库: 预警不应持久化(回滚)');

        // 验证 tx_log 有 ROLLBACK
        // 注: BEGIN 不留存(begin()在快照之后推入live db,rollback用快照覆盖故只剩ROLLBACK)
        const txTypes = db1.tx_log.map(t => t.type);
        assertIncludes(txTypes, 'ROLLBACK', 'TC2 数据库: tx_log 应有 ROLLBACK');
    }

    // TC3: 库存回补(退货入库)
    async function TC3_restock() {
        await setup();
        const r = await InventoryService.restock({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 5 }],
            reason: '退货入库',
            refNo: 'RX-TC3-001',
        });

        assertEqual(r.success, true, 'TC3 回补应成功');
        assertEqual(r.operation, 'restock', 'TC3 operation');
        assertEqual(r.details.totalQty, 5, 'TC3 totalQty应为5');
        assertEqual(r.details.reason, '退货入库', 'TC3 reason');

        // 验证异步任务(restock 仅 1 个通知任务)
        assert(r.asyncOps && r.asyncOps.length === 1, `TC3 异步任务应为1个, 实际 ${r.asyncOps ? r.asyncOps.length : 0}`);
        assertIncludes(r.asyncOps, 'inventory_notify', 'TC3 库存通知任务');

        // 验证数据库
        const db = InventoryService.getMockDB();
        const product = db.products.find(p => p.id === 1);
        assertEqual(product.stock, 105, 'TC3 数据库: 库存应为100+5=105');

        // 验证流水(入库)
        assertEqual(db.inventory_logs.length, 1, 'TC3 数据库: 应有1条流水');
        const flow = db.inventory_logs[0];
        assertEqual(flow.type, '入库', 'TC3 流水类型应为入库');
        assertEqual(flow.qty, 5, 'TC3 流水数量');
        assertEqual(flow.before, 100, 'TC3 流水 before');
        assertEqual(flow.after, 105, 'TC3 流水 after');
        assertEqual(flow.ref_no, 'RX-TC3-001', 'TC3 流水 ref_no');

        // 验证事务日志: BEGIN + COMMIT
        const txTypes = db.tx_log.map(t => t.type);
        assertIncludes(txTypes, 'BEGIN', 'TC3 数据库: tx_log 应有 BEGIN');
        assertIncludes(txTypes, 'COMMIT', 'TC3 数据库: tx_log 应有 COMMIT');
    }

    // TC4: 低库存预警
    async function TC4_lowStockAlert() {
        await setup();
        // 扣减 95,余量 5,触发低库存预警(0 < 5 ≤ 10)
        const r = await InventoryService.deduct({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', qty: 95 }],
            reason: '大批量出库',
        });

        assertEqual(r.success, true, 'TC4 扣减应成功');
        assertEqual(r.details.totalQty, 95, 'TC4 totalQty应为95');
        assertEqual(r.details.alertsTriggered, 1, 'TC4 应触发1次预警');

        // 验证数据库
        const db = InventoryService.getMockDB();
        const product = db.products.find(p => p.id === 1);
        assertEqual(product.stock, 5, 'TC4 数据库: 库存应为100-95=5');

        // 验证预警记录
        assertEqual(db.stock_alerts.length, 1, 'TC4 数据库: 应有1条预警');
        const alert = db.stock_alerts[0];
        assertEqual(alert.product_id, 1, 'TC4 预警 product_id');
        assertEqual(alert.stock, 5, 'TC4 预警 stock');
        assertEqual(alert.threshold, 10, 'TC4 预警 threshold');
        assertEqual(alert.level, '低库存', 'TC4 预警 level应为低库存');

        // 验证流水仍正常写入
        assertEqual(db.inventory_logs.length, 1, 'TC4 数据库: 应有1条流水');
        assertEqual(db.inventory_logs[0].after, 5, 'TC4 流水 after');

        // 验证日志中有 WARN 级别预警
        const warnLogs = r.logs.filter(l => l.level === 'WARN' && l.step === '阶段3-库存扣减');
        assert(warnLogs.length > 0, 'TC4 应有 WARN 级别预警日志');
    }

    // ============================================================
    //  主入口
    // ============================================================

    async function runInventoryRegression(opts) {
        const options = opts || {};
        _sink = options.sink || null;

        const out = [];
        const sep = '═'.repeat(70);
        out.push(sep);
        out.push('  竹香酒官网 · 库存扣减服务 - 回归测试');
        out.push('  日期: ' + new Date().toISOString().slice(0, 19).replace('T', ' '));
        out.push('  目标: js/inventory-service.js → InventoryService');
        out.push(sep);
        out.forEach(l => emit(l, 'info'));
        if (_sink) out.length = 0; else out.push('');

        const cases = [
            { name: 'TC1 正常扣减: qty=2   (事务+库存-2+流水(出库)+异步)',  fn: TC1_normalDeduct },
            { name: 'TC2 库存不足: qty=999 (阶段3抛错+快照回滚+原子性)',     fn: TC2_rollbackOnInsufficientStock },
            { name: 'TC3 库存回补: qty=5   (逆操作+库存+5+流水(入库))',     fn: TC3_restock },
            { name: 'TC4 低库存预警: 余5   (0<余量≤10触发预警记录)',        fn: TC4_lowStockAlert },
        ];

        const results = [];
        let passed = 0, failed = 0;
        for (const c of cases) {
            emit('──────────────────────────────────────────────────────────', 'info');
            emit('▶ 运行: ' + c.name, 'info');
            const r = await runOne(c.name, c.fn);
            results.push(r);
            if (r.status === 'PASS') {
                passed++;
                emit('  ✓ PASS (' + r.duration + 'ms)', 'pass');
            } else {
                failed++;
                emit('  ✗ FAIL (' + r.duration + 'ms)', 'fail');
                emit('    错误: ' + r.error, 'fail');
            }
        }

        emit('', 'info');
        emit(sep, 'info');
        const allPassed = failed === 0;
        const summary = `  回归测试${allPassed ? '全部通过' : '存在失败'}: ${passed}/${cases.length} PASS, ${failed} FAIL`;
        emit(summary, allPassed ? 'pass' : 'fail');
        emit(sep, allPassed ? 'pass' : 'fail');

        // 详细报告
        emit('', 'info');
        emit('详细报告:', 'info');
        results.forEach(r => {
            const icon = r.status === 'PASS' ? '✓' : '✗';
            const type = r.status === 'PASS' ? 'pass' : 'fail';
            emit(`  ${icon} ${r.name} [${r.duration}ms]${r.error ? ' - ' + r.error : ''}`, type);
        });

        // 数据库最终状态
        emit('', 'info');
        emit('Mock 数据库最终状态:', 'info');
        const db = InventoryService.getMockDB();
        db.products.slice(0, 6).forEach(p => {
            const alert = db.stock_alerts.find(a => a.product_id === p.id);
            emit(`  [${p.id}] ${p.name} | 库存 ${p.stock}${alert ? ` | ⚠预警(${alert.level})` : ''}`, 'info');
        });
        emit(`  库存流水: ${db.inventory_logs.length}条`, 'info');
        emit(`  预警记录: ${db.stock_alerts.length}条`, 'info');
        emit(`  事务日志: ${db.tx_log.length}条 (含 BEGIN/COMMIT/ROLLBACK)`, 'info');

        const report = {
            timestamp: new Date().toISOString(),
            total: cases.length,
            passed,
            failed,
            passRate: ((passed / cases.length) * 100).toFixed(1) + '%',
            results,
            success: allPassed,
        };

        if (typeof window !== 'undefined') {
            window.__lastInventoryRegressionReport = report;
        }
        return report;
    }

    // ---------- 暴露 ----------
    if (typeof window !== 'undefined') {
        window.runInventoryRegression = runInventoryRegression;
        window.__runInventoryRegressionPromise = runInventoryRegression;
    }
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { runInventoryRegression };
    }
})();
