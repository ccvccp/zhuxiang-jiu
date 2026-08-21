/* ============================================================
 * decision-module-test.js · AI决策筹划模块(模块29)单元测试
 * ------------------------------------------------------------
 * 模块编号: 29 (AI大脑中枢)
 * 覆盖范围: 10 个 AI 能力 + 结构完整性 + mock 数据 + 编排逻辑判定
 * 用例总数: 16 (DTC1-DTC16)
 *   · DTC1-DTC3:   结构完整性(模块存在/能力清单/用例清单)
 *   · DTC4-DTC13:  10 个 AI 能力逐一验证(对照 mock 数据字段)
 *   · DTC14-DTC15: mock 数据完整性
 *   · DTC16:       编排调度判定逻辑验证
 * 运行方式: module-test.html 按钮 或 控制台 runDecisionModuleTest()
 * 报告存储: window.__lastDecisionModuleTestReport
 * ============================================================ */

(function () {
    'use strict';

    // ---------- mock 数据缓存(复用 toolkit/module-test-cache.js) ----------
    var _cache = createModuleMockCache('29');
    var getMock = _cache.getMock;
    var getMod = _cache.getMod;

    // ---------- 断言工具 ----------
    function assertEqual(actual, expected, msg) {
        var ok = String(actual) === String(expected);
        return { pass: ok, msg: msg + ' | 实际:' + actual + ' 期望:' + expected };
    }
    function assertTrue(cond, msg) {
        return { pass: !!cond, msg: msg + ' | ' + (cond ? '通过' : '失败') };
    }
    function assertNotNull(val, msg) {
        return { pass: val !== null && val !== undefined, msg: msg + ' | ' + (val !== null && val !== undefined ? '非null' : '为null') };
    }
    function assertIncludes(str, sub, msg) {
        var ok = typeof str === 'string' && str.indexOf(sub) !== -1;
        return { pass: ok, msg: msg + ' | ' + (ok ? '包含"' + sub + '"' : '不包含"' + sub + '"') };
    }

    // ---------- 测试用例定义 ----------
    var TEST_CASES = [
        // ===== DTC1-DTC3: 结构完整性 =====
        {
            id: 'DTC1',
            name: '模块29存在且字段完整',
            run: function () {
                var mod = getMod();
                var hasFields = mod && mod.name && mod.domain && mod.aiRate
                    && Array.isArray(mod.aiCapabilities) && Array.isArray(mod.testCases)
                    && typeof mod.mock === 'function' && mod.desc;
                return assertTrue(hasFields, '模块29应包含name/domain/aiRate/aiCapabilities/testCases/mock/desc');
            }
        },
        {
            id: 'DTC2',
            name: '模块29能力清单为10项',
            run: function () {
                var mod = getMod();
                return assertEqual(mod.aiCapabilities.length, 10, 'AI能力应为10项');
            }
        },
        {
            id: 'DTC3',
            name: '模块29测试用例为10项',
            run: function () {
                var mod = getMod();
                return assertEqual(mod.testCases.length, 10, '测试用例应为10项');
            }
        },

        // ===== DTC4-DTC13: 10个AI能力逐一验证 =====
        {
            id: 'DTC4',
            name: 'AI智能角色决策助理-5类角色服务',
            run: function () {
                var m = getMock();
                var roleOk = assertEqual(m.roleService, 5, '角色服务应为5类').pass;
                var rolesOk = assertIncludes(m.rolesServed, '会员', '应服务会员角色').pass
                    && assertIncludes(m.rolesServed, '代理商', '应服务代理商角色').pass
                    && assertIncludes(m.rolesServed, '访客', '应服务访客角色').pass
                    && assertIncludes(m.rolesServed, '网店主', '应服务网店主角色').pass
                    && assertIncludes(m.rolesServed, '管理员', '应服务管理员角色').pass;
                var accOk = assertEqual(m.decisionAccuracy, '92%', '决策准确率应为92%').pass;
                return { pass: roleOk && rolesOk && accOk, msg: '角色决策助理 | 5类角色覆盖+92%准确率' };
            }
        },
        {
            id: 'DTC5',
            name: 'AI智能策略筹划-目标分解+What-if',
            run: function () {
                var m = getMock();
                var effOk = assertEqual(m.planningEfficiency, '60%', '筹划效率应提升60%').pass;
                var copilotOk = assertIncludes(m.roleCopilot, '助理', '应提供决策助理').pass;
                var principleOk = assertIncludes(m.corePrinciple, 'Copilot', '应遵循先Copilot后Agent原则').pass;
                return { pass: effOk && copilotOk && principleOk, msg: '策略筹划 | 效率60%+Copilot原则' };
            }
        },
        {
            id: 'DTC6',
            name: 'AI智能预测推演-蒙特卡洛模拟',
            run: function () {
                var m = getMock();
                return assertEqual(m.forecastAccuracy, '90%', '预测准确率应为90%');
            }
        },
        {
            id: 'DTC7',
            name: 'AI智能编排调度-28模块跨域编排',
            run: function () {
                var m = getMock();
                var modCntOk = assertEqual(m.moduleService, 28, '应编排28个模块').pass;
                var successOk = assertEqual(m.orchestrationSuccess, '95%', '编排成功率应为95%').pass;
                var modulesOk = assertIncludes(m.modulesServed, '01', '应覆盖模块01').pass
                    && assertIncludes(m.modulesServed, '28', '应覆盖模块28').pass;
                return { pass: modCntOk && successOk && modulesOk, msg: '编排调度 | 28模块+95%成功率' };
            }
        },
        {
            id: 'DTC8',
            name: 'AI智能能力路由-插件池动态组合',
            run: function () {
                var m = getMock();
                var reuseOk = assertEqual(m.capabilityReuse, '78%', '能力复用率应为78%').pass;
                var poolOk = assertIncludes(m.pluginPool, '自然语言', '插件池应含自然语言类').pass
                    && assertIncludes(m.pluginPool, '决策推理', '插件池应含决策推理类').pass;
                var cntOk = assertTrue(m.pluginCount >= 100, '插件数量应≥100(实际:' + m.pluginCount + ')').pass;
                return { pass: reuseOk && poolOk && cntOk, msg: '能力路由 | 78%复用+120插件+动态组合' };
            }
        },
        {
            id: 'DTC9',
            name: 'AI智能知识中枢-RAG+组织记忆',
            run: function () {
                var m = getMock();
                var recallOk = assertEqual(m.knowledgeRecall, '93%', '知识召回率应为93%').pass;
                var techOk = assertIncludes(m.techStack, 'RAG', '技术栈应含RAG').pass
                    && assertIncludes(m.techStack, '知识图谱', '技术栈应含知识图谱').pass;
                return { pass: recallOk && techOk, msg: '知识中枢 | 93%召回+RAG+知识图谱' };
            }
        },
        {
            id: 'DTC10',
            name: 'AI智能治理决策-模型提动作规则定执行',
            run: function () {
                var m = getMock();
                var govOk = assertEqual(m.governanceCompliance, '100%', '治理合规率应为100%').pass;
                var principleOk = assertIncludes(m.corePrinciple, '模型提动作', '应遵循模型提动作原则').pass
                    && assertIncludes(m.corePrinciple, '规则定执行', '应遵循规则定执行原则').pass;
                var bcOk = assertIncludes(m.blockchainTypes, '决策存证', '应含决策存证').pass
                    && assertIncludes(m.blockchainTypes, '治理审计', '应含治理审计').pass;
                return { pass: govOk && principleOk && bcOk, msg: '治理决策 | 100%合规+模型规则分离+区块链存证' };
            }
        },
        {
            id: 'DTC11',
            name: 'AI智能反馈闭环-24h延迟',
            run: function () {
                var m = getMock();
                var latencyOk = assertEqual(m.feedbackLatency, '<24h', '闭环延迟应<24h').pass;
                var bcOk = assertIncludes(m.blockchainTypes, '反馈追踪', '应含反馈追踪存证').pass;
                return { pass: latencyOk && bcOk, msg: '反馈闭环 | <24h延迟+反馈追踪' };
            }
        },
        {
            id: 'DTC12',
            name: 'AI智能风控决策-96%覆盖率',
            run: function () {
                var m = getMock();
                var riskOk = assertEqual(m.riskCoverage, '96%', '风控覆盖率应为96%').pass;
                return { pass: riskOk, msg: '风控决策 | 96%覆盖率' };
            }
        },
        {
            id: 'DTC13',
            name: 'AI智能复盘优化-85%覆盖率',
            run: function () {
                var m = getMock();
                var retroOk = assertEqual(m.retrospectiveCoverage, '85%', '复盘覆盖率应为85%').pass;
                return { pass: retroOk, msg: '复盘优化 | 85%覆盖率' };
            }
        },

        // ===== DTC14-DTC15: mock 数据完整性 =====
        {
            id: 'DTC14',
            name: 'mock数据含架构标识与6层结构',
            run: function () {
                var m = getMock();
                var roleOk = assertEqual(m.moduleRole, 'AI大脑中枢', '模块角色应为AI大脑中枢').pass;
                var aiRateOk = assertEqual(m.aiRate, '95%', 'aiRate应为95%').pass;
                var archOk = assertEqual(m.architecture, '6层', '架构应为6层').pass;
                var layersOk = assertIncludes(m.layers, '感知层', '应含感知层').pass
                    && assertIncludes(m.layers, '知识层', '应含知识层').pass
                    && assertIncludes(m.layers, '决策层', '应含决策层').pass
                    && assertIncludes(m.layers, '编排层', '应含编排层').pass
                    && assertIncludes(m.layers, '执行层', '应含执行层').pass
                    && assertIncludes(m.layers, '反馈层', '应含反馈层').pass;
                return { pass: roleOk && aiRateOk && archOk && layersOk, msg: 'mock架构 | AI大脑中枢+6层完整' };
            }
        },
        {
            id: 'DTC15',
            name: 'mock数据含数据库与合规指标',
            run: function () {
                var m = getMock();
                var dbOk = assertTrue(m.dbTables >= 10, '数据库表应≥10(实际:' + m.dbTables + ')').pass;
                var idxOk = assertTrue(m.dbIndexes >= 40, '数据库索引应≥40(实际:' + m.dbIndexes + ')').pass;
                var lawOk = assertTrue(m.lawCompliance >= 10, '对标法律应≥10部(实际:' + m.lawCompliance + ')').pass;
                return { pass: dbOk && idxOk && lawOk, msg: 'mock完整性 | ' + m.dbTables + '表+' + m.dbIndexes + '索引+' + m.lawCompliance + '部法律' };
            }
        },

        // ===== DTC16: 编排调度判定逻辑验证 =====
        {
            id: 'DTC16',
            name: '编排调度判定逻辑-双维服务完整',
            run: function () {
                var m = getMock();
                // 双维服务判定: 角色维度 + 模块维度 均完整
                var roleDimOk = m.roleService === 5 && m.decisionAccuracy === '92%';
                var modDimOk = m.moduleService === 28 && m.orchestrationSuccess === '95%';
                // 核心原则判定: 模型提动作+规则定执行
                var principleOk = m.corePrinciple.indexOf('模型提动作') !== -1
                    && m.corePrinciple.indexOf('规则定执行') !== -1;
                // 技术栈完整: LLM+RAG+Agent+规则引擎
                var techOk = m.techStack.indexOf('LLM') !== -1
                    && m.techStack.indexOf('RAG') !== -1
                    && m.techStack.indexOf('Agent') !== -1
                    && m.techStack.indexOf('规则引擎') !== -1;
                var allOk = roleDimOk && modDimOk && principleOk && techOk;
                return { pass: allOk, msg: '编排判定 | 角色维度(' + (roleDimOk ? 'PASS' : 'FAIL') + ') + 模块维度(' + (modDimOk ? 'PASS' : 'FAIL') + ') + 原则(' + (principleOk ? 'PASS' : 'FAIL') + ') + 技术栈(' + (techOk ? 'PASS' : 'FAIL') + ')' };
            }
        }
    ];

    // ---------- 输出适配 ----------
    var _sink = null;
    function emit(line, type) {
        if (_sink && typeof _sink === 'function') { _sink(line, type); return; }
        if (typeof document !== 'undefined' && document.getElementById) {
            var logEl = document.getElementById('decisionModuleLog');
            if (logEl) {
                var color = type === 'pass' ? '#0f0' : type === 'fail' ? '#f88' : type === 'warn' ? '#fc0' : '#0ff';
                var t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
                var entry = document.createElement('div');
                entry.style.color = color;
                entry.innerHTML = '<span style="opacity:0.6;">[' + t + ']</span> ' + line;
                logEl.appendChild(entry);
                logEl.scrollTop = logEl.scrollHeight;
                return;
            }
        }
        if (typeof console !== 'undefined') console.log(line);
    }

    // ---------- 主执行函数 ----------
    function executeTests() {
        _cache.reset();
        _cache.preheat();

        var results = [];
        var passed = 0, failed = 0;
        var t0 = Date.now();

        TEST_CASES.forEach(function (tc) {
            var r;
            try {
                r = tc.run();
            } catch (e) {
                r = { pass: false, msg: e.message };
            }
            results.push({ id: tc.id, name: tc.name, pass: r.pass, msg: r.msg });
            if (r.pass) passed++; else failed++;
        });

        var elapsed = Date.now() - t0;
        var passRate = (passed / TEST_CASES.length * 100).toFixed(1);
        var report = {
            module: 'AI决策筹划模块(29)',
            total: TEST_CASES.length, passed: passed, failed: failed,
            passRate: passRate + '%',
            elapsed: elapsed + 'ms',
            results: results
        };
        window.__lastDecisionModuleTestReport = report;

        emit('════════════════════════════════════════', 'info');
        emit('  AI决策筹划模块(29) 单元测试报告', 'info');
        emit('  用例: ' + TEST_CASES.length + ' | 通过: ' + passed + ' | 失败: ' + failed + ' | 通过率: ' + passRate + '%', passed === TEST_CASES.length ? 'pass' : 'fail');
        emit('  耗时: ' + elapsed + 'ms', 'info');
        emit('────────────────────────────────────────', 'info');
        results.forEach(function (r) {
            emit('  [' + r.id + '] ' + r.name + ' → ' + (r.pass ? '✓ PASS' : '✗ FAIL') + ' | ' + r.msg, r.pass ? 'pass' : 'fail');
        });
        emit('════════════════════════════════════════', 'info');

        return report;
    }

    // ---------- 对外暴露 ----------
    window.runDecisionModuleTest = function (sink) {
        _sink = sink || null;
        return executeTests();
    };
    window.__runDecisionModuleTestPromise = function () {
        return new Promise(function (resolve) { resolve(executeTests()); });
    };

    emit('AI决策筹划模块(29)测试已加载 | 用例: ' + TEST_CASES.length + ' | 运行: runDecisionModuleTest()', 'info');
})();
