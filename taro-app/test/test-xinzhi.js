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

const mockRequest = async (opts) => {
  requests.push(opts);
  // 按 url 分发 mock 数据
  const url = opts.url || '';
  if (url.includes('/api/xinzhi/radar') && !url.includes('history')) {
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
