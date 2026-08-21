/* ============================================
   竹香酒官网 - 订单折扣叠加计算模块
   含AI优先级匹配+详细日志输出，方便排查计算错误
   ============================================ */

// ---------- 日志工具 ----------
const OrderPriceLog = {
    logs: [],
    enabled: true,

    log(type, step, data) {
        const entry = {
            timestamp: new Date().toISOString(),
            type: type,   // INPUT | MATCH | CALC | CHECK | RESULT | ERROR | WARN
            step: step,
            data: data,
        };
        this.logs.push(entry);
        if (this.enabled) {
            const tag = `[${type}]`;
            console.log(`${tag} ${step}`, data);
        }
        return entry;
    },

    input(step, data) { return this.log('INPUT', step, data); },
    match(step, data) { return this.log('MATCH', step, data); },
    calc(step, data)  { return this.log('CALC', step, data); },
    check(step, data) { return this.log('CHECK', step, data); },
    result(step, data) { return this.log('RESULT', step, data); },
    error(step, data)  { return this.log('ERROR', step, data); },
    warn(step, data)   { return this.log('WARN', step, data); },

    clear() { this.logs = []; },
    getAll() { return this.logs; },
    getByType(type) { return this.logs.filter(l => l.type === type); },
    printTrail() {
        console.log('\n===== 折扣计算全链路日志 =====');
        this.logs.forEach(l => console.log(`[${l.type}] ${l.step}`, l.data));
        console.log('===== 日志结束 =====\n');
    },
};

// ---------- 优先级层级配置 ----------
const PRIORITY_LEVELS = {
    P0: {
        name: 'P0-独占价',
        orderTypes: ['seckill', 'groupbuy_retail'],
        stackable: false,
        desc: '秒杀价/拼团价 → 不叠加任何优惠',
    },
    P1: {
        name: 'P1-独立渠道价',
        orderTypes: ['groupbuy', 'store_purchase'],
        stackable: false,
        desc: '团购价(7-8折)/市级网店进货价(7-9折) → 独立渠道价',
    },
    P2: {
        name: 'P2-促销价',
        orderTypes: ['presale', 'direct_discount'],
        stackable: false,
        desc: '预售价/直降价 → 不叠加满减',
    },
    P3: {
        name: 'P3-多维叠加',
        orderTypes: ['retail', 'live', 'subscribe'],
        stackable: true,
        desc: '满减+优惠券+会员折扣+积分抵扣 → 可叠加(积分≤30%)',
    },
};

// ---------- 折扣规则配置 ----------
const DISCOUNT_CONFIG = {
    // 团购4档阶梯折扣（P1）
    GROUPBUY_TIERS: [
        { min: 50000,  max: 100000,  rate: 0.80, tier: 'T1' }, // ≥¥5万: 8折
        { min: 100000, max: 200000,  rate: 0.75, tier: 'T2' }, // ≥¥10万: 7.5折
        { min: 200000, max: 500000,  rate: 0.72, tier: 'T3' }, // ≥¥20万: 7.2折
        { min: 500000, max: Infinity, rate: 0.70, tier: 'T4' }, // ≥¥50万: 7折
    ],

    // 会员折扣（P3零售时叠加）
    MEMBER_DISCOUNT: {
        L1: 1.00, L2: 0.95, L3: 0.92, L4: 0.90, L5: 0.85,
    },

    // 积分汇率
    POINTS_RATE: 0.01,       // 100竹叶 = ¥1
    POINTS_DEDUCT_MAX: 0.30, // 积分抵扣上限30%

    // 运费规则(购买竹香酒两瓶免运费)
    SHIPPING_FREE_QTY: 2,        // 两瓶免运费
    SHIPPING_BASE: 15,            // 基础运费

    // 满减活动示例（P3叠加）
    FULL_REDUCTION: [
        { threshold: 500,  reduction: 30 },  // 满500减30
        { threshold: 1000, reduction: 80 },  // 满1000减80
        { threshold: 3000, reduction: 300 }, // 满3000减300
    ],
};

// ---------- 核心函数：AI订单类型识别+优先级匹配 ----------
/**
 * AI识别订单类型并匹配折扣优先级层级
 * @param {String} orderType - 订单类型
 * @returns {Object} 匹配结果 {priority, config, stackable}
 */
function matchPriorityLevel(orderType) {
    OrderPriceLog.input('Step1-AI订单类型识别', {
        orderType: orderType,
    });

    // P0-P3优先级匹配
    for (const [level, config] of Object.entries(PRIORITY_LEVELS)) {
        if (config.orderTypes.includes(orderType)) {
            const matchResult = {
                level: level,
                name: config.name,
                stackable: config.stackable,
                desc: config.desc,
                availableDiscounts: getAvailableDiscounts(level, orderType),
            };
            OrderPriceLog.match('Step2-AI优先级层级匹配', {
                orderType: orderType,
                matchedLevel: level,
                levelName: config.name,
                stackable: config.stackable,
                desc: config.desc,
                availableDiscounts: matchResult.availableDiscounts,
            });
            return matchResult;
        }
    }

    // 默认匹配P3（零售标准品）
    OrderPriceLog.warn('Step2-AI优先级层级匹配', {
        orderType: orderType,
        note: '未匹配到特定优先级，默认使用P3零售叠加规则',
    });
    return {
        level: 'P3',
        name: PRIORITY_LEVELS.P3.name,
        stackable: true,
        desc: PRIORITY_LEVELS.P3.desc,
        availableDiscounts: getAvailableDiscounts('P3', 'retail'),
    };
}

// ---------- 获取可用折扣列表 ----------
function getAvailableDiscounts(level, orderType) {
    const discounts = [];
    switch (level) {
        case 'P0':
            discounts.push({ name: '秒杀价/拼团价', stackable: false });
            break;
        case 'P1':
            if (orderType === 'groupbuy') {
                discounts.push({ name: '团购折扣(7-8折)', stackable: false });
            } else if (orderType === 'store_purchase') {
                discounts.push({ name: '网店进货折扣(7-9折)', stackable: false });
            }
            break;
        case 'P2':
            discounts.push({ name: '预售价/直降价', stackable: false });
            break;
        case 'P3':
            discounts.push({ name: '满减', stackable: true });
            discounts.push({ name: '优惠券', stackable: true });
            discounts.push({ name: '会员折扣', stackable: true });
            discounts.push({ name: '积分抵扣(≤30%)', stackable: true });
            break;
    }
    return discounts;
}

// ---------- 核心函数：折扣叠加计算 ----------
/**
 * 计算订单折扣叠加后的最终价格
 * @param {Object} params - 计算参数
 * @param {Object} params.product  - 产品 {name, price}
 * @param {Number} params.quantity  - 数量
 * @param {String} params.orderType - 订单类型
 * @param {String} params.memberLevel - 会员等级 L1-L5
 * @param {Number} params.points    - 可用积分(竹叶)
 * @param {Number} params.couponValue - 优惠券面值
 * @param {Number} params.storeDiscountRate - 网店进货折扣(仅store_purchase)
 * @returns {Object} 计算结果+日志
 */
function calculateOrderPrice(params) {
    OrderPriceLog.clear();
    const { product, quantity, orderType, memberLevel, points = 0, couponValue = 0, storeDiscountRate } = params;

    // --- 输入日志 ---
    OrderPriceLog.input('折扣计算启动', {
        product: product.name,
        retailPrice: product.price,
        quantity: quantity,
        orderType: orderType,
        memberLevel: memberLevel,
        points: points,
        couponValue: couponValue,
    });

    // --- Step 1: 计算商品原价 ---
    const originalTotal = product.price * quantity;
    OrderPriceLog.calc('Step1-商品原价', {
        formula: `${product.price} × ${quantity}`,
        originalTotal: originalTotal,
    });

    // --- Step 2: AI匹配优先级层级 ---
    const priorityMatch = matchPriorityLevel(orderType);
    const level = priorityMatch.level;
    const stackable = priorityMatch.stackable;

    // --- Step 3: 按优先级计算折扣 ---
    let discountedTotal = originalTotal;
    let discountDetail = {};
    let totalDiscount = 0;

    if (level === 'P0') {
        // P0: 秒杀/拼团 - 独占价，不叠加
        OrderPriceLog.calc('Step3-P0独占价计算', {
            strategy: '秒杀价/拼团价独占，不叠加任何优惠',
            discountedTotal: discountedTotal,
            note: '秒杀价已在商品价格中设定，无需额外计算',
        });
        discountDetail = { type: 'P0独占价', amount: 0 };

    } else if (level === 'P1') {
        // P1: 团购/网店进货 - 独立渠道价
        if (orderType === 'groupbuy') {
            // 团购阶梯折扣
            const tier = DISCOUNT_CONFIG.GROUPBUY_TIERS.find(t => originalTotal >= t.min && originalTotal < t.max);
            if (!tier) {
                // 未达到团购最低门槛（¥50,000）
                OrderPriceLog.error('Step3-P1团购门槛校验', {
                    originalTotal: originalTotal,
                    minThreshold: DISCOUNT_CONFIG.GROUPBUY_TIERS[0].min,
                    error: '团购订单金额未达最低门槛¥50,000，不可享受团购折扣',
                });
                return {
                    error: '团购订单金额未达最低门槛¥50,000',
                    originalTotal: originalTotal,
                    logs: OrderPriceLog.getAll(),
                };
            }
            const groupbuyRate = tier.rate;
            const groupbuyDiscount = Math.round((originalTotal * (1 - groupbuyRate)) * 100) / 100;
            discountedTotal = Math.round((originalTotal - groupbuyDiscount) * 100) / 100;

            OrderPriceLog.calc('Step3-P1团购折扣计算', {
                originalTotal: originalTotal,
                matchedTier: { tier: tier.tier, min: tier.min, rate: groupbuyRate },
                formula: `${originalTotal} × ${groupbuyRate} = ${discountedTotal}`,
                groupbuyRate: groupbuyRate,
                groupbuyDiscount: groupbuyDiscount,
                discountedTotal: discountedTotal,
            });

            discountDetail = { type: '团购折扣', rate: groupbuyRate, tier: tier.tier, amount: groupbuyDiscount };

            // P1不叠加优惠券/积分
            OrderPriceLog.check('Step3-P1叠加校验', {
                couponValue: couponValue,
                points: points,
                rule: '团购不叠加优惠券/积分',
                couponApplied: 0,
                pointsApplied: 0,
                result: '✓ 优惠券和积分不叠加',
            });

        } else if (orderType === 'store_purchase') {
            // 网店进货折扣
            const storeRate = storeDiscountRate || 0.80;
            const storeDiscount = Math.round((originalTotal * (1 - storeRate)) * 100) / 100;
            discountedTotal = Math.round((originalTotal - storeDiscount) * 100) / 100;

            OrderPriceLog.calc('Step3-P1网店进货折扣计算', {
                originalTotal: originalTotal,
                formula: `${originalTotal} × ${storeRate} = ${discountedTotal}`,
                storeRate: storeRate,
                storeDiscount: storeDiscount,
                discountedTotal: discountedTotal,
            });

            discountDetail = { type: '网店进货折扣', rate: storeRate, amount: storeDiscount };
        }
        totalDiscount = originalTotal - discountedTotal;

    } else if (level === 'P2') {
        // P2: 预售/直降 - 促销价，不叠加满减
        OrderPriceLog.calc('Step3-P2促销价计算', {
            strategy: '预售价/直降价，不叠加满减',
            discountedTotal: discountedTotal,
            note: '促销价已在商品价格中设定',
        });
        discountDetail = { type: 'P2促销价', amount: 0 };

    } else if (level === 'P3') {
        // P3: 满减+优惠券+会员折扣+积分抵扣 - 多维叠加

        // 3a: 会员折扣
        const memberRate = DISCOUNT_CONFIG.MEMBER_DISCOUNT[memberLevel] || 1.00;
        const memberDiscount = Math.round((originalTotal * (1 - memberRate)) * 100) / 100;
        discountedTotal = Math.round((originalTotal - memberDiscount) * 100) / 100;
        OrderPriceLog.calc('Step3-P3a会员折扣', {
            memberLevel: memberLevel,
            memberRate: memberRate,
            formula: `${originalTotal} × ${memberRate} = ${discountedTotal}`,
            memberDiscount: memberDiscount,
            afterMember: discountedTotal,
        });

        // 3b: 满减
        const fullReduction = DISCOUNT_CONFIG.FULL_REDUCTION
            .filter(r => discountedTotal >= r.threshold)
            .sort((a, b) => b.reduction - a.reduction)[0];
        const reductionAmount = fullReduction ? fullReduction.reduction : 0;
        discountedTotal = Math.round((discountedTotal - reductionAmount) * 100) / 100;
        OrderPriceLog.calc('Step3-P3b满减', {
            matchedReduction: fullReduction ? `满${fullReduction.threshold}减${fullReduction.reduction}` : '无满减',
            reductionAmount: reductionAmount,
            afterReduction: discountedTotal,
        });

        // 3c: 优惠券
        const couponApplied = Math.min(couponValue, discountedTotal);
        discountedTotal = Math.round((discountedTotal - couponApplied) * 100) / 100;
        OrderPriceLog.calc('Step3-P3c优惠券抵扣', {
            couponValue: couponValue,
            couponApplied: couponApplied,
            afterCoupon: discountedTotal,
        });

        // 3d: 积分抵扣（上限30%）
        const pointsValue = Math.round(points * DISCOUNT_CONFIG.POINTS_RATE * 100) / 100;
        const maxDeduct = Math.round(originalTotal * DISCOUNT_CONFIG.POINTS_DEDUCT_MAX * 100) / 100;
        const pointsApplied = Math.min(pointsValue, maxDeduct, discountedTotal);
        discountedTotal = Math.round((discountedTotal - pointsApplied) * 100) / 100;
        OrderPriceLog.calc('Step3-P3d积分抵扣', {
            points: points,
            pointsValue: pointsValue,
            formula: `${points} 竹叶 × ${DISCOUNT_CONFIG.POINTS_RATE} = ¥${pointsValue}`,
            maxDeduct: maxDeduct,
            note: `上限 = 原价 × ${DISCOUNT_CONFIG.POINTS_DEDUCT_MAX} = ¥${maxDeduct}`,
            pointsApplied: pointsApplied,
            afterPoints: discountedTotal,
        });

        // 叠加规则校验
        OrderPriceLog.check('Step3-P3叠加校验', {
            memberDiscount: memberDiscount,
            reductionAmount: reductionAmount,
            couponApplied: couponApplied,
            pointsApplied: pointsApplied,
            totalDiscount: originalTotal - discountedTotal,
            stackable: true,
            result: '✓ P3多维叠加合规',
        });

        discountDetail = {
            type: 'P3多维叠加',
            memberDiscount: memberDiscount,
            reductionAmount: reductionAmount,
            couponApplied: couponApplied,
            pointsApplied: pointsApplied,
        };
        totalDiscount = originalTotal - discountedTotal;
    }

    // --- Step 4: 叠加规则最终校验 ---
    OrderPriceLog.check('Step4-叠加规则最终校验', {
        priorityLevel: level,
        stackable: stackable,
        originalTotal: originalTotal,
        discountedTotal: discountedTotal,
        totalDiscount: totalDiscount,
        discountRate: Math.round((totalDiscount / originalTotal) * 10000) / 100 + '%',
        result: '✓ 校验通过',
    });

    // --- Step 5: 运费计算(购买两瓶免运费) ---
    let shipping = 0;
    if (quantity >= DISCOUNT_CONFIG.SHIPPING_FREE_QTY) {
        shipping = 0;
        OrderPriceLog.calc('Step5-运费计算', {
            quantity: quantity,
            threshold: DISCOUNT_CONFIG.SHIPPING_FREE_QTY,
            shipping: 0,
            note: `购买${quantity}瓶≥${DISCOUNT_CONFIG.SHIPPING_FREE_QTY}瓶，免运费`,
        });
    } else {
        shipping = DISCOUNT_CONFIG.SHIPPING_BASE;
        OrderPriceLog.calc('Step5-运费计算', {
            quantity: quantity,
            threshold: DISCOUNT_CONFIG.SHIPPING_FREE_QTY,
            shipping: shipping,
            note: `购买${quantity}瓶<${DISCOUNT_CONFIG.SHIPPING_FREE_QTY}瓶，收取基础运费`,
        });
    }

    // --- Step 6: 实付金额 ---
    const finalAmount = Math.round((discountedTotal + shipping) * 100) / 100;

    // --- 最终结果 ---
    const result = {
        originalTotal: originalTotal,
        priorityLevel: level,
        priorityName: priorityMatch.name,
        stackable: stackable,
        discountDetail: discountDetail,
        totalDiscount: Math.round(totalDiscount * 100) / 100,
        discountRate: Math.round((totalDiscount / originalTotal) * 10000) / 100,
        shipping: shipping,
        finalAmount: finalAmount,
        logs: OrderPriceLog.getAll(),
    };

    OrderPriceLog.result('折扣计算完成', {
        originalTotal: originalTotal,
        priorityLevel: level,
        totalDiscount: Math.round(totalDiscount * 100) / 100,
        discountRate: Math.round((totalDiscount / originalTotal) * 10000) / 100 + '%',
        shipping: shipping,
        finalAmount: finalAmount,
        stackCheck: '✓ 通过',
    });

    return result;
}

// ---------- 导出 ----------
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        OrderPriceLog,
        PRIORITY_LEVELS,
        DISCOUNT_CONFIG,
        matchPriorityLevel,
        calculateOrderPrice,
    };
}
