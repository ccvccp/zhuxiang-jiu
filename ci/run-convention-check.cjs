#!/usr/bin/env node
/**
 * run-convention-check.cjs  ·  CI 事务服务约定检查运行器(静态门禁)
 * ============================================================
 * 用途:
 *   在 CI 流水线中无依赖运行 service-convention-checker.js 的静态检查
 *   (C1~C7),每次提交自动拦截违反《预检与失败阶段策略》的代码。
 *
 * 设计:
 *   · 零外部依赖,仅用 Node 内置模块(http/fs/path)
 *   · 通过 shim globalThis.fetch 让浏览器版检查器直接读本地文件,
 *     无需启动 HTTP 服务,无端口冲突
 *   · 复用 service-convention-checker.js 的全部静态分析逻辑
 *     (stripComments / extractFnBodys / brace-match / 关键字匹配)
 *   · C8 运行时回归需 headless 浏览器(localStorage/DOM),按约定
 *     由本地 module-test.html 人工触发,CI 不执行(返回 WARN 不阻断)
 *
 * 退出码:
 *   0  检查通过(FAIL === 0)
 *   1  存在 FAIL 项(阻断流水线)
 *   2  运行器/检查器异常
 *
 * 用法:
 *   node ci/run-convention-check.cjs                 # 默认: FAIL 阻断
 *   node ci/run-convention-check.cjs --strict-warn   # WARN 也阻断
 *
 * CI(GitHub Actions)见 .github/workflows/convention-check.yml
 * ============================================================
 */

'use strict';

const path = require('path');
const fs = require('fs');
const fsp = fs.promises;

const PROJECT_ROOT = path.resolve(__dirname, '..');
const APP_ROOT = path.join(PROJECT_ROOT, 'zhuxiang-jiu');
const CHECKER_PATH = path.join(APP_ROOT, 'js', 'toolkit', 'service-convention-checker.js');
const REPORT_PATH = path.join(__dirname, 'convention-report.json');

// ANSI 颜色(非 TTY 也能输出,GitHub Actions 日志支持渲染)
const C = {
    reset: '\x1b[0m', bold: '\x1b[1m',
    red: '\x1b[31m', green: '\x1b[32m', yellow: '\x1b[33m', cyan: '\x1b[36m', gray: '\x1b[90m',
};

// ---------- 校验前置条件 ----------
if (!fs.existsSync(CHECKER_PATH)) {
    console.error(`${C.red}找不到检查器: ${CHECKER_PATH}${C.reset}`);
    console.error(`${C.gray}请确认仓库结构与路径(ci/ 位于仓库根, zhuxiang-jiu/ 为应用根)${C.reset}`);
    process.exit(2);
}
if (!fs.existsSync(APP_ROOT)) {
    console.error(`${C.red}找不到应用根目录: ${APP_ROOT}${C.reset}`);
    process.exit(2);
}

// ---------- shim globalThis.fetch: 相对路径读本地文件 ----------
// 检查器 fetchText(path) 传入相对路径如 'js/inventory-service.js',
// 这里改写为从 APP_ROOT 读盘,返回伪 Response 对象。
const originalFetch = typeof globalThis.fetch === 'function' ? globalThis.fetch : null;

globalThis.fetch = async function fetchShim(url, opts) {
    // 绝对 http(s) URL 透传给原生 fetch(本运行器不会用到,但保留兼容)
    if (typeof url === 'string' && /^https?:\/\//.test(url)) {
        if (!originalFetch) throw new Error('原生 fetch 不可用,无法处理 http URL: ' + url);
        return originalFetch(url, opts);
    }
    const rel = String(url || '').replace(/^[\\/]+/, ''); // 去掉前导斜杠
    const fp = path.join(APP_ROOT, rel);
    try {
        const body = await fsp.readFile(fp, 'utf8');
        return { ok: true, status: 200, statusText: 'OK', text: async () => body };
    } catch (e) {
        // 文件不存在 → 检查器 fetchText 会因 !r.ok 走 FAIL 分支
        return { ok: false, status: 404, statusText: 'Not Found', text: async () => '' };
    }
};

// ---------- 加载检查器(加载即自动注册 3 个服务) ----------
let ConventionChecker;
try {
    ({ ConventionChecker } = require(CHECKER_PATH));
} catch (e) {
    console.error(`${C.red}加载检查器失败: ${e.message}${C.reset}`);
    process.exit(2);
}
if (!ConventionChecker || typeof ConventionChecker.run !== 'function') {
    console.error(`${C.red}检查器未正确导出 ConventionChecker.run${C.reset}`);
    process.exit(2);
}

// ---------- 输出 sink(彩色,逐行写入 stdout) ----------
function sink(line, type) {
    const color = type === 'pass' ? C.green
        : type === 'fail' ? C.red
            : type === 'warn' ? C.yellow
                : C.cyan;
    process.stdout.write(color + line + C.reset + '\n');
}

// ---------- 主流程 ----------
async function main() {
    const args = process.argv.slice(2);
    const strictWarn = args.includes('--strict-warn');

    const services = ConventionChecker.list();
    console.log(`${C.bold}== 事务服务约定检查 · CI 静态门禁 ==${C.reset}`);
    console.log(`${C.gray}应用根目录: ${APP_ROOT}${C.reset}`);
    console.log(`${C.gray}已注册服务: ${services.map(s => s.name).join(' / ')} (${services.length})${C.reset}`);
    console.log(`${C.gray}检查范围  : C1~C7 静态源码 (C8 运行时回归由本地 module-test.html 人工触发)${C.reset}`);
    console.log('');

    let report;
    try {
        // runtime:false → 跳过 C8,只跑静态 C1~C7
        report = await ConventionChecker.run({ runtime: false, sink });
    } catch (e) {
        console.error(`${C.red}检查器执行异常: ${e && e.stack ? e.stack : e}${C.reset}`);
        process.exit(2);
    }

    // 落盘 JSON 报告(供 CI artifact 上传)
    try {
        fs.writeFileSync(REPORT_PATH, JSON.stringify(report, null, 2));
    } catch (e) {
        console.error(`${C.yellow}报告写入失败(不影响门禁): ${e.message}${C.reset}`);
    }

    console.log('');
    console.log(`${C.bold}报告已写入: ${path.relative(PROJECT_ROOT, REPORT_PATH)}${C.reset}`);

    const { pass = 0, warn = 0, fail = 0 } = report;
    const summary = `${pass} PASS / ${warn} WARN / ${fail} FAIL`;

    // 阻断判定
    let blocked = false;
    const reasons = [];
    if (fail > 0) { blocked = true; reasons.push(`${fail} 项 FAIL`); }
    if (strictWarn && warn > 0) { blocked = true; reasons.push(`${warn} 项 WARN(--strict-warn)`); }

    if (blocked) {
        console.error(`${C.red}${C.bold}✗ 约定检查未通过: ${summary} [${reasons.join('; ')}]${C.reset}`);
        // 列出所有 FAIL/WARN(严格模式)项,便于定位
        const offenders = (report.results || []).filter(r =>
            r.severity === 'FAIL' || (strictWarn && r.severity === 'WARN'));
        if (offenders.length) {
            console.error(`${C.red}---- 违规明细 ----${C.reset}`);
            for (const r of offenders) {
                console.error(`${C.red}  [${r.id}@${r.service}] ${r.severity}: ${r.msg}${C.reset}`);
            }
        }
        process.exit(1);
    } else {
        console.log(`${C.green}${C.bold}✓ 约定检查通过: ${summary}${C.reset}`);
        if (warn > 0) {
            console.log(`${C.gray}(含 ${warn} 项 WARN,非阻断;C8 运行时回归请人工在 module-test.html 执行)${C.reset}`);
        }
        process.exit(0);
    }
}

main().catch(e => {
    console.error(`${C.red}未捕获异常: ${e && e.stack ? e.stack : e}${C.reset}`);
    process.exit(2);
});
