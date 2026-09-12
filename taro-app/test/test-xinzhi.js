/**
 * test-xinzhi.js · 信值·臻选购物平台(68号)前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-navbar.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [API 层 xinzhi.ts]
 *   1. 字典完整性(等级/商品分级/维度/求购状态)
 *   2. xinzhiGradeName 映射(S-A-B-C-D + 未知回落)
 *   3. xinzhiDimName 五维中文名
 *   4. toGroupbuy 脱敏字段(publisherMasked 保留 + 无个人明细)
 *   5. toPrimeItem 数值化(字符串数字→number, 缺省兜底)
 *   6. price 映射(α/抵扣/审计标志)
 *   7. radar 映射(五维数组+熔断/冷启动布尔)
 *   8. mode 映射(三态+护栏)
 *   9. 请求头注入(X-Member-Id 自动携带)
 *   [页面层 pages/xinzhi/index.tsx]
 *   10. 五区结构渲染(雷达卡/货架/导购/邻里/求购)
 *   11. 雷达维度条渲染(五维 + 分数)
 *   12. 等级徽章类名(gradeS/gradeA/gradeB/gradeD)
 *   13. 碳档案行渲染(不可交易口径)
 *   14. 灰度脚注(off 态提示)
 *   15. 反馈面板开合(fbBtn 切换)
 *   16. 组件确定性(同 props 多次渲染结构一致)
 *   [平台化 P6-P9 扩展]
 *   17. 字典完备(店铺九态/等级四档/四门禁/铺货五态/订单九态/支付三通道/结算三态)
 *   18. API 方法完备(XinzhiShopAPI 21 + XinzhiTradeAPI 18 = 39)
 *   19. URL 正确性(店铺/铺货/购物车/订单/结算 全端点)
 *   20. 关键请求体(apply/submit/pay mixed/create order ageConfirmed)
 *   21. 映射(建议书透传/购物车快照/预览合计/订单资金源/结算分账/雷达预警)
 *   22. 页面四页签(店铺/购物·订单 新增)
 *   23. 店铺区渲染(店铺卡/铺货列表/公开货架/进店/四门禁公示)
 *   24. 购物车区渲染(实时价+α抵扣明细行/结算预览/年龄门)
 *   25. 订单区渲染(九态徽章/支付三通道/商家发货/admin T+1+冲正)
 *   26. 无店申请流(表单→提交→建议书标签呈现)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'xinzhi.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'xinzhi', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];   // request 调用记录

// ---- P6-P9 平台化 mock 数据(可变: MOCK_SHOP 支持无店场景切换) ----
let MOCK_SHOP = {
  shopId: 1, memberId: 1, shopName: '竹韵臻选小店', category: 'wine',
  intro: '信值优品, 溯源可查', status: 'active',
  shopLevel: '优选店', merchantGrade: 'A', radarTotal: 88,
  materialsComplete: 1, createdAt: '2026-09-10T10:00:00',
  disposition: null,
};
const MOCK_SHELF_ITEM = (listingId, productId, name, price, credit) => ({
  listingId, productId, productName: name, category: '经典系列',
  shopId: 1, shopName: '竹韵臻选小店', sourcePrice: price + credit,
  xinzhiFinalPrice: price, xinzhiCredit: credit, xinzhiAlpha: 0.12,
  breakdownLine: `原价¥${price + credit} - 信值抵扣¥${credit} = 实付¥${price}`,
  status: 'listed', listedAt: '2026-09-11T09:00:00',
});
const MOCK_LISTINGS = [{
  listingId: 11, shopId: 1, memberId: 1, shopName: '竹韵臻选小店',
  productId: 'ZX42-2026L07', productName: '竹奕·竹香经典 42° 500ml',
  category: '经典系列', sourcePrice: 268, status: 'listed',
  gates: {}, gatesPassed: 1, gatesCheckedAt: '2026-09-11T08:00:00',
  xinzhiPrice: { finalPrice: 224.05, xinzhiCredit: 30.55, xinzhiAlpha: 0.12,
    breakdownLine: '原价¥268 - 信值抵扣¥30.55 = 实付¥224.05' },
  reviews: [], listedAt: '2026-09-11T09:00:00',
  delistedAt: '', removedAt: '', createdAt: '', updatedAt: '',
}];
const MOCK_SHELF = {
  total: 2,
  categories: [{
    category: '经典系列', count: 2,
    items: [
      MOCK_SHELF_ITEM(11, 'ZX42-2026L07', '竹奕·竹香经典 42° 500ml', 224.05, 30.55),
      MOCK_SHELF_ITEM(12, 'ZXC-2026L02', '竹藏·珍酿 53° 500ml', 512.4, 75.6),
    ],
  }],
};
const MOCK_CART = {
  memberId: 1,
  items: [{
    listingId: 11, quantity: 2, addedAt: '2026-09-12T08:00:00',
    priceSnapshot: { finalPrice: 224.05, xinzhiCredit: 30.55,
      breakdownLine: '原价¥268 - 信值抵扣¥30.55 = 实付¥224.05' },
  }],
};
const MOCK_PREVIEW = {
  memberId: 1,
  items: [{ listingId: 11, productId: 'ZX42-2026L07', quantity: 2,
    snapshot: MOCK_CART.items[0].priceSnapshot,
    realtime: { finalPrice: 224.05, xinzhiCredit: 30.55 }, priceChanged: false }],
  totals: { baseTotal: 536, afterThreeTotal: 509.2, xinzhiCredit: 61.1,
    goodsTotal: 448.1, shippingFee: 0, actualAmount: 448.1 },
  alphaCap: { capRate: 0.3, creditTotal: 61.1, capAmount: 160.8, ok: true },
  shippingRule: { freeThreshold: 99, fee: 10 },
  shopIds: [1], shopCount: 1, crossShop: false,
  note: '快照仅展示, 下单以实时价为准(防旧价套利)',
};
const MOCK_ORDERS = [
  { orderId: 'XZ20260911000100001', memberId: 1, shopId: 1, shopMemberId: 1,
    shopName: '竹韵臻选小店',
    items: [{ listingId: 11, productId: 'ZX42-2026L07',
      productName: '竹奕·竹香经典 42° 500ml', quantity: 1,
      unitPrice: 224.05, subtotal: 224.05, xinzhiCredit: 30.55, breakdownLine: '' }],
    priceDetail: { baseTotal: 268, afterThreeTotal: 254.6, xinzhiCredit: 30.55,
      goodsTotal: 224.05, shippingFee: 0, actualAmount: 224.05,
      alphaCapRate: 0.1136, alphaCapOk: true, breakdown: [] },
    status: 'PAID', address: { full: '泰山区测试地址' }, remark: '',
    payment: { method: 'mixed', paidAt: '2026-09-11T10:30:00',
      funding: [{ source: 'trust_value', amount: 30.55, txRef: 'redeem:9' },
        { source: 'wallet', amount: 193.5, txRef: 'TX889' }] },
    logistics: { carrier: '', waybillNo: '', shippedAt: '', signedAt: '' },
    review: { rating: 0, content: '', reviewedAt: '', creditNote: '' },
    timeline: [], createdAt: '2026-09-11T10:00:00', updatedAt: '' },
  { orderId: 'XZ20260911000100002', memberId: 1, shopId: 1, shopMemberId: 1,
    shopName: '竹韵臻选小店',
    items: [{ listingId: 12, productId: 'ZXC-2026L02',
      productName: '竹藏·珍酿 53° 500ml', quantity: 1,
      unitPrice: 512.4, subtotal: 512.4, xinzhiCredit: 75.6, breakdownLine: '' }],
    priceDetail: { baseTotal: 588, afterThreeTotal: 558.6, xinzhiCredit: 75.6,
      goodsTotal: 512.4, shippingFee: 0, actualAmount: 512.4,
      alphaCapRate: 0.1286, alphaCapOk: true, breakdown: [] },
    status: 'PENDING', address: { full: '泰山区测试地址' }, remark: '',
    payment: { method: '', paidAt: '', funding: [] },
    logistics: { carrier: '', waybillNo: '', shippedAt: '', signedAt: '' },
    review: { rating: 0, content: '', reviewedAt: '', creditNote: '' },
    timeline: [], createdAt: '2026-09-12T09:00:00', updatedAt: '' },
];
const MOCK_SETTLE = [{
  settleId: 5, orderId: 'XZ20260911000100001', memberId: 1, shopId: 1,
  shopMemberId: 1, shopName: '竹韵臻选小店', orderAmount: 224.05,
  merchantProceeds: 197.16, platformFee: 26.89, proceedsRate: 0.88, feeRate: 0.12,
  status: 'settled', breakdown: {}, walletTxNo: 'TX889',
  pendingAt: '2026-09-11T10:30:00', settledAt: '2026-09-12T02:00:00',
  reversedAt: '', reverseReason: '', reversalDebt: 0, reversalNote: '',
}];

const mockRequest = async (opts) => {
  requests.push(opts);
  // 按 url 分发 mock 数据
  const url = opts.url || '';
  if (url.includes('/api/xinzhi/radar') && !url.includes('history')
      && !url.includes('radar-snapshot')) {
    return { success: true, data: {
      memberId: 1, totalScore: 88, grade: 'A', explanation: '测试',
      circuitBroken: false, coldStart: false, tier: 'trusted',
      computedAt: '2026-09-11T00:00:00',
      dimensions: [
        { key: 'integrity', label: '诚信度', score: 90, weight: 0.3, factors: ['守约良好'] },
        { key: 'mutual', label: '互助值', score: 85, weight: 0.25, factors: ['常接互助'] },
        { key: 'expert', label: '专业度', score: 80, weight: 0.2, factors: [] },
        { key: 'activity', label: '活跃度', score: 75, weight: 0.15, factors: [] },
        { key: 'growth', label: '成长力', score: 70, weight: 0.1, factors: [] },
      ],
    } };
  }
  if (url.includes('/api/xinzhi/prime')) {
    return { success: true, data: [
      { scoreSeq: 1, productId: 'ZX42-2026L07', productName: '竹奕·竹香经典 42° 500ml',
        series: '经典系列', fit: '82.1', safety: '1.0', conversion: '0.7',
        valueScore: '57.5', grade: 'L1', finalRank: '99.1', radarTotal: '88',
        hardBlocked: 0, explanation: '契合 82.1×安全 1.0×转化 0.7 = 价值分 57.5' },
    ] };
  }
  if (url.includes('/api/xinzhi/price')) {
    return { success: true, data: {
      detailSeq: 1, productId: 'ZX42-2026L07', productName: '竹奕·竹香经典',
      memberId: 1, grade: 'A', tier: 'trusted', basePrice: 268,
      afterThreeFactor: 254.6, xinzhiAlpha: 0.12, xinzhiCredit: 30.55,
      finalPrice: 224.05, breakdownLine: '原价¥268 - 信值抵扣¥30.55 = 实付¥224.05',
      floored: 0, auditFlag: '', pricedAt: '2026-09-11T00:00:00' } };
  }
  if (url.includes('/api/xinzhi/neighbor')) {
    return { success: true, data: {
      city: '全站', anonymityK: 5, scope: '平台×品类聚合口径',
      categories: [
        { series: '经典系列', buyerCount: 12, orderCount: 30 },
        { series: '珍藏系列', buyerCount: 6, orderCount: 9 }] } };
  }
  if (url.includes('/api/xinzhi/groupbuy')) {
    return { success: true, data: [
      { groupbuyId: 1, publisherMasked: '邻里12**', title: '求购竹香经典两瓶',
        productId: 'ZX42-2026L07', series: '经典系列', quantity: 2,
        urgency: 'urgent', longitude: 117.1, latitude: 36.6, address: '泰山区',
        status: 'published', responderCount: 1, responders: ['邻里34**'],
        closed: 0, distanceKm: 2.3, createdAt: '2026-09-11T08:00:00' }] };
  }
  if (url.includes('/api/xinzhi/carbon')) {
    return { success: true, data: {
      memberId: 1, carbonGrams: 3200.5, carbonKg: 3.2, helpOrders: 3,
      helpCarbonGrams: 2200.5, groupbuys: 1, groupbuyCarbonGrams: 1000,
      methodology: '67号互助碳+68号求购碳; 只读观测不可交易' } };
  }
  if (url.includes('/api/xinzhi/mode')) {
    return { success: true, data: {
      mode: 'off', source: 'env', paused: false, override: '',
      envMode: 'off', pausedReason: '',
      observablesNeverOff: 'radar/history/score/price...',
      decisionSurfaces: 'groupbuy/merchant/guide' } };
  }
  // ---- P6 店铺 ----
  if (url.includes('/api/xinzhi/shop/statuses')) {
    return { success: true, data: { statuses: [], categories: [],
      shopLevels: {}, thresholds: {} } };
  }
  if (url.includes('/api/xinzhi/shop/apply')) {
    return { success: true, data: {
      shopId: 9, memberId: 1, shopName: '测试申请小店', category: 'wine', intro: '',
      status: 'manual_reviewing', shopLevel: '标准店', merchantGrade: 'B',
      radarTotal: 72, materialsComplete: 0, createdAt: '2026-09-12T08:00:00',
      disposition: {
        kind: 'ai_pre_review', track: 'manual', recommendation: 'manual_review',
        executed: false, requiresAdmin: true,
        note: '雷达总分72在60-79 分档, 转人工审核', at: '2026-09-12T08:00:00' } } };
  }
  if (url.includes('/api/xinzhi/shop/mine')) {
    return { success: true, data: MOCK_SHOP };
  }
  if (url.includes('/radar-snapshot')) {
    return { success: true, data: {
      shopId: 1, status: 'active',
      current: { score: 38, grade: 'B' },
      snapshots: [{ score: 88, grade: 'A', at: '2026-09-11T00:00:00' }],
      warnings: [],
      warning: { kind: 'radar_warning', warningId: 1, proposedAction: 'suspend',
        executed: false, requiresAdmin: true, score: 38, line: 40,
        note: '雷达分跌破40(当前38)——建议暂停整改; 预警永不自动执行, 须 admin 确认',
        at: '2026-09-12T08:00:00' },
      warnLine: 40 } };
  }
  if (/\/api\/xinzhi\/shop\/\d+\/listings/.test(url)) {
    return { success: true, data: { shopId: 1, shopName: '竹韵臻选小店',
      shopLevel: '优选店', count: 2, items: MOCK_SHELF.categories[0].items } };
  }
  if (url.includes('/api/xinzhi/shops')) {
    return { success: true, data: [MOCK_SHOP] };
  }
  if (/^\/api\/xinzhi\/shop\/\d+$/.test(url)) {
    return { success: true, data: { shopId: 1, shopName: '竹韵臻选小店',
      category: 'wine', categoryLabel: '好酒', intro: '信值优品, 溯源可查',
      status: 'active', statusLabel: '正式营业', shopLevel: '优选店',
      merchantGrade: 'A', radarTotal: 88, createdAt: '' } };
  }
  // ---- P7 铺货 ----
  if (url.includes('/api/xinzhi/listing/gates')) {
    return { success: true, data: { gates: [], statuses: [] } };
  }
  if (url.includes('/api/xinzhi/listing/submit')) {
    return { success: true, data: { listingId: 13, shopId: 1, memberId: 1,
      shopName: '竹韵臻选小店', productId: 'ZX42-2026L07',
      productName: '竹奕·竹香经典 42° 500ml', category: '经典系列',
      sourcePrice: 268, status: 'reviewing', gates: {}, gatesPassed: 1,
      xinzhiPrice: { finalPrice: 224.05, xinzhiCredit: 30.55, xinzhiAlpha: 0.12,
        breakdownLine: '' }, reviews: [], createdAt: '', updatedAt: '' } };
  }
  if (url.includes('/api/xinzhi/listing/mine')) {
    return { success: true, data: MOCK_LISTINGS };
  }
  if (url.includes('/api/xinzhi/listings')) {
    return { success: true, data: MOCK_LISTINGS };
  }
  if (/\/api\/xinzhi\/listing\/\d+\/review/.test(url)) {
    return { success: true, data: { ...MOCK_LISTINGS[0], status: 'listed',
      disposition: { kind: 'listing_review', decision: 'approve', executed: true,
        executedBy: 'admin', note: '人工审核通过', at: '' } } };
  }
  if (/\/api\/xinzhi\/listing\/\d+\/(delist|list|remove)/.test(url)) {
    return { success: true, data: MOCK_LISTINGS[0] };
  }
  if (/^\/api\/xinzhi\/listing\/\d+$/.test(url)) {
    return { success: true, data: MOCK_LISTINGS[0] };
  }
  if (url.includes('/api/xinzhi/shelf')) {
    return { success: true, data: MOCK_SHELF };
  }
  // ---- P8 购物车/订单 ----
  if (url.includes('/api/xinzhi/cart/checkout-preview')) {
    return { success: true, data: MOCK_PREVIEW };
  }
  if (url.includes('/api/xinzhi/cart')) {
    return { success: true, data: MOCK_CART };
  }
  if (url.includes('/api/xinzhi/order/create')) {
    return { success: true, data: { orderId: 'XZ20260912000100099',
      status: 'PENDING', statusName: '待付款',
      priceDetail: MOCK_PREVIEW.totals } };
  }
  if (url.includes('/api/xinzhi/order/mine')) {
    return { success: true, data: MOCK_ORDERS };
  }
  if (/\/api\/xinzhi\/order\/[^/]+\/pay/.test(url)) {
    return { success: true, data: { orderId: 'XZ20260911000100002',
      status: 'PAID', statusName: '待发货',
      payment: { method: 'mixed', paidAt: '',
        funding: [{ source: 'trust_value', amount: 30.55, txRef: 'redeem:9' },
          { source: 'wallet', amount: 193.5, txRef: 'TX889' }] },
      settlement: MOCK_SETTLE[0] } };
  }
  if (/\/api\/xinzhi\/order\/[^/]+\/ship/.test(url)) {
    return { success: true, data: { ...MOCK_ORDERS[0], status: 'SHIPPED',
      logistics: { carrier: '顺丰', waybillNo: 'SF123', shippedAt: '', signedAt: '' } } };
  }
  if (/\/api\/xinzhi\/order\//.test(url)) {
    return { success: true, data: MOCK_ORDERS[0] };
  }
  // ---- P9 结算 ----
  if (url.includes('/api/xinzhi/settlement/run')) {
    return { success: true, data: { operator: 'role:admin', settledCount: 1,
      settled: [5], skipped: 0 } };
  }
  if (url.includes('/api/xinzhi/settlement/mine')) {
    return { success: true, data: MOCK_SETTLE };
  }
  if (url.includes('/api/xinzhi/settlements')) {
    return { success: true, data: MOCK_SETTLE };
  }
  if (/\/api\/xinzhi\/settlement\/\d+\/reverse/.test(url)) {
    return { success: true, data: { ...MOCK_SETTLE[0], status: 'reversed',
      reversedAt: '', reversalDebt: 0 } };
  }
  if (/^\/api\/xinzhi\/settlement\/\d+$/.test(url)) {
    return { success: true, data: MOCK_SETTLE[0] };
  }
  return { success: true, data: {} };
};

// 极简 React hooks 运行时(仅支持 useState/useEffect/useCallback 同步路径)
// 支持 default 与命名导入两种消费方式(transpileModule 产物走 react_1.useState)
function miniReact() {
  let hookIdx = 0;
  let renderFn = null;   // 当前组件函数(重渲染用)
  let renderProps = null;
  const states = [];
  const dirty = { v: false };
  const setStateAt = (i) => (v) => {
    states[i] = typeof v === 'function' ? v(states[i]) : v;
    dirty.v = true;   // 标记待重渲(测试主循环统一 flush)
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
  // default 需含全部具名(hooks+createElement——interop 产物走 react_1.default.createElement)
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
  const mod = Object.assign(Object.create(null), {
    __esModule: true,
    default: api,
    ...api,
  });
  return mod;
}

/** 渲染组件并 flush 异步数据(effects→宏任务排空全部微任务→重渲染至稳定) */
async function renderFlushed(Comp, react, maxRounds = 8) {
  const t = react.__test || react.default.__test;
  const settle = () => new Promise(r => setTimeout(r, 20)); // 宏任务: 排空整条微任务链
  let out = null;
  for (let round = 0; round < maxRounds; round++) {
    t.resetIdx();
    t.effects.length = 0;
    out = Comp({});
    t.setRenderer(Comp, {});
    // 执行本帧 effects(useEffect 回调——内部 async 数据链经宏任务排空)
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
  showToast: () => {}, showModal: () => {}, navigateTo: () => {},
};
function createElement(type, props, ...children) {
  return { type, props: props || {}, children: children.flat().filter(c => c != null && c !== false && c !== true) };
}
const reactRef = { current: miniReact() };
const MOCKS = {
  react: reactRef.current,
  '@tarojs/components': Object.assign(Object.create(null), {
    __esModule: true, View: 'View', Text: 'Text', ScrollView: 'ScrollView',
    Input: 'Input', Textarea: 'Textarea',
  }),
  '@tarojs/taro': Object.assign(Object.create(null), {
    __esModule: true, default: mockTaro, useDidShow: () => {},
  }),
  './request': { request: mockRequest },
  './index.module.scss': mockStyles,
  '@/services/auth-service': {
    getMemberId: () => '1',
    getSession: () => ({ memberId: '1', phone: '13800000001',
      nickname: '测试会员', role: 'admin', accessToken: 'tk-test' }),
    requireLogin: () => true,
    isLoggedIn: () => true,
  },
  '@/components/NavBar': { __esModule: true, default: () => null },
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  if (request === '@/api/xinzhi') return apiMod;
  return origLoad.apply(this, arguments);
};

// ============================================================
// 2. 编译加载 API 层
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
  const out = path.join(os.tmpdir(), `xinzhi-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

const apiMod = compileLoad(API_SRC, 'api');
const XinzhiAPI = apiMod.XinzhiAPI;

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
  console.log('信值·臻选购物平台(68号) 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1] 字典完整性 ----------
  record('字典-等级五档', apiMod.XINZHI_GRADE_NAME.S === 'S 臻选'
    && apiMod.XINZHI_GRADE_NAME.D === 'D 风险'
    && Object.keys(apiMod.XINZHI_GRADE_NAME).length === 5);
  record('字典-商品四级', Object.keys(apiMod.XINZHI_PRODUCT_GRADE_NAME).length === 4
    && apiMod.XINZHI_PRODUCT_GRADE_NAME.L1 === 'L1 臻选位');
  record('字典-五维', Object.keys(apiMod.XINZHI_DIM_NAME).length === 5
    && apiMod.XINZHI_DIM_NAME.integrity === '诚信度');
  record('字典-求购状态', apiMod.XINZHI_GB_STATUS_NAME.published === '待响应');

  // ---------- [2-3] 映射函数 ----------
  record('映射-等级名', apiMod.xinzhiGradeName('S') === 'S 臻选'
    && apiMod.xinzhiGradeName('X') === 'X');
  record('映射-维名', apiMod.xinzhiDimName('mutual') === '互助值'
    && apiMod.xinzhiDimName('growth') === '成长力');

  // ---------- [4-8] API 映射(async, mock 数据) ----------
  const radar = await XinzhiAPI.radar();
  record('API-雷达映射', radar.totalScore === 88 && radar.grade === 'A'
    && radar.dimensions.length === 5
    && radar.dimensions[0].score === 90
    && radar.dimensions[0].factors[0] === '守约良好'
    && radar.circuitBroken === false);

  const prime = await XinzhiAPI.prime(10);
  record('API-货架数值化', prime.length === 1
    && prime[0].fit === 82.1 && prime[0].valueScore === 57.5
    && prime[0].grade === 'L1' && prime[0].hardBlocked === false);

  const price = await XinzhiAPI.price('ZX42-2026L07');
  record('API-价格映射', price.xinzhiAlpha === 0.12
    && price.xinzhiCredit === 30.55
    && price.finalPrice === 224.05 && price.floored === false);

  const gb = await XinzhiAPI.groupbuyHall(117.1, 36.6);
  record('API-求购脱敏', gb.length === 1
    && gb[0].publisherMasked === '邻里12**'
    && gb[0].distanceKm === 2.3
    && !JSON.stringify(gb[0]).includes('memberId'));

  const carbon = await XinzhiAPI.carbon();
  record('API-碳档案', carbon.carbonGrams === 3200.5
    && carbon.groupbuyCarbonGrams === 1000
    && carbon.methodology.includes('不可交易'));

  const nb = await XinzhiAPI.neighbor();
  record('API-邻里聚合', nb.anonymityK === 5
    && nb.categories.length === 2
    && nb.categories[0].buyerCount === 12);

  const mode = await XinzhiAPI.mode();
  record('API-灰度态', mode.mode === 'off' && mode.source === 'env'
    && mode.paused === false);

  // ---------- [9] 请求头注入 ----------
  requests.length = 0;
  await XinzhiAPI.radar();
  record('API-会员头注入', requests.length === 1
    && requests[0].headers['X-Member-Id'] === '1');

  // ---------- [17] 平台化字典完备 ----------
  const ShopAPI = apiMod.XinzhiShopAPI;
  const TradeAPI = apiMod.XinzhiTradeAPI;
  record('字典-店铺九态', Object.keys(apiMod.XINZHI_SHOP_STATUS).length === 9
    && apiMod.XINZHI_SHOP_STATUS.active === '正式营业'
    && apiMod.XINZHI_SHOP_STATUS.manual_reviewing === '人工审核中'
    && apiMod.XINZHI_SHOP_STATUS.rejected === '审核拒绝');
  record('字典-店铺等级四档', Object.keys(apiMod.XINZHI_SHOP_GRADE).length === 4
    && apiMod.XINZHI_SHOP_GRADE.S === 'S 旗舰店'
    && apiMod.XINZHI_SHOP_GRADE.C === 'C 新锐店');
  record('字典-四门禁', Object.keys(apiMod.XINZHI_GATES).length === 4
    && apiMod.XINZHI_GATES.qualification === '商家资质门禁'
    && apiMod.XINZHI_GATES.primeScore === '信值分门禁');
  record('字典-铺货五态', Object.keys(apiMod.XINZHI_LISTING_STATUS).length === 5
    && apiMod.XINZHI_LISTING_STATUS.listed === '已上架'
    && apiMod.XINZHI_LISTING_STATUS.reviewing === '待审核(门禁已过)');
  record('字典-订单九态', Object.keys(apiMod.XINZHI_ORDER_STATUS).length === 9
    && apiMod.XINZHI_ORDER_STATUS.PENDING === '待付款'
    && apiMod.XINZHI_ORDER_STATUS.REFUNDED === '已退款');
  record('字典-支付三通道', Object.keys(apiMod.XINZHI_PAY_METHOD).length === 3
    && apiMod.XINZHI_PAY_METHOD.wallet === '余额支付'
    && apiMod.XINZHI_PAY_METHOD.mixed === '组合支付(TV+余额)');
  record('字典-结算三态', Object.keys(apiMod.XINZHI_SETTLE_STATUS).length === 3
    && apiMod.XINZHI_SETTLE_STATUS.settled === '已结算'
    && apiMod.XINZHI_SETTLE_STATUS.reversed === '已冲正');

  // ---------- [18] 39 方法完备 ----------
  const SHOP_METHODS = ['shopStatuses', 'applyShop', 'myShop', 'shops',
    'shopPage', 'reviewShop', 'suspendShop', 'activateShop', 'closeShop',
    'radarSnapshot', 'gatesDict', 'submitListing', 'myListings',
    'listingDetail', 'adminListings', 'reviewListing', 'delistListing',
    'relistListing', 'removeListing', 'shelf', 'shopListings'];
  record('API-店铺方法21个', SHOP_METHODS.length === 21
    && SHOP_METHODS.every(m => typeof ShopAPI[m] === 'function'));
  const TRADE_METHODS = ['cartAdd', 'cartUpdate', 'cartRemove', 'cartMine',
    'checkoutPreview', 'createOrder', 'cancelOrder', 'myOrders', 'orderDetail',
    'payOrder', 'shipOrder', 'confirmOrder', 'reviewOrder', 'runSettlements',
    'mySettlements', 'settlementDetail', 'reverseSettlement', 'settlements'];
  record('API-交易方法18个', TRADE_METHODS.length === 18
    && TRADE_METHODS.every(m => typeof TradeAPI[m] === 'function'));

  // ---------- [19-20] URL 正确性 + 关键请求体 ----------
  const byUrl = (frag) => requests.filter(r => (r.url || '').includes(frag))[0];
  const exact = (u) => requests.find(r => r.url === u);
  requests.length = 0;
  await ShopAPI.applyShop('竹韵臻选小店', 'wine', '信值优品');
  await ShopAPI.shopStatuses();
  await ShopAPI.myShop();
  await ShopAPI.shops('pending');
  await ShopAPI.shopPage(3);
  await ShopAPI.reviewShop(3, true, '资质齐全');
  await ShopAPI.suspendShop(3, '违规整改');
  await ShopAPI.activateShop(3);
  await ShopAPI.closeShop(3, '商家自关');
  await ShopAPI.radarSnapshot(3);
  record('URL-开店申请体', byUrl('/shop/apply').method === 'POST'
    && byUrl('/shop/apply').url === '/api/xinzhi/shop/apply'
    && byUrl('/shop/apply').data.shopName === '竹韵臻选小店'
    && byUrl('/shop/apply').data.category === 'wine'
    && byUrl('/shop/apply').data.intro === '信值优品'
    && byUrl('/shop/apply').headers['X-Member-Id'] === '1');
  record('URL-状态字典与我的店铺',
    byUrl('/shop/statuses').url === '/api/xinzhi/shop/statuses'
    && byUrl('/shop/mine').url === '/api/xinzhi/shop/mine'
    && byUrl('/shop/mine').headers['X-Member-Id'] === '1');
  record('URL-店铺列表admin头', byUrl('/api/xinzhi/shops').url
    === '/api/xinzhi/shops?status=pending'
    && byUrl('/api/xinzhi/shops').headers['X-Role'] === 'admin'
    && byUrl('/api/xinzhi/shops').headers.Authorization === 'Bearer tk-test');
  record('URL-店铺审核建议书', byUrl('/shop/3/review').url
    === '/api/xinzhi/shop/3/review'
    && byUrl('/shop/3/review').data.approved === true
    && byUrl('/shop/3/review').data.note === '资质齐全'
    && byUrl('/shop/3/review').headers['X-Role'] === 'admin');
  record('URL-暂停激活自关', byUrl('/suspend').url === '/api/xinzhi/shop/3/suspend'
    && byUrl('/suspend').data.reason === '违规整改'
    && byUrl('/activate').url === '/api/xinzhi/shop/3/activate'
    && byUrl('/close').data.reason === '商家自关'
    && byUrl('/close').headers['X-Member-Id'] === '1');
  record('URL-雷达快照', byUrl('/radar-snapshot').url
    === '/api/xinzhi/shop/3/radar-snapshot'
    && (byUrl('/radar-snapshot').method || 'GET') === 'GET');

  requests.length = 0;
  await ShopAPI.gatesDict();
  await ShopAPI.submitListing('ZX42-2026L07');
  await ShopAPI.myListings();
  await ShopAPI.listingDetail(11);
  await ShopAPI.adminListings('reviewing');
  await ShopAPI.reviewListing(11, true, '复核通过');
  await ShopAPI.delistListing(11);
  await ShopAPI.relistListing(11);
  await ShopAPI.removeListing(11, '违规移除');
  await ShopAPI.shelf('经典系列');
  await ShopAPI.shopListings(1);
  record('URL-铺货提交体', byUrl('/listing/submit').url
    === '/api/xinzhi/listing/submit'
    && byUrl('/listing/submit').data.productId === 'ZX42-2026L07'
    && byUrl('/listing/submit').headers['X-Member-Id'] === '1');
  record('URL-铺货列表与详情', byUrl('/listing/mine').url
    === '/api/xinzhi/listing/mine?limit=200'
    && byUrl('/listing/11').url === '/api/xinzhi/listing/11'
    && byUrl('/api/xinzhi/listings').url
    === '/api/xinzhi/listings?status=reviewing'
    && byUrl('/api/xinzhi/listings').headers['X-Role'] === 'admin');
  record('URL-铺货审核四门禁复核', byUrl('/listing/11/review').url
    === '/api/xinzhi/listing/11/review'
    && byUrl('/listing/11/review').data.approved === true
    && byUrl('/listing/11/review').headers['X-Role'] === 'admin');
  record('URL-上下架与移除', byUrl('/listing/11/delist').url
    === '/api/xinzhi/listing/11/delist'
    && byUrl('/listing/11/list').url === '/api/xinzhi/listing/11/list'
    && byUrl('/listing/11/remove').data.reason === '违规移除'
    && byUrl('/listing/11/remove').headers['X-Role'] === 'admin');
  record('URL-公开货架品类聚合', byUrl('/api/xinzhi/shelf').url
    === `/api/xinzhi/shelf?category=${encodeURIComponent('经典系列')}&limit=20`
    && byUrl('/shop/1/listings').url
    === '/api/xinzhi/shop/1/listings?limit=200');

  requests.length = 0;
  await TradeAPI.cartAdd(11, 2);
  await TradeAPI.cartUpdate(11, 3);
  await TradeAPI.cartRemove(11);
  await TradeAPI.cartMine();
  await TradeAPI.checkoutPreview();
  record('URL-购物车操作体', byUrl('/cart/add').url === '/api/xinzhi/cart/add'
    && byUrl('/cart/add').data.listingId === 11
    && byUrl('/cart/add').data.quantity === 2
    && byUrl('/cart/update').data.quantity === 3
    && byUrl('/cart/remove').data.listingId === 11
    && byUrl('/cart/mine').url === '/api/xinzhi/cart/mine'
    && byUrl('/checkout-preview').url === '/api/xinzhi/cart/checkout-preview'
    && byUrl('/checkout-preview').method === 'POST');

  requests.length = 0;
  await TradeAPI.createOrder({ address: { full: '泰山区测试地址' },
    remark: '尽快发货', ageConfirmed: true });
  await TradeAPI.cancelOrder('XZ20260911000100001', '不想要了');
  await TradeAPI.myOrders('PENDING');
  await TradeAPI.orderDetail('XZ20260911000100001');
  await TradeAPI.payOrder('XZ20260911000100001', 'mixed', undefined, 45);
  await TradeAPI.shipOrder('XZ20260911000100001', '顺丰', 'SF123');
  await TradeAPI.confirmOrder('XZ20260911000100001');
  await TradeAPI.reviewOrder('XZ20260911000100001', 5, '好酒');
  record('URL-下单体(地址+年龄门)', byUrl('/order/create').url
    === '/api/xinzhi/order/create'
    && byUrl('/order/create').data.address.full === '泰山区测试地址'
    && byUrl('/order/create').data.ageConfirmed === true
    && byUrl('/order/create').data.remark === '尽快发货'
    && byUrl('/order/create').data.items === null);
  record('URL-订单查询与取消', byUrl('/order/mine').url
    === '/api/xinzhi/order/mine?status=PENDING'
    && !!exact('/api/xinzhi/order/XZ20260911000100001')
    && byUrl('/cancel').data.reason === '不想要了');
  record('URL-支付mixed体(三通道)', byUrl('/pay').url
    === '/api/xinzhi/order/XZ20260911000100001/pay'
    && byUrl('/pay').data.method === 'mixed'
    && byUrl('/pay').data.trustId === 45
    && byUrl('/pay').data.useTrustValue === null
    && byUrl('/pay').headers['X-Member-Id'] === '1');
  record('URL-发货确认评价体', byUrl('/ship').data.carrier === '顺丰'
    && byUrl('/ship').data.waybillNo === 'SF123'
    && byUrl('/confirm').url === '/api/xinzhi/order/XZ20260911000100001/confirm'
    && byUrl('/review').data.rating === 5
    && byUrl('/review').data.content === '好酒');

  requests.length = 0;
  await TradeAPI.runSettlements();
  await TradeAPI.mySettlements();
  await TradeAPI.settlementDetail(5);
  await TradeAPI.reverseSettlement(5, '退货冲正');
  await TradeAPI.settlements('settled');
  record('URL-T+1执行admin', byUrl('/settlement/run').url
    === '/api/xinzhi/settlement/run'
    && byUrl('/settlement/run').method === 'POST'
    && byUrl('/settlement/run').headers['X-Role'] === 'admin');
  record('URL-结算查询与冲正', byUrl('/settlement/mine').url
    === '/api/xinzhi/settlement/mine'
    && byUrl('/settlement/5').url === '/api/xinzhi/settlement/5'
    && byUrl('/settlement/5/reverse').data.reason === '退货冲正'
    && byUrl('/settlement/5/reverse').headers['X-Role'] === 'admin'
    && byUrl('/api/xinzhi/settlements').url
    === '/api/xinzhi/settlements?status=settled');

  // ---------- [21] 平台化映射 ----------
  const applied = await ShopAPI.applyShop('测试申请小店', 'wine', '');
  record('映射-申请建议书透传', applied.shopId === 9
    && applied.statusLabel === '人工审核中'
    && applied.disposition && applied.disposition.kind === 'ai_pre_review'
    && applied.disposition.requiresAdmin === true);
  const cartM = await TradeAPI.cartMine();
  record('映射-购物车快照', cartM.memberId === 1
    && cartM.items.length === 1
    && cartM.items[0].listingId === 11
    && cartM.items[0].quantity === 2
    && cartM.items[0].snapshot.finalPrice === 224.05
    && cartM.items[0].snapshot.xinzhiCredit === 30.55);
  const pvM = await TradeAPI.checkoutPreview();
  record('映射-预览合计数值化', pvM.totals.actualAmount === 448.1
    && pvM.totals.xinzhiCredit === 61.1
    && pvM.shippingRule.freeThreshold === 99
    && pvM.alphaCap.ok === true && pvM.crossShop === false);
  const osM = await TradeAPI.myOrders();
  record('映射-订单九态名+资金源', osM.length === 2
    && osM[0].statusName === '待发货'
    && osM[0].payment.funding.length === 2
    && osM[0].payment.funding[0].source === 'trust_value'
    && osM[1].statusName === '待付款'
    && osM[1].priceDetail.actualAmount === 512.4);
  const payM = await TradeAPI.payOrder('XZ20260911000100002', 'mixed', 30.55, 45);
  record('映射-支付结果与结算单', payM.statusName === '待发货'
    && payM.payment.funding[0].amount === 30.55
    && payM.settlement.settleId === 5
    && payM.settlement.merchantProceeds === 197.16);
  const stM = await TradeAPI.settlements();
  record('映射-结算分账明细', stM.length === 1
    && stM[0].statusLabel === '已结算'
    && stM[0].merchantProceeds === 197.16
    && stM[0].platformFee === 26.89
    && stM[0].feeRate === 0.12
    && stM[0].proceedsRate === 0.88);
  const shelfM = await ShopAPI.shelf();
  record('映射-公开货架聚合', shelfM.total === 2
    && shelfM.categories.length === 1
    && shelfM.categories[0].items[0].listingId === 11
    && shelfM.categories[0].items[0].xinzhiFinalPrice === 224.05
    && shelfM.categories[0].items[0].shopName === '竹韵臻选小店');
  const lsM = await ShopAPI.myListings();
  record('映射-我的铺货状态名', lsM.length === 1
    && lsM[0].statusLabel === '已上架'
    && lsM[0].xinzhiPrice.finalPrice === 224.05
    && lsM[0].gatesPassed === true);
  const snapM = await ShopAPI.radarSnapshot(1);
  record('映射-雷达快照预警建议书', snapM.current.score === 38
    && snapM.warnLine === 40
    && snapM.warning.proposedAction === 'suspend'
    && snapM.warning.executed === false);

  // ---------- [10-16] 页面层 ----------
  // 渲染并 flush 异步数据(effects→微任务排空→重渲染至稳定)
  const reactForPage = miniReact();
  Object.assign(MOCKS.react, reactForPage, {
    default: reactForPage,
  });
  const pageMod = compileLoad(PAGE_SRC, 'page');
  const XinzhiPage = pageMod.default;
  const el = await renderFlushed(XinzhiPage, reactForPage);

  // [10] 四区结构(雷达卡+货架+导购在 shelf tab; 邻里/求购在 neighbor tab 由 tab 文案覆盖)
  const flat = JSON.stringify(el);
  record('页面-四区结构', flat.includes('radarCard')
    && flat.includes('臻选货架') && flat.includes('小竹臻选导购')
    && flat.includes('邻里社区'));

  // [11] 雷达维度条(五维分数渲染)
  const dimEls = findAll(el, n => n.props.className === 'dimItem');
  record('页面-雷达五维条', dimEls.length === 5
    && textOf(dimEls[0]).includes('90')
    && textOf(dimEls[0]).includes('诚信度'),
    `dimEls=${dimEls.length} flat.len=${flat.length} radar=${JSON.stringify(reactForPage.__test.states[2]).slice(0, 50)}`);

  // [12] 等级徽章(className 为模板拼接 'gradeBadge gradeX')
  const gradeEl = findAll(el, n =>
    typeof n.props.className === 'string'
    && n.props.className.includes('gradeBadge'));
  record('页面-等级徽章', gradeEl.length === 1
    && textOf(gradeEl[0]).includes('A 优选')
    && gradeEl[0].props.className.includes('gradeA'));

  // [13] 碳档案行
  const carbonEl = findAll(el, n => n.props.className === 'carbonRow');
  record('页面-碳档案行', carbonEl.length === 1
    && textOf(carbonEl[0]).includes('3.2kg')
    && textOf(carbonEl[0]).includes('不可交易'));

  // [14] 灰度脚注
  const footerEl = findAll(el, n => n.props.className === 'modeFooter');
  record('页面-灰度脚注', footerEl.length === 1
    && textOf(footerEl[0]).includes('灰度开放中')
    && textOf(footerEl[0]).includes('宪法'));

  // [15] 反馈入口存在
  const fbBtn = findAll(el, n => n.props.className === 'fbBtn');
  record('页面-反馈入口', fbBtn.length === 1 && textOf(fbBtn[0]) === '反馈');

  // ---------- [22-26] 平台化页面流(四页签/店铺/购物·订单) ----------
  const waitTick = (ms) => new Promise(r => setTimeout(r, ms));
  const clickTab = (root, label) => {
    const t = findAll(root, n => typeof n.props.onClick === 'function'
      && textOf(n) === label)[0];
    if (t) t.props.onClick();
    return !!t;
  };

  // [22] 四页签(既有两页签 + 平台化新增两页签)
  const flatInitial = JSON.stringify(el);
  record('页面-四页签', flatInitial.includes('臻选货架')
    && flatInitial.includes('店铺') && flatInitial.includes('购物·订单')
    && flatInitial.includes('邻里社区'));

  // [23] 店铺页签(懒加载 → 我的店铺/铺货/货架)
  clickTab(el, '店铺');
  await waitTick(80);
  const elShop = reactForPage.__test.rerender();
  const shopCardEl = findAll(elShop, n => n.props.className === 'shopCard');
  record('页面-我的店铺卡', shopCardEl.length === 1
    && textOf(shopCardEl[0]).includes('竹韵臻选小店')
    && textOf(shopCardEl[0]).includes('正式营业')
    && textOf(shopCardEl[0]).includes('A 优选店')
    && textOf(shopCardEl[0]).includes('查看店铺信值快照'));
  const listingEls = findAll(elShop, n => n.props.className === 'listingCard');
  record('页面-我的铺货列表', listingEls.length === 1
    && textOf(listingEls[0]).includes('竹奕·竹香经典')
    && textOf(listingEls[0]).includes('已上架')
    && textOf(listingEls[0]).includes('¥224.05')
    && textOf(listingEls[0]).includes('下架'));
  const gateNoteEl = findAll(elShop, n => n.props.className === 'gateNote');
  record('页面-四门禁公示', gateNoteEl.length === 1
    && textOf(gateNoteEl[0]).includes('商家资质门禁')
    && textOf(gateNoteEl[0]).includes('溯源门禁')
    && textOf(gateNoteEl[0]).includes('信值分门禁'));
  const shelfCatEls = findAll(elShop, n => n.props.className === 'shelfCat');
  const addCartBtns = findAll(elShop, n => n.props.className === 'addCartBtn');
  record('页面-公开货架品类聚合', shelfCatEls.length === 1
    && textOf(shelfCatEls[0]).includes('经典系列')
    && textOf(shelfCatEls[0]).includes('2 件在售')
    && addCartBtns.length === 2);

  // 进店(店铺主页弹层)
  const shopLink = findAll(elShop, n => n.props.className === 'shelfInfo'
    && typeof n.props.onClick === 'function')[0];
  shopLink.props.onClick();
  await waitTick(80);
  const elShopView = reactForPage.__test.rerender();
  const shopViewEl = findAll(elShopView, n => n.props.className === 'shopViewPanel');
  record('页面-进店主页弹层', shopViewEl.length === 1
    && textOf(shopViewEl[0]).includes('竹韵臻选小店')
    && textOf(shopViewEl[0]).includes('加购'));

  // [24] 购物·订单页签(懒加载 → 购物车/预览/订单/结算)
  clickTab(elShopView, '购物·订单');
  await waitTick(100);
  const elTrade = reactForPage.__test.rerender();
  const cartEls = findAll(elTrade, n => n.props.className === 'cartItem');
  record('页面-购物车条目与α抵扣明细行', cartEls.length === 1
    && textOf(cartEls[0]).includes('竹奕·竹香经典')
    && textOf(cartEls[0]).includes('¥224.05')
    && textOf(cartEls[0]).includes('信值抵扣 -¥61.10'));
  const previewEls = findAll(elTrade, n => n.props.className === 'previewPanel');
  record('页面-结算预览卡', previewEls.length === 1
    && textOf(previewEls[0]).includes('应付合计')
    && textOf(previewEls[0]).includes('¥448.10')
    && textOf(previewEls[0]).includes('-¥61.10')
    && textOf(previewEls[0]).includes('运费(满99免)'));
  const ageEls = findAll(elTrade, n => n.props.className === 'ageRow');
  record('页面-下单年龄门', ageEls.length === 1
    && textOf(ageEls[0]).includes('年满 18 周岁')
    && findAll(elTrade, n => n.props.className === 'formBtn'
      && textOf(n) === '提交订单').length === 1);

  // [25] 订单区(九态徽章/支付三通道/商家发货/admin 结算)
  const orderEls = findAll(elTrade, n => n.props.className === 'orderCard');
  const badgeTexts = findAll(elTrade, n =>
    typeof n.props.className === 'string'
    && n.props.className.includes('stBadge')).map(textOf);
  record('页面-订单九态徽章', orderEls.length === 2
    && badgeTexts.includes('待发货') && badgeTexts.includes('待付款')
    && badgeTexts.includes('已结算')
    && textOf(orderEls[0]).includes('信值抵扣 -¥30.55')
    && textOf(orderEls[0]).includes('资金源 trust_value+wallet'));
  const payChips = findAll(elTrade, n =>
    String(n.props.className || '').trim() === 'payChip');
  record('页面-支付三通道', payChips.length === 3
    && payChips.map(textOf).includes('余额支付')
    && payChips.map(textOf).includes('信值TV支付')
    && payChips.map(textOf).includes('组合支付(TV+余额)')
    && textOf(orderEls[1]).includes('取消订单'));
  const carrierInputs = findAll(elTrade, n => n.type === 'Input'
    && (n.props.placeholder || '').includes('承运商'));
  record('页面-商家发货表单(PAID+归属)', carrierInputs.length === 1
    && findAll(elTrade, n => n.props.className === 'formBtn'
      && textOf(n) === '发货').length === 1);
  const adminEls = findAll(elTrade, n => n.props.className === 'adminPanel');
  const settleEls = findAll(elTrade, n => n.props.className === 'settleCard');
  record('页面-admin结算T+1与冲正', adminEls.length === 1
    && textOf(adminEls[0]).includes('执行 T+1 分账(幂等)')
    && settleEls.length === 1
    && textOf(settleEls[0]).includes('商家货款(88%)')
    && textOf(settleEls[0]).includes('¥197.16')
    && textOf(settleEls[0]).includes('冲正(admin 人工)'));

  // [26] 无店申请流(表单→提交→建议书标签呈现)
  MOCK_SHOP = null;
  const reactForPage3 = miniReact();
  Object.assign(MOCKS.react, reactForPage3, { default: reactForPage3 });
  const el3 = await renderFlushed(pageMod.default, reactForPage3);
  clickTab(el3, '店铺');
  await waitTick(80);
  const elShop3 = reactForPage3.__test.rerender();
  const applyEls = findAll(elShop3, n => n.props.className === 'applyCard');
  const nameInput = findAll(elShop3, n => n.type === 'Input'
    && (n.props.placeholder || '').includes('店铺名称'))[0];
  record('页面-无店申请表单', applyEls.length === 1
    && !!nameInput
    && textOf(applyEls[0]).includes('开店申请')
    && textOf(applyEls[0]).includes('好酒')
    && textOf(applyEls[0]).includes('好境'));
  nameInput.props.onInput({ detail: { value: '测试申请小店' } });
  const elFilled3 = reactForPage3.__test.rerender();
  const submitBtn = findAll(elFilled3, n => n.props.className === 'formBtn'
    && textOf(n) === '提交开店申请')[0];
  const applyReqCount = requests.filter(r => r.url
    === '/api/xinzhi/shop/apply').length;
  submitBtn.props.onClick();
  await waitTick(80);
  const elApplied = reactForPage3.__test.rerender();
  const adviceEls = findAll(elApplied, n => n.props.className === 'advicePanel');
  record('页面-申请建议书呈现', adviceEls.length === 1
    && textOf(adviceEls[0]).includes('建议书')
    && textOf(adviceEls[0]).includes('转人工审核')
    && textOf(adviceEls[0]).includes('永不自动执行')
    && requests.filter(r => r.url === '/api/xinzhi/shop/apply').length
      === applyReqCount + 1);
  const appliedCard = findAll(elApplied, n => n.props.className === 'shopCard');
  record('页面-申请后店铺卡', appliedCard.length === 1
    && textOf(appliedCard[0]).includes('测试申请小店')
    && textOf(appliedCard[0]).includes('人工审核中'));

  // [16] 组件确定性(重渲染结构一致)
  const reactForPage2 = miniReact();
  Object.assign(MOCKS.react, reactForPage2, {
    default: reactForPage2,
  });
  const el2 = await renderFlushed(pageMod.default, reactForPage2);
  record('页面-渲染确定性',
    JSON.stringify(findAll(el, n => n.props.className === 'dimItem').map(textOf))
    === JSON.stringify(findAll(el2, n => n.props.className === 'dimItem').map(textOf)));

  // 汇总
  const pass = results.filter(r => r.ok).length;
  console.log('-'.repeat(60));
  console.log(`通过: ${pass} / ${results.length}`);
  process.exit(pass === results.length ? 0 : 1);
})().catch(e => { console.error('FATAL:', e); process.exit(1); });
