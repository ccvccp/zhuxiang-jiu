/**
 * test-zp.js · 智付·AI智能支付大模型 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-zd.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [API 层 zp.ts]
 *   1. 字典完整性(通道七域/健康四态/意图八类/步进四档/
 *      生物四态/授信五档/调额三态/假设四态/版本四态/治理三级)
 *   2. 映射函数(未知回落)
 *   3. 34 方法存在性
 *   4. 观测面映射(通道字典/健康度/模型状态)
 *   5. 请求体构造(routeCompute/entropyCompute/creditEvaluate/
 *      bioChallenge+bioVerify/smartcodeGenerate/modalityParse/
 *      crossborderPreview) + URL/HTTP 方法全景
 *   6. 请求头注入(X-Role: admin + Bearer)
 *   [页面层 pages/zp/index.tsx]
 *   7. 默认导出/标题副标题/四页签
 *   8. 总览交互(刷新→通道+健康渲染)
 *   9. 路由页签(评分交互→候选留痕)/风控页签(熵+授信+胁迫)
 *      /进化页签(治理+漂移+红队+沙盘)
 *   10. 组件确定性(同输入两次渲染结构一致)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'zp.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'zp', 'index.tsx');

// ============================================================
// 1. Mock 上下文(响应结构对齐 zhuxiang-jiu/backend pay69_* 服务)
// ============================================================
const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url === '/api/pay69/model/status') {
    return { success: true, data: {
      modelVersion: 'v1-pay69-registry',
      mode: 'off', channelCount: 7,
      healthStates: ['healthy', 'degraded', 'critical', 'frozen'],
      intentTagCount: 8 } };
  }
  if (url === '/api/pay69/channels') {
    return { success: true, data: {
      channelCount: 7,
      channels: [
        { channelId: 'qr', feeRate: 0.0038, singleLimit: 50000 },
        { channelId: 'wechat', feeRate: 0.006, singleLimit: 50000 },
        { channelId: 'alipay', feeRate: 0.0055, singleLimit: 50000 },
        { channelId: 'bank', feeRate: 0.0045, singleLimit: 100000 },
        { channelId: 'unionpay', feeRate: 0.003, singleLimit: 50000 },
        { channelId: 'credit_tv', feeRate: 0, singleLimit: 10000 },
        { channelId: 'biometric', feeRate: 0.006, singleLimit: 50000 },
      ],
      healthThresholds: { healthy: 0.99, degraded: 0.9 } } };
  }
  if (url === '/api/pay69/health') {
    return { success: true, data: {
      frozenCount: 1,
      channels: [
        { channelId: 'qr', state: 'healthy', frozen: false },
        { channelId: 'wechat', state: 'healthy', frozen: false },
        { channelId: 'alipay', state: 'degraded', frozen: false },
        { channelId: 'bank', state: 'healthy', frozen: false },
        { channelId: 'unionpay', state: 'critical', frozen: false },
        { channelId: 'credit_tv', state: 'frozen', frozen: true },
        { channelId: 'biometric', state: 'healthy', frozen: false },
      ] } };
  }
  if (url === '/api/pay69/intents') {
    return { success: true, data: { count: 0, intents: [] } };
  }
  if (url === '/api/pay69/route/dict') {
    return { success: true, data: {
      weights: { fee: 0.15, health: 0.35, affinity: 0.35, habit: 0.15 },
      windowSize: 50, maxAttempts: 3,
      hardFilters: ['singleLimit', 'frozen', 'credit_tv'] } };
  }
  if (url === '/api/pay69/route/compute') {
    return { success: true, data: {
      amount: 500, candidateCount: 7, tags: ['default'],
      ranking: [
        { channelId: 'alipay', routeScore: 0.675,
          factors: { fee: 0.08, health: 1, affinity: 0.5, habit: 0.5 } },
        { channelId: 'wechat', routeScore: 0.625,
          factors: { fee: 0, health: 1, affinity: 0.5, habit: 0.5 } },
        { channelId: 'biometric', routeScore: 0.61,
          factors: { fee: 0, health: 1, affinity: 0.5, habit: 0.5 } },
      ] } };
  }
  if (url === '/api/pay69/route/window') {
    return { success: true, data: {
      windowSize: 50,
      channels: [
        { channelId: 'wechat', attemptCount: 20, successRate: 0.8 },
        { channelId: 'alipay', attemptCount: 5, successRate: null },
      ] } };
  }
  if (url.startsWith('/api/pay69/route/flows')) {
    return { success: true, data: { count: 0, flows: [] } };
  }
  if (url === '/api/pay69/entropy/dict') {
    return { success: true, data: {
      axes: ['amount', 'trust', 'behavior', 'environment', 'channel', 'history'],
      weights: { amount: 0.2, trust: 0.25, behavior: 0.2, environment: 0.15,
                 channel: 0.1, history: 0.1 },
      ladder: [
        { step: 'free', riskTier: 'light' }, { step: 'otp', riskTier: 'standard' },
        { step: 'biometric', riskTier: 'strong' }, { step: 'dual', riskTier: 'enhanced' }] } };
  }
  if (url === '/api/pay69/entropy/compute') {
    return { success: true, data: {
      entropy: 0.437, step: 'otp', riskTier: 'standard', failSoft: false,
      axes: { amount: 0.3, trust: 0.6, behavior: 0.3, environment: 0.4,
              channel: 0.4, history: 0.1 } } };
  }
  if (url.startsWith('/api/pay69/entropy/records')) {
    return { success: true, data: { count: 0, records: [] } };
  }
  if (url === '/api/pay69/credit/dict') {
    return { success: true, data: {
      grades: ['excellent', 'good', 'fair', 'cautious', 'rejected'],
      installmentRates: { '3': 0.03, '6': 0.045, '12': 0.06 } } };
  }
  if (url === '/api/pay69/credit/evaluate') {
    return { success: true, data: {
      grade: 'excellent', creditApproved: true, score: 0.83, baseLimit: 50000,
      installmentPlans: [
        { periods: 3, annualRate: 0.03, perInstallment: 2060 },
        { periods: 6, annualRate: 0.045, perInstallment: 1047.5 }] } };
  }
  if (url.startsWith('/api/pay69/credit/adjustments')) {
    return { success: true, data: { count: 0, adjustments: [] } };
  }
  if (url === '/api/pay69/biometric/dict') {
    return { success: true, data: {
      methods: ['face', 'fingerprint'],
      coercionThreshold: 0.6, challengeTtl: 120 } };
  }
  if (url === '/api/pay69/biometric/challenge') {
    return { success: true, data: {
      challenge: 'a'.repeat(32), method: 'face', ttlSeconds: 120 } };
  }
  if (url === '/api/pay69/biometric/verify') {
    return { success: true, data: {
      result: 'degraded', match: true, coerceScore: 0.85,
      degradedTo: 'password+manual' } };
  }
  if (url.startsWith('/api/pay69/biometric/events')) {
    return { success: true, data: { count: 0, events: [] } };
  }
  if (url === '/api/pay69/smartcode/dict') {
    return { success: true, data: {
      challengeThreshold: 0.5, ttlSeconds: 300 } };
  }
  if (url === '/api/pay69/smartcode/generate') {
    return { success: true, data: {
      status: 'generated', watermark: 'A1B2C3',
      challengeRequired: false, contextRisk: 0,
      fullCode: 'ZXBJ-QR55:pay69-smart:xxx.yyy.1.zzz' } };
  }
  if (url === '/api/pay69/smartcode/stats') {
    return { success: true, data: {
      totalCodes: 0, challengeRate: 0 } };
  }
  if (url === '/api/pay69/modality/dict') {
    return { success: true, data: {
      modalities: ['voice', 'gesture', 'eyegaze', 'text'],
      accessGroups: ['elderly', 'motor_impaired', 'visually_impaired', 'standard'],
      intentOutcomes: ['direct', 'confirm', 'clarify'] } };
  }
  if (url === '/api/pay69/modality/parse') {
    return { success: true, data: {
      outcome: 'direct', amount: 268, product: '竹香经典', channelId: 'wechat',
      engine: 'rule_based' } };
  }
  if (url === '/api/pay69/modality/stats') {
    return { success: true, data: { stats: {}, directRateByGroup: {} } };
  }
  if (url === '/api/pay69/evolution/dict') {
    return { success: true, data: {
      level: 'L0', levels: ['L0', 'L1', 'L2'],
      versionStatuses: ['draft', 'shadow', 'active', 'retired'],
      evolvableParams: { routeWeights: { riskLevel: 'high' } } } };
  }
  if (url === '/api/pay69/evolution/drift/detect') {
    return { success: true, data: {
      level: 'L0', signalCount: 1,
      signals: [{ domain: 'channelSuccess', subject: 'wechat',
                  metric: 0.8, severity: 'critical',
                  detail: '成功率 0.8 偏离阈值 0.05' }] } };
  }
  if (url.startsWith('/api/pay69/evolution/hypotheses')) {
    return { success: true, data: { count: 0, hypotheses: [] } };
  }
  if (url === '/api/pay69/evolution/governance') {
    return { success: true, data: {
      level: 'L0', killActive: false,
      hypothesesByStatus: {}, paramsByStatus: {} } };
  }
  if (url === '/api/pay69/immunity') {
    return { success: true, data: {
      status: 'active', frozenReason: '', redteamRuns: 2,
      lastRunAllDefended: true } };
  }
  if (url === '/api/pay69/immunity/monitor') {
    return { success: true, data: {
      criticalChannels: 2, signalCount: 3, shouldFreeze: true,
      action: 'auto_frozen', status: 'frozen',
      rules: { criticalChannels: 2, driftSignalCount: 3 } } };
  }
  if (url === '/api/pay69/immunity/redteam') {
    return { success: true, data: {
      allDefended: true, summary: '4/4 防御',
      vectors: [
        { vector: 'RT-01', name: '路由欺骗', defended: true },
        { vector: 'RT-02', name: '熵绕过', defended: true },
        { vector: 'RT-03', name: '模板投毒', defended: true },
        { vector: 'RT-04', name: '胁迫伪造', defended: true }] } };
  }
  if (url.startsWith('/api/pay69/immunity/redteam/runs')) {
    return { success: true, data: { count: 2, runs: [] } };
  }
  if (url === '/api/pay69/crossborder/dict') {
    return { success: true, data: {
      sandboxOnly: true,
      currencies: ['CNY', 'USD', 'EUR', 'JPY', 'GBP', 'HKD'],
      jurisdictions: { CN: { fxControl: 'strict' }, US: { fxControl: 'moderate' } } } };
  }
  if (url === '/api/pay69/crossborder/preview') {
    return { success: true, data: {
      sandboxOnly: true, currency: 'USD', jurisdiction: 'US',
      amountCny: 5000, sandboxRate: 0.14, convertedAmount: 700,
      hedge: { tier: 1, action: 'spot_only' },
      requiredDocs: ['trade_invoice'],
      disclaimer: '沙盘预研——全合成数据, 永不实接渠道' } };
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
  if (request === '@/api/zp') return apiMod;
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
  const out = path.join(os.tmpdir(), `zp-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

apiMod = compileLoad(API_SRC, 'api');
const ZpAPI = apiMod.ZpAPI;

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
  console.log('智付·AI智能支付大模型 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1] 字典完整性 ----------
  record('字典-通道七域', Object.keys(apiMod.CHANNEL_NAME).length === 7
    && apiMod.CHANNEL_NAME.wechat === '微信支付'
    && apiMod.CHANNEL_NAME.credit_tv === '闪银信用(信值抵扣)'
    && apiMod.CHANNEL_NAME.biometric === '生物特征');
  record('字典-健康四态', Object.keys(apiMod.HEALTH_STATE_NAME).length === 4
    && apiMod.HEALTH_STATE_NAME.healthy === '健康'
    && apiMod.HEALTH_STATE_NAME.frozen === '冻结');
  record('字典-意图八类', Object.keys(apiMod.INTENT_TAG_NAME).length === 8
    && apiMod.INTENT_TAG_NAME.fast_needed === '求快'
    && apiMod.INTENT_TAG_NAME.default === '默认');
  record('字典-步进四档', Object.keys(apiMod.STEP_NAME).length === 4
    && apiMod.STEP_NAME.free === '免密支付'
    && apiMod.STEP_NAME.dual === '双人复核');
  record('字典-风险档四级', Object.keys(apiMod.RISK_TIER_NAME).length === 4
    && apiMod.RISK_TIER_NAME.light === '轻量'
    && apiMod.RISK_TIER_NAME.enhanced === '增强');
  record('字典-生物四态', Object.keys(apiMod.BIO_RESULT_NAME).length === 4
    && apiMod.BIO_RESULT_NAME.degraded === '降级(胁迫线索)'
    && apiMod.BIO_RESULT_NAME.challenge_expired === '挑战过期');
  record('字典-授信五档', Object.keys(apiMod.CREDIT_GRADE_NAME).length === 5
    && apiMod.CREDIT_GRADE_NAME.excellent === '优秀'
    && apiMod.CREDIT_GRADE_NAME.rejected === '拒绝');
  record('字典-调额三态', Object.keys(apiMod.ADJ_STATE_NAME).length === 3
    && apiMod.ADJ_STATE_NAME.proposed === '已建议(待终审)');
  record('字典-假设四态', Object.keys(apiMod.HYP_STATUS_NAME).length === 4
    && apiMod.HYP_STATUS_NAME.submitted === '已提交46号');
  record('字典-版本四态', Object.keys(apiMod.PARAM_STATUS_NAME).length === 4
    && apiMod.PARAM_STATUS_NAME.shadow === '影子');
  record('字典-治理三级', Object.keys(apiMod.EVO_LEVEL_NAME).length === 3
    && apiMod.EVO_LEVEL_NAME.L0 === '观察学习'
    && apiMod.EVO_LEVEL_NAME.L2 === '协同进化');

  // ---------- [2] 映射回落 ----------
  record('映射-未知回落', apiMod.channelName('x') === 'x'
    && apiMod.healthStateName('y') === 'y'
    && apiMod.stepName('z') === 'z'
    && apiMod.bioResultName('w') === 'w'
    && apiMod.creditGradeName('v') === 'v'
    && apiMod.hypStatusName('u') === 'u'
    && apiMod.evoLevelName('t') === 't');

  // ---------- [3] 34 方法存在性 ----------
  const METHODS = [
    'channelDict', 'healthView', 'modelStatus', 'intents',
    'routeDict', 'routeCompute', 'routeWindow', 'routeFlows',
    'entropyDict', 'entropyCompute', 'entropyRecords',
    'creditDict', 'creditEvaluate', 'adjustments',
    'bioDict', 'bioChallenge', 'bioVerify', 'bioEvents',
    'smartcodeDict', 'smartcodeGenerate', 'smartcodeStats',
    'modalityDict', 'modalityParse', 'modalityStats',
    'evolutionDict', 'driftDetect', 'hypotheses', 'governance',
    'immunityView', 'immunityMonitor', 'redteamRun', 'redteamRuns',
    'crossborderDict', 'crossborderPreview'];
  record('API-34方法完备', METHODS.length === 34
    && METHODS.every(m => typeof ZpAPI[m] === 'function'));

  // ---------- [4] 观测面映射 ----------
  const st = await ZpAPI.modelStatus();
  record('API-模型状态', st.mode === 'off'
    && st.channelCount === 7 && st.intentTagCount === 8);

  const cd = await ZpAPI.channelDict();
  record('API-通道字典', cd.channelCount === 7
    && cd.channels[0].channelId === 'qr'
    && cd.healthThresholds.healthy === 0.99);

  const hv = await ZpAPI.healthView();
  record('API-健康度观测', hv.frozenCount === 1
    && hv.channels.length === 7
    && hv.channels[5].frozen === true);

  const rd = await ZpAPI.routeDict();
  record('API-路由字典', rd.weights.fee === 0.15
    && rd.weights.health === 0.35
    && rd.hardFilters.length === 3);

  const ws = await ZpAPI.routeWindow();
  record('API-滚动窗口', ws.windowSize === 50
    && ws.channels[0].successRate === 0.8);

  const ed = await ZpAPI.entropyDict();
  record('API-熵字典', ed.axes.length === 6
    && ed.ladder.length === 4);

  const gv = await ZpAPI.governance();
  record('API-治理观测', gv.level === 'L0'
    && gv.killActive === false);

  const iv = await ZpAPI.immunityView();
  record('API-免疫看板', iv.status === 'active'
    && iv.redteamRuns === 2 && iv.lastRunAllDefended === true);

  const cbd = await ZpAPI.crossborderDict();
  record('API-跨境字典', cbd.sandboxOnly === true
    && cbd.currencies.length === 6
    && cbd.jurisdictions.CN.fxControl === 'strict');

  // ---------- [5] 请求体构造 ----------
  requests.length = 0;
  const rc = await ZpAPI.routeCompute(1, 500, ['large_amount'], true);
  record('API-路由评分请求体', requests[0].url === '/api/pay69/route/compute'
    && requests[0].method === 'POST'
    && requests[0].data.amount === 500
    && requests[0].data.tags[0] === 'large_amount'
    && requests[0].data.tvEligible === true
    && rc.candidateCount === 7 && rc.ranking[0].routeScore === 0.675);

  requests.length = 0;
  const en = await ZpAPI.entropyCompute(1, 2000, 'B', 'wechat');
  record('API-熵计算请求体', requests[0].url === '/api/pay69/entropy/compute'
    && requests[0].data.trustTier === 'B'
    && requests[0].data.channelId === 'wechat'
    && en.entropy === 0.437 && en.step === 'otp');

  requests.length = 0;
  const cr = await ZpAPI.creditEvaluate(1, 5000, 'A', 0.9);
  record('API-授信评估请求体', requests[0].data.trustTier === 'A'
    && requests[0].data.cashflowIndex === 0.9
    && cr.grade === 'excellent' && cr.score === 0.83);

  requests.length = 0;
  const bc = await ZpAPI.bioChallenge(9980, 'face');
  const bv = await ZpAPI.bioVerify(9980, bc.challenge, true,
    ['facial_stiffness', 'voice_tremor']);
  record('API-生物链请求体', requests[0].url === '/api/pay69/biometric/challenge'
    && requests[1].url === '/api/pay69/biometric/verify'
    && requests[1].data.signs.length === 2
    && bv.result === 'degraded' && bv.coerceScore === 0.85);

  requests.length = 0;
  const sg = await ZpAPI.smartcodeGenerate(1, 10, 299, 3, true, false);
  record('API-情境码生成请求体', requests[0].url === '/api/pay69/smartcode/generate'
    && requests[0].data.hour === 3
    && requests[0].data.newDevice === true
    && sg.fullCode.startsWith('ZXBJ-QR55:pay69-smart:'));

  requests.length = 0;
  const mp = await ZpAPI.modalityParse(1, '支付 268 元 竹香经典 微信');
  record('API-多模态解析请求体', requests[0].url === '/api/pay69/modality/parse'
    && requests[0].data.modality === 'voice'
    && mp.outcome === 'direct' && mp.amount === 268);

  requests.length = 0;
  const dd = await ZpAPI.driftDetect();
  record('API-漂移检测', requests[0].url === '/api/pay69/evolution/drift/detect'
    && requests[0].method === 'POST'
    && dd.signalCount === 1 && dd.signals[0].severity === 'critical');

  requests.length = 0;
  const im = await ZpAPI.immunityMonitor();
  record('API-分布监控', im.shouldFreeze === true
    && im.action === 'auto_frozen' && im.status === 'frozen');

  requests.length = 0;
  const rt = await ZpAPI.redteamRun();
  record('API-红队执行', rt.allDefended === true
    && rt.vectors.length === 4 && rt.summary === '4/4 防御');

  requests.length = 0;
  const cb = await ZpAPI.crossborderPreview('USD', 'US', 5000);
  record('API-跨境预演请求体', requests[0].url === '/api/pay69/crossborder/preview'
    && requests[0].data.currency === 'USD'
    && requests[0].data.jurisdiction === 'US'
    && cb.convertedAmount === 700 && cb.sandboxRate === 0.14);

  // ---------- [6] 请求头注入 ----------
  requests.length = 0;
  await ZpAPI.modelStatus();
  record('API-admin头注入', requests.length === 1
    && requests[0].headers['X-Role'] === 'admin'
    && requests[0].headers.Authorization === 'Bearer tok');

  // ---------- [7] 页面层 ----------
  const reactForPage = miniReact();
  Object.assign(MOCKS.react, reactForPage, {
    default: reactForPage,
  });
  const pageMod = compileLoad(PAGE_SRC, 'page');
  record('页面-默认导出', typeof pageMod.default === 'function');

  const el = await renderFlushed(pageMod.default, reactForPage);
  const flat = JSON.stringify(el);
  record('页面-标题与副标题', flat.includes('智付·AI智能支付大模型')
    && flat.includes('四层认知支付栈 · 双环自进化 · 宪法约束'));
  record('页面-四页签', ['总览', '路由', '风控', '进化']
    .every(t => flat.includes(t)));

  // 总览交互(runBtn[0]=刷新总览 → 通道+健康渲染)
  const runBtns0 = findAll(el, n => n.props.className === 'runBtn');
  await runBtns0[0].props.onClick();
  const elOv = await renderFlushed(pageMod.default, reactForPage);
  const flatOv = JSON.stringify(elOv);
  record('页面-总览交互', flatOv.includes('7')
    && flatOv.includes('健康度观测')
    && flatOv.includes('冻结')
    && flatOv.includes('通道字典(封闭注册表)'));

  // 路由页签(先加载字典+窗口 → 评分交互 → 候选留痕)
  const t0 = reactForPage.__test;
  t0.states[0] = 'route';
  t0.dirty.v = true;
  let elRoute = await renderFlushed(pageMod.default, reactForPage);
  const loadBtn = findAll(elRoute, n => n.props.className === 'runBtn')[0];
  await loadBtn.props.onClick();
  elRoute = await renderFlushed(pageMod.default, reactForPage);
  const flatRoute = JSON.stringify(elRoute);
  record('页面-路由页签', flatRoute.includes('智能路由评分(四因子确定性)')
    && flatRoute.includes('滚动窗口')
    && flatRoute.includes('路由权重(封闭注册)'));

  const scoreBtn = findAll(elRoute, n => n.props.className === 'qaBtn')[0];
  await scoreBtn.props.onClick();
  const elRt = await renderFlushed(pageMod.default, reactForPage);
  const flatRt = JSON.stringify(elRt);
  // 注: 候选/通道为两个 JSX 子节点, JSON 中不连续, 须分别断言
  record('页面-路由评分交互', flatRt.includes('候选')
    && flatRt.includes('通道')
    && flatRt.includes('0.675')
    && flatRt.includes('LLM 禁入判定链'));

  // 风控页签
  t0.states[0] = 'risk';
  t0.dirty.v = true;
  const elRisk = await renderFlushed(pageMod.default, reactForPage);
  const flatRisk = JSON.stringify(elRisk);
  record('页面-风控页签', flatRisk.includes('风险熵引擎(六轴确定性)')
    && flatRisk.includes('交易级授信')
    && flatRisk.includes('活体意图验证'));

  // 进化页签
  t0.states[0] = 'evo';
  t0.dirty.v = true;
  const elEvo = await renderFlushed(pageMod.default, reactForPage);
  const flatEvo = JSON.stringify(elEvo);
  record('页面-进化页签', flatEvo.includes('自进化治理')
    && flatEvo.includes('漂移检测')
    && flatEvo.includes('红队四向量')
    && flatEvo.includes('跨境沙盘预演'));

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
