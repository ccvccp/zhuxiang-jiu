/**
 * test-promo.js · 36号 AI智能推广模块 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-xinzhi.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule) + Module._load 拦截 mock
 *
 * 覆盖:
 *   [API 层 src/api/promo.ts]
 *   1. 字典完整性(热点平台五/发布平台五/热点四态/内容五态/决策三档/通道三态)
 *   2. 映射函数回落(未知值原样返回)
 *   3. 扫描结果映射(scanned/new/discarded/decisions)
 *   4. 热点列表映射(score 数值化)
 *   5. 决策裁决 URL 与载荷
 *   6. 生成载荷(platforms 透传)
 *   7. 内容列表映射(complianceScore/agentTrace/shortCode)
 *   8. 审核载荷(approved+reviewer)
 *   9. 入队与出队 URL
 *   10. 通道状态映射(effectiveMode 三态)
 *   11. 报表总览映射(嵌套统计)
 *   12. 管理头注入(X-Role: admin)
 *   [页面层 pages/promo/index.tsx]
 *   13. 五页签结构(总览/雷达/内容工厂/发布中心/通道画像)
 *   14. 统计卡渲染(六卡)
 *   15. 待裁决提醒卡(跳转雷达)
 *   16. 扫描按钮 + 热点卡(评分进度条)
 *   17. 裁决按钮(跟进/放弃)
 *   18. 平台多选生成表单
 *   19. 内容卡(状态徽章/Agent轨迹/操作按钮)
 *   20. 队列卡(黄金时段窗口)
 *   21. 通道徽章三色
 *   22. 渲染确定性
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'promo.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'promo', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  const method = (opts.method || 'GET').toUpperCase();
  if (url.includes('/api/promo/radar/scan')) {
    return { success: true, data: {
      scanned: 25, new: 20, discarded: 5,
      decisions: [{ hotspotId: 1, decision: 'auto_engage', reason: '评分72' }],
      hotspots: [] } };
  }
  if (url.includes('/api/promo/radar/hotspots')) {
    return { success: true, data: [
      { hotspotId: 1, platform: 'douyin', title: '中秋送礼国潮热',
        summary: '节庆送礼热度攀升', heat: '96.5', score: '72.4',
        status: 'engaged', brandHits: ['礼', '节'],
        scoreDetail: { heat: 96.5, velocity: 80, brand_relevance: 60, persistence: 50 } },
      { hotspotId: 2, platform: 'weibo', title: '低分热点', score: '35',
        status: 'passed' }] };
  }
  if (url.includes('/api/promo/decisions')) {
    return { success: true, data: [
      { decisionId: 1, hotspotId: 2, hotspotTitle: '宴席用酒指南讨论',
        platform: 'zhihu', score: 58, decision: 'manual_queue',
        reason: '评分58 位于50-70人工区间', decided: false }] };
  }
  if (url.includes('/decide')) {
    return { success: true, data: { hotspotId: 2, decision: 'engaged' } };
  }
  if (url.includes('/api/promo/contents/generate')) {
    return { success: true, data: [
      { contentId: 11, hotspotId: 1, platform: 'douyin', title: '中秋竹香礼赠指南',
        body: '正文...', hashtags: ['#中秋送礼#'],
        status: 'pending', complianceScore: 100, contentGroupId: 7,
        shortCode: 'A-3f2k1', agentTrace: ['glm-5.3', 'glm-5.3', 'glm-5.3', 'glm-5.3'],
        authorityRefs: ['GB/T 10781'] }] };
  }
  // 专匹配须在 /api/promo/contents 宽匹配之前(review/publish 均含 contents 前缀)
  if (url.includes('/review')) {
    return { success: true, data: { contentId: 11, status: 'approved' } };
  }
  if (url.includes('/publish')) {
    return { success: true, data: { contentId: 12, platform: 'xiaohongshu',
      scheduledAt: '2026-09-11T18:30:00', inWindow: true,
      windowHint: '黄金时段 18:00-22:00' } };
  }
  if (url.includes('/api/promo/contents')) {
    return { success: true, data: [
      { contentId: 11, hotspotId: 1, platform: 'douyin',
        title: '中秋竹香礼赠指南', status: 'pending',
        complianceScore: 100, contentGroupId: 7, shortCode: 'A-3f2k1',
        agentTrace: ['glm-5.3', 'glm-5.3', 'glm-5.3', 'glm-5.3'] },
      { contentId: 12, hotspotId: 1, platform: 'xiaohongshu',
        title: '竹香酒宴席笔记', status: 'approved',
        complianceScore: 92, contentGroupId: 7 }] };
  }
  if (url.includes('/api/promo/publish/queue')) {
    return { success: true, data: [
      { contentId: 12, platform: 'xiaohongshu', title: '竹香酒宴席笔记',
        scheduledAt: '2026-09-11T18:30:00', inWindow: true,
        windowHint: '黄金时段 18:00-22:00' }] };
  }
  if (url.includes('/process')) {
    return { success: true, data: [
      { contentId: 12, platform: 'xiaohongshu',
        receipt: { mode: 'mock', publishId: 'P-001', exposureEstimate: 5200 } }] };
  }
  if (url.includes('/api/promo/channels/status')) {
    return { success: true, data: [
      { platform: 'douyin', mode: 'mock', keyConfigured: false, effectiveMode: 'mock' },
      { platform: 'weibo', mode: 'real', keyConfigured: true, effectiveMode: 'real' },
      { platform: 'xiaohongshu', mode: 'real', keyConfigured: false,
        effectiveMode: 'mock_fallback' }] };
  }
  if (url.includes('/api/promo/seo/pushes')) {
    return { success: true, data: [
      { status: 'ok', urls: ['/sitemap.xml', '/landing/1'], pushedAt: '2026-09-11' }] };
  }
  if (url.includes('/api/promo/report/overview')) {
    return { success: true, data: {
      hotspots: { total: 20, engaged: 8, passed: 7, pendingManual: 5 },
      contents: { total: 12, pending: 3, published: 6, rejected: 1 },
      attribution: { clicks: 340, registered: 45, ordered: 9, gmv: 2280.0 },
      dailyCap: { used: 6, limit: 20 } } };
  }
  if (url.includes('/api/promo/audience/profiles')) {
    return { success: true, data: [
      { platform: 'douyin', audience: '18-35 大众娱乐', tone: '快节奏',
        format: '15-45s 短视频脚本', scenes: ['剧情'],
        productTones: ['口粮酒'] }] };
  }
  return { success: true, data: {} };
};

// ---- 极简 React(对齐 test-xinzhi/test-entry 范式) ----
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
const mockTaro = {
  showToast: () => {}, navigateTo: () => {}, navigateBack: () => {},
  useDidShow: () => {},
};

let useMockPromo = false;
const mockPromoApi = {
  PromoAPI: {
    reportOverview: async () => ({
      hotspots: { total: 20, engaged: 8, passed: 7, pendingManual: 5 },
      contents: { total: 12, pending: 3, published: 6, rejected: 1 },
      attribution: { clicks: 340, registered: 45, ordered: 9, gmv: 2280 },
      dailyCap: { used: 6, limit: 20 } }),
    hotspots: async () => [
      { hotspotId: 1, platform: 'douyin', title: '中秋送礼国潮热', score: 72.4,
        status: 'engaged', summary: '节庆热度攀升' }],
    decisions: async () => [
      { decisionId: 1, hotspotId: 2, hotspotTitle: '宴席用酒指南讨论',
        decision: 'manual_queue', reason: '评分58 人工区间' }],
    contents: async () => [
      { contentId: 11, platform: 'douyin', title: '中秋竹香礼赠指南',
        status: 'pending', complianceScore: 100, contentGroupId: 7,
        shortCode: 'A-3f2k1', agentTrace: ['glm-5.3', 'glm-5.3', 'glm-5.3', 'glm-5.3'] }],
    publishQueue: async () => [
      { contentId: 12, platform: 'xiaohongshu', title: '竹香酒宴席笔记',
        scheduledAt: '18:30', inWindow: true, windowHint: '黄金时段' }],
    channelsStatus: async () => [
      { platform: 'douyin', effectiveMode: 'mock', keyConfigured: false },
      { platform: 'weibo', effectiveMode: 'real', keyConfigured: true },
      { platform: 'xiaohongshu', effectiveMode: 'mock_fallback' }],
    seoPushes: async () => [{ status: 'ok', urls: ['/sitemap.xml'], pushedAt: '09-11' }],
    audienceProfiles: async () => [
      { platform: 'douyin', audience: '18-35', tone: '快节奏', format: '短视频' }],
  },
  hotspotStatusName: (s) => s,
  publishPlatformName: (p) => p,
  contentStatusName: (s) => s,
  channelModeName: (m) => m,
};

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
    getMemberId: () => '1', isLoggedIn: () => true,
  },
  '@/components/NavBar': { __esModule: true, default: () => null },
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (Object.prototype.hasOwnProperty.call(MOCKS, request)) return MOCKS[request];
  if (request.endsWith('/api/request') || request === '@/api/request') {
    return { request: mockRequest };
  }
  if (request === '@/api/promo') {
    return useMockPromo ? mockPromoApi : undefined ?? origLoad.apply(this, arguments);
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
  const out = path.join(os.tmpdir(), `promo-${label}-${process.pid}.js`);
  fs.writeFileSync(out, compiled.outputText);
  return require(out);
}

const apiMod = compileLoad(API_SRC, 'api');
const PromoAPI = apiMod.PromoAPI;

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
  console.log('AI智能推广模块(36号) 前端单元测试');
  console.log('='.repeat(60));

  // ---------- [1] 字典完整性 ----------
  record('字典-热点平台五', Object.keys(apiMod.HOTSPOT_PLATFORM_NAME).length === 5
    && apiMod.HOTSPOT_PLATFORM_NAME.xiaohongshu === '小红书');
  record('字典-发布平台五', Object.keys(apiMod.PUBLISH_PLATFORM_NAME).length === 5
    && apiMod.PUBLISH_PLATFORM_NAME.wechat_channels === '百家号');
  record('字典-热点四态', Object.keys(apiMod.HOTSPOT_STATUS_NAME).length === 4
    && apiMod.HOTSPOT_STATUS_NAME.discarded === '风险否决');
  record('字典-内容五态', Object.keys(apiMod.CONTENT_STATUS_NAME).length === 5
    && apiMod.CONTENT_STATUS_NAME.queued === '已入队');
  record('字典-决策三档', Object.keys(apiMod.DECISION_NAME).length === 3
    && apiMod.DECISION_NAME.manual_queue === '人工裁决');
  record('字典-通道三态', Object.keys(apiMod.CHANNEL_MODE_NAME).length === 3
    && apiMod.CHANNEL_MODE_NAME.mock_fallback === '降级模拟');

  // ---------- [2] 映射回落 ----------
  record('映射-未知回落', apiMod.hotspotPlatformName('xx') === 'xx'
    && apiMod.channelModeName('zz') === 'zz');

  // ---------- [3] 扫描结果 ----------
  const sc = await PromoAPI.radarScan();
  record('API-扫描映射', sc.scanned === 25 && sc.new === 20
    && sc.discarded === 5 && sc.decisions[0].decision === 'auto_engage');

  // ---------- [4] 热点列表(score 数值化) ----------
  const hs = await PromoAPI.hotspots();
  record('API-热点数值化', hs.length === 2 && hs[0].score === 72.4
    && hs[0].status === 'engaged' && hs[1].score === 35);

  // ---------- [5] 决策裁决 ----------
  requests.length = 0;
  await PromoAPI.decide(2, true, '测试备注');
  record('API-裁决载荷', requests.length === 1
    && requests[0].url === '/api/promo/decisions/2/decide'
    && requests[0].data.engage === true
    && requests[0].data.note === '测试备注');

  // ---------- [6] 生成载荷 ----------
  requests.length = 0;
  await PromoAPI.generate({ hotspotId: 1, platforms: ['douyin', 'weibo'] });
  record('API-生成载荷', requests.length === 1
    && requests[0].url === '/api/promo/contents/generate'
    && requests[0].data.hotspotId === 1
    && JSON.stringify(requests[0].data.platforms)
      === JSON.stringify(['douyin', 'weibo']));

  // ---------- [7] 内容列表 ----------
  const cs = await PromoAPI.contents();
  record('API-内容映射', cs.length === 2
    && cs[0].complianceScore === 100 && cs[0].shortCode === 'A-3f2k1'
    && cs[0].agentTrace.length === 4
    && cs[1].status === 'approved');

  // ---------- [8] 审核载荷 ----------
  requests.length = 0;
  await PromoAPI.review(11, true);
  record('API-审核载荷', requests.length === 1
    && requests[0].url === '/api/promo/contents/11/review'
    && requests[0].data.approved === true
    && requests[0].data.reviewer === 'admin');

  // ---------- [9] 入队/出队 ----------
  requests.length = 0;
  const pq = await PromoAPI.publish(12);
  await PromoAPI.processPublish();
  record('API-入队出队', pq.inWindow === true
    && requests[0].url === '/api/promo/contents/12/publish'
    && requests[1].url === '/api/promo/publish/process'
    && requests[1].method === 'POST');

  // ---------- [10] 通道状态 ----------
  const ch = await PromoAPI.channelsStatus();
  record('API-通道三态', ch.length === 3
    && ch[0].effectiveMode === 'mock' && ch[0].keyConfigured === false
    && ch[1].effectiveMode === 'real' && ch[1].keyConfigured === true
    && ch[2].effectiveMode === 'mock_fallback');

  // ---------- [11] 报表总览 ----------
  const ov = await PromoAPI.reportOverview();
  record('API-报表总览', ov.hotspots.pendingManual === 5
    && ov.attribution.gmv === 2280 && ov.dailyCap.used === 6
    && ov.dailyCap.limit === 20);

  // ---------- [12] 管理头注入 ----------
  requests.length = 0;
  await PromoAPI.hotspots();
  record('API-管理头注入', requests.length === 1
    && requests[0].headers['X-Role'] === 'admin');

  // ============================================================
  // 页面层
  // ============================================================
  useMockPromo = true;
  const pageMod = compileLoad(PAGE_SRC, 'page');
  const PromoPage = pageMod.default;
  const el = await renderFlushed(PromoPage, reactForPage);
  const flat = JSON.stringify(el);

  // ---------- [13] 五页签(含激活态; badge 文本拼接容忍) ----------
  const tabs = findAll(el, n => String(n.props.className || '').includes('tabItem')
    && String(n.props.className).indexOf('tabBar') < 0);
  const tabTexts = tabs.map(textOf).join(',');
  record('页面-五页签', tabTexts.includes('总览') && tabTexts.includes('热点雷达')
    && tabTexts.includes('内容工厂') && tabTexts.includes('发布中心')
    && tabTexts.includes('通道画像'));

  // ---------- [14] 统计卡(默认总览 tab, 六卡) ----------
  const statCards = findAll(el, n => String(n.props.className).includes('statCard'));
  const statTexts = statCards.map(textOf).join('|');
  record('页面-统计六卡', statCards.length === 6
    && statTexts.includes('热点总数') && statTexts.includes('待人工裁决')
    && statTexts.includes('已发布') && statTexts.includes('归因GMV'));

  // ---------- [15] 待裁决提醒卡 ----------
  const alert = findAll(el, n => String(n.props.className).includes('alertCard'));
  record('页面-待裁决提醒', alert.length === 1
    && textOf(alert[0]).includes('5 条热点待人工裁决'));

  // ---------- [16] 交互: 切雷达页签 ----------
  const radarTab = tabs.find(t => textOf(t).includes('热点雷达'));
  await radarTab.props.onClick();
  await new Promise(r => setTimeout(r, 20));
  const elRadar = reactForPage.__test.rerender();
  const flatRadar = JSON.stringify(elRadar);
  record('页面-雷达扫描按钮', flatRadar.includes('立即扫描五平台热榜'));
  const hsCards = findAll(elRadar, n => String(n.props.className).includes('hotspotCard'));
  const fills = findAll(elRadar, n => String(n.props.className).includes('scoreFill'));
  record('页面-热点卡评分条', hsCards.length === 1
    && fills.length === 1 && fills[0].props.style.width === '72%'
    && textOf(hsCards[0]).includes('中秋送礼国潮热'));

  // ---------- [17] 裁决按钮 ----------
  const btnEngage = findAll(elRadar, n => String(n.props.className).includes('btnEngage'));
  const btnPass = findAll(elRadar, n => String(n.props.className).includes('btnPass'));
  record('页面-裁决按钮', btnEngage.length === 1 && btnPass.length === 1
    && textOf(btnEngage[0]) === '跟进' && textOf(btnPass[0]) === '放弃');

  // ---------- [18] 内容工厂生成表单 ----------
  const studioTab = tabs.find(t => textOf(t).includes('内容工厂'));
  await studioTab.props.onClick();
  await new Promise(r => setTimeout(r, 20));
  const elStudio = reactForPage.__test.rerender();
  const flatStudio = JSON.stringify(elStudio);
  const pickers = findAll(elStudio, n => String(n.props.className || '')
    .includes('pickerItem'));
  record('页面-平台多选表单', flatStudio.includes('Agent 一源多态生成')
    && pickers.length === 6   // 1 热点 + 5 平台
    && flatStudio.includes('生成内容(分析→匹配→生成→自查)'));

  // ---------- [19] 内容卡(徽章/轨迹/操作) ----------
  const cCards = findAll(elStudio, n => String(n.props.className).includes('contentCard'));
  const traces = findAll(elStudio, n => String(n.props.className).includes('traceBadge'));
  record('页面-内容卡轨迹', cCards.length === 1 && traces.length === 4
    && textOf(cCards[0]).includes('中秋竹香礼赠指南')
    && textOf(cCards[0]).includes('A-3f2k1'));

  // ---------- [20] 发布中心队列卡 ----------
  const pubTab = tabs.find(t => textOf(t).includes('发布中心'));
  await pubTab.props.onClick();
  await new Promise(r => setTimeout(r, 20));
  const elPub = reactForPage.__test.rerender();
  const qCards = findAll(elPub, n => String(n.props.className).includes('queueCard'));
  record('页面-队列黄金时段', qCards.length === 1
    && textOf(qCards[0]).includes('黄金时段')
    && JSON.stringify(elPub).includes('处理到期发布'));

  // ---------- [21] 通道徽章三色 ----------
  const chTab = tabs.find(t => textOf(t).includes('通道画像'));
  await chTab.props.onClick();
  await new Promise(r => setTimeout(r, 20));
  const elCh = reactForPage.__test.rerender();
  const flatCh = JSON.stringify(elCh);
  record('页面-通道三色徽章', flatCh.includes('modeMock')
    && flatCh.includes('modeReal') && flatCh.includes('modeFallback')
    && flatCh.includes('百度 SEO 推送') && flatCh.includes('平台受众画像'));

  // ---------- [22] 渲染确定性 ----------
  reactForPage.__test.states.length = 0;
  const elAgain = await renderFlushed(PromoPage, reactForPage);
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
