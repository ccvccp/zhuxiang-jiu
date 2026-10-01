/* ============================================================
 * cooperation-module-test.js · AI智能合作定制模块单元测试
 * ------------------------------------------------------------
 * 模块编号: 15 (合并原合作接口管理模块15 + OEM代工定制模块26)
 * 覆盖范围: 10 个 AI 能力 + 结构完整性 + mock 数据 + 供应链闭环验证
 * 用例总数: 16 (CTC1-CTC16)
 *   · CTC1-CTC3:   结构完整性(模块存在/能力清单/用例清单)
 *   · CTC4-CTC13:  10 个 AI 能力逐一验证(对照 mock 数据字段)
 *   · CTC14-CTC15: mock 数据完整性
 *   · CTC16:       供应链闭环验证(合作15→采购27→仓储28)
 * 运行方式: module-test.html 按钮 或 控制台 runCooperationModuleTest()
 * 报告存储: window.__lastCooperationModuleTestReport
 * ============================================================ */

(function () {
    'use strict';

    // ---------- mock 数据缓存(复用 toolkit/module-test-cache.js) ----------
    const _cache = createModuleMockCache('15');
    const getMock = _cache.getMock;
    const getMod = _cache.getMod;

    // ---------- 断言工具 ----------
    function assertEqual(actual, expected, msg) {
        const ok = String(actual) === String(expected);
        return { pass: ok, msg: msg + ' | 实际:' + actual + ' 期望:' + expected };
    }
    function assertTrue(cond, msg) {
        return { pass: !!cond, msg: msg + ' | ' + (cond ? '通过' : '失败') };
    }

    // ---------- 测试用例定义 ----------
    const TEST_CASES = [
        // ===== CTC1-CTC3: 结构完整性 =====
        {
            id: 'CTC1',
            name: '模块15存在且字段完整',
            run: () => {
                const mod = getMod();
                const hasFields = mod && mod.name && mod.domain && mod.aiRate
                    && Array.isArray(mod.aiCapabilities) && Array.isArray(mod.testCases)
                    && typeof mod.mock === 'function';
                return assertTrue(hasFields, '模块15存在且包含 id/name/domain/aiRate/aiCapabilities/testCases/mock');
            }
        },
        {
            id: 'CTC2',
            name: '10个AI能力全部定义',
            run: () => {
                const mod = getMod();
                const expected = ['AI智能资质审核', 'AI智能需求匹配', 'AI智能定制设计', 'AI智能配方勾调',
                    'AI智能瓶型包装', 'AI智能定价报价', 'AI智能保证金管理', 'AI智能生产品控',
                    'AI智能交付售后', 'AI智能客户风控'];
                const missing = expected.filter(c => !mod.aiCapabilities.includes(c));
                return assertTrue(missing.length === 0,
                    '10个AI能力全存在 | 缺失:' + (missing.join(',') || '无'));
            }
        },
        {
            id: 'CTC3',
            name: '10个测试用例全部定义',
            run: () => {
                const mod = getMod();
                return assertEqual(mod.testCases.length, 10, '测试用例数=10');
            }
        },

        // ===== CTC4-CTC13: 10个AI能力逐一验证(对照 mock 数据字段) =====
        {
            id: 'CTC4',
            name: 'AI智能资质审核: 审核准确率96%',
            run: () => {
                return assertEqual(getMock().qualificationAccuracy, '96%', '资质审核准确率=96%');
            }
        },
        {
            id: 'CTC5',
            name: 'AI智能需求匹配: 匹配精度95%',
            run: () => {
                return assertEqual(getMock().demandMatch, '95%', '需求匹配精度=95%');
            }
        },
        {
            id: 'CTC6',
            name: 'AI智能定制设计: 设计满意度90%',
            run: () => {
                return assertEqual(getMock().designSatisfaction, '90%', '设计满意度=90%');
            }
        },
        {
            id: 'CTC7',
            name: 'AI智能配方勾调: 配方满意度90%+勾调误差<0.5%',
            run: () => {
                const m = getMock();
                const ok1 = m.recipeSatisfaction === '90%';
                const ok2 = m.blendingError === '0.5%';
                return assertTrue(ok1 && ok2,
                    '配方满意度=' + m.recipeSatisfaction + ' 勾调误差=' + m.blendingError);
            }
        },
        {
            id: 'CTC8',
            name: 'AI智能瓶型包装: 设计周期-60%',
            run: () => {
                return assertEqual(getMock().designCycleReduction, '60%', '设计周期降低=60%');
            }
        },
        {
            id: 'CTC9',
            name: 'AI智能定价报价: 定价准确率92%',
            run: () => {
                return assertEqual(getMock().pricingAccuracy, '92%', '定价准确率=92%');
            }
        },
        {
            id: 'CTC10',
            name: 'AI智能保证金管理: 预警覆盖率100%',
            run: () => {
                return assertEqual(getMock().depositCoverage, '100%', '保证金预警覆盖率=100%');
            }
        },
        {
            id: 'CTC11',
            name: 'AI智能生产品控: 排程+40%/质控100%/溯源100%',
            run: () => {
                const m = getMock();
                const ok1 = m.schedulingBoost === '40%';
                const ok2 = m.qcCoverage === '100%';
                const ok3 = m.traceability === '100%';
                return assertTrue(ok1 && ok2 && ok3,
                    '排程=' + m.schedulingBoost + ' 质控=' + m.qcCoverage + ' 溯源=' + m.traceability);
            }
        },
        {
            id: 'CTC12',
            name: 'AI智能交付售后: 预测准确率90%/延期率<5%',
            run: () => {
                const m = getMock();
                const ok1 = m.deliveryAccuracy === '90%';
                const ok2 = m.deliveryDelay === '5%';
                return assertTrue(ok1 && ok2,
                    '交付准确率=' + m.deliveryAccuracy + ' 延期率=' + m.deliveryDelay);
            }
        },
        {
            id: 'CTC13',
            name: 'AI智能客户风控: 复购预测85%+200标签',
            run: () => {
                const m = getMock();
                const ok1 = m.repurchasePrediction === '85%';
                const ok2 = m.clientTags === 200;
                return assertTrue(ok1 && ok2,
                    '复购预测=' + m.repurchasePrediction + ' 客户标签=' + m.clientTags);
            }
        },

        // ===== CTC14-CTC15: mock 数据完整性 =====
        {
            id: 'CTC14',
            name: 'mock数据核心字段完整',
            run: () => {
                const m = getMock();
                const required = ['cooperationMode', 'aiRate', 'qualificationAccuracy', 'demandMatch',
                    'designSatisfaction', 'recipeSatisfaction', 'blendingError', 'designCycleReduction',
                    'pricingAccuracy', 'depositCoverage', 'schedulingBoost', 'qcCoverage',
                    'traceability', 'deliveryAccuracy', 'repurchasePrediction'];
                const missing = required.filter(k => m[k] === undefined);
                return assertTrue(missing.length === 0,
                    '核心字段完整 | 缺失:' + (missing.join(',') || '无'));
            }
        },
        {
            id: 'CTC15',
            name: 'mock数据扩展字段完整(技术栈/行业参考/定制项)',
            run: () => {
                const m = getMock();
                const hasTechStack = m.techStack && m.techStack.includes('GC-MS') && m.techStack.includes('OCR');
                const hasRefs = m.industryRefs && m.industryRefs.includes('川酒天眼') && m.industryRefs.includes('茅台定制');
                const hasMinOrder = m.minOrder === '1瓶起定';
                const hasFastDelivery = m.fastDelivery === '3天发货';
                const hasCustomization = Array.isArray(m.customization) && m.customization.length >= 3;
                return assertTrue(hasTechStack && hasRefs && hasMinOrder && hasFastDelivery && hasCustomization,
                    '技术栈+行业参考+起订量+快交付+定制项 全存在');
            }
        },

        // ===== CTC16: 供应链闭环验证(合作15→采购27→仓储28) =====
        {
            id: 'CTC16',
            name: '供应链闭环: 合作15→采购27→仓储28',
            run: () => {
                // 验证合作模块15存在
                const coop = getMod();
                const coopOk = coop && coop.name.includes('合作');
                // 验证采购模块27存在且引用合作模块15
                const proc = MODULES.find(m => m.id === '27');
                const procMock = proc ? proc.mock() : {};
                const procRefOk = procMock.cooperationModule === 15;
                // 验证仓储模块28存在且引用采购模块27
                const wh = MODULES.find(m => m.id === '28');
                const whMock = wh ? wh.mock() : {};
                const whRefOk = whMock.procurementModule === 27;
                // 验证OEM模块26已合并(不存在)
                const oemRemoved = !MODULES.find(m => m.id === '26');
                return assertTrue(coopOk && procRefOk && whRefOk && oemRemoved,
                    '合作15(' + (coopOk ? '✓' : '✗') + ')→采购27(' + (procRefOk ? '✓' : '✗') + ')→仓储28(' + (whRefOk ? '✓' : '✗') + ') OEM26已合并(' + (oemRemoved ? '✓' : '✗') + ')');
            }
        }
    ];

    // ---------- 执行函数 ----------
    function executeTests() {
        // 重置缓存(每次执行重新获取,确保数据新鲜)
        _cache.reset();
        _cache.preheat();

        const results = TEST_CASES.map(tc => {
            try {
                const r = tc.run();
                return { id: tc.id, name: tc.name, pass: r.pass, msg: r.msg };
            } catch (e) {
                return { id: tc.id, name: tc.name, pass: false, msg: '异常: ' + e.message };
            }
        });

        const passed = results.filter(r => r.pass).length;
        const total = results.length;
        const rate = ((passed / total) * 100).toFixed(1);

        const report = {
            module: '15',
            moduleName: 'AI智能合作定制模块',
            results, passed, total, rate,
            timestamp: new Date().toISOString()
        };

        window.__lastCooperationModuleTestReport = report;
        return report;
    }

    // ---------- 全局暴露 ----------
    window.runCooperationModuleTest = function () {
        const report = executeTests();

        console.log('==========================================');
        console.log('  模块15: AI智能合作定制模块 单元测试');
        console.log('==========================================');
        report.results.forEach(r => {
            const icon = r.pass ? '✅' : '❌';
            console.log('  ' + icon + ' ' + r.id + ' | ' + r.name);
            if (!r.pass) console.log('      → ' + r.msg);
        });
        console.log('------------------------------------------');
        console.log('  通过: ' + report.passed + '/' + report.total + ' (' + report.rate + '%)');
        console.log('==========================================');

        return report;
    };

    console.log('✅ cooperation-module-test.js 已加载 (16用例, CTC1-CTC16)');

})();
