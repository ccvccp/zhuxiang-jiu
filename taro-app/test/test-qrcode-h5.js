/**
 * test-qrcode-h5.js · 二维码 H5 渲染修复 单元测试
 * ============================================================
 * 背景: 旧版 Taro.createCanvasContext 为小程序 API, 在 H5(尤其
 * 电脑浏览器)下 ctx.draw() 静默失败 → 「扫码赚钱」推广码/工段码
 * 画布空白。修复: H5 走离屏 2d canvas → PNG data URL(Image 渲染)。
 *
 * 范式: 纯 Node 脚本(对齐 test-qr.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [工具层 utils/qrcode.ts]
 *   1. qrMatrix 矩阵生成(方阵/确定性/三定位图案暗模块)
 *   2. renderQrMatrix 绘制序列(白底先行/黑模块着墨/fillStyle 兼容轨)
 *   3. qrMatrixToDataUrl(H5 离屏: 画布尺寸/2d 上下文/data URL 返回)
 *   [页面层 pages/promotion — H5 分支]
 *   4. H5 档渲染 Image(data URL)而非 Canvas(旧版画布)
 *   5. 已有推广码自动出图(myCodes → wechat_miniprogram 渠道码)
 *   6. 无码时领取引导(claimCard 兜底)
 *   [页面层 pages/promotion — weapp 分支(零回归)]
 *   7. weapp 档仍走旧版 Canvas(canvasId=promoQr)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const QR_SRC = path.resolve(__dirname, '..', 'src', 'utils', 'qrcode.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'promotion', 'index.tsx');

// ============================================================
// 1. document 离屏 canvas mock(H5 环境模拟)
// ============================================================
let lastCanvas = null;
function makeCanvasStub() {
  const calls = [];
  const ctx = {
    _fillStyles: [],
    set fillStyle(v) { this._fillStyles.push(v); },
    get fillStyle() { return this._fillStyles[this._fillStyles.length - 1]; },
    fillRect(x, y, w, h) { calls.push({ x, y, w, h }); },
  };
  return {
    width: 0, height: 0, _calls: calls,
    getContext(kind) { return kind === '2d' ? ctx : null; },
    toDataURL() { return 'data:image/png;base64,TESTQR'; },
  };
}
global.document = {
  createElement(tag) {
    if (tag === 'canvas') { lastCanvas = makeCanvasStub(); return lastCanvas; }
    return {};
  },
};

// ============================================================
// 2. Mock 上下文(推广页依赖——直返解包数据, 对齐 request 解包口径)
// ============================================================
let WITH_CODE = true;

// 极简 React hooks 运行时(仅 useState/useEffect 同步路径)
function miniReact() {
  let hookIdx = 0;
  let renderFn = null;
  const states = [];
  const dirty = { v: false };
  const setStateAt = (i) => (v) => {
    states[i] = typeof v === 'function' ? v(states[i]) : v;
    dirty.v = true;
  };
  const effects = [];
  const api = {
    useState: (init) => {
      const i = hookIdx++;
      if (!(i in states)) states[i] = init;
      return [states[i], setStateAt(i)];
    },
    useEffect: (fn) => { effects.push(fn); },
    createElement,
  };
  api.__test = {
    effects, states, dirty,
    setRenderer(fn) { renderFn = fn; },
    rerender() { hookIdx = 0; return renderFn({}); },
    resetIdx() { hookIdx = 0; },
  };
  api.default = api;
  return Object.assign(Object.create(null), {
    __esModule: true, default: api, ...api,
  });
}

async function renderFlushed(Comp, react, maxRounds = 8) {
  const t = react.__test || react.default.__test;
  const settle = () => new Promise(r => setTimeout(r, 20));
  let out = null;
  for (let round = 0; round < maxRounds; round++) {
    t.resetIdx();
    t.effects.length = 0;
    out = Comp({});
    t.setRenderer(Comp);
    const fns = t.effects.splice(0);
    for (const fn of fns) {
      try { await fn(); } catch (_) { /* effect 内部失败忽略 */ }
    }
    await settle();
    if (!t.dirty.v) break;
    t.dirty.v = false;
  }
  return out;
}

const mockStyles = {
  __esModule: true,
  default: new Proxy({}, { get: (_, k) => String(k) }),
};
const mockTaro = {
  showToast: () => {}, setClipboardData: () => {},
  nextTick: (fn) => fn(),
  getSystemInfoSync: () => ({ windowWidth: 375 }),
  createCanvasContext: () => ({
    setFillStyle: () => {}, fillRect: () => {}, draw: () => {},
  }),
};
function createElement(type, props, ...children) {
  return { type, props: props || {}, children: children.flat().filter(c => c != null && c !== false && c !== true) };
}
const reactRef = { current: miniReact() };
const componentsMock = Object.assign(Object.create(null), {
  __esModule: true, View: 'View', Text: 'Text',
  Canvas: 'Canvas', Image: 'Image',
});
const taroMock = Object.assign(Object.create(null), {
  __esModule: true, default: mockTaro, ...mockTaro,
});
const MOCKS = {
  react: reactRef.current,
  '@tarojs/components': componentsMock,
  '@tarojs/taro': taroMock,
  '@/api/promotion': { PromoAPI: {
    stats: () => Promise.resolve({
      directCount: 3, qualifiedSubCount: 1,
      level1Threshold: 10, level1RewardAmount: 20,
      level2SubPromoterCount: 6, level2SubThreshold: 5,
      level2RewardAmount: 15, wineMinPrice: 200,
      rewardBalance: 60, wineQualifyAvailable: 1,
      walletRewardCycles: 1,
    }),
    myCodes: () => Promise.resolve(WITH_CODE
      ? [{ code: 'ZXBJTEST01', channel: 'wechat_miniprogram', boundCount: 0 },
         { code: 'DY0001', channel: 'douyin', boundCount: 0 }]
      : []),
    myTeam: () => Promise.resolve([]),
    myRewards: () => Promise.resolve([]),
    claimCode: () => Promise.resolve({
      code: 'ZXBJTEST01', shareTip: '喝竹香酒, 扫码立得优惠',
      reclaimed: false,
    }),
  } },
  './index.module.scss': mockStyles,
  '@/components/NavBar': { __esModule: true, default: () => null },
};

const origLoad = Module._load;
let utilMod = null;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request === '@/utils/qrcode') return utilMod;
  return origLoad.apply(this, arguments);
};

function compileLoad(srcPath, label) {
  const source = fs.readFileSync(srcPath, 'utf-8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.React,
      target: ts.ScriptTarget.ES2019,
      esModuleInterop: true,
    },
    fileName: srcPath,
  });
  const out = path.join(os.tmpdir(), `qrh5-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

// ============================================================
// 3. 断言
// ============================================================
const results = [];
const record = (name, ok, detail = '') => {
  results.push({ name, ok });
  console.log(`  ${ok ? '✓' : '✗'} ${name}${ok ? '' : ` — ${detail}`}`);
};
function findAll(node, predicate, acc = []) {
  if (!node || typeof node !== 'object') return acc;
  if (predicate(node)) acc.push(node);
  for (const child of node.children || []) {
    if (child && typeof child === 'object') findAll(child, predicate, acc);
  }
  return acc;
}

(async () => {
  console.log('='.repeat(60));
  console.log('二维码 H5 渲染修复 · 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1] qrMatrix 矩阵生成 ----------
  const qr = compileLoad(QR_SRC, 'util');
  utilMod = qr;
  const CODE = 'ZXBJTEST01';
  const m1 = qr.qrMatrix(CODE);
  const m2 = qr.qrMatrix(CODE);
  const n = m1.length;
  record('矩阵-方阵且尺寸合法', n === m1[0].length
    && [21, 25, 29, 33, 37, 41, 45].includes(n), `n=${n}`);
  record('矩阵-确定性(同输入同输出)',
    JSON.stringify(m1) === JSON.stringify(m2));
  record('矩阵-三定位图案暗模块',
    m1[3][3] === true && m1[n - 4][3] === true && m1[3][n - 4] === true);
  record('矩阵-内容变化矩阵变化',
    JSON.stringify(qr.qrMatrix(CODE + 'X')) !== JSON.stringify(m1));

  // ---------- [2] renderQrMatrix 绘制序列 ----------
  const cv = makeCanvasStub();
  const ctx2d = cv.getContext('2d');
  qr.renderQrMatrix(ctx2d, m1, 480);
  const rects = cv._calls;
  record('绘制-白底先行', rects.length > 1
    && rects[0].x === 0 && rects[0].y === 0
    && rects[0].w === 480 && rects[0].h === 480
    && ctx2d._fillStyles[0] === '#ffffff');
  const darkCount = rects.length - 1;
  record('绘制-黑模块着墨(>0 且 < 半数)',
    darkCount > 0 && darkCount < (n * n) / 2, `dark=${darkCount}`);
  record('绘制-fillStyle 兼容轨(字符串赋值)',
    typeof ctx2d._fillStyles[1] === 'string'
    && ctx2d._fillStyles[1] === '#000000');

  // ---------- [3] qrMatrixToDataUrl(H5 离屏) ----------
  const url = qr.qrMatrixToDataUrl(m1, 480);
  record('H5离屏-data URL 返回',
    url === 'data:image/png;base64,TESTQR', `url=${url}`);
  record('H5离屏-画布尺寸正确',
    lastCanvas.width === 480 && lastCanvas.height === 480,
    `w=${lastCanvas.width}`);
  record('H5离屏-绘制经 2d 上下文',
    lastCanvas._calls.length === rects.length);

  // ---------- [4] 推广页 H5 分支 ----------
  process.env.TARO_ENV = 'h5';
  WITH_CODE = true;
  const reactH5 = miniReact();
  Object.assign(MOCKS.react, reactH5, { default: reactH5 });
  const pageH5 = compileLoad(PAGE_SRC, 'page-h5');
  const elH5 = await renderFlushed(pageH5.default, reactH5);
  const flatH5 = JSON.stringify(elH5);
  record('页面H5-Image 渲染(data URL)',
    flatH5.includes('"data:image/png;base64,TESTQR"'), flatH5.slice(0, 120));
  const canvasNodes = findAll(elH5, x => x.type === 'Canvas');
  record('页面H5-旧版 Canvas 不出现', canvasNodes.length === 0,
    `canvas=${canvasNodes.length}`);
  const imgNodes = findAll(elH5, x => x.type === 'Image');
  record('页面H5-Image 节点唯一',
    imgNodes.length === 1 && imgNodes[0].props.src
      === 'data:image/png;base64,TESTQR');
  record('页面H5-推广码展示', flatH5.includes('ZXBJTEST01'));

  // ---------- [5] 无码兜底(领取引导) ----------
  WITH_CODE = false;
  const reactH5b = miniReact();
  Object.assign(MOCKS.react, reactH5b, { default: reactH5b });
  const elH5b = await renderFlushed(pageH5.default, reactH5b);
  const flatH5b = JSON.stringify(elH5b);
  record('页面H5-无码领取引导',
    flatH5b.includes('领取推广二维码')
    && !flatH5b.includes('data:image/png'));

  // ---------- [6] weapp 分支(零回归) ----------
  WITH_CODE = true;
  process.env.TARO_ENV = 'weapp';
  const reactWx = miniReact();
  Object.assign(MOCKS.react, reactWx, { default: reactWx });
  const pageWx = compileLoad(PAGE_SRC, 'page-wx');
  const elWx = await renderFlushed(pageWx.default, reactWx);
  const flatWx = JSON.stringify(elWx);
  const canvasWx = findAll(elWx, x => x.type === 'Canvas');
  record('页面weapp-仍走旧版 Canvas',
    canvasWx.length === 1 && canvasWx[0].props.canvasId === 'promoQr');
  record('页面weapp-无 Image 分支泄漏',
    findAll(elWx, x => x.type === 'Image').length === 0);
  record('页面weapp-推广码展示', flatWx.includes('ZXBJTEST01'));

  // ------------------------------------------------------------
  const pass = results.filter(r => r.ok).length;
  const fail = results.length - pass;
  console.log('-'.repeat(60));
  console.log(`总计: ${pass} 通过 / ${fail} 失败`);
  console.log('='.repeat(60));
  if (fail > 0) process.exit(1);
})().catch(e => {
  console.error('测试执行异常:', e);
  process.exit(1);
});
