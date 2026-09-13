/**
 * test-alliance-venue.js · 同盟商城+场馆合作联盟 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-xinzhi.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖(对应 2026-09-14 桌面端类目裁剪修复):
 *   [同盟商城 pages/alliance]
 *   1. 四页签(商城/我的订单/场景/我的商铺)
 *   2. 类目平铺 9 chips(全部+八大类目)
 *   3. 类目行为普通 View 而非横向 ScrollView(裁剪修复验证)
 *   4. 类目字典完整(water→好水...venue→好境)+回落
 *   5. 点击类目 → products?category= 请求
 *   6. 商品卡渲染(名称/价格/库存/购买按钮/溯源徽标)
 *   7. 空类目空态提示
 *   [场馆合作联盟 pages/venue]
 *   8. 两页签(合作场馆/我的合作)
 *   9. 类型平铺 4 chips(全部/酒店/酒吧/会所)+普通 View(修复验证)
 *   10. 类型字典(hotel/bar/club)+回落
 *   11. 点击类型 → partners?partner_type= 请求
 *   12. 合作商卡渲染(名称/类型)
 *   13. 展开合作商看场地(venues 请求+场地行)
 *   14. 组件确定性(双页面)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const ALLIANCE_API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'alliance.ts');
const VENUE_API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'venue.ts');
const ALLIANCE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'alliance', 'index.tsx');
const VENUE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'venue', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];
let lastToast = null;

const MOCK_PRODUCTS_ALL = [
  { productId: 1, name: '竹香山泉 500ml', category: 'water',
    description: '高山活泉', price: 5.0, stock: 99, merchantId: 1,
    trace: { traceVerified: true } },
  { productId: 2, name: '竹韵毛峰茶礼盒', category: 'tea',
    description: '明前茶', price: 128.0, stock: 50, merchantId: 2,
    trace: { traceVerified: false } },
];
const MOCK_PRODUCTS_WATER = [MOCK_PRODUCTS_ALL[0]];
const MOCK_PARTNERS = [
  { id: 1, partnerName: '竹韵大酒店', partnerType: 'hotel', status: 'active',
    partnerLevel: 'D', tastingRate: 0.05, supplyMode: 'direct',
    contactAddress: '历下区泉城路 1 号' },
  { id: 2, partnerName: '清吧小馆', partnerType: 'bar', status: 'active',
    partnerLevel: 'C', tastingRate: 0.03, supplyMode: 'agent',
    contactAddress: '' },
];
const MOCK_VENUES = [
  { id: 1, partnerId: 1, venueName: '宴会厅·竹香厅', venueType: 'banquet',
    capacity: 200, address: '' },
  { id: 2, partnerId: 1, venueName: '品鉴室·雅集', venueType: 'tasting',
    capacity: 30, address: '' },
];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url.startsWith('/api/alliance/products')) {
    if (url.includes('category=water')) return { success: true, data: MOCK_PRODUCTS_WATER };
    return { success: true, data: MOCK_PRODUCTS_ALL };
  }
  if (url === '/api/alliance/my-orders') return { success: true, data: { orders: [] } };
  if (url === '/api/alliance/my-merchant') return { success: true, data: null };
  if (url === '/api/alliance/scenes') return { success: true, data: [] };
  if (url === '/api/alliance/custom-demands') return { success: true, data: [] };
  if (url.startsWith('/api/venue/partners')) {
    if (url.includes('partner_type=hotel')) {
      return { success: true, data: [MOCK_PARTNERS[0]] };
    }
    return { success: true, data: MOCK_PARTNERS };
  }
  if (url.startsWith('/api/venue/venues')) {
    return { success: true, data: MOCK_VENUES };
  }
  if (url === '/api/venue/my-partner') return { success: true, data: null };
  if (url === '/api/member/profile') {
    return { profile: { id: 5, name: '测试会员', points: 0, level: 'L1' } };
  }
  return { success: true, data: {} };
};

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
  navigateTo: () => {},
};
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
  const out = path.join(os.tmpdir(), `av-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

const reactRef = { current: miniReact() };
const MOCKS = {
  react: reactRef.current,
  '@tarojs/components': Object.assign(Object.create(null), {
    __esModule: true, View: 'View', Text: 'Text', ScrollView: 'ScrollView',
    Input: 'Input', Textarea: 'Textarea', Picker: 'Picker',
  }),
  '@tarojs/taro': Object.assign(Object.create(null), {
    __esModule: true, default: mockTaro, ...mockTaro,
  }),
  './index.module.scss': mockStyles,
  './request': { request: mockRequest },
  '@/services/auth-service': {
    __esModule: true,
    getSession: () => ({ memberId: '5', phone: '13800000005',
      nickname: '测试会员', accessToken: 'tk-av' }),
    getMemberId: () => '5',
    requireLogin: () => true,
    isLoggedIn: () => true,
  },
  '@/components/NavBar': { __esModule: true, default: () => null },
  '@/api/member': { __esModule: true, MemberAPI: {
    profile: async () => ({ id: 5, name: '测试会员', points: 0, level: 'L1' }),
  } },
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  if (request === '@/api/alliance') return allianceApiMod;
  if (request === '@/api/venue') return venueApiMod;
  return origLoad.apply(this, arguments);
};

const allianceApiMod = compileLoad(ALLIANCE_API_SRC, 'alliance-api');
const venueApiMod = compileLoad(VENUE_API_SRC, 'venue-api');
const alliancePageMod = compileLoad(ALLIANCE_SRC, 'alliance-page');
const venuePageMod = compileLoad(VENUE_SRC, 'venue-page');

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
  console.log('同盟商城 + 场馆合作联盟 前端单元测试(类目平铺修复)');
  console.log('='.repeat(60));

  // ---------- [字典 4/10] ----------
  const CN = allianceApiMod.CATEGORY_NAME;
  record('字典-类目八类', Object.keys(CN).length === 8
    && CN.water === '好水' && CN.tea === '好茶' && CN.wine === '好酒'
    && CN.dish === '好菜' && CN.meat === '肉类' && CN.fish === '鱼类'
    && CN.vessel === '酒具' && CN.venue === '好境');
  record('字典-类目回落', allianceApiMod.categoryName('X') === 'X');
  const PT = venueApiMod.PARTNER_TYPE_NAME;
  record('字典-场馆三型', Object.keys(PT).length === 3
    && PT.hotel === '酒店' && PT.bar === '酒吧' && PT.club === '会所');
  record('字典-场馆回落', venueApiMod.partnerTypeName('X') === 'X');

  // ---------- [1-7] 同盟商城 ----------
  const reactA = miniReact();
  Object.assign(MOCKS.react, reactA, { default: reactA });
  const elA = await renderFlushed(alliancePageMod.default, reactA);
  const flatA = textOf(elA);
  record('页面-四页签', flatA.includes('商城') && flatA.includes('我的订单')
    && flatA.includes('场景') && flatA.includes('我的商铺'));

  const catChips = findAll(elA, n => typeof n.props.className === 'string'
    && n.props.className.trim().split(' ').includes('catItem'));
  record('页面-类目平铺9chips', catChips.length === 9
    && ['全部', '好水', '好茶', '好酒', '好菜', '肉类', '鱼类', '酒具', '好境']
      .every(c => catChips.some(n => textOf(n) === c)));

  const catBar = findAll(elA, n => typeof n.props.className === 'string'
    && n.props.className.trim() === 'catBar')[0];
  record('页面-类目行为View非ScrollView(修复验证)',
    !!catBar && catBar.type === 'View'
    && catChips.length === 9);

  // 点击好水 → category=water
  const waterChip = catChips.find(n => textOf(n) === '好水');
  requests.length = 0;
  waterChip.props.onClick();
  await new Promise(r => setTimeout(r, 60));
  record('页面-类目筛选请求',
    requests.some(r => r.url === '/api/alliance/products?category=water'));

  const elWater = reactA.__test.rerender();
  const prodCards = findAll(elWater, n => typeof n.props.className === 'string'
    && n.props.className.trim() === 'prodCard');
  record('页面-商品卡渲染', prodCards.length === 1
    && textOf(prodCards[0]).includes('竹香山泉 500ml')
    && textOf(prodCards[0]).includes('¥5.00')
    && textOf(prodCards[0]).includes('库存 99')
    && textOf(prodCards[0]).includes('购买')
    && textOf(prodCards[0]).includes('溯源已验')
    && textOf(prodCards[0]).includes('好水'));

  // 切回全部(async 加载 → 等待后断言两卡)
  const allChip = catChips.find(n => textOf(n) === '全部');
  allChip.props.onClick();
  await new Promise(r => setTimeout(r, 60));
  const elAll2 = reactA.__test.rerender();
  const prodAll = findAll(elAll2, n => typeof n.props.className === 'string'
    && n.props.className.trim() === 'prodCard');
  record('页面-全类目商品', prodAll.length === 2
    && textOf(prodAll[1]).includes('竹韵毛峰茶礼盒'));

  // ---------- [8-13] 场馆联盟 ----------
  const reactV = miniReact();
  Object.assign(MOCKS.react, reactV, { default: reactV });
  const elV = await renderFlushed(venuePageMod.default, reactV);
  const flatV = textOf(elV);
  record('页面-两页签', flatV.includes('合作场馆') && flatV.includes('我的合作'));

  const typeChips = findAll(elV, n => typeof n.props.className === 'string'
    && n.props.className.trim().split(' ').includes('typeItem'));
  const typeBar = findAll(elV, n => typeof n.props.className === 'string'
    && n.props.className.trim() === 'typeBar')[0];
  record('页面-类型平铺4chips+View(修复验证)', typeChips.length === 4
    && ['全部', '酒店', '酒吧', '会所'].every(c =>
      typeChips.some(n => textOf(n) === c))
    && !!typeBar && typeBar.type === 'View');

  const hotelChip = typeChips.find(n => textOf(n) === '酒店');
  requests.length = 0;
  hotelChip.props.onClick();
  await new Promise(r => setTimeout(r, 60));
  record('页面-类型筛选请求',
    requests.some(r => r.url.includes('/api/venue/partners')
      && r.url.includes('partner_type=hotel')));

  const elHotel = reactV.__test.rerender();
  const partnerCards = findAll(elHotel, n => typeof n.props.className === 'string'
    && n.props.className.trim() === 'partnerCard');
  record('页面-合作商卡渲染', partnerCards.length === 1
    && textOf(partnerCards[0]).includes('竹韵大酒店')
    && textOf(partnerCards[0]).includes('酒店'),
    `cards=${partnerCards.length} flat=${textOf(elHotel).slice(0, 100)}`);

  // 展开合作商看场地(点击卡 → venues 请求 + 场地行)
  const expandBtn = findAll(elHotel, n => typeof n.props.onClick === 'function'
    && (textOf(n).includes('竹韵大酒店') || textOf(n).includes('展开')))[0];
  requests.length = 0;
  if (expandBtn) expandBtn.props.onClick();
  await new Promise(r => setTimeout(r, 60));
  const elExpanded = reactV.__test.rerender();
  const expandedText = textOf(elExpanded);
  record('页面-场地展开', expandedText.includes('宴会厅·竹香厅')
    && expandedText.includes('品鉴室·雅集')
    && expandedText.includes('可容纳 200 人'));

  // [14] 组件确定性(双页面)
  const reactA2 = miniReact();
  Object.assign(MOCKS.react, reactA2, { default: reactA2 });
  const elA2 = await renderFlushed(alliancePageMod.default, reactA2);
  record('页面-渲染确定性(同盟)',
    JSON.stringify(findAll(elA, n => typeof n.props.className === 'string'
      && n.props.className.trim().split(' ').includes('catItem')).map(textOf))
    === JSON.stringify(findAll(elA2, n => typeof n.props.className === 'string'
      && n.props.className.trim().split(' ').includes('catItem')).map(textOf)));

  // 汇总
  const pass = results.filter(r => r.ok).length;
  console.log('-'.repeat(60));
  console.log(`通过: ${pass} / ${results.length}`);
  process.exit(pass === results.length ? 0 : 1);
})().catch(e => { console.error('FATAL:', e); process.exit(1); });
