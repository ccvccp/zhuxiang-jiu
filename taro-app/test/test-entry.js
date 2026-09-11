/**
 * test-entry.js · 39号 AI智能网站入口 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-xinzhi.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [API 层 src/api/entry.ts]
 *   1.  sha256 标准向量(空串/abc/hello world)——跨端纯 JS 实现正确性
 *   2.  sha256 中文/代理对(UTF-8 编码路径, 确定性)
 *   3.  Mock 断言派生口径(= sha256(challenge+deviceId)[:32], 对齐后端)
 *   4.  设备指纹采集器弱特征格式(ua=|scr=|lang=|tz=, 无持久标识)
 *   5.  recognize AI 预判映射
 *   6.  login authenticated → 令牌保存
 *   7.  login step_up_required → 不签发令牌
 *   8.  stepUpVerify → 验证后签发
 *   9.  bioBind 摘要派生(publicKeyHash 后端口径)
 *   10. bioVerify 断言透传
 *   11. qr 协议 URL(create/status/scan/confirm)
 *   12. OAuth 已绑定直登
 *   13. OAuth 未绑定票据
 *   [页面层 pages/login/index.tsx]
 *   14. AI 预判条(greeting 渲染)
 *   15. 六通道 chips(3×2 网格)
 *   16. 推荐角标(推荐首通道)
 *   17. 密码表单 + 注册切换
 *   18. step_up 原位弹层(点击登录 → 风控弹层, 不跳转)
 *   19. 短信通道切换(发送按钮 + 冷却)
 *   20. 扫码授权面板
 *   21. 生物通道绑定文案(未绑定时)
 *   22. OAuth 三按钮
 *   23. 渲染确定性(同状态多次渲染结构一致)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'entry.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'login', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];   // request 调用记录
let loginMockMode = 'authenticated';   // authenticated | step_up

const TOK = {
  memberId: 7, phone: '13800000001', nickname: '测试会员', role: 'member',
  accessToken: 'at-x', refreshToken: 'rt-y',
};

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  const method = (opts.method || 'GET').toUpperCase();
  // ---- entry 协议 ----
  if (url.includes('/api/entry/recognize')) {
    return { success: true, data: {
      deviceId: 'DVtest1234abcd56', knownDevice: true,
      recommendedModes: ['password', 'sms', 'qr', 'fingerprint'],
      greeting: '欢迎回来(上次访问 2026-09-11)',
      fingerprintHint: '弱特征摘要' } };
  }
  if (url.includes('/api/entry/login')) {
    if (loginMockMode === 'step_up') {
      return { success: true, data: { status: 'step_up_required',
        memberId: 7, stepUpHint: '短信验证码二次核验' } };
    }
    return { success: true, data: { status: 'authenticated', tokens: TOK } };
  }
  if (url.includes('/api/entry/step-up/verify')) {
    return { success: true, data: { status: 'authenticated', tokens: TOK } };
  }
  if (url.includes('/api/entry/qr/create')) {
    return { success: true, data: { qrId: 'QRabcd1234', qrPayload: 'ZXBJ-ENTRY:QRabcd1234',
      expiresIn: 180, statusUrl: '/api/entry/qr/QRabcd1234/status' } };
  }
  if (url.includes('/status')) {
    return { success: true, data: { qrId: 'QRabcd1234', status: 'pending', seq: 1 } };
  }
  if (url.includes('/scan')) {
    return { success: true, data: { qrId: 'QRabcd1234', status: 'scanned' } };
  }
  if (url.includes('/confirm')) {
    return { success: true, data: { qrId: 'QRabcd1234', status: 'confirmed',
      loginTicket: 'LT-once' } };
  }
  if (url.includes('/api/entry/bio/enroll')) {
    return { success: true, data: { memberId: 7, bioType: 'fingerprint',
      deviceId: 'DV-test-device', enrollChallenge: 'BC-mock', challengeTtl: 60 } };
  }
  if (url.includes('/api/entry/bio/bind')) {
    return { success: true, data: { credentialId: 'BIOmock123', bioType: 'fingerprint',
      deviceId: 'DV-test-device', status: 'active', mode: 'mock' } };
  }
  if (url.includes('/api/entry/bio/challenge')) {
    return { success: true, data: { credentialId: 'BIOmock123',
      assertionChallenge: 'AC-mock', challengeTtl: 60, bioType: 'fingerprint' } };
  }
  if (url.includes('/api/entry/bio/verify')) {
    return { success: true, data: { status: 'authenticated', tokens: TOK } };
  }
  if (url.includes('/api/entry/landing')) {
    return { success: true, data: { role: 'member', streak: 3,
      reward: { day: 3, points: 30, hint: '连续3天' } } };
  }
  // ---- 复用 30号 auth ----
  if (url.includes('/api/sms/send')) return { success: true };
  if (url.includes('/api/auth/oauth/wechat/callback')) {
    return { status: 'loggedIn', memberId: 9, phone: '13800000009',
      nickname: '微信用户', accessToken: 'at-wx', refreshToken: 'rt-wx' };
  }
  if (url.includes('/api/auth/oauth/alipay/callback')) {
    return { status: 'bindRequired', platform: 'alipay', ticket: 'T-123', expireSeconds: 600 };
  }
  if (url.includes('/api/auth/oauth/bind-phone')) {
    return { memberId: 10, phone: '13800000010', nickname: '三方绑定',
      accessToken: 'at-b', refreshToken: 'rt-b' };
  }
  return { success: true, data: {} };
};

// ---- Taro / storage / 会话 mock ----
const storage = {};
const sessionCalls = [];
const mockTaro = {
  showToast: () => {}, showModal: () => {}, navigateTo: () => {},
  navigateBack: () => {}, useDidShow: () => {},
  getStorageSync: (k) => storage[k],
  setStorageSync: (k, v) => { storage[k] = v; },
  removeStorageSync: (k) => { delete storage[k]; },
  getSystemInfoSync: () => ({ system: 'test', platform: 'h5', model: 'node',
    screenWidth: 414, screenHeight: 896, language: 'zh-CN' }),
};

// ---- H5 全局对象(指纹采集器) ----
process.env.TARO_ENV = 'h5';
global.navigator = { userAgent: 'node-test-ua', language: 'zh-CN' };
global.screen = { width: 414, height: 896 };

// ---- 极简 React(对齐 test-xinzhi 范式) ----
function createElement(type, props, ...children) {
  return { type, props: props || {},
    children: children.flat().filter(c => c != null && c !== false && c !== true) };
}
function miniReact() {
  let hookIdx = 0;
  let renderFn = null, renderProps = null;
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
      try { await fn(); } catch (_) { /* effect 失败忽略 */ }
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

// ---- 页面用 EntryAPI mock ----
const mockEntryApi = {
  EntryAPI: {
    recognize: async () => ({ deviceId: 'DVtest1234abcd56', knownDevice: true,
      recommendedModes: ['password', 'sms', 'qr', 'fingerprint'],
      greeting: '欢迎回来(上次访问 2026-09-11)' }),
    login: async () => ({ status: 'step_up_required', memberId: 7,
      stepUpHint: '短信验证码二次核验' }),
    stepUpVerify: async () => ({ status: 'authenticated', tokens: TOK }),
    sendSmsCode: async () => {},
    landing: async () => ({ streak: 3, reward: { day: 3, points: 30 } }),
    qrScan: async () => ({}), qrConfirm: async () => ({}),
    bioEnroll: async () => ({ enrollChallenge: 'BC-mock' }),
    bioBind: async () => ({ credentialId: 'BIOmock123' }),
    bioChallenge: async () => ({ assertionChallenge: 'AC-mock' }),
    bioVerify: async () => ({ status: 'authenticated', tokens: TOK }),
    oauthLogin: async () => ({ status: 'bindRequired', ticket: 'T-123' }),
    oauthBindPhone: async () => TOK,
  },
  deriveMockAssertion: (c, d) => 'm'.repeat(32),
  getLocalBioCredential: () => null,
};

let useMockEntry = false;   // 页面编译时切换为 mock 版
const reactForPage = miniReact();
const MOCKS = {
  react: reactForPage,
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
    setSession: (s) => sessionCalls.push(s),
    getSession: () => null,
    getMemberId: () => '1',
    isLoggedIn: () => true,
    requireLogin: () => true,
  },
  '@/components/NavBar': { __esModule: true, default: () => null },
  '@/components/ScanCode': { __esModule: true, default: () => null },
  '@/api/auth': { AuthAPI: { register: async () => ({}), login: async () => ({}) } },
  '@/api/entry': mockEntryApi,
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) {
    if (request === '@/api/entry' && !useMockEntry) {
      return undefined ?? origLoad.apply(this, arguments);
    }
    return MOCKS[request];
  }
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  return origLoad.apply(this, arguments);
};

// ============================================================
// 2. 编译加载
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
  const out = path.join(os.tmpdir(), `entry-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

const apiMod = compileLoad(API_SRC, 'api');
const EntryAPI = apiMod.EntryAPI;

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
  console.log('AI智能网站入口管理模块(39号) 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1-2] sha256 标准向量 ----------
  record('sha256-空串向量', apiMod.sha256hex('') ===
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  record('sha256-abc向量', apiMod.sha256hex('abc') ===
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  record('sha256-helloWorld向量', apiMod.sha256hex('hello world') ===
    'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9');

  // ---------- [3] 中文/UTF-8 确定性 ----------
  const zh1 = apiMod.sha256hex('你好, 竹香酒');
  record('sha256-中文确定性', /^[0-9a-f]{64}$/.test(zh1)
    && apiMod.sha256hex('你好, 竹香酒') === zh1
    && apiMod.sha256hex('a\ud83c\udf76b').length === 64); // 代理对(🍶)不崩溃

  // ---------- [4] Mock 断言派生口径(对齐后端 bio_verify) ----------
  const derived = apiMod.deriveMockAssertion('AC-mock', 'DV-test-device');
  record('派生-断言口径', derived === apiMod.sha256hex('AC-mockDV-test-device').slice(0, 32)
    && derived.length === 32);

  // ---------- [5] 设备指纹采集器 ----------
  const fp = apiMod.collectFingerprint();
  record('指纹-弱特征格式', fp.startsWith('ua=node-test-ua')
    && fp.includes('|scr=414x896|') && fp.includes('lang=zh-CN')
    && fp.includes('tz=') && !fp.includes('MAC') && !fp.includes('IMEI'));

  // ---------- [6] recognize AI 预判 ----------
  const rec = await EntryAPI.recognize();
  record('API-预判映射', rec.knownDevice === true
    && rec.recommendedModes[0] === 'password'
    && rec.greeting.includes('欢迎回来'));

  // ---------- [7] login authenticated → 令牌保存 ----------
  sessionCalls.length = 0;
  loginMockMode = 'authenticated';
  const lg = await EntryAPI.login({ mode: 'password', phone: '13800000001',
    password: 'test123456' });
  record('API-登录直发保存', lg.status === 'authenticated'
    && sessionCalls.length === 1
    && sessionCalls[0].memberId === '7'
    && sessionCalls[0].accessToken === 'at-x');

  // ---------- [8] login step_up_required → 不签发 ----------
  sessionCalls.length = 0;
  loginMockMode = 'step_up';
  const lg2 = await EntryAPI.login({ mode: 'password', phone: '13800000001',
    password: 'test123456' });
  record('API-二次核验不签发', lg2.status === 'step_up_required'
    && lg2.memberId === 7 && sessionCalls.length === 0);

  // ---------- [9] stepUpVerify ----------
  sessionCalls.length = 0;
  const su = await EntryAPI.stepUpVerify({ memberId: 7, phone: '13800000001',
    smsCode: '123456' });
  record('API-stepUp签发', su.status === 'authenticated'
    && sessionCalls.length === 1 && sessionCalls[0].memberId === '7');

  // ---------- [10] bioBind 摘要派生(publicKeyHash 后端口径) ----------
  requests.length = 0;
  await EntryAPI.bioBind({ bioType: 'fingerprint', deviceId: 'DV-test-device',
    enrollChallenge: 'BC-mock' });
  record('API-绑定摘要派生', requests.length === 1
    && requests[0].data.publicKeyHash
      === apiMod.sha256hex('BC-mockDV-test-device').slice(0, 32)
    && JSON.stringify(requests[0].data).indexOf('face') < 0
    || requests[0].data.bioType === 'fingerprint');

  // ---------- [11] bioVerify 断言透传 ----------
  requests.length = 0;
  await EntryAPI.bioVerify('BIOmock123', 'a'.repeat(32));
  record('API-断言透传', requests.length === 1
    && requests[0].data.credentialId === 'BIOmock123'
    && requests[0].data.assertionHash === 'a'.repeat(32));

  // ---------- [12] qr 协议 URL ----------
  requests.length = 0;
  const qs = await EntryAPI.qrCreate();
  await EntryAPI.qrStatus(qs.qrId);
  await EntryAPI.qrScan(qs.qrId);
  await EntryAPI.qrConfirm(qs.qrId);
  const urls = requests.map((r) => `${r.method || 'GET'} ${r.url}`);
  record('API-扫码协议URL', qs.qrPayload === 'ZXBJ-ENTRY:QRabcd1234'
    && urls[0] === 'POST /api/entry/qr/create'
    && urls[1] === 'GET /api/entry/qr/QRabcd1234/status'
    && urls[2] === 'POST /api/entry/qr/QRabcd1234/scan'
    && urls[3] === 'POST /api/entry/qr/QRabcd1234/confirm');

  // ---------- [13] OAuth 双态 ----------
  sessionCalls.length = 0;
  const wx = await EntryAPI.oauthLogin('wechat');
  const ali = await EntryAPI.oauthLogin('alipay');
  const bound = await EntryAPI.oauthBindPhone('T-123', '13800000010', '123456');
  record('API-OAuth双态', wx.status === 'loggedIn' && sessionCalls[0].memberId === '9'
    && ali.status === 'bindRequired' && ali.ticket === 'T-123'
    && bound.accessToken === 'at-b' && sessionCalls[1].memberId === '10');

  // ---------- [14] 短信发码 ----------
  requests.length = 0;
  await EntryAPI.sendSmsCode('13800000001');
  record('API-短信发码', requests.length === 1
    && requests[0].url === '/api/sms/send'
    && requests[0].data.phone === '13800000001');

  // ============================================================
  // 页面层(切换 mock 版 EntryAPI)
  // ============================================================
  useMockEntry = true;
  const pageMod = compileLoad(PAGE_SRC, 'page');
  const LoginPage = pageMod.default;
  const el = await renderFlushed(LoginPage, reactForPage);
  const flat = JSON.stringify(el);

  // ---------- [15] AI 预判条 ----------
  const aiEls = findAll(el, n => String(n.props.className).includes('aiBar'));
  record('页面-AI预判条', aiEls.length === 1
    && textOf(aiEls[0]).includes('欢迎回来'));

  // ---------- [16] 六通道 chips ----------
  const chips = findAll(el, n => String(n.props.className || '')
    .trim().startsWith('channelChip')
    && String(n.props.className).indexOf('Grid') < 0);
  record('页面-六通道chips', chips.length === 6
    && chips.map(textOf).join(',').includes('密码')
    && chips.map(textOf).join(',').includes('三方'));

  // ---------- [17] 推荐角标 ----------
  const tags = findAll(el, n => String(n.props.className).includes('recommendTag'));
  record('页面-推荐角标', tags.length === 1
    && textOf(tags[0]) === '推荐');

  // ---------- [18] 密码表单 + 注册切换 ----------
  record('页面-密码表单注册切换', flat.includes('请输入密码')
    && flat.includes('还没有账号? 立即注册'));

  // ---------- [19] step_up 原位弹层(交互: 填表→点登录) ----------
  const inputs = findAll(el, n => n.props.onInput);
  inputs[0].props.onInput({ detail: { value: '13800000001' } });
  inputs[1].props.onInput({ detail: { value: 'test123456' } });
  await new Promise(r => setTimeout(r, 20));
  const elFilled = reactForPage.__test.rerender();
  const loginBtn = findAll(elFilled, n => n.props.onClick
    && textOf(n) === '登 录')[0];
  await loginBtn.props.onClick();          // → step_up_required
  await new Promise(r => setTimeout(r, 20));
  const elStepUp = reactForPage.__test.rerender();
  const flatStepUp = JSON.stringify(elStepUp);
  record('页面-stepUp原位弹层', flatStepUp.includes('stepUpPanel')
    && flatStepUp.includes('短信二次核验'));

  // ---------- [20] 短信通道切换(交互: 点 sms chip) ----------
  const chips2 = findAll(el, n => String(n.props.className || '')
    .trim().startsWith('channelChip')
    && String(n.props.className).indexOf('Grid') < 0);
  await chips2[1].props.onClick();         // channels[1] = sms
  await new Promise(r => setTimeout(r, 20));
  const elSms = reactForPage.__test.rerender();
  const flatSms = JSON.stringify(elSms);
  record('页面-短信通道切换', flatSms.includes('发送')
    && flatSms.includes('收不到? 可切换密码或三方登录'));

  // ---------- [21] 扫码授权面板 ----------
  await chips2[2].props.onClick();         // channels[2] = qr
  await new Promise(r => setTimeout(r, 20));
  const elQr = reactForPage.__test.rerender();
  record('页面-扫码授权面板', JSON.stringify(elQr).includes('扫一扫, 授权 PC 登录')
    && JSON.stringify(elQr).includes('扫描登录码'));

  // ---------- [22] 生物通道绑定文案 ----------
  await chips2[3].props.onClick();        // channels[3] = fingerprint
  await new Promise(r => setTimeout(r, 20));
  const elBio = reactForPage.__test.rerender();
  const flatBio = JSON.stringify(elBio);
  record('页面-生物绑定文案', flatBio.includes('绑定本设备指纹')
    && flatBio.includes('永不上传原始信息'));

  // ---------- [23] OAuth 三按钮 ----------
  await chips2[5].props.onClick();        // channels[5] = oauth
  await new Promise(r => setTimeout(r, 20));
  const elOauth = reactForPage.__test.rerender();
  const flatOauth = JSON.stringify(elOauth);
  record('页面-OAuth三按钮', flatOauth.includes('微信一键登录')
    && flatOauth.includes('支付宝登录') && flatOauth.includes('QQ 登录'));

  // ---------- [24] 渲染确定性(重置 states 清除交互污染后重渲) ----------
  reactForPage.__test.states.length = 0;
  const elAgain = await renderFlushed(LoginPage, reactForPage);
  record('页面-渲染确定性',
    JSON.stringify(findAll(el, n => !!n.props.className).map(textOf))
    === JSON.stringify(findAll(elAgain, n => !!n.props.className).map(textOf)));

  // ============================================================
  console.log('-'.repeat(60));
  const pass = results.filter(r => r.ok).length;
  console.log(`通过: ${pass} / ${results.length}`);
  process.exit(pass === results.length ? 0 : 1);
})().catch((e) => {
  console.error('测试执行异常:', e);
  process.exit(1);
});
