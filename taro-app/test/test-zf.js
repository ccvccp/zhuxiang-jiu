/**
 * test-zf.js · 智法·AI智能法务大模型 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-zy.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖:
 *   [API 层 zf.ts]
 *   1. 字典完整性(工艺三档/裁决三态/反馈目标五类/数据四级/跨境三区)
 *   2. 映射函数(未知回落)
 *   3. processCheck 映射(判定+工单)
 *   4. creditAssess 映射(评分+欺诈)
 *   5. twin 映射(三重校验+严格度)
 *   6. status 映射(总览四格)
 *   7. feedback 映射(严格度留痕)
 *   8. 请求头注入(X-Role: admin)
 *   [页面层 pages/zf/index.tsx]
 *   9. 六页签结构
 *   10. hero 卡(工艺校验/违规/判例/严格度)
 *   11. 三重校验条
 *   12. 工艺预设按钮(国标依据文案)
 *   13. 判例卡(败诉标记+规则建议)
 *   14. 进化留痕(裁决中文名)
 *   15. 组件确定性
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'zf.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'zf', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url.includes('/api/legal/status')) {
    return { success: true, data: {
      module: '智法·AI智能法务大模型',
      production: { checks: 12, violations: 2 },
      evolution: { feedbacks: 5, adopted: 4, strictness: 1.2 },
      precedents: 4 } };
  }
  if (url.includes('/api/legal/evolution/twin')) {
    return { success: true, data: {
      tripleVerification: {
        physicalDigital: { score: 0.8, explain: '物理×数字: 覆盖 80%' },
        digitalLegal: { score: 1.0, explain: '数字×法律: 证据化 100%' },
        physicalLegal: { score: 0.9, explain: '物理×法律: 无违规 90%' } },
      twinHealth: 90,
      evolution: { strictness: 1.2, clamp: [0.6, 1.4] } } };
  }
  if (url.includes('/api/legal/production/process-check')) {
    return { success: true, data: {
      checkId: 1, batchId: 'B20260903', verdict: 'violation',
      violations: [{ param: 'additive_count', paramName: '食品添加剂(种)',
        value: 2, explain: '食品添加剂(种) 2 越限 [0, 0](GB 2760 蒸馏酒)' }],
      deviations: [], operator: '张三',
      workOrder: { workOrderId: 'WO-B20260903-1', title: '异常处置工单',
        suggestion: '建议锁定批次', disposition: '永不自动',
        evidence: [], legalBasis: ['GB 2760'] },
      checkedAt: '2026-09-11T00:00:00' } };
  }
  if (url.includes('/api/legal/finance/credit-assess')) {
    return { success: true, data: {
      creditId: 1, entityId: 'SUP001', entityName: '济南粮液',
      score: 98, grade: 'A 优', fraudSuspected: false,
      contradictions: [], formula: '0.3×100 + 0.3×100 + 0.4×95' } };
  }
  if (url.includes('/api/legal/evolution/feedback') && opts.method === 'POST') {
    return { success: true, data: {
      feedbackId: 6, targetType: 'process_check', verdict: 'adopted',
      note: '', strictnessDelta: 0.1, strictnessAfter: 1.3,
      createdAt: '2026-09-11T00:00:00' } };
  }
  if (url.includes('/api/legal/evolution/feedbacks')) {
    return { success: true, data: [
      { feedbackId: 5, targetType: 'price_audit', verdict: 'rejected',
        strictnessAfter: 1.2, createdAt: '2026-09-11T00:00:00' },
      { feedbackId: 4, targetType: 'process_check', verdict: 'adopted',
        strictnessAfter: 1.3, createdAt: '2026-09-10T00:00:00' }] };
  }
  if (url.includes('/api/legal/evolution/precedents')) {
    return { success: true, data: [
      { caseId: 'PC001', caseName: '某酒企价格欺诈行政处罚案',
        outcome: '败诉', lossPoint: '虚构原价',
        ruleSuggestion: '15 日窗口划线价校验',
        relatedScene: 'price_audit' }] };
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
      try { await fn(); } catch (_) { /* ignore */ }
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
const mockTaro = { showToast: () => {}, showModal: () => {}, navigateTo: () => {} };
function createElement(type, props, ...children) {
  return { type, props: props || {}, children: children.flat().filter(c => c != null && c !== false && c !== true) };
}
const reactRef = { current: miniReact() };
const MOCKS = {
  react: reactRef.current,
  '@tarojs/components': Object.assign(Object.create(null), {
    __esModule: true, View: 'View', Text: 'Text', ScrollView: 'ScrollView',
    Input: 'Input',
  }),
  '@tarojs/taro': Object.assign(Object.create(null), {
    __esModule: true, default: mockTaro, useDidShow: () => {},
  }),
  './request': { request: mockRequest },
  './index.module.scss': mockStyles,
  '@/services/auth-service': { getSession: () => ({ accessToken: 'tok' }) },
  '@/components/NavBar': { __esModule: true, default: () => null },
};

const origLoad = Module._load;
let apiMod;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  if (request === '@/api/zf') return apiMod;
  return origLoad.apply(this, arguments);
};

function compileLoad(srcPath, label) {
  const source = fs.readFileSync(srcPath, 'utf-8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React,
      target: ts.ScriptTarget.ES2019, esModuleInterop: true,
    },
    fileName: srcPath,
  });
  const out = path.join(os.tmpdir(), `zf-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

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
  console.log('智法·AI智能法务大模型 前端单元测试');
  console.log('='.repeat(60));

  apiMod = compileLoad(API_SRC, 'api');
  const ZfAPI = apiMod.ZfAPI;

  // [1] 字典
  record('字典-工艺三档', Object.keys(apiMod.VERDICT_NAME).length === 3
    && apiMod.VERDICT_NAME.violation === '违规');
  record('字典-反馈目标五类', Object.keys(apiMod.FEEDBACK_TARGET_NAME).length === 5
    && apiMod.FEEDBACK_TARGET_NAME.passport_verify === '护照验证');
  record('字典-数据四级', Object.keys(apiMod.DATA_LEVEL_NAME).length === 4
    && apiMod.DATA_LEVEL_NAME.L4 === '核心(个人信息)');
  record('字典-跨境三区', Object.keys(apiMod.REGION_NAME).length === 3
    && apiMod.REGION_NAME.EU === '欧盟(GDPR)');

  // [2] 回落
  record('映射-未知回落', apiMod.zfVerdictName('x') === 'x'
    && apiMod.zfDataLevelName('y') === 'y');

  // [3] 工艺校验映射
  const pc = await ZfAPI.processCheck({ batchId: 'B1', params: {} });
  record('API-工艺校验映射', pc.verdict === 'violation'
    && pc.violations[0].param === 'additive_count'
    && pc.workOrder.disposition === '永不自动');

  // [4] 信用映射
  const cr = await ZfAPI.creditAssess({ entityId: 'S', entityName: 'x',
    monthlyOrders: 1, inventoryValue: 1, productionCapacity: 1, repaymentRate: 1 });
  record('API-信用映射', cr.score === 98 && cr.grade === 'A 优'
    && cr.fraudSuspected === false);

  // [5] 孪生映射
  const tw = await ZfAPI.twin();
  record('API-孪生映射', tw.twinHealth === 90
    && tw.tripleVerification.digitalLegal.score === 1.0
    && tw.evolution.strictness === 1.2);

  // [6] 总览映射
  const st = await ZfAPI.status();
  record('API-总览映射', st.production.checks === 12
    && st.production.violations === 2
    && st.evolution.strictness === 1.2
    && st.precedents === 4);

  // [7] 反馈映射
  const fb = await ZfAPI.feedback({ targetType: 'process_check', verdict: 'adopted' });
  record('API-反馈留痕', fb.strictnessAfter === 1.3
    && fb.strictnessDelta === 0.1);

  // [8] 请求头
  requests.length = 0;
  await ZfAPI.status();
  record('API-admin头注入', requests.length === 1
    && requests[0].headers['X-Role'] === 'admin'
    && requests[0].headers.Authorization === 'Bearer tok');

  // [9-15] 页面层
  const reactForPage = miniReact();
  Object.assign(MOCKS.react, reactForPage, { default: reactForPage });
  const pageMod = compileLoad(PAGE_SRC, 'page');
  const el = await renderFlushed(pageMod.default, reactForPage);

  const flat = JSON.stringify(el);
  record('页面-六页签', ['总览', '生产', '金融', '数据', '电商', '进化']
    .every(t => flat.includes(t)));

  const hero = findAll(el, n => n.props.className === 'heroStats');
  record('页面-hero四格', hero.length === 1
    && textOf(hero[0]).includes('12')
    && textOf(hero[0]).includes('1.2'));

  const twinRows = findAll(el, n => n.props.className === 'dimRow');
  record('页面-三重校验条', twinRows.length === 3
    && textOf(twinRows[1]).includes('100%'));

  // 工艺页签(切 tab 后)
  const t0 = reactForPage.__test;
  t0.states[0] = 'production';
  t0.dirty.v = true;
  const elProd = await renderFlushed(pageMod.default, reactForPage);
  const prodFlat = JSON.stringify(elProd);
  record('页面-工艺国标依据', prodFlat.includes('GB/T 10781')
    && prodFlat.includes('GB 2760'));

  // 进化页签(数据惰性加载走 onTab → 模拟点击触发异步链)
  const evoTab = findAll(el, n => typeof n.props.onClick === 'function'
    && textOf(n) === '进化')[0];
  await evoTab.props.onClick();   // setTab + 异步 precedents/feedbacks 回填
  const settle = () => new Promise(r => setTimeout(r, 40));
  await settle();
  let elEvo = await renderFlushed(pageMod.default, reactForPage);
  const pcCards = findAll(elEvo, n => n.props.className === 'pcCard');
  record('页面-判例卡', pcCards.length === 1
    && textOf(pcCards[0]).includes('败诉')
    && textOf(pcCards[0]).includes('规则建议'),
    `pcCards=${pcCards.length}`);

  const fbItems = findAll(elEvo, n => n.props.className === 'fbItem');
  record('页面-进化留痕', fbItems.length === 2
    && textOf(fbItems[0]).includes('拒绝')
    && textOf(fbItems[1]).includes('采纳'),
    `fbItems=${fbItems.length}`);

  // 确定性
  const reactForPage2 = miniReact();
  Object.assign(MOCKS.react, reactForPage2, { default: reactForPage2 });
  const el2 = await renderFlushed(pageMod.default, reactForPage2);
  record('页面-渲染确定性',
    JSON.stringify(findAll(el, n => n.props.className === 'heroStat').map(textOf))
    === JSON.stringify(findAll(el2, n => n.props.className === 'heroStat').map(textOf)));

  const pass = results.filter(r => r.ok).length;
  console.log('-'.repeat(60));
  console.log(`通过: ${pass} / ${results.length}`);
  process.exit(pass === results.length ? 0 : 1);
})().catch(e => { console.error('FATAL:', e); process.exit(1); });
