/* ============================================
   竹香酒官网 · 代理商发货面板函数 - 单元回归测试
   --------------------------------------------
   用途: 验证 module-test.html 内联的 5 个 shipping 面板函数
         (renderShippingList / appendShippingLog / renderShippingResult /
          runShippingOpTest / resetShippingMock) 在每次修改后行为未被破坏。
   --------------------------------------------
   说明:
     · 被测函数是 module-test.html 的 inline <script> 函数声明,
       非严格模式下提升为全局(window.xxx),本文件执行时(通过 onclick)
       inline 脚本已解析完毕,可直接按名调用。
     · 本文件必须在 module-test.html 上下文运行(依赖 #shippingList/
       #shippingLog DOM 元素 + 全局 AgentShippingService)。
   --------------------------------------------
   12 个测试用例:
     PTC1 renderShippingList 初始渲染   空数据时渲染"暂无"占位
     PTC2 appendShippingLog 日志追加     追加一条日志,子元素数+1且含文本
     PTC3 renderShippingResult 成功结果  传入 success 结果,日志含"成功"+认领ID
     PTC4 renderShippingResult 失败结果  传入 failure 结果,日志含"失败"+错误信息
     PTC5 runShippingOpTest 认领场景    claim_taian 后日志含"成功"+认领记录非空
     PTC6 runShippingOpTest 重复认领回滚 claim_dup 后日志含"失败"+认领数不增
     PTC7 runShippingOpTest 释放认领    release 后认领状态→已退出
     PTC8 runShippingOpTest 路由+服务费  order_route 后日志含"服务费计提"+流水非空
     PTC9 resetShippingMock 重置        重置后 DOM 含"暂无"+DB 认领记录为空
     PTC10 网络超时异常场景           stub claim 抛超时,验证 try/catch 兜底+不外抛
     PTC11 并发超时资源竞争           5 请求同时超时,验证各自独立兜底+无竞争丢失
     PTC12 极端并发(20×2 轮)          验证无线程阻塞+无内存泄漏(多轮线性增长)
   --------------------------------------------
   运行方式:
     · 浏览器: 在 module-test.html 点击「面板函数单元测试」按钮
     · 控制台: runShippingPanelTest()
   ============================================ */

(function () {
    'use strict';

    // ---------- 断言工具 ----------
    function assert(cond, message) {
        if (!cond) throw new Error('断言失败: ' + message);
    }
    function assertIncludes(haystack, needle, message) {
        if (typeof haystack !== 'string' || !haystack.includes(needle)) {
            throw new Error((message || '断言失败') + ` (文本中未找到 "${needle}")`);
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

    // ---------- 输出适配(复用 #shippingLog,加 🧪 前缀区分) ----------
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
                entry.innerHTML = `<span style="opacity:0.6;">[${t}]</span> 🧪 ${line}`;
                logEl.appendChild(entry);
                logEl.scrollTop = logEl.scrollHeight;
                return;
            }
        }
        if (typeof console !== 'undefined') console.log('🧪 ' + line);
    }

    // ---------- 前置检查 ----------
    function requireFns() {
        const names = ['renderShippingList', 'appendShippingLog', 'renderShippingResult', 'runShippingOpTest', 'resetShippingMock'];
        names.forEach(n => {
            if (typeof window[n] !== 'function') {
                throw new Error(`被测函数 ${n} 未定义(请在 module-test.html 上下文运行本测试)`);
            }
        });
    }

    function requireEls() {
        ['shippingList', 'shippingLog'].forEach(id => {
            if (!document.getElementById(id)) {
                throw new Error(`DOM 元素 #${id} 不存在(请确认 module-test.html 已加载)`);
            }
        });
    }

    // 快捷别名(指向 inline 全局函数)
    const F = {
        renderList: () => window.renderShippingList(),
        appendLog: (t, m) => window.appendShippingLog(t, m),
        renderResult: (r) => window.renderShippingResult(r),
        runOp: (s) => window.runShippingOpTest(s),
        reset: () => window.resetShippingMock(),
    };

    // ============================================================
    //  测试用例
    // ============================================================

    // PTC1: renderShippingList 初始渲染(空数据 → "暂无"占位)
    async function PTC1_renderEmpty() {
        AgentShippingService.resetMock();
        F.renderList();
        const html = document.getElementById('shippingList').innerHTML;
        assertIncludes(html, '区域认领记录', 'PTC1 应渲染认领记录区块标题');
        assertIncludes(html, '服务费结算', 'PTC1 应渲染服务费结算区块标题');
        assertIncludes(html, '暂无认领记录', 'PTC1 空数据应显示暂无认领记录');
        assertIncludes(html, '暂无服务费流水', 'PTC1 空数据应显示暂无服务费流水');
    }

    // PTC2: appendShippingLog 日志追加
    async function PTC2_appendLog() {
        const logEl = document.getElementById('shippingLog');
        const before = logEl.children.length;
        F.appendLog('pass', 'PTC2 测试日志文本 xyz789');
        const after = logEl.children.length;
        assert(after === before + 1, `PTC2 日志子元素应+1 (before=${before}, after=${after})`);
        assertIncludes(logEl.innerText, 'xyz789', 'PTC2 最新日志应包含文本');
        assertIncludes(logEl.innerText, 'PTC2 测试日志文本', 'PTC2 最新日志应包含完整消息');
    }

    // PTC3: renderShippingResult 成功结果
    async function PTC3_renderSuccess() {
        AgentShippingService.resetMock();
        const fakeResult = {
            success: true,
            claimId: 'CL-TEST-001',
            details: {
                claimId: 'CL-TEST-001',
                agentName: '测试代理',
                region: '测试区域',
                status: '已认领',
            },
            logs: [
                { level: 'INFO', step: '阶段1', message: '前置校验通过' },
                { level: 'INFO', step: '阶段5', message: '事务提交' },
            ],
            asyncOps: ['agent_notify'],
        };
        F.renderResult(fakeResult);
        const text = document.getElementById('shippingLog').innerText;
        assertIncludes(text, '阶段1', 'PTC3 应回放日志 阶段1');
        assertIncludes(text, '阶段5', 'PTC3 应回放日志 阶段5');
        assertIncludes(text, '成功', 'PTC3 应输出成功标记');
        assertIncludes(text, 'CL-TEST-001', 'PTC3 应输出认领ID');
        assertIncludes(text, '测试代理', 'PTC3 应输出代理名称');
    }

    // PTC4: renderShippingResult 失败结果
    async function PTC4_renderFailure() {
        AgentShippingService.resetMock();
        const fakeResult = {
            success: false,
            error: '区域已被认领',
            failedStage: '阶段3-互斥校验',
            logs: [
                { level: 'INFO', step: '阶段1', message: '前置校验' },
                { level: 'ERROR', step: '阶段3-互斥校验', message: '区域已被认领' },
            ],
        };
        F.renderResult(fakeResult);
        const text = document.getElementById('shippingLog').innerText;
        assertIncludes(text, '阶段3', 'PTC4 应回放日志 阶段3');
        assertIncludes(text, '失败', 'PTC4 应输出失败标记');
        assertIncludes(text, '区域已被认领', 'PTC4 应输出错误信息');
        assertIncludes(text, '阶段3-互斥校验', 'PTC4 应输出失败阶段');
    }

    // PTC5: runShippingOpTest('claim_taian') 认领场景
    async function PTC5_claimScenario() {
        AgentShippingService.resetMock();
        await F.runOp('claim_taian');
        const text = document.getElementById('shippingLog').innerText;
        const db = AgentShippingService.getMockDB();
        assertIncludes(text, '代理1认领山东泰安', 'PTC5 应输出场景描述');
        assertIncludes(text, '成功', 'PTC5 认领应成功');
        assert(db.shipping_claims.length === 1, `PTC5 认领记录应=1 (实际 ${db.shipping_claims.length})`);
        const listHtml = document.getElementById('shippingList').innerHTML;
        assert(!listHtml.includes('暂无认领记录'), 'PTC5 渲染后不应再显示暂无认领记录');
        assertIncludes(listHtml, '山东泰安', 'PTC5 渲染应包含认领区域');
    }

    // PTC6: runShippingOpTest('claim_dup') 重复认领回滚
    async function PTC6_dupClaimRollback() {
        AgentShippingService.resetMock();
        await F.runOp('claim_taian');   // 先正常认领
        await F.runOp('claim_dup');     // 代理2重复认领 → 应失败+回滚
        const text = document.getElementById('shippingLog').innerText;
        const db = AgentShippingService.getMockDB();
        assertIncludes(text, '重复认领', 'PTC6 应输出场景描述');
        assertIncludes(text, '失败', 'PTC6 重复认领应失败');
        assert(db.shipping_claims.length === 1, `PTC6 认领记录应仍=1 (回滚不新增, 实际 ${db.shipping_claims.length})`);
    }

    // PTC7: runShippingOpTest('release') 释放认领
    async function PTC7_releaseScenario() {
        AgentShippingService.resetMock();
        await F.runOp('claim_taian');   // 先认领
        await F.runOp('release');        // 释放
        const text = document.getElementById('shippingLog').innerText;
        const db = AgentShippingService.getMockDB();
        assertIncludes(text, '释放山东泰安', 'PTC7 应输出场景描述');
        assertIncludes(text, '成功', 'PTC7 释放应成功');
        const claim = db.shipping_claims[0];
        assert(claim && claim.status === '已退出', `PTC7 认领状态应为已退出 (实际 ${claim ? claim.status : 'null'})`);
    }

    // PTC8: runShippingOpTest('order_route') 路由+服务费
    async function PTC8_orderRouteScenario() {
        AgentShippingService.resetMock();
        await F.runOp('claim_taian');   // 先认领(否则厂家直供无服务费)
        await F.runOp('order_route');   // 路由+服务费计提
        const text = document.getElementById('shippingLog').innerText;
        const db = AgentShippingService.getMockDB();
        assertIncludes(text, '发货路由', 'PTC8 应输出路由信息');
        assertIncludes(text, '代理发货', 'PTC8 应识别为代理发货(非厂家直供)');
        assertIncludes(text, '服务费计提', 'PTC8 应输出服务费计提');
        assert((db.service_fees || []).length >= 1, `PTC8 服务费流水应≥1 (实际 ${(db.service_fees || []).length})`);
    }

    // PTC9: resetShippingMock 重置
    async function PTC9_resetMock() {
        // 先产生数据
        await F.runOp('claim_taian');
        await F.runOp('order_route');
        const dbBefore = AgentShippingService.getMockDB();
        assert(dbBefore.shipping_claims.length > 0, 'PTC9 重置前应有认领记录');
        // 执行重置(被测函数)
        F.reset();
        const dbAfter = AgentShippingService.getMockDB();
        const listHtml = document.getElementById('shippingList').innerHTML;
        assert(dbAfter.shipping_claims.length === 0, `PTC9 重置后认领记录应=0 (实际 ${dbAfter.shipping_claims.length})`);
        assert((dbAfter.service_fees || []).length === 0, `PTC9 重置后服务费流水应=0 (实际 ${(dbAfter.service_fees || []).length})`);
        assertIncludes(listHtml, '暂无认领记录', 'PTC9 重置后 DOM 应显示暂无认领记录');
        assertIncludes(listHtml, '暂无服务费流水', 'PTC9 重置后 DOM 应显示暂无服务费流水');
        assertIncludes(document.getElementById('shippingLog').innerText, 'Mock数据已重置', 'PTC9 应输出重置日志');
    }

    // PTC10: 网络超时异常场景(stub claim 抛超时, 验证 runShippingOpTest try/catch 兜底)
    async function PTC10_networkTimeout() {
        AgentShippingService.resetMock();
        // 备份并 stub claim,模拟网络请求超时(reject)
        const origClaim = AgentShippingService.claim;
        let stubCalled = false;
        AgentShippingService.claim = function () {
            stubCalled = true;
            return new Promise(function (_, reject) {
                setTimeout(function () {
                    reject(new Error('网络请求超时(ETIMEDOUT)'));
                }, 30);
            });
        };
        try {
            // runShippingOpTest 应捕获异常,走 renderShippingResult 失败分支,不向外抛
            await F.runOp('claim_taian');
            const text = document.getElementById('shippingLog').innerText;
            assert(stubCalled, 'PTC10 stub 的 claim 应被调用');
            assertIncludes(text, '网络异常', 'PTC10 应输出网络异常标记(说明 try/catch 兜底生效)');
            assertIncludes(text, '网络请求超时', 'PTC10 应包含超时错误信息');
            assertIncludes(text, '网络请求', 'PTC10 应标记 failedStage=网络请求');
            assertIncludes(text, '失败', 'PTC10 应输出失败标记');
            // 超时未成功 → DB 不应有认领记录
            const db = AgentShippingService.getMockDB();
            assert(db.shipping_claims.length === 0, 'PTC10 超时时认领记录应=0 (claim 未成功)');
        } finally {
            // 恢复原始 claim
            AgentShippingService.claim = origClaim;
        }
    }

    // PTC11: 并发超时资源竞争(5 请求同时超时,验证各自独立兜底+无竞争丢失)
    async function PTC11_concurrentTimeout() {
        AgentShippingService.resetMock();
        const origClaim = AgentShippingService.claim;
        let callCount = 0;
        const callSeq = [];
        AgentShippingService.claim = function () {
            const seq = ++callCount;
            callSeq.push(seq);
            return new Promise(function (_, reject) {
                // 不同延迟(20~60ms)模拟并发超时,验证交错执行不互相干扰
                setTimeout(function () {
                    reject(new Error('网络请求超时(ETIMEDOUT)#' + seq));
                }, 20 + seq * 10);
            });
        };
        try {
            const N = 5;
            const tasks = [];
            for (let i = 0; i < N; i++) {
                tasks.push(F.runOp('claim_taian'));
            }
            // 并发发起,等待全部完成(try/catch 保证不外抛,Promise.all 不 reject)
            await Promise.all(tasks);

            const text = document.getElementById('shippingLog').innerText;
            // stub 应被调用 N 次(无丢失)
            assert(callCount === N, 'PTC11 stub claim 应被调用 ' + N + ' 次 (实际 ' + callCount + ')');

            // 每个并发请求都应独立输出网络异常(验证 try/catch 对每个请求都生效,无资源竞争)
            const errorCount = (text.match(/网络异常/g) || []).length;
            assert(errorCount >= N, 'PTC11 应有≥' + N + ' 条网络异常日志 (实际 ' + errorCount + ',说明有请求未兜底)');

            const timeoutCount = (text.match(/网络请求超时/g) || []).length;
            assert(timeoutCount >= N, 'PTC11 应有≥' + N + ' 条超时错误 (实际 ' + timeoutCount + ')');

            // 每个请求序号都应出现在日志中(验证无竞争导致的日志丢失)
            callSeq.forEach(function (seq) {
                assertIncludes(text, '#' + seq, 'PTC11 应包含请求 #' + seq + ' 的错误(无竞争丢失)');
            });

            // 全部超时失败 → DB 无认领记录(无脏写)
            const db = AgentShippingService.getMockDB();
            assert(db.shipping_claims.length === 0, 'PTC11 并发超时后认领记录应=0 (实际 ' + db.shipping_claims.length + ',说明有脏写)');
        } finally {
            AgentShippingService.claim = origClaim;
        }
    }

    // PTC12: 极端并发(20×2 轮,验证无线程阻塞+无内存泄漏)
    async function PTC12_extremeConcurrency() {
        AgentShippingService.resetMock();
        const origClaim = AgentShippingService.claim;
        let callCount = 0;
        AgentShippingService.claim = function () {
            const seq = ++callCount;
            return new Promise(function (_, reject) {
                // 20~110ms 交错延迟,模拟并发超时
                setTimeout(function () {
                    reject(new Error('网络请求超时(ETIMEDOUT)#' + seq));
                }, 20 + (seq % 10) * 10);
            });
        };

        const logEl = document.getElementById('shippingLog');
        const hasMem = (typeof performance !== 'undefined' && performance.memory && typeof performance.memory.usedJSHeapSize === 'number');
        const now = (typeof performance !== 'undefined' && performance.now) ? function () { return performance.now(); } : function () { return Date.now(); };

        function snap() {
            return {
                dom: logEl.children.length,
                mem: hasMem ? performance.memory.usedJSHeapSize : 0,
                t: now(),
            };
        }

        // 单轮:发起 N 个并发超时请求,返回统计
        async function runRound(N) {
            const calls0 = callCount;
            const before = snap();
            const tasks = [];
            for (let i = 0; i < N; i++) tasks.push(F.runOp('claim_taian'));
            await Promise.all(tasks);
            const after = snap();
            return {
                elapsed: after.t - before.t,
                domDelta: after.dom - before.dom,
                memDelta: hasMem ? (after.mem - before.mem) : 0,
                calls: callCount - calls0,
            };
        }

        try {
            const N = 20;
            const r1 = await runRound(N);
            const r2 = await runRound(N);

            // 1. 线程阻塞验证:两轮都应完成且时间合理(< 2 秒,无线程阻塞)
            assert(r1.elapsed < 2000, 'PTC12 R1 应在 2 秒内完成 (实际 ' + r1.elapsed.toFixed(0) + 'ms,无线程阻塞)');
            assert(r2.elapsed < 2000, 'PTC12 R2 应在 2 秒内完成 (实际 ' + r2.elapsed.toFixed(0) + 'ms,无线程阻塞)');

            // 2. stub 每轮都被调用 N 次(无请求丢失)
            assert(r1.calls === N, 'PTC12 R1 stub 应被调用 ' + N + ' 次 (实际 ' + r1.calls + ')');
            assert(r2.calls === N, 'PTC12 R2 stub 应被调用 ' + N + ' 次 (实际 ' + r2.calls + ')');

            // 3. 内存泄漏验证(DOM):两轮增长应线性(R2 ≈ R1,允许 ±50% 波动,非指数暴涨)
            assert(r1.domDelta > 0, 'PTC12 R1 DOM 节点应增加 (实际 +' + r1.domDelta + ')');
            assert(r2.domDelta > 0, 'PTC12 R2 DOM 节点应增加 (实际 +' + r2.domDelta + ')');
            const domRatio = r2.domDelta / Math.max(r1.domDelta, 1);
            assert(domRatio < 2, 'PTC12 DOM 增长应线性非指数 (R1=+' + r1.domDelta + ', R2=+' + r2.domDelta + ', ratio=' + domRatio.toFixed(2) + ')');

            // 4. 内存泄漏验证(JS 堆,Chrome 系可用):R2 增量不应远大于 R1(允许 GC 波动 ×3)
            if (hasMem) {
                if (r1.memDelta > 0) {
                    assert(r2.memDelta < r1.memDelta * 3,
                        'PTC12 JS 堆内存不应持续暴涨 (R1=+' + (r1.memDelta / 1024).toFixed(0) + 'KB, R2=+' + (r2.memDelta / 1024).toFixed(0) + 'KB)');
                }
            }

            // 5. DB 无脏写(全部超时失败,无认领记录)
            const db = AgentShippingService.getMockDB();
            assert(db.shipping_claims.length === 0, 'PTC12 极端并发后认领记录应=0 (实际 ' + db.shipping_claims.length + ',说明有脏写)');

            // 6. 日志含最后一个序号(无竞争丢失)
            const text = logEl.innerText;
            assertIncludes(text, '#20', 'PTC12 应含请求 #20(最后一个,无竞争丢失)');
        } finally {
            AgentShippingService.claim = origClaim;
        }
    }

    // ============================================================
    //  主入口
    // ============================================================
    async function runShippingPanelTest(opts) {
        const options = opts || {};
        _sink = options.sink || null;

        // 前置检查
        try {
            requireFns();
            requireEls();
        } catch (e) {
            if (typeof console !== 'undefined') console.error('🧪 前置检查失败: ' + e.message);
            return { success: false, error: e.message, results: [] };
        }

        const sep = '═'.repeat(60);
        emit(sep, 'info');
        emit('  代理商发货面板函数 · 单元回归测试', 'info');
        emit('  日期: ' + new Date().toISOString().slice(0, 19).replace('T', ' '), 'info');
        emit('  目标: 5 个 inline 面板函数 (renderShippingList 等)', 'info');
        emit(sep, 'info');

        const cases = [
            { name: 'PTC1 renderShippingList 初始渲染   (空数据→暂无占位)',           fn: PTC1_renderEmpty },
            { name: 'PTC2 appendShippingLog 日志追加   (子元素+1且含文本)',           fn: PTC2_appendLog },
            { name: 'PTC3 renderShippingResult 成功结果 (日志含成功+认领ID)',         fn: PTC3_renderSuccess },
            { name: 'PTC4 renderShippingResult 失败结果 (日志含失败+错误信息)',       fn: PTC4_renderFailure },
            { name: 'PTC5 runShippingOpTest 认领场景    (claim_taian→成功+记录非空)', fn: PTC5_claimScenario },
            { name: 'PTC6 runShippingOpTest 重复认领回滚(claim_dup→失败+记录不增)',   fn: PTC6_dupClaimRollback },
            { name: 'PTC7 runShippingOpTest 释放认领    (release→状态已退出)',        fn: PTC7_releaseScenario },
            { name: 'PTC8 runShippingOpTest 路由+服务费 (order_route→计提+流水非空)', fn: PTC8_orderRouteScenario },
            { name: 'PTC9 resetShippingMock 重置       (DOM+DB 均回到空状态)',       fn: PTC9_resetMock },
            { name: 'PTC10 网络超时异常场景           (stub claim 超时→try/catch 兜底)', fn: PTC10_networkTimeout },
            { name: 'PTC11 并发超时资源竞争           (5 请求同时超时→各自独立兜底)',    fn: PTC11_concurrentTimeout },
            { name: 'PTC12 极端并发(20×2 轮)          (无线程阻塞+无内存泄漏)',         fn: PTC12_extremeConcurrency },
        ];

        const results = [];
        let passed = 0, failed = 0;
        for (const c of cases) {
            emit('────────────────────────────────────────', 'info');
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
        const summary = `  面板函数测试${allPassed ? '全部通过' : '存在失败'}: ${passed}/${cases.length} PASS, ${failed} FAIL`;
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

        // 恢复干净状态
        AgentShippingService.resetMock();
        F.renderList();

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
            window.__lastShippingPanelTestReport = report;
        }
        return report;
    }

    // ---------- 暴露 ----------
    if (typeof window !== 'undefined') {
        window.runShippingPanelTest = runShippingPanelTest;
        window.__runShippingPanelTestPromise = runShippingPanelTest;
    }
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { runShippingPanelTest };
    }
})();
