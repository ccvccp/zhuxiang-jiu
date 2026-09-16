/**
 * test-xx65.js · 65号 智能开店工作台 前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-nexus74.js) + TS 内存编译
 *
 * 覆盖:
 *   [API 层 api/xx65.ts]
 *   1. myShops URL(owner_id 本人) + 映射
 *   2. parseIntent 请求体(ownerId/text) + 映射
 *   3. applyShop 请求体(ownerId+intentId)
 *   4. claimShop 请求体(answers 合规问卷)
 *   5. createDraft 请求体 + 禁词替换映射
 *   6. publishDraft 请求体(S1 confirmed)
 *   7. createCampaign 请求体 + 映射
 *   8. closeShop 宪法豁免(closedBy)
 *   9. 字典: 店铺六态 + 草稿四态
 *   [页面层 pages/xx65]
 *   10. 四页签(开店/内容工坊/营销/治理)
 *   11. 开店四步流程(parse→apply→claim→activate)
 *   12. S1 确认发布+禁词替换展示
 *   13. S6 人工兜底(不受开关影响)
 *   14. 决策 409 友好降级
 *   15. S5 撤销窗口提示
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'xx65.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'xx65', 'index.tsx');

const requests = [];

const INTENT_MOCK = {
  code: 0,
  data: { intentId: '1', category: 'handicraft',
    categoryLabel: '手工艺品', minLevel: 'L3', fallback: false,
    complianceQuestions: ['是否涉及珍稀材质(濒危木材/动物制品)?'] },
};

const DRAFT_MOCK = {
  code: 0,
  data: { draftId: '7', shopId: '1', productName: '木雕摆件',
    description: '高品质手艺', price: '100', status: 'draft',
    llmTrack: 'rule',
    replacements: [{ from: '最好', to: '高品质' }],
    watermark: 'sha256:abc123' },
};

const CAMPAIGN_MOCK = {
  code: 0,
  data: { campaignId: '5', shopId: '1', strategy: 'clearance',
    status: 'active', exclusive: true },
};

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url.startsWith('/api/xx65/shops?owner_id=')) {
    return { code: 0, shops: [
      { shopId: '1', ownerId: '3', status: 'active',
        category: 'handicraft', categoryLabel: '手工艺品' }] };
  }
  if (url === '/api/xx65/intents/parse') return INTENT_MOCK;
  if (url === '/api/xx65/shops/apply') {
    return { code: 0, data: { shopId: '2', status: 'prechecked',
      complianceQuestions: ['是否涉及珍稀材质?'] } };
  }
  if (url === '/api/xx65/shops/2/claim') {
    return { code: 0, data: { shopId: '2', status: 'claimed' } };
  }
  if (url === '/api/xx65/shops/2/activate') {
    return { code: 0, data: { shopId: '2', status: 'active' } };
  }
  if (url === '/api/xx65/shops/1/close') {
    return { code: 0, data: { shopId: '1', status: 'closed' } };
  }
  if (url === '/api/xx65/products/draft') return DRAFT_MOCK;
  if (url === '/api/xx65/drafts/7/publish') {
    return { code: 0, data: { status: 'published', productId: '9' } };
  }
  if (url === '/api/xx65/drafts/7/human-review') {
    return { code: 0, data: { status: 'pending_review' } };
  }
  if (url === '/api/xx65/campaigns') return CAMPAIGN_MOCK;
  if (url === '/api/xx65/campaigns/recommend') {
    return { code: 0, data: { recommendations: [
      { strategy: 'clearance', label: '清仓特卖', score: '0.8',
        roiCashLift: '0.25', trustPortion: '0.3', channels: ['in_site'] }] } };
  }
  if (url.startsWith('/api/xx65/campaigns?shop_id=')) {
    return { code: 0, campaigns: [] };
  }
  if (url.startsWith('/api/xx65/products?shop_id=')) {
    return { code: 0, products: [] };
  }
  if (url.startsWith('/api/xx65/shops/1/health')) {
    return { code: 0, data: { healthScore: '100' } };
  }
  if (url.startsWith('/api/xx65/shops/1/coach')) {
    return { code: 0, data: { total: 0, tips: [] } };
  }
  if (url.startsWith('/api/xx65/products/9/order-window')) {
    return { code: 0, data: {
      productId: 9, productName: '木雕摆件',
      dualTrack: { cashValue: 70, trustValue: 30, note: 'S4' },
      quotaProgress: { balance: 100, singleQuota: 300,
        singleRatio: 0.1, cumulativeRatio: 0.2 },
      warnings: [], elder: false } };
  }
  if (url === '/api/xx65/shops/1/quota-adjust') {
    return { code: 0, data: { submitted: true, kind: 'patch' } };
  }
  if (url === '/api/xx65/redteam') {
    return { code: 0, data: { defended: 7, total: 7, allDefended: true } };
  }
  if (url.startsWith('/api/xx65/model/status')) {
    return { code: 0, data: { mode: 'assist' } };
  }
  throw new Error('unexpected url: ' + url);
};

const mockTaro = { showToast: () => {}, showModal: async () => ({ confirm: false }) };

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === './request' || request === '@/api/request') return { request: mockRequest };
  if (request === '@tarojs/taro') return { default: mockTaro };
  if (request === '@/services/auth-service') {
    return {
      getSession: () => ({ memberId: '3', role: 'member' }),
      getMemberId: () => '3', requireLogin: () => true,
    };
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

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'xx65-'));
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
  const api = compileTs(API_SRC, 'xx65.js');
  const { Xx65API, SHOP_STATES, DRAFT_STATES } = api;

  console.log('[API 层 api/xx65.ts]');
  // 1. myShops
  const shops = await Xx65API.myShops();
  const lastS = requests[requests.length - 1];
  record('myShops URL(本人 owner_id=3)+member 角色头+映射',
    lastS.url === '/api/xx65/shops?owner_id=3'
    && lastS.headers['X-Role'] === 'member'
    && shops.length === 1 && shops[0].shopId === 1
    && shops[0].status === 'active'
    && shops[0].categoryLabel === '手工艺品',
    lastS.url);

  // 2. parseIntent
  const intent = await Xx65API.parseIntent('手工木雕定制');
  const lastI = requests[requests.length - 1];
  record('parseIntent 请求体(ownerId+text)+映射',
    lastI.url === '/api/xx65/intents/parse'
    && lastI.data.ownerId === 3 && lastI.data.text === '手工木雕定制'
    && intent.intentId === 1 && intent.categoryLabel === '手工艺品'
    && intent.complianceQuestions.length === 1,
    JSON.stringify(lastI.data || {}));

  // 3. applyShop
  await Xx65API.applyShop(1);
  const lastA = requests[requests.length - 1];
  record('applyShop 请求体(ownerId+intentId)',
    lastA.url === '/api/xx65/shops/apply'
    && lastA.data.ownerId === 3 && lastA.data.intentId === 1,
    JSON.stringify(lastA.data || {}));

  // 4. claimShop
  await Xx65API.claimShop(2, { '是否涉及珍稀材质?': '否' });
  const lastC = requests[requests.length - 1];
  record('claimShop 请求体(answers 合规问卷)',
    lastC.url === '/api/xx65/shops/2/claim'
    && lastC.data.answers['是否涉及珍稀材质?'] === '否',
    JSON.stringify(lastC.data || {}));

  // 5. createDraft
  const draft = await Xx65API.createDraft(
    { shopId: 1, productName: '木雕摆件', description: '全村最好的手艺', price: 100 });
  const lastD = requests[requests.length - 1];
  record('createDraft 请求体+禁词替换映射',
    lastD.url === '/api/xx65/products/draft'
    && lastD.data.shopId === 1 && lastD.data.price === 100
    && draft.draftId === 7 && draft.llmTrack === 'rule'
    && draft.replacements.length === 1
    && draft.replacements[0].from === '最好',
    JSON.stringify(lastD.data || {}));

  // 6. publishDraft
  await Xx65API.publishDraft(7);
  const lastP = requests[requests.length - 1];
  record('publishDraft 请求体(S1 confirmed)',
    lastP.url === '/api/xx65/drafts/7/publish'
    && lastP.data.confirmed === true,
    JSON.stringify(lastP.data || {}));

  // 7. createCampaign
  const campaign = await Xx65API.createCampaign(
    { shopId: 1, productId: 9, strategy: 'clearance' });
  const lastM = requests[requests.length - 1];
  record('createCampaign 请求体+映射',
    lastM.url === '/api/xx65/campaigns'
    && lastM.data.shopId === 1 && lastM.data.strategy === 'clearance'
    && campaign.campaignId === 5 && campaign.exclusive === true,
    JSON.stringify(lastM.data || {}));

  // 8. closeShop
  await Xx65API.closeShop(1);
  const lastX = requests[requests.length - 1];
  record('closeShop 宪法豁免(closedBy 角色)',
    lastX.url === '/api/xx65/shops/1/close'
    && lastX.data.closedBy === 'member',
    JSON.stringify(lastX.data || {}));

  // 9. 字典
  record('字典: 店铺六态+草稿四态',
    Object.keys(SHOP_STATES).length === 6
    && SHOP_STATES.active === '经营中'
    && Object.keys(DRAFT_STATES).length === 4
    && DRAFT_STATES.pending_review === '人工审核中');

  // 9b. orderWindow
  const ow = await Xx65API.orderWindow(9);
  const lastW = requests[requests.length - 1];
  record('orderWindow URL+双轨映射(S4)',
    lastW.url === '/api/xx65/products/9/order-window?trust_id=3'
    && ow.dualTrack.cashValue === 70
    && ow.dualTrack.trustValue === 30
    && ow.quotaProgress.singleRatio === 0.1,
    lastW.url);

  // 9c. quotaAdjust
  await Xx65API.quotaAdjust(1, 'uplift');
  const lastQ = requests[requests.length - 1];
  record('quotaAdjust 请求体+admin 头(S7)',
    lastQ.url === '/api/xx65/shops/1/quota-adjust'
    && lastQ.data.direction === 'uplift'
    && lastQ.headers['X-Role'] === 'admin',
    JSON.stringify(lastQ.data || {}));

  // 9d. redteam
  const rt = await Xx65API.redteam();
  const lastR = requests[requests.length - 1];
  record('redteam 请求+映射(admin)',
    lastR.url === '/api/xx65/redteam'
    && lastR.headers['X-Role'] === 'admin'
    && rt.defended === 7 && rt.allDefended === true,
    lastR.url);

  console.log('[页面层 pages/xx65]');
  const pageSrc = fs.readFileSync(PAGE_SRC, 'utf-8');
  // 10. 四页签
  record('四页签(开店/内容工坊/营销/治理)',
    pageSrc.includes("'shop'") && pageSrc.includes("'content'")
    && pageSrc.includes("'campaign'") && pageSrc.includes("'govern'")
    && pageSrc.includes('内容工坊') && pageSrc.includes('治理'));
  // 11. 开店四步流程
  record('开店四步流程(parse→apply→claim→activate)',
    pageSrc.includes('handleParse') && pageSrc.includes('handleApply')
    && pageSrc.includes('handleClaim') && pageSrc.includes('handleActivate'));
  // 12. S1 确认发布+禁词替换
  record('S1 确认发布+禁词替换展示',
    pageSrc.includes('确认发布(S1 终审')
    && pageSrc.includes('禁词替换'));
  // 13. S6 人工兜底
  record('S6 人工兜底(不受开关影响)',
    pageSrc.includes('handleHumanReview')
    && pageSrc.includes('S6 · 不受开关影响'));
  // 14. 决策 409 友好降级
  record('决策 409 友好降级检测',
    pageSrc.includes("includes('409')") || pageSrc.includes("includes('决策')"));
  // 15. S5 撤销窗口
  record('S5 撤销窗口提示',
    pageSrc.includes('S5 五分钟撤销窗口')
    && pageSrc.includes('handleRevoke'));
  // 16. 下单窗口+admin 运营区
  record('下单窗口(S4 双轨)+admin 运营区(quota/红队)',
    pageSrc.includes('handleOrderWindow')
    && pageSrc.includes('双轨定价(S4)')
    && pageSrc.includes('运营管理(admin)')
    && pageSrc.includes('handleQuotaAdjust')
    && pageSrc.includes('handleRedteam')
    && pageSrc.includes('46号审批')
    && pageSrc.includes('rtResult.defended')
    && pageSrc.includes('全部防住'));

  fs.rmSync(tmpDir, { recursive: true, force: true });
  console.log('------------------------------------------------------------');
  console.log(`通过: ${PASS} / 失败: ${FAIL} / 总计: ${PASS + FAIL}`);
  process.exit(FAIL === 0 ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
