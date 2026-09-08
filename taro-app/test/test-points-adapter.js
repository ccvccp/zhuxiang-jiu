/**
 * test-points-adapter.js · 积分 API VO 映射单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-domain-config.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule)真实源码
 *       + mock request 层断言 URL/method/body 契约
 *
 * 覆盖(签到后端化):
 *   [signin POST]
 *   1.  URL + method 契约
 *   2.  userId 序列化进 body
 *   3.  isBonus 0/1 → boolean 归一
 *   4.  宝箱日 bonusPoints 透传
 *   [signinRecords GET]
 *   5.  URL 携带 user_id 与 limit
 *   6.  空记录 → []
 *   7.  snake_case 字段容错(sign_date)
 *   [account GET]
 *   8.  五字段映射(totalPoints/frozen/earned/spent/expiring)
 *   9.  缺省字段兜底 0
 *   [logs GET]
 *   10. logs 与 data 双键容错
 *   11. refDesc/ref_desc 容错
 *   12. type/points/balance 透传
 *   [常量契约]
 *   13. 今日已签判定纯函数(记录含今日)
 *   14. 今日已签判定(记录不含今日)
 */
const fs = require('fs');
const path = require('path');
const ts = require('typescript');

const SRC = path.resolve(__dirname, '..', 'src', 'api', 'points.ts');

let passed = 0, failed = 0;
function record(name, ok, detail = '') {
  if (ok) { passed++; console.log(`  ✓ ${name}`); }
  else { failed++; console.log(`  ✗ ${name}${detail ? ` → ${detail}` : ''}`); }
}

// ---------- 编译加载 points.ts(request mock 注入) ----------
const calls = [];
global.__points_calls = calls;
global.__points_respond = {};
function mockRequest(opts) {
  global.__points_calls.push(opts);
  return Promise.resolve(global.__points_respond);
}
const { outputText } = ts.transpileModule(fs.readFileSync(SRC, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 },
});
const sandbox = { module: { exports: {} }, exports: {}, require, console };
sandbox.global = sandbox;
// 拦截 './request' 导入
const Module = require('module');
const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  if (request === './request') return path.resolve(__dirname, 'mock-request.js');
  return origResolve.call(this, request, ...args);
};
fs.writeFileSync(path.resolve(__dirname, 'mock-request.js'),
  'module.exports = { request: function(opts){ return global.__ptsMock(opts); } };');
global.__ptsMock = mockRequest;
const wrapper = new Function('module', 'exports', 'require', 'console', 'globalThis',
  `"use strict"; ${outputText}`);
wrapper(sandbox.module, sandbox.module.exports, require, console, sandbox);
const { PointsAPI } = sandbox.module.exports;

const TODAY = new Date().toISOString().slice(0, 10);

console.log('积分 API 适配层单元测试');
console.log('='.repeat(52));

(async () => {
  // ---------- signin ----------
  calls.length = 0;
  global.__points_respond = { signinId: 1, userId: 2, signDate: TODAY, continuousDays: 7,
    pointsEarned: 50, isBonus: 1, bonusPoints: 50 };
  const r1 = await PointsAPI.signin(2);
  record('signin → POST /api/points/signin',
    calls[0].url === '/api/points/signin' && calls[0].method === 'POST', JSON.stringify(calls[0]));
  record('signin → userId 进 body', calls[0].data.userId === 2);
  record('signin isBonus 1 → true', r1.isBonus === true);
  record('signin 宝箱日 bonusPoints 透传', r1.bonusPoints === 50);
  record('signin 连续天数透传', r1.continuousDays === 7);

  // ---------- signinRecords ----------
  calls.length = 0;
  global.__points_respond = { data: [{ signDate: TODAY, continuousDays: 1, pointsEarned: 10 }] };
  const r2 = await PointsAPI.signinRecords(2, 7);
  record('signinRecords → GET /api/points/signin/{uid}?limit=',
    calls[0].url === `/api/points/signin/2?limit=7` && !calls[0].method, calls[0].url);
  record('signinRecords 今日判定(含今日 → 签过)',
    r2.some(r => r.signDate === TODAY) === true);

  calls.length = 0;
  global.__points_respond = { data: [] };
  const r3 = await PointsAPI.signinRecords(2, 7);
  record('signinRecords 空记录 → []', Array.isArray(r3) && r3.length === 0);
  record('signinRecords 空记录今日判定(未签)',
    r3.some(r => r.signDate === TODAY) === false);

  // snake_case 容错
  calls.length = 0;
  global.__points_respond = { data: [{ sign_date: '2026-09-01', points_earned: 10, is_bonus: 0 }] };
  const r4 = await PointsAPI.signinRecords(2, 7);
  record('signinRecords snake_case 容错(sign_date)',
    r4[0].signDate === '2026-09-01' && r4[0].pointsEarned === 10 && r4[0].isBonus === false);

  // ---------- account ----------
  calls.length = 0;
  global.__points_respond = { totalPoints: 1200, frozenPoints: 50, totalEarned: 3000,
    totalSpent: 1800, expiringPoints: 200 };
  const r5 = await PointsAPI.account(2);
  record('account → GET /api/points/account/{uid}',
    calls[0].url === '/api/points/account/2');
  record('account 五字段映射',
    r5.totalPoints === 1200 && r5.frozenPoints === 50 && r5.totalEarned === 3000
    && r5.totalSpent === 1800 && r5.expiringPoints === 200);

  calls.length = 0;
  global.__points_respond = {};
  const r6 = await PointsAPI.account(2);
  record('account 缺省字段兜底 0',
    r6.totalPoints === 0 && r6.expiringPoints === 0);

  // ---------- logs ----------
  calls.length = 0;
  global.__points_respond = { logs: [{ id: 1, type: 'earn', source: 'signin', points: 10,
    balance: 1210, refDesc: '每日签到(连续第7天)', createdAt: TODAY + 'T08:00:00' }] };
  const r7 = await PointsAPI.logs(2, 50);
  record('logs → GET /api/points/logs/{uid}?limit=',
    calls[0].url === '/api/points/logs/2?limit=50');
  record('logs logs 键解析 + 字段透传',
    r7.length === 1 && r7[0].points === 10 && r7[0].balance === 1210
    && r7[0].type === 'earn' && r7[0].refDesc.includes('连续第7天'));

  calls.length = 0;
  global.__points_respond = { data: [{ id: 2, type: 'spend', points: 5, balance: 100,
    ref_desc: '订单抵扣' }] };
  const r8 = await PointsAPI.logs(2, 50);
  record('logs data 键容错 + ref_desc 容错',
    r8.length === 1 && r8[0].refDesc === '订单抵扣' && r8[0].type === 'spend');

  console.log('='.repeat(52));
  console.log(`结果: ${passed} 通过, ${failed} 失败`);
  // 清理 mock 文件
  fs.unlinkSync(path.resolve(__dirname, 'mock-request.js'));
  process.exit(failed > 0 ? 1 : 0);
})();
