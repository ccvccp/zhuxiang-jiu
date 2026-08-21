/* ============================================
   竹香酒官网 · 代理商区域发货服务认领 - 回归测试脚本
   --------------------------------------------
   用途: 每次修改 agent-shipping-service.js 或其依赖
         (toolkit/upgrade-logger.js、toolkit/transaction-template.js、
          js/checkout-service.js 集成点) 后, 运行本脚本
         确保认领/释放/发货路由/厂家5%服务费流程未被破坏
   --------------------------------------------
   8 个测试用例:
     ── 认领/释放/代理商发货路由(原4用例) ──
     TC1  正常认领     代理商1认领山东泰安  (验证 5阶段事务+认领记录写入+异步任务)
     TC2  重复认领拒绝 区域已被代理商1认领  (验证 阶段3抛错+快照回滚+事务原子性)
     TC3  释放认领     代理商1释放山东泰安  (验证 认领状态→已退出+区域可被重新认领)
     TC4  发货路由+服务费  认领后下单  (验证 订单发货方=代理商+厂家5%同品分润服务费计提)
     ── 厂家直供兼容场景(新增4用例) ──
     TC5  未认领区域下单   区域存在但无认领  (验证 发货方=厂家直供+不计提服务费+无服务费流水)
     TC6  无region参数下单 不传region兼容旧调用 (验证 默认厂家直供+ship_region=null)
     TC7  释放后恢复直供   认领→释放→下单   (验证 路由状态转换:代理商→厂家直供)
     TC8  混合区域下单     认领+未认领各一单 (验证 同会话两单发货方不同+仅认领区域计提服务费)
   --------------------------------------------
   运行方式:
     · 浏览器: 在 module-test.html 点击「发货认领回归测试」按钮
     · 控制台: runAgentShippingRegression()
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

    // ---------- 输出适配 ----------
    let _sink = null;
    function emit(line, type) {
        if (_sink && typeof _sink === 'function') { _sink(line, type); return; }
        if (typeof document !== 'undefined' && document.getElementById) {
            const logEl = document.getElementById('shippingLog');
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
    //  公共前置
    // ============================================================
    async function setup() {
        if (typeof AgentShippingService === 'undefined') {
            throw new Error('AgentShippingService 未加载,请先引入 js/agent-shipping-service.js');
        }
        AgentShippingService.resetMock();
    }

    // TC1: 正常认领(代理商1 认领 山东泰安)
    async function TC1_normalClaim() {
        await setup();
        const db0 = AgentShippingService.getMockDB();
        assertEqual(db0.shipping_claims.length, 0, 'TC1 初始无认领记录');

        const r = await AgentShippingService.claim(1, '山东泰安');

        assertEqual(r.success, true, 'TC1 认领应成功');
        assert(r.claimId && r.claimId.startsWith('SC'), `TC1 认领ID应以SC开头, 实际 ${r.claimId}`);
        assertEqual(r.details.agentName, '张三酒业', 'TC1 认领代理商名称');
        assertEqual(r.details.region, '山东泰安', 'TC1 认领区域');
        assertEqual(r.details.status, '已认领', 'TC1 认领状态');

        // 验证关键阶段
        const steps = r.logs.map(l => l.step);
        const requiredStages = ['阶段2-开启事务', '阶段3-认领校验', '阶段4-写入认领', '阶段5-事务提交'];
        const missing = requiredStages.filter(s => !steps.some(g => g.includes(s)));
        assert(missing.length === 0, `TC1 缺失事务阶段: ${missing.join(',')}`);

        // 验证异步任务
        assert(r.asyncOps && r.asyncOps.length === 2, `TC1 异步任务应为2个, 实际 ${r.asyncOps ? r.asyncOps.length : 0}`);
        assertIncludes(r.asyncOps, 'agent_notify', 'TC1 代理通知任务');
        assertIncludes(r.asyncOps, 'blockchain_notarize', 'TC1 区块链存证任务');

        // 验证数据库联动
        const db1 = AgentShippingService.getMockDB();
        assertEqual(db1.shipping_claims.length, 1, 'TC1 数据库: 应有1条认领记录');
        const claim = db1.shipping_claims[0];
        assertEqual(claim.agent_id, 1, 'TC1 数据库: 认领代理商ID');
        assertEqual(claim.region, '山东泰安', 'TC1 数据库: 认领区域');
        assertEqual(claim.status, '已认领', 'TC1 数据库: 认领状态');
        assertEqual(claim.service_rate, 0.05, 'TC1 数据库: 服务费率应为5%');

        // 验证发货方路由: 已认领区域 → 代理商
        const shipper = AgentShippingService.resolveShipper('山东泰安');
        assertEqual(shipper.shipper, 'agent', 'TC1 路由: 已认领区域发货方应为代理商');
        assertEqual(shipper.agentId, 1, 'TC1 路由: 代理商ID');

        // 验证事务日志: BEGIN + COMMIT, 无 ROLLBACK
        const txTypes = db1.tx_log.map(t => t.type);
        assertIncludes(txTypes, 'BEGIN', 'TC1 数据库: tx_log 应有 BEGIN');
        assertIncludes(txTypes, 'COMMIT', 'TC1 数据库: tx_log 应有 COMMIT');
        assert(!txTypes.includes('ROLLBACK'), 'TC1 数据库: tx_log 不应有 ROLLBACK');
    }

    // TC2: 重复认领拒绝(山东泰安已被代理商1认领,代理商2再认领应失败)
    async function TC2_rejectDuplicateClaim() {
        await setup();
        // 前置: 代理商1 先认领山东泰安
        await AgentShippingService.claim(1, '山东泰安');
        const db0 = AgentShippingService.getMockDB();
        const beforeClaimsLen = db0.shipping_claims.length;

        // 代理商2 尝试重复认领同一区域
        const r = await AgentShippingService.claim(2, '山东泰安');

        // 验证认领失败
        assertEqual(r.success, false, 'TC2 重复认领应失败');
        assert(r.error && r.error.includes('区域已被认领'), `TC2 错误信息应包含"区域已被认领", 实际: ${r.error}`);
        assertEqual(r.failedStage, '阶段3-认领校验', 'TC2 失败阶段应为阶段3');

        // 验证回滚日志
        const rollbackLogs = r.logs.filter(l => l.level === 'ERROR' && l.step === '回滚');
        assert(rollbackLogs.length > 0, 'TC2 应有 ERROR 级别回滚日志');

        // ★ 关键: 验证事务原子性(快照恢复) - 认领记录不应增加
        const db1 = AgentShippingService.getMockDB();
        assertEqual(db1.shipping_claims.length, beforeClaimsLen, 'TC2 数据库: 认领记录不应增加(原子性)');

        // 仍只有代理商1 的认领,代理商2 未写入
        const agent2Claim = db1.shipping_claims.find(c => c.agent_id === 2);
        assertEqual(agent2Claim, undefined, 'TC2 数据库: 代理商2 不应有认领记录');

        // 验证 tx_log 有 ROLLBACK (不断言 BEGIN,与 checkout/inventory 回滚用例一致)
        const txTypes = db1.tx_log.map(t => t.type);
        assertIncludes(txTypes, 'ROLLBACK', 'TC2 数据库: tx_log 应有 ROLLBACK');
    }

    // TC3: 释放认领(代理商1 释放山东泰安,区域可被重新认领)
    async function TC3_releaseClaim() {
        await setup();
        await AgentShippingService.claim(1, '山东泰安');

        const r = await AgentShippingService.release(1, '山东泰安');

        assertEqual(r.success, true, 'TC3 释放应成功');
        assertEqual(r.details.status, '已退出', 'TC3 释放后状态应为已退出');

        // 验证认领记录状态已变更
        const db1 = AgentShippingService.getMockDB();
        const claim = db1.shipping_claims.find(c => c.region === '山东泰安' && c.agent_id === 1);
        assert(claim, 'TC3 数据库: 应存在认领记录');
        assertEqual(claim.status, '已退出', 'TC3 数据库: 认领状态应为已退出');

        // 验证释放后区域路由恢复为厂家直供
        const shipper = AgentShippingService.resolveShipper('山东泰安');
        assertEqual(shipper.shipper, 'manufacturer', 'TC3 路由: 释放后发货方应恢复为厂家直供');

        // 验证释放后该区域可被其他代理商重新认领
        const r2 = await AgentShippingService.claim(2, '山东泰安');
        assertEqual(r2.success, true, 'TC3 释放后代理商2 应能重新认领该区域');
        assertEqual(r2.details.agentName, '李四酒业', 'TC3 重新认领代理商名称');
    }

    // TC4: 发货路由+服务费(认领后下单,订单发货方=代理商+厂家5%同品分润服务费计提)
    async function TC4_orderRoutingAndServiceFee() {
        await setup();
        // 前置: 代理商1 认领山东泰安
        await AgentShippingService.claim(1, '山东泰安');

        // 同步重置 checkout Mock,确保干净环境
        if (typeof CheckoutService === 'undefined') {
            throw new Error('TC4 需要 CheckoutService,请先引入 js/checkout-service.js');
        }
        CheckoutService.resetMock();

        // 下单(收货区域=山东泰安,已认领)
        const r = await CheckoutService.submit({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', price: 268, qty: 2 }],
            memberLevel: 'L5',
            points: 0,
            paymentMethod: 'wechat',
            region: '山东泰安', // ★ 已认领区域
        });

        assertEqual(r.success, true, 'TC4 下单应成功');

        // 验证发货方=代理商
        assertEqual(r.details.shipperType, 'agent', 'TC4 详情: 发货方应为代理商');
        assertEqual(r.details.shipperAgentName, '张三酒业', 'TC4 详情: 发货代理商名称');
        assertEqual(r.details.region, '山东泰安', 'TC4 详情: 收货区域');

        // 验证厂家5%同品分润服务费已计提(按实付金额 finalAmount 计提,含会员折扣+运费)
        const expectedFee = Math.round(r.details.finalAmount * 0.05 * 100) / 100;
        assertEqual(r.details.manufacturerServiceFee, expectedFee, 'TC4 详情: 厂家服务费应为实付金额的5%');

        // 验证订单记录的发货方字段
        const cdb = CheckoutService.getMockDB();
        const order = cdb.orders.find(o => o.order_no === r.orderNo);
        assertEqual(order.shipper_type, 'agent', 'TC4 订单: shipper_type');
        assertEqual(order.shipper_agent_id, 1, 'TC4 订单: shipper_agent_id');
        assertEqual(order.ship_region, '山东泰安', 'TC4 订单: ship_region');

        // 验证分润记录含厂家服务费
        const profit = cdb.profit_records.find(p => p.order_no === r.orderNo);
        assert(profit, 'TC4 分润记录应存在');
        assertEqual(profit.manufacturer_service_fee, expectedFee, 'TC4 分润: 厂家服务费');
        assertEqual(profit.shipper_type, 'agent', 'TC4 分润: 发货方=代理商');

        // 验证服务费流水已写入 checkout DB(随事务原子提交)
        const fees = cdb.service_fees || [];
        assertEqual(fees.length, 1, 'TC4 checkout DB: 应有1条服务费流水');
        assertEqual(fees[0].agent_id, 1, 'TC4 服务费: 代理商ID');
        assertEqual(fees[0].service_fee, expectedFee, 'TC4 服务费: 金额');
        assertEqual(fees[0].settled_as, '同品', 'TC4 服务费: 结算方式为同品');
        assertEqual(fees[0].status, '待发放', 'TC4 服务费: 初始状态待发放');

        // 验证代理商服务费结算汇总
        const settlement = AgentShippingService.getServiceFeeSettlement(1);
        assertEqual(settlement.pendingCount, 1, 'TC4 结算: 待发放1笔');
        assertEqual(settlement.pendingAmount, expectedFee, 'TC4 结算: 待发放金额');
        assertEqual(settlement.settledAs, '同品', 'TC4 结算: 结算方式同品');
    }

    // ============================================================
    //  厂家直供兼容场景(新增 TC5-TC8)
    //  覆盖未认领区域/无region/释放后恢复/混合区域 → 均走厂家直供
    // ============================================================

    // TC5: 未认领区域下单 → 厂家直供(核心兼容场景)
    //   区域存在但无代理商认领时,订单由厂家直供,不计提5%服务费
    async function TC5_unclaimedRegionManufacturerDirect() {
        await setup();
        // ★ 不进行任何认领

        if (typeof CheckoutService === 'undefined') {
            throw new Error('TC5 需要 CheckoutService,请先引入 js/checkout-service.js');
        }
        CheckoutService.resetMock();

        // resolveShipper 只读验证: 未认领区域 → 厂家直供
        const shipper = AgentShippingService.resolveShipper('山东济南');
        assertEqual(shipper.shipper, 'manufacturer', 'TC5 路由: 未认领区域应为厂家直供');
        assertEqual(shipper.agentId, null, 'TC5 路由: 未认领区域代理商ID应为null');
        assertEqual(shipper.agentName, '厂家直供', 'TC5 路由: 未认领区域发货方名称');
        assertEqual(shipper.claimId, null, 'TC5 路由: 未认领区域claimId应为null');

        // 下单(收货区域=山东济南,未认领)
        const r = await CheckoutService.submit({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', price: 268, qty: 2 }],
            memberLevel: 'L5',
            points: 0,
            paymentMethod: 'wechat',
            region: '山东济南', // ★ 未认领区域
        });

        assertEqual(r.success, true, 'TC5 下单应成功');

        // 验证发货方=厂家直供
        assertEqual(r.details.shipperType, 'manufacturer', 'TC5 详情: 发货方应为厂家直供');
        assertEqual(r.details.shipperAgentName, '厂家直供', 'TC5 详情: 发货方名称');
        assertEqual(r.details.region, '山东济南', 'TC5 详情: 收货区域');

        // ★ 关键: 未认领区域不计提5%服务费
        assertEqual(r.details.manufacturerServiceFee, 0, 'TC5 详情: 未认领区域不应计提服务费');

        // 验证订单记录发货方字段
        const cdb = CheckoutService.getMockDB();
        const order = cdb.orders.find(o => o.order_no === r.orderNo);
        assertEqual(order.shipper_type, 'manufacturer', 'TC5 订单: shipper_type');
        assertEqual(order.shipper_agent_id, null, 'TC5 订单: shipper_agent_id 应为null');
        assertEqual(order.ship_region, '山东济南', 'TC5 订单: ship_region');

        // 验证分润记录: 无厂家服务费
        const profit = cdb.profit_records.find(p => p.order_no === r.orderNo);
        assert(profit, 'TC5 分润记录应存在');
        assertEqual(profit.manufacturer_service_fee, 0, 'TC5 分润: 未认领区域厂家服务费应为0');
        assertEqual(profit.shipper_type, 'manufacturer', 'TC5 分润: 发货方=厂家直供');

        // ★ 原子性: service_fees 表不应有记录(未认领不产生服务费流水)
        const fees = cdb.service_fees || [];
        assertEqual(fees.length, 0, 'TC5 checkout DB: 未认领区域不应产生服务费流水');

        // 验证代理商服务费结算汇总为空
        const settlement = AgentShippingService.getServiceFeeSettlement(1);
        assertEqual(settlement.pendingCount, 0, 'TC5 结算: 代理商1无待发放服务费');
        assertEqual(settlement.pendingAmount, 0, 'TC5 结算: 待发放金额为0');
    }

    // TC6: 无 region 参数下单 → 厂家直供(兼容旧调用方)
    //   不传 region 时,checkout 内部跳过路由解析,默认厂家直供
    async function TC6_noRegionManufacturerDirect() {
        await setup();

        if (typeof CheckoutService === 'undefined') {
            throw new Error('TC6 需要 CheckoutService,请先引入 js/checkout-service.js');
        }
        CheckoutService.resetMock();

        // resolveShipper 只读验证: 空/null 区域 → 厂家直供
        assertEqual(AgentShippingService.resolveShipper('').shipper, 'manufacturer', 'TC6 路由: 空区域应为厂家直供');
        assertEqual(AgentShippingService.resolveShipper(null).shipper, 'manufacturer', 'TC6 路由: null区域应为厂家直供');

        // 下单(★ 不传 region,模拟旧调用方/兼容场景)
        const r = await CheckoutService.submit({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', price: 268, qty: 1 }],
            memberLevel: 'L5',
            points: 0,
            paymentMethod: 'wechat',
            // ★ 无 region
        });

        assertEqual(r.success, true, 'TC6 下单应成功');

        // 验证发货方=厂家直供(默认值,未触发路由解析)
        assertEqual(r.details.shipperType, 'manufacturer', 'TC6 详情: 无region发货方应为厂家直供');
        assertEqual(r.details.shipperAgentName, '厂家直供', 'TC6 详情: 发货方名称');
        assertEqual(r.details.region, null, 'TC6 详情: region应为null');

        // 验证不计提服务费
        assertEqual(r.details.manufacturerServiceFee, 0, 'TC6 详情: 无region不应计提服务费');

        // 验证订单记录: ship_region=null(未指定区域)
        const cdb = CheckoutService.getMockDB();
        const order = cdb.orders.find(o => o.order_no === r.orderNo);
        assertEqual(order.shipper_type, 'manufacturer', 'TC6 订单: shipper_type');
        assertEqual(order.shipper_agent_id, null, 'TC6 订单: shipper_agent_id');
        assertEqual(order.ship_region, null, 'TC6 订单: ship_region 应为null');

        // 验证无服务费流水
        const fees = cdb.service_fees || [];
        assertEqual(fees.length, 0, 'TC6 checkout DB: 无region不应产生服务费流水');
    }

    // TC7: 认领后释放再下单 → 恢复厂家直供(路由状态转换)
    //   验证释放认领后,该区域订单发货方从代理商恢复为厂家直供
    async function TC7_releaseThenManufacturerDirect() {
        await setup();

        if (typeof CheckoutService === 'undefined') {
            throw new Error('TC7 需要 CheckoutService,请先引入 js/checkout-service.js');
        }
        CheckoutService.resetMock();

        // 认领 '山东泰安'
        await AgentShippingService.claim(1, '山东泰安');

        // 验证认领后路由=代理商
        const shipper1 = AgentShippingService.resolveShipper('山东泰安');
        assertEqual(shipper1.shipper, 'agent', 'TC7 路由: 认领后应为代理商');

        // 释放认领
        await AgentShippingService.release(1, '山东泰安');

        // ★ 验证释放后路由恢复=厂家直供
        const shipper2 = AgentShippingService.resolveShipper('山东泰安');
        assertEqual(shipper2.shipper, 'manufacturer', 'TC7 路由: 释放后应恢复厂家直供');
        assertEqual(shipper2.agentId, null, 'TC7 路由: 释放后代理商ID应为null');
        assertEqual(shipper2.claimId, null, 'TC7 路由: 释放后claimId应为null');

        // 下单(收货区域=山东泰安,已释放)
        const r = await CheckoutService.submit({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', price: 268, qty: 1 }],
            memberLevel: 'L5',
            points: 0,
            paymentMethod: 'wechat',
            region: '山东泰安', // ★ 已释放区域
        });

        assertEqual(r.success, true, 'TC7 下单应成功');

        // 验证发货方=厂家直供(释放后恢复)
        assertEqual(r.details.shipperType, 'manufacturer', 'TC7 详情: 释放后发货方应恢复厂家直供');
        assertEqual(r.details.manufacturerServiceFee, 0, 'TC7 详情: 释放后不应计提服务费');

        // 验证无服务费流水
        const cdb = CheckoutService.getMockDB();
        const fees = cdb.service_fees || [];
        assertEqual(fees.length, 0, 'TC7 checkout DB: 释放后不应产生服务费流水');

        // 验证订单发货方
        const order = cdb.orders.find(o => o.order_no === r.orderNo);
        assertEqual(order.shipper_type, 'manufacturer', 'TC7 订单: shipper_type');
        assertEqual(order.shipper_agent_id, null, 'TC7 订单: shipper_agent_id');
    }

    // TC8: 混合区域下单(认领+未认领) → 分别路由
    //   同一会话内,认领区域订单走代理商+服务费,未认领区域订单走厂家直供
    async function TC8_mixedRegionRouting() {
        await setup();

        if (typeof CheckoutService === 'undefined') {
            throw new Error('TC8 需要 CheckoutService,请先引入 js/checkout-service.js');
        }
        CheckoutService.resetMock();

        // 认领 '山东泰安'(仅此区域)
        await AgentShippingService.claim(1, '山东泰安');

        // 订单1: 认领区域 → 代理商发货+服务费
        const r1 = await CheckoutService.submit({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', price: 268, qty: 1 }],
            memberLevel: 'L5',
            points: 0,
            paymentMethod: 'wechat',
            region: '山东泰安', // ★ 已认领
        });
        assertEqual(r1.success, true, 'TC8 订单1(认领区域)应成功');
        assertEqual(r1.details.shipperType, 'agent', 'TC8 订单1: 发货方应为代理商');
        assertEqual(r1.details.shipperAgentName, '张三酒业', 'TC8 订单1: 代理商名称');
        assert(r1.details.manufacturerServiceFee > 0, 'TC8 订单1: 应计提服务费(>0)');

        // 订单2: 未认领区域 → 厂家直供+无服务费
        const r2 = await CheckoutService.submit({
            items: [{ id: 1, name: '竹奕·竹香经典 500ml', price: 268, qty: 1 }],
            memberLevel: 'L5',
            points: 0,
            paymentMethod: 'wechat',
            region: '山东济南', // ★ 未认领
        });
        assertEqual(r2.success, true, 'TC8 订单2(未认领区域)应成功');
        assertEqual(r2.details.shipperType, 'manufacturer', 'TC8 订单2: 发货方应为厂家直供');
        assertEqual(r2.details.manufacturerServiceFee, 0, 'TC8 订单2: 不应计提服务费');

        // ★ 关键: 两单发货方不同(同会话内路由隔离)
        assert(r1.details.shipperType !== r2.details.shipperType,
            `TC8: 两单发货方应不同(订单1=${r1.details.shipperType}, 订单2=${r2.details.shipperType})`);

        // 验证仅订单1产生服务费流水(认领区域订单)
        const cdb = CheckoutService.getMockDB();
        const fees = cdb.service_fees || [];
        assertEqual(fees.length, 1, 'TC8 checkout DB: 仅1条服务费流水(认领区域订单)');
        assertEqual(fees[0].order_no, r1.orderNo, 'TC8: 服务费流水应关联订单1');
        assertEqual(fees[0].agent_id, 1, 'TC8: 服务费代理商ID');

        // 验证代理商1结算汇总: 仅1笔待发放(订单1)
        const settlement = AgentShippingService.getServiceFeeSettlement(1);
        assertEqual(settlement.pendingCount, 1, 'TC8 结算: 代理商1仅1笔待发放');
    }

    // ============================================================
    //  主入口
    // ============================================================
    async function runAgentShippingRegression(opts) {
        const options = opts || {};
        _sink = options.sink || null;

        const out = [];
        const sep = '═'.repeat(70);
        out.push(sep);
        out.push('  竹香酒官网 · 代理商区域发货服务认领 - 回归测试');
        out.push('  日期: ' + new Date().toISOString().slice(0, 19).replace('T', ' '));
        out.push('  目标: js/agent-shipping-service.js → AgentShippingService');
        out.push(sep);
        out.forEach(l => emit(l, 'info'));
        if (_sink) out.length = 0; else out.push('');

        const cases = [
            { name: 'TC1 正常认领: 代理1认领山东泰安  (5阶段事务+认领记录+异步任务)', fn: TC1_normalClaim },
            { name: 'TC2 重复认领拒绝: 代理2抢同区域  (阶段3抛错+回滚+原子性)',     fn: TC2_rejectDuplicateClaim },
            { name: 'TC3 释放认领: 代理1释放山东泰安  (状态→已退出+区域可重认领)', fn: TC3_releaseClaim },
            { name: 'TC4 发货路由+服务费: 认领后下单   (发货方=代理+厂家5%同品分润)', fn: TC4_orderRoutingAndServiceFee },
            // ── 厂家直供兼容场景 ──
            { name: 'TC5 未认领区域下单: 山东济南无认领 (厂家直供+无服务费+无流水)', fn: TC5_unclaimedRegionManufacturerDirect },
            { name: 'TC6 无region下单: 兼容旧调用方     (默认厂家直供+ship_region=null)', fn: TC6_noRegionManufacturerDirect },
            { name: 'TC7 释放后恢复直供: 认领→释放→下单 (路由状态转换→厂家直供)', fn: TC7_releaseThenManufacturerDirect },
            { name: 'TC8 混合区域: 认领+未认领各一单    (两单发货方不同+仅认领区域计提)', fn: TC8_mixedRegionRouting },
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
        const db = AgentShippingService.getMockDB();
        emit(`  代理商数: ${db.agents.length}`, 'info');
        emit(`  认领记录数: ${db.shipping_claims.length}`, 'info');
        db.shipping_claims.forEach(c => {
            emit(`  [${c.id}] ${c.agent_name} | ${c.region} | ${c.status} | 服务费率${(c.service_rate * 100)}%`, 'info');
        });
        emit(`  服务费流水数: ${(db.service_fees || []).length}`, 'info');
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
            window.__lastAgentShippingRegressionReport = report;
        }
        return report;
    }

    // ---------- 暴露 ----------
    if (typeof window !== 'undefined') {
        window.runAgentShippingRegression = runAgentShippingRegression;
        window.__runAgentShippingRegressionPromise = runAgentShippingRegression;
    }
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { runAgentShippingRegression };
    }
})();
