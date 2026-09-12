/**
 * test-zd.js · 智单·AI智能订单大模型 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-zy.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [API 层 zd.ts]
 *   1. 字典完整性(问答五域/裁决三态/反馈目标七类/退款分级/退款建议/
 *      异常三型/备忘主题/订单九态)
 *   2. 映射函数(未知回落)
 *   3. 20 方法存在性
 *   4. 织物总览/订单列表/列表数组防御(portraits 非数组 + feedbacks null)
 *   5. 请求体构造(qa/whatif/feedback/memo) + URL/HTTP 方法全景(20 端点)
 *   6. 体检四维/ETA/What-if/退款裁决/异常扫描/参数/检测器/备忘录映射
 *   7. 请求头注入(X-Role: admin + Bearer)
 *   [页面层 pages/zd/index.tsx]
 *   8. 默认导出/标题副标题/四页签
 *   9. 问答交互(点击→answer 插值) / 体检交互(四维分数+建议书)
 *   10. 风控/进化/预测页签渲染 + 组件确定性
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'zd.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'zd', 'index.tsx');

// ============================================================
// 1. Mock 上下文(响应结构对齐 zhuxiang-jiu/backend zd_*_service)
// ============================================================
const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url === '/api/order-ai/status') {
    return { success: true, data: {
      module: '智单·AI智能订单大模型', version: '1.0.0',
      storeMode: 'memory',
      domains: [
        { code: 'fabric', name: '数据织物' },
        { code: 'p0', name: '洞察中枢' },
        { code: 'p1', name: '预测沙盘' },
        { code: 'p2', name: '退款裁决与风险' },
        { code: 'p3', name: '进化闭环' },
      ],
      fabric: { totalOrders: 128, gmv: 258000, dailyDays: 7 },
      constitution: [
        '确定性规则引擎(LLM 禁入判定链, 同输入同输出)',
        '裁决/处置一律建议书(永不自动执行)',
        '预测/评分类响应带 formula 推理链留痕',
        '数字 100% 来自查询层(数字不出现在模板层)',
      ] } };
  }
  if (url === '/api/order-ai/overview') {
    return { success: true, data: {
      hasData: true, totalOrders: 128, paidOrders: 96,
      statusDistribution: {
        PENDING: 12, PAID: 40, SHIPPED: 20, RECEIVED: 8, COMPLETED: 80,
        CANCELLED: 6, CLOSED: 2, RETURNING: 4, REFUNDED: 10 },
      gmv: 258000, refundRate: 0.08, refundedOrders: 10,
      avgOrderValue: 2687.5, avgFulfillmentHours: 56.5,
      fulfillmentSamples: 42,
      memberAggregates: [
        { memberId: 9, orderCount: 12, amount: 19800, share: 0.08 }],
      productAggregates: [
        { productId: 'P101', productName: '竹香陈酿500ml',
          quantity: 66, amount: 39600, share: 0.15 }],
      dailySeries: [{ date: '2026-09-10', orders: 18, gmv: 42000 }],
      note: 'GMV=已支付订单实付合计(退款单计入支付, 由退款率观测)' } };
  }
  if (url.startsWith('/api/order-ai/orders')) {
    return { success: true, data: [
      { orderId: 'ORD-20260912-001', memberId: 9, status: 'PAID',
        items: [{ productId: 'P101', productName: '竹香陈酿500ml',
                  quantity: 2, subtotal: 396 }],
        priceDetail: { actualAmount: 396 },
        createdAt: '2026-09-12T08:30:00' },
      { orderId: 'ORD-20260911-008', memberId: 12, status: 'COMPLETED',
        items: [{ productId: 'P102', productName: '竹香雅集礼盒',
                  quantity: 3, subtotal: 1188 }],
        priceDetail: { actualAmount: 1188 },
        createdAt: '2026-09-11T19:00:00' } ] };
  }
  if (url === '/api/order-ai/qa') {
    return { success: true, data: {
      domain: 'volume', domainName: '单量',
      answer: '当前累计订单 128 单(已支付 96 单), 主链已完成 80 单, '
              + '待付款 12 单; 覆盖会员 35 位。',
      dataSnapshot: { totalOrders: 128, paidOrders: 96, completed: 80 },
      reasoning: '关键词路由→单量域; 取数织物总览(订单 128 单); '
                 + '数字 100% 来自查询层' } };
  }
  if (url === '/api/order-ai/checkup') {
    return { success: true, data: {
      reportId: 3, hasData: true, totalOrders: 128,
      dimensions: [
        { dim: '状态分布健康度', raw: 78.1, score: 83.8,
          explain: '终态占比 78.1%(终态 98/128, 目标 60%)' },
        { dim: '资金安全', raw: 12.5, score: 68.8,
          explain: '待支付+退款中占比 12.5%(12+4/128)' },
        { dim: '履约时效', raw: 56.5, score: 100,
          explain: '平均履约 56.5 小时(样本 42, 基线 72h)' },
        { dim: '退款健康', raw: 7.81, score: 100,
          explain: '退款率 7.81%(10/128, 基线 8%)' }],
      totalScore: 88.15, grade: 'A 优秀',
      formula: '总分 = 0.25×(状态分布+资金安全+履约时效+退款健康); '
               + '状态分布 = 100−|终态占比−60%|×200(确定性)',
      suggestions: ['资金安全维度偏低: 建议核查支付挽回与退款审核节奏'],
      disposition: '体检报告为建议书; 决定权在管理员(不自动执行处置)',
      checkedAt: '2026-09-12T08:00:00' } };
  }
  if (url.startsWith('/api/order-ai/checkups')) {
    return { success: true, data: [
      { reportId: 3, totalScore: 88.15, grade: 'A 优秀',
        checkedAt: '2026-09-12T08:00:00' }] };
  }
  if (url === '/api/order-ai/portrait') {
    return { success: true, data: {
      portraitId: 2, hasData: true, totalOrders: 128,
      topMembers: [
        { memberId: 9, orderCount: 12, amount: 19800, share: 0.08 },
        { memberId: 12, orderCount: 8, amount: 12400, share: 0.05 }],
      topProducts: [
        { productId: 'P101', productName: '竹香陈酿500ml',
          quantity: 66, amount: 39600, share: 0.15 }],
      timeBuckets: [
        { bucket: '凌晨(0-6时)', count: 5, share: 0.04 },
        { bucket: '上午(6-12时)', count: 30, share: 0.23 },
        { bucket: '下午(12-18时)', count: 48, share: 0.38 },
        { bucket: '晚间(18-24时)', count: 45, share: 0.35 }],
      peakBucket: '下午(12-18时)',
      note: '口径: 会员/商品按已支付金额聚合; 时段按 createdAt 小时分桶',
      disposition: '画像为观测建议; 精准营销等动作须管理员确认',
      generatedAt: '2026-09-12T09:30:00' } };
  }
  if (url.startsWith('/api/order-ai/portraits')) {
    // 非数组 data → 前端须防御为 []
    return { success: true, data: { oops: 1 } };
  }
  if (url === '/api/order-ai/eta') {
    return { success: true, data: {
      etaHours: 58.2, sampleSize: 42, recentAvg: 60.5, fullAvg: 55.2,
      recentWeight: 0.6,
      formula: 'ETA = 0.6×近3单履约均值(60.5h) + 0.4×全期均值(55.2h)'
               + '(确定性加权, 同输入同输出)',
      note: '预测为确定性公式; 实际调度决策由管理员裁定' } };
  }
  if (url === '/api/order-ai/whatif') {
    return { success: true, data: {
      baseline: { paidOrders: 96, gmv: 258000, avgOrderValue: 2687.5,
                  avgFulfillmentHours: 56.5, fulfillmentSamples: 42 },
      assumptions: { shipDelayDays: 1, cancelRateDelta: 0.1, aovDelta: 0 },
      scenario: { projectedFulfillmentHours: 80.5,
                  overtimeRiskRatio: 0.12, estOvertimeOrders: 11.4,
                  effectiveOrders: 86.4, gmv: 232200, avgOrderValue: 2687.5 },
      impacts: { gmvDelta: -25800, ordersDelta: -9.6,
                 fulfillmentHoursDelta: 24 },
      mitigations: [
        '履约预计 80.5 小时超 72h 基线(约 11.4 单超时风险): '
        + '建议加急排产/增补运力预案',
        '取消率上升: 建议核查支付超时策略与外呼挽回'],
      formula: 'GMV\' = GMV×(1−取消率变动)×(1+客单价变动); '
               + '履约\' = 平均履约+延迟天数×24h(线性口径, 确定性)',
      disposition: '推演结论为建议书; 不自动执行任何策略调整' } };
  }
  if (url.startsWith('/api/order-ai/forecast')) {
    return { success: true, data: {
      periods: 12,
      rows: [
        { step: 1, date: '2026-09-13', forecastVolume: 18.5 },
        { step: 2, date: '2026-09-14', forecastVolume: 19.2 }],
      basis: { recentAvg: 18.33, fullAvg: 16.5,
               weightScheme: '0.6 近期 + 0.4 全期',
               trendSlope: 0.65, trendDecay: 0.5, historyDays: 7 },
      formula: '基线 = 0.6×近3日单量均值 + 0.4×全期均值; '
               + '预测 = max(0, 基线 + 斜率×步长×0.5)(确定性)',
      determinismNote: '同输入同输出(确定性公式, LLM 禁入)' } };
  }
  if (url.startsWith('/api/order-ai/refund-score/')) {
    const oid = url.split('/refund-score/')[1];
    return { success: true, data: {
      orderId: oid, memberId: 9, amount: 3960,
      factors: [
        { factor: '金额异常度', code: 'amount', raw: 2.5, score: 75,
          explain: '本单 ¥3960 vs 会员历史均值 ¥1584(倍数 2.5×)' },
        { factor: '退款频次', code: 'refundFreq', raw: 0.18, score: 36,
          explain: '会员历史退款率 18%(2/11)' },
        { factor: '会员风险', code: 'memberRisk', raw: 0.27, score: 42,
          explain: '会员退货比 27.27%, 本单消费积分 300(扣回风险)' },
        { factor: '商品域风险', code: 'productRisk', raw: 12, score: 100,
          explain: '单品最大数量 12(囤货阈值 10)' }],
      weights: { amount: 0.3, refundFreq: 0.3,
                 memberRisk: 0.2, productRisk: 0.2 },
      score: 62.9, level: 'high', levelName: '高风险',
      suggestion: 'reject', suggestionName: '建议驳回退款(高风险)',
      reasoning: ['四因子观测(各 0-100, 确定性映射)',
                  '加权总分 62.9 → 高风险 → 建议驳回退款'],
      formula: '退款风险分 = 0.30×金额异常 + 0.30×退款频次 + '
               + '0.20×会员风险 + 0.20×囤货风险(确定性)',
      disposition: '退款裁决为建议书; 决定权在管理员(不自动执行退款)' } };
  }
  if (url === '/api/order-ai/anomaly-scan') {
    return { success: true, data: {
      scannedAt: '2026-09-12T09:00:00', totalOrders: 128, anomalyCount: 2,
      anomalies: [
        { anomalyId: 5, type: 'instant_refund', typeName: '秒退款',
          orderId: 'ORD-20260911-008', memberId: 12, level: 'high',
          detail: '支付后 5.5 小时即申请退款(阈值 24.0h)',
          suggestion: '建议核查会员退款历史与商品质量(建议书不拦截)',
          detectedAt: '2026-09-12T09:00:00' },
        { anomalyId: 6, type: 'bulk_stockpile', typeName: '大额囤货',
          orderId: 'ORD-20260910-003', memberId: 9, level: 'mid',
          detail: '单品数量 12(阈值 10) / 单额 ¥11880(阈值 ¥10000.0)',
          suggestion: '建议核查囤货动机与限购策略(建议书不拦截)',
          detectedAt: '2026-09-12T09:00:00' }],
      rules: [
        '高频下单: 同会员 24h 内 ≥3 单',
        '大额囤货: 单品数量 ≥10 或单额 ≥ ¥10000.0',
        '秒退款: 支付后 24.0h 内申请退款'],
      disposition: '异常处置为建议书; 不自动拦截任何订单',
      note: '三类规则确定性检测(同输入同输出, LLM 禁入)' } };
  }
  if (url.startsWith('/api/order-ai/anomalies')) {
    return { success: true, data: [
      { anomalyId: 6, type: 'bulk_stockpile', typeName: '大额囤货',
        orderId: 'ORD-20260910-003', memberId: 9, level: 'mid',
        detail: '单品数量 12(阈值 10) / 单额 ¥11880',
        suggestion: '建议核查囤货动机与限购策略',
        detectedAt: '2026-09-12T09:00:00' }] };
  }
  if (url === '/api/order-ai/feedback') {
    return { success: true, data: {
      feedbackId: 13, targetType: 'eta_forecast', verdict: 'adopted',
      verdictName: '采纳', note: '预测准',
      feedbackAt: '2026-09-12T09:10:00',
      etaRecentWeightAfter: 0.63 } };
  }
  if (url.startsWith('/api/order-ai/feedbacks')) {
    // null data → 前端须防御为 []
    return { success: true, data: null };
  }
  if (url === '/api/order-ai/params') {
    return { success: true, data: {
      etaRecentWeight: 0.63, updatedAt: '2026-09-12T09:10:00',
      default: 0.6, clamp: [0.4, 0.8],
      multipliers: { adopted: 1.05, corrected: 0.95, rejected: 0.9 },
      note: '反馈闭环驱动 etaRecentWeight 学习; '
            + '安全阀 clamp [0.4, 0.8], 权重永不出界' } };
  }
  if (url === '/api/order-ai/detect') {
    return { success: true, data: {
      alertCount: 1,
      alerts: [{ type: 'spike', metric: '日单量', date: '2026-09-12',
                 value: 52, baseline: 16.5,
                 detail: '日单量 52 超阈值(μ=16.5, σ=3.2, μ+3σ)' }],
      days: 8,
      detectors: [
        'spike: >μ+3σ 且 μ≥5(单量)',
        'drop: <μ−3σ 且降幅 ≥20(PAID 停滞量)',
        'surge: ×3 且样本 ≥20(取消量)'],
      formula: '三检测器作用于织物日时序(μ±3σ/×3 确定性阈值, LLM 禁入)',
      disposition: '检测为观测告警; 处置走建议书, 不自动干预订单' } };
  }
  if (url === '/api/order-ai/memo') {
    return { success: true, data: {
      memoId: 4, topic: 'promotion_prep', topicName: '大促备货',
      notes: '双11备货',
      dataSnapshot: { dailyAvg: 16.5, itemsPerOrder: 2.2,
                     estPromoOrders: 346.5, suggestedStock: 915 },
      assumptions: [
        '假设: 大促周期 7 天(可按实际档期调整)',
        '假设: 大促流量为日常 3 倍(历史无大促样本, 无实测校准)',
        '假设: 备货安全余量 1.2(防断货, 不含在途)'],
      recommendation: '近 7 日日均 16.5 单, 均件数 2.2; '
                      + '大促预估 346.5 单, 建议备货 915 件(建议书, 人工核定)',
      disposition: '决策备忘录为建议书; 决定权在管理员(不自动执行)',
      createdAt: '2026-09-12T09:20:00' } };
  }
  if (url.startsWith('/api/order-ai/memos')) {
    return { success: true, data: [
      { memoId: 4, topic: 'promotion_prep', topicName: '大促备货',
        recommendation: '建议备货 915 件(建议书, 人工核定)',
        createdAt: '2026-09-12T09:20:00' }] };
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
let apiMod = null;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  if (request === '@/api/zd') return apiMod;
  return origLoad.apply(this, arguments);
};

// ============================================================
// 2. 编译加载 TS 源(内存 transpile → tmpdir require)
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
  const out = path.join(os.tmpdir(), `zd-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

apiMod = compileLoad(API_SRC, 'api');
const ZdAPI = apiMod.ZdAPI;

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
  console.log('智单·AI智能订单大模型 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1] 字典完整性 ----------
  record('字典-问答五域', Object.keys(apiMod.QA_DOMAIN_NAME).length === 5
    && apiMod.QA_DOMAIN_NAME.volume === '单量'
    && apiMod.QA_DOMAIN_NAME.gmv === 'GMV'
    && apiMod.QA_DOMAIN_NAME.anomaly === '异常');
  record('字典-裁决三态', Object.keys(apiMod.VERDICT_NAME).length === 3
    && apiMod.VERDICT_NAME.adopted === '采纳'
    && apiMod.VERDICT_NAME.corrected === '修正'
    && apiMod.VERDICT_NAME.rejected === '拒绝');
  record('字典-反馈目标七类', Object.keys(apiMod.FEEDBACK_TARGET_NAME).length === 7
    && apiMod.FEEDBACK_TARGET_NAME.eta_forecast === '履约ETA'
    && apiMod.FEEDBACK_TARGET_NAME.refund_score === '退款裁决');
  record('字典-退款分级', Object.keys(apiMod.REFUND_LEVEL_NAME).length === 3
    && apiMod.REFUND_LEVEL_NAME.low === '低风险'
    && apiMod.REFUND_LEVEL_NAME.high === '高风险');
  record('字典-退款裁决建议', Object.keys(apiMod.REFUND_SUGGESTION_NAME).length === 3
    && apiMod.REFUND_SUGGESTION_NAME.approve === '建议同意退款'
    && apiMod.REFUND_SUGGESTION_NAME.reject === '建议驳回退款');
  record('字典-异常三型', Object.keys(apiMod.ANOMALY_TYPE_NAME).length === 3
    && apiMod.ANOMALY_TYPE_NAME.high_frequency === '高频下单'
    && apiMod.ANOMALY_TYPE_NAME.instant_refund === '秒退款');
  record('字典-备忘录主题', Object.keys(apiMod.MEMO_TOPIC_NAME).length === 2
    && apiMod.MEMO_TOPIC_NAME.promotion_prep === '大促备货'
    && apiMod.MEMO_TOPIC_NAME.timeout_policy === '超时策略');
  record('字典-订单九态', Object.keys(apiMod.ORDER_STATUS_NAME).length === 9
    && apiMod.ORDER_STATUS_NAME.PENDING === '待付款'
    && apiMod.ORDER_STATUS_NAME.COMPLETED === '已完成'
    && apiMod.ORDER_STATUS_NAME.REFUNDED === '已退款');

  // ---------- [2] 映射回落 ----------
  record('映射-未知回落', apiMod.qaDomainName('x') === 'x'
    && apiMod.verdictName('y') === 'y'
    && apiMod.refundLevelName('z') === 'z'
    && apiMod.refundSuggestionName('w') === 'w'
    && apiMod.anomalyTypeName('v') === 'v'
    && apiMod.memoTopicName('u') === 'u'
    && apiMod.orderStatusName('t') === 't'
    && apiMod.feedbackTargetName('s') === 's');

  // ---------- [3] 20 方法存在性 ----------
  const METHODS = [
    'status', 'overview', 'orders', 'qa', 'checkup', 'checkups',
    'portrait', 'portraits', 'eta', 'whatif', 'forecast',
    'refundScore', 'anomalyScan', 'anomalies',
    'feedback', 'feedbacks', 'params', 'detect', 'memo', 'memos'];
  record('API-20方法完备', METHODS.length === 20
    && METHODS.every(m => typeof ZdAPI[m] === 'function'));

  // ---------- [4] 织物底座映射 ----------
  const st = await ZdAPI.status();
  record('API-模块状态', st.module === '智单·AI智能订单大模型'
    && st.domains.length === 5
    && st.fabric.totalOrders === 128
    && st.constitution.length === 4);

  const ov = await ZdAPI.overview();
  record('API-织物总览', ov.totalOrders === 128 && ov.gmv === 258000
    && ov.statusDistribution.PENDING === 12
    && ov.avgFulfillmentHours === 56.5
    && Array.isArray(ov.dailySeries));

  const ord = await ZdAPI.orders(10);
  record('API-订单列表', ord.length === 2
    && ord[0].orderId === 'ORD-20260912-001'
    && ord[1].priceDetail.actualAmount === 1188);

  const ps = await ZdAPI.portraits(5);
  const fbs = await ZdAPI.feedbacks(5);
  record('API-列表数组防御', Array.isArray(ps) && ps.length === 0
    && Array.isArray(fbs) && fbs.length === 0);

  // ---------- [5] 请求体构造 ----------
  requests.length = 0;
  const qa = await ZdAPI.qa('现在有多少订单');
  record('API-问答请求体', requests[0].url === '/api/order-ai/qa'
    && requests[0].method === 'POST'
    && requests[0].data.text === '现在有多少订单'
    && qa.domain === 'volume' && qa.answer.includes('128'));

  requests.length = 0;
  const wi = await ZdAPI.whatif({
    shipDelayDays: 1, cancelRateDelta: 0.1, aovDelta: 0 });
  record('API-WhatIf请求体', requests[0].url === '/api/order-ai/whatif'
    && requests[0].method === 'POST'
    && requests[0].data.shipDelayDays === 1
    && requests[0].data.cancelRateDelta === 0.1
    && requests[0].data.aovDelta === 0
    && wi.scenario.projectedFulfillmentHours === 80.5
    && wi.mitigations.length === 2);

  requests.length = 0;
  const fb = await ZdAPI.feedback('eta_forecast', 'adopted', '预测准');
  record('API-反馈请求体', requests[0].url === '/api/order-ai/feedback'
    && requests[0].method === 'POST'
    && requests[0].data.targetType === 'eta_forecast'
    && requests[0].data.verdict === 'adopted'
    && requests[0].data.note === '预测准'
    && fb.feedbackId === 13 && fb.etaRecentWeightAfter === 0.63);

  requests.length = 0;
  await ZdAPI.feedback('checkup', 'rejected');
  record('API-反馈默认note', requests[0].data.note === ''
    && requests[0].data.targetType === 'checkup');

  requests.length = 0;
  const mm = await ZdAPI.memo('promotion_prep', '双11备货');
  record('API-备忘录请求体', requests[0].url === '/api/order-ai/memo'
    && requests[0].method === 'POST'
    && requests[0].data.topic === 'promotion_prep'
    && requests[0].data.notes === '双11备货'
    && mm.memoId === 4 && mm.topicName === '大促备货'
    && mm.recommendation.includes('915'));

  // ---------- [6] P0-P3 映射 ----------
  const ck = await ZdAPI.checkup();
  record('API-体检四维', ck.dimensions.length === 4
    && ck.totalScore === 88.15 && ck.grade === 'A 优秀'
    && ck.formula.includes('0.25')
    && ck.dimensions[0].dim === '状态分布健康度');

  const cks = await ZdAPI.checkups(5);
  record('API-体检历史', cks.length === 1 && cks[0].reportId === 3);

  const pt = await ZdAPI.portrait();
  record('API-三维画像', pt.topMembers.length === 2
    && pt.topProducts[0].productName === '竹香陈酿500ml'
    && pt.timeBuckets.length === 4
    && pt.peakBucket === '下午(12-18时)');

  const eta = await ZdAPI.eta();
  record('API-ETA预测', eta.etaHours === 58.2 && eta.sampleSize === 42
    && eta.recentWeight === 0.6 && eta.formula.includes('0.6'));

  const fc = await ZdAPI.forecast(12);
  record('API-单量预测', fc.periods === 12 && fc.rows.length === 2
    && fc.basis.recentAvg === 18.33
    && fc.rows[0].forecastVolume === 18.5);

  requests.length = 0;
  const rf = await ZdAPI.refundScore('ORD-20260910-003');
  record('API-退款裁决', requests[0].url
    === '/api/order-ai/refund-score/ORD-20260910-003'
    && rf.factors.length === 4 && rf.level === 'high'
    && rf.suggestion === 'reject' && rf.score === 62.9
    && rf.factors[3].code === 'productRisk');

  requests.length = 0;
  const sc = await ZdAPI.anomalyScan();
  record('API-异常扫描', requests[0].url === '/api/order-ai/anomaly-scan'
    && requests[0].method === 'POST'
    && sc.anomalyCount === 2 && sc.anomalies.length === 2
    && sc.rules.length === 3
    && sc.anomalies[0].type === 'instant_refund');

  const an = await ZdAPI.anomalies(5);
  record('API-异常历史', an.length === 1 && an[0].anomalyId === 6);

  const pm = await ZdAPI.params();
  record('API-参数视图', pm.etaRecentWeight === 0.63
    && pm.clamp[0] === 0.4 && pm.clamp[1] === 0.8
    && pm.multipliers.adopted === 1.05);

  const dt = await ZdAPI.detect();
  record('API-三检测器', dt.alertCount === 1 && dt.alerts.length === 1
    && dt.alerts[0].type === 'spike' && dt.detectors.length === 3);

  const mms = await ZdAPI.memos(5);
  record('API-备忘录历史', mms.length === 1 && mms[0].memoId === 4);

  // ---------- [7] URL/HTTP 方法全景(20 端点) ----------
  requests.length = 0;
  await ZdAPI.status();
  await ZdAPI.overview();
  await ZdAPI.orders(10);
  await ZdAPI.qa('q');
  await ZdAPI.checkup();
  await ZdAPI.checkups(5);
  await ZdAPI.portrait();
  await ZdAPI.portraits(5);
  await ZdAPI.eta();
  await ZdAPI.whatif({ shipDelayDays: 0, cancelRateDelta: 0, aovDelta: 0 });
  await ZdAPI.forecast(6);
  await ZdAPI.refundScore('ORD-1');
  await ZdAPI.anomalyScan();
  await ZdAPI.anomalies(5);
  await ZdAPI.feedback('whatif', 'corrected', 'n');
  await ZdAPI.feedbacks(5);
  await ZdAPI.params();
  await ZdAPI.detect();
  await ZdAPI.memo('timeout_policy', 'n');
  await ZdAPI.memos(5);
  const urls = requests.map(r => r.url);
  const postUrls = requests.filter(r => r.method === 'POST')
    .map(r => r.url).sort();
  record('API-20端点URL全景', requests.length === 20
    && urls.every(u => u.startsWith('/api/order-ai/'))
    && urls.filter(u => u.includes('/api/order-ai/refund-score/')).length === 1
    && postUrls.length === 5
    && postUrls.join(',').includes('/api/order-ai/qa')
    && postUrls.join(',').includes('/api/order-ai/whatif')
    && postUrls.join(',').includes('/api/order-ai/anomaly-scan')
    && postUrls.join(',').includes('/api/order-ai/feedback')
    && postUrls.join(',').includes('/api/order-ai/memo'));

  // ---------- [8] 请求头注入 ----------
  requests.length = 0;
  await ZdAPI.status();
  record('API-admin头注入', requests.length === 1
    && requests[0].headers['X-Role'] === 'admin'
    && requests[0].headers.Authorization === 'Bearer tok');

  // ---------- [9] 页面层 ----------
  const reactForPage = miniReact();
  Object.assign(MOCKS.react, reactForPage, {
    default: reactForPage,
  });
  const pageMod = compileLoad(PAGE_SRC, 'page');
  record('页面-默认导出', typeof pageMod.default === 'function');

  const el = await renderFlushed(pageMod.default, reactForPage);
  const flat = JSON.stringify(el);
  record('页面-标题与副标题', flat.includes('智单·AI智能订单大模型')
    && flat.includes('确定性引擎 · 建议书模式'));
  record('页面-四页签', ['洞察', '预测', '风控', '进化']
    .every(t => flat.includes(t)));

  // 问答交互(点击发送 → answer 数字插值渲染)
  const qaBtn = findAll(el, n => n.props.className === 'qaBtn')[0];
  await qaBtn.props.onClick();
  const elQa = await renderFlushed(pageMod.default, reactForPage);
  record('页面-问答交互', JSON.stringify(elQa).includes('当前累计订单 128 单')
    && JSON.stringify(elQa).includes('单量'));

  // 体检交互(runBtn 序: [0]刷新总览 [1]立即体检 [2]生成画像)
  const runBtns = findAll(elQa, n => n.props.className === 'runBtn');
  await runBtns[1].props.onClick();
  const elCk = await renderFlushed(pageMod.default, reactForPage);
  const flatCk = JSON.stringify(elCk);
  // 注: totalScore/100 为两个 JSX 子节点, JSON 中不连续, 须分别断言
  record('页面-体检交互', flatCk.includes('A 优秀')
    && flatCk.includes('88.15')
    && flatCk.includes('/100')
    && flatCk.includes('建议书')
    && flatCk.includes('状态分布健康度'));

  // 风控页签
  const t0 = reactForPage.__test;
  t0.states[0] = 'risk';
  t0.dirty.v = true;
  const elRisk = await renderFlushed(pageMod.default, reactForPage);
  const flatRisk = JSON.stringify(elRisk);
  record('页面-风控页签', flatRisk.includes('退款裁决评分(四因子)')
    && flatRisk.includes('异常订单扫描(三类规则)')
    && flatRisk.includes('历史异常留痕'));

  // 进化页签
  t0.states[0] = 'evo';
  t0.dirty.v = true;
  const elEvo = await renderFlushed(pageMod.default, reactForPage);
  const flatEvo = JSON.stringify(elEvo);
  record('页面-进化页签', flatEvo.includes('反馈闭环(三态裁决留痕)')
    && flatEvo.includes('三检测器(单量/PAID/取消)')
    && flatEvo.includes('决策备忘录生成')
    && flatEvo.includes('履约ETA'));

  // 预测页签
  t0.states[0] = 'forecast';
  t0.dirty.v = true;
  const elFc = await renderFlushed(pageMod.default, reactForPage);
  record('页面-预测页签', JSON.stringify(elFc).includes('What-if 三维推演')
    && JSON.stringify(elFc).includes('履约 ETA 加权预测')
    && JSON.stringify(elFc).includes('日单量滚动预测'));

  // 组件确定性(同输入两次渲染结构一致)
  const reactForPage2 = miniReact();
  Object.assign(MOCKS.react, reactForPage2, {
    default: reactForPage2,
  });
  const el2 = await renderFlushed(pageMod.default, reactForPage2);
  record('页面-渲染确定性',
    JSON.stringify(findAll(el, n => n.props.className === 'tab').map(textOf))
    === JSON.stringify(findAll(el2, n => n.props.className === 'tab').map(textOf)));

  // 汇总
  const pass = results.filter(r => r.ok).length;
  const fail = results.length - pass;
  console.log('-'.repeat(60));
  console.log(`总计: ${pass} 通过 / ${fail} 失败`);
  process.exit(fail === 0 ? 0 : 1);
})().catch(e => { console.error('FATAL:', e); process.exit(1); });
