/**
 * test-qr.js · 智码·AI智能二维码大模型 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-zp.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [API 层 qr.ts]
 *   1. 字典完整性(六类码/生命周期五态/消费策略三态/三画像/
 *      漂移三态/失败模式六域/判定五态/版式三态/假设三态/
 *      版本四态/红队四向量)
 *   2. 映射函数(未知回落)
 *   3. 44 方法存在性
 *   4. 观测面映射(注册表/模型状态/码实例/愉悦统计)
 *   5. 请求体构造(generate/redeem/traceView/authBegin/
 *      driftCheck/receivingIssue/collectIssue/redteamRun)
 *      + URL/HTTP 方法全景
 *   6. 请求头注入(X-Role: admin + Bearer)
 *   [页面层 pages/qr/index.tsx]
 *   7. 默认导出/标题副标题/四页签
 *   8. 总览交互(加载→注册表+码实例渲染)
 *   9. 六码页签(生成→核销/溯源分层)/愉悦页签(渲染建议)
 *      /免疫页签(红队四向量)
 *   10. 组件确定性(同输入两次渲染结构一致)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'qr.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'qr', 'index.tsx');

// ============================================================
// 1. Mock 上下文(响应结构对齐 zhuxiang-jiu/backend qr70_* 服务)
// ============================================================
const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url === '/api/qr70/model/status') {
    return { success: true, data: {
      modelVersion: 'v1-qr70-registry', mode: 'off',
      codeKindCount: 6, codeTypeCount: 7,
      lifecycleStates: ['generated', 'scanned', 'redeemed', 'expired', 'voided'],
      codeInstanceCount: 12,
      statusDistribution: { generated: 5, redeemed: 4, voided: 1 } } };
  }
  if (url === '/api/qr70/dict') {
    return { success: true, data: {
      codeKindCount: 6, codeCount: 7,
      lifecycleStates: ['generated', 'scanned', 'redeemed', 'expired', 'voided'],
      consumePolicies: ['once', 'session', 'public'],
      kinds: [
        { kind: 'manage', kindLabel: '管理码', codeCount: 1, codeIds: ['manage-workbench'] },
        { kind: 'auth', kindLabel: '认证码', codeCount: 2, codeIds: ['auth-entry', 'auth-session'] },
        { kind: 'trace', kindLabel: '溯源码', codeCount: 1, codeIds: ['trace-bottle'] },
        { kind: 'receiving', kindLabel: '收货码', codeCount: 1, codeIds: ['receiving-sign'] },
        { kind: 'shipping', kindLabel: '发货码', codeCount: 1, codeIds: ['shipping-handover'] },
        { kind: 'collect', kindLabel: '收款码', codeCount: 1, codeIds: ['collect-merchant'] },
      ],
      codes: [{ codeId: 'auth-entry', kind: 'auth', consumePolicy: 'once' }] } };
  }
  if (url.startsWith('/api/qr70/codes?') || url === '/api/qr70/codes') {
    return { success: true, data: {
      count: 2,
      codes: [
        { codeId: 'auth-entry', kind: 'auth', status: 'redeemed',
          scene: 'consumer', consumePolicy: 'once' },
        { codeId: 'trace-bottle', kind: 'trace', status: 'generated',
          scene: 'consumer', consumePolicy: 'public' },
      ] } };
  }
  if (url.startsWith('/api/qr70/events')) {
    return { success: true, data: { events: [] } };
  }
  if (url === '/api/qr70/joy/stats') {
    return { success: true, data: {
      codeCount: 2,
      stats: [
        { codeId: 'trace-bottle', sampleCount: 3, avgDurationMs: 1500,
          completeRate: 1, misTouchRate: 0.33 },
        { codeId: 'auth-entry', sampleCount: 2, avgDurationMs: 900,
          completeRate: 0.5, misTouchRate: 0 },
      ],
      note: '愉悦度=观测指标(纯统计基线, 策略变更走慢环 46号审批)' } };
  }
  if (url === '/api/qr70/codes/generate') {
    return { success: true, data: {
      codeId: 'auth-entry', status: 'generated',
      consumePolicy: 'once', memberId: 9, scene: 'consumer',
      code: 'ZXBJ-QR55:qr70-auth-entry:eyJtZW1iZXJEaWdlc3QiOiI5In0.sig.exp.nonce16',
      nonce: 'nonce16abcd1234', exp: 1800000000 } };
  }
  if (url === '/api/qr70/codes/redeem') {
    return { success: true, data: {
      redeemed: true, verifyStatus: 'ok',
      codeId: 'auth-entry', kind: 'auth',
      consumePolicy: 'once', status: 'redeemed', memberId: 9 } };
  }
  if (url.startsWith('/api/qr70/trace/personas')) {
    return { success: true, data: {
      personas: [
        { persona: 'quality', label: '质检党' },
        { persona: 'story', label: '故事党' },
        { persona: 'value', label: '实惠党' },
      ] } };
  }
  if (url.startsWith('/api/qr70/trace/view')) {
    return { success: true, data: {
      viewable: true, signed: true, batchNo: 'B-DEMO',
      persona: 'quality', personaLabel: '质检党',
      redFlagCount: 1,
      sections: [
        { sectionId: 'qc', title: '质检关卡', priority: 1 },
        { sectionId: 'anomalies', title: '异常提示', priority: 2 },
        { sectionId: 'health', title: '溯源健康度', priority: 3 },
      ] } };
  }
  if (url.startsWith('/api/qr70/trace/view/stats')) {
    return { success: true, data: { personas: [] } };
  }
  if (url === '/api/qr70/auth/dict') {
    return { success: true, data: {
      channels: [
        { channel: 'entry_qr', label: '扫码登录(39号)' },
        { channel: 'confirm', label: '确认令牌(48号)' },
        { channel: 'biometric', label: '生物特征(69号)' },
      ],
      driftLevels: ['none', 'fast', 'drifted'],
      driftThresholds: { fast: 60, drifted: 70 },
      failureModes: ['code_wrong', 'code_expired', 'challenge_expired',
        'coercion', 'mismatch', 'replayed'] } };
  }
  if (url === '/api/qr70/auth/begin') {
    return { success: true, data: {
      authChannel: 'biometric', authChannelLabel: '生物特征(69号)',
      consumePolicy: 'session',
      code: 'ZXBJ-QR55:qr70-auth-session:x.sig.e.n' } };
  }
  if (url === '/api/qr70/auth/drift/check') {
    return { success: true, data: {
      driftLevel: 'drifted', riskScore: 75,
      storedFingerprint: 'fp-s…ed', presentedFingerprint: 'fp-p…ed' } };
  }
  if (url.startsWith('/api/qr70/auth/failure/stats')) {
    return { success: true, data: {
      totalFailures: 4,
      byChannel: { confirm: { code_wrong: 2 }, biometric: { coercion: 1 } } } };
  }
  if (url === '/api/qr70/receiving/dict') {
    return { success: true, data: {
      fenceDefaultKm: 20, receiveWindowDays: 15,
      scanVerdicts: ['matched', 'not_owner', 'order_state_violation',
        'fence_violation', 'time_violation'] } };
  }
  if (url === '/api/qr70/receiving/issue') {
    return { success: true, data: {
      orderId: 'ORD-DEMO', fenceKm: 20, memberId: 9,
      code: 'ZXBJ-QR55:qr70-receiving-sign:x.s.e.n' } };
  }
  if (url === '/api/qr70/receiving/scan') {
    return { success: true, data: {
      scannable: true, verifyStatus: 'ok', orderId: 'ORD-DEMO',
      verdict: 'matched', verdictLabel: '三要素齐备',
      deviceChange: false, escalated: false } };
  }
  if (url === '/api/qr70/receiving/confirm') {
    return { success: true, data: {
      received: true, orderId: 'ORD-DEMO', orderStatus: 'RECEIVED' } };
  }
  if (url === '/api/qr70/shipping/dict') {
    return { success: true, data: {
      layoutBadges: ['长途转运', '优先件', '多品混箱', '易碎加固', '易混验码'],
      ribbonColors: ['红', '蓝', '黄', '绿', '紫', '橙', '棕'],
      confusionScenes: ['wave_pick', 'pack_scan', 'handover'] } };
  }
  if (url === '/api/qr70/shipping/issue') {
    return { success: true, data: {
      waveNo: 'W-DEMO', orderId: 'ORD-DEMO',
      layoutBadges: ['多品混箱', '易混验码'],
      ribbons: { '竹香经典500ml': ['红'] },
      code: 'ZXBJ-QR55:qr70-shipping-handover:x.s.e.n' } };
  }
  if (url === '/api/qr70/shipping/scan') {
    return { success: true, data: {
      scannable: true, waybillNo: 'SF-001', waveNo: 'W-DEMO' } };
  }
  if (url === '/api/qr70/shipping/confirm') {
    return { success: true, data: {
      handedOver: true, waybillNo: 'SF-001' } };
  }
  if (url.startsWith('/api/qr70/shipping/confusion/stats')) {
    return { success: true, data: {
      pairCount: 1,
      pairs: [{ skuA: '竹香经典500ml', skuB: '竹香经典1L', count: 2 }] } };
  }
  if (url === '/api/qr70/manage/dict') {
    return { success: true, data: {
      panelSections: ['purchase', 'production', 'storage', 'logistics',
        'sales', 'aftersale', 'finance', 'product'],
      confirmRequiredLevels: ['approve', 'manage'],
      freqTopThreshold: 2 } };
  }
  if (url === '/api/qr70/manage/issue') {
    return { success: true, data: {
      station: 'STG-PACK', consumePolicy: 'session',
      code: 'ZXBJ-QR55:qr70-manage-workbench:x.s.e.n' } };
  }
  if (url === '/api/qr70/manage/open') {
    return { success: true, data: {
      openable: true, station: 'STG-PACK', grantsCount: 2,
      panels: [{ stage: 'storage', ops: [] }], warnings: [] } };
  }
  if (url.startsWith('/api/qr70/manage/rank')) {
    return { success: true, data: {
      rows: [{ memberId: 9, nodeCode: 'storage.operate', count: 3 }] } };
  }
  if (url === '/api/qr70/collect/dict') {
    return { success: true, data: {
      layouts: [
        { layout: 'street_static', label: '夜市静态码' },
        { layout: 'storefront_dynamic', label: '门店动态码' },
        { layout: 'large_challenge', label: '大额挑战码' },
      ],
      largeAmountThreshold: 1000 } };
  }
  if (url === '/api/qr70/collect/issue') {
    return { success: true, data: {
      merchantId: 66, layout: 'large_challenge',
      layoutLabel: '大额挑战码', challengeRequired: true,
      code: 'ZXBJ-QR55:qr70-collect-merchant:x.s.e.n' } };
  }
  if (url === '/api/qr70/collect/redeem') {
    return { success: true, data: {
      redeemed: true, merchantId: 66, amount: 2000,
      layout: 'large_challenge', challenge: true } };
  }
  if (url.startsWith('/api/qr70/collect/merchant/')) {
    return { success: true, data: {
      merchantId: 66, redeemCount: 3, totalAmount: 2088,
      challengeCount: 1, layoutDistribution: { storefront_dynamic: 2 } } };
  }
  if (url === '/api/qr70/joy/engine/dict') {
    return { success: true, data: {
      layers: ['perceptual', 'decision', 'transfer', 'metacognitive'],
      renderWhitelist: ['fontScale', 'contrastBoost', 'animationPace',
        'buttonOrder', 'feedbackTiming', 'fallbackMode'],
      shadowMinDays: 7, killActive: false } };
  }
  if (url === '/api/qr70/joy/render/params') {
    return { success: true, data: {
      renderParams: {
        fontScale: 1.5, contrastBoost: 1.4, animationPace: 300,
        buttonOrder: 'default', feedbackTiming: 200, fallbackMode: 'voice' },
      whitelist: ['fontScale', 'contrastBoost', 'animationPace',
        'buttonOrder', 'feedbackTiming', 'fallbackMode'] } };
  }
  if (url.startsWith('/api/qr70/joy/hypotheses')) {
    return { success: true, data: {
      count: 2,
      hypotheses: [
        { hypothesisId: 1, paramId: 'render.fontScale', status: 'submitted',
          proposedAction: { from: '1.0', to: '1.2' },
          reason: '老年会员扫码平均耗时超基线 2 倍' },
        { hypothesisId: 2, paramId: 'render.animationPace', status: 'rejected',
          proposedAction: { from: '300', to: '500' }, reason: '慢网机型超时率高' },
      ] } };
  }
  if (url.startsWith('/api/qr70/joy/params')) {
    return { success: true, data: {
      count: 2,
      versions: [
        { version: 1, paramId: 'render.fontScale', value: '1.2', status: 'active' },
        { version: 2, paramId: 'render.fontScale', value: '1.4', status: 'draft' },
      ] } };
  }
  if (url === '/api/qr70/joy/drift/detect') {
    return { success: true, data: {
      recentWindow: 12, drift: 0.05, drifted: false, threshold: 0.3 } };
  }
  if (url === '/api/qr70/joy/health') {
    return { success: true, data: {
      killActive: false,
      hypotheses: { submitted: 1, rejected: 1 },
      paramVersions: { active: 1, retired: 1 } } };
  }
  if (url === '/api/qr70/joy/knowledge') {
    return { success: true, data: {
      atomCount: 4,
      atoms: [
        { atomId: 'auth_chain', reusableKinds: ['manage', 'collect', 'receiving'] },
        { atomId: 'evidence_chain', reusableKinds: ['trace', 'shipping'] },
      ] } };
  }
  if (url === '/api/qr70/immunity/dict') {
    return { success: true, data: {
      vectors: [
        { vector: 'forged_code', label: 'RT-01 伪造码' },
        { vector: 'replay_flood', label: 'RT-02 重放泛洪' },
        { vector: 'whitelist_bypass', label: 'RT-03 白名单绕过' },
        { vector: 'render_poison', label: 'RT-04 渲染投毒' },
      ],
      replayFloodThreshold: 5, freezeDriftThreshold: 0.3 } };
  }
  if (url === '/api/qr70/immunity') {
    return { success: true, data: {
      frozen: { frozen: false, frozenAt: '', frozenReason: '', envKillActive: false },
      vectors: ['forged_code', 'replay_flood', 'whitelist_bypass', 'render_poison'],
      redteamRuns: 4, defendedCount: 4, replayFloodThreshold: 5,
      runs: [
        { vector: 'forged_code', defended: true },
        { vector: 'replay_flood', defended: true },
      ] } };
  }
  if (url === '/api/qr70/immunity/monitor') {
    return { success: true, data: {
      drift: { drifted: false, drift: 0 },
      frozenNow: false, frozen: false, threshold: 0.3 } };
  }
  if (url === '/api/qr70/immunity/freeze') {
    return { success: true, data: {
      frozen: true, frozenAt: 't', frozenReason: '人工冻结(管理台)' } };
  }
  if (url === '/api/qr70/immunity/unfreeze') {
    return { success: true, data: { frozen: false } };
  }
  if (url === '/api/qr70/immunity/redteam') {
    return { success: true, data: {
      vector: 'replay_flood', defended: true,
      detail: { checks: { attempts: 8, accepted: 1, rejected: 7 } } } };
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
  if (request === '@/api/qr') return apiMod;
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
  const out = path.join(os.tmpdir(), `qr-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

apiMod = compileLoad(API_SRC, 'api');
const QrAPI = apiMod.QrAPI;

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

(async () => {
  console.log('='.repeat(60));
  console.log('智码·AI智能二维码大模型 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1] 字典完整性 ----------
  record('字典-六类码', Object.keys(apiMod.CODE_KIND_NAME).length === 6
    && apiMod.CODE_KIND_NAME.manage === '管理码'
    && apiMod.CODE_KIND_NAME.collect === '收款码'
    && apiMod.CODE_KIND_NAME.receiving === '收货码');
  record('字典-生命周期五态', Object.keys(apiMod.LIFECYCLE_NAME).length === 5
    && apiMod.LIFECYCLE_NAME.generated === '已生成'
    && apiMod.LIFECYCLE_NAME.voided === '已作废');
  record('字典-消费策略三态', Object.keys(apiMod.POLICY_NAME).length === 3
    && apiMod.POLICY_NAME.public === '公开码(永不消费)'
    && apiMod.POLICY_NAME.session === '会话码(TTL 内可重复)');
  record('字典-三画像', Object.keys(apiMod.PERSONA_NAME).length === 3
    && apiMod.PERSONA_NAME.quality === '质检党'
    && apiMod.PERSONA_NAME.value === '实惠党');
  record('字典-漂移三态', Object.keys(apiMod.DRIFT_NAME).length === 3
    && apiMod.DRIFT_NAME.drifted === '强漂移');
  record('字典-失败模式六域', Object.keys(apiMod.FAILURE_MODE_NAME).length === 6
    && apiMod.FAILURE_MODE_NAME.coercion === '胁迫降级'
    && apiMod.FAILURE_MODE_NAME.replayed === '重放');
  record('字典-判定五态', Object.keys(apiMod.SCAN_VERDICT_NAME).length === 5
    && apiMod.SCAN_VERDICT_NAME.fence_violation === '围栏外扫码'
    && apiMod.SCAN_VERDICT_NAME.matched === '三要素齐备');
  record('字典-版式三态', Object.keys(apiMod.LAYOUT_NAME).length === 3
    && apiMod.LAYOUT_NAME.large_challenge === '大额挑战码');
  record('字典-假设三态', Object.keys(apiMod.HYP_STATUS_NAME).length === 3
    && apiMod.HYP_STATUS_NAME.submitted === '已提交46号');
  record('字典-版本四态', Object.keys(apiMod.PARAM_STATUS_NAME).length === 4
    && apiMod.PARAM_STATUS_NAME.shadow === '影子(≥7天)');
  record('字典-红队四向量', Object.keys(apiMod.VECTOR_NAME).length === 4
    && apiMod.VECTOR_NAME.forged_code === 'RT-01 伪造码'
    && apiMod.VECTOR_NAME.render_poison === 'RT-04 渲染投毒');

  // ---------- [2] 映射回落 ----------
  record('映射-未知回落', apiMod.codeKindName('x') === 'x'
    && apiMod.lifecycleName('y') === 'y'
    && apiMod.personaName('z') === 'z'
    && apiMod.scanVerdictName('w') === 'w'
    && apiMod.layoutName('v') === 'v'
    && apiMod.vectorName('u') === 'u');

  // ---------- [3] 44 方法存在性 ----------
  const METHODS = [
    'dict', 'modelStatus', 'codes', 'events', 'joyStats',
    'generate', 'redeem',
    'personas', 'traceView', 'traceStats',
    'authDict', 'authBegin', 'driftCheck', 'failureStats',
    'receivingDict', 'receivingIssue', 'receivingScan', 'receivingConfirm',
    'shippingDict', 'shippingIssue', 'shippingScan', 'shippingConfirm',
    'confusionStats',
    'manageDict', 'manageIssue', 'manageOpen', 'manageRank',
    'collectDict', 'collectIssue', 'collectRedeem', 'merchantSummary',
    'engineDict', 'renderParams', 'hypotheses', 'paramVersions',
    'driftDetect', 'joyHealth', 'knowledge',
    'immunityDict', 'immunityView', 'immunityMonitor',
    'immunityFreeze', 'immunityUnfreeze', 'redteamRun'];
  record('API-44方法完备', METHODS.length === 44
    && METHODS.every(m => typeof QrAPI[m] === 'function'));

  // ---------- [4] 观测面映射 ----------
  const st = await QrAPI.modelStatus();
  record('API-模型状态', st.mode === 'off'
    && st.codeKindCount === 6 && st.codeInstanceCount === 12);

  const dd = await QrAPI.dict();
  record('API-注册表字典', dd.codeCount === 7
    && dd.kinds.length === 6
    && dd.consumePolicies[2] === 'public');

  const cs = await QrAPI.codes();
  record('API-码实例视图', cs.count === 2
    && cs.codes[0].consumePolicy === 'once'
    && cs.codes[1].status === 'generated');

  const js = await QrAPI.joyStats();
  record('API-愉悦统计', js.stats[0].avgDurationMs === 1500
    && js.stats[1].completeRate === 0.5);

  const ed = await QrAPI.engineDict();
  record('API-引擎字典', ed.layers.length === 4
    && ed.renderWhitelist.length === 6
    && ed.shadowMinDays === 7);

  const hs = await QrAPI.hypotheses();
  record('API-假设视图', hs.count === 2
    && hs.hypotheses[0].status === 'submitted');

  const kn = await QrAPI.knowledge();
  record('API-知识库', kn.atomCount === 4
    && kn.atoms[0].reusableKinds.length === 3);

  const iv = await QrAPI.immunityView();
  record('API-免疫看板', iv.redteamRuns === 4
    && iv.defendedCount === 4
    && iv.frozen.frozen === false);

  const ms = await QrAPI.merchantSummary(66);
  record('API-商户摘要', ms.redeemCount === 3
    && ms.totalAmount === 2088 && ms.challengeCount === 1);

  // ---------- [5] 请求体构造 ----------
  requests.length = 0;
  const g = await QrAPI.generate(9, 'auth-entry',
    { deviceHint: 'demo' }, 'consumer');
  record('API-生成请求体', requests[0].url === '/api/qr70/codes/generate'
    && requests[0].method === 'POST'
    && requests[0].data.codeId === 'auth-entry'
    && requests[0].data.scene === 'consumer'
    && g.code.startsWith('ZXBJ-QR55:qr70-auth-entry:'));

  requests.length = 0;
  const r = await QrAPI.redeem(g.code, 7);
  record('API-核销请求体', requests[0].url === '/api/qr70/codes/redeem'
    && requests[0].data.operatorId === 7
    && r.redeemed === true && r.verifyStatus === 'ok');

  requests.length = 0;
  const tv = await QrAPI.traceView('BLC-DEMO', 'story');
  record('API-溯源视图请求', requests[0].url.includes('/api/qr70/trace/view')
    && requests[0].url.includes('persona=story')
    && requests[0].method === undefined
    && tv.viewable === true && tv.personaLabel === '质检党'
    && tv.sections.length === 3);

  requests.length = 0;
  const ab = await QrAPI.authBegin(9, 'biometric', 'fp-x');
  record('API-认证发起请求体', requests[0].url === '/api/qr70/auth/begin'
    && requests[0].data.channel === 'biometric'
    && ab.consumePolicy === 'session');

  requests.length = 0;
  const dc = await QrAPI.driftCheck(9, 'fp-stored', 'fp-new', 75);
  record('API-漂移校准请求体', requests[0].url === '/api/qr70/auth/drift/check'
    && requests[0].data.riskScore === 75
    && dc.driftLevel === 'drifted');

  requests.length = 0;
  const ri = await QrAPI.receivingIssue('ORD-DEMO', 20);
  record('API-签收签发请求体', requests[0].url === '/api/qr70/receiving/issue'
    && requests[0].data.fenceKm === 20
    && ri.orderId === 'ORD-DEMO');

  requests.length = 0;
  const rs = await QrAPI.receivingScan(ri.code, 9, '泰安市');
  record('API-三要素扫码请求体', requests[0].url === '/api/qr70/receiving/scan'
    && requests[0].data.city === '泰安市'
    && rs.verdict === 'matched');

  requests.length = 0;
  const ci = await QrAPI.collectIssue(66, 2000, 'storefront', '整箱');
  record('API-收款生成请求体', requests[0].url === '/api/qr70/collect/issue'
    && requests[0].data.merchantId === 66
    && requests[0].data.amount === 2000
    && ci.challengeRequired === true);

  requests.length = 0;
  const si = await QrAPI.shippingIssue('W-DEMO', 'ORD-DEMO',
    ['竹香经典500ml', '竹香经典1L'], '新疆', 'SF');
  record('API-交接签发请求体', requests[0].data.skuNames.length === 2
    && requests[0].data.destinationProvince === '新疆'
    && si.ribbons['竹香经典500ml'][0] === '红');

  requests.length = 0;
  const rp = await QrAPI.renderParams(9, 'trace', true, false, 3);
  record('API-渲染建议请求体', requests[0].url === '/api/qr70/joy/render/params'
    && requests[0].data.elderly === true
    && requests[0].data.consecutiveFailures === 3
    && rp.renderParams.fontScale === 1.5
    && rp.renderParams.fallbackMode === 'voice');

  requests.length = 0;
  const rt = await QrAPI.redteamRun('replay_flood');
  record('API-红队执行请求体', requests[0].url === '/api/qr70/immunity/redteam'
    && requests[0].data.vector === 'replay_flood'
    && rt.defended === true
    && rt.detail.checks.rejected === 7);

  requests.length = 0;
  const mo = await QrAPI.immunityMonitor();
  record('API-分布监控', requests[0].method === 'POST'
    && mo.frozen === false);

  // ---------- [6] 请求头注入 ----------
  requests.length = 0;
  await QrAPI.modelStatus();
  record('API-admin头注入', requests.length === 1
    && requests[0].headers['X-Role'] === 'admin'
    && requests[0].headers.Authorization === 'Bearer tok');

  requests.length = 0;
  await QrAPI.traceView('BLC-X');
  record('API-公开端点无admin头', requests[0].headers === undefined);

  // ---------- [7] 页面层 ----------
  const reactForPage = miniReact();
  Object.assign(MOCKS.react, reactForPage, {
    default: reactForPage,
  });
  const pageMod = compileLoad(PAGE_SRC, 'page');
  record('页面-默认导出', typeof pageMod.default === 'function');

  const el = await renderFlushed(pageMod.default, reactForPage);
  const flat = JSON.stringify(el);
  record('页面-标题与副标题', flat.includes('智码·AI智能二维码大模型')
    && flat.includes('六类码语义中枢 · 流程愉悦引擎 · 安全免疫系统'));
  record('页面-四页签', ['总览', '六码', '愉悦', '免疫']
    .every(t => flat.includes(t)));
  record('页面-九期口径', flat.includes('九期 P0-P8')
    && flat.includes('71 端点'));
  record('页面-铁律展示', flat.includes('LLM 禁入判定链')
    && flat.includes('55号签名链零改动'));

  // 总览交互(loadBtn[0]=加载总览)
  const loadBtns = findAll(el, n => n.props.className === 'loadBtn');
  await loadBtns[0].props.onClick();
  const elOv = await renderFlushed(pageMod.default, reactForPage);
  const flatOv = JSON.stringify(elOv);
  record('页面-总览交互', flatOv.includes('六类码注册表(封闭)')
    && flatOv.includes('码实例留痕(生命周期)')
    && flatOv.includes('愉悦度观测基线(快环)'));

  // 六码页签(生成→核销)
  const t0 = reactForPage.__test;
  t0.states[0] = 'codes';
  t0.dirty.v = true;
  let elCodes = await renderFlushed(pageMod.default, reactForPage);
  const genBtn = findAll(elCodes, n => n.props.className === 'runBtn')[0];
  await genBtn.props.onClick();
  const elGen = await renderFlushed(pageMod.default, reactForPage);
  const flatGen = JSON.stringify(elGen);
  record('页面-生成交互', flatGen.includes('ZXBJ-QR55:qr70-auth-entry:')
    && flatGen.includes('一次性(nonce 核销即失效)'));
  // 核销该码
  const redeemBtn = findAll(elGen, n => n.props.className === 'runBtn')
    .find(n => JSON.stringify(n.children).includes('核销该码'));
  await redeemBtn.props.onClick();
  const elRd = await renderFlushed(pageMod.default, reactForPage);
  const flatRd = JSON.stringify(elRd);
  record('页面-核销交互', flatRd.includes('redeemed=')
    && flatRd.includes('verify=')
    && flatRd.includes('"true"')
    && flatRd.includes('"ok"'));

  // 愉悦页签(加载→渲染建议)
  t0.states[0] = 'joy';
  t0.dirty.v = true;
  const elJoy0 = await renderFlushed(pageMod.default, reactForPage);
  const joyLoad = findAll(elJoy0, n => n.props.className === 'loadBtn')[0];
  await joyLoad.props.onClick();
  const elJoy = await renderFlushed(pageMod.default, reactForPage);
  const flatJoy = JSON.stringify(elJoy);
  record('页面-愉悦页签', flatJoy.includes('四层引擎(自适应学习)')
    && flatJoy.includes('perceptual')
    && flatJoy.includes('假设建议书(46号审批链)'));

  // 免疫页签(看板+红队)
  t0.states[0] = 'immunity';
  t0.dirty.v = true;
  const elIm0 = await renderFlushed(pageMod.default, reactForPage);
  const imLoad = findAll(elIm0, n => n.props.className === 'loadBtn')[0];
  await imLoad.props.onClick();
  const elIm = await renderFlushed(pageMod.default, reactForPage);
  const flatIm = JSON.stringify(elIm);
  record('页面-免疫看板', flatIm.includes('红队批次')
    && flatIm.includes('防御成功')
    && flatIm.includes('进化运行中(未冻结)'));

  // ---------- [10] 组件确定性 ----------
  t0.states[0] = 'overview';
  t0.dirty.v = true;
  const d1 = JSON.stringify(await renderFlushed(pageMod.default, reactForPage));
  const d2 = JSON.stringify(await renderFlushed(pageMod.default, reactForPage));
  record('页面-渲染确定性', d1 === d2);

  // ------------------------------------------------------------
  const pass = results.filter(r => r.ok).length;
  const fail = results.length - pass;
  console.log('-'.repeat(60));
  console.log(`总计: ${pass} 通过 / ${fail} 失败`);
  console.log('='.repeat(60));
  if (fail > 0) process.exit(1);
})().catch(e => {
  console.error('测试执行异常:', e);
  process.exit(1);
});
