/**
 * test-av62.js · 62号·AI智能无形资产估值模型 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-xinzhi.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖:
 *   [API 层 api/av62.ts]
 *   1. 字典完整性(三角色/置信三档/估值目标)
 *   2. 映射函数回落(未知键原样)
 *   3. 方法完备(8 只读端点)
 *   4. admin 头注入(X-Role + Bearer)
 *   5. URL 正确性(状态/看板/资产/详情/评估/注册表/公平性/回流)
 *   6. 资产筛选 query(role 拼接)
 *   7. 响应解包({success,data}壳)
 *   [页面层 pages/av62/index.tsx]
 *   8. 四页签结构(状态/看板/资产/评估)
 *   9. hero 卡与脚注治理口径(观测面/决策面 409)
 *   10. 状态页流(点刷新 → 模式/版本/八因子网格)
 *   11. 看板页流(四区: 估值准确率/资产分布/评估版本链/红队)
 *   12. 资产页流(角色筛选 chips + 行渲染 + 负资产徽标)
 *   13. 评估页流(置信档徽标类名 + 要素/净贡献/基准值)
 *   14. 组件确定性
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'av62.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'av62', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];

const MOCK_STATUS = {
  status: {
    mode: 'off', activeVersion: 'v1-baseline-37', scorerId: '37',
    decisions: ['register', 'assess', 'stress', 'calibrate'],
    factorsMeta: {
      frequency: '频次', recency: '近因', consistency: '一致性',
      crossValidation: '交叉验证', liquidity: '流动性',
      regulatoryRisk: '合规风险', marketDepth: '市场深度',
      provenance: '溯源完备',
    },
  },
};
const MOCK_DASHBOARD = {
  zones: {
    metrics: {
      valuationAccuracy: 0.87, attributionGrounded: 0.94,
      fairness: { compliant: true },
      scorer: { trustScore: 0.91 },
    },
    assets: {
      total: 12, negativeCount: 2,
      byRole: { enterprise: 5, organization: 4, personal: 3 },
      byDomain: { brand: 3, patent: 4, copyright: 5 },
      byLiquidity: { high: 4, medium: 5, low: 3 },
    },
    assessments: {
      total: 20, maxVersionChain: 3,
      byConfidence: { high: 8, medium: 7, low: 5 },
    },
    defense: {
      redteamRuns: 6,
      redteamLatest: { allDefended: true },
    },
  },
};
const MOCK_ASSETS = {
  total: 2, negative: 1,
  assets: [
    { assetId: 1, subjectId: 100, role: 'enterprise', domain: 'patent',
      label: '酿造工艺专利', status: 'active', negative: false },
    { assetId: 2, subjectId: 200, role: 'personal', domain: 'copyright',
      label: '侵权诉讼标的', status: 'active', negative: true },
  ],
};
const MOCK_ASSET_DETAIL = {
  assetId: 1, subjectId: 100, role: 'enterprise', domain: 'patent',
  label: '酿造工艺专利', status: 'active', negative: false,
  evidence: [], factors: [],
};
const MOCK_ASSESSMENTS = {
  total: 2,
  assessments: [
    { assessId: 11, assetId: 1, domain: 'patent', version: 2,
      elementScore: 82, netContribution: 15000, baseValue: 200000,
      confidenceTier: 'high', ruleId: 'R-PATENT-01' },
    { assessId: 12, assetId: 2, domain: 'copyright', version: 1,
      elementScore: 35, netContribution: -8000, baseValue: 50000,
      confidenceTier: 'low', ruleId: '' },
  ],
};

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url === '/api/av62/model/status') return { success: true, data: MOCK_STATUS };
  if (url === '/api/av62/dashboard') return { success: true, data: MOCK_DASHBOARD };
  if (url.startsWith('/api/av62/assets/')) return { success: true, data: MOCK_ASSET_DETAIL };
  if (url.startsWith('/api/av62/assets')) return { success: true, data: MOCK_ASSETS };
  if (url.startsWith('/api/av62/assessments')) return { success: true, data: MOCK_ASSESSMENTS };
  if (url === '/api/av62/registry') return { success: true, data: { roles: 3, domains: 9 } };
  if (url === '/api/av62/fairness/report') return { success: true, data: { ok: true } };
  if (url === '/api/av62/learn/status') return { success: true, data: { batch: 41 } };
  return { success: true, data: {} };
};

// 极简 React hooks 运行时
function miniReact() {
  let hookIdx = 0;
  let renderFn = null;
  let renderProps = null;
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
    useCallback: (fn) => fn,
    createElement,
  };
  api.__test = {
    effects, states, dirty,
    setRenderer(fn, props) { renderFn = fn; renderProps = props; },
    rerender() { hookIdx = 0; return renderFn(renderProps); },
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
    t.setRenderer(Comp, {});
    const fns = t.effects.splice(0);
    for (const fn of fns) {
      try { await fn(); } catch (_) { /* 忽略 */ }
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
const mockTaro = { showToast: () => {} };
function createElement(type, props, ...children) {
  return { type, props: props || {},
    children: children.flat().filter(c => c != null && c !== false && c !== true) };
}

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
  const out = path.join(os.tmpdir(), `av62-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

const reactRef = { current: miniReact() };
const MOCKS = {
  react: reactRef.current,
  '@tarojs/components': Object.assign(Object.create(null), {
    __esModule: true, View: 'View', Text: 'Text', ScrollView: 'ScrollView',
  }),
  '@tarojs/taro': Object.assign(Object.create(null), {
    __esModule: true, default: mockTaro, ...mockTaro,
  }),
  './index.module.scss': mockStyles,
  './request': { request: mockRequest },
  '@/services/auth-service': {
    __esModule: true,
    getSession: () => ({ memberId: '1', phone: '13800000001',
      nickname: '测试管理员', role: 'admin', accessToken: 'tk-av62' }),
    getMemberId: () => '1',
  },
  '@/components/NavBar': { __esModule: true, default: () => null },
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  if (request === '@/api/av62') return apiMod;
  return origLoad.apply(this, arguments);
};

const apiMod = compileLoad(API_SRC, 'api');
const Av62API = apiMod.Av62API;
const pageMod = compileLoad(PAGE_SRC, 'page');

// ============================================================
// 3. 断言工具
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
const textOf = (node) => {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (node && typeof node === 'object') return (node.children || []).map(textOf).join('');
  return '';
};

(async () => {
  console.log('='.repeat(60));
  console.log('62号·AI智能无形资产估值模型 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1-2] 字典与映射 ----------
  record('字典-三角色', Object.keys(apiMod.ROLE_NAME).length === 3
    && apiMod.ROLE_NAME.enterprise === '企业'
    && apiMod.ROLE_NAME.organization === '组织'
    && apiMod.ROLE_NAME.personal === '个人');
  record('字典-置信三档', Object.keys(apiMod.TIER_NAME).length === 3
    && apiMod.TIER_NAME.high === '高置信'
    && apiMod.TIER_NAME.medium === '中置信'
    && apiMod.TIER_NAME.low === '低置信');
  record('字典-估值目标', apiMod.OBJECTIVE_NAME.stability === '稳健'
    && apiMod.OBJECTIVE_NAME.growth === '成长'
    && apiMod.OBJECTIVE_NAME.fair === '公允');
  record('映射-未知键原样', apiMod.roleName('X') === 'X'
    && apiMod.tierName('y') === 'y'
    && apiMod.objectiveName('z') === 'z');

  // ---------- [3-7] API 层 ----------
  const METHODS = ['status', 'dashboard', 'assets', 'asset', 'assessments',
    'registry', 'fairness', 'learnStatus'];
  record('API-方法8个', METHODS.length === 8
    && METHODS.every(m => typeof Av62API[m] === 'function'));

  requests.length = 0;
  const st = await Av62API.status();
  await Av62API.dashboard();
  await Av62API.assets({ role: 'enterprise' });
  await Av62API.asset(1);
  await Av62API.assessments(50);
  await Av62API.registry();
  await Av62API.fairness();
  await Av62API.learnStatus();
  const exact = (u) => requests.find(r => r.url === u);
  record('URL-八端点正确', !!exact('/api/av62/model/status')
    && !!exact('/api/av62/dashboard')
    && !!exact('/api/av62/assets?role=enterprise')
    && !!exact('/api/av62/assets/1')
    && !!exact('/api/av62/assessments?limit=50')
    && !!exact('/api/av62/registry')
    && !!exact('/api/av62/fairness/report')
    && !!exact('/api/av62/learn/status'));
  record('API-admin头注入', exact('/api/av62/model/status').headers['X-Role'] === 'admin'
    && exact('/api/av62/model/status').headers.Authorization === 'Bearer tk-av62');
  record('映射-响应解包', st.status.mode === 'off'
    && st.status.activeVersion === 'v1-baseline-37'
    && Object.keys(st.status.factorsMeta).length === 8);
  const assetsRes = await Av62API.assets();
  record('映射-资产解包', assetsRes.total === 2 && assetsRes.negative === 1
    && assetsRes.assets[0].label === '酿造工艺专利');

  // ---------- [8-14] 页面层 ----------
  const reactP = miniReact();
  Object.assign(MOCKS.react, reactP, { default: reactP });
  const el = await renderFlushed(pageMod.default, reactP);
  const flat = JSON.stringify(el);
  const waitTick = (ms) => new Promise(r => setTimeout(r, ms));
  const clickBtn = (root, label) => {
    const t = findAll(root, n => typeof n.props.onClick === 'function'
      && textOf(n) === label)[0];
    if (t) t.props.onClick();
    return !!t;
  };

  record('页面-四页签', flat.includes('状态') && flat.includes('看板')
    && flat.includes('资产') && flat.includes('评估'));
  const heroEl = findAll(el, n => n.props.className === 'heroCard');
  const footEl = findAll(el, n => n.props.className === 'footNote');
  record('页面-hero与脚注口径', heroEl.length === 1
    && textOf(heroEl[0]).includes('AI智能无形资产估值模型')
    && footEl.length === 1
    && textOf(footEl[0]).includes('不受 AV62_MODE 影响')
    && textOf(footEl[0]).includes('off 态 409'));

  // [10] 状态页流
  clickBtn(el, '刷新模型状态');
  await waitTick(60);
  const elStatus = reactP.__test.rerender();
  const statusText = textOf(elStatus);
  const factorCells = findAll(elStatus, n => n.props.className === 'factorCell');
  record('页面-状态页流(模式/版本/八因子)', statusText.includes('off')
    && statusText.includes('v1-baseline-37')
    && factorCells.length === 8
    && statusText.includes('溯源完备'));

  // [11] 看板页流(四区)
  clickBtn(elStatus, '看板');
  await waitTick(30);
  const elBoardTab = reactP.__test.rerender();
  clickBtn(elBoardTab, '刷新四区看板');
  await waitTick(60);
  const elBoard = reactP.__test.rerender();
  const boardText = textOf(elBoard);
  record('页面-看板四区流', boardText.includes('87.0%')
    && boardText.includes('94.0%')
    && boardText.includes('达标')
    && boardText.includes('资产总数 12')
    && boardText.includes('enterprise:5')
    && boardText.includes('最大版本链 v3')
    && boardText.includes('红队 6 轮')
    && boardText.includes('全防御'));

  // [12] 资产页流(筛选 + 负徽标)
  clickBtn(elBoard, '资产');
  await waitTick(30);
  const elAssetTab = reactP.__test.rerender();
  requests.length = 0;
  clickBtn(elAssetTab, '刷新资产列表');
  await waitTick(60);
  const elAssets = reactP.__test.rerender();
  const assetRows = findAll(elAssets, n => n.props.className === 'assetRow');
  const negBadges = findAll(elAssets, n => n.props.className === 'negBadge');
  const chipEls = findAll(elAssets, n => typeof n.props.className === 'string'
    && n.props.className.trim().split(' ').includes('chip'));
  record('页面-资产列表流(负徽标)', assetRows.length === 2
    && negBadges.length === 1
    && textOf(negBadges[0]) === '负'
    && textOf(elAssets).includes('酿造工艺专利')
    && textOf(elAssets).includes('共 2 项'));
  record('页面-角色筛选chips', chipEls.length === 4
    && ['全部', '企业', '组织', '个人'].every(c =>
      chipEls.some(n => textOf(n) === c)));
  // 点击"企业"筛选 → 请求带 role=enterprise
  const entChip = chipEls.find(n => textOf(n) === '企业');
  requests.length = 0;
  entChip.props.onClick();
  await waitTick(60);
  record('页面-角色筛选请求', requests.some(r =>
    r.url === '/api/av62/assets?role=enterprise'));

  // [13] 评估页流(置信档徽标)
  clickBtn(elAssets, '评估');
  await waitTick(30);
  const elAssessTab = reactP.__test.rerender();
  clickBtn(elAssessTab, '刷新评估流水');
  await waitTick(60);
  const elAssess = reactP.__test.rerender();
  const tierHigh = findAll(elAssess, n =>
    typeof n.props.className === 'string'
    && n.props.className.includes('tierHigh'));
  const tierLow = findAll(elAssess, n =>
    typeof n.props.className === 'string'
    && n.props.className.includes('tierLow'));
  const assessText = textOf(elAssess);
  record('页面-评估流水流(置信徽标/净贡献)',
    tierHigh.length === 1 && tierLow.length === 1
    && assessText.includes('高置信') && assessText.includes('低置信')
    && assessText.includes('要素 82')
    && assessText.includes('净贡献 15000')
    && assessText.includes('净贡献 -8000')
    && assessText.includes('基准值 200000')
    && assessText.includes('共 2 条'));

  // [14] 组件确定性
  const reactP2 = miniReact();
  Object.assign(MOCKS.react, reactP2, { default: reactP2 });
  const el2 = await renderFlushed(pageMod.default, reactP2);
  record('页面-渲染确定性',
    JSON.stringify(findAll(el, n => n.props.className === 'tab').map(textOf))
    === JSON.stringify(findAll(el2, n => n.props.className === 'tab').map(textOf)));

  // 汇总
  const pass = results.filter(r => r.ok).length;
  console.log('-'.repeat(60));
  console.log(`通过: ${pass} / ${results.length}`);
  process.exit(pass === results.length ? 0 : 1);
})().catch(e => { console.error('FATAL:', e); process.exit(1); });
