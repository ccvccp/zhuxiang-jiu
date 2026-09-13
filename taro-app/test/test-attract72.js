/**
 * test-attract72.js · AI智能自动引流大模型(72号)前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-xinzhi.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [API 层 attract72.ts]
 *   1. 字典完整性(主体二域/人格四型/效果三类/状态六态)
 *   2. 映射函数回落(未知键原样返回)
 *   3. 方法完备(Attract72API 12 方法)
 *   4. admin 头注入(X-Role: admin + Bearer 令牌)
 *   5. 画像筛选 query(subjectType/personaType 拼接)
 *   6. 感知同步请求体(POST + today 可选)
 *   7. 画像详情/信号查询 URL
 *   8. 因果推理 POST + 洞察筛选
 *   9. 定律筛选(kind/status) + 结晶/发布(决策面)
 *   10. 自然语言查询体 + 预算三端点(forecast/rebalance/exploration)
 *   11. 响应解包({code, data} 壳 → data)
 *   [页面层 pages/attract72/index.tsx]
 *   12. 五页签结构(画像/信号/洞察/定律/预算)
 *   13. hero 卡与脚注口径(观测面/决策面 409/LLM 禁入)
 *   14. 感知同步按钮流(点击 → 同步 → toast → 画像行渲染)
 *   15. 主体筛选 chips(全部/会员/博主)
 *   16. 画像行渲染(人格中文/信任分/置信/转化)
 *   17. 信号页签(刷新 → 信号行 + 已消费徽标)
 *   18. 洞察页签(反事实推理按钮 → 行渲染 + 效果徽标类名)
 *   19. 定律页签(law/anti 筛选 → 行渲染 + 查询输入框)
 *   20. 预算页签(方案号/月池切片/偏差/渠道配额/探索基金)
 *   21. 决策面 off 态空态文案
 *   22. 组件确定性(多次渲染结构一致)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'attract72.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'attract72', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];   // request 调用记录
let lastToast = null;  // 最近一次 showToast(断言用)

// ---- Mock 数据(对齐后端 attract72_* 服务真实响应结构) ----
const MOCK_PERSONAS = [
  { personaId: 1, subjectType: 'influencer', subjectId: 1, name: '生活美学家老梅',
    personaType: 'connoisseur', platforms: ['redbook'], followerTier: 'T3',
    followerCount: 5000, verified: true, engagementRate: 0.08,
    conversionRate: 0.2, orderRate: 0.05, trustScore: 68, confidence: 0.6,
    stats: { clickCount: 30, registeredCount: 6, orderCount: 3, gmv: 1200, avgOrderAmount: 400 },
    history: [], createdAt: '2026-09-13T22:22:50Z', updatedAt: '' },
  { personaId: 2, subjectType: 'member', subjectId: 1, name: '会员1',
    personaType: 'sharer', platforms: [], followerTier: 'M0',
    followerCount: 0, verified: false, engagementRate: 0,
    conversionRate: 0.18, orderRate: 0.02, trustScore: 55, confidence: 0.4,
    stats: { clickCount: 10, registeredCount: 2, orderCount: 1, gmv: 268, avgOrderAmount: 268 },
    history: [], createdAt: '2026-09-13T22:22:50Z', updatedAt: '' },
];
const MOCK_SIGNALS = [
  { signalId: 1, type: 'festival', ref: '中秋送礼季', payload: {},
    impactChannels: ['wechat', 'redbook'], weight: 1.2,
    consumed: false, createdAt: '2026-09-13T08:00:00Z' },
  { signalId: 2, type: 'radar', ref: 'radar:9', payload: {},
    impactChannels: ['douyin'], weight: 0.8,
    consumed: true, createdAt: '2026-09-13T06:00:00Z' },
];
const MOCK_INSIGHTS = [
  { insightId: 1, dimension: 'content_element', factor: 'scene:品鉴',
    effectType: 'driver', counterfactualScore: 0.25, rateA: 0.35, rateB: 0.10,
    sampleSize: 20, baseSampleSize: 15, confidence: 1.0, observedCount: 2,
    status: 'verified', evidence: { ordersA: 7, ordersB: 1.5, totalClicks: 35 },
    lastObservedAt: '', createdAt: '' },
  { insightId: 2, dimension: 'channel_feature', factor: 'channel:redbook',
    effectType: 'loss', counterfactualScore: -0.18, rateA: 0.05, rateB: 0.23,
    sampleSize: 12, baseSampleSize: 10, confidence: 0.8, observedCount: 1,
    status: 'pending', evidence: { ordersA: 0.6, ordersB: 2.3, totalClicks: 22 },
    lastObservedAt: '', createdAt: '' },
  { insightId: 3, dimension: 'timing', factor: 'timing:晚8点',
    effectType: 'neutral', counterfactualScore: 0.03, rateA: 0.15, rateB: 0.12,
    sampleSize: 3, baseSampleSize: 4, confidence: 0.3, observedCount: 1,
    status: 'pending', evidence: { ordersA: 0.4, ordersB: 0.5, totalClicks: 7 },
    lastObservedAt: '', createdAt: '' },
];
const MOCK_LAWS = [
  { lawId: 1, dimension: 'content_element', factor: 'scene:品鉴',
    effectType: 'driver', kind: 'law', status: 'active',
    submittedAt: '2026-09-12T10:00:00Z', createdAt: '' },
  { lawId: 2, dimension: 'channel_feature', factor: 'channel:redbook',
    effectType: 'loss', kind: 'anti', status: 'active',
    submittedAt: '2026-09-12T11:00:00Z', createdAt: '' },
];
const MOCK_SYNC = {
  modelVersion: 'v1-attract72-registry', mode: 'off', kill: false,
  synced: 4, created: 2, updated: 2, signalsIngested: 3, radar: 2, festival: 1,
  personas: [],
};
const MOCK_CAUSAL = {
  modelVersion: 'v1-attract72-registry', mode: 'off', kill: false,
  factors: 8, drivers: 2, losses: 1, neutrals: 5,
  insightsTotal: 8, lawsValidated: 1, lawsExpired: 0,
};
const MOCK_FORECAST = {
  modelVersion: 'v1-attract72-registry', mode: 'off', kill: false,
  active: {
    forecastId: 7, status: 'active', poolTotal: 3000, deviation: 0.08,
    windowStart: '2026-09-13T12:00:00Z', windowEnd: '2026-09-16T12:00:00Z',
    allocations: [
      { channel: 'wechat', baseShare: 0.5, signalLift: 0.1, lawBoost: 0.05,
        finalShare: 0.55, amount: 1650, exploration: false },
      { channel: 'redbook', baseShare: 0.3, signalLift: 0, lawBoost: 0,
        finalShare: 0.32, amount: 960, exploration: false },
      { channel: 'douyin', baseShare: 0.2, signalLift: 0, lawBoost: 0,
        finalShare: 0.13, amount: 390, exploration: true },
    ],
  },
  history: [
    { forecastId: 6, deviation: 0.05, windowStart: '2026-09-10T12:00:00Z' },
    { forecastId: 5, deviation: 0.11, windowStart: '2026-09-07T12:00:00Z' },
  ],
};
const MOCK_EXPLORATION = {
  rate: 0.13, status: 'active',
  candidates: [{ channel: 'douyin', samples: 3 }],
};
const MOCK_REBALANCE = {
  deviation: 0.22, actual: 880, expected: 700, rebalancedId: 7,
  newForecastId: 8, diff: [], forecast: {},
};
const MOCK_QUERY = {
  answer: '当前驱动因子: scene:品鉴(+0.25)、channel:wechat(+0.15)',
  route: 'drivers',
};

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  // 定律发布(决策面——须先于台账列表匹配)
  if (/\/api\/attract72\/knowledge\/laws\/\d+\/publish$/.test(url)) {
    return { code: 0, data: { ...MOCK_LAWS[0], status: 'active' } };
  }
  if (url === '/api/attract72/personas'
      || url.startsWith('/api/attract72/personas?')) {
    return { code: 0, data: MOCK_PERSONAS };
  }
  if (url === '/api/attract72/personas/sync') return { code: 0, data: MOCK_SYNC };
  if (/^\/api\/attract72\/personas\/\d+$/.test(url)) {
    return { code: 0, data: MOCK_PERSONAS[0] };
  }
  if (url.startsWith('/api/attract72/signals')) {
    return { code: 0, data: MOCK_SIGNALS };
  }
  if (url === '/api/attract72/causal/run') return { code: 0, data: MOCK_CAUSAL };
  if (url.startsWith('/api/attract72/causal/insights')) {
    return { code: 0, data: MOCK_INSIGHTS };
  }
  if (url === '/api/attract72/knowledge/crystallize') {
    return { code: 0, data: MOCK_LAWS[0] };
  }
  if (url.startsWith('/api/attract72/knowledge/laws')) {
    return { code: 0, data: MOCK_LAWS };
  }
  if (url === '/api/attract72/knowledge/query') return { code: 0, data: MOCK_QUERY };
  if (url.startsWith('/api/attract72/budget/forecast')) {
    return { code: 0, data: MOCK_FORECAST };
  }
  if (url === '/api/attract72/budget/rebalance/auto') {
    return { code: 0, data: MOCK_REBALANCE };
  }
  if (url === '/api/attract72/budget/exploration') {
    return { code: 0, data: MOCK_EXPLORATION };
  }
  return { code: 0, data: {} };
};

// 极简 React hooks 运行时(仅 useState/useCallback 同步路径——本页无 useEffect)
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
    rerender() {
      hookIdx = 0;
      return renderFn(renderProps);
    },
    resetIdx() { hookIdx = 0; },
  };
  api.default = api;
  return Object.assign(Object.create(null), {
    __esModule: true, default: api, ...api,
  });
}

/** 渲染组件并 flush 异步数据(点击句柄 → 宏任务排空微任务链 → 重渲染至稳定) */
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
  showToast: (o) => { lastToast = o; },
  showModal: () => {}, navigateTo: () => {},
};
function createElement(type, props, ...children) {
  return { type, props: props || {},
    children: children.flat().filter(c => c != null && c !== false && c !== true) };
}

// ============================================================
// 2. 编译加载
// ============================================================
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
  const out = path.join(os.tmpdir(), `attract72-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

const reactRef = { current: miniReact() };
const MOCKS = {
  react: reactRef.current,
  '@tarojs/components': Object.assign(Object.create(null), {
    __esModule: true, View: 'View', Text: 'Text', ScrollView: 'ScrollView',
    Input: 'Input',
  }),
  '@tarojs/taro': Object.assign(Object.create(null), {
    __esModule: true, default: mockTaro, ...mockTaro,
  }),
  './index.module.scss': mockStyles,
  './request': { request: mockRequest },
  // auth-service(attract72.ts adminHeaders 依赖)
  '@/services/auth-service': {
    __esModule: true,
    getSession: () => ({ memberId: '1', phone: '13800000001',
      nickname: '测试管理员', role: 'admin', accessToken: 'tk-attract72' }),
    getMemberId: () => '1',
    requireLogin: () => true,
    isLoggedIn: () => true,
  },
  '@/components/NavBar': { __esModule: true, default: () => null },
};

// 拦截器先装, 再编译(API 模块的 './request' 依赖经 MOCKS 拦截)
let apiMod;
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  if (request === '@/api/attract72') return apiMod;
  return origLoad.apply(this, arguments);
};
apiMod = compileLoad(API_SRC, 'api');
const Attract72API = apiMod.Attract72API;

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
  console.log('AI智能自动引流大模型(72号) 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1] 字典完整性 ----------
  record('字典-主体二域', Object.keys(apiMod.SUBJECT_TYPE_NAME).length === 2
    && apiMod.SUBJECT_TYPE_NAME.member === '会员'
    && apiMod.SUBJECT_TYPE_NAME.influencer === '博主');
  record('字典-人格四型', Object.keys(apiMod.PERSONA_TYPE_NAME).length === 4
    && apiMod.PERSONA_TYPE_NAME.connoisseur === '专业品鉴型'
    && apiMod.PERSONA_TYPE_NAME.sharer === '生活分享型'
    && apiMod.PERSONA_TYPE_NAME.bargain_hunter === '优惠敏感型'
    && apiMod.PERSONA_TYPE_NAME.newcomer === '新晋型');
  record('字典-效果三类', Object.keys(apiMod.EFFECT_TYPE_NAME).length === 3
    && apiMod.EFFECT_TYPE_NAME.driver === '驱动因子'
    && apiMod.EFFECT_TYPE_NAME.loss === '流失因子'
    && apiMod.EFFECT_TYPE_NAME.neutral === '中性');
  record('字典-状态六态', Object.keys(apiMod.STATUS_NAME).length === 6
    && apiMod.STATUS_NAME.verified === '已验证'
    && apiMod.STATUS_NAME.submitted === '已提交46号'
    && apiMod.STATUS_NAME.active === '已生效');

  // ---------- [2] 映射函数回落 ----------
  record('映射-未知键原样返回',
    apiMod.subjectTypeName('X') === 'X'
    && apiMod.personaTypeName('y') === 'y'
    && apiMod.effectTypeName('z') === 'z'
    && apiMod.statusName('w') === 'w');
  record('映射-中文正常', apiMod.subjectTypeName('member') === '会员'
    && apiMod.personaTypeName('connoisseur') === '专业品鉴型'
    && apiMod.effectTypeName('driver') === '驱动因子'
    && apiMod.statusName('active') === '已生效');

  // ---------- [3] 方法完备 ----------
  const METHODS = ['personas', 'syncPersonas', 'persona', 'signals', 'runCausal',
    'insights', 'laws', 'crystallize', 'publishLaw', 'query', 'forecast',
    'rebalance', 'exploration'];
  record('API-方法13个', METHODS.length === 13
    && METHODS.every(m => typeof Attract72API[m] === 'function'));

  // ---------- [4-11] URL/请求体/解包 ----------
  const byUrl = (frag) => requests.filter(r => (r.url || '').includes(frag))[0];
  const exact = (u) => requests.find(r => r.url === u);

  requests.length = 0;
  const personasRes = await Attract72API.personas({ subjectType: 'influencer' });
  record('URL-画像筛选query', exact('/api/attract72/personas?subjectType=influencer')
    && personasRes.length === 2 && personasRes[0].personaId === 1
    && personasRes[0].name === '生活美学家老梅',
    `urls=${JSON.stringify(requests.map(r => r.url))} res=${JSON.stringify(personasRes).slice(0, 80)}`);

  await Attract72API.personas();
  record('API-admin头注入', exact('/api/attract72/personas')
    && exact('/api/attract72/personas').headers['X-Role'] === 'admin'
    && exact('/api/attract72/personas').headers.Authorization === 'Bearer tk-attract72');

  requests.length = 0;
  const syncRes = await Attract72API.syncPersonas('2026-09-13');
  await Attract72API.syncPersonas();
  record('URL-同步请求体', exact('/api/attract72/personas/sync').method === 'POST'
    && exact('/api/attract72/personas/sync').data.today === '2026-09-13'
    && byUrl('/personas/sync').data && byUrl('/personas/sync').data !== undefined
    && syncRes.synced === 4 && syncRes.created === 2 && syncRes.mode === 'off');

  const p1 = await Attract72API.persona(1);
  record('URL-画像详情', exact('/api/attract72/personas/1')
    && p1.personaType === 'connoisseur' && p1.trustScore === 68);

  requests.length = 0;
  const sigRes = await Attract72API.signals('festival', 50);
  record('URL-信号查询', exact('/api/attract72/signals?type=festival&limit=50')
    && sigRes.length === 2 && sigRes[0].weight === 1.2);

  requests.length = 0;
  const causal = await Attract72API.runCausal();
  await Attract72API.insights({ effectType: 'driver' });
  record('URL-因果推理POST+洞察筛选',
    exact('/api/attract72/causal/run').method === 'POST'
    && causal.drivers === 2 && causal.losses === 1 && causal.neutrals === 5
    && exact('/api/attract72/causal/insights?effectType=driver')
    && causal.modelVersion === 'v1-attract72-registry');

  requests.length = 0;
  const lawsRes = await Attract72API.laws('anti');
  await Attract72API.laws();
  record('URL-定律筛选', exact('/api/attract72/knowledge/laws?kind=anti&limit=50')
    && lawsRes.length === 2 && lawsRes[1].kind === 'anti');

  requests.length = 0;
  const crystal = await Attract72API.crystallize(1);
  const published = await Attract72API.publishLaw(5);
  record('URL-结晶发布(决策面)', exact('/api/attract72/knowledge/crystallize').method === 'POST'
    && exact('/api/attract72/knowledge/crystallize').data.insightId === 1
    && exact('/api/attract72/knowledge/laws/5/publish').method === 'POST'
    && crystal.lawId === 1 && published.status === 'active');

  requests.length = 0;
  const qRes = await Attract72API.query('哪些渠道是驱动因子');
  const fRes = await Attract72API.forecast('2026-09-14T00:00:00Z');
  const rRes = await Attract72API.rebalance('2026-09-14T00:00:00Z');
  const eRes = await Attract72API.exploration();
  record('URL-查询与预算三端点',
    exact('/api/attract72/knowledge/query').data.question === '哪些渠道是驱动因子'
    && qRes.route === 'drivers'
    && exact('/api/attract72/budget/forecast?now=2026-09-14T00:00:00Z')
    && exact('/api/attract72/budget/rebalance/auto').method === 'POST'
    && exact('/api/attract72/budget/rebalance/auto').data.now === '2026-09-14T00:00:00Z'
    && exact('/api/attract72/budget/exploration'));
  record('映射-响应解包({code,data}壳)', fRes.active.forecastId === 7
    && fRes.active.poolTotal === 3000 && fRes.active.deviation === 0.08
    && fRes.history.length === 2
    && rRes.newForecastId === 8 && rRes.deviation === 0.22
    && eRes.rate === 0.13 && eRes.candidates.length === 1);

  // ---------- [12-22] 页面层 ----------
  const reactForPage = miniReact();
  Object.assign(MOCKS.react, reactForPage, { default: reactForPage });
  const el = await renderFlushed(pageMod.default, reactForPage);
  const flat = JSON.stringify(el);
  const waitTick = (ms) => new Promise(r => setTimeout(r, ms));
  const clickBtn = (root, label) => {
    const t = findAll(root, n => typeof n.props.onClick === 'function'
      && textOf(n) === label)[0];
    if (t) t.props.onClick();
    return !!t;
  };

  record('页面-五页签', flat.includes('画像') && flat.includes('信号')
    && flat.includes('洞察') && flat.includes('定律') && flat.includes('预算')
    && findAll(el, n => n.props.className === 'tabBar').length === 1);

  const heroEl = findAll(el, n => n.props.className === 'heroCard');
  const footEl = findAll(el, n => n.props.className === 'footNote');
  record('页面-hero卡与脚注口径', heroEl.length === 1
    && textOf(heroEl[0]).includes('AI智能自动引流大模型')
    && footEl.length === 1
    && textOf(footEl[0]).includes('不受 ATTRACT72_MODE 影响')
    && textOf(footEl[0]).includes('46 号建议书')
    && textOf(footEl[0]).includes('LLM 禁入'));

  record('页面-同步与刷新按钮', flat.includes('感知面同步')
    && flat.includes('刷新列表'));

  const chipEls = findAll(el, n => typeof n.props.className === 'string'
    && n.props.className.trim().split(' ').includes('chip'));
  record('页面-主体筛选chips', chipEls.length === 3
    && ['全部主体', '会员', '博主'].every(c =>
      chipEls.some(n => textOf(n) === c)),
    `chips=${JSON.stringify(chipEls.map(n => ({ c: n.props.className, t: textOf(n) })))}`);

  // [14] 感知同步流(点击 → POST sync → toast → 画像行)
  requests.length = 0;
  lastToast = null;
  const clickedSync = clickBtn(el, '感知面同步');
  await waitTick(80);
  const elSynced = reactForPage.__test.rerender();
  const personaRows = findAll(elSynced, n => n.props.className === 'row');
  record('页面-同步流(POST+toast+行渲染)', clickedSync
    && byUrl('/personas/sync') && byUrl('/personas/sync').method === 'POST'
    && byUrl('/api/attract72/personas') && lastToast
    && lastToast.title.includes('同步完成: 画像 4 条')
    && personaRows.length === 2);

  // [16] 画像行内容(人格中文/信任分/置信/转化)
  record('页面-画像行渲染', textOf(personaRows[0]).includes('#1')
    && textOf(personaRows[0]).includes('生活美学家老梅')
    && textOf(personaRows[0]).includes('专业品鉴型')
    && textOf(personaRows[0]).includes('信任分 68')
    && textOf(personaRows[0]).includes('博主')
    && textOf(personaRows[1]).includes('生活分享型'));

  // [17] 信号页签(刷新 → 信号行)
  clickBtn(elSynced, '信号');
  await waitTick(40);
  const elSignalTab = reactForPage.__test.rerender();
  clickBtn(elSignalTab, '刷新信号');
  await waitTick(80);
  const elSignals = reactForPage.__test.rerender();
  const signalRows = findAll(elSignals, n => n.props.className === 'row');
  record('页面-信号行渲染', signalRows.length === 2
    && textOf(signalRows[0]).includes('festival')
    && textOf(signalRows[0]).includes('中秋送礼季')
    && textOf(signalRows[0]).includes('权重 1.2')
    && textOf(signalRows[1]).includes('已消费'));

  // [18] 洞察页签(推理按钮 → 行渲染 + 效果徽标类名)
  clickBtn(elSignals, '洞察');
  await waitTick(40);
  const elInsightTab = reactForPage.__test.rerender();
  requests.length = 0;
  lastToast = null;
  const clickedRun = clickBtn(elInsightTab, '运行反事实推理');
  await waitTick(80);
  const elInsights = reactForPage.__test.rerender();
  const insightRows = findAll(elInsights, n => n.props.className === 'row');
  record('页面-反事实推理流', clickedRun
    && exact('/api/attract72/causal/run') && exact('/api/attract72/causal/run').method === 'POST'
    && lastToast && lastToast.title.includes('推理完成: 驱动 2 · 流失 1')
    && insightRows.length === 3);
  const driverBadges = findAll(elInsights, n =>
    typeof n.props.className === 'string'
    && n.props.className.includes('effectDriver'));
  const lossBadges = findAll(elInsights, n =>
    typeof n.props.className === 'string'
    && n.props.className.includes('effectLoss'));
  record('页面-效果徽标类名', driverBadges.length === 1
    && lossBadges.length === 1
    && textOf(insightRows[0]).includes('驱动因子')
    && textOf(insightRows[0]).includes('反事实分 0.25')
    && textOf(insightRows[0]).includes('已验证')
    && textOf(insightRows[1]).includes('流失因子'));

  // [19] 定律页签(台账行 + 反知识 + 查询框)
  clickBtn(elInsights, '定律');
  await waitTick(40);
  const elLawTab = reactForPage.__test.rerender();
  clickBtn(elLawTab, '刷新台账');
  await waitTick(80);
  const elLaws = reactForPage.__test.rerender();
  const lawRows = findAll(elLaws, n => n.props.className === 'row');
  const queryInputs = findAll(elLaws, n => n.type === 'Input');
  record('页面-定律台账与查询框', lawRows.length === 2
    && textOf(lawRows[0]).includes('scene:品鉴')
    && textOf(lawRows[0]).includes('已生效')
    && textOf(lawRows[1]).includes('反知识')
    && queryInputs.length === 1
    && (queryInputs[0].props.placeholder || '').includes('驱动因子'));

  // 查询流(输入 → 查询 → 回答卡)
  queryInputs[0].props.onInput({ detail: { value: '哪些渠道是驱动因子' } });
  const elQueryFilled = reactForPage.__test.rerender();
  const queryBtn = findAll(elQueryFilled, n => typeof n.props.onClick === 'function'
    && textOf(n) === '查询')[0];
  requests.length = 0;
  queryBtn.props.onClick();
  await waitTick(80);
  const elAnswer = reactForPage.__test.rerender();
  const answerCard = findAll(elAnswer, n => n.props.className === 'answerCard');
  record('页面-自然语言查询流', exact('/api/attract72/knowledge/query')
    && answerCard.length === 1
    && textOf(answerCard[0]).includes('驱动因子'));

  // [20] 预算页签(方案/配额/偏差/探索基金)
  clickBtn(elAnswer, '预算');
  await waitTick(40);
  const elBudgetTab = reactForPage.__test.rerender();
  requests.length = 0;
  lastToast = null;
  clickBtn(elBudgetTab, '刷新预算状态');
  await waitTick(80);
  const elBudget = reactForPage.__test.rerender();
  const statNums = findAll(elBudget, n => n.props.className === 'statNum')
    .map(textOf);
  const allocRows = findAll(elBudget, n => n.props.className === 'rowMeta');
  const expHead = findAll(elBudget, n => n.props.className === 'resHead'
    && textOf(n).includes('探索基金'));
  const budgetText = JSON.stringify(elBudget);
  record('页面-预算方案与配额', statNums.includes('7')
    && statNums.includes('¥3000') && statNums.includes('8%')
    && allocRows.length >= 3
    && JSON.stringify(allocRows.map(textOf)).includes('wechat')
    && JSON.stringify(allocRows.map(textOf)).includes('探索')
    && expHead.length === 1
    && budgetText.includes('13%') && budgetText.includes('active'),
    `statNums=${JSON.stringify(statNums)} allocRows=${allocRows.length} expHeads=${expHead.length}`);

  // 重博弈快环(toast + 新方案号)
  requests.length = 0;
  clickBtn(elBudget, '偏差重博弈(>15% 快环)');
  await waitTick(80);
  record('页面-重博弈快环流', exact('/api/attract72/budget/rebalance/auto')
    && lastToast && lastToast.title.includes('已重博弈')
    && lastToast.title.includes('#8'));

  // [21] 决策面 off 态空态(历史方案存在但未点过生成——切回定律再验查询)
  const reactOff = miniReact();
  Object.assign(MOCKS.react, reactOff, { default: reactOff });
  const elOff = await renderFlushed(pageMod.default, reactOff);
  clickBtn(elOff, '预算');
  await waitTick(40);
  const elOffBudget = reactOff.__test.rerender();
  record('页面-初始空态(未加载)', textOf(elOffBudget).includes('点击「刷新预算状态」加载'));

  // [22] 组件确定性(多次渲染结构一致)
  const reactAgain = miniReact();
  Object.assign(MOCKS.react, reactAgain, { default: reactAgain });
  const elAgain = await renderFlushed(pageMod.default, reactAgain);
  record('页面-渲染确定性',
    JSON.stringify(findAll(el, n => n.props.className === 'tab').map(textOf))
    === JSON.stringify(findAll(elAgain, n => n.props.className === 'tab').map(textOf))
    && JSON.stringify(findAll(el, n => n.props.className === 'chip').map(textOf))
    === JSON.stringify(findAll(elAgain, n => n.props.className === 'chip').map(textOf)));

  // 汇总
  const pass = results.filter(r => r.ok).length;
  console.log('-'.repeat(60));
  console.log(`通过: ${pass} / ${results.length}`);
  process.exit(pass === results.length ? 0 : 1);
})().catch(e => { console.error('FATAL:', e); process.exit(1); });
