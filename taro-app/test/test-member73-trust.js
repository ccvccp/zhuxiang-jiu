/**
 * test-member73-trust.js · 73号信任面板+遗忘权 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-trust-appeal.js) + TS 内存编译
 * + Module._load mock
 *
 * 覆盖:
 *   [API 层 api/member73.ts]
 *   1. trustPanel URL(本人 member_id 路径)
 *   2. trustPanel 映射(动作流数值化/可撤回清单/四可原则)
 *   3. trustForget URL(POST + 无 body)
 *   4. trustForget 映射(留痕序号/五表删除计数)
 *   [页面层 pages/member73-trust]
 *   5. 页面含四可区块(可解释/可撤回/可验证/可遗忘)
 *   6. 页面含动作流渲染
 *   7. 页面含遗忘确认(二次弹窗)与危险区
 *   8. 重复遗忘友好文案(已遗忘检测)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'member73.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'member73-trust', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];

const PANEL_MOCK = {
  code: 0,
  data: {
    modelVersion: 'v1-member73-p4',
    mode: 'full',
    memberId: 5,
    nickname: '测试会员小竹',
    actions: [
      { kind: 'hint', refId: '3', summary: '您关注的竹香酒有新活动',
        responded: 1, responseType: 'positive', at: '2026-09-15T08:00:00Z' },
      { kind: 'reveal', refId: '2', summary: 'L1→L2 权益告知',
        responded: null, responseType: '', at: '2026-09-14T08:00:00Z' },
      { kind: 'delegate', refId: '7', summary: 'payment→rejected',
        responded: null, responseType: '', at: '2026-09-13T08:00:00Z' },
    ],
    actionTotal: '3',
    revocable: [
      { action: 'hint_render', grantedAt: '2026-09-01T00:00:00Z' },
    ],
    fourPrinciples: {
      '可解释': '全部触达/代办留痕+依据',
      '可撤回': '授权一键 revoke',
    },
  },
};

const FORGET_MOCK = {
  code: 0,
  data: {
    forgetSeq: '11',
    memberId: 5,
    deletedTables: { moments: '4', reveals: '1', grants: '1' },
    note: '五表硬删除留痕',
    at: '2026-09-16T00:00:00Z',
  },
};

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url === '/api/member73/trust/panel/5') return PANEL_MOCK;
  if (url === '/api/member73/trust/forget' && opts.method === 'POST') {
    return FORGET_MOCK;
  }
  throw new Error('unexpected url: ' + url);
};

const mockTaro = {
  showToast: () => {},
  showModal: async () => ({ confirm: false }),
  navigateBack: () => {},
  getStorageSync: () => '',
  setStorageSync: () => {},
};

// ---- Module._load 拦截 ----
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

// ---- TS 内存编译 ----
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'member73-trust-'));
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
  const { Member73API } = api;

  console.log('[API 层 api/member73.ts]');
  // 1-2. trustPanel
  const panel = await Member73API.trustPanel();
  const last = requests[requests.length - 1];
  record('trustPanel URL(本人路径)',
    last.url === '/api/member73/trust/panel/5', last.url);
  record('trustPanel 映射(动作流数值化)',
    panel.memberId === 5 && panel.nickname === '测试会员小竹'
    && panel.actionTotal === 3
    && panel.actions.length === 3
    && panel.actions[0].refId === 3
    && panel.actions[0].responded === 1
    && panel.actions[1].responded === null,
    JSON.stringify(panel.actions[0]).slice(0, 100));
  record('trustPanel 映射(可撤回清单)',
    panel.revocable.length === 1
    && panel.revocable[0].action === 'hint_render',
    JSON.stringify(panel.revocable));
  record('trustPanel 映射(四可原则)',
    panel.fourPrinciples['可解释'] === '全部触达/代办留痕+依据');

  // 3-4. trustForget
  const ledger = await Member73API.trustForget();
  const last2 = requests[requests.length - 1];
  record('trustForget URL(POST)',
    last2.url === '/api/member73/trust/forget'
    && last2.method === 'POST', last2.url);
  record('trustForget 映射(留痕序号/删除计数)',
    ledger.forgetSeq === 11
    && ledger.deletedTables.moments === 4
    && ledger.deletedTables.reveals === 1,
    JSON.stringify(ledger).slice(0, 100));

  console.log('[页面层 pages/member73-trust]');
  const pageSrc = fs.readFileSync(PAGE_SRC, 'utf-8');
  // 5. 四可区块
  record('页面含四可原则区块',
    pageSrc.includes('四项数据权利')
    && pageSrc.includes('fourPrinciples'));
  // 6. 动作流
  record('页面含动作流渲染',
    pageSrc.includes('动作流 · 可解释')
    && pageSrc.includes('KIND_NAME')
    && pageSrc.includes('触达') && pageSrc.includes('代办'));
  // 7. 遗忘确认与危险区
  record('页面含遗忘二次确认(showModal)',
    pageSrc.includes('showModal')
    && pageSrc.includes('确认遗忘 AI 画像'));
  record('页面含不可逆警示',
    pageSrc.includes('不可逆') && pageSrc.includes('仅可执行一次'));
  // 8. 重复遗忘友好文案
  record('页面含重复遗忘友好检测',
    pageSrc.includes("includes('重复')")
    && pageSrc.includes('无需重复操作'));
  // requireLogin 门控
  record('页面登录门控(requireLogin)',
    pageSrc.includes('requireLogin()'));

  fs.rmSync(tmpDir, { recursive: true, force: true });
  console.log('------------------------------------------------------------');
  console.log(`通过: ${PASS} / 失败: ${FAIL} / 总计: ${PASS + FAIL}`);
  process.exit(FAIL === 0 ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
