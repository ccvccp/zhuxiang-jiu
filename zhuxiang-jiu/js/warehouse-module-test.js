/* ============================================================
 * warehouse-module-test.js · AI智能仓储与库存优化模块单元测试
 * ------------------------------------------------------------
 * 模块编号: 28
 * 覆盖范围: 10 个 AI 能力 + 结构完整性 + mock 数据 + 闭环验证
 * 用例总数: 16 (WTC1-WTC16)
 *   · WTC1-WTC3:   结构完整性(模块存在/能力清单/用例清单)
 *   · WTC4-WTC13:  10 个 AI 能力逐一验证(对照 mock 数据字段)
 *   · WTC14-PTC15: mock 数据完整性(核心字段+扩展字段)
 *   · WTC16:       供应链闭环验证(采购→仓储→物流)
 * 运行方式: module-test.html 按钮 或 控制台 runWarehouseModuleTest()
 * 报告存储: window.__lastWarehouseModuleTestReport
 * ============================================================ */

(function () {
    'use strict';

    // ---------- mock 数据缓存(复用 toolkit/module-test-cache.js) ----------
    const _cache = createModuleMockCache('28');
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
        // ===== WTC1-WTC3: 结构完整性 =====
        {
            id: 'WTC1',
            name: '模块28存在且字段完整',
            run: () => {
                const mod = getMod();
                const hasFields = mod && mod.name && mod.domain && mod.aiRate
                    && Array.isArray(mod.aiCapabilities) && Array.isArray(mod.testCases)
                    && typeof mod.mock === 'function';
                return assertTrue(hasFields, '模块28存在且包含 id/name/domain/aiRate/aiCapabilities/testCases/mock');
            }
        },
        {
            id: 'WTC2',
            name: '10个AI能力全部定义',
            run: () => {
                const mod = getMod();
                const expected = ['AI智能入库', 'AI智能出库', 'AI智能盘点', 'AI智能库位优化', 'AI智能库存预测',
                    'AI智能安全库存', 'AI智能温湿度监控', 'AI智能多仓协同', 'AI智能损耗管理', 'AI智能仓配一体'];
                const missing = expected.filter(c => !mod.aiCapabilities.includes(c));
                return assertTrue(missing.length === 0,
                    '10个AI能力全存在 | 缺失:' + (missing.join(',') || '无'));
            }
        },
        {
            id: 'WTC3',
            name: '10个测试用例全部定义',
            run: () => {
                const mod = getMod();
                return assertEqual(mod.testCases.length, 10, '测试用例数=10');
            }
        },

        // ===== WTC4-WTC13: 10个AI能力逐一验证 =====
        {
            id: 'WTC4',
            name: 'AI智能入库: 验货准确率96%',
            run: () => {
                const m = getMock();
                return assertEqual(m.inboundAccuracy, '96%', '入库验货准确率=96%');
            }
        },
        {
            id: 'WTC5',
            name: 'AI智能出库: 拣选效率提升50%',
            run: () => {
                const m = getMock();
                return assertEqual(m.pickingEfficiency, '50%', '拣选效率提升=50%');
            }
        },
        {
            id: 'WTC6',
            name: 'AI智能盘点: 盘点准确率98%',
            run: () => {
                const m = getMock();
                return assertEqual(m.stocktakeAccuracy, '98%', '盘点准确率=98%');
            }
        },
        {
            id: 'WTC7',
            name: 'AI智能库位优化: 库位利用率提升30%',
            run: () => {
                const m = getMock();
                return assertEqual(m.slotOptimization, '30%', '库位利用率提升=30%');
            }
        },
        {
            id: 'WTC8',
            name: 'AI智能库存预测: 预测准确率89%',
            run: () => {
                const m = getMock();
                return assertEqual(m.forecastAccuracy, '89%', '库存预测准确率=89%');
            }
        },
        {
            id: 'WTC9',
            name: 'AI智能安全库存: 库存周转提升25%',
            run: () => {
                const m = getMock();
                return assertEqual(m.turnoverImprovement, '25%', '库存周转提升=25%');
            }
        },
        {
            id: 'WTC10',
            name: 'AI智能温湿度监控: 异常发现率95%',
            run: () => {
                const m = getMock();
                return assertEqual(m.envAnomalyDetection, '95%', '温湿度异常发现率=95%');
            }
        },
        {
            id: 'WTC11',
            name: 'AI智能多仓协同: 调拨及时率92%',
            run: () => {
                const m = getMock();
                return assertEqual(m.transferTimeliness, '92%', '多仓调拨及时率=92%');
            }
        },
        {
            id: 'WTC12',
            name: 'AI智能损耗管理: 损耗降低20%',
            run: () => {
                const m = getMock();
                return assertEqual(m.lossReduction, '20%', '损耗降低=20%');
            }
        },
        {
            id: 'WTC13',
            name: 'AI智能仓配一体: 越库率40%',
            run: () => {
                const m = getMock();
                return assertEqual(m.crossDockRate, '40%', '越库率=40%');
            }
        },

        // ===== WTC14-WTC15: mock 数据完整性 =====
        {
            id: 'WTC14',
            name: 'mock数据核心字段完整',
            run: () => {
                const m = getMock();
                const required = ['procurementModule', 'logisticsModule', 'aiRate',
                    'inboundAccuracy', 'pickingEfficiency', 'stocktakeAccuracy', 'slotOptimization',
                    'forecastAccuracy', 'turnoverImprovement', 'envAnomalyDetection',
                    'transferTimeliness', 'lossReduction', 'crossDockRate'];
                const missing = required.filter(k => m[k] === undefined);
                return assertTrue(missing.length === 0,
                    '核心字段完整 | 缺失:' + (missing.join(',') || '无'));
            }
        },
        {
            id: 'WTC15',
            name: 'mock数据扩展字段完整(仓库类型/库位/库存/ABC/盘点/损耗/调拨/合规)',
            run: () => {
                const m = getMock();
                const hasWhTypes = m.warehouseTypes && m.warehouseTypes.includes('工厂仓');
                const hasLocCode = m.locationCode && m.locationCode.includes('区-排');
                const hasStockTypes = m.stockTypes && m.stockTypes.includes('原料');
                const hasAbc = m.abcClass && m.abcClass.includes('A高频');
                const hasAiZone = m.aiZone && m.aiZone.includes('hot');
                const hasStocktake = m.stocktakeMethods && m.stocktakeMethods.includes('无人机');
                const hasLoss = m.lossTypes && m.lossTypes.includes('蒸发');
                const hasTransfer = m.transferTypes && m.transferTypes.includes('补货');
                const hasLaw = m.lawCompliance === 8;
                const hasDb = m.dbTables === 12 && m.dbIndexes === 42;
                return assertTrue(hasWhTypes && hasLocCode && hasStockTypes && hasAbc && hasAiZone
                    && hasStocktake && hasLoss && hasTransfer && hasLaw && hasDb,
                    '仓库+库位+库存+ABC+AI区+盘点+损耗+调拨+合规+DB 全存在');
            }
        },

        // ===== WTC16: 供应链闭环验证 =====
        {
            id: 'WTC16',
            name: '供应链闭环验证(采购27→仓储28→物流06)',
            run: () => {
                const mod = getMod();
                const mockData = getMock();

                // 验证与模块27(采购)的闭环
                const hasProcurementRef = mockData.procurementModule === 27;
                const hasForecast = mockData.forecastAccuracy === '89%';
                const hasTurnover = mockData.turnoverImprovement === '25%';

                // 验证与模块06(物流)的闭环
                const hasLogisticsRef = mockData.logisticsModule === 6;
                const hasCrossDock = mockData.crossDockRate === '40%';
                const hasTransfer = mockData.transferTimeliness === '92%';

                return assertTrue(hasProcurementRef && hasForecast && hasTurnover
                    && hasLogisticsRef && hasCrossDock && hasTransfer,
                    '闭环: 采购27→仓储28(预测+周转) + 仓储28→物流06(越库+调拨)');
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
            module: '28',
            moduleName: 'AI智能仓储与库存优化模块',
            results, passed, total, rate,
            timestamp: new Date().toISOString()
        };

        window.__lastWarehouseModuleTestReport = report;
        return report;
    }

    // ---------- 全局暴露 ----------
    window.runWarehouseModuleTest = function () {
        const report = executeTests();

        console.log('==========================================');
        console.log('  模块28: AI智能仓储与库存优化模块 单元测试');
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
    window.__runWarehouseModuleTestPromise = function () {
        return Promise.resolve(window.runWarehouseModuleTest());
    };

    console.log('✅ warehouse-module-test.js 已加载 (16用例, WTC1-WTC16)');

})();
