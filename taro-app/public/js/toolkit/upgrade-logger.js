/**
 * upgrade-logger.js  ·  通用结构化事务日志器
 * ============================================================
 * 用途:
 *   为任意需要"事务边界 + 多阶段流程 + 故障排查"的模块提供
 *   统一的日志能力,包含事务ID关联、阶段计时器、已执行阶段
 *   追踪、错误上下文捕获、多通道输出等。
 *
 * 适用场景:
 *   · 多阶段数据库事务(升级/降级/订单/分润/钱包)
 *   · 跨模块联动流程(主事务 + 异步任务)
 *   · 定时任务执行过程追踪
 *   · 任何需要审计回溯的复杂业务流程
 *
 * 特性:
 *   1. 自动事务ID: tx-时间戳-随机后缀,方便关联同一次流程所有日志
 *   2. 计时器: startTimer/stopTimer 自动写入耗时
 *   3. 阶段追踪: executedStages/lastStage 用于回滚时定位失败点
 *   4. 结构化日志: 每条记录含 step/level/message/data/txId/timestamp
 *   5. 多通道输出: 同时输出到 console + 内存数组 + 自定义 sink
 *   6. 级别筛选: getByLevel / filter
 *   7. 序列化: toJSON / 从 JSON 恢复
 *
 * 使用示例:
 *   const logger = new UpgradeLogger({ prefix: 'order' });
 *   logger.info('阶段1-校验', '开始参数校验', { orderId: 1001 });
 *   logger.startTimer('阶段2-库存');
 *   // ... 业务代码 ...
 *   logger.stopTimer('阶段2-库存', '库存扣减完成', { affected: 3 });
 *   logger.error('回滚', '事务触发回滚', {
 *     failedStage: logger.lastStage(),
 *     error: err.message,
 *   });
 *   // 导出全部日志
 *   const allLogs = logger.getAll();
 *
 * 浏览器环境:
 *   直接 <script src="upgrade-logger.js"></script>
 *   全局名: UpgradeLogger / window.UpgradeLogger
 *
 * Node.js 环境:
 *   const { UpgradeLogger } = require('./upgrade-logger.js');
 * ============================================================
 */

(function (root, factory) {
    'use strict';
    const Logger = factory();
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { UpgradeLogger: Logger };
    }
    if (typeof root !== 'undefined') {
        root.UpgradeLogger = Logger;
    }
    if (typeof window !== 'undefined') {
        window.UpgradeLogger = Logger;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const LEVELS = ['TRACE', 'DEBUG', 'INFO', 'WARN', 'ERROR'];

    /**
     * 通用结构化事务日志器
     */
    class UpgradeLogger {
        /**
         * @param {Object} [opts]
         * @param {String} [opts.txId]      - 指定事务ID(默认自动生成)
         * @param {String} [opts.prefix]    - 事务ID前缀(默认 'tx')
         * @param {Function} [opts.sink]     - 自定义输出函数 (entry) => void
         * @param {Boolean} [opts.silent]   - 是否静默 console(默认 false)
         * @param {Number} [opts.minLevel]  - 最低输出级别 0-4(默认 0=TRACE)
         */
        constructor(opts = {}) {
            this.logs = [];
            this.txId = opts.txId || (
                (opts.prefix || 'tx') + '-' +
                Date.now().toString(36) + '-' +
                Math.random().toString(36).slice(2, 6)
            );
            this._timers = {};
            this._sink = typeof opts.sink === 'function' ? opts.sink : null;
            this._silent = !!opts.silent;
            this._minLevel = typeof opts.minLevel === 'number' ? opts.minLevel : 0;
        }

        /**
         * 写入一条日志
         * @param {String} step    - 阶段标识(如 '阶段3-agents表更新')
         * @param {String} level   - TRACE/DEBUG/INFO/WARN/ERROR
         * @param {String} message - 文本描述
         * @param {Object} [data]   - 附加数据(影响行数/SQL/耗时等)
         */
        log(step, level, message, data = {}) {
            const levelIdx = LEVELS.indexOf(level);
            const entry = {
                step,
                level,
                message,
                data,
                txId: this.txId,
                timestamp: new Date().toISOString(),
            };
            this.logs.push(entry);

            // 多通道输出
            if (levelIdx >= this._minLevel) {
                if (!this._silent && typeof console !== 'undefined' && console.log) {
                    console.log(`[${level}] ${this.txId} | ${step} | ${message}`, data);
                }
                if (this._sink) {
                    try { this._sink(entry); } catch (e) { /* sink 异常不影响主流程 */ }
                }
            }
            return entry;
        }

        /** 标准便捷方法 */
        info(step, message, data) { return this.log(step, 'INFO', message, data || {}); }
        warn(step, message, data) { return this.log(step, 'WARN', message, data || {}); }
        error(step, message, data) { return this.log(step, 'ERROR', message, data || {}); }
        debug(step, message, data) { return this.log(step, 'DEBUG', message, data || {}); }
        trace(step, message, data) { return this.log(step, 'TRACE', message, data || {}); }

        /**
         * 计时器:开始计时
         * @param {String} step  - 同一 step 可重复 start/stop
         */
        startTimer(step) {
            this._timers[step] = Date.now();
            return this;
        }

        /**
         * 计时器:停止计时并写入 INFO 日志
         * @param {String} step
         * @param {String} message - 完成描述
         * @param {Object} [extra]  - 附加字段
         */
        stopTimer(step, message, extra = {}) {
            const startedAt = this._timers[step];
            const elapsedMs = startedAt ? Date.now() - startedAt : -1;
            delete this._timers[step];
            return this.info(step, message, { ...extra, elapsedMs });
        }

        /**
         * 摘要:已记录的日志阶段列表(按首次出现顺序)
         * 用于回滚时输出"已执行阶段"
         */
        executedStages() {
            const seen = new Set();
            const list = [];
            for (const l of this.logs) {
                if (l.level === 'INFO' || l.level === 'DEBUG' || l.level === 'WARN') {
                    if (!seen.has(l.step)) { seen.add(l.step); list.push(l.step); }
                }
            }
            return list;
        }

        /** 最后一个已执行阶段(回滚定位失败点用) */
        lastStage() {
            const stages = this.executedStages();
            return stages.length ? stages[stages.length - 1] : null;
        }

        /** 按级别筛选 */
        getByLevel(level) { return this.logs.filter(l => l.level === level); }

        /** 按阶段筛选 */
        getByStep(step) { return this.logs.filter(l => l.step === step); }

        /** 总耗时(首条日志到末条日志) */
        totalElapsedMs() {
            if (this.logs.length < 2) return 0;
            const t0 = new Date(this.logs[0].timestamp).getTime();
            const t1 = new Date(this.logs[this.logs.length - 1].timestamp).getTime();
            return t1 - t0;
        }

        /** 获取全部日志(浅拷贝) */
        getAll() { return this.logs.slice(); }

        /** 转为 JSON 字符串 */
        toJSON() { return JSON.stringify({ txId: this.txId, logs: this.logs }, null, 2); }

        /** 从 JSON 恢复 */
        static fromJSON(json) {
            const obj = typeof json === 'string' ? JSON.parse(json) : json;
            const logger = new UpgradeLogger({ txId: obj.txId, silent: true });
            logger.logs = Array.isArray(obj.logs) ? obj.logs : [];
            return logger;
        }

        /** 清空日志 */
        clear() { this.logs = []; this._timers = {}; return this; }

        /**
         * 创建一个共享 txId 的子日志器(适用于异步子任务继承父事务ID)
         * @returns {UpgradeLogger}
         */
        child() {
            return new UpgradeLogger({
                txId: this.txId,
                sink: this._sink,
                silent: this._silent,
                minLevel: this._minLevel,
            });
        }
    }

    return UpgradeLogger;
});
