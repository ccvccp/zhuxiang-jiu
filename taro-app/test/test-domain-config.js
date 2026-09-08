/**
 * test-domain-config.js · 域名感知 API_BASE 配置单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-navbar.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule)真实源码
 *       + 每用例独立模块域(隔离 window.location 与 TARO_ENV)
 *
 * 覆盖(域名部署 zxjiu.com 的分支逻辑):
 *   [H5 端·按页面域名判定]
 *   1.  zxjiu.com        → https://zxjiu.com(生产主域)
 *   2.  www.zxjiu.com    → https://zxjiu.com(生产 www 子域)
 *   3.  m.zxjiu.com      → https://zxjiu.com(任意子域均生产)
 *   4.  192.168.0.107   → http://192.168.0.107:8000(局域网真机回退)
 *   5.  localhost        → http://192.168.0.107:8000(本机调试回退)
 *   6.  127.0.0.1        → http://192.168.0.107:8000(本机调试回退)
 *   7.  同域子串不误判: zxjiu.com.evil.com → 调试后端(防后缀伪造)
 *   8.  window 未定义(H5 SSR 边界) → 调试后端(安全默认)
 *   [weapp 端·无 window 固定生产]
 *   9.  weapp 构建 → https://zxjiu.com(小程序合法域名要求 https)
 *   [常量契约]
 *   10. PROD_DOMAIN 导出为 zxjiu.com
 *   11. 生产/调试 BASE 均含合法 http(s) 协议头
 */
const fs = require('fs');
const path = require('path');
const ts = require('typescript');

const SRC = path.resolve(__dirname, '..', 'src', 'config', 'index.ts');

let passed = 0, failed = 0;
function record(name, ok, detail = '') {
  if (ok) { passed++; console.log(`  ✓ ${name}`); }
  else { failed++; console.log(`  ✗ ${name}${detail ? ` → ${detail}` : ''}`); }
}

/** 在指定环境下加载 config 模块并返回导出(带完整 origin 模拟) */
function loadConfig(env, hostname, origin) {
  const { outputText } = ts.transpileModule(fs.readFileSync(SRC, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2019,
    },
  });
  // 构造隔离的沙盒域: TARO_ENV 编译常量 + window.location(可缺省)
  const sandbox = { module: { exports: {} }, exports: {}, require, console };
  sandbox.global = sandbox;
  const loc = `({ hostname: ${JSON.stringify(hostname)}, origin: ${JSON.stringify(origin || `http://${hostname}`)} })`;
  const windowDecl = typeof hostname === 'undefined'
    ? 'var window = undefined;'
    : `var window = { location: ${loc} };`;
  const wrapper = new Function('module', 'exports', 'require', 'console', 'globalThis',
    `"use strict"; var process = { env: { TARO_ENV: ${JSON.stringify(env)} } };
     ${windowDecl}
     ${outputText}`);
  wrapper(sandbox.module, sandbox.module.exports, require, console, sandbox);
  return sandbox.module.exports;
}

console.log('域名感知 API_BASE 配置单元测试');
console.log('='.repeat(52));

// ---------- H5 端·按页面域名判定 ----------
const h5 = (host) => loadConfig('h5', host, `http://${host}`).API_BASE;

record('H5 主域 zxjiu.com → 生产 https',
  h5('zxjiu.com') === 'https://zxjiu.com', h5('zxjiu.com'));
record('H5 www.zxjiu.com → 生产 https',
  h5('www.zxjiu.com') === 'https://zxjiu.com', h5('www.zxjiu.com'));
record('H5 任意子域 m.zxjiu.com → 生产 https',
  h5('m.zxjiu.com') === 'https://zxjiu.com', h5('m.zxjiu.com'));
record('H5 局域网 192.168.0.107 → 同机后端 :8000',
  h5('192.168.0.107') === 'http://192.168.0.107:8000', h5('192.168.0.107'));
record('H5 局域网 10.x → 同机后端 :8000',
  h5('10.0.0.5') === 'http://10.0.0.5:8000', h5('10.0.0.5'));
record('H5 局域网 172.16.x → 同机后端 :8000',
  h5('172.16.3.9') === 'http://172.16.3.9:8000', h5('172.16.3.9'));
record('H5 公网 IP 直访 47.236.61.117 → 同源直连',
  h5('47.236.61.117') === 'http://47.236.61.117', h5('47.236.61.117'));
record('H5 localhost → 调试后端',
  h5('localhost') === 'http://192.168.0.107:8000', h5('localhost'));
record('H5 127.0.0.1 → 调试后端',
  h5('127.0.0.1') === 'http://192.168.0.107:8000', h5('127.0.0.1'));
record('后缀伪造域 zxjiu.com.evil.com 不误判为生产(同源兜底)',
  h5('zxjiu.com.evil.com') === 'http://zxjiu.com.evil.com', h5('zxjiu.com.evil.com'));
record('H5 window 未定义(SSR 边界) → 安全默认调试后端',
  loadConfig('h5', undefined).API_BASE === 'http://192.168.0.107:8000');

// ---------- weapp 端 ----------
record('weapp 构建(无 window) → 固定生产 https://zxjiu.com',
  loadConfig('weapp', undefined).API_BASE === 'https://zxjiu.com');

// ---------- 常量契约 ----------
const cfg = loadConfig('h5', '192.168.0.107');
record('PROD_DOMAIN 导出为 zxjiu.com', cfg.PROD_DOMAIN === 'zxjiu.com');
record('生产 BASE 含 https 协议头', h5('zxjiu.com').startsWith('https://'));
record('调试 BASE 含 http 协议头+端口', /http:\/\/\d+\.\d+\.\d+\.\d+:\d+/.test(cfg.API_BASE));

console.log('='.repeat(52));
console.log(`域名感知配置单元测试: ${passed}/${passed + failed} ${failed === 0 ? 'PASS' : 'FAIL'}`);
process.exit(failed === 0 ? 0 : 1);
