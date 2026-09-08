/**
 * test-flash-countdown.js · 秒杀倒计时纯函数单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-domain-config.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule)真实源码
 *
 * 覆盖:
 *   [countdownParts]
 *   1.  未到点返回天/时/分/秒分解
 *   2.  已过点返回 null
 *   3.  非法时间返回 null
 *   [countdownText]
 *   4.  不足 1 天 → 纯 HH:MM:SS 补零
 *   5.  跨天 → "N天 HH:MM:SS"
 *   6.  已过点 → 00:00:00
 *   [phaseOf]
 *   7.  now < start → upcoming
 *   8.  start <= now <= end → inProgress
 *   9.  now > end → ended
 *   10. 非法时间 → unknown
 *   [buyButtonState]
 *   11. upcoming → 未开始禁用
 *   12. ended → 已结束禁用
 *   13. unknown → 暂不可购禁用
 *   14. inProgress + 有库存 → 马上抢可用
 *   15. inProgress + 零库存 → 已售罄禁用
 *   16. 边界: start 时刻 → inProgress(闭区间)
 */
const fs = require('fs');
const path = require('path');
const ts = require('typescript');

const SRC = path.resolve(__dirname, '..', 'src', 'pages', 'flashsale', 'countdown.ts');

let passed = 0, failed = 0;
function record(name, ok, detail = '') {
  if (ok) { passed++; console.log(`  ✓ ${name}`); }
  else { failed++; console.log(`  ✗ ${name}${detail ? ` → ${detail}` : ''}`); }
}

const { outputText } = ts.transpileModule(fs.readFileSync(SRC, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 },
});
const sandbox = { module: { exports: {} }, exports: {}, require, console };
sandbox.global = sandbox;
const wrapper = new Function('module', 'exports', 'require', 'console', 'globalThis',
  `"use strict"; ${outputText}`);
wrapper(sandbox.module, sandbox.module.exports, require, console, sandbox);
const { countdownParts, countdownText, phaseOf, buyButtonState } = sandbox.module.exports;

console.log('秒杀倒计时单元测试');
console.log('='.repeat(52));

const NOW = new Date('2026-09-08T12:00:00+08:00');

// ---------- countdownParts ----------
const p1 = countdownParts('2026-09-08T15:30:45+08:00', NOW);
record('未到点分解(3h30m45s)',
  p1.hours === 3 && p1.minutes === 30 && p1.seconds === 45, JSON.stringify(p1));
record('已过点返回 null', countdownParts('2026-09-08T10:00:00+08:00', NOW) === null);
record('非法时间返回 null', countdownParts('not-a-date', NOW) === null);
const p1d = countdownParts('2026-09-10T12:00:30+08:00', NOW);
record('跨天分解(2天30s)', p1d.days === 2 && p1d.seconds === 30, JSON.stringify(p1d));

// ---------- countdownText ----------
record('不足1天 → HH:MM:SS 补零',
  countdownText('2026-09-08T15:05:09+08:00', NOW) === '03:05:09',
  countdownText('2026-09-08T15:05:09+08:00', NOW));
record('跨天 → N天 HH:MM:SS',
  countdownText('2026-09-10T12:00:00+08:00', NOW) === '2天 00:00:00',
  countdownText('2026-09-10T12:00:00+08:00', NOW));
record('已过点 → 00:00:00',
  countdownText('2026-09-08T10:00:00+08:00', NOW) === '00:00:00');

// ---------- phaseOf ----------
const START = '2026-09-08T14:00:00+08:00';
const END = '2026-09-08T18:00:00+08:00';
record('now < start → upcoming', phaseOf(START, END, NOW) === 'upcoming');
record('区间内 → inProgress', phaseOf(START, END, new Date('2026-09-08T16:00:00+08:00')) === 'inProgress');
record('now > end → ended', phaseOf(START, END, new Date('2026-09-08T19:00:00+08:00')) === 'ended');
record('非法时间 → unknown', phaseOf('bad', END, NOW) === 'unknown');
record('边界: start 时刻 → inProgress(闭区间)',
  phaseOf(START, END, new Date('2026-09-08T14:00:00+08:00')) === 'inProgress');

// ---------- buyButtonState ----------
record('upcoming → 未开始禁用',
  buyButtonState('upcoming', 100).label === '未开始' && buyButtonState('upcoming', 100).disabled);
record('ended → 已结束禁用',
  buyButtonState('ended', 100).label === '已结束' && buyButtonState('ended', 100).disabled);
record('unknown → 暂不可购禁用',
  buyButtonState('unknown', 100).label === '暂不可购' && buyButtonState('unknown', 100).disabled);
record('inProgress+库存 → 马上抢可用',
  buyButtonState('inProgress', 5).label === '马上抢' && !buyButtonState('inProgress', 5).disabled);
record('inProgress+零库存 → 已售罄禁用',
  buyButtonState('inProgress', 0).label === '已售罄' && buyButtonState('inProgress', 0).disabled);

console.log('='.repeat(52));
console.log(`结果: ${passed} 通过, ${failed} 失败`);
process.exit(failed > 0 ? 1 : 0);
