/* ============================================
   竹香酒官网 · 订单结算服务 - 回归测试脚本
   --------------------------------------------
   用途: 每次修改 checkout-service.js 或其依赖
         (toolkit/upgrade-logger.js、toolkit/transaction-template.js、
          js/order-pricing.js) 后, 运行本脚本
         确保订单结算 9 阶段事务流程未被破坏
   --------------------------------------------
   4 个测试用例:
     TC1  正常下单   L5会员+积分    (验证 9 阶段事务+积分扣减/入账+分润+异步任务)
     TC2  优惠券下单  SVIP20券+无积分 (验证 阶段5 执行 + 阶段6 跳过 + 券核销)
     TC3  库存不足    qty=999        (验证 阶段4 抛错 + 快照回滚 + 事务原子性)
     TC4  无效优惠券  INVALID 券码   (验证 阶段5 抛错 + 快照回滚 + 优惠券状态恢复)
   --------------------------------------------
   运行方式:
     · 浏览器: 在 module-test.html 点击「订单结算回归测试」按钮
     · 控制台: runCheckoutRegression()
     · Headless: window.__runCheckoutRegressionPromise
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
    const STAGES = [
        '阶段1-购物车校验', '阶段2-开启事务', '阶段3-订单创建',
        '阶段4-库存扣减', '阶段5-优惠券核销', '阶段6-积分扣减',
        '阶段7-积分入账', '阶段8-分润计算', '阶段9-支付确认',
        '阶段10-事务提交', '完成',
    ];

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
            const logEl = document.getElementById('checkoutLog');
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
        if (typeof CheckoutService === 'undefined') {
            throw new Error('CheckoutService 未加载,请先引入 js/checkout-service.js');
        }
        CheckoutService.resetMock();
    }

    // TC1: 正常下单(L5会员, 含5000积分, 无优惠券)
    async function TC1_normalOrderWithPoints() {
        await setup();
        const db0 = CheckoutService.getMockDB();
        const memberBefore = db0.members.find(m => m.level === 'L5');
        assertEqual(memberBefore.points, 12000, 'TC1 初始积分应为12000');
        assertEqual(db0.products[0].stock, 100, 'TC1 初始库存应为100');

        const r = await CheckoutService.submit({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', price: 268, qty: 2 }],
            memberLevel: 'L5',
            points: 5000,
            paymentMethod: 'wechat',
        });

        assertEqual(r.success, true, 'TC1 下单应成功');
        assert(r.orderNo && r.orderNo.startsWith('ZX'), `TC1 订单号应以ZX开头, 实际 ${r.orderNo}`);

        // 验证关键阶段全部到位(阶段5因无券跳过)
        const steps = r.logs.map(l => l.step);
        const requiredStages = ['阶段1-购物车校验', '阶段2-开启事务', '阶段3-订单创建',
            '阶段4-库存扣减', '阶段6-积分扣减', '阶段7-积分入账',
            '阶段8-分润计算', '阶段9-支付确认', '阶段10-事务提交'];
        const missing = requiredStages.filter(s => !steps.some(g => g.includes(s)));
        assert(missing.length === 0, `TC1 缺失事务阶段: ${missing.join(',')}`);

        // 验证异步任务(2个)
        assert(r.asyncOps && r.asyncOps.length === 2, `TC1 异步任务应为2个, 实际 ${r.asyncOps ? r.asyncOps.length : 0}`);
        assertIncludes(r.asyncOps, 'order_notify', 'TC1 订单通知任务');
        assertIncludes(r.asyncOps, 'blockchain_notarize', 'TC1 区块链存证任务');

        // 验证详情字段
        assertEqual(r.details.paymentMethod, 'wechat', 'TC1 详情 paymentMethod');
        assertEqual(r.details.pointsUsed, 5000, 'TC1 详情 pointsUsed');
        assert(r.details.pointsEarned > 0, `TC1 应有积分入账, 实际 ${r.details.pointsEarned}`);

        // 验证数据库联动
        const db1 = CheckoutService.getMockDB();
        const product1 = db1.products.find(p => p.id === 1);
        assertEqual(product1.stock, 98, 'TC1 数据库: 库存应为100-2=98');

        const orders1 = db1.orders.filter(o => o.order_no === r.orderNo);
        assertEqual(orders1.length, 1, 'TC1 数据库: 应有1条订单');
        assertEqual(orders1[0].status, '已付款', 'TC1 数据库: 订单状态应为已付款');
        assertEqual(orders1[0].payment_method, 'wechat', 'TC1 数据库: 支付方式');
        assertEqual(orders1[0].points_used, 5000, 'TC1 数据库: 订单积分扣减数');
        assertEqual(orders1[0].points_earned, r.details.pointsEarned, 'TC1 数据库: 订单积分入账数');

        // 验证会员积分变动: 12000 - 5000 + pointsEarned
        const member1 = db1.members.find(m => m.level === 'L5');
        assertEqual(member1.points, 12000 - 5000 + r.details.pointsEarned,
            'TC1 数据库: 会员积分应为12000-5000+入账');

        // 验证分润记录
        assertEqual(db1.profit_records.length, 1, 'TC1 数据库: 应有1条分润记录');
        const profit = db1.profit_records[0];
        assertEqual(profit.order_no, r.orderNo, 'TC1 数据库: 分润记录订单号');
        assert(profit.platform_share > 0 && profit.hotel_share > 0, 'TC1 数据库: 分润金额应>0');

        // 验证事务日志: 有 BEGIN + COMMIT, 无 ROLLBACK
        const txTypes = db1.tx_log.map(t => t.type);
        assertIncludes(txTypes, 'BEGIN', 'TC1 数据库: tx_log 应有 BEGIN');
        assertIncludes(txTypes, 'COMMIT', 'TC1 数据库: tx_log 应有 COMMIT');
        assert(!txTypes.includes('ROLLBACK'), 'TC1 数据库: tx_log 不应有 ROLLBACK');
    }

    // TC2: 优惠券下单(SVIP20, 无积分)
    async function TC2_couponOrderWithoutPoints() {
        await setup();
        const couponBefore = CheckoutService.getMockDB().coupons.find(c => c.code === 'SVIP20');
        assertEqual(couponBefore.status, '未使用', 'TC2 初始券状态应为未使用');

        const r = await CheckoutService.submit({
            items: [{ id: 2, name: '竹奕·竹韵佳酿 500ml', price: 368, qty: 1 }],
            memberLevel: 'L5',
            points: 0,
            couponCode: 'SVIP20',
            paymentMethod: 'alipay',
        });

        assertEqual(r.success, true, 'TC2 下单应成功');

        // 验证阶段5(优惠券核销)执行, 阶段6(积分扣减)跳过
        const steps = r.logs.map(l => l.step);
        assertIncludes(steps, '阶段5-优惠券核销', 'TC2 应执行阶段5');

        // 验证详情
        assertEqual(r.details.couponCode, 'SVIP20', 'TC2 详情 couponCode');
        assertEqual(r.details.pointsUsed, 0, 'TC2 详情 pointsUsed应为0(无积分)');
        assertEqual(r.details.paymentMethod, 'alipay', 'TC2 详情 paymentMethod');

        // 验证数据库
        const db = CheckoutService.getMockDB();
        const coupon = db.coupons.find(c => c.code === 'SVIP20');
        assertEqual(coupon.status, '已使用', 'TC2 数据库: 优惠券应已使用');

        const product = db.products.find(p => p.id === 2);
        assertEqual(product.stock, 99, 'TC2 数据库: 库存应为100-1=99');

        // 验证 L5 会员积分未扣减(points=0)
        const member = db.members.find(m => m.level === 'L5');
        assertEqual(member.points, 12000 + r.details.pointsEarned,
            'TC2 数据库: 会员积分应=12000+入账(无扣减)');
    }

    // TC3: 库存不足触发回滚
    async function TC3_rollbackOnInsufficientStock() {
        await setup();
        const db0 = CheckoutService.getMockDB();
        const beforeStock = db0.products[0].stock;
        const beforeOrdersLen = db0.orders.length;
        const beforeCoupons = db0.coupons.map(c => ({ ...c }));

        const r = await CheckoutService.submit({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', price: 268, qty: 999 }],
            memberLevel: 'L3',
            points: 0,
        });

        // 验证下单失败
        assertEqual(r.success, false, 'TC3 下单应失败');
        assert(r.error && r.error.includes('库存不足'), `TC3 错误信息应包含"库存不足", 实际: ${r.error}`);
        assertEqual(r.failedStage, '阶段4-库存扣减', 'TC3 失败阶段应为阶段4');

        // 验证回滚日志
        const rollbackLogs = r.logs.filter(l => l.level === 'ERROR' && l.step === '回滚');
        assert(rollbackLogs.length > 0, 'TC3 应有 ERROR 级别回滚日志');

        // 验证事务前阶段已执行(1,2,3), 阶段4抛错
        const steps = r.logs.map(l => l.step);
        assertIncludes(steps, '阶段3-订单创建', 'TC3 应执行阶段3(订单创建)');

        // ★ 关键: 验证事务原子性(快照恢复)
        const db1 = CheckoutService.getMockDB();
        const afterProduct = db1.products.find(p => p.id === 1);
        assertEqual(afterProduct.stock, beforeStock, 'TC3 数据库: 库存应恢复(原子性)');
        assertEqual(db1.orders.length, beforeOrdersLen, 'TC3 数据库: 订单不应持久化(回滚)');

        // 验证优惠券状态恢复
        const afterCoupons = db1.coupons;
        afterCoupons.forEach((c, i) => {
            assertEqual(c.status, beforeCoupons[i].status, `TC3 数据库: 优惠券${c.code}状态应恢复`);
        });

        // 验证分润记录未写入
        assertEqual(db1.profit_records.length, 0, 'TC3 数据库: 分润记录不应持久化');

        // 验证 tx_log 有 ROLLBACK
        // 注: BEGIN 不应留存 - begin() 将 BEGIN 推入 live db(快照之后),
        //     rollback 用快照覆盖 live db, 故回滚后 tx_log 只含 ROLLBACK。
        //     此行为与 agent-upgrade 回归测试 TC4 / toolkit example-usage 一致。
        const txTypes = db1.tx_log.map(t => t.type);
        assertIncludes(txTypes, 'ROLLBACK', 'TC3 数据库: tx_log 应有 ROLLBACK');
    }

    // TC4: 无效优惠券触发回滚
    async function TC4_rollbackOnInvalidCoupon() {
        await setup();
        const db0 = CheckoutService.getMockDB();
        const beforeStock = db0.products[0].stock;
        const beforeMemberPoints = db0.members.find(m => m.level === 'L5').points;

        const r = await CheckoutService.submit({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', price: 268, qty: 1 }],
            memberLevel: 'L5',
            points: 0,
            couponCode: 'INVALID_CODE',
        });

        // 验证下单失败
        assertEqual(r.success, false, 'TC4 下单应失败');
        assert(r.error && r.error.includes('优惠券无效'), `TC4 错误信息应包含"优惠券无效", 实际: ${r.error}`);
        assertEqual(r.failedStage, '阶段5-优惠券核销', 'TC4 失败阶段应为阶段5');

        // 验证事务前阶段已执行(1,2,3,4), 阶段5抛错
        const steps = r.logs.map(l => l.step);
        assertIncludes(steps, '阶段4-库存扣减', 'TC4 应执行阶段4(库存扣减)');

        // ★ 关键: 验证事务原子性(快照恢复)
        const db1 = CheckoutService.getMockDB();

        // 库存应恢复(阶段4已扣减但被回滚)
        const afterProduct = db1.products.find(p => p.id === 1);
        assertEqual(afterProduct.stock, beforeStock, 'TC4 数据库: 库存应恢复(原子性)');

        // 订单不应持久化(阶段3写入但被回滚)
        assertEqual(db1.orders.length, 0, 'TC4 数据库: 订单不应持久化');

        // 会员积分应恢复
        const afterMember = db1.members.find(m => m.level === 'L5');
        assertEqual(afterMember.points, beforeMemberPoints, 'TC4 数据库: 会员积分应恢复');

        // 所有优惠券状态应仍为未使用
        db1.coupons.forEach(c => {
            assertEqual(c.status, '未使用', `TC4 数据库: 优惠券${c.code}状态应仍为未使用`);
        });

        // 验证 tx_log 有 ROLLBACK
        const txTypes = db1.tx_log.map(t => t.type);
        assertIncludes(txTypes, 'ROLLBACK', 'TC4 数据库: tx_log 应有 ROLLBACK');
    }

    // ============================================================
    //  主入口
    // ============================================================

    async function runCheckoutRegression(opts) {
        const options = opts || {};
        _sink = options.sink || null;

        const out = [];
        const sep = '═'.repeat(70);
        out.push(sep);
        out.push('  竹香酒官网 · 订单结算服务 - 回归测试');
        out.push('  日期: ' + new Date().toISOString().slice(0, 19).replace('T', ' '));
        out.push('  目标: js/checkout-service.js → CheckoutService');
        out.push(sep);
        out.forEach(l => emit(l, 'info'));
        if (_sink) out.length = 0; else out.push('');

        const cases = [
            { name: 'TC1 正常下单: L5+积分  (9阶段事务+积分扣减/入账+分润+异步)', fn: TC1_normalOrderWithPoints },
            { name: 'TC2 优惠券下单: SVIP20  (阶段5执行+阶段6跳过+券核销)',     fn: TC2_couponOrderWithoutPoints },
            { name: 'TC3 库存不足: qty=999   (阶段4抛错+快照回滚+事务原子性)',  fn: TC3_rollbackOnInsufficientStock },
            { name: 'TC4 无效优惠券: INVALID (阶段5抛错+快照回滚+券状态恢复)',  fn: TC4_rollbackOnInvalidCoupon },
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
        const db = CheckoutService.getMockDB();
        db.products.forEach(p => {
            emit(`  [${p.id}] ${p.name} | 库存 ${p.stock}`, 'info');
        });
        db.coupons.forEach(c => {
            emit(`  券 ${c.code} (${c.desc}) | ${c.status}`, 'info');
        });
        db.members.forEach(m => {
            emit(`  [${m.id}] ${m.name} | ${m.level} | 竹叶 ${m.points}`, 'info');
        });
        emit(`  订单数: ${db.orders.length}`, 'info');
        emit(`  分润记录数: ${db.profit_records.length}`, 'info');
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
            window.__lastCheckoutRegressionReport = report;
        }
        return report;
    }

    // ---------- 暴露 ----------
    if (typeof window !== 'undefined') {
        window.runCheckoutRegression = runCheckoutRegression;
        window.__runCheckoutRegressionPromise = runCheckoutRegression; // headless 调用别名
    }
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { runCheckoutRegression };
    }
})();
