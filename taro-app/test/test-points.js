/**
 * test-points.js · 签到/积分体系前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-xinzhi.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖(对应 2026-09-13/14 三轮修复):
 *   [纯函数层 config/index.ts]
 *   1. beijingToday 北京口径(UTC 23:30 → 北京次日——时区修复核心)
 *   2. beijingToday 格式(YYYY-MM-DD)
 *   3. beijingDateOffset(0) === beijingToday()
 *   4. beijingDateOffset(1/6) 日期回退
 *   [API 层 api/points.ts]
 *   5. signin 解包({success,data} 壳 → 字段)
 *   6. signin isBonus 布尔化(数字/布尔兼容)
 *   7. signinRecords 映射(data 数组 + 驼峰)
 *   8. account 解包(totalPoints——账本口径)
 *   [页面-积分中心 pages/points]
 *   9. 今日已签判定(北京口径 signDate 匹配)
 *   10. 签到按钮跳首页(switchTab)
 *   11. 近 7 日日历(7 格 + 今日已签标记)
 *   [页面-我的 pages/mine]
 *   12. 订单区默认折叠(标题 N + 订单卡不渲染)
 *   13. 点击标题展开订单卡
 *   14. 订单状态中文映射(原始英文码 → 中文, 无英文残留)
 *   15. 积分读账本(account.totalPoints 优先于遗留 member.points)
 *   16. 顶部积分/订单统计可点击(navigateTo)
 *   [页面-首页 pages/index]
 *   17. 资产卡积分显示账本 110(而非遗留 0)
 *   18. 已签幂等(toast 今日已签到 + 不发 POST)
 *   19. 未签签到流(POST → toast +10 → 本地存储北京日期)
 *   20. 签到入口跳积分中心(navigateTo points)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const CONFIG_SRC = path.resolve(__dirname, '..', 'src', 'config', 'index.ts');
const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'points.ts');
const MINE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'mine', 'index.tsx');
const PONTS_PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'points', 'index.tsx');
const HOME_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'index', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];
let lastToast = null;
const navCalls = [];
const switchCalls = [];
const storage = {};   // Taro storage 内存实现

// 可变 mock 状态(场景切换: 已签/未签)
let MOCK_SIGNIN_RECORDS = [];   // {signDate, continuousDays, pointsEarned, isBonus}
let MOCK_SIGNED_TODAY = false;  // 控制 signinRecords 是否含今日
let MOCK_ACCOUNT = { userId: 5, totalPoints: 110, frozenPoints: 0,
  totalEarned: 110, totalSpent: 0, expiringPoints: 0 };
const MOCK_MEMBER = { id: 5, name: '测试五号', points: 0, level: 'L1',
  role: 'member' };  // 遗留 points=0——账本口径验证用
const MOCK_ORDERS_RAW = [
  { orderId: 'ZD-SEED-003', status: 'RECEIVED',
    items: [{ productId: 'ZX42-2026L07', productName: '竹奕·竹香型 42° 500ml',
      quantity: 1, unitPrice: 268 }],
    priceDetail: { actualAmount: 268 },
    shipperType: 'manufacturer' },
  { orderId: 'ZD-SEED-006', status: 'COMPLETED',
    items: [{ productId: 'ZX42-2026L05', productName: '竹奕·竹香型 42° 500ml 礼盒',
      quantity: 4, unitPrice: 168 }],
    priceDetail: { actualAmount: 1072 },
    shipperType: 'agent', shipperAgentName: '代理A' },
];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url === '/api/points/signin' && opts.method === 'POST') {
    if (MOCK_SIGNED_TODAY) {
      // 业务 409: request.ts 会 throw——此处直接模拟后端 409 语义
      const e = new Error('今日已签到');
      throw e;
    }
    MOCK_SIGNED_TODAY = true;
    return { success: true, data: { signinId: 9, userId: 5,
      signDate: beijingTodayRef(), continuousDays: 2,
      pointsEarned: 10, isBonus: false, bonusPoints: 0 } };
  }
  if (url.startsWith('/api/points/signin/')) {
    const list = MOCK_SIGNED_TODAY
      ? [{ signDate: beijingTodayRef(), continuousDays: 2,
          pointsEarned: 10, isBonus: 1, bonusPoints: 0 }]
      : [];
    return { success: true, data: list, count: list.length };
  }
  if (url.startsWith('/api/points/account/')) {
    return { success: true, data: MOCK_ACCOUNT };
  }
  if (url.startsWith('/api/points/logs/')) {
    return { success: true, data: [
      { id: 1, type: 'earn', source: 'signin', points: 10,
        balance: 110, refDesc: '每日签到', createdAt: '2026-09-14T00:00:00' },
    ] };
  }
  if (url === '/api/member/profile') return { profile: MOCK_MEMBER };
  if (url === '/api/order/my') return { orders: MOCK_ORDERS_RAW, count: 2 };
  if (url === '/api/wallet/info') return { currentBalance: 88.5 };
  if (url === '/api/credit/quota/5') return { availableQuota: 500 };
  return { success: true, data: {} };
};

// beijingToday 的引用(延迟取——编译后模块加载)
let beijingTodayRef = () => '';

// 极简 React hooks 运行时(useState/useEffect/useCallback)
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

async function renderFlushed(Comp, react, maxRounds = 10) {
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
const mockTaro = {
  showToast: (o) => { lastToast = o; },
  showModal: ({ success }) => success && success({ confirm: false }),
  navigateTo: (o) => navCalls.push(o.url),
  switchTab: (o) => switchCalls.push(o.url),
  setStorageSync: (k, v) => { storage[k] = v; },
  getStorageSync: (k) => storage[k] ?? '',
  removeStorageSync: (k) => { delete storage[k]; },
  getLocation: () => Promise.reject(new Error('no-location')),
  useDidShow: () => {},
};
function createElement(type, props, ...children) {
  return { type, props: props || {},
    children: children.flat().filter(c => c != null && c !== false && c !== true) };
}

// ============================================================
// 2. 编译加载(拦截器先装)
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
  const out = path.join(os.tmpdir(), `points-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

let pointsApiMod;
const reactRef = { current: miniReact() };
const MOCKS = {
  react: reactRef.current,
  '@tarojs/components': Object.assign(Object.create(null), {
    __esModule: true, View: 'View', Text: 'Text', ScrollView: 'ScrollView',
    Input: 'Input', Button: 'Button', Image: 'Image', Picker: 'Picker',
    Textarea: 'Textarea',
  }),
  '@tarojs/taro': Object.assign(Object.create(null), {
    __esModule: true, default: mockTaro, ...mockTaro,
  }),
  './index.module.scss': mockStyles,
  './request': { request: mockRequest },
  '@/config': { __configRef: true },  // 占位: configMod 编译后回填
  '@/api/points': { __pointsRef: true },  // 占位: 编译后回填
  '@/services/auth-service': {
    __esModule: true,
    getSession: () => ({ memberId: '5', phone: '13800000005',
      nickname: '测试五号', accessToken: 'tk-points' }),
    getMemberId: () => '5',
    requireLogin: () => true,
    isLoggedIn: () => true,
    clearSession: () => {},
  },
  '@/services/checkout-service': {
    __esModule: true, default: {
      resetMock: () => {},
      getMockDB: () => ({ products: [], members: [], orders: [] }),
    },
  },
  '@/services/theme-service': {
    __esModule: true,
    applyActiveTheme: async () => {},
    getQuickGridIcon: (_k, d) => d,
  },
  '@/components/NavBar': { __esModule: true, default: () => null },
  '@/api/product': { __esModule: true, ProductAPI: {
    hot: async () => [],
  } },
  '@/api/promotion': { __esModule: true, PromotionAPI: {
    activities: async () => [],
    groupBuyTiers: async () => ({ tiers: [] }),
  } },
  '@/api/member': { __esModule: true, MemberAPI: {
    profile: async () => MOCK_MEMBER,
  } },
  '@/api/wallet': { __esModule: true, WalletAPI: {
    info: async () => ({ currentBalance: 88.5 }),
  } },
  '@/api/credit': { __esModule: true, CreditAPI: {
    quota: async () => ({ availableQuota: 500 }),
  } },
  '@/api/location': { __esModule: true, LocationAPI: {
    nearbyStores: async () => [],
  } },
  '@/api/venue': { __esModule: true, VenueAPI: {
    partners: async () => [],
  } },
  '@/api/order': { __esModule: true, OrderAPI: {
    myOrders: async () => ({ orders: MOCK_ORDERS_RAW, count: 2 }),
  }, ORDER_STATUS_NAME: null },  // 占位: 编译后回填真实 order 模块
  '@/api/auth': { __esModule: true, AuthAPI: { logout: async () => {} } },
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) {
    const hit = MOCKS[request];
    if (hit.__pointsRef) return pointsApiMod;
    if (hit.__configRef) return configMod;
    if (hit.ORDER_STATUS_NAME === null) return orderApiMod;
    return hit;
  }
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  return origLoad.apply(this, arguments);
};

const configMod = compileLoad(CONFIG_SRC, 'config');
pointsApiMod = compileLoad(API_SRC, 'points-api');
const PointsAPI = pointsApiMod.PointsAPI;
beijingTodayRef = configMod.beijingToday;

// order.ts 真实模块(mine 页依赖 ORDER_STATUS_NAME 字典)——直接编译
const orderApiMod = compileLoad(
  path.resolve(__dirname, '..', 'src', 'api', 'order.ts'), 'order-api');
// order.ts 也依赖 ./request(MOCKS 已拦)

const mineMod = compileLoad(MINE_SRC, 'mine');
const pointsPageMod = compileLoad(PONTS_PAGE_SRC, 'points-page');
const homeMod = compileLoad(HOME_SRC, 'home');

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
  console.log('签到/积分体系 前端单元测试(时区/解包/账本口径/折叠/状态中文)');
  console.log('='.repeat(60));

  // ---------- [1-4] 纯函数层(时区修复核心) ----------
  const origNow = Date.now;
  try {
    // UTC 2026-09-13 23:30 → 北京 2026-09-14 07:30(次日)
    Date.now = () => Date.UTC(2026, 8, 13, 23, 30, 0);
    record('纯函数-beijingToday北京口径(UTC23:30→次日)',
      configMod.beijingToday() === '2026-09-14',
      `got=${configMod.beijingToday()}`);
    // UTC 2026-09-13 07:00 → 北京 15:00(同日)
    Date.now = () => Date.UTC(2026, 8, 13, 7, 0, 0);
    record('纯函数-beijingToday同日(UTC7:00→北京15:00)',
      configMod.beijingToday() === '2026-09-13');
    Date.now = () => Date.UTC(2026, 8, 13, 23, 30, 0);
    record('纯函数-beijingDateOffset(0)===today',
      configMod.beijingDateOffset(0) === '2026-09-14');
    record('纯函数-beijingDateOffset(1/6)回退',
      configMod.beijingDateOffset(1) === '2026-09-13'
      && configMod.beijingDateOffset(6) === '2026-09-08');
  } finally {
    Date.now = origNow;
  }
  record('纯函数-beijingToday格式', /^\d{4}-\d{2}-\d{2}$/.test(configMod.beijingToday()));

  // ---------- [5-8] API 层解包 ----------
  MOCK_SIGNED_TODAY = false;
  requests.length = 0;
  const signinRes = await PointsAPI.signin(5);
  record('API-signin解包({success,data}壳)', requests[0].url === '/api/points/signin'
    && requests[0].method === 'POST' && requests[0].data.userId === 5
    && signinRes.pointsEarned === 10 && signinRes.continuousDays === 2
    && signinRes.signDate === beijingTodayRef());

  const recs = await PointsAPI.signinRecords(5, 7);
  record('API-signinRecords映射+isBonus布尔化',
    recs.length === 1 && recs[0].isBonus === true
    && recs[0].signDate === beijingTodayRef());

  const acc = await PointsAPI.account(5);
  record('API-account解包(账本口径)', acc.totalPoints === 110
    && acc.totalEarned === 110 && acc.expiringPoints === 0);

  const logs = await PointsAPI.logs(5, 50);
  record('API-logs映射', logs.length === 1 && logs[0].balance === 110
    && logs[0].refDesc === '每日签到');

  // ---------- [9-11] 积分中心页 ----------
  const reactP = miniReact();
  Object.assign(MOCKS.react, reactP, { default: reactP });
  MOCK_SIGNED_TODAY = true;   // 今日已签场景
  const elPoints = await renderFlushed(pointsPageMod.default, reactP);
  const pointsText = textOf(elPoints);
  record('页面-积分中心今日已签(北京口径)', pointsText.includes('今日已签')
    && pointsText.includes('110'));
  const signinBtn = findAll(elPoints, n => textOf(n) === '今日已签')[0];
  record('页面-签到按钮状态(已签不可点)', !!signinBtn
    && signinBtn.props.onClick === undefined);
  const dayCells = findAll(elPoints, n =>
    typeof n.props.className === 'string'
    && (n.props.className.includes('signed') || n.props.className.includes('dayCell')));
  record('页面-近7日日历渲染', pointsText.includes('近 7 日签到')
    && pointsText.includes('连续签到第 7/14/21 天'));

  // 未签场景: 按钮可点 → switchTab 回首页
  MOCK_SIGNED_TODAY = false;
  const reactP2 = miniReact();
  Object.assign(MOCKS.react, reactP2, { default: reactP2 });
  const elPoints2 = await renderFlushed(pointsPageMod.default, reactP2);
  const goSignBtn = findAll(elPoints2, n => typeof n.props.onClick === 'function'
    && textOf(n) === '去签到')[0];
  switchCalls.length = 0;
  goSignBtn.props.onClick();
  record('页面-未签去签到跳首页', switchCalls.length === 1
    && switchCalls[0] === '/pages/index/index');

  // ---------- [12-16] 我的页(折叠/状态中文/账本口径) ----------
  const reactM = miniReact();
  Object.assign(MOCKS.react, reactM, { default: reactM });
  const elMine = await renderFlushed(mineMod.default, reactM);
  const mineText = textOf(elMine);
  record('页面-订单默认折叠', mineText.includes('我的订单')
    && mineText.includes('共 2 单')
    && !mineText.includes('订单号: ZD-SEED-003')
    && !mineText.includes('订单号: ZD-SEED-006'));
  // 点击标题展开
  const orderHeader = findAll(elMine, n => typeof n.props.onClick === 'function'
    && textOf(n).includes('我的订单'))[0];
  orderHeader.props.onClick();
  await new Promise(r => setTimeout(r, 30));
  const elMineOpen = reactM.__test.rerender();
  const openText = textOf(elMineOpen);
  record('页面-点击展开订单卡', openText.includes('订单号: ZD-SEED-003')
    && openText.includes('订单号: ZD-SEED-006'));
  record('页面-订单状态中文映射',
    openText.includes('待评价') && openText.includes('已完成')
    && openText.includes('待付款') === false ? true
    : (openText.includes('待评价') && openText.includes('已完成')
      && !openText.includes('PENDING') && !openText.includes('RECEIVED')
      && !openText.includes('COMPLETED')));
  record('页面-订单状态无英文码残留',
    !openText.includes('RECEIVED') && !openText.includes('COMPLETED')
    && openText.includes('厂家直供') && openText.includes('代理商: 代理A'));
  record('页面-积分读账本(110而非遗留0)',
    (() => {
      // statItem: children=[statValue(110), statLabel(积分 ›)]——找含"积分 ›"的点击节点
      const statItem = findAll(elMine, n => typeof n.props.onClick === 'function'
        && textOf(n).includes('积分 ›'))[0];
      return !!statItem && statItem.children.length === 2
        && textOf(statItem.children[0]) === '110';
    })());
  // 顶部统计可点击
  const pointsStat = findAll(elMine, n => typeof n.props.onClick === 'function'
    && (textOf(n).includes('积分') || textOf(n.parentElement || {}).includes('积分 ›')))[0];
  navCalls.length = 0;
  if (pointsStat) pointsStat.props.onClick();
  const ordersStat = findAll(elMine, n => typeof n.props.onClick === 'function'
    && (textOf(n).includes('订单') || textOf(n.parentElement || {}).includes('订单 ›')))[0];
  if (ordersStat) ordersStat.props.onClick();
  record('页面-积分/订单统计可点击', navCalls.includes('/pages/points/index')
    && navCalls.includes('/pages/orders/index'));

  // ---------- [17-20] 首页(资产卡/签到流) ----------
  // 场景 A: 已签(北京口径记录) → 幂等
  MOCK_SIGNED_TODAY = true;
  storage['last_signin_date'] = '';   // 清本地兜底
  const reactH = miniReact();
  Object.assign(MOCKS.react, reactH, { default: reactH });
  const elHome = await renderFlushed(homeMod.default, reactH);
  const homeText = textOf(elHome);
  record('页面-资产卡积分显示账本110',
    (() => {
      // assetItem: children=[assetValue(110), assetLabel(积分)]
      const assetItem = findAll(elHome, n => typeof n.props.onClick === 'function'
        && textOf(n) === '110积分')[0];
      return !!assetItem && assetItem.children.length === 2
        && textOf(assetItem.children[0]) === '110';
    })());
  const signEntry = findAll(elHome, n => typeof n.props.onClick === 'function'
    && textOf(n).includes('每日签到'))[0];
  requests.length = 0;
  lastToast = null;
  signEntry.props.onClick();
  await new Promise(r => setTimeout(r, 50));
  record('页面-已签幂等(toast+不发POST)', lastToast
    && lastToast.title === '今日已签到'
    && !requests.some(r => r.url === '/api/points/signin'));

  // 场景 B: 未签 → POST → 成功 toast → 本地存储北京日期
  MOCK_SIGNED_TODAY = false;
  storage['last_signin_date'] = '';
  const reactH2 = miniReact();
  Object.assign(MOCKS.react, reactH2, { default: reactH2 });
  const elHome2 = await renderFlushed(homeMod.default, reactH2);
  const signEntry2 = findAll(elHome2, n => typeof n.props.onClick === 'function'
    && textOf(n).includes('每日签到'))[0];
  requests.length = 0;
  lastToast = null;
  signEntry2.props.onClick();
  await new Promise(r => setTimeout(r, 200));
  record('页面-未签签到流(POST+toast)',
    requests.some(r => r.url === '/api/points/signin' && r.method === 'POST')
    && lastToast && lastToast.title.includes('签到成功 +10 积分'));
  record('页面-签到后本地存储北京日期',
    String(storage['last_signin_date']).includes(beijingTodayRef())
    || String(storage['last_signin_date'] || '').includes(beijingTodayRef()));

  // 资产卡积分点击跳积分中心
  const assetPoints = findAll(elHome2, n => typeof n.props.onClick === 'function'
    && textOf(n) === '110积分')[0];
  navCalls.length = 0;
  if (assetPoints) assetPoints.props.onClick();
  record('页面-资产卡积分跳积分中心',
    navCalls.includes('/pages/points/index'));

  // 汇总
  const pass = results.filter(r => r.ok).length;
  console.log('-'.repeat(60));
  console.log(`通过: ${pass} / ${results.length}`);
  process.exit(pass === results.length ? 0 : 1);
})().catch(e => { console.error('FATAL:', e); process.exit(1); });
