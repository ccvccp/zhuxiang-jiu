/**
 * test-zw.js · 智运·AI智能物流大模型 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-zf.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖:
 *   [API 层 zw.ts]
 *   1. 字典完整性(风险四级/异常四型/理赔四类/验货三态/裁决三态)
 *   2. 映射函数(未知回落)
 *   3. routeDecide 映射(决策+候选)
 *   4. carrierScores 映射(冷启动)
 *   5. eta 映射(在途/已签收)
 *   6. riskAssess 映射(四防+建议)
 *   7. costAnalysis 映射(对比+建议)
 *   8. volumeForecast 映射(权重口径)
 *   9. status 映射(总览四格)
 *   10. feedback 映射(etaWeight 留痕)
 *   11. 请求头注入(X-Role: admin)
 *   [页面层 pages/zw/index.tsx]
 *   12. 六页签结构
 *   13. hero 卡(四格统计)
 *   14. ETA 查询区(输入+按钮)
 *   15. 反馈面板(时效权重提示)
 *   16. 组件确定性
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'zw.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'zw', 'index.tsx');

const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url.includes('/api/logistics-ai/status')) {
    return { success: true, data: {
      module: '智运·AI智能物流大模型', orders: 156,
      signRate: 0.94, avgSignHours: 46.2,
      evolution: { feedbacks: 8, etaWeight: 0.7 } } };
  }
  if (url.includes('/api/logistics-ai/route/decide')) {
    return { success: true, data: {
      decisionId: 3,
      decision: { carrier: 'SF', carrierName: '顺丰速运',
        serviceType: 'express', ruleReason: '零售2件→顺丰特快/标快',
        ruleScore: 80, qualityScore: 100, combinedScore: 88 },
      candidates: [
        { carrier: 'SF', carrierName: '顺丰速运', ruleScore: 80,
          qualityScore: 100, combinedScore: 88 },
        { carrier: 'YT', carrierName: '圆通速递', ruleScore: 75,
          qualityScore: 62, combinedScore: 69.8 }],
      formula: '综合 = 规则分×0.6 + 质量分×0.4' } };
  }
  if (url.includes('/api/logistics-ai/route/carrier-scores')) {
    return { success: true, data: {
      SF: { carrier: 'SF', carrierName: '顺丰速运', score: 100,
        sample: 12, coldStart: false, explain: '...' },
      JD: { carrier: 'JD', carrierName: '京东物流', score: 70,
        sample: 0, coldStart: true, explain: '冷启动' } } };
  }
  if (url.includes('/api/logistics-ai/track/eta/')) {
    return { success: true, data: {
      waybillNo: 'SF1', carrier: 'SF', status: 'transporting',
      remainingHours: 31.4, eta: '2026-09-13T10:00:00',
      basis: 'SF 历史均时效 36h', avgHours: 36, elapsedHours: 10 } };
  }
  if (url.includes('/api/logistics-ai/track/anomalies')) {
    return { success: true, data: [
      { waybillNo: 'SF4', carrier: 'SF', type: 'pickup_timeout',
        severity: 'medium', detail: '下单后 20h 未揽收(阈值 4h)',
        action: '告警仓库' }] };
  }
  if (url.includes('/api/logistics-ai/risk/assess')) {
    return { success: true, data: {
      riskId: 5, damageScore: 60, lossScore: 70, delayScore: 55,
      riskScore: 60.5, riskLevel: 'high',
      factors: ['偏远地区(新疆)+30', '高货值 ¥8000+20'],
      suggestions: ['高风险单: 建议加固包装(木架/双层气泡膜)并足额保价'],
      formula: '0.35×防破损60 + 0.25×防丢失70 + 0.4×防延误55' } };
  }
  if (url.includes('/api/logistics-ai/risk/claims')) {
    return { success: true, data: {
      claimId: 2, claimNo: 'CLM-000002', waybillNo: 'SF-DEMO-001',
      claimTypeName: '破损理赔', claimAmount: 268,
      standard: '保价金额全额赔付', slaDays: 7,
      status: 'pending_review' } };
  }
  if (url.includes('/api/logistics-ai/analysis/cost')) {
    return { success: true, data: {
      byCarrier: [{ carrier: 'YT', count: 40, totalFee: 480,
        avgFee: 12 }, { carrier: 'SF', count: 100, totalFee: 2800,
        avgFee: 28 }],
      byMonth: [{ month: '2026-09', totalFee: 118 }],
      totalFee: 3280, suggestions: ['成本结构均衡, 无议价建议'] } };
  }
  if (url.includes('/api/logistics-ai/analysis/volume-forecast')) {
    return { success: true, data: {
      horizon: 3, historyMonths: 3, recentAvg: 52, fullAvg: 48,
      trendSlope: 4, rows: [{ step: 1, predictedOrders: 54 }],
      weightScheme: '0.6 近期 + 0.4 全期' } };
  }
  if (url.includes('/api/logistics-ai/evolution/feedback') && opts.method === 'POST') {
    return { success: true, data: {
      feedbackId: 9, targetType: 'route_decision', verdict: 'adopted',
      etaWeightDelta: 0.1, etaWeightAfter: 0.8 } };
  }
  if (url.includes('/api/logistics-ai/evolution/feedbacks')) {
    return { success: true, data: [
      { feedbackId: 8, targetType: 'eta', verdict: 'rejected',
        etaWeightAfter: 0.7 }] };
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
  if (request === '@/api/zw') return apiMod;
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
  const out = path.join(os.tmpdir(), `zw-${label}-${process.pid}.js`);
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
  console.log('智运·AI智能物流大模型 前端单元测试');
  console.log('='.repeat(60));

  apiMod = compileLoad(API_SRC, 'api');
  const ZwAPI = apiMod.ZwAPI;

  // [1] 字典
  record('字典-风险四级', Object.keys(apiMod.RISK_LEVEL_NAME).length === 4
    && apiMod.RISK_LEVEL_NAME.extreme === '极高');
  record('字典-异常四型', Object.keys(apiMod.ANOMALY_TYPE_NAME).length === 4
    && apiMod.ANOMALY_TYPE_NAME.stagnation === '运输停滞');
  record('字典-理赔四类', Object.keys(apiMod.CLAIM_TYPE_NAME).length === 4
    && apiMod.CLAIM_TYPE_NAME.stain === '污损');
  record('字典-验货三态', Object.keys(apiMod.INSPECT_RESULT_NAME).length === 3
    && apiMod.INSPECT_RESULT_NAME.shortage === '少收');

  // [2] 回落
  record('映射-未知回落', apiMod.zwRiskLevelName('x') === 'x'
    && apiMod.zwClaimTypeName('y') === 'y');

  // [3] 路由决策映射
  const d = await ZwAPI.routeDecide({ orderType: 'retail', weight: 5,
    pieceCount: 2 });
  record('API-路由决策映射', d.decision.carrierName === '顺丰速运'
    && d.decision.combinedScore === 88
    && d.candidates.length === 2);

  // [4] 质量评分映射
  const sc = await ZwAPI.carrierScores();
  record('API-质量评分映射', sc.SF.score === 100
    && sc.JD.coldStart === true && sc.JD.score === 70);

  // [5] ETA 映射
  const e = await ZwAPI.eta('SF1');
  record('API-ETA映射', e.remainingHours === 31.4
    && e.avgHours === 36 && e.basis.includes('SF'));

  // [6] 风控映射
  const r = await ZwAPI.riskAssess({ weight: 10, pieceCount: 2,
    insuredValue: 8000 });
  record('API-风控映射', r.riskScore === 60.5
    && r.riskLevel === 'high' && r.suggestions.length >= 1);

  // [7] 成本映射
  const c = await ZwAPI.costAnalysis();
  record('API-成本映射', c.totalFee === 3280
    && c.byCarrier[0].carrier === 'YT');

  // [8] 预测映射
  const vf = await ZwAPI.volumeForecast(3);
  record('API-预测映射', vf.recentAvg === 52
    && vf.weightScheme.includes('0.6'));

  // [9] 总览映射
  const st = await ZwAPI.status();
  record('API-总览映射', st.orders === 156
    && st.signRate === 0.94
    && st.evolution.etaWeight === 0.7);

  // [10] 反馈映射
  const fb = await ZwAPI.feedback({ targetType: 'route_decision',
    verdict: 'adopted' });
  record('API-反馈留痕', fb.etaWeightAfter === 0.8
    && fb.etaWeightDelta === 0.1);

  // [11] 请求头
  requests.length = 0;
  await ZwAPI.status();
  record('API-admin头注入', requests.length === 1
    && requests[0].headers['X-Role'] === 'admin'
    && requests[0].headers.Authorization === 'Bearer tok');

  // [12-16] 页面层
  const reactForPage = miniReact();
  Object.assign(MOCKS.react, reactForPage, { default: reactForPage });
  const pageMod = compileLoad(PAGE_SRC, 'page');
  const el = await renderFlushed(pageMod.default, reactForPage);

  const flat = JSON.stringify(el);
  record('页面-六页签', ['总览', '路由', '轨迹', '风控', '分析', '进化']
    .every(t => flat.includes(t)));

  const hero = findAll(el, n => n.props.className === 'heroStats');
  record('页面-hero四格', hero.length === 1
    && textOf(hero[0]).includes('156')
    && textOf(hero[0]).includes('94%'));

  // ETA 查询区(轨迹页签)
  const t0 = reactForPage.__test;
  t0.states[0] = 'track';
  t0.dirty.v = true;
  const elTrack = await renderFlushed(pageMod.default, reactForPage);
  const inputRow = findAll(elTrack, n => n.props.className === 'qaInputRow');
  record('页面-ETA查询区', inputRow.length === 1
    && JSON.stringify(elTrack).includes('运单号'));

  // 进化页签(模拟点击)
  const evoTab = findAll(el, n => typeof n.props.onClick === 'function'
    && textOf(n) === '进化')[0];
  await evoTab.props.onClick();
  const settle = () => new Promise(r => setTimeout(r, 40));
  await settle();
  const elEvo = await renderFlushed(pageMod.default, reactForPage);
  record('页面-反馈面板', JSON.stringify(elEvo).includes('时效权重')
    && JSON.stringify(elEvo).includes('[0.4, 0.8]'));

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
