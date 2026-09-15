/**
 * test-member73-center.js · 73号会员体验中心 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-member73-trust.js) + TS 内存编译
 *
 * 覆盖:
 *   [API 层 api/member73.ts]
 *   1. horizon URL + 保级窗映射
 *   2. mentorMoments URL(本人 memberId) + 时刻映射
 *   3. momentRespond 请求体(responseType)
 *   4. delegateGrant/Revoke/Execute 请求体(action)
 *   5. delegatePredict 映射(topAction/candidates)
 *   [页面层 pages/member73]
 *   6. 四页签(地平线/导师/权益/代办)
 *   7. 资金类永不授权铁律文案
 *   8. 代办五动作授权管理
 *   9. 响应回流三按钮
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'member73.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'member73', 'index.tsx');

const requests = [];

const HORIZON_MOCK = {
  code: 0,
  data: {
    memberId: 5, level: 3, levelName: '竹友', growthValue: '600',
    next: { level: 4, requirement: '1000', gap: '400' },
    estimatedArrival: '2026-10-01T00:00:00Z',
    keepRisk: { periodConsume: '100', requirement: '300',
      remainingAmount: '200', progressPercent: '33.3',
      expireAt: '2026-12-01T00:00:00Z', daysRemaining: '76', atRisk: 1 },
    coldStart: { inShadow: false },
  },
};

const MOMENTS_MOCK = {
  code: 0,
  data: [
    { momentId: '9', momentType: 'order_done', entry: 'order_page',
      triggerScore: '0.8', decision: 'present', rendered: 1,
      hintPayload: { text: '您的专属折扣已备好' },
      responded: 0, responseType: '', at: '2026-09-15T10:00:00Z' },
  ],
};

const PREDICT_MOCK = {
  code: 0,
  data: {
    topAction: 'profile_completion', topReason: '资料缺口 1 项',
    candidates: [{ action: 'profile_completion', signal: '3' }],
    granted: false, riskTier: 'low', execMode: 'authorized_execute',
    engine: '行为序列频率确定性排序',
  },
};

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url === '/api/member73/horizon/5') return HORIZON_MOCK;
  if (url.startsWith('/api/member73/mentor/moments')) return MOMENTS_MOCK;
  if (url.startsWith('/api/member73/delegate/predict')) return PREDICT_MOCK;
  if (url.startsWith('/api/member73/delegate/grants/5')) return { code: 0, data: [] };
  if (url.startsWith('/api/member73/delegate/logs')) return { code: 0, data: [] };
  if (url.startsWith('/api/member73/benefits/')) {
    throw Object.assign(new Error('已是最高等级'), { statusCode: 409 });
  }
  if (url.includes('/respond') || url.includes('/grant')
    || url.includes('/revoke') || url.includes('/execute')) {
    return { code: 0, data: { executeResult: 'executed', note: 'ok' } };
  }
  throw new Error('unexpected url: ' + url);
};

const mockTaro = {
  showToast: () => {}, showModal: async () => ({ confirm: false }),
  navigateBack: () => {}, getStorageSync: () => '', setStorageSync: () => {},
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === './request' || request === '@/api/request') return { request: mockRequest };
  if (request === '@tarojs/taro') return { default: mockTaro };
  if (request === '@/services/auth-service') {
    return { getMemberId: () => 5, requireLogin: () => true };
  }
  if (request === '@tarojs/components') {
    return new Proxy({}, { get: (t, name) => (name === 'default' ? {} : { default: {} }) });
  }
  if (request.includes('index.module.scss')) return { default: new Proxy({}, { get: () => 'c' }) };
  if (request === '@/components/NavBar') return { default: () => null };
  if (request === 'react') {
    return { useState: (v) => [v, () => {}], useEffect: () => {}, useCallback: (f) => f };
  }
  return origLoad.apply(this, arguments);
};

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'member73-center-'));
const compileTs = (srcFile, outName) => {
  const code = fs.readFileSync(srcFile, 'utf-8');
  const js = ts.transpileModule(code, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React,
      target: ts.ScriptTarget.ES2019, esModuleInterop: true,
    },
  }).outputText;
  const outFile = path.join(tmpDir, outName);
  fs.writeFileSync(outFile, js);
  return require(outFile);
};

let PASS = 0, FAIL = 0;
const record = (name, ok, detail = '') => {
  if (ok) { PASS++; console.log('  ✓ ' + name); }
  else { FAIL++; console.log('  ✗ ' + name + ' — ' + detail); }
};

(async () => {
  const api = compileTs(API_SRC, 'member73.js');
  const { Member73API, DELEGATE_ACTION_NAME } = api;

  console.log('[API 层 api/member73.ts]');
  // 1. horizon
  const h = await Member73API.horizon();
  const last = requests[requests.length - 1];
  record('horizon URL(本人路径)',
    last.url === '/api/member73/horizon/5', last.url);
  record('horizon 映射(保级窗数值化)',
    h.level === 3 && h.levelName === '竹友'
    && h.next.gap === 400
    && h.keepRisk.daysRemaining === 76 && h.keepRisk.atRisk === true,
    JSON.stringify(h.keepRisk || {}).slice(0, 80));

  // 2. mentorMoments
  const ms = await Member73API.mentorMoments(30);
  const lastM = requests[requests.length - 1];
  record('mentorMoments URL(memberId+limit)',
    lastM.url === '/api/member73/mentor/moments?memberId=5&limit=30', lastM.url);
  record('mentorMoments 映射(时刻数值化)',
    ms.length === 1 && ms[0].momentId === 9
    && ms[0].triggerScore === 0.8 && ms[0].rendered === true
    && ms[0].hintPayload.text === '您的专属折扣已备好',
    JSON.stringify(ms[0] || {}).slice(0, 80));

  // 3. momentRespond
  await Member73API.momentRespond(9, 'click');
  const lastR = requests[requests.length - 1];
  record('momentRespond(URL+responseType)',
    lastR.url === '/api/member73/mentor/9/respond'
    && lastR.data && lastR.data.responseType === 'click',
    JSON.stringify(lastR.data || {}));

  // 4. grant/revoke/execute 请求体
  await Member73API.delegateGrant('review_order');
  await Member73API.delegateRevoke('review_order');
  await Member73API.delegateExecute('review_order');
  const bodies = requests.slice(-3).map(r => r.data);
  record('grant/revoke/execute 请求体(action)',
    bodies.every(b => b && b.action === 'review_order'),
    JSON.stringify(bodies));

  // 5. delegatePredict 映射
  const p = await Member73API.delegatePredict();
  record('delegatePredict 映射',
    p.topAction === 'profile_completion'
    && p.candidates[0].signal === 3
    && p.execMode === 'authorized_execute',
    JSON.stringify(p).slice(0, 80));

  // 字典
  record('代办五动作字典',
    Object.keys(DELEGATE_ACTION_NAME).length === 5
    && DELEGATE_ACTION_NAME.renewal_prefill === '续费预填');

  console.log('[页面层 pages/member73]');
  const pageSrc = fs.readFileSync(PAGE_SRC, 'utf-8');
  // 6. 四页签
  record('四页签(地平线/导师/权益/代办)',
    pageSrc.includes("'horizon'") && pageSrc.includes("'mentor'")
    && pageSrc.includes("'benefits'") && pageSrc.includes("'delegate'")
    && pageSrc.includes('地平线') && pageSrc.includes('代办'));
  // 7. 资金铁律
  record('资金类永不授权铁律文案',
    pageSrc.includes('资金类永不授权') || pageSrc.includes('永不代办'));
  // 8. 五动作授权管理
  record('五动作授权管理(ACTION_META)',
    pageSrc.includes('ACTION_META')
    && pageSrc.includes('renewal_prefill'));
  // 9. 响应回流三按钮
  record('响应回流三按钮(click/upgrade/ignore)',
    pageSrc.includes("'click'") && pageSrc.includes("'upgrade'")
    && pageSrc.includes("'ignore'"));
  // L5 兜底
  record('L5 最高等级兜底文案',
    pageSrc.includes('已是最高等级'));

  fs.rmSync(tmpDir, { recursive: true, force: true });
  console.log('------------------------------------------------------------');
  console.log(`通过: ${PASS} / 失败: ${FAIL} / 总计: ${PASS + FAIL}`);
  process.exit(FAIL === 0 ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
