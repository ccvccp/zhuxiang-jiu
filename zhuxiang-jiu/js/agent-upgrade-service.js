/**
 * 竹香酒官网 · 代理商升级服务模块（v2.0 · 工具包版）
 * ----------------------------------------------------
 * 本版本基于 toolkit/ 工具包重构,移除原硬编码的事务编排逻辑:
 *   · UpgradeLogger       → toolkit/upgrade-logger.js
 *   · 事务编排(开始/提交/回滚/异步调度) → toolkit/transaction-template.js
 *
 * 保留原外部 API 不变:
 *   module.exports = { AgentUpgradeService, UPGRADE_CONFIG, UpgradeLogger, aiAssessUpgradeRisk }
 *
 * 涉及表：agents, agent_upgrade_logs, agent_purchase_orders,
 *         agent_rebates, agent_ai_rebates, credit_scores,
 *         wallet_accounts, compliance_monitors
 * 关联模块：信用管理、钱包盈利、合规监控、积分管理、区块链存证
 *
 * 事务原则：
 * 1. 全部数据库操作在单事务内完成,任一步骤失败则整体回滚
 * 2. 跨模块联动采用"事务内消息 + 事务后异步执行"双模式
 * 3. 区块链存证在事务提交后异步执行（不阻塞主事务）
 * 4. AI风险评估在事务前执行（只读,不锁数据）
 *
 * 改造说明:
 *   - 原本散落在 upgradeAgent/downgradeAgent 中的 BEGIN/COMMIT/ROLLBACK
 *     错误捕获/异步任务调度逻辑全部下沉到 TransactionTemplate
 *   - 业务代码只关注"每个阶段做什么",事务编排由模板统一处理
 *   - 日志能力(事务ID/计时器/阶段追踪)由 toolkit/upgrade-logger.js 提供
 */

'use strict';

// ============================================================
//  第一部分：加载工具包(Node.js 与浏览器双环境兼容)
// ============================================================

const { UpgradeLogger } = (() => {
    if (typeof require === 'function') {
        try { return require('./toolkit/upgrade-logger.js'); } catch (e) {}
    }
    if (typeof globalThis !== 'undefined' && globalThis.UpgradeLogger) {
        return { UpgradeLogger: globalThis.UpgradeLogger };
    }
    if (typeof window !== 'undefined' && window.UpgradeLogger) {
        return { UpgradeLogger: window.UpgradeLogger };
    }
    throw new Error('UpgradeLogger 未加载,请先引入 toolkit/upgrade-logger.js');
})();

const { TransactionTemplate } = (() => {
    if (typeof require === 'function') {
        try { return require('./toolkit/transaction-template.js'); } catch (e) {}
    }
    if (typeof globalThis !== 'undefined' && globalThis.TransactionTemplate) {
        return { TransactionTemplate: globalThis.TransactionTemplate };
    }
    if (typeof window !== 'undefined' && window.TransactionTemplate) {
        return { TransactionTemplate: window.TransactionTemplate };
    }
    throw new Error('TransactionTemplate 未加载,请先引入 toolkit/transaction-template.js');
})();

// ============================================================
//  第二部分：数据库连接与事务封装(原始 db 接口保留)
// ============================================================

const db = {
    pool: null, // 实际: mysql.createPool({...})

    /**
     * 开启事务
     * @returns {Object} conn - 带事务的连接
     */
    async beginTransaction() {
        const conn = await this.pool.getConnection();
        await conn.beginTransaction();
        return conn;
    },

    /**
     * 提交事务
     */
    async commit(conn) {
        await conn.commit();
        conn.release();
    },

    /**
     * 回滚事务
     */
    async rollback(conn) {
        try { await conn.rollback(); } catch (e) { /* 忽略 */ }
        try { conn.release(); } catch (e) { /* 忽略 */ }
    },
};

/**
 * 事务适配器 - 把 db 模块适配为 TransactionTemplate 所需的接口
 * begin/commit/rollback 三阶段,内部委托给 db 对象
 */
const dbAdapter = {
    async begin(ctx) {
        const conn = await db.beginTransaction();
        ctx.logger.info('事务边界', 'conn 已获取', {
            connId: conn?.id || conn?._id || 'unknown',
            isolationLevel: conn?.isolationLevel || 'default',
        });
        return conn;
    },
    async commit(conn, ctx) {
        await db.commit(conn);
        ctx.logger.info('事务边界', 'COMMIT 完成(连接已释放)', { connReleased: true });
    },
    async rollback(conn, ctx) {
        await db.rollback(conn);
        ctx.logger.info('事务边界', 'ROLLBACK 完成(连接已释放)', { connReleased: true });
    },
};

// ============================================================
//  第三部分：升级配置常量
// ============================================================

const UPGRADE_CONFIG = {
    // 等级定义
    LEVELS: {
        '观察': {
            annual_target: 0,
            first_batch: 0,
            rebate_tier: 'T0',
            rebate_rate: 0,
            taste_quota: 0,
            credit_boost: 0,
            wallet_quota: 0,
            compliance_threshold: '低',
            audit_frequency: '月',
        },
        '市级': {
            annual_target: 500000,
            first_batch: 250000,
            rebate_tier: 'T1',
            rebate_rate: 0.15,
            taste_quota: 27,
            credit_boost: 50,
            wallet_quota: 100000,
            compliance_threshold: '中',
            audit_frequency: '月',
        },
        '核心': {
            annual_target: 1000000,
            first_batch: 500000,
            rebate_tier: 'T2',
            rebate_rate: 0.20,
            rebate_excess_rate: 0.25,
            taste_quota: 50,
            credit_boost: 80,
            wallet_quota: 300000,
            compliance_threshold: '高',
            audit_frequency: '半月',
        },
        '战略': {
            annual_target: 5000000,
            first_batch: 1000000,
            rebate_tier: 'T3',
            rebate_rate: 0.20,
            rebate_mid_rate: 0.25,
            rebate_excess_rate: 0.30,
            taste_quota: 50,
            credit_boost: 120,
            wallet_quota: 1000000,
            compliance_threshold: '极高',
            audit_frequency: '周',
        },
    },

    // 降级触发条件
    DOWNGRADE_RULES: {
        '核心': { min_monthly: 250000, consecutive_months: 3, downgrade_to: '市级' },
        '市级': { min_monthly: 150000, consecutive_months: 3, downgrade_to: '观察' },
        '观察': { min_monthly: 0, consecutive_months: 6, downgrade_to: null, action: '取消资格' },
    },

    // 宽限期（月）
    GRACE_PERIOD: 3,
};

// ============================================================
//  第四部分：AI风险评估引擎（事务前只读执行）
// ============================================================

/**
 * AI升级风险评估
 * @param {Number} agentId
 * @param {String} targetLevel - 目标等级
 * @returns {Object} { score, approval, reasons, avgMonthly, creditScore }
 */
async function aiAssessUpgradeRisk(agentId, targetLevel) {
    // 1. 查询代理商近6个月进货记录（只读，不在事务内）
    const recentPurchases = await db.pool.query(
        'SELECT * FROM agent_purchase_orders WHERE agent_id = ? AND created_at >= DATE_SUB(NOW(), INTERVAL 6 MONTH)',
        [agentId]
    );

    // 2. 查询代理商信用分
    const creditInfo = await db.pool.query(
        'SELECT credit_score, credit_level FROM credit_scores WHERE agent_id = ?',
        [agentId]
    );

    // 3. 查询代理商区域市场数据
    const regionData = await db.pool.query(
        'SELECT region, monthly_sales FROM agents WHERE id = ?',
        [agentId]
    );

    // 4. AI风险评估打分（0-100）
    let score = 60; // 基础分
    const reasons = [];

    // 4.1 进货趋势分析
    const avgMonthly = recentPurchases.reduce((sum, p) => sum + p.total_amount, 0) / 6;
    if (avgMonthly >= 250000) { score += 15; reasons.push('近6月月均进货达标'); }
    else { score -= 10; reasons.push('近6月月均进货偏低'); }

    // 4.2 信用分评估
    const creditScore = creditInfo[0]?.credit_score || 0;
    if (creditScore >= 700) { score += 15; reasons.push(`竹信分${creditScore}≥700`); }
    else if (creditScore >= 600) { score += 5; reasons.push(`竹信分${creditScore}≥600`); }
    else { score -= 20; reasons.push(`竹信分${creditScore}<600，风险偏高`); }

    // 4.3 资金能力评估
    const agentInfo = await db.pool.query('SELECT registered_capital FROM agents WHERE id = ?', [agentId]);
    const capital = agentInfo[0]?.registered_capital || 0;
    if (capital >= 1000000) { score += 10; reasons.push(`注册资本${capital}≥100万`); }

    // 4.4 进货增长率
    const first3 = recentPurchases.slice(0, 3).reduce((s, p) => s + p.total_amount, 0);
    const last3 = recentPurchases.slice(3, 6).reduce((s, p) => s + p.total_amount, 0);
    if (last3 > first3 * 1.2) { score += 10; reasons.push('进货增长趋势良好'); }

    // 5. 审批决策
    score = Math.max(0, Math.min(100, score));
    let approval;
    if (score >= 80) approval = '通过';
    else if (score >= 60) approval = '需人工复核';
    else approval = '拒绝';

    return { score, approval, reasons, avgMonthly, creditScore };
}

// ============================================================
//  第五部分：核心服务（基于 TransactionTemplate 重构）
// ============================================================

class AgentUpgradeService {

    /**
     * 代理商等级升级（主入口）
     *
     * 11 阶段事务结构(由 TransactionTemplate 编排):
     *   preflight    : AI风险评估(只读,可中止)
     *   阶段2-开启事务: BEGIN + 目标等级校验
     *   阶段3-agents表: UPDATE agents
     *   阶段4-升级日志: INSERT agent_upgrade_logs
     *   阶段5-订单表  : UPDATE agent_purchase_orders(待付款返利率)
     *   阶段6-返利重算: SELECT 月度+UPDATE agent_rebates+INSERT agent_ai_rebates
     *   阶段7-信用管理: UPDATE credit_scores
     *   阶段8-钱包模块: UPDATE wallet_accounts
     *   阶段9-合规监控: UPDATE compliance_monitors
     *   阶段10-事务提交: COMMIT
     *   asyncTasks   : 区块链/AI监控/通知(异步)
     *
     * @param {Object} params
     * @param {Number} params.agentId
     * @param {String} params.fromLevel
     * @param {String} params.toLevel
     * @param {String} params.upgradeType  - '主动升级'|'AI建议升级'|'合同续签升级'|'系统降级'
     * @param {String} params.operator
     * @param {String} params.remark
     * @returns {Object} { success, logId?, txId, details?, error?, failedStage?, logs }
     */
    async upgradeAgent({ agentId, fromLevel, toLevel, upgradeType, operator, remark }) {
        // B1/agent: Mutex 锁包裹整个升级事务,防止同一代理商并发升级/降级导致 lost-update
        //   覆盖阶段3-9 所有 UPDATE(agents/orders/rebates/credit_scores/compliance_monitors)
        const result = await window.mutex.withLock('agent:' + agentId, async () => {
            const template = new TransactionTemplate({
                name: 'agent_upgrade',
                adapter: dbAdapter,
            });

            return await template.run({
            context: {
                agentId, fromLevel, toLevel, upgradeType, operator, remark,
                service: this, // 供 asyncTasks 调用 calculateRebate 等
                logId: null,
                targetConfig: null,
                aiResult: null,
                newRebate: 0,
                rebateDelta: 0,
            },

            // ---------- 事务前只读检查 ----------
            preflight: async (ctx) => {
                ctx.logger.info('preflight', 'AI风险评估开始', { agentId, fromLevel, toLevel });
                const aiResult = await aiAssessUpgradeRisk(agentId, toLevel);
                ctx.aiResult = aiResult;
                ctx.logger.info('preflight', 'AI评估完成', {
                    score: aiResult.score,
                    approval: aiResult.approval,
                    reasons: aiResult.reasons,
                    avgMonthly: aiResult.avgMonthly,
                    creditScore: aiResult.creditScore,
                });
                if (aiResult.approval === '拒绝') {
                    ctx.logger.error('preflight', 'AI审批拒绝,流程终止', aiResult);
                    return { abort: true, reason: 'AI风险评估未通过', aiResult };
                }
            },

            // ---------- 事务内 9 个阶段 ----------
            stages: [
                // 阶段2: 开启事务 + 目标等级校验
                {
                    name: '阶段2-开启事务',
                    action: async (ctx) => {
                        ctx.conn = await ctx.template.adapter.begin(ctx);
                        // 目标等级校验(在事务内,失败可触发回滚)
                        const targetConfig = UPGRADE_CONFIG.LEVELS[ctx.toLevel];
                        if (!targetConfig) {
                            throw new Error(`无效的目标等级: ${ctx.toLevel}`);
                        }
                        ctx.targetConfig = targetConfig;
                        ctx.logger.debug('阶段2-开启事务', '目标等级配置加载完成', {
                            toLevel: ctx.toLevel,
                            rebateTier: targetConfig.rebate_tier,
                            rebateRate: targetConfig.rebate_rate,
                            annualTarget: targetConfig.annual_target,
                            tasteQuota: targetConfig.taste_quota,
                        });
                    },
                },

                // 阶段3: agents表更新
                {
                    name: '阶段3-agents表',
                    action: async (ctx) => {
                        const cfg = ctx.targetConfig;
                        ctx.logger.info('阶段3-agents表', '执行UPDATE agents', {
                            set: {
                                agent_level: ctx.toLevel,
                                annual_target: cfg.annual_target,
                                current_rebate_tier: cfg.rebate_tier,
                                taste_quota_monthly: cfg.taste_quota,
                            },
                            where: { id: ctx.agentId },
                        });
                        const [r] = await ctx.conn.execute(
                            `UPDATE agents
                             SET agent_level = ?,
                                 annual_target = ?,
                                 first_batch_amount = ?,
                                 current_rebate_tier = ?,
                                 tier_updated_at = NOW(),
                                 upgrade_count = upgrade_count + 1,
                                 taste_quota_monthly = ?,
                                 ai_risk_level = ?
                             WHERE id = ?`,
                            [
                                ctx.toLevel,
                                cfg.annual_target,
                                cfg.first_batch,
                                cfg.rebate_tier,
                                cfg.taste_quota,
                                ctx.aiResult.score >= 80 ? '低' : '中',
                                ctx.agentId,
                            ]
                        );
                        ctx.logger.info('阶段3-agents表', 'agents表更新完成', {
                            affectedRows: r.affectedRows,
                            changedRows: r.changedRows,
                            warning: r.affectedRows === 0 ? '未匹配到代理商记录' : null,
                        });
                    },
                },

                // 阶段4: 写入升级日志
                {
                    name: '阶段4-升级日志',
                    action: async (ctx) => {
                        const cfg = ctx.targetConfig;
                        ctx.logger.info('阶段4-升级日志', '执行INSERT agent_upgrade_logs', {
                            fromLevel: ctx.fromLevel, toLevel: ctx.toLevel, upgradeType: ctx.upgradeType,
                        });
                        const [logResult] = await ctx.conn.execute(
                            `INSERT INTO agent_upgrade_logs
                             (agent_id, from_level, to_level, from_tier, to_tier,
                              from_rebate_rate, to_rebate_rate, from_annual_target, to_annual_target,
                              from_taste_quota, to_taste_quota, upgrade_type,
                              ai_risk_score, ai_approval, ai_reasons,
                              effective_date, status, operator, remark, created_at)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURDATE(), '已生效', ?, ?, NOW())`,
                            [
                                ctx.agentId,
                                ctx.fromLevel, ctx.toLevel,
                                UPGRADE_CONFIG.LEVELS[ctx.fromLevel]?.rebate_tier || 'T0',
                                cfg.rebate_tier,
                                UPGRADE_CONFIG.LEVELS[ctx.fromLevel]?.rebate_rate || 0,
                                cfg.rebate_rate,
                                UPGRADE_CONFIG.LEVELS[ctx.fromLevel]?.annual_target || 0,
                                cfg.annual_target,
                                UPGRADE_CONFIG.LEVELS[ctx.fromLevel]?.taste_quota || 0,
                                cfg.taste_quota,
                                ctx.upgradeType,
                                ctx.aiResult.score,
                                ctx.aiResult.approval,
                                JSON.stringify(ctx.aiResult.reasons),
                                ctx.operator,
                                ctx.remark,
                            ]
                        );
                        ctx.logId = logResult.insertId;
                        ctx.logger.info('阶段4-升级日志', '升级日志写入完成', { logId: ctx.logId });
                    },
                },

                // 阶段5: 更新待付款订单返利率
                {
                    name: '阶段5-订单表',
                    action: async (ctx) => {
                        const cfg = ctx.targetConfig;
                        ctx.logger.info('阶段5-订单表', '执行UPDATE agent_purchase_orders(待付款)', {
                            set: { rebate_rate: cfg.rebate_rate },
                            where: { agent_id: ctx.agentId, status: '待付款' },
                        });
                        const [r] = await ctx.conn.execute(
                            `UPDATE agent_purchase_orders
                             SET rebate_rate = ?
                             WHERE agent_id = ? AND status = '待付款'`,
                            [cfg.rebate_rate, ctx.agentId]
                        );
                        ctx.logger.info('阶段5-订单表', '订单表更新完成', {
                            affectedRows: r.affectedRows,
                            note: r.affectedRows === 0 ? '无待付款订单(可接受)' : null,
                        });
                    },
                },

                // 阶段6: 返利重算(SELECT + UPDATE + INSERT 三步)
                {
                    name: '阶段6-返利重算',
                    action: async (ctx) => {
                        const cfg = ctx.targetConfig;
                        // 6.1 查询当月已付款订单总额
                        ctx.logger.info('阶段6-返利重算', '查询当月进货总额', { agentId: ctx.agentId });
                        const [rows] = await ctx.conn.execute(
                            `SELECT COALESCE(SUM(total_amount), 0) as total
                             FROM agent_purchase_orders
                             WHERE agent_id = ? AND status IN ('已付款','已发货','已签收')
                               AND created_at >= DATE_FORMAT(NOW(), '%Y-%m-01')`,
                            [ctx.agentId]
                        );
                        const monthlyTotal = rows[0]?.total || 0;
                        ctx.logger.info('阶段6-返利重算', '当月进货总额已查询', { monthlyTotal });

                        // 6.2 用超额累进公式重算返利
                        const newRebate = this.calculateRebate(monthlyTotal, ctx.toLevel);
                        const oldRebate = this.calculateRebate(monthlyTotal, ctx.fromLevel);
                        const rebateDelta = Math.round((newRebate - oldRebate) * 100) / 100;
                        ctx.newRebate = newRebate;
                        ctx.rebateDelta = rebateDelta;
                        ctx.logger.info('阶段6-返利重算', '返利差额计算完成', {
                            monthlyTotal, oldRebate, newRebate, rebateDelta,
                            formula: `${ctx.fromLevel}→${ctx.toLevel}`,
                        });

                        // 6.3 更新返利结算表
                        ctx.logger.info('阶段6-返利重算', '执行UPDATE agent_rebates', {
                            set: { rebate_amount: newRebate, ai_tier_match: `${cfg.rebate_tier}-${cfg.rebate_rate * 100}%` },
                        });
                        await ctx.conn.execute(
                            `UPDATE agent_rebates
                             SET rebate_amount = ?,
                                 ai_tier_match = ?
                             WHERE agent_id = ? AND period = DATE_FORMAT(NOW(), '%Y-%m')
                               AND status = '待结算'`,
                            [newRebate, `${cfg.rebate_tier}-${cfg.rebate_rate * 100}%`, ctx.agentId]
                        );

                        // 6.4 写入AI返利记录
                        ctx.logger.info('阶段6-返利重算', '执行INSERT agent_ai_rebates', { agentId: ctx.agentId });
                        await ctx.conn.execute(
                            `INSERT INTO agent_ai_rebates
                             (rebate_id, agent_id, period, ai_tier_match, ai_rebate_calc,
                              ai_suggestion, ai_anomaly, created_at)
                             SELECT id, ?, DATE_FORMAT(NOW(), '%Y-%m'),
                                    ?, ?, '等级升级返利重算', 0, NOW()
                             FROM agent_rebates
                             WHERE agent_id = ? AND period = DATE_FORMAT(NOW(), '%Y-%m')
                               AND status = '待结算'`,
                            [
                                ctx.agentId,
                                `${cfg.rebate_tier}-${cfg.rebate_rate * 100}%`,
                                JSON.stringify({ monthlyTotal, oldRebate, newRebate, rebateDelta }),
                                ctx.agentId,
                            ]
                        );
                    },
                },

                // 阶段7: 信用分加成
                {
                    name: '阶段7-信用管理',
                    action: async (ctx) => {
                        const cfg = ctx.targetConfig;
                        ctx.logger.info('阶段7-信用管理', '执行UPDATE credit_scores', {
                            set: { credit_score_add: cfg.credit_boost, reason: `代理商升级至${ctx.toLevel}` },
                        });
                        await ctx.conn.execute(
                            `UPDATE credit_scores
                             SET credit_score = credit_score + ?,
                                 score_change_reason = CONCAT(score_change_reason, ' | 代理商升级至${ctx.toLevel}'),
                                 updated_at = NOW()
                             WHERE agent_id = ?`,
                            [cfg.credit_boost, ctx.agentId]
                        );
                        ctx.logger.info('阶段7-信用管理', `竹信分+${cfg.credit_boost} 完成`);
                    },
                },

                // 阶段8: 钱包额度联动 (B1/wallet: 补 Mutex 锁,防并发升级/降级 lost-update)
                {
                    name: '阶段8-钱包模块',
                    action: async (ctx) => {
                        await window.mutex.withLock('wallet:' + ctx.agentId, async () => {
                            const cfg = ctx.targetConfig;
                            ctx.logger.info('阶段8-钱包模块', '执行UPDATE wallet_accounts', {
                                set: { credit_limit: cfg.wallet_quota, tier_level: ctx.toLevel },
                            });
                            await ctx.conn.execute(
                                `UPDATE wallet_accounts
                                 SET credit_limit = ?,
                                     tier_level = ?,
                                     updated_at = NOW()
                                 WHERE agent_id = ?`,
                                [cfg.wallet_quota, ctx.toLevel, ctx.agentId]
                            );
                            ctx.logger.info('阶段8-钱包模块', `预付款额度→${cfg.wallet_quota} 完成`);
                        });
                    },
                },

                // 阶段9: 合规监控联动
                {
                    name: '阶段9-合规监控',
                    action: async (ctx) => {
                        const cfg = ctx.targetConfig;
                        ctx.logger.info('阶段9-合规监控', '执行UPDATE compliance_monitors', {
                            set: {
                                risk_threshold: cfg.compliance_threshold,
                                audit_frequency: cfg.audit_frequency,
                            },
                        });
                        await ctx.conn.execute(
                            `UPDATE compliance_monitors
                             SET risk_threshold = ?,
                                 audit_frequency = ?,
                                 monitoring_scope = CONCAT(monitoring_scope, ' | 升级后监控'),
                                 updated_at = NOW()
                             WHERE agent_id = ?`,
                            [cfg.compliance_threshold, cfg.audit_frequency, ctx.agentId]
                        );
                        ctx.logger.info('阶段9-合规监控', `监控阈值→${cfg.compliance_threshold} 完成`);
                    },
                },

                // 阶段10: 提交事务
                {
                    name: '阶段10-事务提交',
                    action: async (ctx) => {
                        ctx.logger.info('阶段10-事务提交', '准备提交事务(执行COMMIT)', {
                            executedStages: ctx.logger.executedStages(),
                            totalElapsedMs: ctx.logger.totalElapsedMs(),
                        });
                        await ctx.template.adapter.commit(ctx.conn, ctx);
                        ctx.conn = null; // commit 后置空,避免 catch 误回滚
                    },
                },
            ],

            // ---------- 事务后异步任务(失败仅记日志,不阻塞主流程) ----------
            asyncTasks: [
                {
                    name: '阶段11-区块链',
                    action: (ctx) => ctx.service.asyncBlockchainNotarize(
                        ctx.logId,
                        { agentId: ctx.agentId, fromLevel: ctx.fromLevel, toLevel: ctx.toLevel, aiResult: ctx.aiResult },
                        ctx.logger
                    ),
                },
                {
                    name: '阶段11-AI监控',
                    action: (ctx) => ctx.service.asyncSetupAIMonitor(
                        ctx.agentId, ctx.toLevel, ctx.targetConfig, ctx.logger
                    ),
                },
                {
                    name: '阶段11-通知',
                    action: (ctx) => ctx.service.asyncNotifyAgent(
                        ctx.agentId, ctx.fromLevel, ctx.toLevel,
                        {
                            newRebateRate: ctx.targetConfig.rebate_rate,
                            newRebate: ctx.newRebate,
                            rebateDelta: ctx.rebateDelta,
                            tasteQuota: ctx.targetConfig.taste_quota,
                        },
                        ctx.logger
                    ),
                },
            ],
        });
        });

        // ========== 结果形状转换(保持原 API 兼容) ==========
        // 中止(AI拒绝)
        if (result.aborted) {
            return {
                success: false,
                error: result.reason || '前置检查中止',
                aiResult: result.aiResult,
                txId: result.txId,
                logs: result.logs,
            };
        }
        // 成功
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true,
                logId: ctx.logId,
                txId: result.txId,
                details: {
                    fromLevel: ctx.fromLevel,
                    toLevel: ctx.toLevel,
                    aiScore: ctx.aiResult.score,
                    aiApproval: ctx.aiResult.approval,
                    newRebateRate: ctx.targetConfig.rebate_rate,
                    newRebate: ctx.newRebate,
                    rebateDelta: ctx.rebateDelta,
                    tasteQuota: ctx.targetConfig.taste_quota,
                    creditBoost: ctx.targetConfig.credit_boost,
                    walletQuota: ctx.targetConfig.wallet_quota,
                    complianceThreshold: ctx.targetConfig.compliance_threshold,
                    auditFrequency: ctx.targetConfig.audit_frequency,
                },
                logs: result.logs,
            };
        }
        // 失败(事务回滚)
        return {
            success: false,
            error: result.error,
            errorCode: result.errorCode || null,
            txId: result.txId,
            failedStage: result.failedStage,
            executedStages: result.executedStages,
            logs: result.logs,
        };
    }

    /**
     * 超额累进返利计算(T0-T3 边际累进, 与后端 agent_service.REBATE_TIERS 对齐, 决策 D-9 2026-08-29)
     * <20万: 0 | 20-50万: 超出部分15% | 50-100万: 20-50万15%+超出25% | >100万: 再加超出部分30%
     */
    calculateRebate(monthlyPurchase, level) {
        const config = UPGRADE_CONFIG.LEVELS[level];
        if (!config) return 0;

        const T1 = 200000, T2 = 500000, T3 = 1000000;
        const R15 = 0.15, R25 = 0.25, R30 = 0.30;

        let rebate = 0;
        if (monthlyPurchase < T1) {
            rebate = 0;
        } else if (monthlyPurchase < T2) {
            rebate = (monthlyPurchase - T1) * R15;
        } else if (monthlyPurchase < T3) {
            rebate = (T2 - T1) * R15 + (monthlyPurchase - T2) * R25;
        } else {
            rebate = (T2 - T1) * R15 + (T3 - T2) * R25 + (monthlyPurchase - T3) * R30;
        }
        return Math.round(rebate * 100) / 100;
    }

    // ============================================================
    //  第六部分：异步事务后操作
    // ============================================================

    /**
     * 区块链存证（异步）
     */
    async asyncBlockchainNotarize(logId, data, logger) {
        const log = logger || new UpgradeLogger({ prefix: 'baas' });
        const start = Date.now();
        const taskId = 'baas-' + Date.now().toString(36);
        log.info('阶段11-区块链', '异步任务启动: 区块链存证', { taskId, logId, async: true });
        try {
            log.startTimer('阶段11-区块链');
            const hash = await this.callBaaS({
                type: 'agent_upgrade',
                logId,
                data,
                timestamp: Date.now(),
            });
            log.info('阶段11-区块链', 'BaaS上链完成', { taskId, hash });

            await db.pool.execute(
                'UPDATE agent_upgrade_logs SET blockchain_hash = ? WHERE id = ?',
                [hash, logId]
            );
            log.stopTimer('阶段11-区块链', '区块链存证完成(已写回hash)', {
                taskId, hash, totalElapsedMs: Date.now() - start,
            });
            return hash;
        } catch (error) {
            log.error('阶段11-区块链', '区块链存证失败', {
                taskId, logId, error: error.message, elapsedMs: Date.now() - start,
            });
            throw error;
        }
    }

    /**
     * AI监控设置（异步）
     */
    async asyncSetupAIMonitor(agentId, level, config, logger) {
        const log = logger || new UpgradeLogger({ prefix: 'ai_monitor' });
        const start = Date.now();
        const taskId = 'ai-monitor-' + Date.now().toString(36);
        log.info('阶段11-AI监控', '异步任务启动: AI监控设置', { taskId, agentId, level, async: true });
        try {
            log.startTimer('阶段11-AI监控');
            const monthlyTarget = config.annual_target / 12;
            await db.pool.execute(
                `INSERT INTO ai_monitor_tasks
                 (agent_id, monitor_type, target_value, grace_period, status, created_at)
                 VALUES (?, 'upgrade_tracking', ?, ?, 'active', NOW())`,
                [agentId, monthlyTarget, UPGRADE_CONFIG.GRACE_PERIOD]
            );
            log.stopTimer('阶段11-AI监控', 'AI监控任务设置完成', {
                taskId, agentId, monthlyTarget: Math.round(monthlyTarget),
                gracePeriod: UPGRADE_CONFIG.GRACE_PERIOD,
                totalElapsedMs: Date.now() - start,
            });
        } catch (error) {
            log.error('阶段11-AI监控', 'AI监控设置失败', {
                taskId, agentId, error: error.message, elapsedMs: Date.now() - start,
            });
            throw error;
        }
    }

    /**
     * 通知推送（异步）
     */
    async asyncNotifyAgent(agentId, fromLevel, toLevel, details, logger) {
        const log = logger || new UpgradeLogger({ prefix: 'notify' });
        const start = Date.now();
        const taskId = 'notify-' + Date.now().toString(36);
        log.info('阶段11-通知', '异步任务启动: 通知推送', {
            taskId, agentId, fromLevel, toLevel, async: true,
        });
        try {
            log.startTimer('阶段11-通知');
            const message = `恭喜升级！
          等级: ${fromLevel} → ${toLevel}
          返利率: ${details.newRebateRate}
          本月返利: ¥${details.newRebate} (较旧档位+¥${details.rebateDelta})
          品鉴酒配额: ${details.tasteQuota}瓶/月
          请关注新的年度任务，3个月宽限期内需达标。`;

            await this.pushNotification(agentId, {
                type: 'agent_upgrade',
                title: `代理商等级升级通知`,
                content: message,
            });
            log.stopTimer('阶段11-通知', '通知推送完成', {
                taskId, agentId, totalElapsedMs: Date.now() - start,
            });
        } catch (error) {
            log.error('阶段11-通知', '通知推送失败', {
                taskId, agentId, error: error.message, elapsedMs: Date.now() - start,
            });
            throw error;
        }
    }

    /**
     * BaaS调用（伪代码,实际对接区块链服务商）
     */
    async callBaaS(data) { return '0x' + Date.now().toString(16); }

    /**
     * 推送通知（伪代码,实际对接推送服务）
     */
    async pushNotification(agentId, msg) { /* 实际调用推送服务 */ }

    // ============================================================
    //  第七部分：降级服务（基于 TransactionTemplate 重构,含取消资格分支）
    // ============================================================

    /**
     * 代理商降级（连续3月不达标自动触发）
     *
     * 两个分支:
     *   · 取消资格: 观察级连续6月不达标 → status='已退出'
     *   · 正常降级: 核心/市级连续3月不达标 → 等级下调一档
     *
     * @param {Number} agentId
     * @param {String} fromLevel  - 当前等级
     * @param {String} reason     - 降级原因
     * @returns {Object} { success, toLevel?, action?, txId, logs, [error, failedStage] }
     */
    async downgradeAgent(agentId, fromLevel, reason) {
        const rule = UPGRADE_CONFIG.DOWNGRADE_RULES[fromLevel];
        const isCancellation = !rule || !rule.downgrade_to;
        const toLevel = rule?.downgrade_to;
        const targetConfig = toLevel ? UPGRADE_CONFIG.LEVELS[toLevel] : null;

        // B1/agent: Mutex 锁包裹整个降级/取消事务,防止同一代理商并发升级/降级导致 lost-update
        const result = await window.mutex.withLock('agent:' + agentId, async () => {
            const template = new TransactionTemplate({
                name: isCancellation ? 'agent_cancellation' : 'agent_downgrade',
                adapter: dbAdapter,
            });

            return await template.run({
            context: {
                agentId, fromLevel, toLevel, reason, rule, targetConfig,
                service: this, isCancellation,
            },

            stages: isCancellation ? [
                // ========== 取消资格分支: 单步事务 ==========
                {
                    name: '取消资格-开启事务',
                    action: async (ctx) => {
                        ctx.logger.warn('取消资格', `${ctx.fromLevel}级连续不达标,取消代理资格`, { agentId: ctx.agentId });
                        ctx.conn = await ctx.template.adapter.begin(ctx);
                    },
                },
                {
                    name: '取消资格-agents表',
                    action: async (ctx) => {
                        ctx.logger.info('取消资格-agents表', '执行UPDATE agents(status→已退出)', { agentId: ctx.agentId });
                        await ctx.conn.execute(
                            `UPDATE agents SET status = '已退出', tier_updated_at = NOW() WHERE id = ?`,
                            [ctx.agentId]
                        );
                        ctx.logger.info('取消资格-agents表', 'agents.status→已退出 完成');
                    },
                },
                {
                    name: '取消资格-提交事务',
                    action: async (ctx) => {
                        ctx.logger.info('取消资格-提交事务', '准备提交取消资格事务', {
                            executedStages: ctx.logger.executedStages(),
                        });
                        await ctx.template.adapter.commit(ctx.conn, ctx);
                        ctx.conn = null;
                    },
                },
            ] : [
                // ========== 正常降级分支: 多阶段事务 ==========
                {
                    name: '降级-开启事务',
                    action: async (ctx) => {
                        ctx.logger.info('降级-开启事务', '准备开启事务', {
                            fromLevel: ctx.fromLevel, toLevel: ctx.toLevel, reason: ctx.reason,
                        });
                        ctx.conn = await ctx.template.adapter.begin(ctx);
                        ctx.logger.warn('降级', `降级: ${ctx.fromLevel} → ${ctx.toLevel}`, {
                            agentId: ctx.agentId, reason: ctx.reason,
                            minMonthly: ctx.rule.min_monthly,
                            consecutiveMonths: ctx.rule.consecutive_months,
                        });
                    },
                },
                {
                    name: '降级-agents表',
                    action: async (ctx) => {
                        const cfg = ctx.targetConfig;
                        ctx.logger.info('降级-agents表', '执行UPDATE agents(降级)', {
                            set: {
                                agent_level: ctx.toLevel,
                                annual_target: cfg.annual_target,
                                current_rebate_tier: cfg.rebate_tier,
                                taste_quota_monthly: cfg.taste_quota,
                                ai_risk_level: '高',
                            },
                        });
                        await ctx.conn.execute(
                            `UPDATE agents
                             SET agent_level = ?,
                                 annual_target = ?,
                                 current_rebate_tier = ?,
                                 taste_quota_monthly = ?,
                                 tier_updated_at = NOW(),
                                 ai_risk_level = '高'
                             WHERE id = ?`,
                            [
                                ctx.toLevel,
                                cfg.annual_target,
                                cfg.rebate_tier,
                                cfg.taste_quota,
                                ctx.agentId,
                            ]
                        );
                        ctx.logger.info('降级-agents表', 'agents表降级完成');
                    },
                },
                {
                    name: '降级-日志',
                    action: async (ctx) => {
                        const cfg = ctx.targetConfig;
                        ctx.logger.info('降级-日志', '执行INSERT agent_upgrade_logs(系统降级)', {
                            upgradeType: '系统降级', operator: 'SYSTEM',
                        });
                        await ctx.conn.execute(
                            `INSERT INTO agent_upgrade_logs
                             (agent_id, from_level, to_level, from_tier, to_tier,
                              upgrade_type, ai_risk_score, ai_approval, ai_reasons,
                              effective_date, status, operator, remark, created_at)
                             VALUES (?, ?, ?, ?, ?, '系统降级', 0, '自动触发', ?, CURDATE(), '已生效', 'SYSTEM', ?, NOW())`,
                            [
                                ctx.agentId,
                                ctx.fromLevel, ctx.toLevel,
                                UPGRADE_CONFIG.LEVELS[ctx.fromLevel].rebate_tier,
                                cfg.rebate_tier,
                                JSON.stringify([ctx.reason]),
                                ctx.reason,
                            ]
                        );
                        ctx.logger.info('降级-日志', '降级日志写入完成');
                    },
                },
                {
                    name: '降级-信用分',
                    action: async (ctx) => {
                        ctx.logger.info('降级-信用分', '执行UPDATE credit_scores(扣减100)', {
                            set: { credit_score_sub: 100, reason: '降级扣分' },
                        });
                        await ctx.conn.execute(
                            `UPDATE credit_scores
                             SET credit_score = GREATEST(0, credit_score - 100),
                                 score_change_reason = CONCAT(score_change_reason, ' | 降级扣分'),
                                 updated_at = NOW()
                             WHERE agent_id = ?`,
                            [ctx.agentId]
                        );
                        ctx.logger.info('降级-信用分', '信用分-100 完成');
                    },
                },
                {
                    name: '降级-钱包',
                    action: async (ctx) => {
                        await window.mutex.withLock('wallet:' + ctx.agentId, async () => {
                            const cfg = ctx.targetConfig;
                            ctx.logger.info('降级-钱包', '执行UPDATE wallet_accounts(额度降低)', {
                                set: { credit_limit: cfg.wallet_quota, tier_level: ctx.toLevel },
                            });
                            await ctx.conn.execute(
                                `UPDATE wallet_accounts
                                 SET credit_limit = ?,
                                     tier_level = ?,
                                     updated_at = NOW()
                                 WHERE agent_id = ?`,
                                [cfg.wallet_quota, ctx.toLevel, ctx.agentId]
                            );
                            ctx.logger.info('降级-钱包', `钱包额度→${cfg.wallet_quota} 完成`);
                        });
                    },
                },
                {
                    name: '降级-提交事务',
                    action: async (ctx) => {
                        ctx.logger.info('降级-提交事务', '准备提交降级事务', {
                            executedStages: ctx.logger.executedStages(),
                            totalElapsedMs: ctx.logger.totalElapsedMs(),
                        });
                        await ctx.template.adapter.commit(ctx.conn, ctx);
                        ctx.conn = null;
                    },
                },
            ],

            // 异步通知(降级/取消资格都需通知)
            asyncTasks: isCancellation ? [] : [
                {
                    name: '阶段11-通知',
                    action: (ctx) => ctx.service.asyncNotifyAgent(
                        ctx.agentId, ctx.fromLevel, ctx.toLevel,
                        {
                            newRebateRate: ctx.targetConfig.rebate_rate,
                            newRebate: 0,
                            rebateDelta: 0,
                            tasteQuota: ctx.targetConfig.taste_quota,
                        },
                        ctx.logger
                    ),
                },
            ],
        });
        });

        // ========== 结果形状转换 ==========
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true,
                action: isCancellation ? '取消资格' : '降级',
                toLevel: ctx.toLevel,
                txId: result.txId,
                logs: result.logs,
            };
        }
        return {
            success: false,
            error: result.error,
            errorCode: result.errorCode || null,
            txId: result.txId,
            failedStage: result.failedStage,
            executedStages: result.executedStages,
            logs: result.logs,
        };
    }

    // ============================================================
    //  第八部分：月度达标检查（定时任务调用,使用 UpgradeLogger 直接记录)
    // ============================================================

    /**
     * 月度达标检查（每月1日执行）
     * 检查所有代理商上月进货是否达标，连续不达标则降级
     */
    async monthlyTierCheck() {
        const logger = new UpgradeLogger({ prefix: 'monthly_check' });
        const start = Date.now();
        logger.info('月度检查', '月度达标检查启动', {
            txId: logger.txId,
            schedule: '每月1日定时执行',
        });

        // 查询所有活跃代理商
        logger.startTimer('月度检查-查询');
        logger.info('月度检查-查询', '查询所有活跃代理商上月进货汇总');
        const agents = await db.pool.query(
            `SELECT a.id, a.agent_level,
                    COALESCE(SUM(po.total_amount), 0) as monthly_purchase,
                    a.ai_tier_prediction
             FROM agents a
             LEFT JOIN agent_purchase_orders po
               ON po.agent_id = a.id
              AND po.status IN ('已付款','已发货','已签收')
              AND po.created_at >= DATE_SUB(NOW(), INTERVAL 1 MONTH)
             WHERE a.status = '活跃'
             GROUP BY a.id`
        );
        logger.stopTimer('月度检查-查询', '代理商列表查询完成', { count: agents.length });

        const results = { checked: 0, passed: 0, warned: 0, downgraded: 0 };

        logger.info('月度检查', '开始逐个检查代理商', { totalAgents: agents.length });

        for (const agent of agents) {
            results.checked++;
            const rule = UPGRADE_CONFIG.DOWNGRADE_RULES[agent.agent_level];
            if (!rule) {
                logger.debug('月度检查', `代理商${agent.id}无降级规则(${agent.agent_level}),跳过`);
                continue;
            }

            logger.debug('月度检查', `检查代理商${agent.id} (${agent.agent_level})`, {
                monthlyPurchase: agent.monthly_purchase,
                threshold: rule.min_monthly,
                gap: agent.monthly_purchase - rule.min_monthly,
            });

            if (agent.monthly_purchase < rule.min_monthly) {
                // 不达标：查询连续不达标月数
                const [missRows] = await db.pool.query(
                    `SELECT consecutive_missed_months FROM agent_ai_rebates
                     WHERE agent_id = ? ORDER BY id DESC LIMIT 1`,
                    [agent.id]
                );
                const missed = (missRows[0]?.consecutive_missed_months || 0) + 1;

                logger.warn('月度检查', `代理商${agent.id}本月不达标`, {
                    monthly: agent.monthly_purchase,
                    threshold: rule.min_monthly,
                    missedConsecutive: missed,
                    required: rule.consecutive_months,
                });

                if (missed >= rule.consecutive_months) {
                    // 连续N月不达标 → 降级
                    logger.warn('月度检查', `代理商${agent.id}触发降级(连续${missed}月不达标)`, {
                        agentId: agent.id,
                        fromLevel: agent.agent_level,
                        downgradeTo: rule.downgrade_to,
                    });
                    const result = await this.downgradeAgent(
                        agent.id,
                        agent.agent_level,
                        `连续${missed}月未达${rule.min_monthly}门槛`
                    );
                    if (result.success) {
                        results.downgraded++;
                        logger.info('月度检查', `代理商${agent.id}降级成功`, {
                            toLevel: result.toLevel,
                            txId: result.txId,
                        });
                    } else {
                        logger.error('月度检查', `代理商${agent.id}降级失败`, {
                            error: result.error,
                            failedStage: result.failedStage,
                            txId: result.txId,
                        });
                    }
                } else {
                    // 预警
                    results.warned++;
                    logger.info('月度检查',
                        `代理商${agent.id}不达标预警(${missed}/${rule.consecutive_months})`,
                        { monthly: agent.monthly_purchase, threshold: rule.min_monthly }
                    );
                }
            } else {
                results.passed++;
                logger.debug('月度检查', `代理商${agent.id}达标`, {
                    monthly: agent.monthly_purchase,
                    threshold: rule.min_monthly,
                });
            }
        }

        logger.info('月度检查', '月度检查完成', {
            ...results,
            totalElapsedMs: Date.now() - start,
            txId: logger.txId,
        });
        return { success: true, results, logs: logger.getAll() };
    }
}

// ============================================================
//  第九部分：导出(保持原 API 兼容)
// ============================================================

module.exports = {
    AgentUpgradeService,
    UPGRADE_CONFIG,
    UpgradeLogger, // 从 toolkit 透传
    aiAssessUpgradeRisk,
};

// ============================================================
//  使用示例
// ============================================================
/*
const service = new AgentUpgradeService();

// 升级（¥25万→¥50万，市级→核心）
const result = await service.upgradeAgent({
    agentId: 10001,
    fromLevel: '市级',
    toLevel: '核心',
    upgradeType: '主动升级',
    operator: 'admin',
    remark: '代理商主动申请升级至核心代理',
});

// 降级（连续3月不达标自动触发）
const downResult = await service.downgradeAgent(
    10001,
    '核心',
    '连续3月未达25万门槛'
);

// 月度检查（定时任务每月1日执行）
const checkResult = await service.monthlyTierCheck();
*/
