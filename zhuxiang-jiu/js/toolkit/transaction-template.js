/**
 * transaction-template.js  ·  通用事务编排模板
 * ============================================================
 * 用途:
 *   为多阶段事务流程提供统一编排框架,自动处理:
 *     · 事务边界(BEGIN/COMMIT/ROLLBACK)
 *     · 阶段计时与日志
 *     · 失败时自动回滚 + 错误上下文捕获
 *     · 事务后异步任务调度
 *
 * 适用场景:
 *   · 代理商升级/降级(11阶段事务)
 *   · 订单结算(库存+优惠券+分润+积分多阶段)
 *   · 钱包预付款(扣减+分账+合规校验)
 *   · 老酒兑换回收(评估+回收+积分+现金)
 *   · 任何需要原子性的多步骤业务流程
 *
 * 适配器(adapter):
 *   抽象事务底层实现,同时支持两种模式:
 *     · DB 模式:  conn = await pool.getConnection(); conn.beginTransaction();
 *     · Mock 模式: snapshot = deepClone(db); writeDB(snapshot) 回滚
 *
 * 使用示例(参考 agent-upgrade-service.js):
 *   const { TransactionTemplate } = require('./transaction-template.js');
 *   const { UpgradeLogger } = require('./upgrade-logger.js');
 *
 *   // 1. 定义事务适配器(DB 模式)
 *   const dbAdapter = {
 *     async begin(ctx) {
 *       const conn = await db.pool.getConnection();
 *       await conn.beginTransaction();
 *       return conn;
 *     },
 *     async commit(conn, ctx) {
 *       await conn.commit();
 *       conn.release();
 *     },
 *     async rollback(conn, ctx) {
 *       try { await conn.rollback(); } catch (e) {}
 *       try { conn.release(); } catch (e) {}
 *     },
 *   };
 *
 *   // 2. 构造模板
 *   const template = new TransactionTemplate({
 *     name: 'agent_upgrade',
 *     adapter: dbAdapter,
 *   });
 *
 *   // 3. 定义阶段
 *   const result = await template.run({
 *     context: { agentId: 1, fromLevel: '市级', toLevel: '核心' },
 *     preflight: async (ctx) => {
 *       const ai = await aiAssess(ctx.agentId, ctx.toLevel);
 *       ctx.aiResult = ai;
 *       if (ai.approval === '拒绝') return { abort: true, reason: 'AI拒绝' };
 *     },
 *     stages: [
 *       { name: '阶段2-开启事务', action: async (ctx) => {
 *           ctx.conn = await ctx.template.adapter.begin(ctx);
 *         } },
 *       { name: '阶段3-agents表', action: async (ctx) => {
 *           await ctx.conn.execute('UPDATE agents SET ...', [...]);
 *         } },
 *       // ... 更多阶段
 *     ],
 *     asyncTasks: [
 *       { name: '区块链存证', action: async (ctx) => { await callBaaS(...); } },
 *     ],
 *   });
 *
 *   // 4. 检查结果
 *   if (result.success) { console.log('升级成功, txId:', result.txId); }
 *
 * 浏览器环境:
 *   <script src="upgrade-logger.js"></script>
 *   <script src="transaction-template.js"></script>
 *   全局名: TransactionTemplate / window.TransactionTemplate
 * ============================================================
 */

(function (root, factory) {
    'use strict';
    // 自动加载 UpgradeLogger(若已加载则复用)
    const LoggerCtor = (typeof root !== 'undefined' && root.UpgradeLogger)
        || (typeof window !== 'undefined' && window.UpgradeLogger)
        || (typeof require === 'function' ? require('./upgrade-logger.js').UpgradeLogger : null);

    const Template = factory(LoggerCtor);
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { TransactionTemplate: Template };
    }
    if (typeof root !== 'undefined') {
        root.TransactionTemplate = Template;
    }
    if (typeof window !== 'undefined') {
        window.TransactionTemplate = Template;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (UpgradeLogger) {
    'use strict';

    if (!UpgradeLogger) {
        throw new Error('TransactionTemplate 依赖 UpgradeLogger,请先加载 upgrade-logger.js');
    }

    /**
     * 通用事务编排模板
     */
    class TransactionTemplate {
        /**
         * @param {Object} opts
         * @param {String} opts.name      - 流程名(用于日志前缀)
         * @param {Object} opts.adapter   - 事务适配器 { begin, commit, rollback }
         * @param {UpgradeLogger} [opts.logger] - 自定义日志器
         * @param {Boolean} [opts.autoRollbackStages] - 失败时逆序调用各阶段 onRollback(默认 true)
         */
        constructor(opts = {}) {
            this.name = opts.name || 'transaction';
            this.adapter = opts.adapter || null;
            this.logger = opts.logger || new UpgradeLogger({ prefix: this.name });
            this.autoRollbackStages = opts.autoRollbackStages !== false;
        }

        /**
         * 运行事务流程
         * @param {Object} params
         * @param {Object} [params.context]      - 业务上下文(会注入 template/logger/txId)
         * @param {Function} [params.preflight]   - 事务前只读检查 (ctx) => { abort?: boolean, ... }
         * @param {Array} params.stages           - 阶段列表
         *   [{ name, action(ctx), skip?(ctx), onRollback?(ctx) }]
         * @param {Array} [params.asyncTasks]     - 事务后异步任务
         *   [{ name, action(ctx) }]
         * @returns {Object} { success, txId, ctx, logs, [error, failedStage] }
         */
        async run({ context, preflight, stages, asyncTasks }) {
            const ctx = context || {};
            ctx.template = this;
            ctx.logger = this.logger;
            ctx.txId = this.logger.txId;
            ctx.conn = null; // DB 模式: 连接; Mock 模式: 快照
            let _currentStage = null; // 当前正在执行的阶段(用于 catch 精确定位)

            this.logger.info('入口', `${this.name} 流程启动`, { txId: ctx.txId });

            try {
                // ========== Preflight: 事务前只读检查 ==========
                if (typeof preflight === 'function') {
                    _currentStage = 'preflight';
                    this.logger.startTimer('preflight');
                    this.logger.info('preflight', '事务前只读检查开始');
                    const preResult = await preflight(ctx);
                    this.logger.stopTimer('preflight', '前置检查完成', { result: preResult });
                    if (preResult && preResult.abort) {
                        this.logger.warn('preflight', '流程被前置检查中止', preResult);
                        return {
                            success: false,
                            aborted: true,
                            txId: ctx.txId,
                            ctx,
                            ...preResult,
                            logs: this.logger.getAll(),
                        };
                    }
                }

                // ========== 各阶段顺序执行 ==========
                if (!Array.isArray(stages)) {
                    throw new Error('stages 参数必须是数组');
                }
                for (let i = 0; i < stages.length; i++) {
                    const stage = stages[i];
                    if (!stage || !stage.name) continue;

                    if (typeof stage.skip === 'function' && stage.skip(ctx)) {
                        this.logger.debug(stage.name, '阶段跳过(skip 返回 true)', { stageIndex: i });
                        continue;
                    }

                    _currentStage = stage.name;
                    this.logger.startTimer(stage.name);
                    this.logger.info(stage.name, `阶段开始: ${stage.name}`, { stageIndex: i });

                    if (typeof stage.action === 'function') {
                        await stage.action(ctx);
                    }

                    this.logger.stopTimer(stage.name, `阶段完成: ${stage.name}`, { stageIndex: i });
                    _currentStage = null; // 阶段成功完成,清空
                }

                // ========== 异步任务(不阻塞,失败仅记日志) ==========
                if (Array.isArray(asyncTasks) && asyncTasks.length) {
                    this.logger.info('异步任务', '启动事务后异步任务', {
                        tasks: asyncTasks.map(t => t.name),
                    });
                    for (const task of asyncTasks) {
                        if (!task || !task.name) continue;
                        const startTs = Date.now();
                        this.logger.info(task.name, '异步任务启动', { async: true });
                        Promise.resolve(task.action(ctx))
                            .then(() => this.logger.info(task.name, '异步任务完成', {
                                elapsedMs: Date.now() - startTs,
                            }))
                            .catch(err => this.logger.error(task.name, '异步任务失败', {
                                error: err.message,
                                elapsedMs: Date.now() - startTs,
                            }));
                    }
                }

                this.logger.info('完成', `${this.name} 流程完成`, {
                    totalElapsedMs: this.logger.totalElapsedMs(),
                    executedStages: this.logger.executedStages(),
                });

                return {
                    success: true,
                    txId: ctx.txId,
                    ctx,
                    logs: this.logger.getAll(),
                };

            } catch (error) {
                // ========== 失败: 错误上下文 + 自动回滚 ==========
                // 优先使用 _currentStage(精确捕获抛错时正在执行的阶段)
                // 退化到 lastStage()(基于日志推断,可能因 adapter 内部 info 日志干扰)
                const failedAt = _currentStage || this.logger.lastStage() || '未知阶段';
                this.logger.error('回滚', `${this.name} 事务触发回滚`, {
                    failedStage: failedAt,
                    errorMessage: error.message,
                    errorCode: error.code || null,
                    errorStack: error.stack ? error.stack.split('\n').slice(0, 3).join(' | ') : null,
                    executedStages: this.logger.executedStages(),
                });

                // 逆序调用各阶段 onRollback(可选)
                if (this.autoRollbackStages && Array.isArray(stages)) {
                    const executed = this.logger.executedStages();
                    for (let i = executed.length - 1; i >= 0; i--) {
                        const stageName = executed[i];
                        const stage = stages.find(s => s && s.name === stageName);
                        if (stage && typeof stage.onRollback === 'function') {
                            try {
                                this.logger.info('回滚', `执行 ${stageName} 自定义回滚`);
                                await stage.onRollback(ctx);
                            } catch (rbErr) {
                                this.logger.error('回滚', `${stageName} 自定义回滚失败`, {
                                    error: rbErr.message,
                                });
                            }
                        }
                    }
                }

                // 全局回滚(通过 adapter)
                if (this.adapter && ctx.conn) {
                    this.logger.startTimer('回滚-ROLLBACK');
                    this.logger.info('回滚', '执行 ROLLBACK(恢复事务边界前状态)', {
                        connId: ctx.conn && (ctx.conn.id || ctx.conn._id) || 'unknown',
                    });
                    try {
                        await this.adapter.rollback(ctx.conn, ctx);
                    } catch (rbErr) {
                        this.logger.error('回滚', 'ROLLBACK 调用失败', { error: rbErr.message });
                    }
                    ctx.conn = null;
                    this.logger.stopTimer('回滚-ROLLBACK', 'ROLLBACK 执行完成(连接/快照已释放)', {
                        connReleased: true,
                    });
                }

                this.logger.error('回滚', `${this.name} 流程最终结果: 失败`, {
                    totalElapsedMs: this.logger.totalElapsedMs(),
                });

                return {
                    success: false,
                    error: error.message,
                    errorCode: error.code || null,
                    txId: ctx.txId,
                    failedStage: failedAt,
                    executedStages: this.logger.executedStages(),
                    ctx,
                    logs: this.logger.getAll(),
                };
            }
        }
    }

    return TransactionTemplate;
});
