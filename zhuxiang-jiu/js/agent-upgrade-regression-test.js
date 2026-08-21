/* ============================================
   竹香酒官网 · 代理商升级服务 - 回归测试脚本
   --------------------------------------------
   用途: 每次修改 main.js 中的 AgentUpgradeClient
         或 agent-upgrade-service.js 后, 运行本脚本
         确保升级/降级/回滚 事务流程未被破坏
   --------------------------------------------
   4 个测试用例:
     TC1  升级1  市级→核心   (验证 11 阶段事务 + 跨模块联动)
     TC2  升级2  核心→战略   (验证 AI 高分直通 + 钱包额度上限)
     TC3  降级   核心→市级   (验证反向事务 + 信用分扣减)
     TC4  回滚   无效等级    (验证快照恢复 + 事务原子性)
   --------------------------------------------
   运行方式:
     · 浏览器: 在 module-test.html 点击「一键回归测试」按钮
     · 控制台: runAgentUpgradeRegression()
     · Headless: window.__runAgentUpgradeRegressionPromise
   ============================================ */

(function () {
    'use strict';

    // ---------- 断言工具 ----------
    function assert(cond, message) {
        if (!cond) throw new Error('断言失败: ' + message);
    }
    function assertEqual(actual, expected, message) {
        if (actual !== expected) {
            throw new Error((message || '断言失败') + ` (期望 ${JSON.stringify(expected)}, 实际 ${JSON.stringify(actual)})`);
        }
    }
    function assertApprox(actual, expected, eps, message) {
        if (Math.abs(actual - expected) > eps) {
            throw new Error((message || '断言失败') + ` (期望约 ${expected}, 实际 ${actual}, 容差 ${eps})`);
        }
    }
    function assertIncludes(arr, item, message) {
        if (!arr.some(x => (typeof x === 'string' && x.includes(item)) || x === item)) {
            throw new Error((message || '断言失败') + ` (数组中未找到 ${item})`);
        }
    }

    // ---------- 测试执行器 ----------
    const STAGES = [
        '阶段1-AI风险评估', '阶段2-开启事务', '阶段3-agents表',
        '阶段4-升级日志', '阶段5-订单表', '阶段6-返利重算',
        '阶段7-信用管理', '阶段8-钱包模块', '阶段9-合规监控',
        '阶段10-事务提交', '阶段11-异步', '完成',
    ];

    async function runOne(name, fn) {
        const start = Date.now();
        try {
            await fn();
            return { name, status: 'PASS', duration: Date.now() - start, error: null };
        } catch (e) {
            return { name, status: 'FAIL', duration: Date.now() - start, error: e.message };
        }
    }

    // ---------- 输出适配(浏览器 / 控制台) ----------
    let _sink = null; // 可注入外部 sink
    function emit(line, type) {
        if (_sink && typeof _sink === 'function') {
            _sink(line, type);
            return;
        }
        // 浏览器 DOM
        if (typeof document !== 'undefined' && document.getElementById) {
            const logEl = document.getElementById('agentLog');
            if (logEl) {
                const color = type === 'pass' ? '#0f0' : type === 'fail' ? '#f88' : type === 'warn' ? '#fc0' : '#0ff';
                const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
                const entry = document.createElement('div');
                entry.style.color = color;
                entry.innerHTML = `<span style="opacity:0.6;">[${t}]</span> ${line}`;
                logEl.appendChild(entry);
                logEl.scrollTop = logEl.scrollHeight;
                return;
            }
        }
        // 退化为 console
        if (typeof console !== 'undefined') console.log(line);
    }

    // ============================================================
    //  测试用例定义
    // ============================================================

    // 每个用例独立运行前先 resetMock,确保互不干扰
    async function setup() {
        if (typeof AgentUpgradeClient === 'undefined') {
            throw new Error('AgentUpgradeClient 未加载,请先引入 js/main.js');
        }
        AgentUpgradeClient.resetMock();
    }

    // TC1: 升级1 市级→核心
    async function TC1_upgradeMarketToCore() {
        await setup();
        const db0 = AgentUpgradeClient.getMockDB();
        const before = db0.agents.find(a => a.id === 1);
        assertEqual(before.agent_level, '市级', '张三初始等级应为市级');
        assertEqual(before.upgrade_count, 0, '张三初始升级次数应为0');
        assertEqual(db0.credit_scores.find(c => c.agent_id === 1).credit_score, 720, '张三初始竹信分应为720');
        assertEqual(db0.wallet_accounts.find(w => w.agent_id === 1).credit_limit, 100000, '张三初始钱包额度应为10万');

        const r = await AgentUpgradeClient.upgrade({
            agentId: 1, fromLevel: '市级', toLevel: '核心',
            upgradeType: '主动升级', operator: 'admin', remark: 'TC1-回归测试',
        });

        assertEqual(r.success, true, 'TC1 升级应成功');
        assert(r.logs && r.logs.length >= 12, `TC1 日志步骤应≥12, 实际 ${r.logs ? r.logs.length : 0}`);

        // 验证 11 阶段全部到位
        const steps = r.logs.map(l => l.step);
        const missing = STAGES.filter(s => !steps.some(g => g.includes(s)));
        assert(missing.length === 0, `TC1 缺失事务阶段: ${missing.join(',')}`);

        // 验证异步任务触发
        assert(r.asyncOps && r.asyncOps.length === 3, `TC1 异步任务应为3个, 实际 ${r.asyncOps ? r.asyncOps.length : 0}`);
        assertIncludes(r.asyncOps, 'blockchain_notarize', 'TC1 区块链存证任务');
        assertIncludes(r.asyncOps, 'ai_monitor_setup', 'TC1 AI监控任务');
        assertIncludes(r.asyncOps, 'agent_notify', 'TC1 通知推送任务');

        // 验证详情
        assertEqual(r.details.fromLevel, '市级', 'TC1 详情 fromLevel');
        assertEqual(r.details.toLevel, '核心', 'TC1 详情 toLevel');
        assert(r.details.aiScore >= 60, `TC1 AI评分应≥60, 实际 ${r.details.aiScore}`);
        assertEqual(r.details.tasteQuota, 50, 'TC1 品鉴酒配额应为50瓶');
        assertEqual(r.details.creditBoost, 80, 'TC1 信用加分应为+80');
        assertEqual(r.details.walletQuota, 300000, 'TC1 钱包额度应为30万');

        // 验证数据库联动(跨模块同步更新)
        const db1 = AgentUpgradeClient.getMockDB();
        const agent1 = db1.agents.find(a => a.id === 1);
        assertEqual(agent1.agent_level, '核心', 'TC1 数据库: 张三等级应已更新为核心');
        assertEqual(agent1.annual_target, 1000000, 'TC1 数据库: 年度任务应为100万');
        assertEqual(agent1.current_rebate_tier, 'T2', 'TC1 数据库: 返利档应为T2');
        assertEqual(agent1.taste_quota_monthly, 50, 'TC1 数据库: 品鉴酒配额应为50');
        assertEqual(agent1.upgrade_count, 1, 'TC1 数据库: 升级次数应为1');

        const cs1 = db1.credit_scores.find(c => c.agent_id === 1);
        assertEqual(cs1.credit_score, 800, 'TC1 数据库: 竹信分应为720+80=800');

        const wallet1 = db1.wallet_accounts.find(w => w.agent_id === 1);
        assertEqual(wallet1.credit_limit, 300000, 'TC1 数据库: 钱包额度应为30万');
        assertEqual(wallet1.tier_level, '核心', 'TC1 数据库: 钱包tier应同步更新');

        const cm1 = db1.compliance_monitors.find(m => m.agent_id === 1);
        assertEqual(cm1.risk_threshold, '高', 'TC1 数据库: 合规阈值应为高');
        assertEqual(cm1.audit_frequency, '半月', 'TC1 数据库: 审计频率应为半月');

        // 验证待付款订单返利率已更新
        const pendingOrders = db1.orders.filter(o => o.agent_id === 1 && o.status === '待付款');
        assert(pendingOrders.length > 0, 'TC1 数据库: 应有待付款订单');
        pendingOrders.forEach(o => {
            assertEqual(o.rebate_rate, 0.20, `TC1 数据库: 订单${o.id}返利率应为0.20`);
        });

        // 验证升级日志已写入
        assert(db1.upgrade_logs.length >= 1, 'TC1 数据库: 应有升级日志记录');
        const lastLog = db1.upgrade_logs[db1.upgrade_logs.length - 1];
        assertEqual(lastLog.from_level, '市级', 'TC1 日志: from_level');
        assertEqual(lastLog.to_level, '核心', 'TC1 日志: to_level');

        // 验证返利重算结果
        // Bug#3 修复后 monthlyTotal 改用 avgMonthly(237500=950000/4月),非硬编码 250000
        // newRebate = avgMonthly × newRate(核心0.20) = 237500 × 0.20 = 47500
        assertEqual(r.details.newRebate, 47500, 'TC1 新返利应为¥47500(月均23.75万×核心20%)');
    }

    // TC2: 升级2 核心→战略
    async function TC2_upgradeCoreToStrategic() {
        await setup();
        const r = await AgentUpgradeClient.upgrade({
            agentId: 2, fromLevel: '核心', toLevel: '战略',
            upgradeType: 'AI建议升级', operator: 'ai_engine', remark: 'TC2-回归测试',
        });

        assertEqual(r.success, true, 'TC2 升级应成功');
        assert(r.logs.length >= 12, `TC2 日志步骤应≥12, 实际 ${r.logs.length}`);

        // 验证 AI 高分直通(李四竹信分850,应≥80直通)
        assert(r.details.aiScore >= 80, `TC2 AI评分应≥80(直通), 实际 ${r.details.aiScore}`);

        // 验证钱包额度上限
        assertEqual(r.details.walletQuota, 1000000, 'TC2 钱包额度应为100万');

        // 验证数据库
        const db = AgentUpgradeClient.getMockDB();
        const agent = db.agents.find(a => a.id === 2);
        assertEqual(agent.agent_level, '战略', 'TC2 数据库: 李四等级应为战略');
        assertEqual(agent.annual_target, 5000000, 'TC2 数据库: 年度任务应为500万');
        assertEqual(agent.current_rebate_tier, 'T3', 'TC2 数据库: 返利档应为T3');
        assertEqual(agent.taste_quota_monthly, 50, 'TC2 数据库: 品鉴酒配额应为50(达上限)');

        // 验证信用分加成(+120)
        const cs = db.credit_scores.find(c => c.agent_id === 2);
        assertEqual(cs.credit_score, 970, 'TC2 数据库: 竹信分应为850+120=970');

        // 验证钱包
        const wallet = db.wallet_accounts.find(w => w.agent_id === 2);
        assertEqual(wallet.credit_limit, 1000000, 'TC2 数据库: 钱包额度应为100万');
        assertEqual(wallet.tier_level, '战略', 'TC2 数据库: 钱包tier应同步更新');

        // 验证合规监控(战略级)
        const cm = db.compliance_monitors.find(m => m.agent_id === 2);
        assertEqual(cm.risk_threshold, '极高', 'TC2 数据库: 合规阈值应为极高');
        assertEqual(cm.audit_frequency, '周', 'TC2 数据库: 审计频率应为周');
    }

    // TC3: 降级 核心→市级
    async function TC3_downgradeCoreToMarket() {
        await setup();
        const r = await AgentUpgradeClient.downgrade(2, '核心', '连续3月未达25万门槛');

        assertEqual(r.success, true, 'TC3 降级应成功');
        assertEqual(r.toLevel, '市级', 'TC3 应降级至市级');

        // 验证降级日志包含 WARN
        const warnLogs = r.logs.filter(l => l.level === 'WARN');
        assert(warnLogs.length > 0, 'TC3 应有WARN级别降级日志');

        // 验证数据库
        const db = AgentUpgradeClient.getMockDB();
        const agent = db.agents.find(a => a.id === 2);
        assertEqual(agent.agent_level, '市级', 'TC3 数据库: 李四等级应为市级');
        assertEqual(agent.annual_target, 500000, 'TC3 数据库: 年度任务应降至50万');
        assertEqual(agent.current_rebate_tier, 'T1', 'TC3 数据库: 返利档应为T1');
        assertEqual(agent.taste_quota_monthly, 27, 'TC3 数据库: 品鉴酒配额应为27');
        assertEqual(agent.ai_risk_level, '高', 'TC3 数据库: AI风险级别应为高(降级后)');

        // 验证信用分扣减 100
        const cs = db.credit_scores.find(c => c.agent_id === 2);
        assertEqual(cs.credit_score, 750, 'TC3 数据库: 竹信分应为850-100=750');

        // 验证钱包额度降低
        const wallet = db.wallet_accounts.find(w => w.agent_id === 2);
        assertEqual(wallet.credit_limit, 100000, 'TC3 数据库: 钱包额度应降至10万');
        assertEqual(wallet.tier_level, '市级', 'TC3 数据库: 钱包tier应同步降级');

        // 验证降级日志记录
        const lastLog = db.upgrade_logs[db.upgrade_logs.length - 1];
        assertEqual(lastLog.from_level, '核心', 'TC3 日志: from_level');
        assertEqual(lastLog.to_level, '市级', 'TC3 日志: to_level');
        assertEqual(lastLog.upgrade_type, '系统降级', 'TC3 日志: upgrade_type');
        assertEqual(lastLog.operator, 'SYSTEM', 'TC3 日志: operator应为SYSTEM');
    }

    // TC4: 回滚(无效等级触发)
    async function TC4_rollbackOnInvalidLevel() {
        await setup();
        const db0 = AgentUpgradeClient.getMockDB();
        const beforeAgent = db0.agents.find(a => a.id === 1);
        const beforeLevel = beforeAgent.agent_level;
        const beforeScore = db0.credit_scores.find(c => c.agent_id === 1).credit_score;
        const beforeWallet = db0.wallet_accounts.find(w => w.agent_id === 1).credit_limit;

        const r = await AgentUpgradeClient.upgrade({
            agentId: 1, fromLevel: '市级', toLevel: '不存在的等级',
            upgradeType: '回滚测试', operator: 'test', remark: 'TC4-触发回滚',
        });

        // 验证升级失败
        assertEqual(r.success, false, 'TC4 升级应失败');
        assert(r.error && r.error.includes('无效目标等级'), `TC4 错误信息应包含"无效目标等级", 实际: ${r.error}`);

        // 验证回滚日志
        const rollbackLogs = r.logs.filter(l => l.level === 'ERROR' && l.step === '回滚');
        assert(rollbackLogs.length > 0, 'TC4 应有 ERROR 级别回滚日志');

        // 验证事务前两步已执行(阶段1 AI评估 + 阶段2 开启事务)
        const steps = r.logs.map(l => l.step);
        assertIncludes(steps, '阶段1-AI风险评估', 'TC4 应执行阶段1');
        assertIncludes(steps, '阶段2-开启事务', 'TC4 应执行阶段2(快照建立)');

        // 验证后续阶段未执行(因阶段3抛错)
        const afterStage2 = steps.filter(s => s.includes('阶段3') || s.includes('阶段4') || s.includes('阶段5'));
        assert(afterStage2.length === 0, `TC4 阶段3+不应执行, 实际执行了 ${afterStage2.length} 步`);

        // ★ 关键: 验证事务原子性(快照恢复)
        const db1 = AgentUpgradeClient.getMockDB();
        const afterAgent = db1.agents.find(a => a.id === 1);
        assertEqual(afterAgent.agent_level, beforeLevel, 'TC4 数据库: 等级应恢复为市级(原子性)');
        assertEqual(afterAgent.current_rebate_tier, beforeAgent.current_rebate_tier, 'TC4 数据库: 返利档应恢复');

        const afterScore = db1.credit_scores.find(c => c.agent_id === 1).credit_score;
        assertEqual(afterScore, beforeScore, 'TC4 数据库: 竹信分应恢复');

        const afterWallet = db1.wallet_accounts.find(w => w.agent_id === 1).credit_limit;
        assertEqual(afterWallet, beforeWallet, 'TC4 数据库: 钱包额度应恢复');

        // 验证无升级日志写入(事务回滚,日志不应持久化)
        const newLogs = db1.upgrade_logs.filter(l => l.remark === 'TC4-触发回滚');
        assertEqual(newLogs.length, 0, 'TC4 数据库: 回滚事务的升级日志不应持久化');

        // 验证 tx_log 有 ROLLBACK 记录
        assertIncludes(db1.tx_log.map(t => t.type), 'ROLLBACK', 'TC4 数据库: tx_log 应有 ROLLBACK 记录');
    }

    // ============================================================
    //  主入口
    // ============================================================

    async function runAgentUpgradeRegression(opts) {
        const options = opts || {};
        _sink = options.sink || null; // 注入外部 sink(line, type) => void

        const out = [];
        const sep = '═'.repeat(70);
        out.push(sep);
        out.push('  竹香酒官网 · 代理商升级服务 - 回归测试');
        out.push('  日期: ' + new Date().toISOString().slice(0, 19).replace('T', ' '));
        out.push('  目标: main.js → AgentUpgradeClient');
        out.push(sep);
        out.forEach(l => emit(l, 'info'));
        if (_sink) out.length = 0; else out.push('');

        const cases = [
            { name: 'TC1 升级1: 市级→核心 (11阶段事务+跨模块联动)', fn: TC1_upgradeMarketToCore },
            { name: 'TC2 升级2: 核心→战略 (AI高分直通+钱包上限)',   fn: TC2_upgradeCoreToStrategic },
            { name: 'TC3 降级:  核心→市级 (反向事务+信用分扣减)',    fn: TC3_downgradeCoreToMarket },
            { name: 'TC4 回滚:  无效等级   (快照恢复+事务原子性)',  fn: TC4_rollbackOnInvalidLevel },
        ];

        const results = [];
        let passed = 0, failed = 0;
        for (const c of cases) {
            emit('──────────────────────────────────────────────────────────', 'info');
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
        const summary = `  回归测试${allPassed ? '全部通过' : '存在失败'}: ${passed}/${cases.length} PASS, ${failed} FAIL`;
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

        // 数据库最终状态
        emit('', 'info');
        emit('Mock 数据库最终状态:', 'info');
        const db = AgentUpgradeClient.getMockDB();
        db.agents.forEach(a => {
            const cs = db.credit_scores.find(c => c.agent_id === a.id);
            const wallet = db.wallet_accounts.find(w => w.agent_id === a.id);
            emit(`  [${a.id}] ${a.name} | ${a.agent_level} | 竹信分${cs ? cs.credit_score : 'N/A'} | 钱包¥${(wallet ? wallet.credit_limit : 0).toLocaleString()}`, 'info');
        });
        emit(`  升级日志记录数: ${db.upgrade_logs.length}`, 'info');
        emit(`  事务日志: ${db.tx_log.length}条 (含 BEGIN/COMMIT/ROLLBACK)`, 'info');

        const report = {
            timestamp: new Date().toISOString(),
            total: cases.length,
            passed,
            failed,
            passRate: ((passed / cases.length) * 100).toFixed(1) + '%',
            results,
            success: allPassed,
        };

        // 兼容 Node.js 风格返回(Promise + 全局标记)
        if (typeof window !== 'undefined') {
            window.__lastRegressionReport = report;
        }
        return report;
    }

    // ---------- 暴露 ----------
    if (typeof window !== 'undefined') {
        window.runAgentUpgradeRegression = runAgentUpgradeRegression;
        window.__runAgentUpgradeRegressionPromise = runAgentUpgradeRegression; // headless 调用别名
    }
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { runAgentUpgradeRegression };
    }
})();
