/**
 * test-recycle-negotiation.js · 新酒议价回收前端单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-pocket-admin.js) + TS 内存编译 + Module._load mock
 *
 * 覆盖(新酒议价前端入口):
 *   [API 层 api/recycle.ts]
 *   1. submitNewWineValuation 请求体(userId 注入/字段透传)
 *   2. myNegotiations URL(带 user_id 筛选)
 *   3. proposePrice 请求体(proposedPrice/reason)
 *   4. acceptNegotiation 请求体(acceptedBy=user)
 *   5. rejectNegotiation 请求体(rejectedBy=user)
 *   6. recycleNewWine URL+请求体(payoutMethod/Account)
 *   7. toNegotiation 字段映射(数值化/history 数组/状态字典)
 *   [页面层 pages/recycle]
 *   8. 三视图结构(老酒估价/新酒议价/我的回收)
 *   9. 议价弹层(出价输入/±10% 窗口提示/轮次历史/接受拒绝)
 *   10. 议价回收打款弹层
 *   [日期边界层 utils/wine-age.ts]
 *   11. minusYears 整年回推
 *   12. minusYears 2/29 溢出回退
 *   13. toLocalDateStr 本地时区(UTC 偏移防护)
 *   14. calcWineAge 整年边界(3年/2年/0年)
 *   15. 页面日期边界接线(老酒 end / 新酒 start+end)
 *   16. 页面酒龄预检提示(老酒<3年 → 新酒议价 / 新酒>3年 → 老酒)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');
const ts = require('typescript');

const API_SRC = path.resolve(__dirname, '..', 'src', 'api', 'recycle.ts');
const PAGE_SRC = path.resolve(__dirname, '..', 'src', 'pages', 'recycle', 'index.tsx');
const AGE_SRC = path.resolve(__dirname, '..', 'src', 'utils', 'wine-age.ts');

const requests = [];
let memberSeq = 42;

const mockRequest = async (opts) => {
  requests.push(opts);
  const url = opts.url || '';
  if (url === '/api/recycle/new-wine/valuation' && opts.method === 'POST') {
    return { data: { id: 7, userId: opts.data.userId,
      productId: opts.data.productId,
      purchasePrice: opts.data.purchasePrice,
      purchaseDate: opts.data.purchaseDate,
      wineAge: 1, wineAgeCategory: 'year1', wineAgeCategoryName: '1年酒',
      conditionGrade: opts.data.conditionGrade,
      bottleCount: opts.data.bottleCount,
      aiBasePrice: 90, currentPrice: 90, finalPrice: null,
      negotiationRound: 0, maxRounds: 3, status: 'pending',
      history: [{ round: 0, role: 'ai', price: 90,
                  action: 'initial_valuation', timestamp: 't' }],
      createdAt: 't', updatedAt: 't' } };
  }
  if (url.startsWith('/api/recycle/negotiations')) {
    return { data: [
      { id: 7, userId: 1, productId: 'ZX52-2026X02', purchasePrice: '100',
        purchaseDate: '2025-01-01', wineAge: '1', wineAgeCategory: 'year1',
        wineAgeCategoryName: '1年酒', conditionGrade: 'B', bottleCount: '2',
        aiBasePrice: '90', currentPrice: '95', finalPrice: null,
        negotiationRound: '1', maxRounds: '3', status: 'user_proposed',
        history: [{ round: '1', role: 'user', price: '95',
                    action: 'user_propose', coefficient: '1.0556' }],
        createdAt: 't', updatedAt: 't' },
    ] };
  }
  if (/\/api\/recycle\/negotiation\/\d+\/propose$/.test(url)) {
    return { data: { id: 7, status: 'user_proposed',
      currentPrice: opts.data.proposedPrice,
      negotiationRound: 1, aiBasePrice: 90, maxRounds: 3,
      history: [], finalPrice: null, userId: 1, productId: 'p',
      purchasePrice: 100, purchaseDate: '2025-01-01', wineAge: 1,
      wineAgeCategory: 'year1', wineAgeCategoryName: '1年酒',
      conditionGrade: 'B', bottleCount: 2, createdAt: 't', updatedAt: 't' } };
  }
  if (/\/api\/recycle\/negotiation\/\d+\/accept$/.test(url)) {
    return { data: { id: 7, status: 'accepted', finalPrice: opts.data.finalPrice,
      currentPrice: 95, aiBasePrice: 90, maxRounds: 3, history: [],
      negotiationRound: 1, userId: 1, productId: 'p', purchasePrice: 100,
      purchaseDate: '2025-01-01', wineAge: 1, wineAgeCategory: 'year1',
      wineAgeCategoryName: '1年酒', conditionGrade: 'B', bottleCount: 2,
      createdAt: 't', updatedAt: 't' } };
  }
  if (/\/api\/recycle\/negotiation\/\d+\/reject$/.test(url)) {
    return { data: { id: 7, status: 'rejected', currentPrice: 90,
      aiBasePrice: 90, maxRounds: 3, history: [], finalPrice: null,
      negotiationRound: 1, userId: 1, productId: 'p', purchasePrice: 100,
      purchaseDate: '2025-01-01', wineAge: 1, wineAgeCategory: 'year1',
      wineAgeCategoryName: '1年酒', conditionGrade: 'B', bottleCount: 2,
      createdAt: 't', updatedAt: 't' } };
  }
  if (/\/api\/recycle\/new-wine\/\d+\/recycle$/.test(url)) {
    return { data: { exchangeId: 99, finalPrice: 95,
      cashAmount: 76, status: 'completed' } };
  }
  throw new Error('unexpected url: ' + url);
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === './request' || request === '@/api/request') return { request: mockRequest };
  if (request === '@/services/auth-service') {
    return { getMemberId: () => String(memberSeq), requireLogin: () => true };
  }
  if (request === '@tarojs/taro') {
    return { default: { showToast: () => {}, showModal: async () => ({ confirm: true }) } };
  }
  if (request === '@tarojs/components') {
    return new Proxy({}, { get: (t, name) => (name === 'default' ? {} : { default: {} }) });
  }
  if (request.includes('index.module.scss')) return { default: new Proxy({}, { get: () => 'c' }) };
  if (request === '@/components/NavBar') return { default: () => null };
  if (request === 'react') {
    return { useState: (v) => [v, () => {}], useEffect: () => {},
             useCallback: (f) => f };
  }
  return origLoad.apply(this, arguments);
};

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'recycle-neg-'));
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
  const api = compileTs(API_SRC, 'recycle.js');
  const { RecycleAPI, NEG_STATUS_NAME, negStatusName } = api;

  console.log('[API 层 recycle.ts]');
  // 1. submitNewWineValuation
  const neg = await RecycleAPI.submitNewWineValuation({
    productId: 'ZX52-2026X02', purchasePrice: 100,
    purchaseDate: '2025-06-01', conditionGrade: 'B', bottleCount: 2,
  });
  const sv = requests[requests.length - 1];
  record('submitNewWineValuation 请求体(userId 注入+字段透传)',
    sv.url === '/api/recycle/new-wine/valuation' && sv.method === 'POST'
    && sv.data.userId === 42 && sv.data.productId === 'ZX52-2026X02'
    && sv.data.purchasePrice === 100 && sv.data.bottleCount === 2,
    JSON.stringify(sv.data));
  record('submitNewWineValuation 返回议价记录(AI 基准价)',
    neg.aiBasePrice === 90 && neg.status === 'pending'
    && neg.wineAgeCategoryName === '1年酒');

  // 2. myNegotiations
  const list = await RecycleAPI.myNegotiations();
  const ml = requests[requests.length - 1];
  record('myNegotiations URL(带 user_id 筛选)',
    ml.url === '/api/recycle/negotiations?user_id=42&limit=50', ml.url);

  // 7. toNegotiation 映射(Redis 字符串数值化)
  record('toNegotiation 字段映射(字符串数值化)',
    list.length === 1 && list[0].aiBasePrice === 90
    && list[0].currentPrice === 95 && list[0].negotiationRound === 1
    && list[0].bottleCount === 2
    && list[0].history[0].coefficient === 1.0556,
    JSON.stringify(list[0]));

  // 3. proposePrice
  await RecycleAPI.proposePrice(7, 95, '品相好');
  const pp = requests[requests.length - 1];
  record('proposePrice 请求体',
    pp.url === '/api/recycle/negotiation/7/propose' && pp.method === 'POST'
    && pp.data.proposedPrice === 95 && pp.data.reason === '品相好',
    JSON.stringify(pp.data));

  // 4. acceptNegotiation
  await RecycleAPI.acceptNegotiation(7);
  const ac = requests[requests.length - 1];
  record('acceptNegotiation 请求体(acceptedBy=user)',
    ac.url === '/api/recycle/negotiation/7/accept'
    && ac.data.acceptedBy === 'user' && ac.data.finalPrice === null);

  // 5. rejectNegotiation
  await RecycleAPI.rejectNegotiation(7, '太低');
  const rj = requests[requests.length - 1];
  record('rejectNegotiation 请求体(rejectedBy=user)',
    rj.url === '/api/recycle/negotiation/7/reject'
    && rj.data.rejectedBy === 'user' && rj.data.reason === '太低');

  // 6. recycleNewWine
  await RecycleAPI.recycleNewWine(7, 'wechat', 'wx_acc_123');
  const rc = requests[requests.length - 1];
  record('recycleNewWine URL+请求体',
    rc.url === '/api/recycle/new-wine/7/recycle' && rc.method === 'POST'
    && rc.data.payoutMethod === 'wechat' && rc.data.payoutAccount === 'wx_acc_123');

  // 状态字典
  record('议价状态字典(6 态)',
    NEG_STATUS_NAME.pending === '待议价'
    && NEG_STATUS_NAME.user_proposed === '已出价'
    && NEG_STATUS_NAME.ai_counter === 'AI已反价'
    && NEG_STATUS_NAME.accepted === '议价成功'
    && negStatusName('expired') === '已过期');

  console.log('[页面层 pages/recycle]');
  const pageCode = fs.readFileSync(PAGE_SRC, 'utf-8');
  // 8. 三视图结构
  record('三视图结构(老酒估价/新酒议价/我的回收)',
    pageCode.includes("'newwine'") && pageCode.includes('renderNewWine')
    && pageCode.includes('新酒议价回收'));
  // 9. 议价弹层
  record('议价弹层(出价/±10%窗口/轮次历史/接受拒绝)',
    pageCode.includes('handlePropose') && pageCode.includes('handleAcceptNeg')
    && pageCode.includes('handleRejectNeg') && pageCode.includes('议价过程')
    && pageCode.includes('* 0.9'));
  // 10. 打款弹层
  record('议价回收打款弹层',
    pageCode.includes('payoutPanel') && pageCode.includes('handleNegRecycle')
    && pageCode.includes('确认回收'));

  console.log('[日期边界层 utils/wine-age.ts]');
  const age = compileTs(AGE_SRC, 'wine-age.js');
  const { toLocalDateStr, minusYears, calcWineAge, TODAY_LOCAL, THREE_YEARS_AGO } = age;

  // 11. minusYears 常规(同月日整年回推)
  record('minusYears 常规(2026-09-15 → 2023-09-15)',
    toLocalDateStr(minusYears(new Date(2026, 8, 15), 3)) === '2023-09-15');

  // 12. minusYears 2/29 溢出回退(2028-02-29 → 2025-02-28, 非 3/1)
  record('minusYears 2/29 溢出回退一天',
    toLocalDateStr(minusYears(new Date(2028, 1, 29), 3)) === '2025-02-28');

  // 13. toLocalDateStr 无 UTC 偏移(本地午夜不受 toISOString 影响)
  record('toLocalDateStr 本地时区(非 UTC)',
    toLocalDateStr(new Date(2026, 8, 15, 23, 30)) === '2026-09-15'
    && new Date(2026, 8, 15, 0, 30).toISOString().slice(0, 10) !== '2026-09-15');

  // 14. 酒龄整年边界(满3年当天=3, 次日=2, 今天=0)
  const now2026 = new Date(2026, 8, 15);
  record('calcWineAge 整年边界(3年前=3/3年+1天=2/今天=0)',
    calcWineAge('2023-09-15', now2026) === 3
    && calcWineAge('2023-09-16', now2026) === 2
    && calcWineAge('2026-09-15', now2026) === 0
    && calcWineAge('2025-09-16', now2026) === 0);

  // 15. 模块常量与页面接线
  record('页面日期边界接线(老酒 end=3年前/新酒 start=3年前+end=今天)',
    pageCode.includes('end={THREE_YEARS_AGO}')
    && pageCode.includes('start={THREE_YEARS_AGO}')
    && pageCode.includes('end={TODAY_LOCAL}')
    && pageCode.includes('value={dateStr || THREE_YEARS_AGO}')
    && !pageCode.includes('toISOString'));

  // 16. 页面酒龄预检(老酒<3年/新酒>3年 友好提示, 免打后端 409)
  record('页面酒龄预检提示(老酒未满3年/新酒超3年)',
    pageCode.includes('未满3年请走「新酒议价」')
    && pageCode.includes('满三年请走老酒估价')
    && pageCode.includes('calcWineAge'));

  console.log('\n通过: ' + PASS + ' / 失败: ' + FAIL + ' / 总计: ' + (PASS + FAIL));
  process.exit(FAIL === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
