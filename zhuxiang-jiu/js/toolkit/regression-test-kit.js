/**
 * regression-test-kit.js  ·  通用回归测试框架
 * ============================================================
 * 用途:
 *   为任何业务模块提供统一的回归测试运行框架,包含:
 *     · 断言工具(等于/近似/包含/存在/抛错)
 *     · 用例运行器(单用例计时/独立错误捕获)
 *     · 阶段完整性校验(验证日志包含所有预期阶段)
 *     · 报告生成(JSON 格式 + 多通道输出)
 *     · 输出适配(浏览器 DOM / Node console / 自定义 sink)
 *
 * 适用场景:
 *   · 事务流程回归测试(升级/降级/订单/分润)
 *   · 单元/集成测试自动化
 *   · CI/CD 流水线断言
 *   · 手动修改代码后一键回归
 *
 * 使用示例(参考 agent-upgrade-regression-test.js):
 *   const { RegressionTestKit } = require('./regression-test-kit.js');
 *
 *   const kit = new RegressionTestKit({
 *     name: '竹香酒官网 · 代理商升级服务',
 *     sink: (line, type) => console.log(line),  // 可选
 *   });
 *
 *   const EXPECTED_STAGES = [
 *     '阶段1-AI风险评估', '阶段2-开启事务', '阶段3-agents表',
 *     '阶段4-升级日志', '阶段10-事务提交', '完成',
 *   ];
 *
 *   const report = await kit.run({
 *     cases: [
 *       {
 *         name: 'TC1 升级 市级→核心',
 *         setup: () => AgentUpgradeClient.resetMock(),
 *         fn: async () => {
 *           const r = await AgentUpgradeClient.upgrade({...});
 *           kit.assertEqual(r.success, true, '升级应成功');
 *           kit.assertStages(r.logs, EXPECTED_STAGES, '11阶段事务');
 *           kit.assertIncludes(r.asyncOps, 'blockchain_notarize', '异步任务');
 *         },
 *       },
 *       // ... 更多用例
 *     ],
 *   });
 *
 *   if (report.success) { console.log('✅ 全部通过'); }
 *
 * 浏览器环境:
 *   <script src="regression-test-kit.js"></script>
 *   全局名: RegressionTestKit / window.RegressionTestKit
 * ============================================================
 */

(function (root, factory) {
    'use strict';
    const Kit = factory();
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { RegressionTestKit: Kit };
    }
    if (typeof root !== 'undefined') {
        root.RegressionTestKit = Kit;
    }
    if (typeof window !== 'undefined') {
        window.RegressionTestKit = Kit;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /**
     * 通用回归测试框架
     */
    class RegressionTestKit {
        /**
         * @param {Object} [opts]
         * @param {String} [opts.name]  - 测试集名称
         * @param {Function} [opts.sink] - 自定义输出 (line, type) => void
         *         type ∈ 'info' | 'pass' | 'fail' | 'warn'
         */
        constructor(opts = {}) {
            this.name = opts.name || '回归测试';
            this._sink = typeof opts.sink === 'function' ? opts.sink : null;
        }

        // ===================== 断言工具 =====================

        /** 布尔断言 */
        assert(cond, message) {
            if (!cond) throw new Error('断言失败: ' + message);
        }

        /** 严格相等 */
        assertEqual(actual, expected, message) {
            if (actual !== expected) {
                throw new Error((message || '断言失败') +
                    ` (期望 ${JSON.stringify(expected)}, 实际 ${JSON.stringify(actual)})`);
            }
        }

        /** 近似相等(数值容差) */
        assertApprox(actual, expected, eps, message) {
            if (Math.abs(actual - expected) > eps) {
                throw new Error((message || '断言失败') +
                    ` (期望约 ${expected}, 实际 ${actual}, 容差 ${eps})`);
            }
        }

        /** 数组包含(支持字符串子串匹配) */
        assertIncludes(arr, item, message) {
            if (!Array.isArray(arr)) {
                throw new Error((message || '断言失败') + ` (实际值非数组: ${typeof arr})`);
            }
            if (!arr.some(x => (typeof x === 'string' && typeof item === 'string' && x.includes(item)) || x === item)) {
                throw new Error((message || '断言失败') + ` (数组中未找到 ${JSON.stringify(item)})`);
            }
        }

        /** 存在性(非 null/undefined) */
        assertExists(value, message) {
            if (value === null || value === undefined) {
                throw new Error('断言失败: ' + (message || '值不应为 null/undefined'));
            }
        }

        /** 期望抛出异常 */
        async assertThrows(asyncFn, errorMatch, message) {
            let threw = false;
            let actualError = null;
            try {
                await asyncFn();
            } catch (e) {
                threw = true;
                actualError = e;
            }
            if (!threw) {
                throw new Error('断言失败: ' + (message || '期望抛出异常但未抛出'));
            }
            if (errorMatch && actualError && !String(actualError.message).includes(errorMatch)) {
                throw new Error('断言失败: ' + (message || `期望异常包含 "${errorMatch}", 实际: "${actualError.message}"`));
            }
        }

        /**
         * 校验日志包含所有预期阶段
         * @param {Array} logs        - 日志数组(每项含 step 字段)
         * @param {Array} expected    - 预期阶段名(子串匹配)
         * @param {String} [message]
         */
        assertStages(logs, expected, message) {
            if (!Array.isArray(logs)) {
                throw new Error((message || '断言失败') + ` (logs 非数组: ${typeof logs})`);
            }
            const steps = logs.map(l => (l && l.step) || '');
            const missing = expected.filter(s => !steps.some(g => typeof g === 'string' && g.includes(s)));
            if (missing.length > 0) {
                throw new Error((message || '断言失败') + ` 缺失事务阶段: ${missing.join(',')}`);
            }
        }

        // ===================== 输出适配 =====================

        /**
         * 输出一行(自动适配 DOM/console/sink)
         * @param {String} line
         * @param {String} [type]  'info' | 'pass' | 'fail' | 'warn'
         */
        emit(line, type = 'info') {
            if (this._sink) {
                this._sink(line, type);
                return;
            }
            // 浏览器 DOM
            if (typeof document !== 'undefined' && document.getElementById) {
                const logEl = document.getElementById('agentLog') || document.getElementById('testLog');
                if (logEl) {
                    const colorMap = { pass: '#0f0', fail: '#f88', warn: '#fc0', info: '#0ff' };
                    const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
                    const entry = document.createElement('div');
                    entry.style.color = colorMap[type] || '#0ff';
                    entry.innerHTML = `<span style="opacity:0.6;">[${t}]</span> ${line}`;
                    logEl.appendChild(entry);
                    logEl.scrollTop = logEl.scrollHeight;
                    return;
                }
            }
            // Node console 退化
            if (typeof console !== 'undefined' && console.log) {
                console.log(line);
            }
        }

        // ===================== 用例运行 =====================

        /**
         * 运行单个用例(独立计时 + 错误捕获)
         * @private
         */
        async _runOne(name, fn, setup) {
            const start = Date.now();
            try {
                if (typeof setup === 'function') await setup();
                await fn();
                return { name, status: 'PASS', duration: Date.now() - start, error: null };
            } catch (e) {
                return { name, status: 'FAIL', duration: Date.now() - start, error: e.message };
            }
        }

        /**
         * 运行用例集
         * @param {Object} params
         * @param {Array} params.cases  - [{ name, fn, setup? }]
         * @returns {Object} 报告 { timestamp, total, passed, failed, passRate, results, success }
         */
        async run({ cases }) {
            const sep = '═'.repeat(70);
            this.emit(sep, 'info');
            this.emit(`  ${this.name} - 回归测试`, 'info');
            this.emit('  日期: ' + new Date().toISOString().slice(0, 19).replace('T', ' '), 'info');
            this.emit(sep, 'info');

            const results = [];
            let passed = 0, failed = 0;
            for (const c of cases) {
                if (!c || !c.name || typeof c.fn !== 'function') continue;
                this.emit('──────────────────────────────────────────────────────────', 'info');
                this.emit('▶ 运行: ' + c.name, 'info');
                const r = await this._runOne(c.name, c.fn, c.setup);
                results.push(r);
                if (r.status === 'PASS') {
                    passed++;
                    this.emit('  ✓ PASS (' + r.duration + 'ms)', 'pass');
                } else {
                    failed++;
                    this.emit('  ✗ FAIL (' + r.duration + 'ms)', 'fail');
                    this.emit('    错误: ' + r.error, 'fail');
                }
            }

            this.emit('', 'info');
            this.emit(sep, 'info');
            const allPassed = failed === 0;
            const summary = `  回归测试${allPassed ? '全部通过' : '存在失败'}: ${passed}/${cases.length} PASS, ${failed} FAIL`;
            this.emit(summary, allPassed ? 'pass' : 'fail');
            this.emit(sep, allPassed ? 'pass' : 'fail');

            // 详细报告
            this.emit('', 'info');
            this.emit('详细报告:', 'info');
            results.forEach(r => {
                const icon = r.status === 'PASS' ? '✓' : '✗';
                const type = r.status === 'PASS' ? 'pass' : 'fail';
                this.emit(`  ${icon} ${r.name} [${r.duration}ms]${r.error ? ' - ' + r.error : ''}`, type);
            });

            const report = {
                timestamp: new Date().toISOString(),
                name: this.name,
                total: cases.length,
                passed,
                failed,
                passRate: ((passed / Math.max(cases.length, 1)) * 100).toFixed(1) + '%',
                results,
                success: allPassed,
            };

            if (typeof window !== 'undefined') {
                window.__lastRegressionReport = report;
            }
            return report;
        }
    }

    return RegressionTestKit;
});
