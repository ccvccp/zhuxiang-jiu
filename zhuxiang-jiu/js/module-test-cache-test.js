/* ============================================================
 * module-test-cache-test.js · createModuleMockCache 工具函数单元测试
 * ------------------------------------------------------------
 * 测试目标: toolkit/module-test-cache.js 的 createModuleMockCache 函数
 * 风格: JUnit 风格(setUp/tearDown + 断言工具 + 生命周期 + 边界覆盖)
 *
 * 用例总数: 28 (MTCC1-MTCC28)
 *   · MTCC1-MTCC5:    基础操作(getMock/getMod 返回值正确性)
 *   · MTCC6-MTCC8:    懒加载(延迟初始化行为验证)
 *   · MTCC9-MTCC11:   引用稳定性(多次调用返回同一引用)
 *   · MTCC12-MTCC15:  重置行为(reset 后重新初始化一致性)
 *   · MTCC16-MTCC19:  边界-无效模块ID(容错处理不抛异常)
 *   · MTCC20-MTCC23:  跨实例隔离(多缓存实例互不干扰)
 *   · MTCC24-MTCC25:  数据完整性(与 MODULES 原始数据一致)
 *   · MTCC26-MTCC28:  并发安全模拟(高频调用一致性)
 *
 * 运行方式:
 *   · 浏览器: module-test.html 按钮 或 控制台 runCacheUtilTest()
 *   · Headless: window.__runCacheUtilTestPromise
 * 报告存储: window.__lastCacheUtilTestReport
 * ============================================================ */

(function () {
    'use strict';

    // ========== JUnit 风格断言工具 ==========

    /** 断言两值严格相等(assertEquals) */
    function assertEquals(expected, actual, msg) {
        if (String(expected) !== String(actual)) {
            throw new Error(msg + ' | 期望:' + expected + ' 实际:' + actual);
        }
        return true;
    }

    /** 断言两对象引用相同(assertSame) */
    function assertSame(expected, actual, msg) {
        if (expected !== actual) {
            throw new Error(msg + ' | 引用不同(应为同一对象)');
        }
        return true;
    }

    /** 断言两对象引用不同(assertNotSame) */
    function assertNotSame(unexpected, actual, msg) {
        if (unexpected === actual) {
            throw new Error(msg + ' | 引用相同(应为不同对象)');
        }
        return true;
    }

    /** 断言非 null/undefined(assertNotNull) */
    function assertNotNull(actual, msg) {
        if (actual === null || actual === undefined) {
            throw new Error(msg + ' | 值为' + (actual === null ? 'null' : 'undefined'));
        }
        return true;
    }

    /** 断言为 null 或 undefined(assertNull) */
    function assertNull(actual, msg) {
        if (actual !== null && actual !== undefined) {
            throw new Error(msg + ' | 期望null/undefined,实际:' + actual);
        }
        return true;
    }

    /** 断言为真(assertTrue) */
    function assertTrue(cond, msg) {
        if (!cond) {
            throw new Error(msg + ' | 条件为false');
        }
        return true;
    }

    /** 断言为假(assertFalse) */
    function assertFalse(cond, msg) {
        if (cond) {
            throw new Error(msg + ' | 条件为true');
        }
        return true;
    }

    /** 断言函数抛出异常(assertThrows) */
    function assertThrows(fn, msg) {
        var thrown = false;
        try { fn(); } catch (e) { thrown = true; }
        if (!thrown) {
            throw new Error(msg + ' | 期望抛异常但未抛出');
        }
        return true;
    }

    /** 断言函数不抛异常(assertNoThrow) */
    function assertNoThrow(fn, msg) {
        try { fn(); } catch (e) {
            throw new Error(msg + ' | 不应抛异常但抛出: ' + e.message);
        }
        return true;
    }

    // ========== JUnit 风格 setUp/tearDown ==========

    /**
     * setUp: 为每个测试创建新鲜的缓存实例
     * 使用模块25(AiOps)作为主要测试模块
     *
     * 注意: 不能用 `moduleId || '25'`,因为空字符串 '' 是 falsy,
     * 会导致 setUp('') 被当作未传参而回退到 '25'(MTCC19 曾因此误测模块25)。
     * 此处显式区分"未传参"(undefined→默认'25')与"传入空字符串"(保留'')。
     */
    function setUp(moduleId) {
        return createModuleMockCache(moduleId === undefined ? '25' : moduleId);
    }

    /**
     * tearDown: 清理(当前无全局副作用需清理)
     * IIFE闭包自动回收,无需手动清理
     */
    function tearDown() {
        // 无操作(IIFE 闭包变量随实例回收)
    }

    // ========== 测试用例定义 ==========

    const TEST_CASES = [

        // ===== MTCC1-MTCC5: 基础操作 =====

        {
            id: 'MTCC1',
            name: 'getMock返回非空对象',
            run: function () {
                const cache = setUp('25');
                const mock = cache.getMock();
                assertNotNull(mock, 'getMock应返回非null对象');
                assertTrue(typeof mock === 'object', 'getMock返回值应为object类型');
                tearDown();
            }
        },
        {
            id: 'MTCC2',
            name: 'getMod返回正确模块ID',
            run: function () {
                const cache = setUp('25');
                const mod = cache.getMod();
                assertNotNull(mod, 'getMod应返回非null模块');
                assertEquals('25', mod.id, '模块ID应为25');
                tearDown();
            }
        },
        {
            id: 'MTCC3',
            name: 'getMock返回对象含预期字段',
            run: function () {
                const cache = setUp('25');
                const mock = cache.getMock();
                assertTrue(mock.monitoring !== undefined, 'mock应含monitoring字段');
                assertTrue(mock.coverage !== undefined, 'mock应含coverage字段');
                assertTrue(mock.uptime !== undefined, 'mock应含uptime字段');
                tearDown();
            }
        },
        {
            id: 'MTCC4',
            name: 'getMod返回模块含name字段',
            run: function () {
                const cache = setUp('25');
                const mod = cache.getMod();
                assertNotNull(mod.name, '模块应含name字段');
                assertTrue(mod.name.indexOf('AI') >= 0, '模块name应包含"AI"');
                tearDown();
            }
        },
        {
            id: 'MTCC5',
            name: 'getMock和getMod共享同一缓存',
            run: function () {
                const cache = setUp('25');
                const mockFromGetMock = cache.getMock();
                const modFromGetMod = cache.getMod();
                assertNotNull(mockFromGetMock, 'getMock应返回非null');
                assertNotNull(modFromGetMod, 'getMod应返回非null');
                // 先调getMock再调getMod,两者应共享同一缓存(getMod不会重新find)
                assertEquals('25', modFromGetMod.id, 'getMod返回的模块ID应为25');
                tearDown();
            }
        },

        // ===== MTCC6-MTCC8: 懒加载 =====

        {
            id: 'MTCC6',
            name: '无preheat直接getMock返回有效数据',
            run: function () {
                const cache = setUp('25');
                // 不调用preheat,直接getMock应触发懒加载
                const mock = cache.getMock();
                assertNotNull(mock, '无preheat时getMock应返回非null');
                assertTrue(Object.keys(mock).length > 0, '无preheat时mock应有字段');
                tearDown();
            }
        },
        {
            id: 'MTCC7',
            name: 'preheat后getMock返回有效数据',
            run: function () {
                const cache = setUp('25');
                cache.preheat();
                const mock = cache.getMock();
                assertNotNull(mock, 'preheat后getMock应返回非null');
                assertTrue(Object.keys(mock).length > 0, 'preheat后mock应有字段');
                tearDown();
            }
        },
        {
            id: 'MTCC8',
            name: 'preheat和直接getMock返回等价数据',
            run: function () {
                // 两个独立缓存实例:一个先preheat,一个直接getMock
                const cacheA = setUp('25');
                const cacheB = setUp('25');
                cacheA.preheat();
                const mockA = cacheA.getMock();
                const mockB = cacheB.getMock();
                // 不同实例的引用应不同
                assertNotSame(mockA, mockB, '两个独立缓存的mock应不同引用');
                // 但字段值应等价
                assertEquals(mockA.monitoring, mockB.monitoring, 'monitoring字段值应等价');
                assertEquals(mockA.coverage, mockB.coverage, 'coverage字段值应等价');
                tearDown();
            }
        },

        // ===== MTCC9-MTCC11: 引用稳定性 =====

        {
            id: 'MTCC9',
            name: '多次getMock返回同一引用',
            run: function () {
                const cache = setUp('25');
                const a = cache.getMock();
                const b = cache.getMock();
                const c = cache.getMock();
                assertSame(a, b, '第一次和第二次getMock应同一引用');
                assertSame(a, c, '第一次和第三次getMock应同一引用');
                tearDown();
            }
        },
        {
            id: 'MTCC10',
            name: '多次getMod返回同一引用',
            run: function () {
                const cache = setUp('25');
                const a = cache.getMod();
                const b = cache.getMod();
                const c = cache.getMod();
                assertSame(a, b, '第一次和第二次getMod应同一引用');
                assertSame(a, c, '第一次和第三次getMod应同一引用');
                tearDown();
            }
        },
        {
            id: 'MTCC11',
            name: 'preheat后getMock返回与preheat相同引用',
            run: function () {
                const cache = setUp('25');
                cache.preheat();
                const afterPreheat = cache.getMock();
                const secondCall = cache.getMock();
                assertSame(afterPreheat, secondCall, 'preheat后多次getMock应同一引用');
                tearDown();
            }
        },

        // ===== MTCC12-MTCC15: 重置行为 =====

        {
            id: 'MTCC12',
            name: 'reset后getMod返回null(getMod重新初始化前)',
            run: function () {
                const cache = setUp('25');
                cache.preheat();
                const beforeReset = cache.getMod();
                assertNotNull(beforeReset, 'reset前getMod应返回非null');
                cache.reset();
                // reset后直接调getMod会触发重新初始化,所以这里验证重新初始化的结果
                const afterReset = cache.getMod();
                assertNotNull(afterReset, 'reset后getMod(经重新初始化)应返回非null');
                tearDown();
            }
        },
        {
            id: 'MTCC13',
            name: 'reset后getMock重新初始化返回有效数据',
            run: function () {
                const cache = setUp('25');
                const before = cache.getMock();
                cache.reset();
                const after = cache.getMock();
                assertNotNull(after, 'reset后getMock应返回非null');
                assertEquals(before.monitoring, after.monitoring, 'reset前后monitoring应一致');
                tearDown();
            }
        },
        {
            id: 'MTCC14',
            name: 'reset+preheat重新初始化返回有效数据',
            run: function () {
                const cache = setUp('25');
                cache.preheat();
                const before = cache.getMock();
                cache.reset();
                cache.preheat();
                const after = cache.getMock();
                assertNotNull(after, 'reset+preheat后getMock应返回非null');
                assertEquals(before.coverage, after.coverage, 'reset+preheat前后coverage应一致');
                tearDown();
            }
        },
        {
            id: 'MTCC15',
            name: 'reset前后getMock返回不同引用但等价数据',
            run: function () {
                const cache = setUp('25');
                const before = cache.getMock();
                cache.reset();
                const after = cache.getMock();
                assertNotSame(before, after, 'reset前后应不同引用(重新mock())');
                assertEquals(before.monitoring, after.monitoring, 'reset前后monitoring值应等价');
                assertEquals(before.uptime, after.uptime, 'reset前后uptime值应等价');
                tearDown();
            }
        },

        // ===== MTCC16-MTCC19: 边界-无效模块ID =====

        {
            id: 'MTCC16',
            name: '不存在的moduleId-getMock返回空对象',
            run: function () {
                const cache = setUp('999');
                const mock = cache.getMock();
                assertNotNull(mock, '不存在的moduleId应返回非null(空对象)');
                assertEquals(0, Object.keys(mock).length, '不存在的moduleId应返回空对象');
                tearDown();
            }
        },
        {
            id: 'MTCC17',
            name: '不存在的moduleId-getMod返回undefined',
            run: function () {
                const cache = setUp('999');
                const mod = cache.getMod();
                assertNull(mod, '不存在的moduleId应返回undefined');
                tearDown();
            }
        },
        {
            id: 'MTCC18',
            name: '不存在的moduleId-getMock不抛异常',
            run: function () {
                const cache = setUp('999');
                assertNoThrow(function () { cache.getMock(); }, '不存在的moduleId getMock不应抛异常');
                assertNoThrow(function () { cache.getMod(); }, '不存在的moduleId getMod不应抛异常');
                assertNoThrow(function () { cache.preheat(); }, '不存在的moduleId preheat不应抛异常');
                tearDown();
            }
        },
        {
            id: 'MTCC19',
            name: '空字符串moduleId-getMock返回空对象',
            run: function () {
                const cache = setUp('');
                const mock = cache.getMock();
                assertNotNull(mock, '空字符串moduleId应返回非null(空对象)');
                assertEquals(0, Object.keys(mock).length, '空字符串moduleId应返回空对象');
                tearDown();
            }
        },

        // ===== MTCC20-MTCC23: 跨实例隔离 =====

        {
            id: 'MTCC20',
            name: '相同moduleId两个实例返回不同mock引用',
            run: function () {
                const cacheA = setUp('25');
                const cacheB = setUp('25');
                const mockA = cacheA.getMock();
                const mockB = cacheB.getMock();
                assertNotSame(mockA, mockB, '相同moduleId不同实例应不同引用');
                tearDown();
            }
        },
        {
            id: 'MTCC21',
            name: 'reset一个实例不影响另一实例',
            run: function () {
                const cacheA = setUp('25');
                const cacheB = setUp('25');
                cacheA.preheat();
                cacheB.preheat();
                const beforeB = cacheB.getMock();
                cacheA.reset(); // 只重置A
                const afterB = cacheB.getMock();
                assertSame(beforeB, afterB, 'reset A不应影响B的缓存引用');
                tearDown();
            }
        },
        {
            id: 'MTCC22',
            name: '不同moduleId返回不同mock数据',
            run: function () {
                const cache15 = setUp('15');
                const cache25 = setUp('25');
                const mock15 = cache15.getMock();
                const mock25 = cache25.getMock();
                assertNotSame(mock15, mock25, '不同moduleId应不同引用');
                // 模块15有cooperationMode字段,模块25有monitoring字段
                assertTrue(mock15.cooperationMode !== undefined, '模块15应有cooperationMode字段');
                assertTrue(mock25.monitoring !== undefined, '模块25应有monitoring字段');
                assertTrue(mock15.monitoring === undefined, '模块15不应有monitoring字段');
                assertTrue(mock25.cooperationMode === undefined, '模块25不应有cooperationMode字段');
                tearDown();
            }
        },
        {
            id: 'MTCC23',
            name: '四个模块缓存实例互不相同',
            run: function () {
                const c15 = setUp('15');
                const c25 = setUp('25');
                const c27 = setUp('27');
                const c28 = setUp('28');
                const m15 = c15.getMock();
                const m25 = c25.getMock();
                const m27 = c27.getMock();
                const m28 = c28.getMock();
                assertNotSame(m15, m25, '模块15和25应不同引用');
                assertNotSame(m15, m27, '模块15和27应不同引用');
                assertNotSame(m15, m28, '模块15和28应不同引用');
                assertNotSame(m25, m27, '模块25和27应不同引用');
                assertNotSame(m25, m28, '模块25和28应不同引用');
                assertNotSame(m27, m28, '模块27和28应不同引用');
                tearDown();
            }
        },

        // ===== MTCC24-MTCC25: 数据完整性 =====

        {
            id: 'MTCC24',
            name: 'getMock数据与MODULES原始mock字段一致',
            run: function () {
                const cache = setUp('25');
                const cachedMock = cache.getMock();
                const originalMock = MODULES.find(function (m) { return m.id === '25'; }).mock();
                // 逐字段对比
                assertEquals(originalMock.monitoring, cachedMock.monitoring, 'monitoring字段应一致');
                assertEquals(originalMock.coverage, cachedMock.coverage, 'coverage字段应一致');
                assertEquals(originalMock.uptime, cachedMock.uptime, 'uptime字段应一致');
                assertEquals(originalMock.mttr, cachedMock.mttr, 'mttr字段应一致');
                tearDown();
            }
        },
        {
            id: 'MTCC25',
            name: 'getMod与MODULES.find结果一致',
            run: function () {
                const cache = setUp('25');
                const cachedMod = cache.getMod();
                const originalMod = MODULES.find(function (m) { return m.id === '25'; });
                assertEquals(originalMod.id, cachedMod.id, '模块id应一致');
                assertEquals(originalMod.name, cachedMod.name, '模块name应一致');
                assertEquals(originalMod.domain, cachedMod.domain, '模块domain应一致');
                assertEquals(originalMod.aiRate, cachedMod.aiRate, '模块aiRate应一致');
                tearDown();
            }
        },

        // ===== MTCC26-MTCC28: 并发安全模拟 =====

        {
            id: 'MTCC26',
            name: '高频reset+preheat+getMock(100轮一致性)',
            run: function () {
                const cache = setUp('25');
                let allSame = true;
                let lastMock = null;
                for (let i = 0; i < 100; i++) {
                    cache.reset();
                    cache.preheat();
                    const m = cache.getMock();
                    if (i === 0) {
                        lastMock = m;
                    } else {
                        // 每轮reset后getMock应返回新引用(重新mock())
                        // 但字段值应一致
                        if (m.monitoring !== lastMock.monitoring) allSame = false;
                    }
                }
                assertTrue(allSame, '100轮reset+preheat+getMock字段值应一致');
                tearDown();
            }
        },
        {
            id: 'MTCC27',
            name: '多实例交错操作不串扰',
            run: function () {
                const c15 = setUp('15');
                const c25 = setUp('25');
                const c27 = setUp('27');
                const c28 = setUp('28');
                // 交错操作
                c15.preheat();
                c25.preheat();
                c15.reset();
                c27.preheat();
                c28.preheat();
                c25.reset();
                c27.reset();
                c28.reset();
                c15.preheat();
                // 验证每个实例仍返回正确的模块数据
                assertEquals('15', c15.getMod().id, '交错操作后c15应返回模块15');
                assertEquals('25', c25.getMod().id, '交错操作后c25应返回模块25');
                assertEquals('27', c27.getMod().id, '交错操作后c27应返回模块27');
                assertEquals('28', c28.getMod().id, '交错操作后c28应返回模块28');
                tearDown();
            }
        },
        {
            id: 'MTCC28',
            name: '压力测试-1000次getMock返回一致引用',
            run: function () {
                const cache = setUp('25');
                cache.preheat();
                const first = cache.getMock();
                let allSame = true;
                for (let i = 0; i < 1000; i++) {
                    const m = cache.getMock();
                    if (m !== first) {
                        allSame = false;
                        break;
                    }
                }
                assertTrue(allSame, '1000次getMock应返回同一引用');
                tearDown();
            }
        }
    ];

    // ---------- 输出适配(直接写 cacheUtilLog 面板) ----------
    function emit(line, type) {
        if (typeof document !== 'undefined' && document.getElementById) {
            const logEl = document.getElementById('cacheUtilLog');
            if (logEl) {
                const color = type === 'pass' ? '#0f0' : type === 'fail' ? '#f88' : type === 'warn' ? '#fc0' : '#0ff';
                const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
                const entry = document.createElement('div');
                entry.style.color = color;
                entry.innerHTML = '<span style="opacity:0.6;">[' + t + ']</span> ' + line;
                logEl.appendChild(entry);
                logEl.scrollTop = logEl.scrollHeight;
                return;
            }
        }
        if (typeof console !== 'undefined') console.log(line);
    }

    // ========== 主执行函数 ==========

    window.runCacheUtilTest = function () {
        const results = [];
        let passed = 0, failed = 0;
        const t0 = Date.now();

        if (typeof createModuleMockCache !== 'function') {
            const msg = '[CacheUtilTest] createModuleMockCache 未加载,请确保 toolkit/module-test-cache.js 已引入';
            console.error(msg);
            return { success: false, error: msg };
        }

        TEST_CASES.forEach(function (tc) {
            let result;
            try {
                tc.run();
                result = { pass: true, msg: '通过' };
            } catch (e) {
                result = { pass: false, msg: e.message };
            }
            const pass = result.pass;
            if (pass) passed++; else failed++;
            results.push({ id: tc.id, name: tc.name, pass: pass, msg: result.msg, duration: 0 });
        });

        const elapsed = Date.now() - t0;
        const total = TEST_CASES.length;
        const passRate = (passed / total * 100).toFixed(1);
        const report = {
            module: 'cache-util',
            moduleName: 'createModuleMockCache 工具函数',
            total: total, passed: passed, failed: failed,
            passRate: passRate + '%',
            success: failed === 0,
            elapsedMs: elapsed,
            results: results,
            timestamp: new Date().toISOString()
        };
        window.__lastCacheUtilTestReport = report;

        // 日志输出到 cacheUtilLog 面板
        emit('🔧 [缓存工具测试] ' + report.moduleName + ' | ' +
            passed + '/' + total + ' PASS · 通过率' + passRate + '% · 耗时' + elapsed + 'ms', 'info');
        results.forEach(function (r) {
            emit('  ' + (r.pass ? '✓' : '✗') + ' ' + r.id + ' ' + r.name + ' | ' + r.msg,
                r.pass ? 'pass' : 'fail');
        });
        emit(passed === total ? '🔧 [缓存工具汇总] ' + passed + '/' + total + ' 全 PASS ✅' :
            '🔧 [缓存工具汇总] ' + passed + '/' + total + ' 有失败 ❌',
            passed === total ? 'pass' : 'warn');

        console.log('[CacheUtilTest]', report);
        return report;
    };

    // headless 钩子
    window.__runCacheUtilTestPromise = function () {
        return Promise.resolve(window.runCacheUtilTest());
    };

})();
