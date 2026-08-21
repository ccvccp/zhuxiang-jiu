/* ============================================
   竹香酒官网 - 分润计算核心模块
   含详细日志输出，方便排查计算错误
   ============================================ */

// ---------- 日志工具 ----------
const ProfitLog = {
    logs: [],
    enabled: true,

    log(type, step, data) {
        const entry = {
            timestamp: new Date().toISOString(),
            type: type,        // 'INPUT' | 'CALC' | 'SPLIT' | 'RESULT' | 'ERROR' | 'WARN'
            step: step,        // 计算步骤描述
            data: data,        // 计算数据
        };
        this.logs.push(entry);
        if (this.enabled) {
            const tag = `[${type}]`;
            console.log(`${tag} ${step}`, data);
        }
        return entry;
    },

    input(step, data) { return this.log('INPUT', step, data); },
    calc(step, data) { return this.log('CALC', step, data); },
    split(step, data) { return this.log('SPLIT', step, data); },
    result(step, data) { return this.log('RESULT', step, data); },
    error(step, data) { return this.log('ERROR', step, data); },
    warn(step, data) { return this.log('WARN', step, data); },

    clear() { this.logs = []; },
    getAll() { return this.logs; },
    getByType(type) { return this.logs.filter(l => l.type === type); },
    printAll() { this.logs.forEach(l => console.log(`[${l.type}] ${l.step}`, l.data)); },
};

// ---------- 常量配置 ----------
const PROFIT_CONFIG = {
    // SVIP进货折扣（同SVIP会员价8.5折）
    SVIP_DISCOUNT: 0.85,

    // 分润比例
    RATIO_WITH_AGENT:    { platform: 0.60, agent: 0.20, partner: 0.20 },
    RATIO_WITHOUT_AGENT: { platform: 0.80, agent: 0.00, partner: 0.20 },

    // 市级网店三档进货折扣
    STORE_DISCOUNT: {
        EXCELLENT: 0.70,  // 月销>¥9,000
        STANDARD: 0.80,   // 月销¥5,000-¥9,000
        BELOW:     0.90,  // 月销<¥5,000
    },

    // 代理商返利阶梯（超额累进，与设计文档§4.3完全对齐）
    // ≤25万: 无返利 | 25-50万: 15%全额 | 50-100万: 前50万20%+超出25% | >100万: 前50万20%+50-100万25%+超出30%
    AGENT_REBATE: {
        TIER1_MAX: 250000,    // 25万门槛
        TIER2_MAX: 500000,    // 50万
        TIER3_MAX: 1000000,   // 100万
        RATE_15: 0.15,
        RATE_20: 0.20,
        RATE_25: 0.25,
        RATE_30: 0.30,
    },

    // 品鉴酒比例
    TASTING_WINE_RATE: 0.03,
    TASTING_WINE_MAX: 50, // 瓶/月
};

// ---------- 核心函数1：酒店合作商分润计算 ----------
/**
 * 计算酒店/酒吧/会所合作商的分润
 * @param {Object} product  - 产品对象 {name, price}
 * @param {Number} quantity - 进货数量
 * @param {Boolean} hasAgent - 是否有代理商
 * @param {String} level    - 合作商等级 'S'|'A'|'B'|'C'|'D'
 * @returns {Object} 分润结果
 */
function calculateHotelProfitSharing(product, quantity, hasAgent = true, level = 'A') {
    ProfitLog.clear();
    ProfitLog.input('酒店分润计算启动', {
        product: product.name,
        retailPrice: product.price,
        quantity: quantity,
        hasAgent: hasAgent,
        level: level,
    });

    // --- Step 1: 计算零售总额 ---
    const retailTotal = product.price * quantity;
    ProfitLog.calc('Step1-零售总额', {
        formula: `${product.price} × ${quantity}`,
        result: retailTotal,
    });

    // --- Step 2: 计算SVIP进货总额 ---
    const svipUnitPrice = Math.round(product.price * PROFIT_CONFIG.SVIP_DISCOUNT * 100) / 100;
    const svipTotal = Math.round(svipUnitPrice * quantity * 100) / 100;
    ProfitLog.calc('Step2-SVIP进货总额', {
        formula: `${product.price} × ${PROFIT_CONFIG.SVIP_DISCOUNT} = ${svipUnitPrice}/瓶`,
        svipUnitPrice: svipUnitPrice,
        svipTotal: svipTotal,
    });

    // --- Step 3: 计算差价（分润基数） ---
    const profitBase = Math.round((retailTotal - svipTotal) * 100) / 100;
    ProfitLog.calc('Step3-分润基数（差价）', {
        formula: `${retailTotal} - ${svipTotal}`,
        profitBase: profitBase,
    });

    // --- Step 4: 计算品鉴酒成本 ---
    let tastingWineQty = 0;
    let tastingWineCost = 0;
    const tastingRate = (level === 'S') ? 0.05 : (level === 'A') ? 0.03 : (level === 'B') ? 0.02 : (level === 'C') ? 0.01 : 0;
    if (tastingRate > 0) {
        tastingWineQty = Math.min(Math.floor(quantity * tastingRate), PROFIT_CONFIG.TASTING_WINE_MAX);
        tastingWineCost = tastingWineQty * product.price;
    }
    ProfitLog.calc('Step4-品鉴酒成本', {
        level: level,
        tastingRate: `${(tastingRate * 100)}%`,
        tastingWineQty: tastingWineQty,
        tastingWineCost: tastingWineCost,
        note: tastingWineCost > 0 ? '本站承担' : '无品鉴酒',
    });

    // --- Step 5: 分润分配 ---
    const ratio = hasAgent ? PROFIT_CONFIG.RATIO_WITH_AGENT : PROFIT_CONFIG.RATIO_WITHOUT_AGENT;
    ProfitLog.input('Step5-分润比例配置', {
        hasAgent: hasAgent,
        ratio: hasAgent ? '本站60%+代理20%+酒店20%' : '本站80%+酒店20%',
        ratioDetail: ratio,
    });

    const platformShare = Math.round(profitBase * ratio.platform * 100) / 100;
    const agentShare = Math.round(profitBase * ratio.agent * 100) / 100;
    const hotelShare = Math.round(profitBase * ratio.partner * 100) / 100;

    ProfitLog.split('Step5-分润分配', {
        profitBase: profitBase,
        platformShare: { formula: `${profitBase} × ${ratio.platform}`, result: platformShare },
        agentShare: { formula: `${profitBase} × ${ratio.agent}`, result: agentShare },
        hotelShare: { formula: `${profitBase} × ${ratio.partner}`, result: hotelShare },
        splitCheck: Math.round((platformShare + agentShare + hotelShare) * 100) / 100 === profitBase ? '✓ 校验通过' : '✗ 校验失败',
    });

    // --- Step 6: 本站实际收益（扣除品鉴酒成本） ---
    const platformNet = Math.round((platformShare - tastingWineCost) * 100) / 100;
    ProfitLog.calc('Step6-本站实际收益', {
        formula: `${platformShare} - ${tastingWineCost}（品鉴酒成本）`,
        platformGross: platformShare,
        tastingWineCost: tastingWineCost,
        platformNet: platformNet,
    });

    // --- Step 7: 最终结果 ---
    const result = {
        retailTotal: retailTotal,
        svipTotal: svipTotal,
        profitBase: profitBase,
        tastingWineQty: tastingWineQty,
        tastingWineCost: tastingWineCost,
        platformShare: platformShare,
        agentShare: agentShare,
        hotelShare: hotelShare,
        platformNet: platformNet,
        hasAgent: hasAgent,
    };

    ProfitLog.result('酒店分润计算完成', result);
    return result;
}

// ---------- 核心函数2：市级网店分润计算 ----------
/**
 * 计算市级网店的分润
 * @param {Object} product     - 产品对象 {name, price}
 * @param {Number} quantity     - 销售数量
 * @param {String} discountTier - 折扣档位 'EXCELLENT'|'STANDARD'|'BELOW'
 * @param {Boolean} hasAgent    - 是否有代理商
 * @returns {Object} 分润结果
 */
function calculateCityStoreProfitSharing(product, quantity, discountTier = 'EXCELLENT', hasAgent = true) {
    ProfitLog.clear();
    ProfitLog.input('市级网店分润计算启动', {
        product: product.name,
        retailPrice: product.price,
        quantity: quantity,
        discountTier: discountTier,
        hasAgent: hasAgent,
    });

    // --- Step 1: 计算零售总额 ---
    const retailTotal = product.price * quantity;
    ProfitLog.calc('Step1-零售总额', {
        formula: `${product.price} × ${quantity}`,
        result: retailTotal,
    });

    // --- Step 2: 确定进货折扣档位 ---
    const discountRate = PROFIT_CONFIG.STORE_DISCOUNT[discountTier];
    const tierName = discountTier === 'EXCELLENT' ? '优秀档(月销>¥9,000)' :
                     discountTier === 'STANDARD'  ? '达标档(月销¥5,000-¥9,000)' :
                     '未达标档(月销<¥5,000)';
    ProfitLog.input('Step2-进货折扣档位', {
        tier: discountTier,
        tierName: tierName,
        discountRate: discountRate,
    });

    // --- Step 3: 计算网店主进货总额 ---
    const storeUnitPrice = Math.round(product.price * discountRate * 100) / 100;
    const storeTotal = Math.round(storeUnitPrice * quantity * 100) / 100;
    ProfitLog.calc('Step3-网店主进货总额', {
        formula: `${product.price} × ${discountRate} = ${storeUnitPrice}/瓶 × ${quantity}`,
        storeUnitPrice: storeUnitPrice,
        storeTotal: storeTotal,
    });

    // --- Step 4: 计算差价（分润基数） ---
    const profitBase = Math.round((retailTotal - storeTotal) * 100) / 100;
    ProfitLog.calc('Step4-分润基数（差价）', {
        formula: `${retailTotal} - ${storeTotal}`,
        profitBase: profitBase,
    });

    // --- Step 5: 分润分配 ---
    const ratio = hasAgent ? PROFIT_CONFIG.RATIO_WITH_AGENT : PROFIT_CONFIG.RATIO_WITHOUT_AGENT;
    ProfitLog.input('Step5-分润比例配置', {
        hasAgent: hasAgent,
        ratio: hasAgent ? '本站60%+代理20%+网店20%' : '本站80%+网店20%',
        ratioDetail: ratio,
    });

    const platformShare = Math.round(profitBase * ratio.platform * 100) / 100;
    const agentShare = Math.round(profitBase * ratio.agent * 100) / 100;
    const storeShare = Math.round(profitBase * ratio.partner * 100) / 100;

    ProfitLog.split('Step5-分润分配', {
        profitBase: profitBase,
        platformShare: { formula: `${profitBase} × ${ratio.platform}`, result: platformShare },
        agentShare: { formula: `${profitBase} × ${ratio.agent}`, result: agentShare },
        storeShare: { formula: `${profitBase} × ${ratio.partner}`, result: storeShare },
        splitCheck: Math.round((platformShare + agentShare + storeShare) * 100) / 100 === profitBase ? '✓ 校验通过' : '✗ 校验失败',
    });

    // --- Step 6: 最终结果 ---
    const result = {
        retailTotal: retailTotal,
        storeUnitPrice: storeUnitPrice,
        storeTotal: storeTotal,
        profitBase: profitBase,
        platformShare: platformShare,
        agentShare: agentShare,
        storeShare: storeShare,
        hasAgent: hasAgent,
        discountTier: discountTier,
        discountRate: discountRate,
    };

    ProfitLog.result('市级网店分润计算完成', result);
    return result;
}

// ---------- 核心函数3：代理商返利计算（超额累进，与设计文档§4.3对齐）----------
/**
 * 计算代理商月度返利（超额累进制）
 * 阶梯规则：
 *   ≤¥25万: 无返利（未达门槛）
 *   ¥25-50万: 全额按15%返还
 *   ¥50-100万: 前50万按20% + 超出部分按25%
 *   >¥100万: 前50万按20% + 50-100万按25% + 超出100万按30%
 * @param {Number} monthlyPurchase - 月度进货额
 * @returns {Object} 返利结果
 */
function calculateAgentRebate(monthlyPurchase) {
    ProfitLog.clear();
    ProfitLog.input('代理商返利计算启动', {
        monthlyPurchase: monthlyPurchase,
        note: '超额累进制，与设计文档§4.3完全对齐',
    });

    if (monthlyPurchase < 0) {
        ProfitLog.error('输入校验', { error: '月度进货额不能为负数', input: monthlyPurchase });
        return { error: '月度进货额不能为负数' };
    }

    const R = PROFIT_CONFIG.AGENT_REBATE;
    let rebate = 0;
    let tierName = '';
    let detail = {};

    // --- 超额累进计算（边界: 达到门槛即适用该档） ---
    if (monthlyPurchase < R.TIER1_MAX) {
        // <25万: 无返利
        tierName = 'T0-未达门槛(<¥25万)';
        rebate = 0;
        detail = { tier: 'T0', amount: 0, rate: 0 };
        ProfitLog.warn('Step1-门槛校验', {
            monthlyPurchase: monthlyPurchase,
            threshold: R.TIER1_MAX,
            result: '未达返利门槛，无返利',
        });

    } else if (monthlyPurchase < R.TIER2_MAX) {
        // 25-50万: 全额15%（≥25万且<50万）
        tierName = 'T1-基础档(¥25-50万)';
        const rebate15 = Math.round(monthlyPurchase * R.RATE_15 * 100) / 100;
        rebate = rebate15;
        detail = { tier: 'T1', amount15: rebate15, rate15: R.RATE_15 };
        ProfitLog.calc('Step1-T1基础返利(15%全额)', {
            formula: `${monthlyPurchase} × ${R.RATE_15}`,
            rebate15: rebate15,
        });

    } else if (monthlyPurchase < R.TIER3_MAX) {
        // 50-100万: 前50万20% + 超出25%（≥50万且<100万）
        tierName = 'T2-进阶档(¥50-100万)';
        const rebate20 = Math.round(R.TIER2_MAX * R.RATE_20 * 100) / 100;
        const excessAmount = monthlyPurchase - R.TIER2_MAX;
        const rebate25 = Math.round(excessAmount * R.RATE_25 * 100) / 100;
        rebate = Math.round((rebate20 + rebate25) * 100) / 100;
        detail = { tier: 'T2', amount20: rebate20, amount25: rebate25, excess: excessAmount };
        ProfitLog.calc('Step1-T2超额累进返利', {
            part1: { formula: `${R.TIER2_MAX} × ${R.RATE_20}`, rebate20: rebate20 },
            part2: { formula: `${excessAmount} × ${R.RATE_25}`, rebate25: rebate25 },
            total: rebate,
        });

    } else {
        // >100万: 前50万20% + 50-100万25% + 超出30%
        tierName = 'T3-核心档(>¥100万)';
        const rebate20 = Math.round(R.TIER2_MAX * R.RATE_20 * 100) / 100;
        const rebate25 = Math.round((R.TIER3_MAX - R.TIER2_MAX) * R.RATE_25 * 100) / 100;
        const excessAmount = monthlyPurchase - R.TIER3_MAX;
        const rebate30 = Math.round(excessAmount * R.RATE_30 * 100) / 100;
        rebate = Math.round((rebate20 + rebate25 + rebate30) * 100) / 100;
        detail = { tier: 'T3', amount20: rebate20, amount25: rebate25, amount30: rebate30, excess: excessAmount };
        ProfitLog.calc('Step1-T3超额累进返利', {
            part1: { formula: `${R.TIER2_MAX} × ${R.RATE_20}`, rebate20: rebate20 },
            part2: { formula: `${R.TIER3_MAX - R.TIER2_MAX} × ${R.RATE_25}`, rebate25: rebate25 },
            part3: { formula: `${excessAmount} × ${R.RATE_30}`, rebate30: rebate30 },
            total: rebate,
        });
    }

    // --- Step 2: 有效返利率 ---
    const effectiveRate = monthlyPurchase > 0
        ? Math.round((rebate / monthlyPurchase) * 10000) / 100
        : 0;
    ProfitLog.calc('Step2-有效返利率', {
        formula: `${rebate} / ${monthlyPurchase}`,
        effectiveRate: `${effectiveRate}%`,
    });

    // --- Step 3: 实际成本 ---
    const actualCost = Math.round((monthlyPurchase - rebate) * 100) / 100;
    ProfitLog.calc('Step3-实际进货成本', {
        formula: `${monthlyPurchase} - ${rebate}`,
        actualCost: actualCost,
    });

    // --- 最终结果 ---
    const result = {
        monthlyPurchase: monthlyPurchase,
        tierName: tierName,
        rebate: rebate,
        effectiveRate: effectiveRate,
        actualCost: actualCost,
        detail: detail,
    };

    ProfitLog.result('代理商返利计算完成', result);
    return result;
}

// ---------- 核心函数4：代理商综合收益计算（三重收益最优设计）----------
/**
 * 计算代理商月度综合收益（三重收益）
 * 收益1：进货返利（15-30%超额累进）
 * 收益2：区域分润（区域内酒店/网店销售差价的20%）
 * 收益3：品鉴酒/广告酒价值（3%品鉴酒，本站承担成本但代理商可支配）
 * @param {Object} params
 * @param {Number} params.monthlyPurchase - 代理商自身进货额
 * @param {Array}  params.partnerSales    - 区域内合作商销售 [{product, quantity, type, level}]
 * @returns {Object} 综合收益结果
 */
function calculateAgentTotalRevenue(params) {
    ProfitLog.clear();
    const { monthlyPurchase, partnerSales = [] } = params;

    ProfitLog.input('代理商综合收益计算启动', {
        monthlyPurchase: monthlyPurchase,
        partnerCount: partnerSales.length,
        note: '三重收益: 返利+分润+品鉴酒',
    });

    // --- 收益1：进货返利 ---
    ProfitLog.log('CALC', '--- 收益1: 进货返利 ---', {});
    const rebateResult = calculateAgentRebate(monthlyPurchase);
    const rebateIncome = rebateResult.rebate || 0;

    // --- 收益2：区域分润 ---
    ProfitLog.log('CALC', '--- 收益2: 区域分润 ---', {});
    let totalProfitBase = 0;
    let totalAgentShare = 0;
    const partnerDetails = [];

    partnerSales.forEach((sale, idx) => {
        ProfitLog.log('CALC', `  合作商${idx + 1}: ${sale.type} ${sale.product.name}×${sale.quantity}`, {});

        let share;
        if (sale.type === 'hotel') {
            share = calculateHotelProfitSharing(sale.product, sale.quantity, true, sale.level || 'A');
            totalProfitBase += share.profitBase;
            totalAgentShare += share.agentShare;
            partnerDetails.push({
                type: 'hotel', product: sale.product.name, quantity: sale.quantity,
                profitBase: share.profitBase, agentShare: share.agentShare,
            });
        } else if (sale.type === 'store') {
            share = calculateCityStoreProfitSharing(sale.product, sale.quantity, sale.discountTier || 'EXCELLENT', true);
            totalProfitBase += share.profitBase;
            totalAgentShare += share.agentShare;
            partnerDetails.push({
                type: 'store', product: sale.product.name, quantity: sale.quantity,
                profitBase: share.profitBase, agentShare: share.agentShare,
            });
        }
    });

    ProfitLog.calc('收益2-区域分润汇总', {
        partnerCount: partnerSales.length,
        totalProfitBase: totalProfitBase,
        totalAgentShare: totalAgentShare,
    });

    // --- 收益3：品鉴酒价值 ---
    ProfitLog.log('CALC', '--- 收益3: 品鉴酒/广告酒 ---', {});
    // 代理商自身品鉴酒（按进货量3%计算，本站承担成本但代理商可支配）
    const tastingWineQty = Math.min(
        Math.floor(monthlyPurchase / 268 * PROFIT_CONFIG.TASTING_WINE_RATE),
        PROFIT_CONFIG.TASTING_WINE_MAX
    );
    const tastingWineValue = Math.round(tastingWineQty * 268 * 100) / 100;
    ProfitLog.calc('收益3-品鉴酒价值', {
        tastingWineQty: tastingWineQty,
        unitValue: 268,
        tastingWineValue: tastingWineValue,
        note: '本站承担成本，代理商可支配用于品鉴推广',
    });

    // --- 综合收益汇总 ---
    const totalRevenue = Math.round((rebateIncome + totalAgentShare + tastingWineValue) * 100) / 100;
    const result = {
        monthlyPurchase: monthlyPurchase,
        revenue1_rebate: { income: rebateIncome, tier: rebateResult.tierName, rate: rebateResult.effectiveRate },
        revenue2_sharing: { income: totalAgentShare, partnerCount: partnerSales.length, base: totalProfitBase, details: partnerDetails },
        revenue3_tasting: { qty: tastingWineQty, value: tastingWineValue },
        totalRevenue: totalRevenue,
        actualCost: rebateResult.actualCost,
        netProfit: Math.round((totalRevenue) * 100) / 100,
    };

    ProfitLog.result('代理商综合收益计算完成', {
        收益1_返利: rebateIncome,
        收益2_分润: totalAgentShare,
        收益3_品鉴酒: tastingWineValue,
        综合收益: totalRevenue,
        实际成本: rebateResult.actualCost,
    });

    return result;
}

// ---------- 批量计算+日志汇总 ----------
/**
 * 批量计算多个订单的分润
 * @param {Array} orders - 订单数组 [{type, product, quantity, ...}]
 * @returns {Object} 汇总结果
 */
function calculateBatchProfitSharing(orders) {
    ProfitLog.clear();
    ProfitLog.input('批量分润计算启动', { orderCount: orders.length });

    const results = [];
    let totalPlatform = 0;
    let totalAgent = 0;
    let totalPartner = 0;

    orders.forEach((order, idx) => {
        ProfitLog.log('CALC', `--- 订单 ${idx + 1}/${orders.length} ---`, { order: order });

        let result;
        if (order.type === 'hotel') {
            result = calculateHotelProfitSharing(order.product, order.quantity, order.hasAgent, order.level);
            totalPlatform += result.platformNet;
        } else if (order.type === 'store') {
            result = calculateCityStoreProfitSharing(order.product, order.quantity, order.discountTier, order.hasAgent);
            totalPlatform += result.platformShare;
        } else if (order.type === 'agent') {
            result = calculateAgentRebate(order.monthlyPurchase);
            totalPlatform += result.totalRebate;
        } else {
            ProfitLog.error('未知订单类型', { order: order });
            return;
        }

        if (result.agentShare) totalAgent += result.agentShare;
        if (result.hotelShare) totalPartner += result.hotelShare;
        if (result.storeShare) totalPartner += result.storeShare;

        results.push({ order: order, result: result });
    });

    const summary = {
        totalOrders: orders.length,
        totalPlatform: Math.round(totalPlatform * 100) / 100,
        totalAgent: Math.round(totalAgent * 100) / 100,
        totalPartner: Math.round(totalPartner * 100) / 100,
        grandTotal: Math.round((totalPlatform + totalAgent + totalPartner) * 100) / 100,
    };

    ProfitLog.result('批量分润汇总', summary);
    return { results: results, summary: summary, logs: ProfitLog.getAll() };
}

// ---------- 导出（如需模块化）----------
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        ProfitLog,
        PROFIT_CONFIG,
        calculateHotelProfitSharing,
        calculateCityStoreProfitSharing,
        calculateAgentRebate,
        calculateAgentTotalRevenue,
        calculateBatchProfitSharing,
    };
}
