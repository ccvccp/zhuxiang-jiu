/**
 * test-nexus74.js · 74号 NexusFlow 发布工作台 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-member73-center.js) + TS 内存编译
 *
 * 覆盖:
 *   [API 层 api/nexus74.ts]
 *   1. sources URL + X-Role 头 + 映射
 *   2. complianceCheck 请求体(text/platform) + 映射(stateLabel)
 *   3. adapt 请求体(sourceId/platform) + 映射
 *   4. publish 请求体(adaptationId/auto=false)
 *   5. publicationReceipt 请求体(result)
 *   [页面层 pages/nexus74]
 *   6. 四页签(总览/素材合规/适配发布/指标复盘)
 *   7. 合规检查+警示语注入修复
 *   8. B 档回执登记(数据诚实)
 *   9. 决策 409 友好降级检测
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'nexus74.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'nexus74', 'index.tsx');

const requests = [];

const SOURCES_MOCK = {
  code: 0,
  data: [
    { sourceId: '1', title: '竹映酱香品鉴入门',
      body: '本文讲解酱香型白酒品鉴法。',
      intent: 'tutorial', intentLabel: '教程',
      keywords: ['酱香型'], hasImage: 0, hasVideo: 0,
      createdAt: '2026-09-15T10:00:00Z' },
  ],
};

const CHECK_MOCK = {
  code: 0,
  data: {
    state: 'review_required', stateLabel: '需人工复核',
    platform: 'zhihu', hits: [], boundaryMatched: [],
    safeHarborApplied: false, isLiquorContent: true,
    warningPresent: false, fixable: true,
    fixAction: 'POST /warning/inject', note: '确定性',
  },
};

const ADAPT_MOCK = {
  code: 0,
  data: {
    adaptationId: '21', sourceId: '1', platform: 'zhihu',
    platformName: '知乎', personaState: 'professional',
    personaStateLabel: '专业导师', title: '如何理性看待?',
    summary: '核心观点…', complianceState: 'pass',
  },
};

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url.startsWith('/api/nexus74/sources') && !opts.method) return SOURCES_MOCK;
  if (url === '/api/nexus74/compliance/check') return CHECK_MOCK;
  if (url === '/api/nexus74/warning/inject') {
    return { code: 0, data: { text: '修复后文本 ——过量饮酒有害健康——' } };
  }
  if (url === '/api/nexus74/adapt') return ADAPT_MOCK;
  if (url === '/api/nexus74/publish') {
    return { code: 0, data: { publicationId: 9 } };
  }
  if (url.includes('/receipt')) {
    return { code: 0, data: { publicationId: 9, result: 'published' } };
  }
  if (url.startsWith('/api/nexus74/publications') || url.startsWith('/api/nexus74/adaptations')
    || url.startsWith('/api/nexus74/retrospects')) {
    return { code: 0, data: [] };
  }
  if (url.startsWith('/api/nexus74/model/status')) {
    return { code: 0, data: { mode: 'full', kill: false,
      immunity: { status: 'active' }, platformCount: 6, redlines: ['R1_induce'] } };
  }
  if (url.startsWith('/api/nexus74/quota/status')) {
    return { code: 0, data: { mode: 'full', beijingHour: 5,
      silence: { enabled: true, hours: [0, 23], active: true },
      platforms: [{ platform: 'zhihu', platformName: '知乎', adapterTier: 'B',
        todayPublished: '1', cap: '3', remaining: '2' }] } };
  }
  if (url.startsWith('/api/nexus74/metrics/summary')) {
    return { code: 0, data: { mode: 'full',
      global: { publications: '4', published: '1', withMetrics: '1', avgEngagement: '0.3' },
      auditDistribution: {}, platforms: [] } };
  }
  throw new Error('unexpected url: ' + url);
};

const mockTaro = {
  showToast: () => {}, showModal: async () => ({ confirm: false }),
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === './request' || request === '@/api/request') return { request: mockRequest };
  if (request === '@tarojs/taro') return { default: mockTaro };
  if (request === '@/services/auth-service') {
    return { getMemberId: () => 1, requireLogin: () => true };
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

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus74-'));
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
  const api = compileTs(API_SRC, 'nexus74.js');
  const { NexusAPI, PUBLISH_PLATFORMS } = api;

  console.log('[API 层 api/nexus74.ts]');
  // 1. sources
  const sources = await NexusAPI.sources(20);
  const last = requests[requests.length - 1];
  record('sources URL+admin头+映射',
    last.url === '/api/nexus74/sources?limit=20'
    && last.headers['X-Role'] === 'admin'
    && sources.length === 1 && sources[0].sourceId === 1
    && sources[0].intentLabel === '教程',
    last.url);

  // 2. complianceCheck
  const check = await NexusAPI.complianceCheck('测试文本', 'zhihu');
  const lastC = requests[requests.length - 1];
  record('complianceCheck 请求体+映射',
    lastC.url === '/api/nexus74/compliance/check'
    && lastC.data.text === '测试文本' && lastC.data.platform === 'zhihu'
    && check.stateLabel === '需人工复核' && check.fixable === true,
    JSON.stringify(lastC.data || {}));

  // 3. adapt
  const adapt = await NexusAPI.adapt(1, 'zhihu');
  const lastA = requests[requests.length - 1];
  record('adapt 请求体+映射',
    lastA.url === '/api/nexus74/adapt'
    && lastA.data.sourceId === 1 && lastA.data.platform === 'zhihu'
    && adapt.adaptationId === 21 && adapt.platformName === '知乎',
    JSON.stringify(lastA.data || {}));

  // 4. publish
  await NexusAPI.publish(1, 'zhihu', 21);
  const lastP = requests[requests.length - 1];
  record('publish 请求体(adaptationId+auto=false)',
    lastP.url === '/api/nexus74/publish'
    && lastP.data.sourceId === 1 && lastP.data.platform === 'zhihu'
    && lastP.data.adaptationId === 21 && lastP.data.auto === false,
    JSON.stringify(lastP.data || {}));

  // 5. receipt
  await NexusAPI.publicationReceipt(9, 'published', 'ok');
  const lastR = requests[requests.length - 1];
  record('receipt 请求体(result)',
    lastR.url === '/api/nexus74/publications/9/receipt'
    && lastR.data.result === 'published',
    JSON.stringify(lastR.data || {}));

  // 字典
  record('发布平台字典(六平台)',
    PUBLISH_PLATFORMS.length === 6
    && PUBLISH_PLATFORMS.some(p => p.key === 'wechat_mp'));

  console.log('[页面层 pages/nexus74]');
  const pageSrc = fs.readFileSync(PAGE_SRC, 'utf-8');
  // 6. 四页签
  record('四页签(总览/素材合规/适配发布/指标复盘)',
    pageSrc.includes("'overview'") && pageSrc.includes("'source'")
    && pageSrc.includes("'publish'") && pageSrc.includes("'metrics'")
    && pageSrc.includes('素材合规') && pageSrc.includes('指标复盘'));
  // 7. 合规+注入修复
  record('合规检查+警示语注入修复',
    pageSrc.includes('complianceCheck') && pageSrc.includes('warningInject')
    && pageSrc.includes('一键注入警示语'));
  // 8. B 档回执
  record('B 档回执登记(数据诚实)',
    pageSrc.includes("handleReceipt")
    && pageSrc.includes('登记已发布') && pageSrc.includes('登记被拒'));
  // 9. 决策 409 友好降级
  record('决策 409 友好降级检测',
    pageSrc.includes("includes('409')") || pageSrc.includes("includes('决策')"));
  // 确定性口径
  record('LLM 禁入口径文案',
    pageSrc.includes('LLM 禁入') || pageSrc.includes('确定性'));

  fs.rmSync(tmpDir, { recursive: true, force: true });
  console.log('------------------------------------------------------------');
  console.log(`通过: ${PASS} / 失败: ${FAIL} / 总计: ${PASS + FAIL}`);
  process.exit(FAIL === 0 ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
