/**
 * example-usage.js  ·  工具包使用示例
 * ============================================================
 * 演示如何用 toolkit/ 下三个工具搭建一个完整的多阶段事务流程
 * 并配以回归测试,作为其他模块迁移到工具包的参考模板。
 *
 * 演示业务: 简化版"订单结算"事务(库存扣减 + 优惠券核销 + 积分入账)
 *
 * 运行方式:
 *   · Node:   node toolkit/example-usage.js
 *   · 浏览器: <script src="upgrade-logger.js"></script>
 *            <script src="transaction-template.js"></script>
 *            <script src="regression-test-kit.js"></script>
 *            <script src="example-usage.js"></script>
 *            调用 runDemo() 即可
 * ============================================================
 */

(function (root, factory) {
    'use strict';
    // 自动加载依赖(三种环境)
    const UpgradeLogger = root.UpgradeLogger
        || (typeof window !== 'undefined' && window.UpgradeLogger)
        || (typeof require === 'function' ? require('./upgrade-logger.js').UpgradeLogger : null);
    const TransactionTemplate = root.TransactionTemplate
        || (typeof window !== 'undefined' && window.TransactionTemplate)
        || (typeof require === 'function' ? require('./transaction-template.js').TransactionTemplate : null);
    const RegressionTestKit = root.RegressionTestKit
        || (typeof window !== 'undefined' && window.RegressionTestKit)
        || (typeof require === 'function' ? require('./regression-test-kit.js').RegressionTestKit : null);

    const demo = factory(UpgradeLogger, TransactionTemplate, RegressionTestKit);

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = demo;
    }
    if (typeof root !== 'undefined') root.runDemo = demo;
    if (typeof window !== 'undefined') window.runDemo = demo;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (UpgradeLogger, TransactionTemplate, RegressionTestKit) {
    'use strict';

    // ============================================================
    //  Mock 数据库(仅演示,生产请替换为真实 db 模块)
    // ============================================================
    function createMockDB() {
        return {
            products: [
                { id: 1, name: '竹香经典 45°', stock: 100, price: 268 },
                { id: 2, name: '竹韵佳酿 42°', stock: 50, price: 368 },
            ],
            coupons: [
                { id: 'C001', code: 'NEW10', discount: 0.10, status: '未使用' },
                { id: 'C002', code: 'SVIP20', discount: 0.20, status: '未使用' },
            ],
            members: [
                { id: 1, name: '张三', points: 500, level: 'L3' },
                { id: 2, name: '李四', points: 1200, level: 'L5' },
            ],
            orders: [],
            tx_log: [], // 事务日志: BEGIN/COMMIT/ROLLBACK
        };
    }

    let db = createMockDB();

    // ============================================================
    //  事务适配器(Mock 模式 - 基于快照回滚)
    // ============================================================
    const mockAdapter = {
        // 开启事务: 拍快照 + 记 BEGIN
        begin(ctx) {
            const snapshot = JSON.parse(JSON.stringify(db));
            db.tx_log.push({ type: 'BEGIN', time: new Date().toISOString() });
            ctx.logger.info('阶段2-开启事务', '事务已开启(快照已建立)', {
                txLogLen: db.tx_log.length,
            });
            return snapshot;
        },
        // 提交: 记 COMMIT + 持久化
        commit(snapshot, ctx) {
            db.tx_log.push({ type: 'COMMIT', time: new Date().toISOString(), steps: 3 });
            // Mock 持久化: 快照丢弃,当前 db 即为最终状态
            ctx.logger.info('阶段4-提交', '事务提交成功(快照丢弃)');
        },
        // 回滚: 用快照覆盖当前 db + 记 ROLLBACK
        rollback(snapshot, ctx) {
            if (snapshot) {
                snapshot.tx_log.push({ type: 'ROLLBACK', time: new Date().toISOString() });
                db = snapshot;
                ctx.logger.info('回滚', '快照已恢复(所有修改撤销)');
            }
        },
    };

    // ============================================================
    //  构造订单结算事务模板
    // ============================================================
    function buildOrderTemplate() {
        return new TransactionTemplate({
            name: 'order_settlement',
            adapter: mockAdapter,
        });
    }

    // 业务流程函数
    async function settleOrder({ orderId, memberId, productId, qty, couponCode }) {
        const template = buildOrderTemplate();
        return await template.run({
            context: { orderId, memberId, productId, qty, couponCode },
            preflight: async (ctx) => {
                ctx.logger.info('preflight', '参数校验', { orderId, memberId, productId });
                if (!db.products.find(p => p.id === ctx.productId)) {
                    return { abort: true, reason: '商品不存在' };
                }
                if (ctx.qty <= 0) return { abort: true, reason: '数量必须>0' };
            },
            stages: [
                // 阶段2: 开启事务(由 adapter 处理)
                {
                    name: '阶段2-开启事务',
                    action: async (ctx) => {
                        ctx.conn = await ctx.template.adapter.begin(ctx);
                    },
                },
                // 阶段3: 库存扣减
                {
                    name: '阶段3-库存扣减',
                    action: async (ctx) => {
                        const p = db.products.find(x => x.id === ctx.productId);
                        if (p.stock < ctx.qty) {
                            throw new Error(`库存不足: 需要${ctx.qty}, 现有${p.stock}`);
                        }
                        p.stock -= ctx.qty;
                        ctx.subtotal = p.price * ctx.qty;
                        ctx.logger.info('阶段3-库存扣减', '库存已扣减', {
                            product: p.name, remaining: p.stock, subtotal: ctx.subtotal,
                        });
                    },
                },
                // 阶段4: 优惠券核销
                {
                    name: '阶段4-优惠券核销',
                    skip: (ctx) => !ctx.couponCode,
                    action: async (ctx) => {
                        const c = db.coupons.find(x => x.code === ctx.couponCode && x.status === '未使用');
                        if (!c) throw new Error('优惠券无效');
                        c.status = '已使用';
                        ctx.discount = ctx.subtotal * c.discount;
                        ctx.logger.info('阶段4-优惠券核销', '优惠券已核销', {
                            code: c.code, discount: ctx.discount,
                        });
                    },
                },
                // 阶段5: 积分入账(等级加成)
                {
                    name: '阶段5-积分入账',
                    action: async (ctx) => {
                        const m = db.members.find(x => x.id === ctx.memberId);
                        const earn = Math.floor((ctx.subtotal - (ctx.discount || 0)) / 10);
                        const levelBoost = m.level === 'L5' ? 1.08 : m.level === 'L4' ? 1.05 : 1;
                        const gained = Math.floor(earn * levelBoost);
                        m.points += gained;
                        ctx.pointsEarned = gained;
                        db.orders.push({
                            id: ctx.orderId, member_id: ctx.memberId,
                            subtotal: ctx.subtotal, discount: ctx.discount || 0,
                            points: gained, time: new Date().toISOString(),
                        });
                        ctx.logger.info('阶段5-积分入账', '积分已入账', {
                            member: m.name, gained, total: m.points,
                        });
                    },
                },
            ],
            asyncTasks: [
                {
                    name: '阶段6-通知推送',
                    action: async (ctx) => {
                        ctx.logger.info('阶段6-通知推送', '订单确认推送已发送', {
                            orderId: ctx.orderId,
                        });
                    },
                },
            ],
        });
    }

    // 暴露 resetMock 供测试调用
    function resetMock() { db = createMockDB(); }
    function getDB() { return db; }

    // ============================================================
    //  回归测试用例(演示 RegressionTestKit 用法)
    // ============================================================
    async function runTests(opts = {}) {
        const kit = new RegressionTestKit({
            name: '竹香酒官网 · 订单结算 - 工具包演示',
            sink: typeof opts.sink === 'function' ? opts.sink : null,
        });

        const EXPECTED_STAGES = [
            'preflight', '阶段2-开启事务', '阶段3-库存扣减',
            '阶段4-优惠券核销', '阶段5-积分入账', '完成',
        ];

        return await kit.run({
            cases: [
                {
                    name: 'TC1 正常下单(含优惠券)',
                    setup: resetMock,
                    fn: async () => {
                        const r = await settleOrder({
                            orderId: 9001, memberId: 2, productId: 1,
                            qty: 2, couponCode: 'SVIP20',
                        });
                        kit.assertEqual(r.success, true, 'TC1 应成功');
                        kit.assertStages(r.logs, EXPECTED_STAGES, 'TC1 阶段完整性');
                        const d = getDB();
                        kit.assertEqual(d.products[0].stock, 98, 'TC1 库存应-2');
                        kit.assertEqual(d.coupons[1].status, '已使用', 'TC1 优惠券应已使用');
                        kit.assertEqual(d.members[1].points, 1200 + r.ctx.pointsEarned, 'TC1 积分应已入账');
                    },
                },
                {
                    name: 'TC2 无优惠券下单',
                    setup: resetMock,
                    fn: async () => {
                        const r = await settleOrder({
                            orderId: 9002, memberId: 1, productId: 2, qty: 1,
                        });
                        kit.assertEqual(r.success, true, 'TC2 应成功');
                        // 阶段4应被跳过(无 couponCode)
                        kit.assertEqual(r.ctx.discount, undefined, 'TC2 无折扣');
                    },
                },
                {
                    name: 'TC3 库存不足触发回滚',
                    setup: resetMock,
                    fn: async () => {
                        const r = await settleOrder({
                            orderId: 9003, memberId: 1, productId: 2, qty: 999,
                        });
                        kit.assertEqual(r.success, false, 'TC3 应失败');
                        kit.assert(r.error && r.error.includes('库存不足'), 'TC3 错误信息');
                        // 关键: 验证原子性(快照恢复)
                        const d = getDB();
                        kit.assertEqual(d.products[1].stock, 50, 'TC3 库存应恢复');
                        kit.assertEqual(d.orders.length, 0, 'TC3 订单不应持久化');
                        const txTypes = d.tx_log.map(t => t.type);
                        kit.assertIncludes(txTypes, 'ROLLBACK', 'TC3 tx_log 应有 ROLLBACK');
                    },
                },
                {
                    name: 'TC4 商品不存在(前置中止)',
                    setup: resetMock,
                    fn: async () => {
                        const r = await settleOrder({
                            orderId: 9004, memberId: 1, productId: 999, qty: 1,
                        });
                        kit.assertEqual(r.success, false, 'TC4 应中止');
                        kit.assertEqual(r.aborted, true, 'TC4 应标记为前置中止');
                    },
                },
            ],
        });
    }

    // ============================================================
    //  入口: 同时演示独立 Logger + 完整流程 + 回归测试
    // ============================================================
    async function runDemo(opts = {}) {
        const lines = [];
        const sink = opts.sink || ((line, type) => lines.push(`[${type}] ${line}`));

        // 1) 独立使用 UpgradeLogger(脱离事务)
        const logger = new UpgradeLogger({ prefix: 'demo', sink: (e) => sink(`[${e.level}] ${e.step} | ${e.message}`, 'info') });
        logger.info('demo', '工具包加载完成', {
            logger: !!UpgradeLogger,
            template: !!TransactionTemplate,
            kit: !!RegressionTestKit,
        });

        // 2) 运行完整流程(含成功 + 失败回滚)
        const report = await runTests({ sink });

        // 输出汇总
        sink('', 'info');
        sink(`工具包演示完成: ${report.passed}/${report.total} PASS, ${report.failed} FAIL`, report.success ? 'pass' : 'fail');

        return { report, capturedLines: lines };
    }

    return runDemo;
});
