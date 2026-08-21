/**
 * service-convention-checker.js  ·  事务服务约定检查器
 * ============================================================
 * 用途: 把《事务工具包-预检与失败阶段策略.md》第 7 节维护清单
 *       固化为可执行脚本,每次新增/修改事务服务时自动运行,提前发现违规。
 *
 * 两类检查:
 *   A) 静态源码检查(fetch 源文件 + brace-match 抽取 + 关键字/模式匹配)
 *      C1  preflight 不得含业务判断关键字(库存不足/stock</余额不足/...)
 *      C2  stages 区域不得用 return { abort(阶段应 throw)
 *      C3  共享核心(applyDeduct 等)函数体不得出现 TransactionTemplate(禁止嵌套事务)
 *      C4  服务文件须引用 TransactionTemplate(确实基于工具包)
 *      C5  回归测试文件须断言 failedStage
 *      C6  回归测试文件须断言 ROLLBACK
 *      C7  回归测试中任一 TC 函数体不得同时断言 ROLLBACK 与 BEGIN
 *   B) 运行时检查(调用 window.runXxxRegression,校验 report)
 *      C8  回归全部通过(passed===total && success)
 *
 * 用法:
 *   ConventionChecker.register({...})       // 注册新服务
 *   const r = await ConventionChecker.run() // 运行全部检查
 *   ConventionChecker.runOnLoad = true       // 页面加载自动运行(默认 true)
 *
 * 浏览器环境:需通过 http 服务访问(用 fetch 读取源码);file:// 下 C1-C7 降级提示。
 * 全局名:ConventionChecker / window.ConventionChecker
 * ============================================================
 */

const ConventionChecker = (function () {
    'use strict';

    // ---------- 服务注册表(新增服务时在此 register 一条即可) ----------
    const SERVICES = [];

    // 业务判断关键字 denylist(命中即视为 preflight 越界做业务判断)
    const BUSINESS_KEYWORDS = [
        '库存不足', /stock\s*<[^=]/, /stock\s*<=/, '余额不足', '优惠券无效',
        '积分不足', '商品不存在', /points\s*<\s*\w/, '授权不足',
    ];

    // ---------- 输出适配 ----------
    let _sink = null;
    function emit(line, type) {
        if (_sink && typeof _sink === 'function') { _sink(line, type); return; }
        if (typeof document !== 'undefined' && document.getElementById) {
            const el = document.getElementById('conventionLog');
            if (el) {
                const color = type === 'pass' ? '#0f0' : type === 'fail' ? '#f88'
                    : type === 'warn' ? '#fc0' : '#0ff';
                const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
                const div = document.createElement('div');
                div.style.color = color;
                div.innerHTML = `<span style="opacity:0.6;">[${t}]</span> ${line}`;
                el.appendChild(div);
                el.scrollTop = el.scrollHeight;
                return;
            }
        }
        if (typeof console !== 'undefined') console.log(line);
    }

    // ---------- 源码抽取工具 ----------

    /** 剥离注释(// 行注释 + /* 块注释),保留字符串字面量。供抽取前预处理,避免命中注释里的同名关键字。 */
    function stripComments(src) {
        let out = '';
        const n = src.length;
        let i = 0, inStr = false, strCh = '';
        while (i < n) {
            const c = src[i];
            const next = src[i + 1];
            if (inStr) {
                out += c;
                if (c === '\\') { out += src[i + 1] || ''; i += 2; continue; }
                if (c === strCh) inStr = false;
                i++; continue;
            }
            if (c === '"' || c === "'" || c === '`') { inStr = true; strCh = c; out += c; i++; continue; }
            if (c === '/' && next === '/') { while (i < n && src[i] !== '\n') i++; continue; }
            if (c === '/' && next === '*') {
                i += 2;
                while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++;
                i += 2; continue;
            }
            out += c; i++;
        }
        return out;
    }

    /**
     * 抽取所有名为 name 的函数体(支持 function 声明 / async function / 对象方法简写 / 箭头属性)
     * 返回 [{ body, start, end }]。基于括号/字符串感知的 brace-match。
     * 注意:调用前应先 stripComments,避免命中注释里的同名词。
     */
    function extractFnBodys(src, name) {
        const out = [];
        let idx = 0;
        const max = src.length;

        // 判断 name 出现处是否为"属性别名"(name: bareId,)而非函数定义
        // 别名形如  applyDeduct: applyDeduct,  —— 应跳过,否则会误匹配到后续函数体
        function isPropertyAlias(at) {
            let p = at + name.length;
            while (p < max && (src[p] === ' ' || src[p] === '\t')) p++;
            if (src[p] !== ':') return false;          // 不是属性键上下文,按函数处理
            p++;
            while (p < max && (src[p] === ' ' || src[p] === '\t')) p++;
            // 读值的首个标识符
            let id = '';
            while (p < max && /[A-Za-z0-9_$]/.test(src[p])) { id += src[p]; p++; }
            // 值是 function/async/get/set 或直接跟 ( (箭头) → 是函数属性,不跳过
            if (id === 'function' || id === 'async' || id === 'get' || id === 'set') return false;
            if (id === '') {
                // 值不以标识符开头(可能是 ( 或 [ 或 { ) → 非别名,不跳过
                return false;
            }
            // 值是裸标识符,后接 ,/}/换行 → 别名,跳过
            while (p < max && (src[p] === ' ' || src[p] === '\t')) p++;
            return src[p] === ',' || src[p] === '}' || src[p] === '\n' || src[p] === '\r';
        }

        while (idx < max) {
            const at = src.indexOf(name, idx);
            if (at === -1) break;
            idx = at + name.length;
            // 词边界:前一字符不得是 标识符字符
            const before = at > 0 ? src[at - 1] : ' ';
            if (/[A-Za-z0-9_$]/.test(before)) continue;
            // 后一字符不得是 标识符字符(避免命中 applyDeductFoo 之类)
            const afterCh = src[at + name.length] || ' ';
            if (/[A-Za-z0-9_$]/.test(afterCh)) continue;

            // 判定 name 之后(跳过空白)的首字符,决定是否为函数定义:
            //   ':'  → 属性键(可能是别名 / 箭头属性 / 方法),由 isPropertyAlias 区分
            //   '('  → 函数声明 / 方法简写 (function NAME( / async function NAME( / NAME( shorthand)
            //   其它 → 引用/调用结果(如 cases 数组里 fn: TC2_xxx, ),跳过
            let p = at + name.length;
            while (p < max && (src[p] === ' ' || src[p] === '\t')) p++;
            const nextCh = src[p] || '';
            if (nextCh === ':') {
                if (isPropertyAlias(at)) continue;   // 别名,跳过
                // 否则是箭头属性/方法属性,继续往下找 '{'
            } else if (nextCh === '(') {
                // 函数声明 / 方法简写,继续往下找 '{'
            } else {
                continue;                            // 引用/值,跳过
            }

            // 从 name 之后扫描到"函数体的第一个 {",途中维持括号深度=0
            let j = at + name.length;
            let depth = 0, inStr = false, strCh = '', scanned = 0;
            let bodyStart = -1;
            while (j < max && scanned < 800) {
                const c = src[j];
                if (inStr) {
                    if (c === '\\') { j += 2; scanned += 2; continue; }
                    if (c === strCh) inStr = false;
                    j++; scanned++; continue;
                }
                if (c === '"' || c === "'" || c === '`') { inStr = true; strCh = c; j++; scanned++; continue; }
                if (c === '(') depth++;
                else if (c === ')') depth = Math.max(0, depth - 1);
                else if (c === '{' && depth === 0) { bodyStart = j; break; }
                else if (c === ';' && depth === 0) break;        // 语句结束,非函数定义(调用)
                else if (c === '=' && depth === 0 && src[j + 1] !== '=' && src[j + 1] !== '>') {
                    // 形如 name = function/() => ,继续往后找
                }
                j++; scanned++;
            }
            if (bodyStart === -1) continue;

            // brace-match 取函数体
            let d = 0, k = bodyStart, end = -1;
            while (k < max) {
                const c = src[k];
                if (c === '{') d++;
                else if (c === '}') { d--; if (d === 0) { end = k; break; } }
                k++;
            }
            if (end === -1) continue;
            out.push({ body: src.slice(bodyStart, end + 1), start: at, end });
            idx = end + 1;
        }
        return out;
    }

    /** 抽取 `prop: [ ... ]` 数组区域文本(stages / asyncTasks) */
    function extractArrayRegion(src, prop) {
        const key = prop + ':';
        const at = src.indexOf(key);
        if (at === -1) return null;
        let i = at + key.length;
        while (i < src.length && src[i] !== '[') i++;
        if (i >= src.length) return null;
        let d = 0, start = i;
        while (i < src.length) {
            const c = src[i];
            if (c === '[') d++;
            else if (c === ']') { d--; if (d === 0) return src.slice(start, i + 1); }
            i++;
        }
        return null;
    }

    /** 抽取测试文件中所有 TC 函数体(以 async function TCn_ 或 function TCn_ 开头) */
    function extractTCBodies(src) {
        const out = [];
        const re = /(?:async\s+)?function\s+(TC\d+_\w*)\s*\(/g;
        let m;
        while ((m = re.exec(src)) !== null) {
            const fns = extractFnBodys(src, m[1]);
            if (fns.length) out.push({ name: m[1], body: fns[0].body });
        }
        return out;
    }

    function matchAny(text, kw) {
        return kw.some(k => (k instanceof RegExp ? k.test(text) : text.indexOf(k) !== -1));
    }

    // ---------- fetch 源码 ----------
    async function fetchText(path) {
        const r = await fetch(path, { cache: 'no-store' });
        if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + path);
        return await r.text();
    }

    // ---------- 单服务检查 ----------
    async function checkService(svc) {
        const results = [];
        const push = (id, severity, msg) => results.push({ id, service: svc.name, severity, msg });

        let svcSrc = null, testSrc = null, fetchOk = true;
        try {
            svcSrc = stripComments(await fetchText(svc.servicePath));
        } catch (e) {
            fetchOk = false;
            push('FETCH', 'FAIL', `无法读取服务源码 ${svc.servicePath}: ${e.message}(需 http 服务, file:// 不可用)`);
        }
        try {
            if (svc.testPath) testSrc = stripComments(await fetchText(svc.testPath));
        } catch (e) {
            if (svc.testPath) push('FETCH', 'WARN', `无法读取测试源码 ${svc.testPath}: ${e.message}`);
        }

        if (!fetchOk) return results; // 源码都拿不到,静态检查无法进行

        // C1: preflight 不得含业务判断关键字
        const pf = extractFnBodys(svcSrc, 'preflight');
        if (pf.length === 0) {
            push('C1-preflight', 'WARN', '未找到 preflight 函数(该服务可能无预检,确认是否符合约定)');
        } else {
            const hit = matchAny(pf[0].body, BUSINESS_KEYWORDS);
            push('C1-preflight', hit ? 'FAIL' : 'PASS',
                hit ? 'preflight 含业务判断关键字(应移至事务内阶段)' : 'preflight 仅结构/参数校验,无业务判断');
        }

        // C2: stages 区域不得用 return { abort(阶段应 throw)
        const stages = extractArrayRegion(svcSrc, 'stages');
        if (stages) {
            const hasAbort = /return\s*\{\s*abort\s*:\s*true/.test(stages);
            push('C2-stages-throw', hasAbort ? 'FAIL' : 'PASS',
                hasAbort ? 'stages 区域出现 return { abort:true }(阶段失败应 throw,不应 abort)' : 'stages 失败均用 throw,符合约定');
        } else {
            push('C2-stages-throw', 'WARN', '未找到 stages 数组区域');
        }

        // C3: 共享核心函数体不得出现 TransactionTemplate(禁止嵌套事务)
        const sharedCores = svc.sharedCoreFns || [];
        if (sharedCores.length === 0) {
            push('C3-shared-core', 'PASS', '无共享核心(不对外委托,跳过嵌套事务检查)');
        } else {
            let bad = [];
            for (const fn of sharedCores) {
                const bodies = extractFnBodys(svcSrc, fn);
                if (bodies.length === 0) { bad.push(`${fn}(未找到)`); continue; }
                if (bodies.some(b => /TransactionTemplate|new\s+Template\s*\(/.test(b.body))) {
                    bad.push(fn);
                }
            }
            push('C3-shared-core', bad.length ? 'FAIL' : 'PASS',
                bad.length ? `共享核心 [${bad.join(',')}] 含 TransactionTemplate(禁止嵌套事务)` : '共享核心未开子事务,符合约定');
        }

        // C4: 服务文件须引用 TransactionTemplate(基于工具包)
        const usesTK = /TransactionTemplate/.test(svcSrc);
        push('C4-uses-toolkit', usesTK ? 'PASS' : 'WARN',
            usesTK ? '服务基于 TransactionTemplate' : '服务未引用 TransactionTemplate(确认是否真为事务服务)');

        // C5: 回归测试须断言 failedStage
        if (testSrc) {
            push('C5-test-failedStage', /failedStage/.test(testSrc) ? 'PASS' : 'WARN',
                /failedStage/.test(testSrc) ? '测试断言了 failedStage' : '测试未断言 failedStage(失败用例应校验失败阶段)');

            // C6: 回归测试须断言 ROLLBACK
            push('C6-test-rollback', /ROLLBACK/.test(testSrc) ? 'PASS' : 'WARN',
                /ROLLBACK/.test(testSrc) ? '测试断言了 ROLLBACK' : '测试未断言 ROLLBACK');

            // C7: 任一 TC 函数体不得同时断言 ROLLBACK 与 BEGIN
            const tcs = extractTCBodies(testSrc);
            const badTCs = tcs.filter(t =>
                /ROLLBACK/.test(t.body) && /BEGIN/.test(t.body) && /txTypes|tx_log/.test(t.body));
            push('C7-test-begin-after-rollback', badTCs.length ? 'WARN' : 'PASS',
                badTCs.length ? `TC [${badTCs.map(t => t.name).join(',')}] 同时断言 ROLLBACK+BEGIN(回滚用例不应断言 BEGIN)` : '无回滚用例误断言 BEGIN');
        } else {
            push('C5-test-failedStage', 'WARN', '无测试源码,跳过测试断言检查');
            push('C6-test-rollback', 'WARN', '无测试源码,跳过');
            push('C7-test-begin-after-rollback', 'WARN', '无测试源码,跳过');
        }

        return results;
    }

    // ---------- 运行时检查 ----------
    async function checkRuntime(svc) {
        const results = [];
        if (!svc.runFn || typeof window === 'undefined' || typeof window[svc.runFn] !== 'function') {
            results.push({ id: 'C8-runtime', service: svc.name, severity: 'WARN', msg: `无运行函数 ${svc.runFn || '(未配置)'},跳过运行时检查` });
            return results;
        }
        try {
            const report = await window[svc.runFn]();
            const ok = report && report.success && report.passed === report.total;
            results.push({
                id: 'C8-runtime', service: svc.name,
                severity: ok ? 'PASS' : 'FAIL',
                msg: ok ? `回归全部通过 ${report.passed}/${report.total}` : `回归未通过 ${report.passed}/${report.total} PASS(失败 ${report.failed || 0})`,
            });
        } catch (e) {
            results.push({ id: 'C8-runtime', service: svc.name, severity: 'FAIL', msg: `运行回归异常: ${e.message}` });
        }
        return results;
    }

    // ---------- 主入口 ----------
    async function run(opts) {
        const options = opts || {};
        _sink = options.sink || null;
        const withRuntime = options.runtime !== false; // 默认含运行时

        const sep = '═'.repeat(70);
        emit(sep, 'info');
        emit('  事务服务约定检查器 · ' + new Date().toISOString().slice(0, 19).replace('T', ' '), 'info');
        emit('  检查项: C1预检无业务判断 / C2阶段用throw / C3共享核心无嵌套事务 /', 'info');
        emit('          C4基于工具包 / C5断言failedStage / C6断言ROLLBACK / C7回滚不断言BEGIN / C8回归通过', 'info');
        emit(sep, 'info');

        const all = [];
        for (const svc of SERVICES) {
            emit('', 'info');
            emit(`▼ 服务: ${svc.name}  (${svc.servicePath})`, 'info');
            const staticRes = await checkService(svc);
            all.push(...staticRes);
            staticRes.forEach(r => {
                const icon = r.severity === 'PASS' ? '✓' : r.severity === 'FAIL' ? '✗' : '⚠';
                const type = r.severity === 'PASS' ? 'pass' : r.severity === 'FAIL' ? 'fail' : 'warn';
                emit(`  ${icon} [${r.id}] ${r.severity}: ${r.msg}`, type);
            });
        }

        if (withRuntime) {
            emit('', 'info');
            emit('▼ 运行时检查(执行各回归套件)', 'info');
            for (const svc of SERVICES) {
                const rtRes = await checkRuntime(svc);
                all.push(...rtRes);
                rtRes.forEach(r => {
                    const icon = r.severity === 'PASS' ? '✓' : r.severity === 'FAIL' ? '✗' : '⚠';
                    const type = r.severity === 'PASS' ? 'pass' : r.severity === 'FAIL' ? 'fail' : 'warn';
                    emit(`  ${icon} [${r.id}@${r.service}] ${r.severity}: ${r.msg}`, type);
                });
            }
        }

        // 汇总
        const passN = all.filter(r => r.severity === 'PASS').length;
        const warnN = all.filter(r => r.severity === 'WARN').length;
        const failN = all.filter(r => r.severity === 'FAIL').length;
        emit('', 'info');
        emit(sep, 'info');
        const ok = failN === 0;
        emit(`  约定检查${ok ? '全部通过' : '存在失败'}: ${passN} PASS / ${warnN} WARN / ${failN} FAIL`, ok ? 'pass' : 'fail');
        emit(sep, ok ? 'pass' : 'fail');

        const report = {
            timestamp: new Date().toISOString(),
            total: all.length, pass: passN, warn: warnN, fail: failN,
            success: ok, results: all,
        };
        if (typeof window !== 'undefined') window.__lastConventionReport = report;
        return report;
    }

    // ---------- 公共 API ----------
    return {
        register(desc) {
            // desc: { name, servicePath, testPath?, sharedCoreFns?, standaloneFns?, runFn? }
            if (!SERVICES.find(s => s.name === desc.name)) SERVICES.push(desc);
            return this;
        },
        list() { return SERVICES.slice(); },
        run,
        // 业务关键字 denylist 可外部覆盖
        setBusinessKeywords(kw) { BUSINESS_KEYWORDS.length = 0; kw.forEach(k => BUSINESS_KEYWORDS.push(k)); return this; },
    };
})();

// ---------- 预注册现有 3 个事务服务(新增服务时仿照追加一条即可) ----------
if (typeof ConventionChecker !== 'undefined') {
    ConventionChecker
        .register({
            name: 'inventory',
            servicePath: 'js/inventory-service.js',
            testPath: 'js/inventory-regression-test.js',
            sharedCoreFns: ['applyDeduct', 'applyRestock'],
            standaloneFns: ['deduct', 'restock'],
            runFn: 'runInventoryRegression',
        })
        .register({
            name: 'checkout',
            servicePath: 'js/checkout-service.js',
            testPath: 'js/checkout-regression-test.js',
            sharedCoreFns: [],
            standaloneFns: ['submit'],
            runFn: 'runCheckoutRegression',
        })
        .register({
            name: 'agent-upgrade',
            servicePath: 'js/agent-upgrade-service.js',
            testPath: 'js/agent-upgrade-regression-test.js',
            sharedCoreFns: [],
            standaloneFns: [],
            runFn: 'runAgentUpgradeRegression',
        })
        .register({
            name: 'agent-shipping',
            servicePath: 'js/agent-shipping-service.js',
            testPath: 'js/agent-shipping-regression-test.js',
            sharedCoreFns: ['accrueServiceFee'],
            standaloneFns: ['claim', 'release'],
            runFn: 'runAgentShippingRegression',
        });
}

// 暴露到 window 全局
if (typeof window !== 'undefined') {
    window.ConventionChecker = ConventionChecker;
}
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ConventionChecker };
}
