/**
 * test-trace-scan.js · 溯源验真页扫码查询单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-navbar.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule)真实源码
 *       + Module._load 拦截 mock react/Taro/api/scss
 *
 * 覆盖(扫码查询功能——含 H5 降级修复与 weapp fail 回调修复):
 *   [扫码入口]
 *   1.  H5 端扫码降级: toast 引导手动输入, 不调用 scanCode、零 API
 *   2.  weapp 扫码成功·批次号 → publicTrace
 *   3.  weapp 扫码成功·瓶码(BLC) → publicTraceByCode + 回填输入态
 *   4.  weapp 扫码码值自动去空格
 *   5.  weapp 扫码工段打卡码(ZXBJ-TRACE) → 拒绝 toast + 零 API
 *   6.  weapp 扫码失败(fail 回调) → toast + console.error 留痕
 *   7.  weapp 扫码空内容 → 空输入引导 toast
 *   [手动查询]
 *   8.  空输入 → toast + 零 API
 *   9.  批次号 → publicTrace + result 落态 + loading 复位
 *   10. 瓶码 BLC- / 箱顶码 TBC- / 箱底码 BBC- → publicTraceByCode
 *   11. 普通批次号不误判 → publicTrace
 *   12. 输入去空格后作为 API 参数
 *   13. API 失败 → 错误 toast + result 置空
 *   [输入绑定]
 *   14. 输入框 onInput 双向绑定
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const SRC = path.resolve(__dirname, '..', 'src', 'pages', 'trace-view', 'index.tsx');

// ============================================================
// 1. Mock 上下文(调用记录 + 可配置行为)
// ============================================================
const toasts = [];                      // Taro.showToast 文案记录
const scanCalls = [];                   // Taro.scanCode 调用记录
const apiCalls = { publicTrace: [], publicTraceByCode: [] };
const errorLogs = [];                   // console.error 拦截记录
let scanBehavior = null;                // (opts) => void: 触发 success/fail
let apiMode = 'ok';                     // ok | error

const FIXTURE_BATCH = {
  batchNo: 'ZX52-2026L08', status: 'released', currentStageSeq: 7,
  timeline: [], chainValid: true, health: { score: 100, factors: {} },
};
const FIXTURE_LIFE = {
  ...FIXTURE_BATCH, code: 'BLC-ZX52L08-0001', codeType: 'life',
  lifeStatus: 'pending', prodReleased: true,
};

const mockTaro = {
  showToast(o) { toasts.push((o && o.title) || ''); },
  scanCode(opts) {
    scanCalls.push(opts);
    if (scanBehavior) scanBehavior(opts);
  },
};

const TraceProdAPI = {
  async publicTrace(no) {
    apiCalls.publicTrace.push(no);
    if (apiMode === 'error') throw new Error('批次不存在: ' + no);
    return FIXTURE_BATCH;
  },
  async publicTraceByCode(code) {
    apiCalls.publicTraceByCode.push(code);
    if (apiMode === 'error') throw new Error('瓶码不存在: ' + code);
    return FIXTURE_LIFE;
  },
};

// ============================================================
// 2. 极简 React(元素树 + useState 槽位 mock)
// ============================================================
function createElement(type, props, ...children) {
  return {
    type,
    props: props || {},
    children: children.filter(c => c !== null && c !== undefined
      && c !== false && c !== true),
  };
}

// useState mock: 按调用顺序落槽; setter 直写槽(供渲染后断言)
let stateSlots = [];
let stateIndex = 0;
function mockUseState(initial) {
  const idx = stateIndex++;
  if (stateSlots[idx] === undefined) {
    stateSlots[idx] = typeof initial === 'function' ? initial() : initial;
  }
  return [stateSlots[idx], (v) => {
    stateSlots[idx] = typeof v === 'function'
      ? v(stateSlots[idx]) : v;
  }];
}

const mockReact = {
  createElement, Fragment: 'Fragment', useState: mockUseState,
};

// CSS Modules Proxy: 任意类名 → {name}-cls
const mockStyles = new Proxy({}, {
  get: (t, k) => (typeof k === 'string' ? `${k}-cls` : undefined),
});

const MOCKS = {
  react: {
    __esModule: true, default: mockReact,
    createElement, Fragment: 'Fragment', useState: mockUseState,
  },
  '@tarojs/components': {
    __esModule: true, View: 'View', Text: 'Text', Input: 'Input',
  },
  '@tarojs/taro': { __esModule: true, default: mockTaro },
  './index.module.scss': { __esModule: true, default: mockStyles },
  '@/components/NavBar': {
    __esModule: true, default: () => null,
  },
  '@/components/ScanCode': {
    __esModule: true, default: () => null,
  },
  '@/api/traceProd': {
    __esModule: true, TraceProdAPI, PublicTraceVO: undefined,
  },
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  return origLoad.apply(this, arguments);
};

// ============================================================
// 3. 内存编译加载真实组件源码
// ============================================================
const source = fs.readFileSync(SRC, 'utf-8');
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.React,
    target: ts.ScriptTarget.ES2019,
    esModuleInterop: true,
  },
  fileName: SRC,
});
const compiledFile = path.join(os.tmpdir(),
  `trace-view-compiled-${process.pid}.js`);
fs.writeFileSync(compiledFile, compiled.outputText);
const TraceViewPage = require(compiledFile).default;

// ============================================================
// 4. 断言工具
// ============================================================
function findAll(node, predicate, acc = []) {
  if (!node || typeof node !== 'object') return acc;
  if (predicate(node)) acc.push(node);
  for (const child of node.children || []) {
    if (child && typeof child === 'object') findAll(child, predicate, acc);
  }
  return acc;
}
const findByClass = (root, cls) =>
  findAll(root, n => n.props.className === cls);

/** 渲染一次页面(重置 hook 序号; stateSlots 保留以便预设/断言) */
function renderPage() {
  stateIndex = 0;
  return TraceViewPage({});
}

/** 刷新微任务链(doQuery 为 async) */
const flush = () => new Promise(r => setTimeout(r, 20));

function reset() {
  toasts.length = 0;
  scanCalls.length = 0;
  apiCalls.publicTrace.length = 0;
  apiCalls.publicTraceByCode.length = 0;
  errorLogs.length = 0;
  scanBehavior = null;
  apiMode = 'ok';
  stateSlots = [];
  stateIndex = 0;
}

// ============================================================
// 5. 用例执行
// ============================================================
const results = [];
const record = (name, ok, detail = '') => {
  results.push({ name, ok, detail });
  console.log(`  ${ok ? '✓' : '✗'} ${name}${ok ? '' : ` — ${detail}`}`);
};

(async () => {
  console.log('='.repeat(60));
  console.log('溯源验真页扫码查询 · 单元测试');
  console.log('='.repeat(60));

  // ---------- [1] H5 扫码打开扫码组件(替代旧 toast 降级) ----------
  reset();
  process.env.TARO_ENV = 'h5';
  let el = renderPage();
  let scanBtn = findByClass(el, 'ghostBtn-cls')[0];
  scanBtn.props.onClick();
  record('H5 扫码打开扫码组件(scanVisible 态)',
    stateSlots[3] === true, `slot3=${JSON.stringify(stateSlots[3])}`);
  record('H5 扫码不调用原生 scanCode',
    scanCalls.length === 0);
  record('H5 扫码零 API 调用',
    apiCalls.publicTrace.length === 0
    && apiCalls.publicTraceByCode.length === 0);

  // ---------- [2] weapp 扫码成功·批次号 ----------
  reset();
  process.env.TARO_ENV = 'weapp';
  el = renderPage();
  scanBtn = findByClass(el, 'ghostBtn-cls')[0];
  scanBehavior = o => o.success({ result: 'ZX52-2026L08' });
  scanBtn.props.onClick();
  await flush();
  record('weapp 扫码成功·批次号 → publicTrace',
    scanCalls.length === 1
    && apiCalls.publicTrace.length === 1
    && apiCalls.publicTrace[0] === 'ZX52-2026L08'
    && apiCalls.publicTraceByCode.length === 0,
    `trace=${JSON.stringify(apiCalls.publicTrace)}`);
  record('weapp 扫码成功·result 落态',
    stateSlots[1] === FIXTURE_BATCH);

  // ---------- [3] weapp 扫码成功·瓶码 ----------
  reset();
  el = renderPage();
  scanBtn = findByClass(el, 'ghostBtn-cls')[0];
  scanBehavior = o => o.success({ result: '  BLC-ZX52L08-0001  ' });
  scanBtn.props.onClick();
  await flush();
  record('weapp 扫码成功·瓶码(BLC) → publicTraceByCode',
    apiCalls.publicTraceByCode.length === 1
    && apiCalls.publicTraceByCode[0] === 'BLC-ZX52L08-0001'
    && apiCalls.publicTrace.length === 0,
    `byCode=${JSON.stringify(apiCalls.publicTraceByCode)}`);
  record('weapp 扫码码值去空格后回填输入态',
    stateSlots[0] === 'BLC-ZX52L08-0001',
    `slot0=${JSON.stringify(stateSlots[0])}`);

  // ---------- [5] weapp 扫码工段打卡码 ----------
  reset();
  el = renderPage();
  scanBtn = findByClass(el, 'ghostBtn-cls')[0];
  scanBehavior = o => o.success({ result: 'ZXBJ-TRACE:STG-BREW:v1' });
  scanBtn.props.onClick();
  await flush();
  record('weapp 扫码工段打卡码 → 拒绝 toast',
    toasts.length === 1
    && toasts[0] === '这是工段打卡码, 请扫瓶身溯源码');
  record('weapp 工段打卡码零 API 调用',
    apiCalls.publicTrace.length === 0
    && apiCalls.publicTraceByCode.length === 0);

  // ---------- [6] weapp 扫码失败 ----------
  reset();
  el = renderPage();
  scanBtn = findByClass(el, 'ghostBtn-cls')[0];
  scanBehavior = o => o.fail({ errMsg: 'scanCode:fail cancel' });
  const origError = console.error;
  console.error = (...args) => errorLogs.push(args.join(' '));
  try { scanBtn.props.onClick(); } finally { console.error = origError; }
  await flush();
  record('weapp 扫码失败 → toast 提示',
    toasts.length === 1
    && toasts[0] === '扫码取消或失败, 请手动输入');
  record('weapp 扫码失败 → console.error 留痕',
    errorLogs.length === 1
    && errorLogs[0].startsWith('[trace-view] scanCode failed:'),
    `log=${JSON.stringify(errorLogs)}`);
  record('weapp 扫码失败零 API 调用',
    apiCalls.publicTrace.length === 0
    && apiCalls.publicTraceByCode.length === 0);

  // ---------- [7] weapp 扫码空内容 ----------
  reset();
  el = renderPage();
  scanBtn = findByClass(el, 'ghostBtn-cls')[0];
  scanBehavior = o => o.success({ result: '' });
  scanBtn.props.onClick();
  await flush();
  record('weapp 扫码空内容 → 空输入引导 toast',
    toasts.length === 1 && toasts[0] === '请输入批次号或瓶身码'
    && apiCalls.publicTrace.length === 0);

  // ---------- [8] 手动查询·空输入 ----------
  reset();
  el = renderPage();
  const queryBtn = findByClass(el, 'primaryBtn-cls')[0];
  queryBtn.props.onClick();
  await flush();
  record('手动查询空输入 → toast + 零 API',
    toasts.length === 1 && toasts[0] === '请输入批次号或瓶身码'
    && apiCalls.publicTrace.length === 0
    && apiCalls.publicTraceByCode.length === 0);

  // ---------- [9] 手动查询·批次号 ----------
  reset();
  stateSlots = ['ZX52-2026L08'];  // 预设 batchNo 输入态
  el = renderPage();
  const queryBtn2 = findByClass(el, 'primaryBtn-cls')[0];
  queryBtn2.props.onClick();
  await flush();
  record('手动查询批次号 → publicTrace',
    apiCalls.publicTrace.length === 1
    && apiCalls.publicTrace[0] === 'ZX52-2026L08'
    && apiCalls.publicTraceByCode.length === 0);
  record('手动查询成功 result 落态',
    stateSlots[1] === FIXTURE_BATCH);
  record('查询结束 loading 复位 false',
    stateSlots[2] === false, `slot2=${JSON.stringify(stateSlots[2])}`);

  // ---------- [10] 流通码三前缀路由 ----------
  for (const code of ['BLC-ZX52L08-0001', 'TBC-ZX52L08-0001', 'BBC-ZX52L08-0001']) {
    reset();
    stateSlots = [code];
    el = renderPage();
    const btn = findByClass(el, 'primaryBtn-cls')[0];
    btn.props.onClick();
    await flush();
    record(`流通码路由·${code.slice(0, 3)} → publicTraceByCode`,
      apiCalls.publicTraceByCode.length === 1
      && apiCalls.publicTraceByCode[0] === code
      && apiCalls.publicTrace.length === 0,
      `byCode=${JSON.stringify(apiCalls.publicTraceByCode)}`);
  }

  // ---------- [11] 普通批次号不误判 ----------
  reset();
  stateSlots = ['ZX42-2026B01'];
  el = renderPage();
  const btn11 = findByClass(el, 'primaryBtn-cls')[0];
  btn11.props.onClick();
  await flush();
  record('普通批次号不误判 → publicTrace',
    apiCalls.publicTrace.length === 1
    && apiCalls.publicTraceByCode.length === 0);

  // ---------- [12] 输入去空格 ----------
  reset();
  stateSlots = ['  ZX52-2026L08  '];
  el = renderPage();
  const btn12 = findByClass(el, 'primaryBtn-cls')[0];
  btn12.props.onClick();
  await flush();
  record('输入去空格后作为 API 参数',
    apiCalls.publicTrace.length === 1
    && apiCalls.publicTrace[0] === 'ZX52-2026L08',
    `param=${JSON.stringify(apiCalls.publicTrace)}`);

  // ---------- [13] API 失败 ----------
  reset();
  apiMode = 'error';
  stateSlots = ['XXX-BAD-000'];
  el = renderPage();
  const btn13 = findByClass(el, 'primaryBtn-cls')[0];
  btn13.props.onClick();
  await flush();
  record('API 失败 → 错误 toast',
    toasts.length === 1
    && toasts[0] === '批次不存在: XXX-BAD-000',
    `toasts=${JSON.stringify(toasts)}`);
  record('API 失败 → result 置空',
    stateSlots[1] === null, `slot1=${JSON.stringify(stateSlots[1])}`);

  // ---------- [14] 输入框双向绑定 ----------
  reset();
  el = renderPage();
  const inputEl = findByClass(el, 'input-cls')[0];
  inputEl.props.onInput({ detail: { value: 'ZX52-2026L08' } });
  record('输入框 onInput → batchNo 态更新',
    stateSlots[0] === 'ZX52-2026L08',
    `slot0=${JSON.stringify(stateSlots[0])}`);

  // ============================================================
  // 汇总
  // ============================================================
  const pass = results.filter(r => r.ok).length;
  console.log('='.repeat(60));
  console.log(`扫码查询单元测试: ${pass}/${results.length} PASS`);
  console.log('='.repeat(60));
  if (pass !== results.length) {
    results.filter(r => !r.ok)
      .forEach(r => console.log(`  ✗ ${r.name} — ${r.detail}`));
    process.exitCode = 1;
  }
})();
