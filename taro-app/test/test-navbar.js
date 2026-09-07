/**
 * test-navbar.js · NavBar 通用导航栏组件单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-checkout-*.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [渲染层]
 *   1. weapp 端零渲染(原生导航栏自带返回, 避免双栏)
 *   2. h5 端元素树七要素(wrapper/spacer/navbar/backBtn/arrow/backText/title)
 *   3. 标题动态传递(不同 title 正确落到 title 节点)
 *   4. 返回按钮文案("返回")
 *   5. hoverClass active 态类名传递
 *   6. 纯函数性: 同入参多次调用结构一致(确定性口径)
 *   [行为层]
 *   7. 栈>1 → navigateBack(回上一页)
 *   8. 栈=1 直达 → switchTab 兜底回首页(刷新场景)
 *   9. 深栈(3 页)同样 navigateBack
 *   10. 连续多次点击健壮性(无异常/计数幂等)
 *   11. weapp 端重复调用持续 null(稳定性)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const SRC = path.resolve(__dirname, '..', 'src', 'components', 'NavBar', 'index.tsx');

// ============================================================
// 1. Mock 上下文(Taro 调用记录 + 可配置页面栈深度)
// ============================================================
const calls = { navigateBack: 0, switchTab: [] };
let pageStackDepth = 1; // 当前页面栈深度(可按用例切换)

const mockTaro = {
  navigateBack() { calls.navigateBack += 1; },
  switchTab(opts) { calls.switchTab.push(opts && opts.url); },
  getCurrentPages() { return new Array(pageStackDepth); },
};

// CSS Modules 运行时样式对象 mock(固定类名, 供结构断言)
const mockStyles = {
  wrapper: 'wrapper-cls', spacer: 'spacer-cls', navbar: 'navbar-cls',
  backBtn: 'backBtn-cls', backBtnActive: 'backBtnActive-cls',
  arrow: 'arrow-cls', backText: 'backText-cls', title: 'title-cls',
};

// 极简 React.createElement(返回普通元素树, 供遍历断言)
function createElement(type, props, ...children) {
  return {
    type,
    props: props || {},
    children: children.filter(c => c !== null && c !== undefined && c !== false && c !== true),
  };
}
const mockReact = { createElement };

const MOCKS = {
  react: Object.assign(Object.create(null), {
    __esModule: true, default: mockReact, createElement,
  }),
  '@tarojs/components': Object.assign(Object.create(null), {
    __esModule: true, View: 'View', Text: 'Text',
  }),
  '@tarojs/taro': Object.assign(Object.create(null), {
    __esModule: true, default: mockTaro,
  }),
  './index.module.scss': mockStyles,
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  return origLoad.apply(this, arguments);
};

// ============================================================
// 2. 内存编译 TSX → CommonJS, 加载真实组件源码
//    (测的是 src/components/NavBar/index.tsx 本体, 非复制体)
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
const compiledFile = path.join(os.tmpdir(), `navbar-compiled-${process.pid}.js`);
fs.writeFileSync(compiledFile, compiled.outputText);
const NavBar = require(compiledFile).default;

// ============================================================
// 3. 元素树断言工具
// ============================================================
function findAll(node, predicate, acc = []) {
  if (!node || typeof node !== 'object') return acc;
  if (predicate(node)) acc.push(node);
  for (const child of node.children || []) {
    if (child && typeof child === 'object') findAll(child, predicate, acc);
  }
  return acc;
}

const findByClass = (root, cls) => findAll(root, n => n.props.className === cls);

function textOf(node) {
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (node && typeof node === 'object') return (node.children || []).map(textOf).join('');
  return '';
}

const treeDepth = (node) => (node && typeof node === 'object'
  ? 1 + Math.max(0, ...(node.children || []).map(treeDepth)) : 0);

// ============================================================
// 4. 用例执行
// ============================================================
const results = [];
const record = (name, ok, detail = '') => {
  results.push({ name, ok, detail });
  console.log(`  ${ok ? '✓' : '✗'} ${name}${ok ? '' : ` — ${detail}`}`);
};
const resetCalls = () => { calls.navigateBack = 0; calls.switchTab = []; };

console.log('='.repeat(60));
console.log('NavBar 通用导航栏组件 · 单元测试');
console.log('='.repeat(60));

// ---------- [1] weapp 端零渲染 ----------
process.env.TARO_ENV = 'weapp';
record('weapp 端零渲染(原生导航栏自带返回)',
  NavBar({ title: '钱包' }) === null);

// ---------- [2] h5 端元素树七要素 ----------
process.env.TARO_ENV = 'h5';
const el = NavBar({ title: '钱包' });
const zoneChecks = [
  ['wrapper 根容器', 'wrapper-cls'],
  ['spacer 占位(防内容遮挡)', 'spacer-cls'],
  ['navbar 导航主体', 'navbar-cls'],
  ['backBtn 返回按钮', 'backBtn-cls'],
  ['arrow 返回箭头', 'arrow-cls'],
  ['backText 返回文案', 'backText-cls'],
  ['title 居中标题', 'title-cls'],
];
for (const [label, cls] of zoneChecks) {
  const hits = findByClass(el, cls);
  record(`h5 端要素·${label}`,
    hits.length === 1, `命中 ${hits.length} 处`);
}
record('h5 端根节点类型为 View 容器', el.type === 'View');

// ---------- [3] 标题动态传递 ----------
for (const t of ['商品详情', '我的订单', '溯源验真']) {
  const e = NavBar({ title: t });
  const titleEl = findByClass(e, 'title-cls')[0];
  record(`标题动态传递·"${t}"`,
    textOf(titleEl) === t, `实际 "${textOf(titleEl)}"`);
}

// ---------- [4] 返回按钮文案 ----------
record('返回按钮文案为"返回"',
  textOf(findByClass(el, 'backText-cls')[0]) === '返回');

// ---------- [5] hoverClass active 态传递 ----------
record('hoverClass 按压态类名传递',
  findByClass(el, 'backBtn-cls')[0].props.hoverClass === 'backBtnActive-cls');

// ---------- [6] 纯函数性(同入参同输出) ----------
const el2 = NavBar({ title: '钱包' });
record('纯函数性·同入参两次调用结构一致',
  treeDepth(el) === treeDepth(el2)
  && findByClass(el, 'backBtn-cls').length === findByClass(el2, 'backBtn-cls').length);

// ---------- [7] 栈>1 → navigateBack ----------
resetCalls();
pageStackDepth = 2;
findByClass(el, 'backBtn-cls')[0].props.onClick();
record('栈>1 点击返回 → navigateBack 回上一页',
  calls.navigateBack === 1 && calls.switchTab.length === 0,
  `back=${calls.navigateBack}, switchTab=${JSON.stringify(calls.switchTab)}`);

// ---------- [8] 栈=1 直达 → switchTab 兜底首页 ----------
resetCalls();
pageStackDepth = 1;
findByClass(el, 'backBtn-cls')[0].props.onClick();
record('栈=1 直达点击返回 → switchTab 兜底回首页',
  calls.switchTab.length === 1 && calls.switchTab[0] === '/pages/index/index'
  && calls.navigateBack === 0,
  `switchTab=${JSON.stringify(calls.switchTab)}, back=${calls.navigateBack}`);

// ---------- [9] 深栈(3 页)同样 navigateBack ----------
resetCalls();
pageStackDepth = 3;
findByClass(el, 'backBtn-cls')[0].props.onClick();
record('深栈(3 页)点击返回 → navigateBack',
  calls.navigateBack === 1 && calls.switchTab.length === 0);

// ---------- [10] 连续点击健壮性 ----------
resetCalls();
pageStackDepth = 1;
let noThrow = true;
try {
  for (let i = 0; i < 3; i++) findByClass(el, 'backBtn-cls')[0].props.onClick();
} catch (e) { noThrow = false; }
record('连续 3 次点击无异常且计数幂等',
  noThrow && calls.switchTab.length === 3 && calls.navigateBack === 0,
  `switchTab=${calls.switchTab.length}`);

// ---------- [11] weapp 端重复调用持续 null ----------
process.env.TARO_ENV = 'weapp';
let stableNull = true;
for (let i = 0; i < 5; i++) {
  if (NavBar({ title: 'x' }) !== null) stableNull = false;
}
record('weapp 端重复调用持续 null(稳定性)', stableNull);

// ============================================================
// 5. 汇总
// ============================================================
const pass = results.filter(r => r.ok).length;
console.log('='.repeat(60));
console.log(`NavBar 单元测试: ${pass}/${results.length} PASS`);
console.log('='.repeat(60));
if (pass !== results.length) {
  results.filter(r => !r.ok).forEach(r => console.log(`  ✗ ${r.name} — ${r.detail}`));
  process.exitCode = 1;
}
