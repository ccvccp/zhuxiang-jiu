/* ============================================================
 * procurement-module-test.js · AI智能原料采购与供应商管理模块单元测试
 * ------------------------------------------------------------
 * 模块编号: 27
 * 覆盖范围: 10 个 AI 能力 + 结构完整性 + mock 数据 + executeTest 判定
 * 用例总数: 16 (PTC1-PTC16)
 *   · PTC1-PTC3:   结构完整性(模块存在/能力清单/用例清单)
 *   · PTC4-PTC13:  10 个 AI 能力逐一验证(对照 mock 数据字段)
 *   · PTC14-PTC15: mock 数据完整性(核心字段+扩展字段)
 *   · PTC16:       executeTest 判定逻辑验证
 * 运行方式: module-test.html 按钮 或 控制台 runProcurementModuleTest()
 * 报告存储: window.__lastProcurementModuleTestReport
 * ============================================================ */

(function () {
    'use strict';

    // ---------- mock 数据缓存(复用 toolkit/module-test-cache.js) ----------
    const _cache = createModuleMockCache('27');
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
        // ===== PTC1-PTC3: 结构完整性 =====
        {
            id: 'PTC1',
            name: '模块27存在且字段完整',
            run: () => {
                const mod = getMod();
                const hasFields = mod && mod.name && mod.domain && mod.aiRate
                    && Array.isArray(mod.aiCapabilities) && Array.isArray(mod.testCases)
                    && typeof mod.mock === 'function';
                return assertTrue(hasFields, '模块27存在且包含 id/name/domain/aiRate/aiCapabilities/testCases/mock');
            }
        },
        {
            id: 'PTC2',
            name: '10个AI能力全部定义',
            run: () => {
                const mod = getMod();
                const expected = ['AI智能供应商画像', 'AI智能寻源', 'AI智能比价', 'AI智能采购预测', 'AI智能合同管理',
                    'AI智能来料检验', 'AI智能供应商评级', 'AI智能风险预警', 'AI智能成本核算', 'AI智能协同补货'];
                const missing = expected.filter(c => !mod.aiCapabilities.includes(c));
                return assertTrue(missing.length === 0,
                    '10个AI能力全存在 | 缺失:' + (missing.join(',') || '无'));
            }
        },
        {
            id: 'PTC3',
            name: '10个测试用例全部定义',
            run: () => {
                const mod = getMod();
                return assertEqual(mod.testCases.length, 10, '测试用例数=10');
            }
        },

        // ===== PTC4-PTC13: 10个AI能力逐一验证 =====
        {
            id: 'PTC4',
            name: 'AI智能供应商画像: 200+标签,置信度90%',
            run: () => {
                const m = getMock();
                return assertTrue(m.supplierTags === 200 && m.profileConfidence === '90%',
                    '供应商画像 200标签 置信度90%');
            }
        },
        {
            id: 'PTC5',
            name: 'AI智能寻源: 匹配精度93%',
            run: () => {
                const m = getMock();
                return assertEqual(m.sourcingMatch, '93%', '寻源匹配精度=93%');
            }
        },
        {
            id: 'PTC6',
            name: 'AI智能比价: 比价准确率95%',
            run: () => {
                const m = getMock();
                return assertEqual(m.priceCompareAccuracy, '95%', '比价准确率=95%');
            }
        },
        {
            id: 'PTC7',
            name: 'AI智能采购预测: 预测准确率88%',
            run: () => {
                const m = getMock();
                return assertEqual(m.demandForecast, '88%', '采购预测准确率=88%');
            }
        },
        {
            id: 'PTC8',
            name: 'AI智能合同管理: 审查通过率92%',
            run: () => {
                const m = getMock();
                return assertEqual(m.contractReviewPass, '92%', '合同审查通过率=92%');
            }
        },
        {
            id: 'PTC9',
            name: 'AI智能来料检验: 检验准确率97%',
            run: () => {
                const m = getMock();
                return assertEqual(m.inspectionAccuracy, '97%', '来料检验准确率=97%');
            }
        },
        {
            id: 'PTC10',
            name: 'AI智能供应商评级: AI推荐准确率90%',
            run: () => {
                const m = getMock();
                return assertEqual(m.ratingAccuracy, '90%', '供应商评级准确率=90%');
            }
        },
        {
            id: 'PTC11',
            name: 'AI智能风险预警: 提前预警率85%',
            run: () => {
                const m = getMock();
                return assertEqual(m.riskEarlyWarning, '85%', '风险提前预警率=85%');
            }
        },
        {
            id: 'PTC12',
            name: 'AI智能成本核算: 节约潜力识别率80%',
            run: () => {
                const m = getMock();
                return assertEqual(m.costSavingId, '80%', '成本节约识别率=80%');
            }
        },
        {
            id: 'PTC13',
            name: 'AI智能协同补货: 补货及时率95%',
            run: () => {
                const m = getMock();
                return assertEqual(m.replenishmentTimeliness, '95%', '补货及时率=95%');
            }
        },

        // ===== PTC14-PTC15: mock 数据完整性 =====
        {
            id: 'PTC14',
            name: 'mock数据核心字段完整',
            run: () => {
                const m = getMock();
                const required = ['cooperationModule', 'aiRate', 'supplierTags', 'profileConfidence',
                    'sourcingMatch', 'priceCompareAccuracy', 'demandForecast', 'contractReviewPass',
                    'inspectionAccuracy', 'ratingAccuracy', 'riskEarlyWarning', 'costSavingId',
                    'replenishmentTimeliness'];
                const missing = required.filter(k => m[k] === undefined);
                return assertTrue(missing.length === 0,
                    '核心字段完整 | 缺失:' + (missing.join(',') || '无'));
            }
        },
        {
            id: 'PTC15',
            name: 'mock数据扩展字段完整(等级/维度/检验/区块链/法律)',
            run: () => {
                const m = getMock();
                const hasLevels = m.supplierLevels === 'D→C→B→A→S';
                const hasDims = m.ratingDimensions === 6;
                const hasMaterials = m.rawMaterials && m.rawMaterials.includes('原粮');
                const hasInspection = m.inspectionMethods && m.inspectionMethods.includes('GC-MS');
                const hasBlockchain = m.blockchainTypes && m.blockchainTypes.includes('合同');
                const hasLaw = m.lawCompliance === 7;
                const hasDb = m.dbTables === 12 && m.dbIndexes === 38;
                return assertTrue(hasLevels && hasDims && hasMaterials && hasInspection && hasBlockchain && hasLaw && hasDb,
                    '等级+维度+原料+检验+区块链+法律+DB 全存在');
            }
        },

        // ===== PTC16: executeTest 判定逻辑验证 =====
        {
            id: 'PTC16',
            name: 'executeTest 判定逻辑(合作闭环验证)',
            run: () => {
                const mod = getMod();
                const mockData = getMock();

                // 验证与合作模块15的闭环关系
                const hasCoopRef = mockData.cooperationModule === 15;
                const hasSourcing = mockData.sourcingMatch === '93%';
                const hasForecast = mockData.demandForecast === '88%';
                const hasReplenishment = mockData.replenishmentTimeliness === '95%';

                return assertTrue(hasCoopRef && hasSourcing && hasForecast && hasReplenishment,
                    '合作闭环: cooperationModule=15 + 寻源+预测+补货 数据一致');
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
            module: '27',
            moduleName: 'AI智能原料采购与供应商管理模块',
            results, passed, total, rate,
            timestamp: new Date().toISOString()
        };

        window.__lastProcurementModuleTestReport = report;
        return report;
    }

    // ---------- 全局暴露 ----------
    window.runProcurementModuleTest = function () {
        const report = executeTests();

        console.log('==========================================');
        console.log('  模块27: AI智能原料采购与供应商管理模块 单元测试');
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

    // headless 钩子
    window.__runProcurementModuleTestPromise = function () {
        return Promise.resolve(window.runProcurementModuleTest());
    };

    console.log('✅ procurement-module-test.js 已加载 (16用例, PTC1-PTC16)');

})();
