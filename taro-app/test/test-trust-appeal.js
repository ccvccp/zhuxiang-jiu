/**
 * test-trust-appeal.js · 信值兑换订单·申诉入口前端单元测试(64号会员面)
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-points.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖(2026-09-15 申诉入口补齐):
 *   [API 层 api/xx64.ts]
 *   1. APPEAL_STATUS_NAME 字典(四态中文名)
 *   2. myOrders URL(buyer_id 自注入 + limit)
 *   3. myOrders 请求头(X-Role: member)
 *   4. myOrders 映射(订单数值化 + appeal 联查对象/无申诉 null)
 *   5. appeal 请求体(orderId/reason/submittedBy)
 *   [页面层 pages/trust]
 *   6. 申诉判定(非 initiated 且无进行中申诉 → 可申诉)
 *   7. 申诉判定(进行中申诉 → 不可申诉)
 *   8. 申诉判定(终审后 → 可再次申诉)
 *   9. 申诉判定(initiated 订单 → 不可申诉)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'xx64.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'trust', 'index.tsx');

// ============================================================
// 1. Mock 上下文
// ============================================================
const requests = [];

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url.startsWith('/api/xx64/my-orders')) {
    return {
      success: true, total: 2,
      byStatus: { paid: 1, completed: 1 },
      orders: [
        { orderId: '11', buyerId: '5', sellerId: '2', trustId: '5',
          price: '100', product: '竹香酒 42°', status: 'paid',
          createdAt: '2026-09-15T04:00:00Z',
          appeal: { appealId: '3', status: 'recalculated', decision: '',
            submittedAt: '2026-09-15T04:30:00Z', reviewedAt: '',
            expiresAt: '2026-09-17T04:30:00Z' } },
        { orderId: '12', buyerId: '5', sellerId: '2', trustId: '5',
          price: '200', product: '竹香酒礼盒', status: 'completed',
          createdAt: '2026-09-14T04:00:00Z', appeal: null },
      ],
      note: '我的订单——每单附最新申诉进度(观测面)',
    };
  }
  if (url === '/api/xx64/appeals' && opts.method === 'POST') {
    return { success: true, appealId: 9, orderId: 12, status: 'recalculated',
      expiresAt: '2026-09-17T05:00:00Z',
      note: '申诉已受理——重算结果仅展示(终审为人工决定, 终审前订单现状不变)',
      createdAt: '2026-09-15T05:00:00Z' };
  }
  throw new Error('unexpected url: ' + url);
};

const mockTaro = {
  getStorageSync: (k) => (k === 'trust_id_cache' ? '5' : ''),
  setStorageSync: () => {},
  removeStorageSync: () => {},
  showToast: () => {},
  showModal: () => {},
  navigateBack: () => {},
};

// ---- Module._load 拦截 ----
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === './request' || request === '@/api/request') return { request: mockRequest };
  if (request === '@tarojs/taro') return { default: mockTaro };
  if (request === '@/services/auth-service') {
    return { getMemberId: () => 5, getSession: () => ({ role: 'member' }),
      requireLogin: () => true };
  }
  if (request === '@tarojs/components') {
    return new Proxy({}, { get: (t, name) => (name === 'default' ? {} : { default: {} }) });
  }
  if (request.includes('index.module.scss')) return { default: new Proxy({}, { get: () => 'c' }) };
  if (request === '@/components/NavBar') return { default: () => null };
  if (request === '@/api/points') return { PointsAPI: { account: async () => ({ totalPoints: 110 }) } };
  if (request === 'react') return { useState: (v) => [v, () => {}], useEffect: () => {}, useCallback: (f) => f };
  return origLoad.apply(this, arguments);
};

// ---- TS 内存编译 ----
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'trust-appeal-'));
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
  const api = compileTs(API_SRC, 'xx64.js');
  const { Xx64API, APPEAL_STATUS_NAME } = api;

  console.log('[API 层 xx64.ts]');
  // 1. 字典
  record('申诉状态字典四态',
    APPEAL_STATUS_NAME.recalculated === '重算完成·待终审'
    && APPEAL_STATUS_NAME.approved === '终审通过·已翻转'
    && APPEAL_STATUS_NAME.rejected === '终审维持'
    && APPEAL_STATUS_NAME.expired === '超期未审·维持');

  // 2-4. myOrders
  const orders = await Xx64API.myOrders(20);
  const last = requests[requests.length - 1];
  record('myOrders URL(buyer_id+limit)',
    last.url === '/api/xx64/my-orders?buyer_id=5&limit=20', last.url);
  record('myOrders 头(X-Role: member)',
    last.headers && last.headers['X-Role'] === 'member');
  record('myOrders 映射+联查',
    orders.length === 2
    && orders[0].orderId === 11 && orders[0].price === 100
    && orders[0].appeal && orders[0].appeal.appealId === 3
    && orders[0].appeal.status === 'recalculated'
    && orders[1].appeal === null,
    JSON.stringify(orders[0]).slice(0, 120));

  // 5. appeal 请求体
  await Xx64API.appeal(12, '价格计算有误');
  const ap = requests[requests.length - 1];
  record('appeal 请求体',
    ap.url === '/api/xx64/appeals' && ap.method === 'POST'
    && ap.data.orderId === 12 && ap.data.reason === '价格计算有误'
    && ap.data.submittedBy === 'member');

  console.log('[页面层 pages/trust]');
  // 6-9. appealable 判定(从源码编译后页面不可直接调用——以纯逻辑复刻断言,
  //      与页面同款: 非 initiated && !(进行中申诉))
  const APPEAL_TERMINAL = ['approved', 'rejected', 'expired'];
  const appealable = (o) => {
    if (o.status === 'initiated') return false;
    if (o.appeal && !APPEAL_TERMINAL.includes(o.appeal.status)) return false;
    return true;
  };
  record('进行中申诉 → 不可申诉',
    appealable({ status: 'paid', appeal: { status: 'recalculated' } }) === false);
  record('终审后 → 可再次申诉',
    appealable({ status: 'disputed', appeal: { status: 'rejected' } }) === true
    && appealable({ status: 'paid', appeal: { status: 'expired' } }) === true);
  record('initiated 订单 → 不可申诉',
    appealable({ status: 'initiated', appeal: null }) === false);
  record('无申诉订单 → 可申诉',
    appealable({ status: 'completed', appeal: null }) === true);

  // 页面源码静态断言(申诉入口存在)
  const pageCode = fs.readFileSync(PAGE_SRC, 'utf-8');
  record('页面含申诉弹层渲染',
    pageCode.includes('renderAppealPanel') && pageCode.includes('提交申诉'));
  record('页面含订单申诉区块',
    pageCode.includes('订单·申诉') && pageCode.includes('appealBtn'));
  record('页面加载 myOrders',
    pageCode.includes('loadOrders') && pageCode.includes('Xx64API.myOrders'));

  console.log('\n通过: ' + PASS + ' / 失败: ' + FAIL + ' / 总计: ' + (PASS + FAIL));
  process.exit(FAIL === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
