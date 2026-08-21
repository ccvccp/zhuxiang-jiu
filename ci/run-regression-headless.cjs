#!/usr/bin/env node
/**
 * run-regression-headless.cjs  ·  CI 运行时回归测试运行器 (C8)
 * ============================================================
 * 用途:
 *   在 CI 流水线中用 puppeteer-core + 系统 chromium 加载
 *   module-test.html,执行各服务注册的 runFn (C8-runtime),
 *   含 runAgentShippingRegression (8用例: 含厂家直供兼容场景 TC5-TC8)。
 *
 * 设计:
 *   · puppeteer-core 连接系统 chromium (CI 用 apt 安装,不下载,省时间)
 *   · Node 内置 http 模块启动静态文件服务器 (零外部依赖,无端口冲突)
 *   · 在页面上下文中调用 ConventionChecker.run({ runtime: true })
 *   · 结果落盘 ci/regression-report.json (供 CI artifact 上传 + Job Summary)
 *
 * CI 环境:
 *   · CHROME_BIN 环境变量由 .github/workflows/convention-check.yml 注入
 *   · 备用路径: /usr/bin/chromium-browser / /usr/bin/chromium / /usr/bin/google-chrome
 *   · 本地复现: 设 CHROME_BIN 指向本机 chrome, 或脚本会自动查找
 *
 * 退出码:
 *   0  所有服务回归通过 (fail === 0)
 *   1  存在 FAIL 项 (阻断流水线)
 *   2  运行器/浏览器异常
 *
 * 用法:
 *   node ci/run-regression-headless.cjs              # 默认: FAIL 阻断
 *   CHROME_BIN=/path/to/chrome node ci/run-regression-headless.cjs
 *
 * CI (GitHub Actions) 见 .github/workflows/convention-check.yml Job 2
 * ============================================================
 */

'use strict';

const path = require('path');
const fs = require('fs');
const http = require('http');
const { execSync } = require('child_process');

const PROJECT_ROOT = path.resolve(__dirname, '..');
const APP_ROOT = path.join(PROJECT_ROOT, 'zhuxiang-jiu');
const REPORT_PATH = path.join(__dirname, 'regression-report.json');
const PORT = 8090; // 避开 8080 (本地开发用)

// ANSI 颜色 (GitHub Actions 日志支持渲染)
const C = {
    reset: '\x1b[0m', bold: '\x1b[1m',
    red: '\x1b[31m', green: '\x1b[32m', yellow: '\x1b[33m',
    cyan: '\x1b[36m', gray: '\x1b[90m',
};

// ---------- 查找 chromium 可执行路径 ----------
function findChrome() {
    // 优先用环境变量 (CI 注入)
    if (process.env.CHROME_BIN && fs.existsSync(process.env.CHROME_BIN)) {
        return process.env.CHROME_BIN;
    }
    // CI 常见路径
    const candidates = [
        '/usr/bin/chromium-browser',
        '/usr/bin/chromium',
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        // macOS 本地
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ];
    for (const p of candidates) {
        try {
            fs.accessSync(p, fs.constants.X_OK);
            return p;
        } catch (e) { /* continue */ }
    }
    // Windows 本地 (常见安装路径)
    if (process.platform === 'win32') {
        const winCandidates = [
            'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
            'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        ];
        for (const p of winCandidates) {
            if (fs.existsSync(p)) return p;
        }
    }
    // 用 which/where 查找
    try {
        const cmd = process.platform === 'win32'
            ? 'where chrome 2>nul'
            : 'which chromium-browser chromium google-chrome 2>/dev/null || true';
        const out = execSync(cmd, { encoding: 'utf8' });
        const found = out.split('\n').map(s => s.trim()).filter(Boolean);
        if (found.length) return found[0];
    } catch (e) { /* ignore */ }
    return null;
}

// ---------- 启动静态文件服务器 (Node 内置 http) ----------
function startServer() {
    return new Promise((resolve, reject) => {
        const mimeMap = {
            '.html': 'text/html; charset=utf-8',
            '.js':   'application/javascript; charset=utf-8',
            '.css':  'text/css; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.png':  'image/png',
            '.svg':  'image/svg+xml',
        };
        const server = http.createServer((req, res) => {
            let url = req.url.split('?')[0];
            if (url === '/') url = '/index.html';
            const fp = path.join(APP_ROOT, url);
            // 防目录穿越
            if (!fp.startsWith(APP_ROOT)) {
                res.statusCode = 403;
                res.end('403 Forbidden');
                return;
            }
            fs.readFile(fp, (err, data) => {
                if (err) {
                    res.statusCode = 404;
                    res.end('404 Not Found');
                    return;
                }
                const ext = path.extname(fp);
                res.setHeader('Content-Type', mimeMap[ext] || 'application/octet-stream');
                res.setHeader('Access-Control-Allow-Origin', '*');
                res.end(data);
            });
        });
        server.on('error', reject);
        server.listen(PORT, '127.0.0.1', () => resolve(server));
    });
}

// ---------- 主流程 ----------
async function main() {
    let puppeteer;
    try {
        puppeteer = require('puppeteer-core');
    } catch (e) {
        console.error(`${C.red}puppeteer-core 未安装${C.reset}`);
        console.error(`${C.gray}请运行: npm install puppeteer-core${C.reset}`);
        process.exit(2);
    }

    const chromePath = findChrome();
    if (!chromePath) {
        console.error(`${C.red}找不到 chromium 可执行文件${C.reset}`);
        console.error(`${C.gray}CI: workflow 已安装 chromium-browser${C.reset}`);
        console.error(`${C.gray}本地: 设 CHROME_BIN 环境变量指向 chrome.exe 路径${C.reset}`);
        process.exit(2);
    }
    console.log(`${C.gray}chromium 路径: ${chromePath}${C.reset}`);

    // 启动静态文件服务器
    const server = await startServer();
    console.log(`${C.gray}HTTP 服务器已启动: http://127.0.0.1:${PORT}${C.reset}`);

    let browser;
    try {
        browser = await puppeteer.launch({
            executablePath: chromePath,
            headless: 'new',
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage', // 避免 /dev/shm 不足
                '--disable-gpu',
            ],
        });

        const page = await browser.newPage();
        // 转发浏览器 console 到 CI 日志
        page.on('console', msg => {
            const text = msg.text();
            if (text) console.log(`${C.gray}[browser] ${text}${C.reset}`);
        });
        page.on('pageerror', err => {
            console.error(`${C.red}[pageerror] ${err.message}${C.reset}`);
        });

        // 加载 module-test.html
        const url = `http://127.0.0.1:${PORT}/module-test.html`;
        console.log(`${C.gray}加载页面: ${url}${C.reset}`);
        await page.goto(url, { waitUntil: 'networkidle0', timeout: 30000 });

        // 等待 ConventionChecker + 发货认领回归函数加载
        await page.waitForFunction(
            'typeof ConventionChecker !== "undefined" && typeof window.runAgentShippingRegression === "function"',
            { timeout: 10000 }
        );
        console.log(`${C.green}页面加载完成, ConventionChecker 已就绪${C.reset}`);

        // 列出注册的服务
        const services = await page.evaluate(() => ConventionChecker.list().map(s => s.name));
        console.log(`${C.gray}已注册服务: ${services.join(' / ')} (${services.length})${C.reset}`);
        console.log(`${C.gray}检查范围: C1~C7 静态 + C8 运行时回归 (含发货认领8用例)${C.reset}`);
        console.log('');

        // 执行全量约定检查 (含 C8 运行时回归)
        console.log(`${C.bold}== 开始执行全量约定检查 (C1-C8) ==${C.reset}`);
        const report = await page.evaluate(async () => {
            const r = await ConventionChecker.run({ runtime: true });
            return {
                total: r.total,
                pass: r.pass,
                warn: r.warn,
                fail: r.fail,
                success: r.success,
                fails: (r.results || [])
                    .filter(x => x.severity === 'FAIL')
                    .map(x => ({ id: x.id, service: x.service, msg: x.msg })),
                // 附带发货认领回归详情 (含 TC1-TC8 厂家直供兼容场景)
                shippingRegression: window.__lastAgentShippingRegressionReport || null,
                // 附带 checkout 回归详情
                checkoutRegression: window.__lastCheckoutRegressionReport || null,
            };
        });

        // 落盘报告
        fs.writeFileSync(REPORT_PATH, JSON.stringify(report, null, 2));
        console.log(`${C.gray}报告已写入: ${path.relative(PROJECT_ROOT, REPORT_PATH)}${C.reset}`);

        // 输出摘要
        console.log('');
        console.log(`${C.bold}== 运行时回归结果 ==${C.reset}`);
        console.log(`  ${C.green}PASS: ${report.pass}${C.reset} / ${C.yellow}WARN: ${report.warn}${C.reset} / ${C.red}FAIL: ${report.fail}${C.reset}`);

        if (report.shippingRegression) {
            const sr = report.shippingRegression;
            console.log('');
            console.log(`${C.bold}发货认领回归: ${sr.passed}/${sr.total} PASS (${sr.passRate})${C.reset}`);
            (sr.results || []).forEach(t => {
                const icon = t.status === 'PASS' ? `${C.green}✓` : `${C.red}✗`;
                console.log(`  ${icon} ${t.name} [${t.duration}ms]${t.error ? ' - ' + t.error : ''}${C.reset}`);
            });
        }

        if (report.fails.length) {
            console.error('');
            console.error(`${C.red}---- 违规明细 ----${C.reset}`);
            for (const f of report.fails) {
                console.error(`${C.red}  [${f.id}@${f.service}] ${f.msg}${C.reset}`);
            }
        }

        // 退出码
        if (report.fail > 0) {
            console.error('');
            console.error(`${C.red}${C.bold}✗ 运行时回归未通过: ${report.fail} 项 FAIL${C.reset}`);
            process.exit(1);
        } else {
            console.log('');
            console.log(`${C.green}${C.bold}✓ 运行时回归全部通过${C.reset}`);
            process.exit(0);
        }
    } catch (e) {
        console.error(`${C.red}运行时回归异常: ${e && e.stack ? e.stack : e}${C.reset}`);
        process.exit(2);
    } finally {
        if (browser) {
            await browser.close().catch(() => {});
        }
        server.close();
    }
}

main().catch(e => {
    console.error(`${C.red}未捕获异常: ${e && e.stack ? e.stack : e}${C.reset}`);
    process.exit(2);
});
