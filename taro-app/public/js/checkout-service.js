/**
 * checkout-service.js  ·  订单结算服务模块（基于工具包）
 * ============================================================
 * 用途:
 *   实现订单结算的完整事务流程,包含订单创建/库存扣减/优惠券核销/
 *   积分扣减与入账/分润计算/支付确认等 9 阶段。
 *
 * 基于 toolkit/ 工具包:
 *   · UpgradeLogger         → 结构化事务日志(事务ID/计时器/阶段追踪)
 *   · TransactionTemplate   → 事务编排(BEGIN/COMMIT/ROLLBACK/异步任务)
 *
 * 事务结构:
 *   preflight     : 购物车校验 + calculateOrderPrice 计算价格
 *   阶段2-开启事务 : BEGIN(快照)
 *   阶段3-订单创建 : 写入 orders 表
 *   阶段4-库存扣减 : products.stock -= qty(不足则抛错触发回滚)
 *   阶段5-优惠券核销: coupons.status → 已使用(skip if 无优惠券)
 *   阶段6-积分扣减 : members.points -= usedPoints(skip if 0)
 *   阶段7-积分入账 : members.points += earnedPoints(等级加成)
 *   阶段8-分润计算 : 记录分润明细(平台/代理商/合作商)
 *   阶段9-支付确认 : orders.status → 已付款
 *   阶段10-提交事务: COMMIT
 *   asyncTasks    : 通知推送 + 区块链存证
 *
 * 使用示例:
 *   const result = await CheckoutService.submit({
 *       items: Cart.get(),           // 购物车商品
 *       memberLevel: 'L5',           // 会员等级
 *       points: 5000,                // 使用积分
 *       couponCode: 'SVIP20',        // 优惠券码(可选)
 *       paymentMethod: 'wechat',     // 支付方式
 *   });
 *   if (result.success) { showToast('订单' + result.orderNo + '提交成功'); }
 *
 * 浏览器环境:
 *   需先加载 toolkit/upgrade-logger.js + toolkit/transaction-template.js
 *   以及 js/order-pricing.js (calculateOrderPrice) + js/inventory-service.js (库存扣减核心)
 *   全局名: CheckoutService / window.CheckoutService
 * ============================================================
 */

const CheckoutService = (function () {
    'use strict';

    const STORAGE_KEY = 'zhuxiang_checkout_db_v1';

    // ---------- 配置 ----------
    const CONFIG = {
        // 积分汇率: 100 竹叶 = ¥1
        POINTS_RATE: 0.01,
        // 积分抵扣上限: 订单金额的 30%
        POINTS_DEDUCT_MAX_RATE: 0.30,
        // 积分入账: 每消费 ¥10 = 1 竹叶
        EARN_RATE: 0.1,
        // 等级加成
        LEVEL_BOOST: { L1: 1.0, L2: 1.0, L3: 1.02, L4: 1.05, L5: 1.08 },
        // 分润规则(无代理商时)
        PROFIT_SPLIT: { platform: 0.80, hotel: 0.20 },
        // 免运费门槛(购买两瓶免运费)
        FREE_SHIPPING_QTY: 2,
    };

    let mode = 'mock'; // 'mock' | 'live'
    let apiBase = '/api/checkout';
    // 防重入标记:避免连点提交按钮导致的并发重复下单(基于 PTC11/PTC12 并发验证:
    // 多请求并发时各自独立处理,防重入直接拒绝第二个,减少不必要的重复事务)
    let _submitInFlight = false;

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
            coupons: [
                { id: 'C001', code: 'NEW10', discount: 0.10, status: '未使用', desc: '新人9折' },
                { id: 'C002', code: 'SVIP20', discount: 0.20, status: '未使用', desc: 'SVIP8折' },
            ],
            members: [
                { id: 1, name: '张三', points: 5000, level: 'L3' },
                { id: 2, name: '李四', points: 12000, level: 'L5' },
            ],
            orders: [],
            profit_records: [],
            tx_log: [], // BEGIN/COMMIT/ROLLBACK
        };
        writeDB(db);
        return db;
    }

    // ---------- Mock 事务适配器(快照模式) ----------
    function createAdapter(dbRef) {
        return {
            begin(ctx) {
                const snapshot = JSON.parse(JSON.stringify(dbRef.db));
                dbRef.db.tx_log.push({ type: 'BEGIN', time: new Date().toISOString() });
                ctx.logger.info('阶段2-开启事务', '事务已开启(快照已建立)', {
                    txLogLen: dbRef.db.tx_log.length,
                });
                return snapshot;
            },
            commit(_snapshot, ctx) {
                dbRef.db.tx_log.push({ type: 'COMMIT', time: new Date().toISOString(), steps: 8 });
                writeDB(dbRef.db);
                ctx.logger.info('阶段10-事务提交', '事务提交成功(已写入)', {
                    orderNo: ctx.orderNo,
                });
            },
            rollback(snapshot, ctx) {
                if (snapshot) {
                    snapshot.tx_log.push({ type: 'ROLLBACK', time: new Date().toISOString() });
                    dbRef.db = snapshot;
                    writeDB(snapshot);
                    ctx.logger.error('回滚', '事务已回滚(快照恢复)', {
                        orderNo: ctx.orderNo || '(未创建)',
                    });
                }
            },
        };
    }

    // ---------- 提交订单(主入口) ----------
    async function submit(params) {
        // 工具包可用性检查
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) {
            throw new Error('CheckoutService 需要工具包,请先加载 js/toolkit/upgrade-logger.js 和 js/toolkit/transaction-template.js');
        }

        const { items, memberLevel, points = 0, couponCode = null, paymentMethod = 'wechat', region = null } = params;

        // B1: 为共享资源操作补 Mutex 锁(订单号/优惠券/积分),细粒度保护
        //   锁 key 格式: {资源}:{id}, 多锁按 key 升序获取+反向释放(避免死锁)
        //   dbRef 在锁内创建, 避免并发 lost-update(与 inventory-service 一致)
        const lockKeys = ['order:next'];
        if (couponCode) lockKeys.push('coupon:' + couponCode);
        if (points && points > 0) lockKeys.push('points:' + (memberLevel || 'default'));

        const result = await window.mutex.withLocks(lockKeys, async () => {
            const dbRef = { db: readDB() };
            const adapter = createAdapter(dbRef);
            const template = new Template({ name: 'checkout', adapter: adapter });

            return await template.run({
            context: {
                items, memberLevel, points, couponCode, paymentMethod, region,
                orderNo: null, priceResult: null, memberId: null,
                pointsUsed: 0, pointsEarned: 0,
                // 发货方(由阶段3按区域认领情况解析)
                shipperType: 'manufacturer', shipperAgentId: null,
                shipperAgentName: '厂家直供', shipperClaimId: null,
                serviceFee: 0, // 阶段8: 厂家→代理商 5% 同品分润服务费
            },

            // ---------- 事务前只读检查 ----------
            preflight: async (ctx) => {
                ctx.logger.info('阶段1-购物车校验', '开始校验购物车', {
                    itemCount: ctx.items.length,
                    totalQty: ctx.items.reduce((s, i) => s + i.qty, 0),
                });

                // 空购物车检查
                if (!ctx.items || ctx.items.length === 0) {
                    ctx.logger.error('阶段1-购物车校验', '购物车为空,中止流程');
                    return { abort: true, reason: '购物车为空' };
                }

                // 合并购物车商品为主商品(简化版:取第一件)
                const mainItem = ctx.items[0];
                const totalQty = ctx.items.reduce((s, i) => s + i.qty, 0);
                const originalTotal = mainItem.price * totalQty;

                // 查询优惠券折扣(若提供券码,计算抵扣金额传入价格计算)
                // 无效券不在此拦截,保留阶段5抛错触发回滚(TC4 覆盖)
                let couponValue = 0;
                if (ctx.couponCode) {
                    const coupon = dbRef.db.coupons.find(c => c.code === ctx.couponCode && c.status === '未使用');
                    if (coupon) {
                        couponValue = Math.round(originalTotal * coupon.discount * 100) / 100;
                        ctx.logger.info('阶段1-优惠券校验', '优惠券折扣已识别', {
                            code: coupon.code, discount: coupon.discount, couponValue: couponValue,
                        });
                    } else {
                        ctx.logger.warn('阶段1-优惠券校验', '优惠券未找到或已使用,阶段5将抛错', { code: ctx.couponCode });
                    }
                }

                // 调用 calculateOrderPrice 计算价格(只读,无副作用)
                if (typeof calculateOrderPrice !== 'function') {
                    throw new Error('calculateOrderPrice 未加载,请先引入 js/order-pricing.js');
                }
                ctx.priceResult = calculateOrderPrice({
                    product: { name: mainItem.name, price: mainItem.price },
                    quantity: totalQty,
                    orderType: 'retail',
                    memberLevel: ctx.memberLevel,
                    points: ctx.points,
                    couponValue: couponValue,
                });

                if (ctx.priceResult.error) {
                    ctx.logger.error('阶段1-价格计算', '价格计算失败', { error: ctx.priceResult.error });
                    return { abort: true, reason: ctx.priceResult.error };
                }

                // 查找会员
                const member = dbRef.db.members.find(m => m.level === ctx.memberLevel);
                if (member) ctx.memberId = member.id;

                ctx.logger.info('阶段1-价格计算', '价格计算完成', {
                    originalTotal: ctx.priceResult.originalTotal,
                    totalDiscount: ctx.priceResult.totalDiscount,
                    finalAmount: ctx.priceResult.finalAmount,
                    shipping: ctx.priceResult.shipping,
                });
            },

            // ---------- 事务内 9 个阶段 ----------
            stages: [
                // 阶段2: 开启事务
                {
                    name: '阶段2-开启事务',
                    action: async (ctx) => {
                        ctx.conn = await ctx.template.adapter.begin(ctx);
                    },
                },

                // 阶段3: 订单创建 + 发货方路由
                //   按收货区域解析发货方:已认领区域→该代理商发货+售后;
                //   未认领区域→厂家直供。AgentShippingService 未加载时默认厂家直供。
                {
                    name: '阶段3-订单创建',
                    action: async (ctx) => {
                        ctx.orderNo = 'ZX' + Date.now();
                        const totalQty = ctx.items.reduce((s, i) => s + i.qty, 0);

                        // 发货方路由(只读解析,无副作用)
                        if (ctx.region && typeof AgentShippingService !== 'undefined') {
                            const shipper = AgentShippingService.resolveShipper(ctx.region);
                            ctx.shipperType = shipper.shipper;
                            ctx.shipperAgentId = shipper.agentId;
                            ctx.shipperAgentName = shipper.agentName;
                            ctx.shipperClaimId = shipper.claimId;
                        }
                        ctx.logger.info('阶段3-订单创建', '写入 orders 表', {
                            orderNo: ctx.orderNo, itemCount: ctx.items.length, totalQty,
                            region: ctx.region || '(未指定)',
                            shipper: ctx.shipperType,
                            shipperAgent: ctx.shipperAgentName,
                        });
                        dbRef.db.orders.push({
                            order_no: ctx.orderNo,
                            member_id: ctx.memberId,
                            member_level: ctx.memberLevel,
                            items: ctx.items.map(i => ({
                                id: i.id, name: i.name, price: i.price, qty: i.qty,
                            })),
                            original_total: ctx.priceResult.originalTotal,
                            total_discount: ctx.priceResult.totalDiscount,
                            discount_detail: ctx.priceResult.discountDetail,
                            shipping: ctx.priceResult.shipping,
                            final_amount: ctx.priceResult.finalAmount,
                            coupon_code: ctx.couponCode,
                            points_used: 0, // 阶段6更新
                            points_earned: 0, // 阶段7更新
                            payment_method: ctx.paymentMethod,
                            // 发货方(认领区域→代理商;未认领→厂家直供)
                            ship_region: ctx.region || null,
                            shipper_type: ctx.shipperType,
                            shipper_agent_id: ctx.shipperAgentId,
                            shipper_agent_name: ctx.shipperAgentName,
                            shipper_claim_id: ctx.shipperClaimId,
                            status: '待付款', // 阶段9更新为已付款
                            created_at: new Date().toISOString(),
                        });
                        ctx.logger.info('阶段3-订单创建', '订单记录已写入', { orderNo: ctx.orderNo });
                    },
                },

                // 阶段4: 库存扣减(委托 InventoryService.applyDeduct 共享核心)
                //   库存逻辑统一收敛至 inventory-service.js,本阶段只做委托调用。
                //   applyDeduct 在本事务内执行校验+扣减+流水+预警,失败抛错触发外层回滚。
                {
                    name: '阶段4-库存扣减',
                    action: async (ctx) => {
                        if (typeof InventoryService === 'undefined') {
                            throw new Error('InventoryService 未加载,请先引入 js/inventory-service.js');
                        }
                        const r = InventoryService.applyDeduct(dbRef, ctx.items, ctx.logger, {
                            reason: '订单出库',
                            refNo: ctx.orderNo,
                        });
                        ctx.deductedLines = r.deductedLines;
                        ctx.alertsTriggered = r.alertsTriggered;
                    },
                },

                // 阶段5: 优惠券核销(skip if 无优惠券)
                {
                    name: '阶段5-优惠券核销',
                    skip: (ctx) => !ctx.couponCode,
                    action: async (ctx) => {
                        const coupon = dbRef.db.coupons.find(
                            c => c.code === ctx.couponCode && c.status === '未使用'
                        );
                        if (!coupon) {
                            throw new Error(`优惠券无效或已使用: ${ctx.couponCode}`);
                        }
                        coupon.status = '已使用';
                        ctx.logger.info('阶段5-优惠券核销', '优惠券已核销', {
                            code: coupon.code, discount: coupon.discount,
                        });
                    },
                },

                // 阶段6: 积分扣减(skip if 0)
                {
                    name: '阶段6-积分扣减',
                    skip: (ctx) => ctx.points === 0,
                    action: async (ctx) => {
                        const member = dbRef.db.members.find(m => m.id === ctx.memberId);
                        if (!member) {
                            ctx.logger.warn('阶段6-积分扣减', '未找到会员,跳过积分扣减');
                            return;
                        }
                        if (member.points < ctx.points) {
                            throw new Error(`积分不足: 需要${ctx.points}现有${member.points}`);
                        }
                        member.points -= ctx.points;
                        ctx.pointsUsed = ctx.points;
                        ctx.logger.info('阶段6-积分扣减', '积分已扣减', {
                            member: member.name, deducted: ctx.points, remaining: member.points,
                        });
                    },
                },

                // 阶段7: 积分入账(消费获得)
                {
                    name: '阶段7-积分入账',
                    action: async (ctx) => {
                        const member = dbRef.db.members.find(m => m.id === ctx.memberId);
                        if (!member) {
                            ctx.logger.warn('阶段7-积分入账', '未找到会员,跳过积分入账');
                            return;
                        }
                        const baseEarn = Math.floor(
                            (ctx.priceResult.finalAmount - ctx.pointsUsed * CONFIG.POINTS_RATE) * CONFIG.EARN_RATE
                        );
                        const boost = CONFIG.LEVEL_BOOST[ctx.memberLevel] || 1.0;
                        ctx.pointsEarned = Math.floor(baseEarn * boost);
                        member.points += ctx.pointsEarned;

                        // 更新订单的积分字段
                        const order = dbRef.db.orders.find(o => o.order_no === ctx.orderNo);
                        if (order) {
                            order.points_used = ctx.pointsUsed;
                            order.points_earned = ctx.pointsEarned;
                        }
                        ctx.logger.info('阶段7-积分入账', '积分已入账', {
                            member: member.name,
                            earned: ctx.pointsEarned,
                            boost: boost,
                            total: member.points,
                        });
                    },
                },

                // 阶段8: 分润计算 + 厂家→代理商服务费计提
                //   平台/酒店分润不变;当发货方为认领代理商时,
                //   厂家按订单金额 5% 计提同品分润作为服务费(委托 AgentShippingService 共享核心,
                //   随本事务提交/回滚,保证原子性)。
                {
                    name: '阶段8-分润计算',
                    action: async (ctx) => {
                        const finalAmount = ctx.priceResult.finalAmount;
                        const platformShare = Math.round(finalAmount * CONFIG.PROFIT_SPLIT.platform * 100) / 100;
                        const hotelShare = Math.round(finalAmount * CONFIG.PROFIT_SPLIT.hotel * 100) / 100;

                        // 厂家→代理商 5% 同品分润服务费(仅发货方为认领代理商时计提)
                        let manufacturerServiceFee = 0;
                        let feeRecordId = null;
                        if (ctx.shipperType === 'agent' && typeof AgentShippingService !== 'undefined') {
                            const totalQty = ctx.items.reduce((s, i) => s + i.qty, 0);
                            const r = AgentShippingService.accrueServiceFee(dbRef, {
                                agentId: ctx.shipperAgentId,
                                agentName: ctx.shipperAgentName,
                                region: ctx.region,
                                orderNo: ctx.orderNo,
                                shippedQty: totalQty,
                                orderAmount: finalAmount,
                            }, ctx.logger);
                            manufacturerServiceFee = r.serviceFee;
                            feeRecordId = r.record.id;
                            ctx.serviceFee = manufacturerServiceFee;
                        }

                        dbRef.db.profit_records.push({
                            order_no: ctx.orderNo,
                            total_amount: finalAmount,
                            platform_share: platformShare,
                            hotel_share: hotelShare,
                            agent_share: 0, // 网站订单无代理商供货分润
                            // 厂家→代理商 同品分润服务费(为网店发货)
                            manufacturer_service_fee: manufacturerServiceFee,
                            service_fee_record_id: feeRecordId,
                            shipper_type: ctx.shipperType,
                            shipper_agent_name: ctx.shipperAgentName,
                            split_rule: ctx.shipperType === 'agent'
                                ? `厂家直供分润:平台80%+酒店20%;另厂家按发货量5%同品分润给代理商${ctx.shipperAgentName}作服务费`
                                : '无代理商:平台80%+酒店20%',
                            created_at: new Date().toISOString(),
                        });
                        ctx.logger.info('阶段8-分润计算', '分润明细已记录', {
                            total: finalAmount,
                            platform: platformShare,
                            hotel: hotelShare,
                            manufacturerServiceFee: manufacturerServiceFee,
                            shipper: ctx.shipperType,
                        });
                    },
                },

                // 阶段9: 支付确认
                {
                    name: '阶段9-支付确认',
                    action: async (ctx) => {
                        const order = dbRef.db.orders.find(o => o.order_no === ctx.orderNo);
                        if (!order) throw new Error('订单不存在: ' + ctx.orderNo);
                        order.status = '已付款';
                        order.paid_at = new Date().toISOString();
                        ctx.logger.info('阶段9-支付确认', '订单状态→已付款', {
                            orderNo: ctx.orderNo,
                            finalAmount: ctx.priceResult.finalAmount,
                            paymentMethod: ctx.paymentMethod,
                        });
                    },
                },

                // 阶段10: 提交事务
                {
                    name: '阶段10-提交事务',
                    action: async (ctx) => {
                        ctx.logger.info('阶段10-事务提交', '准备提交事务', {
                            executedStages: ctx.logger.executedStages(),
                            totalElapsedMs: ctx.logger.totalElapsedMs(),
                        });
                        await ctx.template.adapter.commit(ctx.conn, ctx);
                        ctx.conn = null;
                    },
                },
            ],

            // ---------- 事务后异步任务 ----------
            asyncTasks: [
                {
                    name: '阶段11-通知推送',
                    action: (ctx) => {
                        ctx.logger.info('阶段11-通知推送', '订单确认通知已发送', {
                            orderNo: ctx.orderNo,
                            finalAmount: ctx.priceResult.finalAmount,
                        });
                    },
                },
                {
                    name: '阶段11-区块链存证',
                    action: (ctx) => {
                        ctx.logger.info('阶段11-区块链存证', '订单上链完成', {
                            orderNo: ctx.orderNo,
                            hash: '0x' + Date.now().toString(16),
                        });
                    },
                },
            ],
            });
        });

        // ========== 日志格式适配(补 msg 别名) ==========
        const logs = result.logs.map(l => ({ ...l, msg: l.message }));

        // ========== 结果形状转换 ==========
        if (result.aborted) {
            return { success: false, error: result.reason, logs };
        }
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true,
                orderNo: ctx.orderNo,
                details: {
                    originalTotal: ctx.priceResult.originalTotal,
                    totalDiscount: ctx.priceResult.totalDiscount,
                    shipping: ctx.priceResult.shipping,
                    finalAmount: ctx.priceResult.finalAmount,
                    pointsUsed: ctx.pointsUsed,
                    pointsEarned: ctx.pointsEarned,
                    paymentMethod: ctx.paymentMethod,
                    couponCode: ctx.couponCode,
                    // 发货方(认领区域→代理商;未认领→厂家直供)
                    region: ctx.region || null,
                    shipperType: ctx.shipperType,
                    shipperAgentName: ctx.shipperAgentName,
                    // 厂家→代理商 5% 同品分润服务费(为网店发货)
                    manufacturerServiceFee: ctx.serviceFee,
                },
                logs,
                asyncOps: ['order_notify', 'blockchain_notarize'],
            };
        }
        return {
            success: false,
            error: result.error,
            failedStage: result.failedStage,
            executedStages: result.executedStages,
            logs,
        };
    }

    // ---------- Live: 调用后端 API ----------
    async function liveSubmit(params) {
        const r = await EnvAdapter.request({
            url: apiBase + '/submit',
            method: 'POST',
            data: params,
            header: { 'Content-Type': 'application/json' },
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
    }

    // ---------- 公共 API ----------
    return {
        init() {
            if (!EnvAdapter.storage.get(STORAGE_KEY)) initMockDB(true);
        },
        setMode(m) { mode = m; return this; },
        setApiBase(base) { apiBase = base; return this; },
        getMode() { return mode; },
        resetMock() { EnvAdapter.storage.remove(STORAGE_KEY); initMockDB(true); return this; },
        getMockDB() { return readDB(); },
        CONFIG: CONFIG,
        async submit(params) {
            // 防重入:连点提交时直接拒绝第二个,减少不必要的重复事务
            if (_submitInFlight) {
                return { success: false, error: '订单提交进行中,请勿重复点击', logs: [{ step: '防重入', level: 'WARN', msg: '提交被防重入拦截' }] };
            }
            _submitInFlight = true;
            try {
                if (mode === 'live') {
                    try { return await liveSubmit(params); }
                    catch (e) {
                        return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] };
                    }
                }
                return await submit(params);
            } finally {
                _submitInFlight = false;
            }
        },
    };
})();

// 暴露到 window 全局
if (typeof window !== 'undefined') {
    window.CheckoutService = CheckoutService;
}
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { CheckoutService };
}
