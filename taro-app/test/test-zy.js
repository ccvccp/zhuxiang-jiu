/**
 * test-zy.js · 智启元·AI智能财务大模型 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-xinzhi.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [API 层 zy.ts]
 *   1. 字典完整性(问答五域/裁决三态/反馈目标/税务结构/风险五级/异常三型)
 *   2. 映射函数(未知回落)
 *   3. status 映射(反馈进化态+异常列表)
 *   4. feedback 映射(趋势权重留痕)
 *   5. forecast 映射(基线口径)
 *   6. taxSimulate 映射(四结构+最优建议)
 *   7. taxHeatmap 映射(五维风险+综合级)
 *   8. cashSchedule 映射(缺口推演)
 *   9. decisionMemo 映射(DCF+敏感性)
 *   10. qa 映射(意图+推理链)
 *   11. 请求头注入(X-Role: admin)
 *   [页面层 pages/zy/index.tsx]
 *   12. 六页签结构
 *   13. 总览 hero 卡(反馈统计四格)
 *   14. 异常卡渲染(检测器名+月度)
 *   15. 问答区(五域提示+输入框)
 *   16. 反馈流留痕(裁决中文名)
 *   17. 组件确定性(同输入多次渲染结构一致)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'zy.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'zy', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url.includes('/api/zy/status')) {
    return { success: true, data: {
      feedbacks: { total: 12, adopted: 8, trendWeight: 0.7 },
      anomalies: [
        { type: 'spike', month: '202608', value: 258000, baseline: 92000,
          detail: '净收入 258000 超均值+3σ', severity: 'high',
          disposition: '仅预警, 处置须人工(永不自动)' }],
      note: '进化全确定性; 建议永不自动执行' } };
  }
  if (url.includes('/api/zy/evolution/feedback') && opts.method === 'POST') {
    return { success: true, data: {
      feedbackId: 13, targetType: 'forecast', verdict: 'adopted',
      note: '预测准', correction: {}, createdAt: '2026-09-11T08:00:00',
      trendDelta: 0.1, trendWeightAfter: 0.8 } };
  }
  if (url.includes('/api/zy/evolution/feedbacks')) {
    return { success: true, data: [
      { feedbackId: 12, targetType: 'forecast', verdict: 'adopted',
        note: '', correction: {}, createdAt: '2026-09-11T07:00:00' },
      { feedbackId: 11, targetType: 'tax_suggestion', verdict: 'rejected',
        note: '', correction: {}, createdAt: '2026-09-10T07:00:00' }] };
  }
  if (url.includes('/api/zy/forecast')) {
    return { success: true, data: {
      horizon: 6,
      rows: [{ step: 1, netAmount: 152000, costAmount: 60000,
               taxAmount: 21000, netProfit: 71000 }],
      basis: { recentAvg: { netAmount: 150000 }, fullAvg: { netAmount: 140000 },
               weightScheme: '0.6 近期 + 0.4 全期', trendApplied: true,
               historyMonths: 8 },
      determinismNote: '同输入同输出' } };
  }
  if (url.includes('/api/zy/tax/simulate')) {
    return { success: true, data: {
      amount: 100000, quantity: 100,
      structures: [
        { structure: 'standard', structureName: '一般销售', vat: 11504,
          consumptionTax: 17699, incomeTax: 8493, total: 37696,
          effectiveRate: 0.38, note: '标准 13% 增值税 + 白酒消费税' },
        { structure: 'bundle', structureName: '组合销售', vat: 11504,
          consumptionTax: 14159, incomeTax: 9844, total: 35507,
          effectiveRate: 0.36, note: '消费税计税基础可分摊' }],
      recommendation: { best: 'bundle', bestName: '组合销售',
        bestTotal: 35507, savingVsWorst: 2189,
        note: '模拟留痕, 变更交易结构须业务侧人工决策' } } };
  }
  if (url.includes('/api/zy/tax/risk-heatmap')) {
    return { success: true, data: {
      month: '202608',
      risks: [
        { risk: '综合税负率偏离', signal: '偏离行业均值 25%', value: 31.2,
          severity: 'attention', severityName: '关注', detail: '当月综合税负率 31.2%' },
        { risk: '退款率(进项转出遗漏)', signal: '阈值 15%', value: 4.5,
          severity: 'low', severityName: '低', detail: '退款率 4.5%' }],
      overall: { level: 'attention', levelName: '关注', topRisk: '综合税负率偏离' },
      note: '风险仅预警; 处置须人工/审批, 永不自动执行' } };
  }
  if (url.includes('/api/zy/evolution/cash-schedule')) {
    return { success: true, data: {
      days: 90, dailyInflow: 5000, dailyOutflow: 4200,
      firstGapDay: null, maxGap: 0,
      rows: [{ day: 1, date: '2026-09-12', netFlow: 800,
               cumulative: 800, gap: 0 }],
      suggestions: ['推演期内现金流为正, 无缺口风险'],
      note: '确定性日均推演; 建议永不自动执行' } };
  }
  if (url.includes('/api/zy/evolution/decision-memo')) {
    return { success: true, data: {
      memoId: 3, type: 'investment',
      assumptions: { initialInvestment: 500000, annualCashFlow: 150000,
        growthRate: 0.05, years: 5, discountRate: 0.08 },
      npv: 130712.5, irrApprox: null, paybackYears: 3.2,
      sensitivities: [
        { discountRate: 0.06, npv: 176000.1 },
        { discountRate: 0.08, npv: 130712.5 },
        { discountRate: 0.1, npv: 89400.2 }],
      conclusion: 'NPV > 0, 财务可行',
      assumptionNote: '所有假设由决策者提供并负责',
      createdAt: '2026-09-11T08:00:00' } };
  }
  if (url.includes('/api/zy/qa')) {
    return { success: true, data: {
      domain: 'revenue', intent: '收入查询',
      answer: '202608 月净收入 ¥258000, 环比 +12.5%',
      reasoning: '取最新月度快照 netAmount, 环比=当月/上月-1',
      dataSnapshot: { month: '202608', netAmount: 258000 } } };
  }
  if (url.includes('/api/zy/evolution/anomalies')) {
    return { success: true, data: [] };
  }
  if (url.includes('/api/zy/evolution/memos')) {
    return { success: true, data: [] };
  }
  return { success: true, data: {} };
};

// 极简 React hooks 运行时(仅支持 useState/useEffect/useCallback 同步路径)
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
  const mod = Object.assign(Object.create(null), {
    __esModule: true,
    default: api,
    ...api,
  });
  return mod;
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
    getSession: () => ({ accessToken: 'tok' }),
  },
  '@/components/NavBar': { __esModule: true, default: () => null },
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  if (request === '@/api/zy') return apiMod;
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
  const out = path.join(os.tmpdir(), `zy-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

const apiMod = compileLoad(API_SRC, 'api');
const ZyAPI = apiMod.ZyAPI;

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
  console.log('智启元·AI智能财务大模型 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1] 字典完整性 ----------
  record('字典-问答五域', Object.keys(apiMod.QA_DOMAIN_NAME).length === 5
    && apiMod.QA_DOMAIN_NAME.revenue === '收入'
    && apiMod.QA_DOMAIN_NAME.anomaly === '异常');
  record('字典-裁决三态', Object.keys(apiMod.VERDICT_NAME).length === 3
    && apiMod.VERDICT_NAME.adopted === '采纳');
  record('字典-反馈目标五类', Object.keys(apiMod.FEEDBACK_TARGET_NAME).length === 5
    && apiMod.FEEDBACK_TARGET_NAME.tax_suggestion === '税务建议');
  record('字典-税务四结构', Object.keys(apiMod.TAX_STRUCTURE_NAME).length === 4
    && apiMod.TAX_STRUCTURE_NAME.cross_border === '跨境零售');
  record('字典-风险五级', Object.keys(apiMod.RISK_LEVEL_NAME).length === 5
    && apiMod.RISK_LEVEL_NAME.critical === '严重');
  record('字典-异常三型', Object.keys(apiMod.ANOMALY_TYPE_NAME).length === 3
    && apiMod.ANOMALY_TYPE_NAME.surge === '频率激增');

  // ---------- [2] 映射回落 ----------
  record('映射-未知回落', apiMod.qaDomainName('x') === 'x'
    && apiMod.verdictName('y') === 'y'
    && apiMod.riskLevelName('z') === 'z');

  // ---------- [3-10] API 映射 ----------
  const st = await ZyAPI.status();
  record('API-总览映射', st.feedbacks.total === 12
    && st.feedbacks.adopted === 8
    && st.feedbacks.trendWeight === 0.7
    && st.anomalies.length === 1
    && st.anomalies[0].severity === 'high');

  const fb = await ZyAPI.feedback({ targetType: 'forecast', verdict: 'adopted' });
  record('API-反馈留痕', fb.feedbackId === 13
    && fb.trendWeightAfter === 0.8 && fb.trendDelta === 0.1);

  const fbs = await ZyAPI.feedbacks(10);
  record('API-反馈列表', fbs.length === 2 && fbs[0].verdict === 'adopted');

  const fc = await ZyAPI.forecast(6);
  record('API-预测映射', fc.horizon === 6
    && fc.rows[0].netAmount === 152000
    && fc.basis.weightScheme.includes('0.6')
    && fc.basis.trendApplied === true);

  const sim = await ZyAPI.taxSimulate({ amount: 100000, quantity: 100 });
  record('API-税负模拟', sim.structures.length === 2
    && sim.recommendation.best === 'bundle'
    && sim.recommendation.bestName === '组合销售'
    && sim.recommendation.savingVsWorst === 2189
    && sim.structures[0].effectiveRate === 0.38);

  const hm = await ZyAPI.taxHeatmap();
  record('API-风险热力图', hm.risks.length === 2
    && hm.risks[0].severityName === '关注'
    && hm.overall.topRisk === '综合税负率偏离');

  const cs = await ZyAPI.cashSchedule(90);
  record('API-资金调度', cs.dailyInflow === 5000
    && cs.firstGapDay === null && cs.maxGap === 0
    && cs.suggestions[0].includes('无缺口'));

  const mm = await ZyAPI.decisionMemo({ type: 'investment', params: {} });
  record('API-决策备忘', mm.npv === 130712.5
    && mm.paybackYears === 3.2
    && mm.sensitivities.length === 3
    && mm.conclusion.includes('可行'));

  const qa = await ZyAPI.qa('本月收入多少');
  record('API-智能问答', qa.domain === 'revenue'
    && qa.answer.includes('258000')
    && qa.reasoning.includes('快照'));

  // ---------- [11] 请求头注入 ----------
  requests.length = 0;
  await ZyAPI.status();
  record('API-admin头注入', requests.length === 1
    && requests[0].headers['X-Role'] === 'admin'
    && requests[0].headers.Authorization === 'Bearer tok');

  // ---------- [12-17] 页面层 ----------
  const reactForPage = miniReact();
  Object.assign(MOCKS.react, reactForPage, {
    default: reactForPage,
  });
  const pageMod = compileLoad(PAGE_SRC, 'page');
  const el = await renderFlushed(pageMod.default, reactForPage);

  // [12] 六页签
  const flat = JSON.stringify(el);
  record('页面-六页签', ['总览', '问答', '分析', '预测', '税务', '进化']
    .every(t => flat.includes(t)));

  // [13] hero 卡(反馈统计四格)
  const heroEls = findAll(el, n => n.props.className === 'heroStats');
  record('页面-总览hero卡', heroEls.length === 1
    && textOf(heroEls[0]).includes('12')
    && textOf(heroEls[0]).includes('0.7'));

  // [14] 异常卡
  const alertEls = findAll(el, n => n.props.className === 'alertCard');
  record('页面-异常卡', alertEls.length === 1
    && textOf(alertEls[0]).includes('金额突增')
    && textOf(alertEls[0]).includes('202608'));

  // [15] 问答区(五域提示——切至 qa 页签后渲染)
  const t0 = reactForPage.__test;
  t0.states[0] = 'qa';   // useState 序 0 = tab
  t0.dirty.v = true;
  const elQa = await renderFlushed(pageMod.default, reactForPage);
  const qaSub = findAll(elQa, n => n.props.className === 'qaSub');
  record('页面-问答五域', qaSub.length === 1
    && textOf(qaSub[0]).includes('收入')
    && textOf(qaSub[0]).includes('异常'));

  // [16] 反馈流(裁决中文名)
  const fbItems = findAll(el, n => n.props.className === 'fbItem');
  record('页面-反馈留痕', fbItems.length === 2
    && textOf(fbItems[0]).includes('采纳')
    && textOf(fbItems[1]).includes('拒绝'));

  // [17] 组件确定性
  const reactForPage2 = miniReact();
  Object.assign(MOCKS.react, reactForPage2, {
    default: reactForPage2,
  });
  const el2 = await renderFlushed(pageMod.default, reactForPage2);
  record('页面-渲染确定性',
    JSON.stringify(findAll(el, n => n.props.className === 'fbItem').map(textOf))
    === JSON.stringify(findAll(el2, n => n.props.className === 'fbItem').map(textOf)));

  // 汇总
  const pass = results.filter(r => r.ok).length;
  console.log('-'.repeat(60));
  console.log(`通过: ${pass} / ${results.length}`);
  process.exit(pass === results.length ? 0 : 1);
})().catch(e => { console.error('FATAL:', e); process.exit(1); });
